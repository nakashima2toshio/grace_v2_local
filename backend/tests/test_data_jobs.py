# backend/tests/test_data_jobs.py
"""データ準備ジョブ（チャンキング / 登録 / 削除）のテスト。

**実 Qdrant・実 LLM（Ollama）・実 API キーは不要**（CI の必須条件）。
Qdrant クライアントと `register_to_qdrant` / チャンク化本体をスタブへ差し替える。

最重要の検証は **「承認しなければ破壊されない」** こと:
- 削除は常に CONFIRM を通り、拒否・タイムアウトなら `delete_collection` を呼ばない
- 登録は `recreate=True` のときだけ CONFIRM を通り、拒否なら登録しない
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from backend.app.core.data_jobs import (
    ChunkingParams,
    DeleteParams,
    RegisterParams,
    _chunking_runner,
    _delete_runner,
    _register_runner,
)
from backend.app.core.jobs import _resolve_runner, job_manager
from backend.app.core.support_agent import SupportEvent
from backend.app.main import app
from config import get_default_ollama_model
from grace.intervention import InterventionAction, InterventionResponse

client = TestClient(app)


# =============================================================================
# ヘルパ
# =============================================================================


class EventCollector:
    """runner が emit したイベントを溜める。"""

    def __init__(self) -> None:
        self.events: list[SupportEvent] = []

    def __call__(self, event: SupportEvent) -> None:
        self.events.append(event)

    def steps(self, status: str | None = None) -> list[tuple[str, str]]:
        return [
            (e.step or "", e.status or "")
            for e in self.events
            if e.type == "step" and (status is None or e.status == status)
        ]

    def has_error(self) -> bool:
        return any(e.type == "error" for e in self.events)

    def messages(self) -> list[str]:
        return [e.message for e in self.events]


def approve(_request) -> InterventionResponse:
    return InterventionResponse(action=InterventionAction.PROCEED)


def reject(_request) -> InterventionResponse:
    return InterventionResponse(action=InterventionAction.CANCEL)


def timeout(_request) -> InterventionResponse:
    return InterventionResponse(action=InterventionAction.CANCEL, timeout_reached=True)


class StubQdrantClient:
    def __init__(self, names=("faq_anthropic", "gov_anthropic")):
        self._names = list(names)
        self.deleted: list[str] = []

    def get_collections(self):
        class _R:
            def __init__(self, names):
                self.collections = [type("C", (), {"name": n})() for n in names]

        return _R(self._names)

    def delete_collection(self, collection_name: str):
        if collection_name not in self._names:
            raise ValueError("not found")
        self._names.remove(collection_name)
        self.deleted.append(collection_name)


@pytest.fixture
def stub_qdrant(monkeypatch):
    """Qdrant クライアントと一覧取得をスタブへ差し替える。"""
    stub = StubQdrantClient()
    import qdrant_client_wrapper
    import services.qdrant_service as qs

    monkeypatch.setattr(qdrant_client_wrapper, "get_qdrant_client", lambda: stub)
    monkeypatch.setattr(
        qs,
        "get_all_collections",
        lambda _c: [
            {"name": n, "points_count": 100, "status": "green"} for n in stub._names
        ],
    )
    return stub


# =============================================================================
# runner の登録（jobs.py の型解決）
# =============================================================================

@pytest.mark.parametrize(
    "params, expected_kind",
    [
        (ChunkingParams(input_file="OUTPUT/a.csv"), "chunking"),
        (RegisterParams(input_file="qa_output/a.csv", collection="c"), "register"),
        (DeleteParams(collections=["c"]), "delete"),
    ],
)
def test_runner_is_registered(params, expected_kind):
    """params の型から runner が解決できる（import 時の register_runner が効く）。"""
    runner, kind = _resolve_runner(params)
    assert kind == expected_kind
    assert callable(runner)


# =============================================================================
# プロバイダ方針（ローカル LLM ＋ Gemini Embedding）
# =============================================================================

def test_chunking_default_model_is_local_llm():
    """チャンク化の既定モデルがローカル LLM であること。

    Anthropic のモデル名が残っていると Ollama へ存在しないモデルを投げる。
    """
    assert ChunkingParams(input_file="OUTPUT/a.csv").model == get_default_ollama_model()


def test_register_default_provider_stays_gemini():
    """**Embedding は Gemini のまま。**

    LLM をローカル化しても既存 Qdrant コレクション（3072次元）を使い続ける
    ための決定。ここが "ollama" になると 768 次元になり全件再登録が必要になる。
    """
    assert RegisterParams(input_file="qa_output/a.csv", collection="c").provider == "gemini"


# =============================================================================
# 削除：承認しなければ消えない
# =============================================================================

def test_delete_requires_approval(stub_qdrant):
    """承認すれば削除される。"""
    events = EventCollector()
    result = _delete_runner(DeleteParams(collections=["faq_anthropic"]), events, approve)

    assert result is not None
    assert result["deleted"] == ["faq_anthropic"]
    assert result["cancelled"] is False
    assert stub_qdrant.deleted == ["faq_anthropic"]


def test_delete_rejected_does_not_delete(stub_qdrant):
    """**拒否したら削除されない。**"""
    events = EventCollector()
    result = _delete_runner(DeleteParams(collections=["faq_anthropic"]), events, reject)

    assert result is not None
    assert result["cancelled"] is True
    assert result["deleted"] == []
    assert stub_qdrant.deleted == [], "拒否したのに削除された"


def test_delete_timeout_does_not_delete(stub_qdrant):
    """**タイムアウトしたら削除されない（安全側）。**"""
    events = EventCollector()
    result = _delete_runner(DeleteParams(collections=["faq_anthropic"]), events, timeout)

    assert result["cancelled"] is True
    assert "タイムアウト" in result["reason"]
    assert stub_qdrant.deleted == [], "タイムアウトしたのに削除された"


def test_delete_confirm_message_includes_counts(stub_qdrant):
    """承認画面に対象名と件数が出る（何が消えるか分からないまま押させない）。"""
    captured = {}

    def capture(request):
        captured["message"] = request.message
        captured["reason"] = request.reason
        return InterventionResponse(action=InterventionAction.CANCEL)

    _delete_runner(
        DeleteParams(collections=["faq_anthropic", "gov_anthropic"]),
        EventCollector(),
        capture,
    )

    assert "faq_anthropic" in captured["message"]
    assert "gov_anthropic" in captured["message"]
    assert "200" in captured["message"]  # 100 件 × 2
    assert "元に戻せません" in captured["message"]


def test_delete_skips_missing_collections(stub_qdrant):
    """存在しない名前は対象外にし、存在する分だけ削除する。"""
    events = EventCollector()
    result = _delete_runner(
        DeleteParams(collections=["faq_anthropic", "does_not_exist"]), events, approve
    )

    assert result["deleted"] == ["faq_anthropic"]
    assert result["missing"] == ["does_not_exist"]


def test_delete_all_missing_is_error(stub_qdrant):
    """全部存在しなければエラーにする（承認を求めない）。"""
    events = EventCollector()
    result = _delete_runner(DeleteParams(collections=["nope"]), events, approve)

    assert result is None
    assert events.has_error()


def test_delete_empty_list_is_error(stub_qdrant):
    result = _delete_runner(DeleteParams(collections=[]), EventCollector(), approve)
    assert result is None


def test_delete_emits_confirm_step(stub_qdrant):
    """`ConfirmModal` が読む step イベントを出す（action_type / args）。"""
    events = EventCollector()
    _delete_runner(DeleteParams(collections=["faq_anthropic"]), events, approve)

    started = [
        e for e in events.events
        if e.type == "step" and e.step == "confirm" and e.status == "started"
    ]
    assert len(started) == 1
    assert started[0].data["action_type"] == "delete_collections"
    assert started[0].data["requires_confirmation"] is True


# =============================================================================
# 登録：recreate のときだけ承認を求める
# =============================================================================

@pytest.fixture
def stub_register(monkeypatch, tmp_path):
    """`register_to_qdrant` と入力ファイル解決をスタブへ差し替える。"""
    calls: list[dict] = []

    import qa_qdrant.register_to_qdrant as mod

    def fake_register(**kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(mod, "register_to_qdrant", fake_register)

    # 入力ファイルの実体を用意して resolve を通す
    csv = tmp_path / "input.csv"
    csv.write_text("question,answer\nあ,い\n", encoding="utf-8")

    import services.data_pipeline_service as dps

    monkeypatch.setattr(dps, "resolve_input_file", lambda _p, base=None: csv)
    return calls


def test_register_without_recreate_skips_confirm(stub_qdrant, stub_register):
    """`recreate=False` なら承認を求めない（毎回ダイアログを出さない）。"""
    def must_not_be_called(_request):
        raise AssertionError("recreate=False なのに承認を求めた")

    events = EventCollector()
    result = _register_runner(
        RegisterParams(input_file="qa_output/a.csv", collection="new_collection"),
        events,
        must_not_be_called,
    )

    assert result is not None
    assert result["registered"] is True
    assert ("confirm", "skipped") in events.steps()
    assert len(stub_register) == 1


def test_register_recreate_on_existing_asks_confirm(stub_qdrant, stub_register):
    """`recreate=True` かつ既存があれば承認を求め、承認すれば登録する。"""
    events = EventCollector()
    result = _register_runner(
        RegisterParams(input_file="qa_output/a.csv", collection="faq_anthropic", recreate=True),
        events,
        approve,
    )

    assert result["registered"] is True
    assert ("confirm", "finished") in events.steps()
    assert stub_register[0]["recreate"] is True


def test_register_recreate_rejected_does_not_register(stub_qdrant, stub_register):
    """**拒否したら登録も再作成もしない（既存データは維持）。**"""
    events = EventCollector()
    result = _register_runner(
        RegisterParams(input_file="qa_output/a.csv", collection="faq_anthropic", recreate=True),
        events,
        reject,
    )

    assert result["cancelled"] is True
    assert result["registered"] is False
    assert stub_register == [], "拒否したのに register_to_qdrant が呼ばれた"


def test_register_recreate_timeout_does_not_register(stub_qdrant, stub_register):
    """**タイムアウトしたら登録しない（安全側）。**"""
    result = _register_runner(
        RegisterParams(input_file="qa_output/a.csv", collection="faq_anthropic", recreate=True),
        EventCollector(),
        timeout,
    )

    assert result["cancelled"] is True
    assert "タイムアウト" in result["reason"]
    assert stub_register == []


def test_register_recreate_on_missing_collection_skips_confirm(stub_qdrant, stub_register):
    """`recreate=True` でも**コレクションが無ければ**壊すものが無いので承認不要。"""
    def must_not_be_called(_request):
        raise AssertionError("未作成なのに承認を求めた")

    events = EventCollector()
    result = _register_runner(
        RegisterParams(input_file="qa_output/a.csv", collection="brand_new", recreate=True),
        events,
        must_not_be_called,
    )

    assert result["registered"] is True
    assert ("confirm", "skipped") in events.steps()


def test_register_passes_params_through(stub_qdrant, stub_register):
    """パラメータが `register_to_qdrant` へそのまま渡る。"""
    _register_runner(
        RegisterParams(
            input_file="qa_output/a.csv",
            collection="c",
            batch_size=50,
            embed_workers=4,
            max_docs=10,
            domain="dom",
            provider="gemini",
        ),
        EventCollector(),
        approve,
    )

    kwargs = stub_register[0]
    assert kwargs["collection_name"] == "c"
    assert kwargs["batch_size"] == 50
    assert kwargs["embed_workers"] == 4
    assert kwargs["max_docs"] == 10
    assert kwargs["domain"] == "dom"
    assert kwargs["provider"] == "gemini"


# =============================================================================
# チャンキング
# =============================================================================

def test_chunking_runs_without_llm_api_key(monkeypatch, tmp_path):
    """**API キーが無くても走る。**

    LLM はローカル（Ollama）実行のため API キーが存在しない。以前の実装は
    `ANTHROPIC_API_KEY` 未設定を起動ガードで弾いていたが、そのままでは
    チャンク化が常に失敗するためガードごと削除した。
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    csv = tmp_path / "input.csv"
    csv.write_text("Text\nあいうえお\n", encoding="utf-8")
    output = tmp_path / "out" / "input_chunks.csv"
    output.parent.mkdir()
    output.write_text("Text\nあ\n", encoding="utf-8")

    import services.data_pipeline_service as dps

    monkeypatch.setattr(dps, "resolve_input_file", lambda _p, base=None: csv)
    monkeypatch.setattr(dps, "load_input_text", lambda *a, **k: "あいうえお" * 100)
    monkeypatch.setattr(dps, "run_chunking_sync", lambda *a, **k: ["chunk1"])

    import chunking.csv_text_to_chunks_text_csv as cm

    monkeypatch.setattr(cm, "generate_output_filename", lambda *a, **k: str(output))

    events = EventCollector()
    result = _chunking_runner(
        ChunkingParams(input_file="OUTPUT/input.csv"), events, approve
    )

    assert result is not None
    assert not events.has_error()


def test_chunking_rejects_bad_input_path():
    """許可ディレクトリ外は error（例外を投げない）。"""
    events = EventCollector()
    result = _chunking_runner(ChunkingParams(input_file="logs/app.log"), events, approve)

    assert result is None
    assert events.has_error()


def test_chunking_happy_path(monkeypatch, tmp_path):
    """読み込み → チャンク化 → 出力の 3 ステップが流れる。"""
    csv = tmp_path / "input.csv"
    csv.write_text("Text\nあいうえお\n", encoding="utf-8")
    output = tmp_path / "out" / "input_chunks.csv"
    output.parent.mkdir()
    output.write_text("Text\nあ\n", encoding="utf-8")

    import services.data_pipeline_service as dps

    monkeypatch.setattr(dps, "resolve_input_file", lambda _p, base=None: csv)
    monkeypatch.setattr(dps, "load_input_text", lambda *a, **k: "あいうえお" * 100)
    monkeypatch.setattr(dps, "run_chunking_sync", lambda *a, **k: ["chunk1", "chunk2"])

    import chunking.csv_text_to_chunks_text_csv as cm

    monkeypatch.setattr(cm, "generate_output_filename", lambda *a, **k: str(output))

    events = EventCollector()
    result = _chunking_runner(
        ChunkingParams(input_file="OUTPUT/input.csv", output_dir=str(output.parent)),
        events,
        approve,
    )

    assert result is not None
    assert result["chunks"] == 2
    assert result["output_file"] == str(output)
    assert result["model"] == get_default_ollama_model()
    finished = dict(events.steps("finished"))
    assert set(finished) == {"load", "chunk", "save"}


def test_chunking_model_reaches_chunker(monkeypatch, tmp_path):
    """指定したモデル名が `run_chunking_sync` へ渡ること。"""
    captured: dict = {}

    csv = tmp_path / "input.csv"
    csv.write_text("Text\nあ\n", encoding="utf-8")
    output = tmp_path / "out.csv"
    output.write_text("Text\nあ\n", encoding="utf-8")

    import services.data_pipeline_service as dps

    def fake_run(_text, **kwargs):
        captured.update(kwargs)
        return ["c1"]

    monkeypatch.setattr(dps, "resolve_input_file", lambda _p, base=None: csv)
    monkeypatch.setattr(dps, "load_input_text", lambda *a, **k: "あ" * 50)
    monkeypatch.setattr(dps, "run_chunking_sync", fake_run)

    import chunking.csv_text_to_chunks_text_csv as cm

    monkeypatch.setattr(cm, "generate_output_filename", lambda *a, **k: str(output))

    _chunking_runner(
        ChunkingParams(input_file="OUTPUT/input.csv", model="qwen2.5:7b"),
        EventCollector(),
        approve,
    )

    assert captured["model"] == "qwen2.5:7b"


def test_chunking_empty_text_is_error(monkeypatch, tmp_path):
    """空テキストは error（LLM を呼ばない）。"""
    csv = tmp_path / "input.csv"
    csv.write_text("Text\n", encoding="utf-8")

    import services.data_pipeline_service as dps

    monkeypatch.setattr(dps, "resolve_input_file", lambda _p, base=None: csv)
    monkeypatch.setattr(dps, "load_input_text", lambda *a, **k: "   ")

    events = EventCollector()
    result = _chunking_runner(ChunkingParams(input_file="OUTPUT/input.csv"), events, approve)

    assert result is None
    assert events.has_error()


def test_chunking_failure_is_reported_as_error(monkeypatch, tmp_path):
    """チャンク化中の例外を error イベントへ変換する（Ollama 未起動など）。"""
    csv = tmp_path / "input.csv"
    csv.write_text("Text\nあ\n", encoding="utf-8")

    import services.data_pipeline_service as dps

    def boom(*_a, **_k):
        raise ConnectionError("Ollama へ接続できません")

    monkeypatch.setattr(dps, "resolve_input_file", lambda _p, base=None: csv)
    monkeypatch.setattr(dps, "load_input_text", lambda *a, **k: "あ" * 50)
    monkeypatch.setattr(dps, "run_chunking_sync", boom)

    import chunking.csv_text_to_chunks_text_csv as cm

    monkeypatch.setattr(cm, "generate_output_filename", lambda *a, **k: str(tmp_path / "o.csv"))

    events = EventCollector()
    result = _chunking_runner(ChunkingParams(input_file="OUTPUT/input.csv"), events, approve)

    assert result is None
    assert events.has_error()
    assert any("ConnectionError" in m for m in events.messages())
# =============================================================================
# API 層
# =============================================================================

def _wait(predicate, timeout_s=5.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("条件が満たされなかった")


def test_delete_endpoint_starts_job_and_waits_for_confirm(stub_qdrant):
    """`POST /api/qdrant/delete` は 202 を返し、承認するまで削除しない。"""
    response = client.post("/api/qdrant/delete", json={"collections": ["faq_anthropic"]})
    assert response.status_code == 202
    body = response.json()
    job_id = body["job_id"]
    assert body["stream_url"] == f"/api/data/stream/{job_id}"

    job = job_manager.get(job_id)
    assert job is not None

    # intervention が出るまで待つ。この時点でまだ削除されていないこと
    intervention = _wait(
        lambda: next(
            (e for e in list(job.events)
             if e["type"] == "intervention" and e.get("status") == "waiting"),
            None,
        )
    )
    assert stub_qdrant.deleted == [], "承認前に削除された"

    # 承認を注入
    confirm_response = client.post(
        f"/api/data/confirm/{job_id}",
        json={
            "intervention_id": intervention["data"]["intervention_id"],
            "approve": True,
        },
    )
    assert confirm_response.status_code == 200
    assert confirm_response.json()["status"] == "resolved"

    _wait(lambda: job.done)
    assert stub_qdrant.deleted == ["faq_anthropic"]


def test_delete_endpoint_rejection_keeps_data(stub_qdrant):
    """拒否を注入すると削除されないまま完了する。"""
    response = client.post("/api/qdrant/delete", json={"collections": ["gov_anthropic"]})
    job_id = response.json()["job_id"]
    job = job_manager.get(job_id)

    intervention = _wait(
        lambda: next(
            (e for e in list(job.events)
             if e["type"] == "intervention" and e.get("status") == "waiting"),
            None,
        )
    )
    client.post(
        f"/api/data/confirm/{job_id}",
        json={"intervention_id": intervention["data"]["intervention_id"], "approve": False},
    )

    _wait(lambda: job.done)
    assert stub_qdrant.deleted == []
    assert job.result["cancelled"] is True


def test_delete_endpoint_rejects_empty_list():
    """空リストは Pydantic が 422 で弾く。"""
    assert client.post("/api/qdrant/delete", json={"collections": []}).status_code == 422


def test_result_endpoint_returns_kind(stub_qdrant):
    """結果の形が種別で違うので `kind` を返す。"""
    response = client.post("/api/qdrant/delete", json={"collections": ["faq_anthropic"]})
    job_id = response.json()["job_id"]

    result = client.get(f"/api/data/result/{job_id}")
    assert result.status_code == 200
    assert result.json()["kind"] == "delete"


def test_result_endpoint_404():
    assert client.get("/api/data/result/no_such_job").status_code == 404


def test_stream_endpoint_404():
    assert client.get("/api/data/stream/no_such_job").status_code == 404


def test_chunking_endpoint_validates_params():
    """範囲外のパラメータは 422（LLM を呼ぶ前に弾く）。"""
    base = {"input_file": "OUTPUT/a.csv"}
    assert client.post("/api/chunking/run", json={**base, "workers": 0}).status_code == 422
    assert client.post("/api/chunking/run", json={**base, "workers": 999}).status_code == 422
    assert client.post("/api/chunking/run", json={**base, "block_size": 10}).status_code == 422
    assert client.post("/api/chunking/run", json={"input_file": ""}).status_code == 422


# =============================================================================
# 再購読（タブを離れて戻ったときの復元）
#
# フロントはタブ切替でパネルをアンマウントするため SSE 購読が切れる。
# `activeJobs` に job_id を残して再購読する設計だが、それが成立するのは
# **バックエンドがイベントを先頭からリプレイし、完了ジョブも一定期間残す**
# ためである。この前提が壊れると画面側が黙って進捗を失う。
# =============================================================================

def test_stream_events_replays_from_beginning(stub_qdrant):
    """**購読し直すとイベントが先頭から流れる**（再購読でタイムラインが復元できる）。"""
    response = client.post("/api/qdrant/delete", json={"collections": ["faq_anthropic"]})
    job_id = response.json()["job_id"]
    job = job_manager.get(job_id)

    # 承認待ちまで進める
    intervention = _wait(
        lambda: next(
            (e for e in list(job.events)
             if e["type"] == "intervention" and e.get("status") == "waiting"),
            None,
        )
    )

    # 「タブを離れて戻った」= 新しい購読を開く
    replayed = []
    for event in job.stream_events(poll_timeout=0.1):
        if event is None:  # keepalive = これ以上は来ない
            break
        replayed.append(event)
        if event["type"] == "intervention":
            break

    steps = [(e.get("step"), e.get("status")) for e in replayed if e["type"] == "step"]
    assert ("inspect", "started") in steps, "先頭のステップが復元されていない"
    assert ("inspect", "finished") in steps
    assert ("confirm", "started") in steps
    assert any(e["type"] == "intervention" for e in replayed), "承認待ちが復元されていない"

    # 後片付け（ジョブを完了させる）
    client.post(
        f"/api/data/confirm/{job_id}",
        json={"intervention_id": intervention["data"]["intervention_id"], "approve": False},
    )
    _wait(lambda: job.done)


def test_result_endpoint_reports_running_before_completion(stub_qdrant):
    """承認待ちのジョブは `running` を返す（再購読の前に存在確認できる）。"""
    response = client.post("/api/qdrant/delete", json={"collections": ["gov_anthropic"]})
    job_id = response.json()["job_id"]
    job = job_manager.get(job_id)

    intervention = _wait(
        lambda: next(
            (e for e in list(job.events)
             if e["type"] == "intervention" and e.get("status") == "waiting"),
            None,
        )
    )

    status = client.get(f"/api/data/result/{job_id}")
    assert status.status_code == 200
    assert status.json()["status"] == "running"
    assert status.json()["kind"] == "delete"

    client.post(
        f"/api/data/confirm/{job_id}",
        json={"intervention_id": intervention["data"]["intervention_id"], "approve": False},
    )
    _wait(lambda: job.done)


def test_missing_job_returns_404_not_500(stub_qdrant):
    """**消えた job_id は 404。**

    フロントは 404 を「ジョブはもう無い」と解釈して記憶を捨てる。
    500 やタイムアウトになると、SSE の onerror 経由で
    「切断されました」という誤ったエラーを出してしまう。
    """
    assert client.get("/api/data/result/deadbeef1234").status_code == 404

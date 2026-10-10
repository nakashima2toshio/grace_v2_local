"""`qa_qdrant/make_qa_register_qdrant.py` の起動時チェックを固定するテスト。

2026-09-26 まで次の 2 つが食い違っていた（経緯は `qa_qdrant/docs/make_qa_register_qdrant_ipo.md` §3.3 の 3・5）。

- 起動時に確かめるのは `GOOGLE_API_KEY`（Embedding）だけで、Q/A 生成に使う Ollama へ
  繋がるか・モデルが pull 済みかは確かめなかった。Ollama が落ちていると各チャンクで
  `Connection error` を出しながら `Q/A生成完了: 0 ペア` まで進んでいた。
  → Q/A 生成の前に、データ管理タブ・チャンク化 CLI と同じ
  `services.data_pipeline_service.ollama_unreachable_message()` /
  `model_not_pulled_message()` で確かめ、終了コード 1 で止める。
  Q/A 済み CSV の登録だけなら Ollama は要らない。
- `--provider` は受け取るだけで使われず、何を指定しても Gemini で Embedding していた。
  → `choices=["gemini"]` にして、他の値は argparse が終了コード 2 で拒否する。

Ollama の確認・Q/A 生成・登録は差し替えるので、実 LLM / Qdrant / API キーは不要。
"""

import sys

import pytest

import qa_qdrant.make_qa_register_qdrant as mqr


class _FakePipeline:
    """`QAPipeline` の代わり。生成されたら記録する（事前チェックで止まるなら生成されない）。"""

    instances = []

    def __init__(self, **kwargs):
        _FakePipeline.instances.append(kwargs)

    def run(self, **kwargs):  # pragma: no cover - 事前チェックで止まるテストでは呼ばれない
        raise AssertionError("run() は呼ばれない想定")


@pytest.fixture
def env(monkeypatch):
    """Ollama は「落ちている」状態を既定にし、生成・登録の呼び出しを記録する。"""
    import services.data_pipeline_service as dps

    calls = {"registration": [], "pulled_check": []}

    def fake_registration(**kwargs):
        calls["registration"].append(kwargs)
        return True

    def fake_not_pulled(model):
        calls["pulled_check"].append(model)
        return None

    _FakePipeline.instances = []
    monkeypatch.setenv("GOOGLE_API_KEY", "dummy")
    monkeypatch.setattr(dps, "ollama_unreachable_message", lambda *a, **k: "❌ Ollama に接続できません")
    monkeypatch.setattr(dps, "model_not_pulled_message", fake_not_pulled)
    monkeypatch.setattr(mqr, "QAPipeline", _FakePipeline)
    monkeypatch.setattr(mqr, "run_registration", fake_registration)
    return calls


def _argv(monkeypatch, tmp_path, *args):
    monkeypatch.setattr(sys, "argv", [
        "make_qa_register_qdrant.py",
        "--collection", "tmp_collection",
        "--output", str(tmp_path / "qa"),
        "--ui-output", str(tmp_path / "ui"),
        *args,
    ])


def test_chunk_csv_with_ollama_down_exits_with_1(monkeypatch, tmp_path, env):
    """チャンク済み CSV から Q/A を生成するとき、Ollama に繋がらなければ生成前に終了コード 1。"""
    csv = tmp_path / "chunks.csv"
    csv.write_text("text\n富士山は日本で最も高い山である。\n", encoding="utf-8")
    _argv(monkeypatch, tmp_path, "--input-file", str(csv))

    with pytest.raises(SystemExit) as exc:
        mqr.main()
    assert exc.value.code == 1
    assert _FakePipeline.instances == []
    assert env["registration"] == []


def test_dataset_with_ollama_down_exits_with_1(monkeypatch, tmp_path, env):
    """`--dataset` から Q/A を生成するとき、Ollama に繋がらなければ生成前に終了コード 1。"""
    _argv(monkeypatch, tmp_path, "--dataset", "cc_news")

    with pytest.raises(SystemExit) as exc:
        mqr.main()
    assert exc.value.code == 1
    assert _FakePipeline.instances == []
    assert env["registration"] == []


def test_model_not_pulled_exits_with_1(monkeypatch, tmp_path, env):
    """Ollama は動いていても、`--model` が pull 済みでなければ生成前に終了コード 1。"""
    import services.data_pipeline_service as dps

    monkeypatch.setattr(dps, "ollama_unreachable_message", lambda *a, **k: None)
    monkeypatch.setattr(dps, "model_not_pulled_message", lambda model: f"❌ {model} が pull されていません")
    csv = tmp_path / "chunks.csv"
    csv.write_text("text\n本文\n", encoding="utf-8")
    _argv(monkeypatch, tmp_path, "--input-file", str(csv), "--model", "no-such-model:1b")

    with pytest.raises(SystemExit) as exc:
        mqr.main()
    assert exc.value.code == 1
    assert _FakePipeline.instances == []


def test_qa_csv_does_not_need_ollama(monkeypatch, tmp_path, env):
    """Q/A 済み CSV の登録だけなら、Ollama が落ちていても通る（確認もしない）。"""
    csv = tmp_path / "qa.csv"
    csv.write_text("question,answer\nQ,A\n", encoding="utf-8")
    _argv(monkeypatch, tmp_path, "--input-file", str(csv))

    mqr.main()
    assert len(env["registration"]) == 1
    assert env["registration"][0]["provider"] == "gemini"
    assert env["pulled_check"] == []


def test_non_gemini_provider_is_rejected(monkeypatch, tmp_path, env, capsys):
    """`--provider openai` は黙って Gemini で登録せず、argparse が終了コード 2 で拒否する。"""
    csv = tmp_path / "qa.csv"
    csv.write_text("question,answer\nQ,A\n", encoding="utf-8")
    _argv(monkeypatch, tmp_path, "--input-file", str(csv), "--provider", "openai")

    with pytest.raises(SystemExit) as exc:
        mqr.main()
    assert exc.value.code == 2
    assert "--provider" in capsys.readouterr().err
    assert env["registration"] == []

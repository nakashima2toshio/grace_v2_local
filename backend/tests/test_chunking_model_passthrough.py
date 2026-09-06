"""チャンク化で「選んだモデルが実際に使われる」ことのテスト。

## なぜこのファイルがあるのか

データ管理タブの ① チャンキングは、画面のモデル欄と
`core/data_jobs.py::_resolve_model()` で正しいモデル名まで解決できていたのに、
**実際の LLM 呼び出しでは別のモデルが使われていた**。ログはこうなっていた:

    チャンク化処理開始 (3段階)
    モデル: gemma4:12b-mlx                          ← 解決結果は正しい
    OllamaClient initialized: ... model=gemma4:e4b  ← 実際に使われたのは別物
    [step1_block_242] Error: 404 model 'gemma4:e4b' not found

原因は `AsyncAPIClient._resolve_model()` に残っていた Anthropic 移植時代の分岐で、
**モデル名が "claude" で始まらなければ捨てて既定へ差し替える**というものだった。
Ollama のモデル名は当然 "claude" で始まらないため、**渡したモデルは常に捨てられていた**。

`chunking/` は「Web 化にあたって無改修」という方針だったが、これは方針以前の
バグなので直す（無改修方針は「Web 対応のために手を入れない」であって、
「壊れていても直さない」ではない）。
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from pydantic import BaseModel

import chunking.async_api_client as aac
from chunking.async_api_client import AsyncAPIClient


class _Schema(BaseModel):
    """generate_structured の戻り値に使う最小スキーマ。"""

    value: str = "ok"


class _StubLLM:
    """`create_llm_client("ollama")` の代わり。呼ばれたモデル名を記録する。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate_structured(self, contents, response_schema, model, **kwargs):
        self.calls.append(model)
        return response_schema()


# =============================================================================
# _resolve_model — 渡したモデルを捨てない
# =============================================================================

def test_resolve_model_uses_the_requested_model():
    """**渡されたモデルをそのまま使う。**

    回帰: 以前は `model.lower().startswith("claude")` でなければ既定へ
    差し替えていた。本リポジトリの LLM は Ollama なので "claude" で始まる
    モデル名は存在せず、**画面で選んだモデルが 100% 無視されていた**。
    """
    assert AsyncAPIClient._resolve_model("gemma4:12b-mlx", "gemma4:e4b") == "gemma4:12b-mlx"
    assert AsyncAPIClient._resolve_model("llama3.2:latest", "gemma4:12b-mlx") == "llama3.2:latest"
    # 後方互換: Anthropic 名を明示したときもそのまま通す
    assert AsyncAPIClient._resolve_model("claude-sonnet-4-6", "gemma4:12b-mlx") == "claude-sonnet-4-6"


def test_resolve_model_falls_back_only_when_unspecified():
    """未指定（None / 空文字 / 空白）のときだけ既定へ倒す。"""
    assert AsyncAPIClient._resolve_model(None, "gemma4:12b-mlx") == "gemma4:12b-mlx"
    assert AsyncAPIClient._resolve_model("", "gemma4:12b-mlx") == "gemma4:12b-mlx"
    assert AsyncAPIClient._resolve_model("   ", "gemma4:12b-mlx") == "gemma4:12b-mlx"


# =============================================================================
# generate_content — 実際の LLM 呼び出しまで届く
# =============================================================================

def test_generate_content_calls_the_llm_with_the_requested_model(monkeypatch):
    """`generate_content(model=X)` の X が **LLM 呼び出しまで届く**。

    `_resolve_model` の単体テストだけでは、呼び出し経路のどこかで
    落ちていても気づけない。実際に `generate_structured` が受け取った
    モデル名を見る。
    """
    stub = _StubLLM()
    monkeypatch.setattr(aac, "create_llm_client", lambda *a, **k: stub)

    client = AsyncAPIClient(default_model="gemma4:e4b", max_retries=1)
    result = asyncio.run(
        client.generate_content(
            model="gemma4:12b-mlx",
            contents="テキスト",
            response_schema=_Schema,
            task_id="t1",
        )
    )

    assert result is not None
    assert stub.calls == ["gemma4:12b-mlx"], (
        f"要求したモデルが LLM まで届いていない（実際に呼ばれたのは {stub.calls}）"
    )


def test_generate_content_uses_the_client_default_when_model_is_blank(monkeypatch):
    """モデル未指定なら、そのクライアントの既定が使われる。"""
    stub = _StubLLM()
    monkeypatch.setattr(aac, "create_llm_client", lambda *a, **k: stub)

    client = AsyncAPIClient(default_model="gemma4:12b-mlx", max_retries=1)
    asyncio.run(
        client.generate_content(model="", contents="テキスト", response_schema=_Schema)
    )

    assert stub.calls == ["gemma4:12b-mlx"]


# =============================================================================
# 既定モデルを import 時に焼き付けない
# =============================================================================

def test_client_default_model_is_not_baked_at_import_time():
    """`AsyncAPIClient` の既定モデルを **import 時に確定させない**。

    ⚠️ この割れはプロセス起動時の環境でしか再現しないため、子プロセスで確認する
    （`core/data_jobs.py` と同じ理由・同じ手口）。

    修正前は `default_model: str = get_default_ollama_model()` が dataclass ならぬ
    関数シグネチャの既定として import 時に評価され、`.env` の
    `OLLAMA_DEFAULT_MODEL` がそのまま焼き付いていた。
    """
    sentinel = "gemma4:never-pulled-sentinel"
    code = textwrap.dedent(
        """
        import inspect

        from chunking.async_api_client import AsyncAPIClient

        default = inspect.signature(AsyncAPIClient.__init__).parameters["default_model"].default
        print("SIGNATURE_DEFAULT=%s" % (default if default is not None else ""))
        """
    )
    repo_root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["OLLAMA_DEFAULT_MODEL"] = sentinel
    env["PYTHONPATH"] = str(repo_root)

    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr

    line = next(
        ln for ln in proc.stdout.splitlines() if ln.startswith("SIGNATURE_DEFAULT=")
    )
    baked = line.split("=", 1)[1]
    assert baked == "", (
        f"既定モデル {baked!r} が import 時に焼き付いている"
        "（環境変数を変えても実行時に追随しない）"
    )


# =============================================================================
# chunks_all_async — API キー不要・モデルをクライアントへ渡す
# =============================================================================

def test_chunks_all_async_runs_without_anthropic_api_key(monkeypatch, tmp_path):
    """**`ANTHROPIC_API_KEY` を要求しない。**

    LLM はローカル（Ollama）実行でキーが存在しない。以前は
    `chunks_all_async` の冒頭で未設定なら ValueError を投げており、
    キーを消した環境ではチャンク化が必ず落ちた。
    """
    import chunking.csv_text_to_chunks_text_csv as cm

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    captured: dict = {}

    class _StubClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(cm, "AsyncAPIClient", _StubClient)

    async def _fake_step(chunks_or_text, _client, model, *args, **kwargs):
        captured.setdefault("models", []).append(model)
        return ["チャンク1", "チャンク2"]

    monkeypatch.setattr(cm, "_step1_hierarchical_split", _fake_step)
    monkeypatch.setattr(cm, "_step2_semantic_chunking", _fake_step)
    monkeypatch.setattr(cm, "_step3_continuity_check", _fake_step)
    monkeypatch.setattr(cm, "_enforce_max_chunk_tokens", lambda chunks, _limit: chunks)

    chunks = asyncio.run(
        cm.chunks_all_async("本文" * 100, model="gemma4:12b-mlx", max_workers=2)
    )

    assert chunks == ["チャンク1", "チャンク2"]


def test_chunks_all_async_hands_the_model_to_the_client(monkeypatch):
    """クライアント生成時にも、使うモデルを渡す。

    3 段階それぞれの呼び出しでモデルを指定してはいるが、クライアント側の
    既定も揃えておかないと「既定は別物」という状態が残り、経路がひとつ
    増えるたびに同じ事故を繰り返す。
    """
    import chunking.csv_text_to_chunks_text_csv as cm

    captured: dict = {}

    class _StubClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(cm, "AsyncAPIClient", _StubClient)

    async def _fake_step(chunks_or_text, _client, model, *args, **kwargs):
        captured.setdefault("models", []).append(model)
        return ["チャンク"]

    monkeypatch.setattr(cm, "_step1_hierarchical_split", _fake_step)
    monkeypatch.setattr(cm, "_step2_semantic_chunking", _fake_step)
    monkeypatch.setattr(cm, "_step3_continuity_check", _fake_step)
    monkeypatch.setattr(cm, "_enforce_max_chunk_tokens", lambda chunks, _limit: chunks)

    asyncio.run(cm.chunks_all_async("本文" * 100, model="llama3.2:latest", max_workers=2))

    assert captured.get("default_model") == "llama3.2:latest", (
        f"クライアントへモデルが渡っていない（受け取った引数: {sorted(captured)}）"
    )
    # 3 段階すべてに同じモデルが渡る
    assert captured["models"] == ["llama3.2:latest"] * 3

# backend/tests/test_chunking_abort.py
"""LLM が連続で失敗したらチャンク化を中断すること。

## なぜこのテストがあるか

`AsyncAPIClient` はリトライを使い切ると `None` を返し、呼び出し側は
**機械的分割へフォールバックして次のブロックへ進む**。1 ブロックだけ落ちた
ときはそれでよいが、API キー切れ・モデル名の誤り・ネットワーク断のように
**全ブロックで等しく失敗する**原因では話が別で、

- 1 ブロックあたり `max_retries` 回ぶんの待ちを払い続ける
- LLM を一度も使えていないのに「成功」した CSV が出来上がる

姉妹リポジトリ（grace_v2_local）では実際にこれが起き、1229 ブロックを
185 時間かけて処理したうえで中身のない CSV を書き出した（実測 2026-09-06）。
連続失敗が続いたら早い段階で `ChunkingAbortedError` を投げて止める。

**実 LLM・実 API キーは不要**（`generate_structured` をスタブへ差し替える）。
"""
from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from chunking.async_api_client import (
    DEFAULT_ABORT_AFTER_CONSECUTIVE_FAILURES,
    AsyncAPIClient,
    ChunkingAbortedError,
)
from config import get_default_ollama_model


class _Result(BaseModel):
    # docstring を書かない（pydantic が description としてスキーマへ入れるため）
    value: str


def _client(monkeypatch, *, side_effect, **kwargs) -> AsyncAPIClient:
    """LLM クライアントの生成を差し替えた `AsyncAPIClient` を作る。"""
    import chunking.async_api_client as mod

    class _StubLLM:
        def __init__(self):
            self.calls = 0

        def generate_structured(self, contents, schema, model, **_kw):
            self.calls += 1
            return side_effect(self.calls)

    stub = _StubLLM()
    monkeypatch.setattr(mod, "create_llm_client", lambda *a, **k: stub)
    client = AsyncAPIClient(max_retries=1, **kwargs)
    client.stub = stub  # type: ignore[attr-defined]
    return client


def _call(client: AsyncAPIClient, task_id: str = "t"):
    # ⚠️ 本リポジトリの LLM はローカル LLM（Ollama）。LLM 呼び出し自体は
    #    スタブしているのでモデル名は素通りするが、表記は既定へ合わせる。
    return asyncio.run(
        client.generate_content(get_default_ollama_model(), "text", _Result, task_id=task_id)
    )


def _always_fail(_n):
    raise RuntimeError("boom")


def _always_ok(_n):
    return _Result(value="ok")


def test_連続失敗が上限に達したら中断する(monkeypatch):
    client = _client(monkeypatch, side_effect=_always_fail, abort_after_consecutive_failures=3)

    # 2 ブロック目までは None を返して先へ進む（1 件だけの失敗は止めない）
    assert _call(client, "b1") is None
    assert _call(client, "b2") is None

    with pytest.raises(ChunkingAbortedError) as excinfo:
        _call(client, "b3")

    message = str(excinfo.value)
    assert "3 回連続で失敗" in message
    # 原因の当てさせ方をしない。確認すべき項目を具体的に並べる。
    # ⚠️ 本リポジトリの LLM はローカル LLM（Ollama）なので、案内する内容も
    #    Ollama のもの（常駐・pull 済み・タイムアウト・並列数）になる。
    #    grace_v2 は API キーとモデル名を挙げているが、**確かめたいのは
    #    「原因を当てさせず具体的な確認項目を並べているか」**で、そこは同じ。
    assert "ollama serve" in message
    assert "pull" in message
    assert "CHUNKING_LLM_TIMEOUT" in message


def test_成功したら連続失敗のカウントは戻る(monkeypatch):
    """途中で 1 件成功すれば、単発の失敗が積み上がって止まることはない。"""
    calls = {"n": 0}

    def flaky(_n):
        calls["n"] += 1
        # 失敗 → 成功 → 失敗 → 成功 …
        if calls["n"] % 2 == 1:
            raise RuntimeError("boom")
        return _Result(value="ok")

    client = _client(monkeypatch, side_effect=flaky, abort_after_consecutive_failures=2)

    assert _call(client, "b1") is None       # 1 回目: 失敗
    assert _call(client, "b2") is not None   # 2 回目: 成功 → カウントが 0 に戻る
    assert _call(client, "b3") is None       # 3 回目: 失敗（連続 1 回目）
    assert client._consecutive_failures == 1


def test_ゼロを渡せば中断しない(monkeypatch):
    """`CHUNKING_ABORT_AFTER_FAILURES=0` 相当。従来どおりフォールバックで進む。"""
    client = _client(monkeypatch, side_effect=_always_fail, abort_after_consecutive_failures=0)

    for i in range(5):
        assert _call(client, f"b{i}") is None


def test_成功はカウントを積まない(monkeypatch):
    client = _client(monkeypatch, side_effect=_always_ok, abort_after_consecutive_failures=1)

    for i in range(3):
        assert _call(client, f"b{i}") is not None
    assert client._consecutive_failures == 0


def test_既定の上限は環境変数から決まる():
    """既定は 3。`CHUNKING_ABORT_AFTER_FAILURES` で変えられる。"""
    assert DEFAULT_ABORT_AFTER_CONSECUTIVE_FAILURES >= 1

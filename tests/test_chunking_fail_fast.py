"""チャンク化が「延々と失敗し続けて成功を装う」のを防ぐテスト。

## なぜこのファイルがあるのか

モデル名の取り違えが直ったあと、次はこうなった:

    [step1_block_99] Error: Request timed out.. Retrying in 1s (attempt 1/3)
    ...
    [step1_block_69] Failed after 3 retries. Using fallback.
    Step1: 段落分割:  0%| | 1/1229 [09:03<185:17:07, 543.18s/it]

1 ブロックあたり 543 秒（180 秒のタイムアウト × 3 回）。1229 ブロックを
この調子で回すと **185 時間**かかり、しかも全ブロックがフォールバック
（機械的な分割）なので、出来上がるのは中身のない CSV である。

つまり **失敗しているのに止まらず、最後は「成功」として書き出す**。
これは 404 のときとまったく同じ構図で、原因が変わっても被害は同じ。

そこで「最初の数ブロックが連続で失敗したら、その場で中断する」。
1229 ブロック分の時間を捨てる前に、理由つきで止まる。
"""
from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

import chunking.async_api_client as aac
from chunking.async_api_client import AsyncAPIClient, ChunkingAbortedError


class _Schema(BaseModel):
    value: str = "ok"


class _AlwaysTimeout:
    """generate_structured が必ずタイムアウトするスタブ。"""

    def __init__(self) -> None:
        self.calls = 0

    def generate_structured(self, *_a, **_k):
        self.calls += 1
        raise TimeoutError("Request timed out.")


class _AlwaysOk:
    def generate_structured(self, contents, response_schema, model, **kwargs):
        return response_schema()


def _client(monkeypatch, llm, **kwargs) -> AsyncAPIClient:
    monkeypatch.setattr(aac, "create_llm_client", lambda *a, **k: llm)
    return AsyncAPIClient(default_model="gemma4:12b-mlx", max_retries=1, **kwargs)


# =============================================================================
# 連続失敗で中断する
# =============================================================================

def test_aborts_after_consecutive_failures(monkeypatch):
    """**連続で失敗し続けたら中断する。**

    回帰: 1229 ブロックすべてがタイムアウトしても止まらず、185 時間かけて
    フォールバック（機械的分割）だけの CSV を「成功」として書いていた。
    """
    llm = _AlwaysTimeout()
    client = _client(monkeypatch, llm, abort_after_consecutive_failures=3)

    async def run():
        for i in range(10):
            await client.generate_content(
                model="gemma4:12b-mlx",
                contents="text",
                response_schema=_Schema,
                task_id=f"block_{i}",
            )

    with pytest.raises(ChunkingAbortedError) as excinfo:
        asyncio.run(run())

    message = str(excinfo.value)
    assert "3" in message, "何回連続で失敗したのかが分からない"
    assert "timed out" in message.lower(), "最後のエラー内容が伝わっていない"


def test_success_resets_the_failure_streak(monkeypatch):
    """途中で 1 回でも成功したら連続失敗はリセットする。

    たまたま失敗が散らばっただけのジョブを、合計回数で中断させない。
    """
    class _Flaky:
        def __init__(self) -> None:
            self.n = 0

        def generate_structured(self, contents, response_schema, model, **kwargs):
            self.n += 1
            # 2 回に 1 回失敗する（連続 2 回は起きない）
            if self.n % 2 == 1:
                raise TimeoutError("Request timed out.")
            return response_schema()

    client = _client(monkeypatch, _Flaky(), abort_after_consecutive_failures=3)

    async def run():
        results = []
        for i in range(12):
            results.append(
                await client.generate_content(
                    model="gemma4:12b-mlx",
                    contents="text",
                    response_schema=_Schema,
                    task_id=f"block_{i}",
                )
            )
        return results

    results = asyncio.run(run())  # ChunkingAbortedError が出ないこと
    assert any(r is not None for r in results)


def test_abort_can_be_disabled(monkeypatch):
    """`abort_after_consecutive_failures=0` で従来どおり（中断しない）。

    大量バッチを「失敗分はフォールバックで埋めて完走させたい」運用のための逃げ道。
    """
    client = _client(monkeypatch, _AlwaysTimeout(), abort_after_consecutive_failures=0)

    async def run():
        return [
            await client.generate_content(
                model="gemma4:12b-mlx",
                contents="text",
                response_schema=_Schema,
                task_id=f"block_{i}",
            )
            for i in range(5)
        ]

    assert asyncio.run(run()) == [None] * 5


def test_successful_run_never_aborts(monkeypatch):
    """成功し続ける限り中断しない（当たり前だが固定しておく）。"""
    client = _client(monkeypatch, _AlwaysOk(), abort_after_consecutive_failures=3)

    async def run():
        return [
            await client.generate_content(
                model="gemma4:12b-mlx",
                contents="text",
                response_schema=_Schema,
                task_id=f"block_{i}",
            )
            for i in range(10)
        ]

    assert all(r is not None for r in asyncio.run(run()))


# =============================================================================
# 出力トークン上限
# =============================================================================

def test_output_token_cap_is_not_absurd(monkeypatch):
    """出力上限を実際に必要な大きさに保つ。

    回帰: `chunks_all_async` は `max_output_tokens=16384` を渡していた。
    チャンクは `MAX_CHUNK_TOKENS = 512` で切られ、入力ブロックも既定
    1000 文字なので、16384 は **実際に使う量の 16〜32 倍**。Ollama では
    この値が `num_predict` になり、モデルが停止トークンを出さないと
    その上限まで生成し続けるため、1 リクエストの最悪時間を跳ね上げる。
    """
    import chunking.csv_text_to_chunks_text_csv as cm

    captured: dict = {}

    class _StubClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(cm, "AsyncAPIClient", _StubClient)

    async def _fake_step(chunks_or_text, _client, model, *args, **kwargs):
        return ["チャンク"]

    monkeypatch.setattr(cm, "_step1_hierarchical_split", _fake_step)
    monkeypatch.setattr(cm, "_step2_semantic_chunking", _fake_step)
    monkeypatch.setattr(cm, "_step3_continuity_check", _fake_step)
    monkeypatch.setattr(cm, "_enforce_max_chunk_tokens", lambda chunks, _limit: chunks)

    asyncio.run(cm.chunks_all_async("本文" * 100, model="gemma4:12b-mlx", max_workers=2))

    cap = captured.get("max_output_tokens")
    assert cap is not None, "max_output_tokens を渡していない"
    assert cap <= 8192, (
        f"出力上限 {cap} が大きすぎる（モデルの max_output は 8192）。"
        "num_predict がそのまま最悪生成時間になる"
    )


def test_client_timeout_is_configurable(monkeypatch):
    """チャンク化の LLM タイムアウトを環境変数で調整できる。

    ローカル LLM が遅いだけの環境で、対話用の既定（180 秒）に縛られると
    正常なジョブまでタイムアウトで潰れる。**既定は変えない**が、
    `CHUNKING_LLM_TIMEOUT` で明示的に延ばせるようにする。
    """
    captured: dict = {}

    def _fake_create(provider, **kwargs):
        captured.update(kwargs)
        return _AlwaysOk()

    monkeypatch.setattr(aac, "create_llm_client", _fake_create)
    monkeypatch.setenv("CHUNKING_LLM_TIMEOUT", "900")

    AsyncAPIClient(default_model="gemma4:12b-mlx")

    assert captured.get("timeout") == 900.0, (
        f"CHUNKING_LLM_TIMEOUT が効いていない（渡された引数: {sorted(captured)}）"
    )


def test_client_timeout_defaults_to_none(monkeypatch):
    """未設定なら timeout を指定しない（`OllamaClient` の既定に従う）。"""
    captured: dict = {}

    def _fake_create(provider, **kwargs):
        captured.update(kwargs)
        return _AlwaysOk()

    monkeypatch.setattr(aac, "create_llm_client", _fake_create)
    monkeypatch.delenv("CHUNKING_LLM_TIMEOUT", raising=False)

    AsyncAPIClient(default_model="gemma4:12b-mlx")

    assert captured.get("timeout") is None

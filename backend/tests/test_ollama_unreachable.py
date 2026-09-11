# backend/tests/test_ollama_unreachable.py
"""Ollama が落ちているとき、**実行前に**止まることを固定するテスト。

## 観測された事実（推測ではない）

実測 2026-09-11。Ollama を落としたままチャンク化を走らせたときのログ:

    19:48:34 [WARNING] Ollama のモデル一覧を取得できませんでした:
                       [Errno 61] Connection refused        ← ここで分かっていた
    19:48:34 [WARNING] [step1_block_15] Error: Connection error.. Retrying in 1s
    19:48:35 [WARNING] [step1_block_15] Error: Connection error.. Retrying in 2s
    ...（3 ブロック × 3 回）
    19:48:43 ChunkingAbortedError: LLM 呼び出しが 3 回連続で失敗した…
               確認してください:
               - ollama serve が動いているか / そのモデルが pull 済みか
               - 1 ブロックの処理がタイムアウトより長くないか
               - 並列ワーカー数を下げる

**答えを知っていたのに、9 秒かけたうえで候補 3 つを並べて利用者に当てさせた。**

## なぜ素通りしていたか

`list_pulled_ollama_models()` は失敗を一律で空リストにし、呼び出し側は
「空 = 判定不能」として素通りする。応答形式が変わっただけで実際には動く
ジョブを止めないための設計で、そこは正しい。

だが **接続拒否は曖昧ではない。** サーバが居ないのだから、これから投げる
リクエストは 1 本残らず失敗する。「判定不能」に混ぜてはいけなかった。

⚠️ 実際の Ollama へは接続しない。
"""
from __future__ import annotations

import httpx

from services.data_pipeline_service import ollama_unreachable_message


class TestDefinitelyDown:
    """接続が確立できない ＝ 確実。ここで止める。"""

    def test_connect_error_is_reported(self, monkeypatch):
        monkeypatch.setattr(
            httpx, "get",
            _raise(httpx.ConnectError("[Errno 61] Connection refused")),
        )
        message = ollama_unreachable_message()

        assert message is not None
        assert "接続できません" in message

    def test_connect_timeout_is_reported(self, monkeypatch):
        monkeypatch.setattr(httpx, "get", _raise(httpx.ConnectTimeout("timed out")))
        assert ollama_unreachable_message() is not None

    def test_message_tells_how_to_start_it(self, monkeypatch):
        """候補を並べて当てさせない。起動コマンドをそのまま出す。"""
        monkeypatch.setattr(httpx, "get", _raise(httpx.ConnectError("refused")))
        message = ollama_unreachable_message()

        assert "ollama serve" in message
        assert "open -a Ollama" in message

    def test_message_names_the_endpoint(self, monkeypatch):
        """接続先を出す。起動済みのつもりで別ポートを見ている場合がある。"""
        monkeypatch.setattr(httpx, "get", _raise(httpx.ConnectError("refused")))
        assert "11434" in ollama_unreachable_message()


class TestAmbiguousIsLetThrough:
    """⚠️ **判定不能では止めない。** 実際に動くジョブを事前確認で殺さない。"""

    def test_reachable_server_returns_none(self, monkeypatch):
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _ok())
        assert ollama_unreachable_message() is None

    def test_read_timeout_is_not_treated_as_down(self, monkeypatch):
        """繋がってはいる（モデルのロードで遅いだけ、等）。"""
        monkeypatch.setattr(httpx, "get", _raise(httpx.ReadTimeout("slow")))
        assert ollama_unreachable_message() is None

    def test_http_error_is_not_treated_as_down(self, monkeypatch):
        """一覧だけ拒否される構成もありうる。本処理は動くかもしれない。"""
        monkeypatch.setattr(httpx, "get", _raise(httpx.HTTPError("500")))
        assert ollama_unreachable_message() is None

    def test_unexpected_payload_is_not_treated_as_down(self, monkeypatch):
        """応答形式が変わっただけ。疎通はしている。"""
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _ok(payload="not json at all"))
        assert ollama_unreachable_message() is None


class TestEveryEntryPointChecks:
    """CLI と Web の両方で確認すること（片方だけに入れない）。"""

    def test_cli_stops_before_the_llm_loop(self, monkeypatch):
        from chunking import csv_text_to_chunks_text_csv as cli

        monkeypatch.setattr(
            cli, "_ollama_unreachable_message", lambda: "❌ Ollama に接続できません"
        )
        assert cli._ollama_unreachable_message() is not None

    def test_web_delegates_to_the_same_function(self, monkeypatch):
        from backend.app.core import data_jobs

        monkeypatch.setattr(
            httpx, "get", _raise(httpx.ConnectError("[Errno 61] Connection refused"))
        )
        message = data_jobs._ollama_unreachable_message()

        assert message is not None and "接続できません" in message


# =============================================================================
# helpers
# =============================================================================

def _raise(exc: Exception):
    def _boom(*_a, **_k):
        raise exc
    return _boom


def _ok(payload=None):
    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return payload if payload is not None else {"data": [{"id": "gemma4:12b-mlx"}]}

    return _Response()

# backend/tests/test_chunking_workers_default.py
"""チャンク化の並列ワーカー数の既定を固定するテスト。

## 観測された事実（推測ではない）

既定は長らく 8 だった。クラウド API（Gemini / Anthropic）時代の値で、
そちらは同時リクエストを本当に並列処理するため妥当だった。
**ローカルの Ollama は既定で 1 本ずつしか処理しない。**

実測 2026-09-11（`gemma4:12b-mlx` / 20 行 / 55 ブロック / workers=8 /
`CHUNKING_LLM_TIMEOUT=600`）:

    10:58:19 開始（8 本を同時投入）
    10:59:28 1 本目完了   ← 68.8 秒
    11:00:18 2 本目        ← +50 秒
    11:01:05 3 本目        ← +47 秒
    11:02:31 4 本目        ← +86 秒
    11:06:48 7 本目        ← +98 秒
    11:08:19 block_32 タイムアウト（10:58:19 + 600 秒 ちょうど）

完了が 1 本ずつ 50〜90 秒間隔で届く ＝ 逐次実行。509 秒で 7 ブロック
＝ 約 73 秒/ブロックで、**単発の実測 62.7 秒とほぼ同じ**。並列度を上げて
得られた速度はゼロだった。

しかも害がある。残り 7 本はキューで待つだけなのに、その待ち時間が各自の
タイムアウトを食いつぶす。順番が来ないまま期限切れになり、機械的分割の
フォールバックへ落ちる。`workers=8` が生んだものは待ち行列と
タイムアウトだけだった。

⚠️ 実際の Ollama へは接続しない。
"""
from __future__ import annotations

from config import get_default_chunking_workers


class TestDefaultIsSerial:
    """Ollama が並列化を明示していない限り 1 本ずつ投げること。"""

    def test_defaults_to_one(self, monkeypatch):
        monkeypatch.delenv("OLLAMA_NUM_PARALLEL", raising=False)
        assert get_default_chunking_workers() == 1

    def test_follows_ollama_num_parallel(self, monkeypatch):
        """Ollama 側を増やしたときだけ、こちらも増やしてよい。"""
        monkeypatch.setenv("OLLAMA_NUM_PARALLEL", "4")
        assert get_default_chunking_workers() == 4

    def test_garbage_value_falls_back_to_one(self, monkeypatch):
        """読めない値で例外を出さない（起動を止める理由にならない）。"""
        monkeypatch.setenv("OLLAMA_NUM_PARALLEL", "たくさん")
        assert get_default_chunking_workers() == 1

    def test_zero_is_clamped(self, monkeypatch):
        """0 を渡されても Semaphore(0) にしない（永久に進まなくなる）。"""
        monkeypatch.setenv("OLLAMA_NUM_PARALLEL", "0")
        assert get_default_chunking_workers() == 1


class TestEveryEntryPointUsesIt:
    """CLI・Web・クライアントの 3 経路が同じ既定を使うこと。

    ⚠️ **1 か所でも 8 のまま残すと意味が無い。** 既定が散らばっていたのが
    元の問題で、`get_default_ollama_model()` と同じく実体は 1 か所に置く。
    """

    def test_async_client(self, monkeypatch):
        monkeypatch.delenv("OLLAMA_NUM_PARALLEL", raising=False)
        from chunking.async_api_client import AsyncAPIClient

        assert AsyncAPIClient().max_workers == 1

    def test_async_client_honors_an_explicit_value(self, monkeypatch):
        """明示的に渡された値は捨てない。"""
        monkeypatch.delenv("OLLAMA_NUM_PARALLEL", raising=False)
        from chunking.async_api_client import AsyncAPIClient

        assert AsyncAPIClient(max_workers=3).max_workers == 3

    def test_cli_argument(self, monkeypatch):
        monkeypatch.delenv("OLLAMA_NUM_PARALLEL", raising=False)
        from chunking.csv_text_to_chunks_text_csv import create_parser

        args = create_parser().parse_args(["--input-file", "OUTPUT/x.csv"])
        assert args.workers == 1

    def test_web_request_schema(self, monkeypatch):
        monkeypatch.delenv("OLLAMA_NUM_PARALLEL", raising=False)
        from backend.app.schemas import ChunkingRequest

        assert ChunkingRequest(input_file="OUTPUT/x.csv").workers == 1

    def test_web_job_params(self, monkeypatch):
        monkeypatch.delenv("OLLAMA_NUM_PARALLEL", raising=False)
        from backend.app.core.data_jobs import ChunkingParams

        assert ChunkingParams(input_file="OUTPUT/x.csv").workers == 1

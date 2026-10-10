"""CLI でも「そのモデルが pull 済みか」を実行前に確かめることのテスト。

## なぜこのファイルがあるのか

事前チェック（`model_not_pulled_message`）は Web（データ管理タブ）側の
`_chunking_runner` にしか入っておらず、**CLI 経路には無かった**。
そのため `python -m chunking.csv_text_to_chunks_text_csv` で未 pull の
モデル名を使うと、1 ブロックにつき 404 を 3 回叩いてから次のブロックへ進み、
連続失敗の中断（`ChunkingAbortedError`）に届くまで待たされる。

実測 2026-09-11 のログ:

    [step1_block_34] Error: 404 ... model 'gemma4:e4b' not found. Retrying in 1s
    [step1_block_34] Error: 404 ... Retrying in 2s
    [step1_block_34] Error: 404 ... Retrying in 4s
    [step1_block_34] Failed after 3 retries. Using fallback.
    （同じことを block_44 / block_54 でも繰り返してから中断）

**pull 済みなのは `gemma4:e4b-mlx` で、指定は `gemma4:e4b`。差は 1 語**。
事前チェックが働いていれば、pull 済み一覧つきで 1 秒で返せた。
"""
from __future__ import annotations

import chunking.csv_text_to_chunks_text_csv as cm
import services.data_pipeline_service as dps


def test_message_lists_the_pulled_models(monkeypatch):
    """**pull 済み一覧をメッセージに載せる。**

    実際の取り違えは `gemma4:e4b` と `gemma4:e4b-mlx` のような 1 語差で起きる。
    「見つかりません」だけでは何が違うのか分からない。
    """
    monkeypatch.setattr(
        dps, "list_pulled_ollama_models",
        lambda *a, **k: ["gemma4:e4b-mlx", "gemma4:12b-mlx"],
    )

    message = dps.model_not_pulled_message("gemma4:e4b")

    assert message is not None
    assert "gemma4:e4b" in message
    assert "gemma4:e4b-mlx" in message, "pull 済み一覧が出ていないと差分に気づけない"
    assert "gemma4:12b-mlx" in message


def test_pulled_model_passes(monkeypatch):
    monkeypatch.setattr(
        dps, "list_pulled_ollama_models", lambda *a, **k: ["gemma4:12b-mlx"]
    )
    assert dps.model_not_pulled_message("gemma4:12b-mlx") is None


def test_unknown_list_does_not_block(monkeypatch):
    """一覧を取れない（＝判定不能）なら素通しする。

    事前確認を理由に、実際には動くジョブを止めない。
    """
    monkeypatch.setattr(dps, "list_pulled_ollama_models", lambda *a, **k: [])
    assert dps.model_not_pulled_message("なんでもよい") is None


# =============================================================================
# CLI がその判定を使っていること
# =============================================================================

def test_cli_has_the_preflight_hook(monkeypatch):
    """CLI 側に事前チェックの入口があること。

    回帰: この関数が無く、CLI は 404 を 9 回叩いてから中断していた。
    """
    monkeypatch.setattr(
        dps, "list_pulled_ollama_models", lambda *a, **k: ["gemma4:12b-mlx"]
    )

    assert cm._model_not_pulled_message("gemma4:12b-mlx") is None

    message = cm._model_not_pulled_message("gemma4:e4b")
    assert message is not None
    assert "gemma4:12b-mlx" in message


def test_cli_preflight_never_blocks_on_import_failure(monkeypatch):
    """事前確認が壊れていてもジョブは止めない。

    ここは本処理ではないので、確認できないことを理由に落とさない
    （`list_pulled_ollama_models` が失敗を空リストで返すのと同じ方針）。
    """
    import builtins

    real_import = builtins.__import__

    def _boom(name, *args, **kwargs):
        if name == "services.data_pipeline_service":
            raise ImportError("配線の都合で読めない状況を模す")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _boom)

    assert cm._model_not_pulled_message("gemma4:whatever") is None

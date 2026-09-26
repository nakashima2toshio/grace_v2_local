"""`QAPipeline(dataset_name=...)` の種別（`config["type"]`）を固定するテスト。

2026-09-26 まで、`config.DATASET_CONFIGS` の各エントリに `type` キーが無く、
`QAPipeline` が `self.config.get("type", "unknown")` で既定値へ倒れていた。種別は

- 出力ファイル名（`qa_pairs_<種別>_<日時>.csv`）と UI 用 CSV 名（`qa_pairs_<種別>.csv`）
- チャンク ID の接頭辞（ID 列の無い CSV では `<種別>_chunk_<n>`）
- 途中経過の保存ファイル（`qa_progress_<種別>.jsonl`。再開時に処理済みチャンクを飛ばす）

に使われるため、**どのデータセットでも `unknown` になり、途中で落ちたデータセット A の
途中経過を、別のデータセット B の再開が読んでしまう**状態だった（ID も同じ `unknown_chunk_<n>`）。
`type` が無ければデータセット名を使うよう直した（経緯は
`qa_qdrant/docs/make_qa_register_qdrant_ipo.md` §3.2）。

`QAPipeline` は `__init__` を通さずに組み立てる（SmartQAGenerator の LLM クライアント生成を避ける）。
"""

import pandas as pd
import pytest

from config import DATASET_CONFIGS
from qa_generation.pipeline import QAPipeline


def _pipeline(tmp_path, dataset_name=None, input_file=None):
    p = QAPipeline.__new__(QAPipeline)
    p.dataset_name = dataset_name
    p.input_file = input_file
    p.output_dir = str(tmp_path)
    p.text_column = None
    p.config = p._load_config()
    return p


@pytest.mark.parametrize("name", ["cc_news", "livedoor", "wikipedia_ja"])
def test_dataset_type_is_the_dataset_name(tmp_path, name):
    """`type` キーの無いデータセットは、データセット名を種別にする（`unknown` にしない）。"""
    assert "type" not in DATASET_CONFIGS[name], "前提: 設定に type キーが無い"
    assert _pipeline(tmp_path, dataset_name=name).config["type"] == name


def test_datasets_do_not_share_progress_file(tmp_path):
    """別のデータセットが同じ途中経過ファイルを読み書きしないこと。"""
    a = _pipeline(tmp_path, dataset_name="cc_news")
    b = _pipeline(tmp_path, dataset_name="livedoor")
    assert a._progress_path() != b._progress_path()
    assert a._progress_path().name == "qa_progress_cc_news.jsonl"


def test_progress_of_another_dataset_is_not_resumed(tmp_path):
    """データセット A の途中経過が残っていても、データセット B の再開に混ざらないこと。"""
    a = _pipeline(tmp_path, dataset_name="cc_news")
    b = _pipeline(tmp_path, dataset_name="livedoor")

    # A の 0 番チャンクの結果が途中経過として残っている（A が途中で落ちた状態）
    a_chunk_id = a._load_chunks_from_csv(pd.DataFrame({"text": ["A の本文"]}))[0]["id"]
    a._append_progress(a_chunk_id, [{"question": "A の質問", "answer": "A の答え"}])

    b_chunk_id = b._load_chunks_from_csv(pd.DataFrame({"text": ["B の本文"]}))[0]["id"]
    assert b_chunk_id != a_chunk_id
    assert b._load_progress() == {}


def test_input_file_type_is_unchanged(tmp_path):
    """`--input-file` の種別は従来どおり入力ファイル名（拡張子なし）。"""
    p = _pipeline(tmp_path, input_file="output_chunked/cc_news_1per_chunks.csv")
    assert p.config["type"] == "cc_news_1per_chunks"

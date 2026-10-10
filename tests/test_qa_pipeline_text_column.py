"""`--text-column` が Q/A 生成まで届くことを固定するテスト。

2026-09-26 まで、`qa_qdrant/make_qa_register_qdrant.py` は `--text-column` を入口の判定にだけ使い、
`QAPipeline` へ渡していなかった。`QAPipeline._load_chunks_from_csv()` は
`text` → `Combined_Text` → `content` → `chunk_text` の固定順で列を探すため、
`body` と `text` の両方がある CSV で `--text-column body` を指定しても、`text` 列が使われていた
（経緯は `qa_qdrant/docs/make_qa_register_qdrant_ipo.md` §3.3 の 4）。

`QAPipeline(text_column=...)` を足し、CLI から渡すよう直した。既定 `None` は従来の自動検出のままなので、
データ管理タブ（`services/data_pipeline_service.py`）と `make_qa.py` の挙動は変わらない。

実 LLM / Qdrant / API キーは不要（`QAPipeline` は `__init__` を通さずに組み立てるか、差し替える）。
"""

import sys

import pandas as pd
import pytest

import qa_qdrant.make_qa_register_qdrant as mqr
from qa_generation.pipeline import QAPipeline


def _pipeline(text_column=None):
    """SmartQAGenerator の初期化（LLM クライアント生成）を避けるため `__init__` を通さない。"""
    p = QAPipeline.__new__(QAPipeline)
    p.text_column = text_column
    p.config = {"type": "doc"}
    return p


def test_explicit_text_column_wins_over_auto_detection():
    """`text` 列があっても、指定した列の本文でチャンクを作ること。"""
    df = pd.DataFrame({"text": ["使わない本文"], "body": ["指定した本文"]})
    chunks = _pipeline("body")._load_chunks_from_csv(df)
    assert [c["text"] for c in chunks] == ["指定した本文"]


def test_explicit_text_column_missing_raises():
    """指定した列が無ければ、別の列へ黙って落ちずに ValueError。"""
    df = pd.DataFrame({"text": ["本文"]})
    with pytest.raises(ValueError, match="body"):
        _pipeline("body")._load_chunks_from_csv(df)


def test_default_keeps_auto_detection_order():
    """未指定（None）なら従来どおり `text` → `Combined_Text` … の順で探す。"""
    df = pd.DataFrame({"Combined_Text": ["結合本文"], "content": ["別の本文"]})
    chunks = _pipeline()._load_chunks_from_csv(df)
    assert [c["text"] for c in chunks] == ["結合本文"]


class _FakePipeline:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        _FakePipeline.instances.append(self)

    def run(self, **kwargs):
        from pathlib import Path

        out = Path(self.kwargs["output_dir"])
        out.mkdir(parents=True, exist_ok=True)
        qa_csv = out / "qa_pairs_chunks_20260926_101500.csv"
        qa_csv.write_text("question,answer\nQ,A\n", encoding="utf-8")
        return {"saved_files": {"qa_csv": str(qa_csv)}, "qa_count": 1}


@pytest.mark.parametrize(
    ("header", "extra_args", "expected"),
    [
        ("body", ["--text-column", "body"], "body"),          # 独自の列名
        ("text,body", ["--text-column", "body"], "body"),     # text もあるが指定を優先
        ("Combined_Text", [], "Combined_Text"),               # 既定 text が無く Combined_Text へ
    ],
)
def test_cli_passes_text_column_to_pipeline(monkeypatch, tmp_path, header, extra_args, expected):
    """CLI の判定で決めた列名が `QAPipeline(text_column=...)` へ渡ること。"""
    import services.data_pipeline_service as dps

    csv = tmp_path / "chunks.csv"
    values = ",".join("本文" for _ in header.split(","))
    csv.write_text(f"{header}\n{values}\n", encoding="utf-8")

    _FakePipeline.instances = []
    monkeypatch.setenv("GOOGLE_API_KEY", "dummy")
    monkeypatch.setattr(dps, "ollama_unreachable_message", lambda *a, **k: None)
    monkeypatch.setattr(dps, "model_not_pulled_message", lambda *a, **k: None)
    monkeypatch.setattr(mqr, "QAPipeline", _FakePipeline)
    monkeypatch.setattr(mqr, "run_registration", lambda **kwargs: True)
    monkeypatch.setattr(sys, "argv", [
        "make_qa_register_qdrant.py",
        "--input-file", str(csv),
        "--collection", "tmp_collection",
        "--output", str(tmp_path / "qa"),
        "--ui-output", str(tmp_path / "ui"),
        *extra_args,
    ])

    mqr.main()

    assert len(_FakePipeline.instances) == 1
    assert _FakePipeline.instances[0].kwargs["text_column"] == expected

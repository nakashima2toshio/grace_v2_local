# tests/test_register_ui_csv_keeps_input.py
"""`register_to_qdrant` の UI 用 CSV が、入力の CSV そのものを上書きしない。

## 背景（実測 2026-11-07・Mac）

    python qa_qdrant/register_to_qdrant.py --input-file qa_output/saas_docs.csv \\
        --collection saas_docs_anthropic --recreate
    📋 UI用ファイル作成中: qa_output/saas_docs.csv

UI 用 CSV は `<ui_output_dir>/<正規化したファイル名>`（既定 `qa_output/`）へ
`question` / `answer` の 2 列だけを書く。入力が `qa_output/` にあると出力先が**入力と同じファイル**になり、
`topic` などほかの列を黙って消した CSV で元データを上書きしていた（git で管理している
`qa_output/saas_docs.csv` が変更扱いになり、次の登録ではペイロードの `topic` も失われる）。
`qa_output/ec_ad_rules_statutes_template.csv`（`rule_id` / `source_url` / `status` 列を持つ）も同じ目に遭う。

出力先が入力と同じファイルなら書かずに警告する。

⚠️ Qdrant・Embedding には接続しない（`qa_qdrant.register_to_qdrant` の依存をスタブにする）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

import qa_qdrant.register_to_qdrant as reg

ROWS = pd.DataFrame({
    "question": ["稼働状況はどこで確認できますか？", "メンテナンスの通知は？"],
    "answer": ["ステータスページ（status.example.jp）です。", "7日前までに通知します。"],
    "topic": ["障害", "メンテナンス"],
})


def _stub_qdrant(monkeypatch):
    client = SimpleNamespace(
        get_collections=lambda: SimpleNamespace(collections=[]),
        get_collection=lambda _name: SimpleNamespace(points_count=len(ROWS)),
    )
    monkeypatch.setattr(reg, "create_qdrant_client", lambda: client)
    monkeypatch.setattr(reg, "create_or_recreate_collection_for_qdrant", lambda *a, **k: None)
    monkeypatch.setattr(reg, "embed_texts_for_qdrant", lambda texts: [[0.0] * 3 for _ in texts])
    monkeypatch.setattr(
        reg, "build_points_for_qdrant",
        lambda df, vectors, **k: [SimpleNamespace(payload={}) for _ in range(len(df))],
    )
    monkeypatch.setattr(reg, "upsert_points_to_qdrant", lambda *a, **k: None)


class TestUiCsvDoesNotOverwriteInput:
    def test_入力がqa_outputにあっても元のCSVを上書きしない(self, tmp_path, monkeypatch):
        _stub_qdrant(monkeypatch)
        qa_output = tmp_path / "qa_output"
        qa_output.mkdir()
        src = qa_output / "saas_docs.csv"
        ROWS.to_csv(src, index=False)
        before = src.read_text(encoding="utf-8")

        ok = reg.register_to_qdrant(
            input_file=str(src), collection_name="saas_docs_anthropic", recreate=True,
            create_ui_csv=True, ui_output_dir=str(qa_output),
        )

        assert ok is True
        assert src.read_text(encoding="utf-8") == before, "入力の CSV が UI 用 CSV で上書きされた"
        assert list(pd.read_csv(src).columns) == ["question", "answer", "topic"]

    def test_出力先が別ならUI用CSVを従来どおり書く(self, tmp_path, monkeypatch):
        _stub_qdrant(monkeypatch)
        src = tmp_path / "saas_docs.csv"
        ROWS.to_csv(src, index=False)
        ui_dir = tmp_path / "ui"

        ok = reg.register_to_qdrant(
            input_file=str(src), collection_name="saas_docs_anthropic", recreate=True,
            create_ui_csv=True, ui_output_dir=str(ui_dir),
        )

        assert ok is True
        assert list(pd.read_csv(ui_dir / "saas_docs.csv").columns) == ["question", "answer"]

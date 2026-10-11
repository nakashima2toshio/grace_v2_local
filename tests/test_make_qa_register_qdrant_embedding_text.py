# tests/test_make_qa_register_qdrant_embedding_text.py
"""Q/A を Qdrant へ登録するとき、3 つの入口が**同じ文**を Embedding すること。

## なぜこのテストがあるか

2026-10-11 まで、`make_qa_register_qdrant.py` の Phase 2（`run_registration()`）だけが
`question` 列**だけ**を Embedding し、`register_to_qdrant.py` とデータ管理タブの「③ Qdrant 登録」
（どちらも `register_to_qdrant.detect_text_column()`）は `question + "\\n" + answer` を Embedding していた。
同じ Q/A CSV でも、どの入口で登録したかでベクトルの意味が変わり、RAG のスコア分布（しきい値の前提）も変わる。

`question + answer` にそろえた（回答側の語でも検索に当たる。payload の `answer` を Embedding に使わないと、
質問文が短い・言い換えが多いときに取りこぼす）。文の組み立ては `detect_text_column()` の 1 か所だけにする。

**Embedding・Qdrant は不要**（`embed_texts_for_qdrant` と Qdrant クライアントをスタブにする）。
"""
from __future__ import annotations

import pandas as pd

import qa_qdrant.make_qa_register_qdrant as mqr
from qa_qdrant.register_to_qdrant import detect_text_column


def _run(monkeypatch, tmp_path, df: pd.DataFrame) -> tuple[bool, list[str]]:
    embedded: list[str] = []

    def fake_embed(texts):
        embedded.extend(texts)
        return [[0.1, 0.2, 0.3] for _ in texts]

    monkeypatch.setattr(mqr, "embed_texts_for_qdrant", fake_embed)
    monkeypatch.setattr(mqr, "create_qdrant_client", lambda: object())
    monkeypatch.setattr(mqr, "create_or_recreate_collection_for_qdrant", lambda *a, **k: None)
    monkeypatch.setattr(mqr, "upsert_points_to_qdrant", lambda *a, **k: None)

    csv = tmp_path / "qa_pairs_doc.csv"
    df.to_csv(csv, index=False)
    ok = mqr.run_registration(
        csv_path=str(csv),
        collection_name="doc_qa",
        recreate=False,
        batch_size=2,
        provider="gemini",
        ui_output_dir=str(tmp_path / "qa_output"),
    )
    return ok, embedded


QA = pd.DataFrame({
    "question": ["住民票の写しはどこで取れますか？", "手数料はいくらですか？", "コンビニでも取れますか？"],
    "answer": ["市役所の窓口で取得できます。", "1通300円です。", "マイナンバーカードがあれば取れます。"],
})


def test_質問と回答をつないだ文をEmbeddingする(monkeypatch, tmp_path):
    ok, embedded = _run(monkeypatch, tmp_path, QA)
    assert ok
    assert embedded[0] == "住民票の写しはどこで取れますか？\n市役所の窓口で取得できます。"
    assert len(embedded) == 3


def test_register_to_qdrantと同じ文になる(monkeypatch, tmp_path):
    """データ管理タブ・`register_to_qdrant.py` と同じ組み立て方（`detect_text_column()`）であること。"""
    _ok, embedded = _run(monkeypatch, tmp_path, QA)
    expected, _method = detect_text_column(QA)
    assert embedded == expected


def test_質問か回答の列が無ければ登録しない(monkeypatch, tmp_path):
    ok, embedded = _run(monkeypatch, tmp_path, QA[["question"]])
    assert ok is False
    assert embedded == []

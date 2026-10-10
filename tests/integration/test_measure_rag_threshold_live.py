"""`scripts/measure_rag_threshold.py::measure` を実 Qdrant で流す結合テスト。

Embedding API は呼ばない（質問ごとに固定ベクトルを返すスタブ）。本番の RAG ツールと同じ
`search_rag_knowledge_base_structured()` が実 Qdrant で返すスコアを、許可コレクションの中で
最大のものとして拾えるか、緩和閾値（0.5）未満を「なし」にするかを見る。
共用 Qdrant を壊さないよう、`grace_it_` のコレクションだけを使う。
"""

import pytest
from qdrant_client.http import models

import agent_tools
from scripts import measure_rag_threshold

pytestmark = pytest.mark.integration


def test_measure_takes_the_best_score_across_collections(qdrant_client, temp_collection, monkeypatch):
    qdrant_client.create_collection(
        temp_collection, vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE))
    qdrant_client.upsert(temp_collection, points=[
        models.PointStruct(id=1, vector=[1.0, 0.0], payload={"question": "住民票", "answer": "300 円"}),
    ])
    vectors = {"同じ向き": [1.0, 0.0], "45度": [1.0, 1.0], "直交": [0.0, 1.0]}
    measure_rag_threshold._query_vectors.cache_clear()
    monkeypatch.setattr(measure_rag_threshold, "_query_vectors", lambda q: (vectors[q], None))
    monkeypatch.setattr(agent_tools, "_collections_cache", None)   # 直前に作ったコレクションを見せる

    rows = measure_rag_threshold.measure(
        ["同じ向き", "45度", "直交"], [temp_collection, "grace_it_does_not_exist"])

    tops = {q: s for q, s, _ in rows}
    assert tops["同じ向き"] == pytest.approx(1.0, abs=1e-4)
    assert tops["45度"] == pytest.approx(0.7071, abs=1e-3)
    assert tops["直交"] is None            # 緩和閾値 0.5 にも届かない → 本番でも 0 件
    assert {c for q, s, c in rows if s is not None} == {temp_collection}

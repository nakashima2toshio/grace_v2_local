"""実 Qdrant に対する qdrant_client_wrapper / services.qdrant_service の結合テスト。

スタブでは分からない「Qdrant が実際にどう受け取るか」を見る:
コレクション設定・ポイント ID の冪等性・dense / hybrid 検索の経路選択。
ベクトルは固定値で、Embedding API は呼ばない。
"""

import logging

import pandas as pd
import pytest
from qdrant_client.http import models

import qdrant_client_wrapper as wrapper
from services import qdrant_service

pytestmark = pytest.mark.integration

DIM = 4


def _qa_df(n: int) -> pd.DataFrame:
    return pd.DataFrame(
        {"question": [f"q{i}" for i in range(n)], "answer": [f"a{i}" for i in range(n)]}
    )


def _one_hot(i: int) -> list:
    v = [0.0] * DIM
    v[i % DIM] = 1.0
    return v


def _count(client, name: str) -> int:
    return client.count(name, exact=True).count


# ----------------------------------------------------------------------
# コレクション作成（qdrant_client_wrapper.create_or_recreate_collection）
# ----------------------------------------------------------------------
class TestCreateCollection:
    def test_sparse_collection_has_dense_sparse_and_domain_index(self, qdrant_client, temp_collection):
        wrapper.create_or_recreate_collection(
            qdrant_client, temp_collection, vector_size=DIM, use_sparse=True
        )

        params = qdrant_client.get_collection(temp_collection).config.params
        assert params.vectors.size == DIM
        assert params.vectors.distance == models.Distance.COSINE
        assert "text-sparse" in params.sparse_vectors
        assert "domain" in qdrant_client.get_collection(temp_collection).payload_schema

    def test_recreate_drops_existing_points(self, qdrant_client, temp_collection):
        wrapper.create_or_recreate_collection(qdrant_client, temp_collection, vector_size=DIM)
        df = _qa_df(2)
        wrapper.upsert_points(
            qdrant_client, temp_collection,
            wrapper.build_points(df, [_one_hot(0), _one_hot(1)], "d", "x.csv"),
        )
        assert _count(qdrant_client, temp_collection) == 2

        wrapper.create_or_recreate_collection(
            qdrant_client, temp_collection, recreate=True, vector_size=DIM
        )

        assert _count(qdrant_client, temp_collection) == 0

    def test_without_recreate_keeps_existing_points(self, qdrant_client, temp_collection):
        wrapper.create_or_recreate_collection(qdrant_client, temp_collection, vector_size=DIM)
        wrapper.upsert_points(
            qdrant_client, temp_collection,
            wrapper.build_points(_qa_df(1), [_one_hot(0)], "d", "x.csv"),
        )

        wrapper.create_or_recreate_collection(qdrant_client, temp_collection, vector_size=DIM)

        assert _count(qdrant_client, temp_collection) == 1


# ----------------------------------------------------------------------
# ポイント登録（build_points / upsert_points）
# ----------------------------------------------------------------------
class TestUpsert:
    def test_upsert_twice_does_not_duplicate(self, qdrant_client, temp_collection):
        """stable_point_id が決定的なので、同じ入力の再登録は件数を増やさない。"""
        wrapper.create_or_recreate_collection(qdrant_client, temp_collection, vector_size=DIM)
        df = _qa_df(3)
        vectors = [_one_hot(i) for i in range(3)]

        for _ in range(2):
            points = wrapper.build_points(df, vectors, "d", "/path/to/qa.csv")
            wrapper.upsert_points(qdrant_client, temp_collection, points)

        assert _count(qdrant_client, temp_collection) == 3
        payloads = [
            p.payload for p in qdrant_client.scroll(temp_collection, limit=10)[0]
        ]
        assert {p["question"] for p in payloads} == {"q0", "q1", "q2"}
        assert {p["source"] for p in payloads} == {"qa.csv"}
        assert {p["schema"] for p in payloads} == {"qa:v1"}

    def test_upsert_in_small_batches_returns_total(self, qdrant_client, temp_collection):
        wrapper.create_or_recreate_collection(qdrant_client, temp_collection, vector_size=DIM)
        df = _qa_df(5)
        points = wrapper.build_points(df, [_one_hot(i) for i in range(5)], "d", "x.csv")

        n = wrapper.upsert_points(qdrant_client, temp_collection, points, batch_size=2)

        assert n == 5
        assert _count(qdrant_client, temp_collection) == 5


# ----------------------------------------------------------------------
# 検索（qdrant_client_wrapper.search_collection）
# ----------------------------------------------------------------------
@pytest.fixture
def dense_collection(qdrant_client, temp_collection):
    """dense のみ（名前なしベクトル）。q0..q3 がそれぞれ軸 0..3 を向く。"""
    wrapper.create_or_recreate_collection(qdrant_client, temp_collection, vector_size=DIM)
    wrapper.upsert_points(
        qdrant_client, temp_collection,
        wrapper.build_points(_qa_df(DIM), [_one_hot(i) for i in range(DIM)], "d", "x.csv"),
    )
    return temp_collection


@pytest.fixture
def hybrid_collection(qdrant_client, temp_collection):
    """services 版で作る hybrid コレクション（dense は名前付き "default" + sparse）。

    q0 は dense で、q1 は sparse でクエリに一致させる。
    """
    qdrant_service.create_or_recreate_collection_for_qdrant(
        qdrant_client, temp_collection, recreate=True, vector_size=DIM, use_sparse=True
    )
    df = _qa_df(2)
    points = qdrant_service.build_points_for_qdrant(
        df, [_one_hot(0), _one_hot(1)], domain="d", source_file="x.csv",
        sparse_vectors=[
            models.SparseVector(indices=[5], values=[1.0]),
            models.SparseVector(indices=[9], values=[1.0]),
        ],
    )
    qdrant_service.upsert_points_to_qdrant(qdrant_client, temp_collection, points)
    return temp_collection


QUERY_NEAR_Q0 = [0.9, 0.1, 0.0, 0.0]
SPARSE_MATCHES_Q1 = models.SparseVector(indices=[9], values=[1.0])


class TestSearch:
    def test_dense_search_returns_nearest_first(self, qdrant_client, dense_collection):
        hits = wrapper.search_collection(qdrant_client, dense_collection, _one_hot(2), limit=2)

        assert [h["payload"]["question"] for h in hits][0] == "q2"
        assert hits[0]["score"] == pytest.approx(1.0, abs=1e-5)

    def test_score_threshold_drops_unrelated_points(self, qdrant_client, dense_collection):
        hits = wrapper.search_collection(
            qdrant_client, dense_collection, _one_hot(1), limit=4, score_threshold=0.5
        )

        assert [h["payload"]["question"] for h in hits] == ["q1"]

    def test_sparse_query_on_dense_only_collection_uses_dense(self, qdrant_client, dense_collection, caplog):
        """sparse 未設定のコレクションには hybrid を投げず、dense だけで答える。"""
        with caplog.at_level(logging.INFO, logger=wrapper.logger.name):
            hits = wrapper.search_collection(
                qdrant_client, dense_collection, QUERY_NEAR_Q0,
                sparse_vector=SPARSE_MATCHES_Q1, limit=2,
            )

        assert hits[0]["payload"]["question"] == "q0"
        assert wrapper._vector_config_cache[dense_collection]["has_sparse"] is False
        assert "Hybrid Search成功" not in caplog.text

    def test_hybrid_search_lets_sparse_change_ranking(self, qdrant_client, hybrid_collection, caplog):
        """dense だけなら q0 が 1 位。sparse が q1 に一致すると RRF で q1 が上がる。"""
        dense_hits = wrapper.search_collection(
            qdrant_client, hybrid_collection, QUERY_NEAR_Q0, limit=2
        )
        with caplog.at_level(logging.INFO, logger=wrapper.logger.name):
            hybrid_hits = wrapper.search_collection(
                qdrant_client, hybrid_collection, QUERY_NEAR_Q0,
                sparse_vector=SPARSE_MATCHES_Q1, limit=2,
            )

        assert dense_hits[0]["payload"]["question"] == "q0"
        assert hybrid_hits[0]["payload"]["question"] == "q1"
        assert "Hybrid Search成功" in caplog.text
        assert wrapper._vector_config_cache[hybrid_collection] == {
            "is_named_vector": True, "dense_vector_name": "default", "has_sparse": True,
        }

    def test_missing_collection_returns_empty_list(self, qdrant_client, temp_collection):
        """存在しないコレクションでも例外を外へ出さず [] を返す。"""
        assert wrapper.search_collection(qdrant_client, temp_collection, _one_hot(0)) == []


# ----------------------------------------------------------------------
# 一覧・閲覧（get_all_collections / QdrantDataFetcher）
# ----------------------------------------------------------------------
class TestListing:
    def test_get_all_collections_reports_points_count(self, qdrant_client, dense_collection):
        listed = {c["name"]: c for c in wrapper.get_all_collections(qdrant_client)}

        assert listed[dense_collection]["points_count"] == DIM

    def test_data_fetcher_returns_payload_columns(self, qdrant_client, dense_collection):
        df = wrapper.QdrantDataFetcher(qdrant_client).fetch_collection_points(dense_collection)

        assert len(df) == DIM
        assert {"ID", "question", "answer", "domain"} <= set(df.columns)

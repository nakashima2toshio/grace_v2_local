"""`CollectionInfo.vectors_count` が無い qdrant-client でも件数を返すことの回帰テスト。

qdrant-client 1.19.1 の `CollectionInfo` には `vectors_count` が無い（1.15.1 には
Optional で残っていて、新しい Qdrant サーバー相手だと None になる）。以前は
`info.vectors_count` を直接読んでいたため AttributeError を握りつぶし、
「Error / N/A」の行や `{"error": ...}` を返していた（2026-10-10 に実 Qdrant で確認）。
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from qdrant_client.http import models

import qdrant_client_wrapper
from services import qdrant_service


def _info(**counts):
    """`CollectionInfo` 相当のスタブ。渡さなかった属性は存在しない。"""
    return SimpleNamespace(
        status=models.CollectionStatus.GREEN,
        config=SimpleNamespace(
            params=SimpleNamespace(vectors=SimpleNamespace(size=3072, distance="Cosine"))
        ),
        **counts,
    )


def _client(info):
    client = MagicMock()
    col = MagicMock()
    col.name = "c1"
    client.get_collections.return_value.collections = [col]
    client.get_collection.return_value = info
    return client


# vectors_count が無い（1.19.1）/ None（1.15.1 + 新しいサーバー）/ ある（旧サーバー）
CASES = [
    pytest.param({"points_count": 42, "indexed_vectors_count": 40}, 42, id="missing"),
    pytest.param({"vectors_count": None, "points_count": 42, "indexed_vectors_count": 40}, 42, id="none"),
    pytest.param({"vectors_count": 84, "points_count": 42, "indexed_vectors_count": 40}, 84, id="present"),
]

FETCHERS = [
    pytest.param(qdrant_service.QdrantDataFetcher, id="services"),
    pytest.param(qdrant_client_wrapper.QdrantDataFetcher, id="wrapper"),
]


@pytest.mark.parametrize("counts, expected", CASES)
def test_collection_vectors_count(counts, expected):
    assert qdrant_client_wrapper.collection_vectors_count(_info(**counts)) == expected


@pytest.mark.parametrize("fetcher_cls", FETCHERS)
@pytest.mark.parametrize("counts, expected", CASES)
def test_fetch_collections_returns_counts(fetcher_cls, counts, expected):
    df = fetcher_cls(_client(_info(**counts))).fetch_collections()

    row = df.iloc[0]
    assert row["Collection"] == "c1"
    assert row["Status"] != "Error"
    assert row["Vectors Count"] == expected
    assert row["Points Count"] == 42
    assert row["Indexed Vectors"] == 40


@pytest.mark.parametrize("fetcher_cls", FETCHERS)
@pytest.mark.parametrize("counts, expected", CASES)
def test_fetch_collection_info_returns_counts(fetcher_cls, counts, expected):
    info = fetcher_cls(_client(_info(**counts))).fetch_collection_info("c1")

    assert "error" not in info
    assert info["vectors_count"] == expected
    assert info["points_count"] == 42
    assert info["indexed_vectors"] == 40
    assert info["config"] == {"vector_size": 3072, "distance": "Cosine"}


@pytest.mark.parametrize("counts, expected", CASES)
def test_get_all_collections_simple_returns_counts(counts, expected):
    rows = qdrant_service.get_all_collections_simple(_client(_info(**counts)))

    assert rows == [
        {"name": "c1", "vectors_count": expected, "points_count": 42, "status": "green"}
    ]

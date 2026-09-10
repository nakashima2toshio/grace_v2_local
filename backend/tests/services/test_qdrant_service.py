from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from qdrant_client.http import models

from services.qdrant_service import (
    QdrantDataFetcher,
    QdrantHealthChecker,
    build_inputs_for_embedding,
    build_points_for_qdrant,
    embed_query_for_search,
    embed_texts_for_qdrant,
    get_collection_embedding_params,
    get_dynamic_collection_mapping,
    load_csv_for_qdrant,
    map_collection_to_csv,
    merge_collections,
)


@pytest.fixture
def mock_qdrant_client():
    client = MagicMock()
    return client

class TestQdrantService:

    def test_map_collection_to_csv(self):
        # 現行の map_collection_to_csv は「完全一致のみ」をサポートする。
        # （命名規則依存の 'qa_' プレフィックス除去ロジックは廃止済み）
        with patch("os.path.exists") as mock_exists:
            # Case 1: Exact match
            mock_exists.return_value = True
            assert map_collection_to_csv("test") == "test.csv"

            # Case 2: 完全一致するファイルが無ければ None を返す
            #         （旧仕様の 'qa_test' -> 'test.csv' 変換は廃止された）
            mock_exists.return_value = False
            assert map_collection_to_csv("qa_test") is None

    def test_get_dynamic_collection_mapping(self, mock_qdrant_client):
        # Mock collections
        mock_c1 = MagicMock()
        mock_c1.name = "col1"
        mock_qdrant_client.get_collections.return_value.collections = [mock_c1]
        
        # Mock payload scroll
        mock_point = MagicMock()
        mock_point.payload = {"source": "source.csv"}
        mock_qdrant_client.scroll.return_value = ([mock_point], None)
        
        mapping = get_dynamic_collection_mapping(mock_qdrant_client)
        assert mapping["col1"] == "source.csv"

    def test_get_collection_embedding_params(self, mock_qdrant_client):
        # Case 1: 3072 dim
        mock_info = MagicMock()
        mock_info.config.params.vectors.size = 3072
        mock_qdrant_client.get_collection.return_value = mock_info
        
        params = get_collection_embedding_params(mock_qdrant_client, "c")
        assert params["dims"] == 3072
        assert params["model"] == "gemini-embedding-001"

    def test_health_checker(self):
        checker = QdrantHealthChecker()
        with patch.object(checker, "check_port", return_value=True), \
             patch("services.qdrant_service.QdrantClient") as MockClient:
            
            MockClient.return_value.get_collections.return_value.collections = []
            success, msg, metrics = checker.check_qdrant()
            assert success is True
            assert metrics is not None

    def test_data_fetcher(self, mock_qdrant_client):
        fetcher = QdrantDataFetcher(mock_qdrant_client)
        
        # fetch_collections
        mock_c = MagicMock()
        mock_c.name = "c1"
        mock_qdrant_client.get_collections.return_value.collections = [mock_c]
        mock_qdrant_client.get_collection.return_value.points_count = 100
        
        df = fetcher.fetch_collections()
        assert len(df) == 1
        assert df.iloc[0]["Collection"] == "c1"
        
        # fetch_collection_points
        mock_point = MagicMock()
        mock_point.id = 1
        mock_point.payload = {"k": "v"}
        mock_qdrant_client.scroll.return_value = ([mock_point], None)
        
        df_points = fetcher.fetch_collection_points("c1")
        assert len(df_points) == 1
        assert df_points.iloc[0]["k"] == "v"

    def test_load_csv_for_qdrant(self):
        with patch("os.path.exists", return_value=True), \
             patch("pandas.read_csv") as mock_read:
            
            mock_read.return_value = pd.DataFrame({
                "Question": ["q"], "Answer": ["a"]
            })
            
            df = load_csv_for_qdrant("dummy.csv")
            assert "question" in df.columns
            assert "answer" in df.columns

    def test_build_inputs_for_embedding(self):
        df = pd.DataFrame({"question": ["q"], "answer": ["a"]})
        inputs = build_inputs_for_embedding(df, include_answer=True)
        assert inputs[0] == "q\na"

    @patch("services.qdrant_service.create_embedding_client")
    def test_embed_texts_for_qdrant(self, mock_create):
        mock_client = MagicMock()
        mock_client.embed_texts.return_value = [[0.1]*3072]
        mock_create.return_value = mock_client
        
        vecs = embed_texts_for_qdrant(["text"], model="gemini")
        assert len(vecs) == 1
        assert len(vecs[0]) == 3072

    def test_build_points_for_qdrant(self):
        df = pd.DataFrame({"question": ["q"], "answer": ["a"]})
        vectors = [[0.1]*3072]
        
        points = build_points_for_qdrant(df, vectors, "domain", "source.csv")
        assert len(points) == 1
        assert isinstance(points[0], models.PointStruct)
        assert points[0].payload["question"] == "q"

    @patch("services.qdrant_service.create_embedding_client")
    def test_embed_query_for_search(self, mock_create):
        mock_client = MagicMock()
        mock_client.embed_text.return_value = [0.1]*3072
        mock_create.return_value = mock_client
        
        vec = embed_query_for_search("q", dims=3072)
        assert len(vec) == 3072

    def test_merge_collections(self, mock_qdrant_client):
        # Mock scroll
        p1 = models.Record(id=1, vector=[0.1]*3072, payload={"a": 1})
        mock_qdrant_client.scroll.side_effect = [([p1], None), ([p1], None)] # Called for each source col
        mock_qdrant_client.get_collection.return_value.points_count = 1
        
        result = merge_collections(mock_qdrant_client, ["s1"], "target")

        assert result["success"] is True
        mock_qdrant_client.upsert.assert_called()


class TestContentBasedPointId:
    """#51: 内容ハッシュベースの決定的ポイントID（位置非依存・再登録べき等）"""

    def test_stable_point_id_deterministic(self):
        """ポイントIDが決定的であること（旧 hash() はプロセスごとに変動した）"""
        from qdrant_client_wrapper import stable_point_id

        a = stable_point_id("domain-source.csv-0")
        b = stable_point_id("domain-source.csv-0")
        assert a == b
        assert 0 < a < 2 ** 63
        # 既知のキーに対する期待値（プロセスを跨いだ安定性の固定値検証）
        assert stable_point_id("x-y-0") == 3147582548484565541

    def test_stable_point_id_distinct_keys(self):
        from qdrant_client_wrapper import stable_point_id

        assert stable_point_id("d-s-0") != stable_point_id("d-s-1")

    def test_build_points_uses_stable_ids(self):
        """build_points_for_qdrant の ID が同一入力で再現すること"""
        df = pd.DataFrame({"question": ["q1", "q2"], "answer": ["a1", "a2"]})
        vectors = [[0.1] * 3072, [0.2] * 3072]

        points1 = build_points_for_qdrant(df, vectors, "domain", "source.csv", start_index=0)
        points2 = build_points_for_qdrant(df, vectors, "domain", "source.csv", start_index=0)

        assert [p.id for p in points1] == [p.id for p in points2]
        assert points1[0].id != points1[1].id

    def test_point_id_is_position_independent(self):
        """同一Q/Aは行の並び順が変わっても同一IDになること（位置非依存）"""
        df_a = pd.DataFrame({"question": ["q1", "q2"], "answer": ["a1", "a2"]})
        df_b = pd.DataFrame({"question": ["q2", "q1"], "answer": ["a2", "a1"]})
        vectors = [[0.1] * 3072, [0.2] * 3072]

        pa = build_points_for_qdrant(df_a, vectors, "dom", "src.csv")
        pb = build_points_for_qdrant(df_b, vectors, "dom", "src.csv")

        # q1/a1 の ID は並び順に関係なく一致する
        assert pa[0].id == pb[1].id

    def test_point_id_changes_with_content(self):
        """内容が異なれば別ID、同一内容なら同一ID"""
        df1 = pd.DataFrame({"question": ["q"], "answer": ["a"]})
        df3 = pd.DataFrame({"question": ["q"], "answer": ["b"]})
        v = [[0.1] * 3072]

        assert build_points_for_qdrant(df1, v, "d", "s.csv")[0].id != \
               build_points_for_qdrant(df3, v, "d", "s.csv")[0].id

    def test_point_id_independent_of_start_index(self):
        """start_index が変わっても内容が同じなら同一ID（旧実装は位置依存だった）"""
        df = pd.DataFrame({"question": ["q1"], "answer": ["a1"]})
        v = [[0.1] * 3072]

        id0 = build_points_for_qdrant(df, v, "d", "s.csv", start_index=0)[0].id
        id100 = build_points_for_qdrant(df, v, "d", "s.csv", start_index=100)[0].id
        assert id0 == id100

    def test_provenance_columns_preserved(self):
        """chunk_id/topic/doc_id があれば payload に保持、無い行には付与しない"""
        df = pd.DataFrame([
            {"question": "q1", "answer": "a1", "chunk_id": 5, "topic": "t"},
            {"question": "q2", "answer": "a2", "chunk_id": None, "topic": None},
        ])
        v = [[0.1] * 3072, [0.2] * 3072]
        points = build_points_for_qdrant(df, v, "d", "s.csv")

        assert points[0].payload.get("chunk_id") == 5
        assert points[0].payload.get("topic") == "t"
        # NaN/None は付与しない
        assert "chunk_id" not in points[1].payload
        assert "topic" not in points[1].payload

    def test_text_csv_content_key(self):
        """Q/Aカラムが無い汎用テキストCSVは本文カラムでIDを決定する"""
        df1 = pd.DataFrame({"text": ["hello world"]})
        df2 = pd.DataFrame({"text": ["hello   world"]})  # 空白差は正規化で吸収
        v = [[0.1] * 3072]
        assert build_points_for_qdrant(df1, v, "d", "s.csv")[0].id == \
               build_points_for_qdrant(df2, v, "d", "s.csv")[0].id

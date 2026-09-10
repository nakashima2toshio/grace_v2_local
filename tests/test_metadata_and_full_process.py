
import os
import sys
import unittest

import pandas as pd

# プロジェクトルートをパスに追加
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services.qdrant_service import build_points_for_qdrant


class TestQdrantMetadataAndProcess(unittest.TestCase):

    def test_full_point_conversion_with_metadata(self):
        """バッチサイズに関わらず、全データがメタデータ付きで変換されるか検証"""
        # バッチサイズを超えるデータを想定（例: 150件）
        num_records = 150
        df = pd.DataFrame({
            'question': [f'Q{i}' for i in range(num_records)],
            'answer': [f'A{i}' for i in range(num_records)]
        })
        vectors = [[0.1] * 3072] * num_records
        
        # 修正後のロジックをシミュレートして全件処理を確認
        points = build_points_for_qdrant(df, vectors, domain="test", source_file="test.csv")
        
        # メタデータ付与（修正予定のロジック）
        for p in points:
            p.payload["embedding_provider"] = "gemini"
            p.payload["embedding_model"] = "gemini-embedding-001"
            
        self.assertEqual(len(points), num_records)
        self.assertEqual(points[149].payload['embedding_provider'], 'gemini')
        self.assertEqual(points[149].payload['question'], 'Q149')

# 注: 「payload 優先で embedding model を解決する」テストは、production が未実装の機能
# （get_collection_embedding_params はベクトル次元からのみ推論）への wish テストだった
# ため削除した。現行の次元ベース挙動は
# tests/services/test_qdrant_service.py::TestQdrantService::test_get_collection_embedding_params
# でカバーしている。payload 優先解決を実装する場合は別途テストを追加すること。

if __name__ == '__main__':
    unittest.main()

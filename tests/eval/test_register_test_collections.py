# -*- coding: utf-8 -*-
"""register_test_collections のデータ整合性テスト。

実 Qdrant / Embedding には依存せず、以下を検証する:
- データ CSV が存在し question/answer を持つこと
- コレクション名が業界プロファイル（PROFILES.collections）と一致すること
- out-of-scope 検証用の「穴」（意図的な未カバー領域）が保たれていること
"""

import csv

from eval.vertical.register_test_collections import DATA_DIR, VERTICAL_COLLECTIONS


def _load_rows(csv_name: str) -> list[dict]:
    path = DATA_DIR / csv_name
    assert path.exists(), f"データ CSV が存在しません: {path}"
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _all_csv_names() -> list[str]:
    return [csv_name for pairs in VERTICAL_COLLECTIONS.values() for _, csv_name in pairs]


class TestDataFiles:
    def test_all_csv_have_question_answer(self):
        for csv_name in _all_csv_names():
            rows = _load_rows(csv_name)
            assert len(rows) >= 5, f"{csv_name}: データが少なすぎます（{len(rows)} 件）"
            for row in rows:
                assert row.get("question"), f"{csv_name}: question が空の行があります"
                assert row.get("answer"), f"{csv_name}: answer が空の行があります"

    def test_no_duplicate_questions(self):
        for csv_name in _all_csv_names():
            questions = [row["question"] for row in _load_rows(csv_name)]
            assert len(questions) == len(set(questions)), f"{csv_name}: question が重複しています"


class TestProfileConsistency:
    def test_collections_match_profiles(self):
        """登録先コレクション名がプロファイルの検索スコープに含まれること。"""
        from agent_support_example import PROFILES

        for vertical, pairs in VERTICAL_COLLECTIONS.items():
            profile_collections = set(PROFILES[vertical].collections)
            for collection, _ in pairs:
                assert collection in profile_collections, (
                    f"{vertical}: {collection} が PROFILES['{vertical}'].collections にありません"
                )

    def test_collection_naming_convention(self):
        for pairs in VERTICAL_COLLECTIONS.values():
            for collection, _ in pairs:
                assert collection.endswith("_anthropic"), collection


class TestCoverageHoles:
    """out-of-scope 分岐の検証に必要な「穴」が埋まっていないこと（§2 条件5）。"""

    HOLES = {
        "ec": "入荷予定",       # 「この商品の入荷予定日は？」は未カバーのまま
        "saas": "売上見込",     # 「御社の来期の売上見込みは？」は未カバーのまま
        "gov": "税制改正",      # 「来年の税制改正の予測は？」は未カバーのまま
    }

    def test_out_of_scope_terms_not_covered(self):
        for vertical, term in self.HOLES.items():
            for _, csv_name in VERTICAL_COLLECTIONS[vertical]:
                for row in _load_rows(csv_name):
                    text = f"{row['question']}{row['answer']}"
                    assert term not in text, (
                        f"{csv_name}: out-of-scope 検証用の穴 '{term}' がカバーされています"
                    )

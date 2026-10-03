# backend/tests/test_review_keyword_excludes.py
"""第1段の候補検出で、除外語の一部として現れた keyword を一致と数えないこと。

## 回帰（実測 2026-10-02 / 10-03・化粧品LP案・grace_v2_local のローカル LLM）

keihyo-09（数量限定の根拠）の keyword「限定」が「今だけ期間限定」の中で一致し、
期間の表示を数量限定の候補として ③ Detect へ回していた。残すかどうかは LLM 次第で、
同じモデルでも誤検知が出る回と出ない回があった。

⚠️ LLM にも Qdrant にも接続しない。
"""
from __future__ import annotations

from backend.app.core.review_agent import run_review_agent_core
from backend.app.core.review_gates import select_candidate_rules
from backend.app.core.rulesets import EC_AD


def _ids(text: str) -> set:
    return {c.rule_id for c in select_candidate_rules(text, EC_AD)}


class TestKeihyo09IgnoresPeriodLimits:

    def test_period_limit_is_not_a_quantity_limit(self):
        ids = _ids("・今だけ期間限定 通常価格 12,000円 → 4,980円")
        assert "keihyo-09" not in ids
        # 期間の表示は keihyo-07 が引き続き候補にする
        assert "keihyo-07" in ids

    def test_other_period_phrases(self):
        for text in ("本日限定の特別価格", "今月限定キャンペーン", "週末限定セール"):
            assert "keihyo-09" not in _ids(text), text

    def test_quantity_limits_still_match(self):
        for text in ("限定100個のみ", "先着50名様", "在庫僅少です", "数量限定"):
            assert "keihyo-09" in _ids(text), text

    def test_both_in_one_segment_still_matches(self):
        """除外語を伏せても、同じ段落の数量限定は拾う。"""
        assert "keihyo-09" in _ids("期間限定・限定100個の特別セット")

    def test_pipeline_does_not_judge_keihyo09_on_the_cosmetic_lp(self, review_stub):
        document = (
            "当社の美容液は業界No.1の実力です。\n\n"
            "・今だけ期間限定 通常価格 12,000円 → 4,980円\n"
            "・送料無料"
        )
        run_review_agent_core(document)
        judged = {rid for _t, rid, _e in review_stub.detect_calls}
        assert "keihyo-09" not in judged
        assert "keihyo-07" in judged


class TestCriteria:

    def test_keihyo09_criteria_exclude_period_limits(self):
        description = EC_AD.rule_by_id("keihyo-09").description
        assert "「期間限定」「本日限定」など**期間**を限る表示" in description

    def test_public_description_is_unchanged(self):
        """要旨（登録済みの規程行・検索クエリ）は変えていない → 再登録は不要。"""
        assert EC_AD.rule_by_id("keihyo-09").public_description().endswith(
            "実態を伴わない数量限定表示は取引条件の有利誤認に該当しうる。")

# backend/tests/test_review_document_excerpt.py
"""**表記漏れの「該当箇所」が文書の大半を占めない**ことを固定するテスト。

## 背景（実測 2026-08-17 23:50 / 2026-08-18 21:41 — 2 回とも同じ）

8 行の LP に対する 2 件の指摘が、どちらも該当箇所として **7 行ぶん**を表示していた。

    中 / 商品の引渡時期 / 特定商取引法 第11条 / 確定
    該当箇所: ■ 特定商取引法に基づく表記 販売業者: 株式会社サンプル
              運営責任者: 山田太郎 所在地: 東京都千代田区1-1-1
              電話番号: 03-0000-0000 販売価格: 4,980円（税込）
              返品: 商品到着後8日以内、未開封に限り返品可能（送料はお客様負担）

「引渡時期が**無い**」という指摘に対して 7 行を塗っても、**直す場所を示せていない**。

## なぜ #85 の空スパン処理をすり抜けたか

#85 では「指し示せる箇所が無ければ空スパン（ハイライトしない）」にした。しかし
`_build_finding` は「excerpt が文書内に見つかるか」だけを見ていた。

    offset = segment.text.find(excerpt)
    if offset >= 0:          # ← 表記ブロック丸ごとでも「見つかった」
        start = segment.start + offset

LLM は「該当箇所を抜き出せ」と言われると、表記漏れであっても表記ブロックを丸ごと
返してくる。それは当然**文書内に見つかる**ので位置解決に成功し、空スパンの分岐へ
落ちなかった。

## 上限を 2 つ持つ理由（or 判定）

- **割合**（`DOCUMENT_EXCERPT_MAX_RATIO = 0.4`）… 短い文書向け。実測の 160 文字の
  LP では約 0.87 が返ってきた。絶対値だけだと 140 文字は許容範囲に見えてしまう。
- **絶対値**（`DOCUMENT_EXCERPT_MAX_CHARS = 200`）… 長い文書向け。5,000 文字の LP に
  対する 1,000 文字の excerpt は割合 0.2 だが、直す場所としては役に立たない。

ここで固定すること:
  1. 実測の再現: 文書の大半を占める excerpt を採用しないこと
  2. 具体的な箇所を指せているときは従来どおり採用すること（#85 の挙動を壊さない）
  3. 割合・絶対値の両方が効くこと
  4. セグメントスコープには影響しないこと（キーワード型の該当箇所は残す）
  5. 採用しないときは空スパン（ハイライトしない）になること

⚠️ LLM にも Qdrant にも接続しない。
"""
from __future__ import annotations

import pytest

from backend.app.core.review_agent import (
    DOCUMENT_EXCERPT_MAX_CHARS,
    DOCUMENT_EXCERPT_MAX_RATIO,
    DOCUMENT_SEGMENT_ID,
    _is_too_broad,
    run_review_agent_core,
)
from backend.app.core.review_gates import DetectVerdict
from backend.app.core.rulesets import get_ruleset

GOOD_LP = """当社の美容液は、うるおいを与えて肌をなめらかに整えます。
■ 特定商取引法に基づく表記
販売業者: 株式会社サンプル
運営責任者: 山田太郎
所在地: 東京都千代田区1-1-1
電話番号: 03-0000-0000
販売価格: 4,980円（税込）
返品: 商品到着後8日以内、未開封に限り返品可能（送料はお客様負担）"""

# 実測で「該当箇所」として表示されていた文字列（2 行目以降＝ 7 行ぶん）。
MEASURED_EXCERPT = GOOD_LP.split("\n", 1)[1]


def _always_check_ids():
    return {r.rule_id for r in get_ruleset("ec_ad").always_check_rules}


def _detect_returning(excerpt):
    """表記漏れルールだけが `excerpt` を返す検出器。"""
    def _detect(_text, rule, _evidence):
        if rule.rule_id not in _always_check_ids():
            return DetectVerdict(violates=False)
        return DetectVerdict(
            violates=True, message=f"{rule.title}の記載がありません",
            suggestion="追記してください", excerpt=excerpt,
        )
    return _detect


def _doc_findings(result):
    return [f for f in result.findings if f.segment_id == DOCUMENT_SEGMENT_ID]


# =============================================================================
# ① 実測の再現
# =============================================================================

class TestMeasuredCase:

    def test_the_measured_excerpt_is_not_used(self, review_stub):
        """7 行ぶんの excerpt を該当箇所にしない。"""
        review_stub.detect = _detect_returning(MEASURED_EXCERPT)

        result = run_review_agent_core(GOOD_LP)

        findings = _doc_findings(result)
        assert findings, "表記漏れの指摘が出ていない"
        for finding in findings:
            assert finding.excerpt == "", (
                f"文書の大半（{len(finding.excerpt)}/{len(GOOD_LP)} 文字）を"
                "該当箇所にしている"
            )
            assert (finding.start, finding.end) == (0, 0)

    def test_the_measured_excerpt_is_judged_too_broad(self):
        """判定関数の単体確認（実測値そのもの）。"""
        assert _is_too_broad(MEASURED_EXCERPT, GOOD_LP) is True
        # 参考: 実測は約 0.87
        assert len(MEASURED_EXCERPT) / len(GOOD_LP) > DOCUMENT_EXCERPT_MAX_RATIO


# =============================================================================
# ② 具体的な箇所は従来どおり採用する（#85 の挙動を壊さない）
# =============================================================================

class TestPreciseExcerptIsKept:

    def test_short_excerpt_resolves_to_offsets(self, review_stub):
        """規程不一致など、指し示せる箇所があるときは位置解決する。"""
        review_stub.detect = _detect_returning("商品到着後8日以内")

        result = run_review_agent_core(GOOD_LP)

        findings = _doc_findings(result)
        assert findings
        for finding in findings:
            assert finding.excerpt == "商品到着後8日以内"
            assert GOOD_LP[finding.start:finding.end] == finding.excerpt

    def test_excerpt_not_in_the_document_is_still_empty(self, review_stub):
        """文書内に見つからない excerpt は従来どおり空スパン（#85）。"""
        review_stub.detect = _detect_returning("原文には存在しない文字列")

        result = run_review_agent_core(GOOD_LP)

        for finding in _doc_findings(result):
            assert (finding.excerpt, finding.start, finding.end) == ("", 0, 0)


# =============================================================================
# ③ 2 つの上限がそれぞれ効く
# =============================================================================

class TestBothLimitsApply:

    def test_ratio_limit_catches_short_documents(self):
        """短い文書: 文字数は少なくても割合が大きければ広すぎる。"""
        document = "あ" * 100
        excerpt = "あ" * 50      # 50 文字 < 200 だが 0.5 > 0.4

        assert len(excerpt) < DOCUMENT_EXCERPT_MAX_CHARS
        assert _is_too_broad(excerpt, document) is True

    def test_absolute_limit_catches_long_documents(self):
        """長い文書: 割合は小さくても長すぎればポインタにならない。"""
        document = "あ" * 5000
        excerpt = "あ" * 1000    # 0.2 < 0.4 だが 1000 > 200

        assert len(excerpt) / len(document) < DOCUMENT_EXCERPT_MAX_RATIO
        assert _is_too_broad(excerpt, document) is True

    def test_a_precise_pointer_in_a_long_document_is_kept(self):
        document = "あ" * 5000
        excerpt = "あ" * 80

        assert _is_too_broad(excerpt, document) is False

    @pytest.mark.parametrize("excerpt,document,expected", [
        ("", "本文", False),          # excerpt なしは対象外
        ("本文", "", False),          # 文書が空なら判定しない（ゼロ除算回避）
    ])
    def test_edge_cases(self, excerpt, document, expected):
        assert _is_too_broad(excerpt, document) is expected


# =============================================================================
# ④ セグメントスコープには影響しない
# =============================================================================

class TestSegmentScopeIsUnaffected:
    """⚠️ キーワード型ルールの該当箇所は**残す**。

    「業界No.1」のような該当箇所は指摘そのものの中身であり、消すと
    どこが問題なのか分からなくなる。セグメントは `MAX_SEGMENT_CHARS`（400）で
    上限が付いているので、そもそも文書全体を塗ることはない。
    """

    def test_keyword_finding_keeps_its_excerpt(self, review_stub):
        document = "業界No.1の品質です。"

        result = run_review_agent_core(document)

        segment_findings = [
            f for f in result.findings if f.segment_id != DOCUMENT_SEGMENT_ID
        ]
        assert segment_findings, "キーワード型の指摘が出ていない"
        for finding in segment_findings:
            assert finding.excerpt, "セグメントスコープの該当箇所まで消している"
            assert document[finding.start:finding.end] == finding.excerpt

    def test_paraphrased_segment_excerpt_still_falls_back_to_the_segment(
        self, review_stub,
    ):
        """LLM が言い換えたときのセグメント全体フォールバックは維持（#85）。"""
        review_stub.detect = lambda _t, _r, _e: DetectVerdict(
            violates=True, message="言い換えた指摘", suggestion="修正",
            excerpt="原文には存在しない文字列",
        )
        document = "業界No.1の品質です。"

        result = run_review_agent_core(document)

        segment_findings = [
            f for f in result.findings if f.segment_id != DOCUMENT_SEGMENT_ID
        ]
        assert segment_findings
        assert segment_findings[0].excerpt == document

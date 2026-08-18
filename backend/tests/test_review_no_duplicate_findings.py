# backend/tests/test_review_no_duplicate_findings.py
"""**同一ルール × 同一箇所の指摘が重複しない**ことを固定するテスト。

## 背景（実測 2026-08-17 20:07 / GRACE-Review）

「適正LP案」への 11 件の指摘のうち、**6 件は同一ルールの重複**だった。

    tokusho-05（事業者名・住所・連絡先）: 3 件
    tokusho-02（代金の支払時期・方法）  : 2 件
    tokusho-03（商品の引渡時期）        : 2 件
    tokusho-04（返品特約の表示）        : 2 件

## ⚠️ これは #85（表記漏れを文書全体で判定）で**解消済み**である

原因は dedup の欠落ではなく、`always_check` のルールが毎セグメントで判定されて
いたことだった。文書全体で 1 回だけ判定するようにした結果、重複は構造的に
発生しなくなった。同じ内容を 2 回並べた文書（16 セグメント）で実測:

    findings=8  segments=16
    rule_id ごとの件数: {tokusho-01..06: 各 1, keihyo-08: 2}
    完全重複 (rule_id, start, end): {}

**したがって本 PR は実装を変更していない。** 不変条件をテストで固定するだけである。

## なぜ `(rule_id, excerpt)` で dedup しなかったのか

`keihyo-08` が 2 件出ているのは、**同じ表現が文書の別の位置に 2 回ある**ためで、
これは正当な 2 件である。`(rule_id, excerpt)` を鍵に dedup すると 2 件目が消え、
ユーザーは直すべき箇所を 1 つ見落とす。

本リポジトリの方針は「**Review では指摘を消す方向のミスが最も痛い**」
（`review_gates.py` の docstring）。したがって重複判定の鍵に**位置を含める**
（`rule_id, start, end`）。位置が同じなら本当に同じ指摘であり、位置が違うなら
別の指摘である。

## ここで固定すること

  1. 同一ルール × 同一スパンの指摘が 2 件出ないこと
  2. **同じ表現が別の位置にあれば両方指摘されること**（1 と対で意味を持つ）
  3. 表記漏れルールは 1 文書あたり最大 1 件であること
  4. `finding_id` が一意であること

⚠️ LLM にも Qdrant にも接続しない。
"""
from __future__ import annotations

from collections import Counter

from backend.app.core.review_agent import (
    DOCUMENT_SEGMENT_ID,
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

# 同じ内容を 2 回並べる。以前の実装では表記漏れの判定回数が倍になっていた。
DOUBLED_LP = GOOD_LP + "\n\n" + GOOD_LP


def _always_violates(_text, rule, _evidence):
    """全ルールが必ず違反と答える最悪ケース（重複が出るなら必ず出る条件）。"""
    return DetectVerdict(
        violates=True, message=f"{rule.title}違反", suggestion="修正", excerpt="",
    )


def _always_check_ids():
    return {r.rule_id for r in get_ruleset("ec_ad").always_check_rules}


def _spans(result):
    return [(f.rule_id, f.start, f.end) for f in result.findings]


# =============================================================================
# ① 完全重複を出さない
# =============================================================================

class TestNoExactDuplicates:

    def test_no_duplicate_rule_and_span(self, review_stub):
        """同一ルール × 同一スパンが 2 件出ない。"""
        review_stub.detect = _always_violates

        result = run_review_agent_core(DOUBLED_LP)

        duplicates = {k: c for k, c in Counter(_spans(result)).items() if c > 1}
        assert duplicates == {}, f"同じ指摘が重複している: {duplicates}"

    def test_always_check_rules_appear_at_most_once(self, review_stub):
        """表記漏れルールは 1 文書あたり最大 1 件。

        実測では tokusho-05 が 3 件、tokusho-02/03/04 が各 2 件出ていた。
        """
        review_stub.detect = _always_violates

        result = run_review_agent_core(DOUBLED_LP)

        counts = Counter(f.rule_id for f in result.findings)
        for rule_id in _always_check_ids():
            assert counts[rule_id] <= 1, (
                f"{rule_id} が {counts[rule_id]} 件出ている"
                "（表記漏れは文書全体で 1 回判定するはず）"
            )

    def test_finding_ids_are_unique(self, review_stub):
        review_stub.detect = _always_violates

        result = run_review_agent_core(DOUBLED_LP)

        ids = [f.finding_id for f in result.findings]
        assert len(ids) == len(set(ids)), f"finding_id が重複している: {ids}"


# =============================================================================
# ② 消す方向のミスを作らない（① と対で意味を持つ）
# =============================================================================

class TestDistinctLocationsAreBothKept:
    """⚠️ **この検証が無いと ① は「重複を消す」だけの片側の主張になる。**

    `(rule_id, excerpt)` を鍵に dedup すれば ① は通るが、同じ表現が別の位置に
    2 回ある文書で 2 件目が消え、直すべき箇所を見落とす。
    """

    def test_same_phrase_at_two_places_yields_two_findings(self, review_stub):
        """同じ表現が 2 箇所にあれば 2 件とも指摘する。"""
        document = "業界No.1の品質です。\n\n別の段落です。\n\n業界No.1の実績です。"

        result = run_review_agent_core(document)

        keihyo = [f for f in result.findings if f.rule_id.startswith("keihyo-")]
        assert len(keihyo) >= 2, (
            f"同じ表現の 2 箇所目が消えている（{len(keihyo)} 件）。"
            "重複判定の鍵に位置を含めていない可能性がある"
        )
        starts = {f.start for f in keihyo}
        assert len(starts) == len(keihyo), "同じ位置を重複して指摘している"

    def test_offsets_still_point_into_the_original(self, review_stub):
        """2 箇所目のオフセットも原文を指す（先頭の位置を使い回していない）。"""
        document = "業界No.1の品質です。\n\n別の段落です。\n\n業界No.1の実績です。"

        result = run_review_agent_core(document)

        for finding in result.findings:
            if finding.segment_id == DOCUMENT_SEGMENT_ID:
                continue    # 文書スコープは空スパン（ハイライトしない）
            assert document[finding.start:finding.end] == finding.excerpt

# tests/test_review_facts.py
"""文字列だけで決まる事実（`review_facts`）で LLM の判定を補うこと。

## 回帰（実測 2026-10-03・grace_v2_local・ローカル LLM・各 2 回再現）

1. 表記漏れLP案: 「販売価格: 4,980円（税込）」だけで購入時の送料が無いのに、
   tokusho-01（販売価格・送料の明示）を 2 回とも「違反なし」とした。
2. OK 例: 広告「未開封に限り」・規程「未使用・未開封」を、policy-01 が 2 回とも
   「広告が規程より顧客に不利」と判定した（実際は広告のほうが条件が少なく有利）。

どちらも判定基準に例として書いてあるのに読み落とした。文字列で決まる部分は
LLM に頼らず決める。

⚠️ LLM にも Qdrant にも接続しない。
"""
from __future__ import annotations

from backend.app.core.review_agent import run_review_agent_core
from backend.app.core.review_facts import (
    parse_ad_return_terms,
    parse_policy_return_terms,
    purchase_shipping_shown,
    return_terms_not_worse,
)
from backend.app.core.review_gates import DetectVerdict

# frontend/src/components/ReviewForm.tsx のサンプルと同じ本文
MISSING_LP = (
    "当社の美容液は、うるおいを与えて肌をなめらかに整えます。\n\n"
    "■ 特定商取引法に基づく表記\n"
    "販売業者: 株式会社サンプル\n"
    "運営責任者: 山田太郎\n"
    "所在地: 東京都千代田区1-1-1\n"
    "電話番号: 03-0000-0000\n"
    "販売価格: 4,980円（税込）\n"
    "返品: 商品到着後8日以内、未開封に限り返品可能（送料はお客様負担）"
)
OK_LP = (
    "当社の美容液は、うるおいを与えて肌をなめらかに整えます。\n\n"
    "■ 特定商取引法に基づく表記\n"
    "販売業者: 株式会社サンプル\n"
    "運営責任者: 山田太郎\n"
    "所在地: 東京都千代田区1-1-1\n"
    "電話番号: 03-0000-0000\n"
    "販売価格: 4,980円（税込）\n"
    "送料: 全国一律600円（税込）\n"
    "お支払い方法: クレジットカード（前払い）、銀行振込（前払い）\n"
    "発送時期: ご注文確認後3営業日以内に発送\n"
    "返品: 商品到着後14日以内、未開封に限り返品可能（送料はお客様負担）"
)
COSMETIC_LP = (
    "当社の美容液は業界No.1の実力です。\n\n"
    "・今だけ期間限定 通常価格 12,000円 → 4,980円\n"
    "・送料無料"
)
# ec_policy_anthropic の行（実測 2026-10-03 の ② Retrieve が返した 5 件）
POLICY_ROWS = [
    "当ストアでは商品到着後14日以内かつ未使用・未開封の商品に限り返品を承ります。"
    "お客様都合の返品は返送料をお客様にご負担いただきます。"
    "不良品・誤配送の場合は当ストア負担で返品・交換いたします。",
    "サイズ違い・色違いの交換は商品到着後14日以内、未使用品に限り1回まで承ります。"
    "在庫がない場合は返品・返金対応となります。",
    "返品商品の到着確認後、5営業日以内にご購入時のお支払い方法へ返金いたします。",
    "発送前のご注文は注文履歴からキャンセルできます。",
    "商品到着後7日以内にお問い合わせフォームから不良箇所の写真を添えてご連絡ください。",
]
OK_MESSAGE = ("広告の表示では返品条件が「未開封」に限定されていますが、規程では"
              "「未使用・未開封」となっており、広告の表示が規程より顧客に不利になるおそれがあります。")


class TestPurchaseShipping:

    def test_return_shipping_is_not_purchase_shipping(self):
        assert purchase_shipping_shown(MISSING_LP) is False

    def test_shown(self):
        assert purchase_shipping_shown(OK_LP) is True
        assert purchase_shipping_shown(COSMETIC_LP) is True   # 「送料無料」


class TestReturnTerms:

    def test_parse_ad_and_policy(self):
        ad = parse_ad_return_terms(OK_LP)
        assert (ad.days, ad.conditions, ad.customer_pays_shipping) == (
            14, frozenset({"未開封"}), True)
        policy = parse_policy_return_terms(POLICY_ROWS)
        assert (policy.days, policy.conditions, policy.customer_pays_shipping) == (
            14, frozenset({"未使用", "未開封"}), True)

    def test_ok_example_is_not_worse(self):
        reason = return_terms_not_worse(OK_MESSAGE, OK_LP, POLICY_ROWS)
        assert reason and "14 日 ≥ 規程 14 日" in reason

    def test_shorter_period_is_worse(self):
        msg = "広告では返品期限が8日以内、規程では14日以内で、顧客に不利です。"
        assert return_terms_not_worse(msg, MISSING_LP, POLICY_ROWS) is None

    def test_extra_condition_is_worse(self):
        ad = "返品: 商品到着後14日以内、未使用・未開封・タグ付きに限り返品可能"
        assert return_terms_not_worse("返品条件が厳しい", ad, POLICY_ROWS) is None

    def test_customer_pays_when_policy_does_not(self):
        policy = ["商品到着後14日以内かつ未開封の商品に限り返品を承ります。返送料は当店負担です。"]
        ad = "返品: 商品到着後14日以内、未開封に限り返品可能（送料はお客様負担）"
        assert return_terms_not_worse("返品の送料負担が重い", ad, policy) is None

    def test_other_topics_are_not_judged(self):
        msg = "返品の期限は同じだが、返金の時期が規程より遅い。"
        assert return_terms_not_worse(msg, OK_LP, POLICY_ROWS) is None

    def test_ambiguous_policy_is_not_judged(self):
        policy = ["商品到着後14日以内に返品を承ります。", "セール品は商品到着後7日以内に返品を承ります。"]
        assert return_terms_not_worse("返品条件が不利", OK_LP, policy) is None

    def test_unreadable_is_not_judged(self):
        assert return_terms_not_worse("返品条件が不利", "返品はできます", POLICY_ROWS) is None


def _only(rule_id, message="指摘"):
    def detect(_text, rule, _evidence):
        if rule.rule_id == rule_id:
            return DetectVerdict(violates=True, message=message, suggestion="修正")
        return DetectVerdict(violates=False)
    return detect


class TestPipeline:

    def test_missing_shipping_is_flagged_even_if_the_llm_says_no(self, review_stub):
        review_stub.purchase_shipping_shown = None       # 実物の判定を使う
        review_stub.groundedness.support_rate = 0.95
        review_stub.detect = lambda *_a: DetectVerdict(violates=False)

        result = run_review_agent_core(MISSING_LP)

        tokusho01 = [f for f in result.findings if f.rule_id == "tokusho-01"]
        assert tokusho01 and "購入時の送料" in tokusho01[0].message

    def test_shipping_present_is_left_to_the_llm(self, review_stub):
        review_stub.purchase_shipping_shown = None
        review_stub.detect = lambda *_a: DetectVerdict(violates=False)
        result = run_review_agent_core(OK_LP)
        assert not [f for f in result.findings if f.rule_id == "tokusho-01"]

    def test_detector_failure_is_not_replaced(self, review_stub):
        """判定に失敗したときは補わない（判定失敗を確定にしない方針を崩さない）。"""
        review_stub.purchase_shipping_shown = None
        review_stub.detect = lambda *_a: None
        result = run_review_agent_core(MISSING_LP)
        for finding in result.findings:
            assert "購入時の送料（送料の額" not in finding.message

    def test_ok_example_policy01_is_suppressed(self, review_stub):
        review_stub.groundedness.support_rate = 0.95
        review_stub.rag_output = [{"payload": {"question": "返品規定", "answer": POLICY_ROWS[0]}}]
        review_stub.detect = _only("policy-01", OK_MESSAGE)

        result = run_review_agent_core(OK_LP)

        assert not [f for f in result.findings if f.rule_id == "policy-01"]
        assert result.summary.suppressed == 1

    def test_shorter_period_is_still_flagged(self, review_stub):
        review_stub.groundedness.support_rate = 0.95
        review_stub.rag_output = [{"payload": {"question": "返品規定", "answer": POLICY_ROWS[0]}}]
        review_stub.detect = _only(
            "policy-01", "広告では返品期限が8日以内、規程では14日以内で、顧客に不利です。")

        result = run_review_agent_core(MISSING_LP)

        assert [f for f in result.findings if f.rule_id == "policy-01"]

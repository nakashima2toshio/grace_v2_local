# backend/tests/test_review_policy_evidence.py
"""**policy-01 が自社の規程を引く**ことを固定するテスト。

## 背景（実測 2026-08-19 06:11）

検査対象の広告は「返品: 商品到着後**8日**以内」と表示している。一方、自社規程
（`ec_policy_anthropic`）は「商品到着後**14日**以内」。規程より顧客に不利な条件を
広告に出しているので、`policy-01`（表示内容と社内規程の不一致）が指摘すべき事案
である。**それが 1 度も発火していない。**

原因は 2 つあり、どちらも単独で発火を止める。

### 原因 1: 検索が自分自身を引き当てていた（自己一致）

② の既定クエリは `f"{rule.title} {rule.description}"`。ところが
`ec_ad_rules_anthropic` には**ルール自身が 1 行として入っている**ため、
このクエリは自分を引く。

    query   = '表示内容と社内規程の不一致 広告に表示した取引条件（返品期限…'
    top hit = 社内規程 —（表示内容と社内規程の不一致）      0.9380  ← 同一テキスト

本命の「返品規定を教えてください（14日）」は別コレクションで **0.6647**。絶対閾値
0.70 にも届かず、仮に届いても #93 の `evidence_top_ratio`（0.9380 × 0.92 = 0.863）に
阻まれる。**構造的に採用されない。**

条文ルール（tokusho-*）は「引きたいのが条文そのもの」なので自己一致で問題ない。
policy-01 だけが「引きたいのは条文ではなく自社の規程」という別種のルールである。

### 原因 2: description が 8 日を免責していた

旧 description は「法令が定める既定値どおりの表示（例: 返品期限 8 日）は、それ自体は
適法である」で終わっており、**8 日という表示そのものを免責する**読み方ができた。

規程不一致は「適法かどうか」ではなく「自社の規程と食い違うか」の指摘なので、
適法であっても食い違えば指摘する、と書き替える。

## ここで固定すること

  1. policy-01 が**取引条件の語**で検索すること（ルール本文ではなく）
  2. 検索範囲が**自社規程のコレクション**に絞られること
  3. 条文ルール（tokusho-* 等）は従来どおりであること（巻き添えにしない）
  4. description が「適法でも食い違えば指摘する」と述べていること
  5. description が『広告の表示』と『規程の条件』の併記を求めていること

⚠️ LLM にも Qdrant にも接続しない。**スコアそのものは検証できない**
（Embedding が要る）。ここで固定するのは「何を、どこから引くか」だけ。
"""
from __future__ import annotations

from backend.app.core.review_agent import run_review_agent_core
from backend.app.core.rulesets import EC_AD, RuleItem

# 実測に合わせた検査対象（規程は 14 日、広告は 8 日）。
LP_WITH_MISMATCH = (
    "当社の美容液は、うるおいを与えて肌をなめらかに整えます。"
    "■ 特定商取引法に基づく表記 販売業者: 株式会社サンプル "
    "販売価格: 4,980円（税込） "
    "返品: 商品到着後8日以内、未開封に限り返品可能（送料はお客様負担）"
)


def _policy_rule() -> RuleItem:
    rule = EC_AD.rule_by_id("policy-01")
    assert rule is not None
    return rule


def _rag_calls(stub):
    return [kwargs for name, kwargs in stub.tool_calls if name == "rag_search"]


# =============================================================================
# ① 何を引くか
# =============================================================================

class TestWhatPolicyRuleRetrieves:

    def test_query_is_about_transaction_terms_not_the_rule_text(self):
        """クエリは取引条件の語であること（ルール本文だと自己一致する）。"""
        rule = _policy_rule()

        assert rule.evidence_query, "検索クエリの上書きが無い"
        assert rule.retrieval_query() == rule.evidence_query
        for term in ("返品", "解約", "送料", "期限"):
            assert term in rule.evidence_query, f"取引条件の語 '{term}' が無い"

    def test_query_does_not_contain_the_rule_title(self):
        """⚠️ タイトルを含めると `ec_ad_rules_anthropic` の自分の行に当たる。"""
        rule = _policy_rule()

        assert rule.title not in rule.evidence_query

    def test_scope_is_the_company_policy_collection(self):
        """検索範囲は自社規程のコレクションだけ（条文コレクションを外す）。"""
        rule = _policy_rule()

        assert rule.evidence_collections == ["ec_policy_anthropic"]
        assert "ec_ad_rules_anthropic" not in rule.evidence_collections


# =============================================================================
# ② パイプラインで実際にそう呼ばれる
# =============================================================================

class TestThePipelineHonorsTheOverride:

    def test_search_is_issued_with_the_override(self, review_stub):
        rule = _policy_rule()

        run_review_agent_core(LP_WITH_MISMATCH)

        calls = _rag_calls(review_stub)
        matched = [c for c in calls if c.get("query") == rule.evidence_query]
        assert matched, f"上書きクエリで検索していない: {[c.get('query') for c in calls]}"
        for call in matched:
            assert call.get("allowed_collections") == ["ec_policy_anthropic"], (
                f"検索範囲が絞られていない: {call.get('allowed_collections')}"
            )

    def test_statute_rules_are_not_affected(self, review_stub):
        """条文ルールは従来どおり（ルール本文 + RuleSet 既定のスコープ）。

        ⚠️ policy-01 の逃げ道が条文ルールを巻き添えにしていないことの確認。
        """
        run_review_agent_core(LP_WITH_MISMATCH)

        calls = _rag_calls(review_stub)
        tokusho_01 = EC_AD.rule_by_id("tokusho-01")
        matched = [c for c in calls if tokusho_01.title in (c.get("query") or "")]
        assert matched, "条文ルールがルール本文で検索されていない"
        for call in matched:
            assert call.get("allowed_collections") == EC_AD.collections


# =============================================================================
# ③ description が指摘を殺していない
# =============================================================================

class TestDescriptionDoesNotExcuseTheMismatch:
    """`description` は ③ Detect のプロンプトへそのまま入る。

    ⚠️ **#98 まではそうなっていなかった。** 実装が `description` を渡しておらず、
    ここで文言をいくら固定しても LLM の挙動には届いていなかった（経緯は
    `test_review_detect_criteria_in_prompt.py` 冒頭）。プロンプトへ届くことは
    そちらで固定してある。このクラスは文言の中身だけを見る。
    """

    def test_it_says_lawfulness_is_not_the_criterion(self):
        """適法かどうかで判定しない（規程より不利かどうかで判定する）。"""
        description = _policy_rule().description

        assert "適法かどうかは判定材料にしない" in description
        assert "適法でも規程より不利なら" in description

    def test_it_gives_the_measured_example(self):
        """14 日 vs 8 日 の具体例を残す（抽象的な指示だけだと判断が揺れる）。"""
        description = _policy_rule().description

        assert "14 日" in description
        assert "8 日" in description

    def test_it_asks_for_both_sides_in_the_message(self):
        """指摘文に『広告の表示』と『規程の条件』を両方書かせる。

        片方だけだと「返品期限が不適切です」のような、何と食い違うのか
        分からない指摘になる。
        """
        description = _policy_rule().description

        assert "『広告の表示』と『規程の条件』を両方書き" in description

    def test_it_stays_out_of_legal_violation_territory(self):
        """法令違反として扱わない（#88 で確立した帰属は維持する）。"""
        rule = _policy_rule()

        assert "法令違反の指摘ではなく、社内整合性の指摘" in rule.description
        assert rule.law == "社内規程"
        assert rule.severity_default == "medium"

    def test_it_does_not_report_terms_absent_from_the_policy(self):
        """規程に無い項目は指摘しない（規程不一致は照合できる項目だけ）。"""
        assert "【規程】に対応する条件が書かれていない項目" in _policy_rule().description


# =============================================================================
# ④ 食い違いの「方向」を見る（#96）
# =============================================================================

class TestDirectionOfTheMismatch:
    """**語句が違うことではなく、顧客の受けられる扱いが狭まるかで判定する。**

    ## 背景（実測 2026-08-19 23:53）

    #95 で 8 日 vs 14 日 の検出は動くようになった（返品規定のスコアが
    0.6647 → 0.7543 へ上がり閾値 0.70 を超えた）。ところが広告を規程どおりの
    14 日へ直しても、policy-01 が別の理由で発火し続けた。

        指摘: 対象テキストの返品条件は「未開封に限り」としているが、社内規程では
              「未使用・未開封」の両方を条件としており、「未使用」の要件が欠落している。

    **未開封の商品は必然的に未使用**なので、顧客が返品できる範囲は 1 ミリも
    狭まっていない。にもかかわらず「規程不一致」として confirmed で出ていた。

    原因は #95 で書いた description にある。

        食い違いがあれば、たとえ広告側の表示が適法であっても指摘する

    これは「**あらゆる**食い違いを指摘せよ」という指示なので、LLM は文字面の差を
    素直に拾う。広告文は規程の要約なのだから、語句が一致しないのは当たり前で、
    このままではどんな広告も規程不一致になる。

    判定軸を「規程と語句が一致するか」から「**その広告を見た顧客が受けられる
    扱いが、規程より狭まっているか**」へ戻す。
    """

    def test_the_criterion_is_disadvantage_not_difference(self):
        """判定軸が「顧客に不利か」であること（「違うか」ではない）。"""
        description = _policy_rule().description

        assert "顧客に不利" in description
        assert "顧客が受けられる扱いが、規程より狭まっているか" in description

    def test_wording_mismatch_alone_is_forbidden(self):
        """⚠️ **語句の不一致だけを理由に指摘してはならない**と明示すること。"""
        description = _policy_rule().description

        assert "語句が一致しないことを理由に指摘してはならない" in description

    def test_the_measured_false_positive_is_named(self):
        """実測の誤検知（未開封 vs 未使用・未開封）を例として書いておく。

        抽象的な指示だけでは判断が揺れる。#90 で「複数事項ルール」を直したときと
        同じで、**実際に外した事例をそのまま例に入れる**のが効く。
        """
        description = _policy_rule().description

        assert "未使用・未開封" in description
        assert "未開封の商品は" in description
        assert "指摘しない" in description

    def test_more_favourable_terms_are_not_reported(self):
        """広告のほうが顧客に有利／同等なら指摘しない。"""
        description = _policy_rule().description

        assert "広告のほうが顧客に有利、または同等" in description

    def test_stricter_terms_are_still_reported(self):
        """狭める方向の食い違いは従来どおり指摘する（8 日 vs 14 日）。

        ⚠️ 誤検知を抑えるあまり、本来の検出まで殺していないことの確認。
        """
        description = _policy_rule().description

        assert "広告のほうが期限が短い／負担が重い／条件が厳しい" in description
        assert "受けられる" in description and "はずの期間を狭めている" in description

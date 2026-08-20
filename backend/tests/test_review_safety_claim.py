# backend/tests/test_review_safety_claim.py
"""**安全性の保証表現**（yakki-04）が拾われることを固定する。

## 背景（実測 2026-08-20 20:18 / 21:01 / `NG 例（優良誤認・薬機法）`）

    当社の美容液は業界No.1の実力です。
    使い続ければシミが治ると評判で、副作用がないので誰でも安心してお使いいただけます。
                                    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**3 回の実行すべてで、この一文が一度も指摘されなかった。**

「副作用がない」は安全性を保証・断定する表現であり、医薬品等適正広告基準では
認められない典型例である。にもかかわらず指摘が出なかったのは、**実装の不具合では
なくルールセットの守備範囲の不足**だった。既存 22 件のキーワードを調べると

    yakki-01 治る/治療/改善/予防/効く/病気/症状/免疫力
    yakki-02 シワが消える/若返る/美白/…/治る/治療/改善/効く/症状/シミ/…
    yakki-03 血行促進/筋肉増強/痩身/医療用/治療器/コリをほぐす
    keihyo-* 最高/最強/世界初/No.1/通常価格/今だけ/無料/…

「副作用」「安全」「安心」を見るルールがどこにも無い。第1段を通過しないので
第2段の LLM に一度も提示されない（#99 の yakki-02 と同じ構造の穴）。

## 追加したルール

  yakki-04 安全性の保証表現（医薬品医療機器等法 第66条 / high / キーワード型）

⚠️ **「安心」「安全」という語が入っているだけで指摘してはならない。**
そのままだと注意喚起（「お肌に合わない場合はご使用をおやめください」）や
取引上の安心（「安心してお買い物いただけます」）まで拾ってしまう。
第1段は広く拾い、**第2段が【判定基準】で切り分ける**（#99 で確立した建て付け）。

## ここで固定すること

  1. 「副作用がない」で yakki-04 が候補に上がること（第1段）
  2. 指摘する／しないの切り分けが**プロンプトへ届く**こと（第2段）
  3. ルールの帰属（条文・重大度・判定単位）
  4. ルール総数の変化（22 → 23）と、always_check が増えていないこと

⚠️ LLM には接続しない（第1段は純関数、第2段は組み立てたプロンプト文字列を検査する）。
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from backend.app.core.review_gates import (
    create_violation_detector,
    select_candidate_rules,
)
from backend.app.core.rulesets import EC_AD

# 実測 3 回とも指摘されなかったセグメント（化粧品LP案の 2 文目）。
SAFETY_CLAIM = (
    "使い続ければシミが治ると評判で、副作用がないので誰でも安心してお使いいただけます。"
)


def _candidate_ids(segment: str) -> set[str]:
    return {c.rule_id for c in select_candidate_rules(segment, EC_AD)}


# =============================================================================
# ① 第1段: 候補に上がる
# =============================================================================

class TestTheSafetyRuleBecomesACandidate:

    def test_the_measured_sentence_reaches_the_rule(self):
        """⚠️ これが本体。実測 3 回とも候補に上がらなかった一文。"""
        assert "yakki-04" in _candidate_ids(SAFETY_CLAIM)

    @pytest.mark.parametrize("phrase", [
        "副作用がない", "副作用はありません", "副作用なし",
        "無添加だから安心", "誰でも安心してお使いいただけます",
    ])
    def test_the_wordings_of_a_safety_guarantee(self, phrase):
        """安全性保証の言い回しが一通り届くこと。"""
        assert "yakki-04" in _candidate_ids(f"この美容液は{phrase}。")

    def test_the_other_yakki_rules_still_fire_on_the_same_sentence(self):
        """既存ルールを押しのけていないこと。

        同じ一文には「シミが治る」も含まれるので yakki-01 / yakki-02 も
        候補のまま。絞り込みは第2段が行う。
        """
        ids = _candidate_ids(SAFETY_CLAIM)

        assert {"yakki-01", "yakki-02", "yakki-04"} <= ids


# =============================================================================
# ② 第2段: 指摘する／しないの切り分けがプロンプトへ届く
# =============================================================================

@pytest.fixture
def prompt(monkeypatch):
    """`detect()` が実際に LLM へ渡したプロンプト文字列を返すファクトリ。"""
    captured: list[str] = []

    def _generate(**kwargs):
        captured.append(kwargs["contents"])
        return SimpleNamespace(text='{"violates": false}')

    client = SimpleNamespace(models=SimpleNamespace(generate_content=_generate))
    module = SimpleNamespace(create_chat_client=lambda _c: client)
    monkeypatch.setitem(sys.modules, "grace", SimpleNamespace(llm_compat=module))
    monkeypatch.setitem(sys.modules, "grace.llm_compat", module)

    def _build(rule_id="yakki-04", text=SAFETY_CLAIM):
        detect = create_violation_detector(
            SimpleNamespace(llm=SimpleNamespace(prompt_addendum=""))
        )
        detect(text, EC_AD.rule_by_id(rule_id), "規程本文")
        return captured[-1]

    return _build


class TestTheCriteriaReachTheModel:

    def test_what_to_report(self, prompt):
        text = prompt()

        assert "安全性を**保証・断定**する表現" in text
        assert "副作用がない" in text

    def test_a_caution_notice_is_not_a_violation(self, prompt):
        """注意喚起はむしろ適切な表示なので指摘しない。"""
        text = prompt()

        assert "お肌に合わない場合はご使用をおやめください" in text
        assert "注意喚起" in text

    def test_reassurance_about_the_transaction_is_not_a_violation(self, prompt):
        """配送・サポートについての「安心」は商品の安全性ではない。"""
        text = prompt()

        assert "商品の安全性ではなく" in text
        assert "取引・配送・サポートについて" in text

    def test_the_word_alone_is_not_enough(self, prompt):
        """「安心」「安全」の語だけで指摘してはならない、と明示すること。

        これが無いと第1段の広いキーワードがそのまま誤検出になる。
        """
        text = prompt()

        assert "個人差を無視して" in text
        assert "という語が入っているだけで指摘してはならない" in text


# =============================================================================
# ③ 帰属と判定単位
# =============================================================================

class TestTheRuleIsAttributedCorrectly:

    def _rule(self):
        rule = EC_AD.rule_by_id("yakki-04")
        assert rule is not None, "yakki-04 が定義されていない"
        return rule

    def test_law_and_article(self):
        rule = self._rule()

        assert rule.law == "医薬品医療機器等法"
        assert rule.article == "第66条"
        assert rule.category == "効能効果"

    def test_severity_is_high(self):
        """安全性の誤認は健康被害に直結しうるので high。"""
        assert self._rule().severity_default == "high"

    def test_it_is_a_keyword_rule(self):
        """書かれている表現を見るルールなので always_check ではない。

        表記漏れ（何かが『無い』ことの検出）とは判定単位が違う（#85）。
        """
        assert self._rule().always_check is False


# =============================================================================
# ④ ルールセット全体への影響
# =============================================================================

class TestTheRulesetGrewByExactlyOne:

    def test_the_total_is_23(self):
        assert len(EC_AD.rules) == 23

    def test_the_yakki_family_is_4(self):
        assert sum(1 for r in EC_AD.rules if r.law == "医薬品医療機器等法") == 4

    def test_always_check_is_unchanged(self):
        """yakki-04 はキーワード型なので常時チェックは 7 のまま。

        ここが増えると文書 1 通あたりの LLM 呼び出しが増える。
        """
        assert len(EC_AD.always_check_rules) == 7

    def test_the_laws_list_is_unchanged(self):
        """新しい法令を増やしたわけではない（既存の薬機法に 1 件足しただけ）。"""
        assert {r.law for r in EC_AD.rules} == {
            "景品表示法", "特定商取引法", "医薬品医療機器等法", "社内規程",
        }

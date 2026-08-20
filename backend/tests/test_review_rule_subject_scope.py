# backend/tests/test_review_rule_subject_scope.py
"""**ルールは自分の主題だけを判定する**ことを固定するテスト。

## 背景（実測 2026-08-17 20:07 / GRACE-Review）

### ① ルール ID と指摘内容が対応していなかった

    重大 / 定期購入の条件明示 / 特定商取引法 第12条の6 / 確定
    該当箇所: 商品到着後8日以内、未開封に限り返品可能
    指摘: 規程では返品受付期間を「14日以内」と定めているが、対象テキストでは
          「8日以内」と記載されており、規程と異なる条件が表示されています

対象の広告に**定期購入の記載は一切ない**。第12条の6 は継続契約の条件明示の規定で、
指摘内容（返品期間）とは無関係。しかも tokusho-04（返品特約の表示）が同じ
「8日 vs 14日」を既に指摘済みで、**同一事実の二重計上**になっていた。

### ② 社内規程との不一致を「法令違反・重大」に格上げしていた

    重大 / 返品特約の表示 / 特定商取引法 第11条 / 確定
    指摘: 規程では「14日以内」…対象テキストでは「8日以内」…

**8 日は法定の既定日数である。** tokusho-04 の `description` 自身がそう書いている。

    「返品特約の表示が無い場合、商品到着後8日間は送料購入者負担で返品が可能となる」

つまり「8日以内・未開封・送料お客様負担」の表示は**特商法第11条には適合している**。
問題は自社規程（14日）より短いという**社内整合性**であって法令違反ではない。
それを「重大 / 特商法第11条 / 確定」として出すのは**帰属の誤り**である。

## 原因は 1 つ

どちらも「ルールが自分の主題外を指摘している」ことに起因する。
tokusho-04 は**返品特約が表示されているか**を見るルールで、この文書は表示している。
「期間が社内規程と違う」はこのルールの主題ではない。

## 修正

1. `create_violation_detector` のプロンプトに主題スコープの制約を追加
2. 社内規程との不一致は `policy-01`（`law="社内規程"` / `category="規程不一致"` /
   `severity_default="medium"`）へ帰属させる — **指摘自体は失わない**

ここで固定すること:
  1. 主題外を指摘しない制約がプロンプトに入ること
  2. 非該当の取引形態（定期購入など）で violates=false にする制約が入ること
  3. 「〜の表示」ルールは記載の有無だけを見る制約が入ること
  4. 法定既定値どおりの表示を法令違反にしない制約が入ること
  5. `policy-01` が法令ルールと分かれていること（law / category / 重大度）
  6. 既存の判定原則を落としていないこと

⚠️ LLM には接続しない（プロンプトは実際に組み立てたものを検査する）。
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from backend.app.core.review_gates import create_violation_detector
from backend.app.core.rulesets import EC_AD


@pytest.fixture
def prompt(monkeypatch):
    """`detect()` が実際に LLM へ渡したプロンプトを返すファクトリ。"""
    captured: list[str] = []

    def _generate(**kwargs):
        captured.append(kwargs["contents"])
        return SimpleNamespace(text='{"violates": false}')

    client = SimpleNamespace(models=SimpleNamespace(generate_content=_generate))
    module = SimpleNamespace(create_chat_client=lambda _c: client)
    monkeypatch.setitem(sys.modules, "grace", SimpleNamespace(llm_compat=module))
    monkeypatch.setitem(sys.modules, "grace.llm_compat", module)

    def _build(rule_id="tokusho-04", text="返品: 商品到着後8日以内"):
        detect = create_violation_detector(
            SimpleNamespace(llm=SimpleNamespace(prompt_addendum=""))
        )
        detect(text, EC_AD.rule_by_id(rule_id), "規程本文")
        return captured[-1]

    return _build


# =============================================================================
# ① 主題スコープの制約
# =============================================================================

class TestSubjectScopeConstraint:

    def test_out_of_subject_findings_are_forbidden(self, prompt):
        """「別の問題があってもこのルールの主題でなければ violates=false」。"""
        text = prompt()

        assert "判定するのは【ルール】の主題だけ" in text
        assert "別のルールで判定される" in text

    def test_inapplicable_transaction_type_is_not_a_violation(self, prompt):
        """定期購入の記載が無いのに定期購入ルールで指摘するのを防ぐ。

        実測では tokusho-06（第12条の6）が返品期間の話を指摘していた。
        """
        text = prompt(rule_id="tokusho-06")

        assert "ルールが前提とする取引形態" in text
        assert "定期購入・継続課金の記載が一切ない" in text

    def test_disclosure_rules_only_check_presence(self, prompt):
        """「〜の表示」ルールは記載の有無だけを見る。"""
        text = prompt()

        assert "記載の有無だけを見る" in text
        assert "記載内容が【規程】の数値と" in text

    def test_statutory_default_is_not_a_violation(self, prompt):
        """法定既定値どおりの表示を法令違反にしない（返品 8 日）。"""
        text = prompt()

        assert "法令が定める既定値どおりの表示を法令違反として指摘しない" in text
        assert "社内基準と法定の既定値は別物" in text


# =============================================================================
# ② 既存の判定原則を落としていない
# =============================================================================

class TestExistingPrinciplesSurvive:

    def test_evidence_only_and_excerpt_rules_are_kept(self, prompt):
        text = prompt()

        for principle in (
            "【規程】に書かれている内容のみを根拠にすること",
            "抵触しない場合は violates=false",
            "該当箇所をそのまま抜き出すこと",
            "否定文脈",
        ):
            assert principle in text

    def test_rule_and_target_are_still_passed(self, prompt):
        text = prompt(rule_id="tokusho-04", text="返品: 商品到着後8日以内")

        assert "返品特約の表示" in text
        assert "特定商取引法 第11条" in text
        assert "返品: 商品到着後8日以内" in text
        assert "# 規程\n規程本文" in text


# =============================================================================
# ③ 規程不一致の帰属（指摘自体は失わない）
# =============================================================================

class TestPolicyMismatchRuleAttribution:

    def _rule(self):
        rule = EC_AD.rule_by_id("policy-01")
        assert rule is not None, "policy-01 が定義されていない"
        return rule

    def test_it_is_not_attributed_to_a_law(self):
        """法令ルールと分ける（実測では特商法第11条に帰属していた）。"""
        rule = self._rule()

        assert rule.law == "社内規程"
        assert rule.category == "規程不一致"
        assert "特定商取引法" not in rule.law

    def test_severity_is_not_high(self):
        """法令違反ではないので重大にしない（実測では重大だった）。"""
        assert self._rule().severity_default == "medium"

    def test_it_is_judged_on_the_whole_document(self):
        """表示と規程の照合は文書全体で見る（#85 の判定単位）。"""
        assert self._rule().always_check is True

    def test_the_description_states_it_is_not_a_legal_violation(self):
        """description は ③ Detect のプロンプトへ入るので、ここで明示しておく。

        ⚠️ 文言は #95 で書き替えた。旧版は「法令が定める既定値どおりの表示
        （例: 返品期限 8 日）は、それ自体は適法である」で終わっており、
        **8 日という表示そのものを免責する**読み方ができた。実測 2026-08-19
        06:11 では policy-01 が violates=false で沈黙している。

        規程不一致の指摘は「適法かどうか」ではなく「自社の規程と食い違うか」
        なので、適法であっても食い違えば指摘する、と明示に書き替えた。
        """
        description = self._rule().description

        assert "法令違反の指摘ではなく、社内整合性の指摘" in description
        # 「適法だから指摘しない」でも「適法でも違えば全部指摘」でもない。
        # 判定軸は「規程より顧客に不利か」（#96 で絞り込んだ。理由は下の
        # TestDirectionOfTheMismatch を参照）。
        assert "適法かどうかは判定材料にしない" in description
        assert "14 日" in description and "8 日" in description

    def test_law_rules_are_unaffected(self):
        """法令ルールの帰属は変えていない。"""
        laws = {r.law for r in EC_AD.rules if r.rule_id != "policy-01"}

        assert laws == {"景品表示法", "特定商取引法", "医薬品医療機器等法"}
        assert EC_AD.rule_by_id("tokusho-04").severity_default == "high"

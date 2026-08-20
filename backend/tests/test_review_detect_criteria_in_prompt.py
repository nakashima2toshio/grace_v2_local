# backend/tests/test_review_detect_criteria_in_prompt.py
"""**`RuleItem.description`（判定基準）が ③ Detect のプロンプトへ届く**ことを固定する。

## 背景 — 3 日間、届かない場所を直していた

`rulesets.py` は冒頭（14 行目）と `RuleItem.description` の定義（107 行目）で
こう宣言している。

    2. **判定基準**: `RuleItem.description` を ③ Detect の LLM プロンプトへ埋め込む。
    description: str   # 判定基準。LLM プロンプトへ埋め込む／根拠フォールバック

**実装がそうなっていなかった。** `create_violation_detector` が組み立てていたのは

    # ルール       ← rule.title / rule.law / rule.article
    # 規程         ← RAG で引いた根拠
    # 対象テキスト  ← 検査対象

の 3 つだけで、`rule.description` はどこにも入っていなかった
（`grep -n "rule.description" review_gates.py` が 0 件）。
LLM が受け取る判定基準は**ルール名だけ**だった。

### なぜ今まで動いているように見えたか

#95 より前、policy-01 の根拠検索はルールセット全体（`ec_ad_rules_anthropic` を含む）
を対象にしていた。そこには policy-01 自身の行が登録されているので、`description` が
**自己ヒット（実測 score 0.9380）して【規程】欄に紛れ込んでいた**。判定基準が LLM に
届いていたのは偶然である。

#95 で policy-01 の検索先を `ec_policy_anthropic`（自社の返品規程）へ限定した。
検索の精度は上がった（返品規定 0.6647 → 0.7543）が、同時に**判定基準が LLM へ届く
唯一の経路を塞いだ**。

| | 【規程】欄の中身 | LLM が持つ判定基準 |
|---|---|---|
| #95 前 | policy-01 自身の行（旧 description） | あり（偶然） |
| #95 後 | 自社の返品規程 | **無し。ルール名だけ** |

その結果 #96 で `description` をどれだけ書き替えても挙動は 1 mm も変わらず、
実測 2026-08-20 19:55 でも同じ誤検出が再現した。

    [policy-01] 社内規程では返品条件として「未使用・未開封」の両方を要件として
                いるが、対象テキストでは「未開封」のみ記載されており、「未使用」の
                条件が欠けている。

`description` はこの誤検出を名指しで禁止している（「未開封の商品は未使用でもある
ため…指摘しない」）。**そのテキストが LLM に渡っていなかった**だけである。

## ここで固定すること

  1. `rule.description` がプロンプトへそのまま入ること（全ルール）
  2. `# 判定基準` が `# 規程` より前に置かれること（何を見るか → 何と照合するか）
  3. policy-01 の誤検出を止める文言が実際に届いていること
  4. 判定の観点は【判定基準】が決める、と原則に書かれていること
  5. 【規程】【対象テキスト】は従来どおり渡ること

⚠️ LLM には接続しない（実際に組み立てたプロンプト文字列を検査する）。
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from backend.app.core.review_gates import create_violation_detector
from backend.app.core.rulesets import EC_AD


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

    def _build(rule_id="policy-01", text="返品: 商品到着後14日以内、未開封に限り返品可能",
               evidence="返品は商品到着後14日以内、未使用・未開封の場合に承ります。"):
        detect = create_violation_detector(
            SimpleNamespace(llm=SimpleNamespace(prompt_addendum=""))
        )
        detect(text, EC_AD.rule_by_id(rule_id), evidence)
        return captured[-1]

    return _build


# =============================================================================
# ① 判定基準がプロンプトへ届く
# =============================================================================

class TestCriteriaReachTheModel:

    def test_the_description_appears_verbatim(self, prompt):
        """policy-01 の判定基準がそのまま入る。

        ⚠️ これが本体。3 日間の誤検出は、この 1 行が無かったことに尽きる。
        """
        assert EC_AD.rule_by_id("policy-01").description in prompt()

    @pytest.mark.parametrize("rule_id", [r.rule_id for r in EC_AD.rules])
    def test_every_rule_carries_its_own_criteria(self, prompt, rule_id):
        """policy-01 に限らず、全ルールの判定基準が届くこと。

        条文ルールも同じ穴に落ちていた（ルール名と条番号しか渡っていなかった）。
        """
        rule = EC_AD.rule_by_id(rule_id)

        assert rule.description in prompt(rule_id=rule_id)

    def test_the_criteria_section_has_its_own_heading(self, prompt):
        """`# 判定基準` という見出しで、根拠（`# 規程`）と区別されること。"""
        text = prompt()

        assert f"# 判定基準\n{EC_AD.rule_by_id('policy-01').description}" in text

    def test_criteria_come_before_the_evidence(self, prompt):
        """順序は 判定基準 → 規程 → 対象テキスト。

        「何を見るか」を先に与えてから「何と照合するか」を渡す。逆順だと
        規程本文に引きずられて主題外の差分を拾いやすい。
        """
        text = prompt()

        assert text.index("# 判定基準") < text.index("# 規程") < text.index("# 対象テキスト")


# =============================================================================
# ② 実測の誤検出を止める文言が届いている
# =============================================================================

class TestTheMeasuredFalsePositiveIsAddressed:
    """実測 2026-08-20 19:55 の「未使用が欠けている」を名指しで潰す。"""

    def test_the_wording_only_mismatch_is_forbidden_in_the_prompt(self, prompt):
        text = prompt()

        assert "語句が一致しないことを理由に指摘してはならない" in text

    def test_the_unopened_example_is_in_the_prompt(self, prompt):
        """「未使用・未開封」→「未開封」を指摘しない、という具体例が届くこと。"""
        text = prompt()

        assert "未開封の商品は未使用でもある" in text
        assert "顧客が返品できる範囲は狭まっていない" in text

    def test_the_direction_of_the_mismatch_is_in_the_prompt(self, prompt):
        """判定軸（規程より顧客に不利か）が届くこと。"""
        text = prompt()

        assert "顧客が受けられる扱いが、規程より狭まっているか" in text


# =============================================================================
# ③ 原則が「判定基準が観点を決める」と言っている
# =============================================================================

class TestThePrincipleDelegatesScopeToTheCriteria:

    def test_the_criteria_decide_what_is_judged(self, prompt):
        text = prompt()

        assert "何を見るかは【判定基準】が決める" in text
        assert "そこに書かれていない観点で指摘しないこと" in text

    def test_facts_still_come_only_from_evidence_and_target(self, prompt):
        """事実の裏付けは【規程】と【対象テキスト】に限る（推測禁止は維持）。

        ⚠️ 旧文言「【規程】に書かれている内容のみを根拠にすること」は、
        判定基準を追加した今は**判定基準そのものを無視させる**読み方ができる。
        役割を分けて書き直した。
        """
        text = prompt()

        assert "事実の裏付けは【規程】と【対象テキスト】に書かれている内容だけを使う" in text
        assert "推測で指摘しないこと" in text


# =============================================================================
# ④ 既存の受け渡しを壊していない
# =============================================================================

class TestExistingSectionsSurvive:

    def test_rule_identity_evidence_and_target_are_still_passed(self, prompt):
        text = prompt(rule_id="tokusho-04",
                      text="返品: 商品到着後8日以内",
                      evidence="返品規程の本文")

        assert "返品特約の表示" in text
        assert "特定商取引法 第11条" in text
        assert "# 規程\n返品規程の本文" in text
        assert "# 対象テキスト\n返品: 商品到着後8日以内" in text

    def test_subject_scope_constraints_are_kept(self, prompt):
        """#88 で入れた主題スコープの制約を落としていないこと。"""
        text = prompt()

        for principle in (
            "判定するのは【ルール】の主題だけ",
            "ルールが前提とする取引形態",
            "記載の有無だけを見る",
            "法令が定める既定値どおりの表示を法令違反として指摘しない",
            "否定文脈",
        ):
            assert principle in text, principle

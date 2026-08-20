# backend/tests/test_review_yakki_product_scope.py
"""薬機法ルールが**品目（食品／化粧品）を取り違えない**ことを固定する。

## 背景（実測 2026-08-20 20:18 / 化粧品LP案）

    [HIGH] 食品の医薬品的効能標榜（医薬品医療機器等法 第68条）
    該当箇所: シミが治ると評判で
    指摘: 「シミが治る」という表現は、食品・健康食品において疾病または皮膚症状の
          治療効果を標榜するものであり、医薬品的効能効果の標榜に該当しうる。

**対象は美容液＝化粧品であって食品ではない。** 指摘の中身（「シミが治る」は違法）は
正しいが、条文（第68条 ではなく 第66条）も品目（食品ではなく化粧品）も誤っている。
しかも message が「食品・健康食品において」と、対象テキストにない事実を述べている。

### 原因は第1段のキーワード

    yakki-01 kw=['治る', '治療', '改善', ...]        → 「シミが治る」に一致
    yakki-02 kw=['シワが消える', '若返る', '美白', ...] → 一致しない

yakki-02（化粧品の効能範囲逸脱・第66条）は**候補にすら上がらない**ので、正しいルールが
LLM に一度も提示されていなかった。

### 修正の方針

第1段は品目を判別できない（キーワード一致だけ）。だから**広く拾って第2段で絞る**という
このモジュール本来の建て付けに戻す。

  1. yakki-02 のキーワードに治療訴求語を**わざと重ねる** → 両方が候補に上がる
  2. 各ルールの `description`（＝【判定基準】）に品目条件を書く
     - yakki-01: 化粧品なら violates=false（yakki-02 の主題）
     - yakki-02: 食品なら violates=false（yakki-01 の主題）

⚠️ **2 は #98 より前には成立しなかった。** `description` が ③ Detect のプロンプトへ
渡っていなかったため、品目条件をいくら書いても LLM には届かなかった
（経緯は `test_review_detect_criteria_in_prompt.py` 冒頭）。

## ここで固定すること

  1. 「シミが治る」で **yakki-02 が候補に上がる**こと（第1段）
  2. 品目の切り分けが**プロンプトへ届く**こと（第2段）
  3. yakki-01 の主題は食品、yakki-02 の主題は化粧品、という帰属が保たれること
  4. 既存のキーワードを落としていないこと

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

# 実測 2026-08-20 20:18 の該当セグメント（化粧品LP案の 2 文目）。
COSMETIC_CURE_CLAIM = (
    "使い続ければシミが治ると評判で、副作用がないので誰でも安心してお使いいただけます。"
)


def _candidate_ids(segment: str) -> set[str]:
    return {c.rule_id for c in select_candidate_rules(segment, EC_AD)}


# =============================================================================
# ① 第1段: 正しいルールが候補に上がる
# =============================================================================

class TestTheCosmeticRuleBecomesACandidate:

    def test_the_cosmetic_rule_is_offered(self):
        """「シミが治る」で yakki-02 が候補に入る。

        ⚠️ これが本体。実測では候補に上がらず、正しいルールが LLM に
        一度も提示されていなかった。
        """
        assert "yakki-02" in _candidate_ids(COSMETIC_CURE_CLAIM)

    def test_the_food_rule_is_still_offered_too(self):
        """yakki-01 も候補のまま（第1段は品目を判別できないので両方出す）。

        絞り込みは第2段の LLM が【判定基準】の品目条件で行う。
        """
        assert "yakki-01" in _candidate_ids(COSMETIC_CURE_CLAIM)

    @pytest.mark.parametrize("phrase", ["シミが治る", "ニキビが治る", "肌荒れを改善", "症状に効く"])
    def test_treatment_claims_reach_the_cosmetic_rule(self, phrase):
        """治療訴求の言い回しが一通り yakki-02 へ届くこと。"""
        assert "yakki-02" in _candidate_ids(f"この美容液は{phrase}と評判です。")

    def test_the_original_cosmetic_keywords_survive(self):
        """既存のキーワードを落としていない（重ねただけ）。"""
        keywords = EC_AD.rule_by_id("yakki-02").keywords

        for original in ("シワが消える", "若返る", "美白", "アンチエイジング",
                         "細胞再生", "永久"):
            assert original in keywords, original


# =============================================================================
# ② 第2段: 品目の切り分けがプロンプトへ届く
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

    def _build(rule_id):
        detect = create_violation_detector(
            SimpleNamespace(llm=SimpleNamespace(prompt_addendum=""))
        )
        detect(COSMETIC_CURE_CLAIM, EC_AD.rule_by_id(rule_id), "規程本文")
        return captured[-1]

    return _build


class TestTheProductScopeReachesTheModel:

    def test_the_food_rule_excludes_cosmetics(self, prompt):
        """yakki-01 のプロンプトに「化粧品なら violates=false」が入る。"""
        text = prompt("yakki-01")

        assert "このルールの対象は食品・健康食品" in text
        assert "このルールでは violates=false" in text
        assert "yakki-02" in text          # どのルールへ回すかを明示する
        assert "品目を取り違えて" in text

    def test_the_cosmetic_rule_excludes_food(self, prompt):
        """yakki-02 のプロンプトに「食品なら violates=false」が入る。"""
        text = prompt("yakki-02")

        assert "このルールの対象は化粧品" in text
        assert "食品・健康食品なら violates=false" in text
        assert "yakki-01" in text

    def test_the_cosmetic_rule_claims_treatment_wording(self, prompt):
        """yakki-02 が「治療の訴求も範囲外」と明言していること。

        これが無いと、化粧品側へ回しても「56項目の話ではない」と判断して
        取りこぼす（誤検出が false negative に置き換わるだけになる）。
        """
        text = prompt("yakki-02")

        assert "疾病・症状の『治療』を訴求する表現も 56項目の範囲外" in text
        assert "シミが治る" in text


# =============================================================================
# ③ 帰属（条文・品目）は変えていない
# =============================================================================

class TestAttributionIsUnchanged:

    def test_the_food_rule_keeps_its_article(self):
        rule = EC_AD.rule_by_id("yakki-01")

        assert rule.law == "医薬品医療機器等法"
        assert rule.article == "第68条"

    def test_the_cosmetic_rule_keeps_its_article(self):
        rule = EC_AD.rule_by_id("yakki-02")

        assert rule.law == "医薬品医療機器等法"
        assert rule.article == "第66条"

    def test_neither_became_an_always_check_rule(self):
        """効能効果はキーワード型（書かれている表現を見る）のまま。"""
        assert EC_AD.rule_by_id("yakki-01").always_check is False
        assert EC_AD.rule_by_id("yakki-02").always_check is False


# =============================================================================
# ④ 「記載が無い」指摘の該当箇所
# =============================================================================

class TestAbsenceFindingsHaveNoExcerpt:
    """実測 2026-08-20 20:18 で tokusho-05 の該当箇所が的外れだった。

        [HIGH] 事業者名・住所・連絡先
        該当箇所: 当社の美容液は業界No.1の実力です。      ← 無関係
        指摘: 事業者の氏名・住所・電話番号のいずれも記載されていない。

    無い記載は抜き出せないので excerpt は空にするのが正しい。
    """

    def test_the_prompt_forbids_a_bogus_excerpt(self, prompt):
        text = prompt("yakki-01")

        assert "「記載が無い」ことを指摘するときは excerpt を空にすること" in text
        assert "関係のない文を該当箇所として入れない" in text

    def test_the_excerpt_rule_for_real_violations_survives(self, prompt):
        """書かれている表現を指摘するときは従来どおり抜き出す。"""
        text = prompt("yakki-01")

        assert "該当箇所をそのまま抜き出すこと" in text

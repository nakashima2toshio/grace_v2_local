# backend/tests/test_review_multi_item_rules.py
"""**複数の事項を求めるルールで、一部の欠落を見落とさない**ことを固定するテスト。

## 背景（実測 2026-08-17 23:50 と 2026-08-18 21:41 で再現）

法定表示事項を点検させたところ、**送料の記載漏れが指摘されなかった**。

    21:41:44  RAG for tokusho-01（販売価格・送料の明示）
    21:41:49  判定 ← violates=false（groundedness 呼び出しが続かない）

対象の広告に**送料の記載は無い**。特商法第11条は送料の明示を義務付けているので、
これは指摘すべきである。2 回の実行で同じ結果になっており、偶発ではない。

## 原因は #88 で追加した制約の書き方

    「〜の表示」「〜の明示」を求めるルールは、記載の有無だけを見る。
    **記載があれば violates=false** とすること。

**「何の記載か」を書いていなかった。** `tokusho-01` は「**販売価格・送料**の明示」で
2 つの事項を求めるため、「販売価格の記載はある」→「記載があるので violates=false」
と読める。

同じ穴は表記漏れ 7 ルール中 **4 つ**にある。

    tokusho-01  販売価格 + 送料
    tokusho-04  可否 + 条件 + 期限 + 送料負担
    tokusho-05  事業者名 + 住所 + 電話番号 + 代表者名
    tokusho-06  契約期間 + 継続回数 + 支払総額 + 解約条件

## ⚠️ 見落とし（false negative）は誤検知より重い

`review_gates.py` の方針は「**Review では指摘を消す方向のミスが最も痛い**」。
コンプライアンス点検では、余計な指摘は人が捨てられるが、出なかった指摘は
気付きようがない。

なお 20:07 の実行では tokusho-01 が発火していたものの、
「販売価格および送料の具体的な記載が一切ありません」と**販売価格まで無い**と
書いていた。今回の修正は「欠けている事項だけを書く」ことも同時に固定する。

ここで固定すること:
  1. 複数事項をすべて個別に確認する制約が入ること
  2. 1 つでも欠ければ violates=true と明示されること
  3. 記載済みの事項を「無い」と書かせないこと
  4. 実測に対応する具体例（販売価格あり・送料なし）が入ること
  5. すべて揃っていれば violates=false（誤検知を戻さない）
  6. #88 で入れた他の制約を落としていないこと

⚠️ LLM には接続しない（プロンプトは実際に組み立てたものを検査する）。
プロンプト変更なので、この検査は「指示が届いていること」までしか保証できない。
実挙動は次回の実行ログで確認する。
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from backend.app.core.review_gates import create_violation_detector
from backend.app.core.rulesets import EC_AD

# 実測の対象文書（販売価格の記載はあるが送料の記載が無い）。
GOOD_LP = """当社の美容液は、うるおいを与えて肌をなめらかに整えます。
■ 特定商取引法に基づく表記
販売業者: 株式会社サンプル
運営責任者: 山田太郎
所在地: 東京都千代田区1-1-1
電話番号: 03-0000-0000
販売価格: 4,980円（税込）
返品: 商品到着後8日以内、未開封に限り返品可能（送料はお客様負担）"""

# 複数の事項を求める表記漏れルール（同じ穴を持つ）。
MULTI_ITEM_RULES = ("tokusho-01", "tokusho-04", "tokusho-05", "tokusho-06")


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

    def _build(rule_id="tokusho-01", text=GOOD_LP):
        detect = create_violation_detector(
            SimpleNamespace(llm=SimpleNamespace(prompt_addendum=""))
        )
        detect(text, EC_AD.rule_by_id(rule_id), "規程本文")
        return captured[-1]

    return _build


# =============================================================================
# ① 複数事項をすべて確認させる
# =============================================================================

class TestAllRequiredItemsAreChecked:

    def test_every_item_must_be_checked_individually(self, prompt):
        text = prompt()

        assert "ルールが複数の事項を求めるときは、そのすべてを個別に確認すること" in text

    def test_one_missing_item_is_a_violation(self, prompt):
        """1 つでも欠ければ violates=true（見落としを作らない）。"""
        text = prompt()

        assert "1 つでも欠けていれば violates=true" in text

    def test_all_present_is_not_a_violation(self, prompt):
        """すべて揃っていれば violates=false（誤検知を戻さない）。

        tokusho-05（事業者名・住所・電話番号・代表者名）は 4 事項すべてが
        記載されているので、この文書では指摘してはいけない。
        """
        text = prompt(rule_id="tokusho-05")

        assert "すべて揃っているときだけviolates=false" in text.replace("\n", "")


# =============================================================================
# ② 記載済みの事項を「無い」と書かせない
# =============================================================================

class TestMessageNamesOnlyTheMissingItems:
    """20:07 の実行では「販売価格および送料の記載が一切ありません」と、
    実在する販売価格まで「無い」と書いていた。
    """

    def test_message_covers_only_missing_items(self, prompt):
        text = prompt()

        assert "欠けている事項だけ" in text
        assert "記載済みの事項を「無い」と書かない" in text

    def test_the_measured_case_is_given_as_an_example(self, prompt):
        """実測に対応する具体例（販売価格あり・送料なし）が入っていること。"""
        text = prompt()

        assert "販売価格: 4,980円（税込）" in text
        assert "送料の記載が無い" in text
        assert "message は送料についてのみ述べる" in text


# =============================================================================
# ③ #88 の制約を落としていない
# =============================================================================

class TestSubjectScopeConstraintsSurvive:

    @pytest.mark.parametrize("fragment", [
        "判定するのは【ルール】の主題だけ",
        "ルールが前提とする取引形態",
        "記載の有無だけを見る",
        "法令が定める既定値どおりの表示を法令違反として指摘しない",
    ])
    def test_constraint_is_kept(self, prompt, fragment):
        assert fragment in prompt()

    def test_base_principles_are_kept(self, prompt):
        text = prompt()

        for principle in (
            "【規程】に書かれている内容のみを根拠にすること",
            "該当箇所をそのまま抜き出すこと",
            "否定文脈",
        ):
            assert principle in text


# =============================================================================
# ④ 対象ルールの前提（穴を持つルールの一覧）
# =============================================================================

class TestMultiItemRulesExist:
    """ルール定義が変わってこのテストの前提が崩れたら気付けるようにする。"""

    @pytest.mark.parametrize("rule_id", MULTI_ITEM_RULES)
    def test_rule_requires_multiple_items(self, rule_id):
        rule = EC_AD.rule_by_id(rule_id)

        assert rule is not None, f"{rule_id} が存在しない"
        assert rule.always_check is True
        # 「A・B」「A、B」等で複数事項を列挙している
        assert "・" in rule.title or "、" in rule.description, (
            f"{rule_id} が単一事項のルールになっている（前提が変わった）"
        )

    def test_the_prompt_reaches_every_multi_item_rule(self, prompt):
        """4 ルールすべてで同じ制約が渡ること。"""
        for rule_id in MULTI_ITEM_RULES:
            assert "1 つでも欠けていれば violates=true" in prompt(rule_id=rule_id)

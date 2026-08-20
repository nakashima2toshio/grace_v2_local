# backend/tests/test_review_absence_excerpt.py
"""**excerpt が空でも表記漏れの指摘を取り下げない**ことを固定する。

## 背景（実測 2026-08-20 20:18 → 21:01）

\\#99 で「記載が無いことを指摘するときは excerpt を空にすること」という制約を
プロンプトへ足した。無関係な文を該当箇所として引用する誤りを止めるためである。

    実測 20:18 [HIGH] 事業者名・住所・連絡先
              該当箇所: 当社の美容液は業界No.1の実力です。   ← 無関係
              指摘: 事業者の氏名・住所・電話番号のいずれも記載されていない。

制約は効いた（21:01 では表記漏れ 3 件すべて該当箇所が空）。**ところが同じ実行で
tokusho-05 の指摘そのものが消えた。**

| ルール | 20:18 | 21:01 |
|---|---|---|
| tokusho-05 事業者名・住所・連絡先 | 指摘あり（excerpt が無関係） | **消えた** |

対象テキスト（化粧品LP案 100 字）に事業者名・住所・電話番号は**一切ない**。
特商法 第11条 の表示義務違反であり、20:18 の指摘は**正しかった**。
21:01 で消えたのは取りこぼし（false negative）である。

### なぜ制約が疑わしいか

tokusho-05 の判定に効きうる変更は #99 のうち excerpt 制約だけである。

  - yakki-01 / yakki-02 の変更 → tokusho-05 とは無関係
  - Qdrant 再登録（20:47）→ tokusho-05 の規程本文は 8/18 版と同一（変更 3 件は
    yakki-01 / yakki-02 / policy-01 のみ）

プロンプト上、excerpt 制約は

    「抵触する場合、excerpt には対象テキストから該当箇所をそのまま抜き出すこと」

の直後に置かれている。「抜き出せないなら抵触ではない」と読む余地があった。

⚠️ **1 回の実行では断定できない**（LLM の揺らぎの可能性も残る）。ただし取りこぼしは
Review にとって最も損失が大きい失敗であり、読み違えの余地は塞いでおく。

## 修正

「excerpt が空であることは、指摘を取り下げる理由にはならない」を明示に足した。
表記漏れの指摘は必ず excerpt が空になる、とも書いた。

## ここで固定すること

  1. excerpt が空でも violates=true にしてよい、と書かれていること
  2. 無関係な文を入れない制約（#99）を落としていないこと
  3. 書かれている表現を指摘するときの抜き出し規則（従来）を落としていないこと

⚠️ LLM には接続しない（組み立てたプロンプト文字列を検査する）。
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from backend.app.core.review_gates import create_violation_detector
from backend.app.core.rulesets import EC_AD

# 実測 21:01 で指摘が消えたルール（文書全体で判定する表記漏れ）。
ABSENCE_RULE = "tokusho-05"


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

    def _build(rule_id=ABSENCE_RULE, text="当社の美容液は業界No.1の実力です。"):
        detect = create_violation_detector(
            SimpleNamespace(llm=SimpleNamespace(prompt_addendum=""))
        )
        detect(text, EC_AD.rule_by_id(rule_id), "規程本文")
        return captured[-1]

    return _build


# =============================================================================
# ① 空の excerpt は指摘を取り下げる理由にならない
# =============================================================================

class TestAnEmptyExcerptDoesNotCancelTheFinding:

    def test_the_prompt_says_so_explicitly(self, prompt):
        """⚠️ これが本体。実測 21:01 の取りこぼしを塞ぐ。"""
        text = prompt()

        assert "excerpt が空であることは、指摘を取り下げる理由にはならない" in text

    def test_it_tells_the_model_what_to_emit_instead(self, prompt):
        """「では何を出すのか」まで書く（禁止だけだと従いようがない）。"""
        text = prompt()

        assert "抵触しているなら violates=true とすること" in text
        assert "excerpt を空にしたまま message と suggestion だけを書けばよい" in text

    def test_it_names_the_case_that_always_looks_like_this(self, prompt):
        """表記漏れは必ずこの形になる、と一般化しておく。"""
        assert "表記漏れの指摘は必ずこの形になる" in prompt()

    @pytest.mark.parametrize("rule_id", [r.rule_id for r in EC_AD.always_check_rules])
    def test_every_always_check_rule_gets_the_guard(self, prompt, rule_id):
        """表記漏れ（always_check）の全ルールにこの保護が届くこと。"""
        text = prompt(rule_id=rule_id)

        assert "指摘を取り下げる理由にはならない" in text


# =============================================================================
# ② #99 の制約と従来の抜き出し規則を落としていない
# =============================================================================

class TestTheSurroundingRulesSurvive:

    def test_the_bogus_excerpt_ban_is_kept(self, prompt):
        """無関係な文を該当箇所に入れない（#99 で入れた制約）。"""
        text = prompt()

        assert "「記載が無い」ことを指摘するときは excerpt を空にすること" in text
        assert "関係のない文を該当箇所として入れない" in text

    def test_the_verbatim_excerpt_rule_is_kept(self, prompt):
        """書かれている表現を指摘するときは従来どおり抜き出す。"""
        text = prompt()

        assert "該当箇所をそのまま抜き出すこと" in text
        assert "言い換えない" in text

    def test_the_multi_item_rule_is_kept(self, prompt):
        """複数事項を個別に確認する制約（#93）も残っていること。

        tokusho-05 は事業者名・住所・電話番号・代表者名を同時に求めるので、
        この制約が消えると指摘内容が雑になる。
        """
        text = prompt()

        assert "ルールが複数の事項を求めるときは、そのすべてを個別に確認すること" in text

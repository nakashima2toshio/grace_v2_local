# tests/test_review_cosmetic_lp_expected.py
"""画面のサンプル「NG 例（優良誤認・薬機法）＝化粧品LP案」で守る期待値。

## 経緯（実測 2026-10-02・3 サンプル × 2 モデル）

化粧品LP案では選択できる 2 つのモデルとも同じ 11 件を指摘した（docs/review_rag_rules_todo.md §0.3）。監修の結果、
次のように整理した。

| 区分 | ルール | 扱い |
|---|---|---|
| 確実な指摘 | keihyo-03 / yakki-02 / yakki-04 / keihyo-04 / tokusho-01 | 必ず出る（ここで固定） |
| 要確認が妥当 | keihyo-07（期間限定・期限なし） | 出ても「確定」にしない |
| 過剰 | keihyo-08（条件の付かない「送料無料」） | ③ Detect の判定基準で除外 |
| 抜粋だから出る | tokusho-02〜05 | 別途（特商法表記が別ページの LP の扱い） |

LLM の判断そのものはスタブでは確かめられない。ここで固定するのは
**LLM へ渡るまでの経路**（どのルールが判定に回るか・強制 high・確定の上限）と、
③ Detect へ渡す**判定基準・指示文の中身**である。

⚠️ LLM にも Qdrant にも接続しない。
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from backend.app.core.review_agent import run_review_agent_core
from backend.app.core.review_gates import DetectVerdict, create_violation_detector
from backend.app.core.rulesets import EC_AD

# frontend/src/components/ReviewForm.tsx の「NG 例（優良誤認・薬機法）」と同じ本文
COSMETIC_LP = (
    "当社の美容液は業界No.1の実力です。\n"
    "\n"
    "使い続ければシミが治ると評判で、副作用がないので誰でも安心してお使いいただけます。\n"
    "\n"
    "・今だけ期間限定 通常価格 12,000円 → 4,980円\n"
    "・送料無料"
)

# 監修で「確実な指摘」とした 5 件
EXPECTED = {"keihyo-03", "yakki-02", "yakki-04", "keihyo-04", "tokusho-01"}
# うち重大リスク語（No.1 / 治る / 副作用がない）で強制 high になるもの
FORCED = {"keihyo-03", "yakki-02", "yakki-04"}


def _flag(rule_ids):
    """指定したルールだけ違反とする検出器（LLM の代役）。"""
    def detect(_text, rule, _evidence):
        if rule.rule_id in rule_ids:
            return DetectVerdict(violates=True, message=f"{rule.title}の指摘",
                                 suggestion="〇〇へ修正してください")
        return DetectVerdict(violates=False)
    return detect


class TestCosmeticLpExpectedFindings:

    def test_expected_rules_reach_detect(self, review_stub):
        """確実な 5 件が、どれも ③ Detect の判定に回ること（入口の固定）。"""
        run_review_agent_core(COSMETIC_LP)

        judged = {rule_id for _text, rule_id, _ev in review_stub.detect_calls}
        assert EXPECTED <= judged, f"判定に回っていない: {EXPECTED - judged}"

    def test_expected_findings_survive_to_the_result(self, review_stub):
        review_stub.groundedness.support_rate = 0.95
        review_stub.detect = _flag(EXPECTED)

        result = run_review_agent_core(COSMETIC_LP)

        found = {f.rule_id for f in result.findings}
        assert EXPECTED <= found, f"指摘が消えた: {EXPECTED - found}"
        for finding in result.findings:
            if finding.rule_id in FORCED:
                assert finding.severity == "high" and finding.forced
                assert finding.status == "review_required"


class TestKeihyo07IsNeverConfirmedAutomatically:

    def test_high_support_rate_stops_at_review_required(self, review_stub):
        """期限が無いだけでは常態化は決まらない → 確定にせず人が判断する。"""
        review_stub.groundedness.support_rate = 0.95
        review_stub.groundedness.verified = True
        review_stub.detect = _flag({"keihyo-07"})

        result = run_review_agent_core(COSMETIC_LP)

        keihyo07 = [f for f in result.findings if f.rule_id == "keihyo-07"]
        assert keihyo07, "keihyo-07 の指摘自体は残す"
        assert all(f.status == "review_required" for f in keihyo07)

    def test_only_rules_that_ask_for_it_are_capped(self):
        capped = {r.rule_id for r in EC_AD.rules if r.confirm_needs_human}
        assert capped == {"keihyo-07"}


@pytest.fixture
def prompt(monkeypatch):
    """`detect()` が LLM へ渡したプロンプト文字列を返す。"""
    captured: list[str] = []

    def _generate(**kwargs):
        captured.append(kwargs["contents"])
        return SimpleNamespace(text='{"violates": false}')

    client = SimpleNamespace(models=SimpleNamespace(generate_content=_generate))
    module = SimpleNamespace(create_chat_client=lambda _c: client)
    monkeypatch.setitem(sys.modules, "grace", SimpleNamespace(llm_compat=module))
    monkeypatch.setitem(sys.modules, "grace.llm_compat", module)

    def _build(rule_id="keihyo-08", text="・送料無料"):
        detect = create_violation_detector(
            SimpleNamespace(llm=SimpleNamespace(prompt_addendum=""))
        )
        detect(text, EC_AD.rule_by_id(rule_id), "規程本文")
        return captured[-1]

    return _build


class TestDetectCriteria:

    def test_keihyo08_does_not_flag_unconditional_free_shipping(self, prompt):
        text = prompt("keihyo-08")
        assert "条件を付けずに「送料無料」「無料」とだけ書いているとき" in text
        assert "条件があることが広告文から読み取れる" in text

    def test_keihyo07_does_not_assert_the_practice_continues(self, prompt):
        assert "常態化を事実として断定しないこと" in prompt("keihyo-07", "今だけ期間限定")

    def test_public_descriptions_are_unchanged(self):
        """指示文は 2 段落目以降に足した。要旨（登録済みの規程行・検索クエリ）は
        変えていないので、Qdrant の再登録は要らない。"""
        assert EC_AD.rule_by_id("keihyo-08").public_description().endswith(
            "条件を記載せず無料を強調する表示は有利誤認に該当しうる。")
        assert EC_AD.rule_by_id("keihyo-07").public_description().endswith(
            "期限経過後は速やかに表示を変更する必要がある。")


class TestSuggestionAndWording:

    def test_suggestion_must_not_add_stricter_conditions(self, prompt):
        """実測 2026-10-02: 返品の修正案に「未使用」を足し、今より厳しくしていた。"""
        assert "今より顧客に厳しい表示にしないこと" in prompt()

    def test_suggestion_must_not_invent_values(self, prompt):
        """実測 2026-10-02: 根拠の無い「5,000円以上のご購入」を修正案に書いていた。"""
        text = prompt()
        assert "修正案に架空の値を書かないこと" in text
        assert "「〇〇」で穴埋め" in text

    def test_user_facing_wording(self, prompt):
        """実測 2026-10-02: 「該当しうります」「対象テキスト」が画面に出ていた。"""
        text = prompt()
        assert "「対象テキスト」" in text and "「広告文」と書くこと" in text
        assert "「該当しうります」のような誤った活用にしないこと" in text

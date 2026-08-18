# backend/tests/test_review_gates.py
"""GRACE-Review の判定ロジック（backend/app/core/review_gates.py）のテスト。

設計: backend/docs/review_agent_spec.md §3.3。LLM・Qdrant・API キーは不要。

判定は純関数として切り出してあるため、ここでは**全分岐を直接**固定できる。
LLM 判定器のファクトリ（`create_*`）については、`grace.llm_compat` を sys.modules へ
差し込んだスタブクライアントで、正常系と異常系（空応答・例外・想定外出力）を検証する。

特に重視しているのは「**判定できないときにどちらへ倒れるか**」である。
Review では指摘を消す方向のミスが最も痛い（見落とし）ため、判定不能時は
指摘を残す側へ倒れることを各所で固定している。
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from backend.app.core.review_gates import (
    VACUOUS_MARKERS,
    DetectVerdict,
    adjust_severity,
    apply_forced_high,
    create_mention_classifier,
    create_vacuous_judge,
    create_violation_detector,
    decide_finding_status,
    detect_vacuous_finding,
    select_candidate_rules,
    select_document_rules,
    should_force_high,
    should_rescue_finding,
)
from backend.app.core.rulesets import EC_AD

NOTIFY = EC_AD.notify_th   # 0.85
CONFIRM = EC_AD.confirm_th  # 0.60


@pytest.fixture
def fake_llm(monkeypatch):
    """`grace.llm_compat.create_chat_client` をスタブへ差し替えるフィクスチャ。

    `grace` パッケージ本体は Embedding 用の重い依存を引くため、sys.modules へ
    直接スタブを差し込んで実 import を回避する（CI でも同じ経路になる）。
    """

    def _install(responder):
        client = SimpleNamespace(models=SimpleNamespace(generate_content=responder))
        module = SimpleNamespace(create_chat_client=lambda _c: client)
        monkeypatch.setitem(sys.modules, "grace", SimpleNamespace(llm_compat=module))
        monkeypatch.setitem(sys.modules, "grace.llm_compat", module)

    return _install


def _config_stub():
    """review_gates が触る属性だけを持つ config スタブ。"""
    return SimpleNamespace(llm=SimpleNamespace(prompt_addendum=""))


def _text_response(text: str):
    """generate_content の戻り（text 属性のみ）。"""
    return lambda **_kwargs: SimpleNamespace(text=text)


# =============================================================================
# ① 第1段: select_candidate_rules
# =============================================================================

def test_select_candidates_returns_empty_without_ruleset():
    """RuleSet 未指定なら候補ゼロ（LLM 呼び出しも発生しない）。"""
    assert select_candidate_rules("業界No.1です", None) == []


def test_select_candidates_excludes_always_check_rules():
    """⚠️ **意図的な反転。** always_check はセグメント候補に**入らない**。

    以前は「always_check のルールはキーワード不問で常に候補になる」ことを固定して
    いた。これが誤検知の原因だった（実測 2026-08-17 20:07）。判定 LLM にセグメント
    1 行だけを渡して「文書に記載が一切ない」と判定させていたため、同じ文書の別の行に
    書かれている事業者名・返品特約・販売価格まで「無い」と指摘していた。

    表記漏れは `select_document_rules` が文書全体で判定する。
    """
    assert select_candidate_rules("", EC_AD) == []
    assert select_candidate_rules("本日は晴天なり。", EC_AD) == []


def test_select_document_rules_returns_the_always_check_rules():
    """文書全体スコープの候補は always_check のルール。"""
    candidates = select_document_rules(EC_AD)
    ids = {c.rule_id for c in candidates}

    assert ids == {r.rule_id for r in EC_AD.always_check_rules}
    for candidate in candidates:
        assert candidate.always_check is True
        assert candidate.matched_keyword is None


def test_select_document_rules_without_ruleset():
    assert select_document_rules(None) == []


def test_select_candidates_matches_keyword_rule():
    """キーワード一致したルールが候補に加わり、一致語が記録される。"""
    candidates = select_candidate_rules("業界No.1の品質です", EC_AD)
    hit = [c for c in candidates if c.rule_id == "keihyo-03"]
    assert len(hit) == 1
    assert hit[0].matched_keyword == "No.1"
    assert hit[0].always_check is False


def test_select_candidates_skips_unmatched_keyword_rules():
    """一致しないキーワードルールは候補に入らない（第2段の呼び出しを節約）。"""
    candidates = select_candidate_rules("本日は晴天なり。", EC_AD)
    keyword_ids = {c.rule_id for c in candidates if not c.always_check}
    assert keyword_ids == set()


# =============================================================================
# ② 第2段: create_violation_detector
# =============================================================================

def test_violation_detector_parses_verdict(fake_llm):
    """正常系: JSON 応答が DetectVerdict へパースされる。"""
    fake_llm(_text_response(
        '{"violates": true, "message": "根拠の明示が無い", '
        '"suggestion": "調査出典を併記する", "excerpt": "業界No.1"}'
    ))
    detect = create_violation_detector(_config_stub())
    verdict = detect("業界No.1です", EC_AD.rule_by_id("keihyo-03"), "根拠条文")
    assert isinstance(verdict, DetectVerdict)
    assert verdict.violates is True
    assert verdict.excerpt == "業界No.1"


def test_violation_detector_returns_none_on_empty_response(fake_llm):
    """空応答は None（呼び出し側が安全側＝要確認へ倒す）。"""
    fake_llm(_text_response(""))
    detect = create_violation_detector(_config_stub())
    assert detect("t", EC_AD.rule_by_id("keihyo-03"), "e") is None


def test_violation_detector_returns_none_on_exception(fake_llm):
    """API 例外は None（例外を伝播させてパイプラインを止めない）。"""

    def boom(**_kwargs):
        raise RuntimeError("api down")

    fake_llm(boom)
    detect = create_violation_detector(_config_stub())
    assert detect("t", EC_AD.rule_by_id("keihyo-03"), "e") is None


def test_violation_detector_returns_none_on_broken_json(fake_llm):
    """スキーマ不一致も None（安全側）。"""
    fake_llm(_text_response("これはJSONではない"))
    detect = create_violation_detector(_config_stub())
    assert detect("t", EC_AD.rule_by_id("keihyo-03"), "e") is None


# =============================================================================
# ③ 重大リスク語の二段判定: should_force_high / create_mention_classifier
# =============================================================================

def test_force_high_without_ruleset():
    assert should_force_high("業界No.1", None) == (False, None, None)


def test_force_high_no_keyword_match():
    """重大リスク語が無ければ第2段（LLM）は呼ばれない。"""

    def never_called(_text):
        raise AssertionError("キーワード不一致なのに分類器が呼ばれた")

    assert should_force_high("本日は晴天なり。", EC_AD, never_called) == (False, None, None)


def test_force_high_without_classifier_forces():
    """分類器が無い場合は安全側＝強制 high。"""
    forced, keyword, mention = should_force_high("業界No.1の品質", EC_AD)
    assert forced is True
    assert keyword == "No.1"
    assert mention is None


@pytest.mark.parametrize(
    "mention,expected_forced",
    [("claim", True), ("negation", False), ("quotation", False), (None, True)],
)
def test_force_high_second_stage(mention, expected_forced):
    """claim は強制、negation / quotation は誤検知として抑止。分類失敗は安全側。"""
    forced, keyword, got = should_force_high(
        "No.1という表現について", EC_AD, lambda _t: mention
    )
    assert forced is expected_forced
    assert keyword == "No.1"
    assert got == mention


@pytest.mark.parametrize(
    "output,expected",
    [
        ("claim", "claim"),
        ("negation", "negation"),
        ("quotation", "quotation"),
        ("  CLAIM  ", "claim"),
        ("わかりません", None),
    ],
)
def test_mention_classifier_parses_output(fake_llm, output, expected):
    """分類器の出力パース。想定外の語は None（安全側へ倒す材料）。"""
    fake_llm(_text_response(output))
    classify = create_mention_classifier(_config_stub())
    assert classify("No.1です") == expected


def test_mention_classifier_returns_none_on_exception(fake_llm):
    def boom(**_kwargs):
        raise RuntimeError("api down")

    fake_llm(boom)
    assert create_mention_classifier(_config_stub())("No.1") is None


# =============================================================================
# ④ 誤検知抑止: detect_vacuous_finding / create_vacuous_judge
# =============================================================================

def test_detect_vacuous_no_marker_skips_llm():
    """候補句が無ければ LLM は呼ばれず、実質的な指摘として扱う。"""

    def never_called(_message):
        raise AssertionError("候補句が無いのに判定器が呼ばれた")

    assert detect_vacuous_finding("根拠の明示が無い", never_called) == (False, None)


def test_detect_vacuous_marker_without_judge_keeps_finding():
    """判定器が無い場合は落とさない（marker は記録する）。"""
    vacuous, marker = detect_vacuous_finding("特に問題ありません", None)
    assert vacuous is False
    assert marker in VACUOUS_MARKERS


@pytest.mark.parametrize(
    "verdict,expected_vacuous",
    [(True, True), (False, False), (None, False)],
)
def test_detect_vacuous_second_stage(verdict, expected_vacuous):
    """第2段の判定。判定不能（None）は安全側＝指摘を残す。"""
    vacuous, marker = detect_vacuous_finding("特に問題ありません", lambda _m: verdict)
    assert vacuous is expected_vacuous
    assert marker is not None


@pytest.mark.parametrize(
    "output,expected",
    [("vacuous", True), ("substantive", False), ("???", None)],
)
def test_vacuous_judge_parses_output(fake_llm, output, expected):
    fake_llm(_text_response(output))
    assert create_vacuous_judge(_config_stub())("特に問題ありません") == expected


def test_vacuous_judge_returns_none_on_exception(fake_llm):
    def boom(**_kwargs):
        raise RuntimeError("api down")

    fake_llm(boom)
    assert create_vacuous_judge(_config_stub())("m") is None


# =============================================================================
# ⑤ 指摘ゲート: decide_finding_status
# =============================================================================

@pytest.mark.parametrize(
    "support_rate,expected",
    [
        (1.0, "confirmed"),
        (NOTIFY, "confirmed"),          # しきい値ちょうどは confirmed
        (NOTIFY - 0.01, "review_required"),
        (CONFIRM, "review_required"),   # しきい値ちょうどは review_required
        (CONFIRM - 0.01, "suppressed"),
        (0.0, "suppressed"),
    ],
)
def test_decide_finding_status_by_support_rate(support_rate, expected):
    assert decide_finding_status(support_rate, True, 2, NOTIFY, CONFIRM) == expected


def test_decide_finding_status_unverified_is_review_required():
    """検証不能は指摘を消さず要確認へ（Support の escalate とは倒す先が異なる）。"""
    assert decide_finding_status(1.0, False, 2, NOTIFY, CONFIRM) == "review_required"


def test_decide_finding_status_without_citation_is_review_required():
    """根拠ゼロも消さずに要確認へ。"""
    assert decide_finding_status(1.0, True, 0, NOTIFY, CONFIRM) == "review_required"


# =============================================================================
# ⑥ 救済: should_rescue_finding
# =============================================================================

def test_rescue_only_applies_to_suppressed():
    """suppressed 以外は救済不要。"""
    for status in ("confirmed", "review_required"):
        assert should_rescue_finding(status, False, 2, "根拠の明示が無い") is False


def test_rescue_substantive_finding_with_citation():
    """矛盾なし・根拠あり・実質的な指摘は救済する。"""
    assert should_rescue_finding("suppressed", False, 2, "根拠の明示が無い") is True


@pytest.mark.parametrize(
    "has_contradiction,citation_count,message",
    [
        (True, 2, "根拠の明示が無い"),   # 矛盾あり → 落とす
        (False, 0, "根拠の明示が無い"),  # 根拠ゼロ → 落とす
        (False, 2, ""),                  # 指摘文が空 → 落とす
    ],
)
def test_rescue_rejects_unsafe_cases(has_contradiction, citation_count, message):
    assert should_rescue_finding(
        "suppressed", has_contradiction, citation_count, message
    ) is False


def test_rescue_rejects_vacuous_message():
    """「問題ありません」型は救済しない（第2段で vacuous と判定された場合）。"""
    assert should_rescue_finding(
        "suppressed", False, 2, "特に問題ありません", lambda _m: True
    ) is False


# =============================================================================
# ⑦ 重大度: adjust_severity / apply_forced_high
# =============================================================================

@pytest.mark.parametrize("base", ["high", "medium", "low"])
def test_adjust_severity_keeps_base_when_well_supported(base):
    """支持率が高ければ既定の重大度のまま。"""
    assert adjust_severity(base, 0.95, NOTIFY, CONFIRM) == base


@pytest.mark.parametrize(
    "base,expected",
    [("high", "medium"), ("medium", "low"), ("low", "low")],
)
def test_adjust_severity_downgrades_mid_confidence(base, expected):
    """中信頼は 1 段下げる（low はそれ以上下がらない）。"""
    assert adjust_severity(base, 0.70, NOTIFY, CONFIRM) == expected


@pytest.mark.parametrize("base", ["high", "medium", "low"])
def test_adjust_severity_keeps_base_below_confirm(base):
    """confirm 未満は suppressed / 救済の対象なので、ここでは下げない。"""
    assert adjust_severity(base, 0.10, NOTIFY, CONFIRM) == base


def test_apply_forced_high_noop_when_not_forced():
    assert apply_forced_high("low", "confirmed", False) == ("low", "confirmed")


@pytest.mark.parametrize(
    "severity,status", [("low", "suppressed"), ("medium", "confirmed")]
)
def test_apply_forced_high_upgrades(severity, status):
    """強制時は high かつ review_required（自動確定にはしない）。"""
    assert apply_forced_high(severity, status, True) == ("high", "review_required")

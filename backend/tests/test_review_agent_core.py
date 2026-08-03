# backend/tests/test_review_agent_core.py
"""GRACE-Review コア（backend/app/core/review_agent.py）の配線テスト。

設計: backend/docs/review_agent_spec.md §3。

判定そのものの純関数（`decide_finding_status` / `adjust_severity` 等）は
`test_review_gates.py` が固定している。本モジュールが固定するのは**配線**:

- ① Segment が原文オフセットを保つこと（UI のハイライトが直接ここに依存する）
- ②〜④' のループが「候補ゼロなら LLM を呼ばない」こと（コスト制御の根幹）
- ガード上限（`MAX_SEGMENTS` / `MAX_LLM_CALLS`）で確実に止まること
- KPI カウンタ（`detected_raw` / `rescued` / `forced_high` / `rules_evaluated`）が
  実際の処理回数と一致すること
- ⑦ Action の分岐と、ジョブ基盤への runner 登録

外部依存（LLM・Qdrant・intervention・アクション実行）は conftest の
`review_stub` で差し替える。実 API キー・Qdrant は不要。
"""
from __future__ import annotations

from typing import List

import pytest

from backend.app.core.review_agent import (
    MAX_LLM_CALLS,
    REVIEW_STEP_IDS,
    ReviewParams,
    ReviewResult,
    Segment,
    _build_report,
    _decide_review_action,
    review_result_to_dict,
    run_review_agent_core,
    split_segments,
)
from backend.app.core.review_gates import DetectVerdict
from backend.app.core.rulesets import get_ruleset
from backend.app.core.support_agent import SupportEvent

# =============================================================================
# ① Segment — 決定的分割（LLM 不使用）
# =============================================================================

class TestSplitSegments:
    """分割規則と**原文オフセット**の保存を固定する。"""

    def test_offsets_slice_back_to_the_original_text(self):
        """全セグメントで text[start:end] == segment.text（UI ハイライトの前提）。"""
        document = (
            "当社の商品は業界No.1の品質です。\n"
            "\n"
            "・送料無料\n"
            "・返品可能\n"
            "\n"
            "■ 特定商取引法に基づく表記\n"
            "販売業者: 株式会社サンプル\n"
        )
        segments, truncated = split_segments(document)

        assert not truncated
        assert segments, "分割結果が空になっている"
        for segment in segments:
            assert document[segment.start:segment.end] == segment.text, (
                f"{segment.segment_id} のオフセットが原文とずれている"
            )

    def test_blank_line_splits_paragraphs(self):
        segments, _ = split_segments("第一段落です。\n\n第二段落です。")
        assert [s.text for s in segments] == ["第一段落です。", "第二段落です。"]
        assert all(s.kind == "paragraph" for s in segments)

    def test_list_items_become_one_segment_per_line(self):
        segments, _ = split_segments("・送料無料\n・返品可能\n・即日発送")
        assert [s.text for s in segments] == ["・送料無料", "・返品可能", "・即日発送"]
        assert all(s.kind == "list_item" for s in segments)

    def test_numbered_list_is_recognized(self):
        segments, _ = split_segments("1. 申し込み\n2. 支払い")
        assert [s.kind for s in segments] == ["list_item", "list_item"]

    def test_heading_marker_is_recognized(self):
        segments, _ = split_segments("■ 特商法表記\n販売業者: 株式会社A")
        assert segments[0].kind == "heading"
        assert segments[0].text == "■ 特商法表記"
        # 見出しと同じブロック内の非マーカー行は paragraph
        assert segments[1].kind == "paragraph"

    def test_long_paragraph_is_split_at_sentence_end(self):
        """max_chars 超えの段落は文末（。）で切る。切れ目は文の途中に来ない。"""
        document = "。".join(f"これは{i}番目の文です" for i in range(20)) + "。"
        segments, _ = split_segments(document, max_chars=50)

        assert len(segments) > 1, "長い段落が分割されていない"
        for segment in segments:
            assert document[segment.start:segment.end] == segment.text
        # 連結すると原文に戻る（文字の欠落・重複がない）
        assert "".join(s.text for s in segments) == document

    def test_paragraph_without_sentence_end_is_not_split(self):
        """文末記号が無ければ分割できない（無理に切らない）。"""
        document = "あ" * 500
        segments, _ = split_segments(document, max_chars=50)
        assert len(segments) == 1
        assert segments[0].text == document

    def test_whitespace_only_input_yields_no_segments(self):
        segments, truncated = split_segments("   \n\n \t \n")
        assert segments == []
        assert not truncated

    def test_max_segments_truncates_and_flags(self):
        document = "\n\n".join(f"段落{i}です。" for i in range(30))
        segments, truncated = split_segments(document, max_segments=10)
        assert len(segments) == 10
        assert truncated is True

    def test_segment_ids_are_sequential_and_unique(self):
        segments, _ = split_segments("あ。\n\nい。\n\nう。")
        assert [s.segment_id for s in segments] == ["s001", "s002", "s003"]


# =============================================================================
# パイプライン全体の配線
# =============================================================================

def _collect(events: List[SupportEvent]):
    """emit 用のコレクタを返す。"""
    return lambda event: events.append(event)


def _steps(events, step: str, status: str) -> List[SupportEvent]:
    return [
        e for e in events
        if e.type == "step" and e.step == step and e.status == status
    ]


# 景表法（No.1）と薬機法（治る）の両方に触れる文。どちらも `critical_keywords`
# でもあるため、強制 high の経路も同時に通る。
NG_DOC = "当社の化粧品は業界No.1の実力。使えばシミが治ると評判です。"

# `critical_keywords` を含まない違反文（keihyo-07 有利誤認・severity_default=medium）。
# 強制 high を経由しない経路＝create_ticket 側の分岐を通すために使う。
MEDIUM_DOC = "今だけ期間限定の特別価格です。"


class TestPipelineWiring:

    def test_no_api_key_returns_none_with_error_event(self, monkeypatch, review_stub):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        events: List[SupportEvent] = []
        result = run_review_agent_core("本文", emit=_collect(events))

        assert result is None
        assert [e.type for e in events] == ["error"]
        assert "ANTHROPIC_API_KEY" in events[0].message

    def test_ruleset_scope_is_injected_into_config(self, review_stub):
        """S1: RuleSet の検索スコープ・方針が config へ配線される。"""
        run_review_agent_core(NG_DOC, ruleset="ec_ad")
        rs = get_ruleset("ec_ad")
        # config はリクエスト単位のディープコピーなので、共有シングルトンは汚れない
        assert review_stub.config.qdrant.allowed_collections == []
        # 検索へは RuleSet のコレクションが渡っている
        rag = [kw for name, kw in review_stub.tool_calls if name == "rag_search"]
        assert rag, "rag_search が呼ばれていない"
        assert rag[0]["allowed_collections"] == list(rs.collections)

    def test_config_singleton_is_not_mutated(self, review_stub):
        """config はリクエスト単位のディープコピー。

        `jobs.py` はジョブごとにワーカースレッドを立てるため、シングルトンを
        直接書き換えると Review の検索スコープが並走中の Support のスコープを
        奪う（support_agent.py と同じ理由でディープコピーが必須）。
        """
        before = list(review_stub.config.qdrant.allowed_collections)
        before_addendum = review_stub.config.llm.prompt_addendum

        run_review_agent_core(NG_DOC, ruleset="ec_ad")

        assert review_stub.config.qdrant.allowed_collections == before
        assert review_stub.config.llm.prompt_addendum == before_addendum

    def test_confirm_none_falls_back_to_auto_proceed(self, review_stub):
        """CLI（confirm=None）でも intervention へ callable が渡る。

        None のまま渡すと `InterventionHandler` が承認を解決できず、CONFIRM 待ちで
        止まる。support_agent.py と同じく AUTO_PROCEED へフォールバックする。
        """
        run_review_agent_core(NG_DOC, confirm=None)

        assert callable(review_stub.handler_kwargs["on_confirm"])
        assert callable(review_stub.handler_kwargs["on_escalate"])

    def test_confirm_callback_is_passed_through_when_given(self, review_stub):
        """Web からは InterventionBridge.resolver がそのまま渡る。"""
        def resolver(_req):
            raise AssertionError("呼ばれない想定")

        run_review_agent_core(NG_DOC, confirm=resolver)

        assert review_stub.handler_kwargs["on_confirm"] is resolver
        assert review_stub.handler_kwargs["on_escalate"] is resolver

    def test_unknown_ruleset_skips_and_returns_empty(self, review_stub):
        events: List[SupportEvent] = []
        result = run_review_agent_core(NG_DOC, ruleset="no-such", emit=_collect(events))

        assert result is not None
        assert result.ruleset is None
        assert result.findings == []
        assert _steps(events, "ruleset", "skipped")
        assert not review_stub.detect_calls, "RuleSet 無しで検出器を呼んでいる"

    def test_empty_document_short_circuits_without_llm(self, review_stub):
        result = run_review_agent_core("   ", emit=lambda _e: None)
        assert result is not None
        assert result.segments == []
        assert result.segments_total == 0
        assert not review_stub.detect_calls

    def test_findings_are_produced_for_violating_text(self, review_stub):
        result = run_review_agent_core(NG_DOC)

        assert result is not None
        assert result.findings, "違反文書から指摘が 1 件も出ていない"
        rule_ids = {f.rule_id for f in result.findings}
        # No.1（景表法）と 完治（薬機法）の両方が拾われる
        assert any(r.startswith("keihyo-") for r in rule_ids)
        assert any(r.startswith("yakki-") for r in rule_ids)

    def test_finding_offsets_point_into_the_original_document(self, review_stub):
        """指摘の start/end は**原文**を指す（セグメント相対ではない）。"""
        document = "前置きの段落です。\n\n" + NG_DOC
        result = run_review_agent_core(document)

        assert result.findings
        for finding in result.findings:
            assert document[finding.start:finding.end] == finding.excerpt

    def test_excerpt_not_in_segment_falls_back_to_whole_segment(self, review_stub):
        """LLM が言い換えた excerpt は位置解決できない → セグメント全体を指す。"""
        review_stub.detect = lambda _t, rule, _e: DetectVerdict(
            violates=True, message="言い換えた指摘", suggestion="修正",
            excerpt="原文には存在しない文字列",
        )
        document = "業界No.1の品質です。"
        result = run_review_agent_core(document)

        assert result.findings
        finding = result.findings[0]
        assert finding.excerpt == document
        assert (finding.start, finding.end) == (0, len(document))

    def test_detector_failure_keeps_the_finding(self, review_stub):
        """判定不能（verdict=None）でも指摘を消さない（安全側は「人に見せる」）。"""
        review_stub.detect = lambda _t, _r, _e: None
        result = run_review_agent_core("業界No.1の品質です。")

        assert result.findings, "判定失敗の指摘が捨てられている"
        assert "自動判定に失敗" in result.findings[0].message

    def test_all_steps_are_emitted_once(self, review_stub):
        events: List[SupportEvent] = []
        run_review_agent_core(NG_DOC, use_web=False, do_action=True,
                              emit=_collect(events))

        for step in REVIEW_STEP_IDS:
            terminal = (_steps(events, step, "finished")
                        + _steps(events, step, "skipped"))
            assert len(terminal) == 1, f"ステップ {step} の終了イベントが {len(terminal)} 件"

    def test_result_event_carries_serializable_payload(self, review_stub):
        events: List[SupportEvent] = []
        run_review_agent_core(NG_DOC, emit=_collect(events))

        results = [e for e in events if e.type == "result"]
        assert len(results) == 1
        payload = results[0].data
        assert isinstance(payload, dict)
        assert payload["document_title"] == "無題"
        assert isinstance(payload["findings"], list)
        assert isinstance(payload["summary"], dict)


# =============================================================================
# ② Retrieve — 規程が引けないときのフォールバック
# =============================================================================

class TestRetrieveFallback:

    def test_rule_description_is_used_when_search_returns_nothing(self, review_stub):
        """コレクション未登録でも RuleItem.description を根拠に検査を続ける。"""
        review_stub.rag_output = None       # 検索失敗
        result = run_review_agent_core("業界No.1の品質です。")

        assert review_stub.detect_calls, "検索失敗で検出器が呼ばれていない"
        _text, _rule_id, evidence = review_stub.detect_calls[0]
        assert evidence, "根拠が空のまま検出器へ渡っている"
        assert result.findings
        assert result.findings[0].citations, "条文フォールバックの引用が付いていない"
        assert result.findings[0].citations[0].startswith("[規程]")

    def test_search_hits_become_citations(self, review_stub):
        result = run_review_agent_core("業界No.1の品質です。")
        assert result.findings
        assert result.findings[0].citations == ["[規程] 景品表示法 優良誤認"]


# =============================================================================
# ③ Detect — 二段判定のコスト制御
# =============================================================================

class TestTwoStageCostControl:

    def test_no_keyword_no_always_check_means_no_llm_call(self, monkeypatch,
                                                          review_stub):
        """第1段で候補ゼロなら第2段 LLM は 1 回も呼ばれない。"""
        # always_check（特商法）のあるルールセットだと必ず候補が出るため、
        # always_check を持たないルールだけの RuleSet で検証する。
        from backend.app.core import rulesets

        rs = get_ruleset("ec_ad")
        keyword_only = rulesets.RuleSet(
            id="kw_only", name="キーワードのみ",
            collections=list(rs.collections),
            rules=[r for r in rs.rules if not r.always_check],
            critical_keywords=list(rs.critical_keywords),
        )
        monkeypatch.setattr(
            "backend.app.core.review_agent.get_ruleset",
            lambda _id: keyword_only,
        )
        result = run_review_agent_core("本日は晴天なり。特に何も主張しません。")

        assert review_stub.detect_calls == []
        assert result.rules_evaluated == 0
        assert result.findings == []

    def test_always_check_rules_run_even_without_keywords(self, review_stub):
        """特商法の表記漏れは「無い」ことの検出なのでキーワード無しでも走る。"""
        run_review_agent_core("本日は晴天なり。")
        checked = {rule_id for _t, rule_id, _e in review_stub.detect_calls}
        assert any(r.startswith("tokusho-") for r in checked)

    def test_max_llm_calls_guard_stops_and_flags_truncated(self, monkeypatch,
                                                           review_stub):
        monkeypatch.setattr("backend.app.core.review_agent.MAX_LLM_CALLS", 5)
        document = "\n\n".join(f"業界No.1の商品{i}です。" for i in range(20))
        result = run_review_agent_core(document)

        assert len(review_stub.detect_calls) <= 5
        assert result.truncated is True
        assert result.rules_evaluated <= 5

    def test_rules_evaluated_counts_actual_llm_calls(self, review_stub):
        result = run_review_agent_core(NG_DOC)
        assert result.rules_evaluated == len(review_stub.detect_calls)

    def test_detected_raw_counts_violations_before_suppression(self, review_stub):
        """detected_raw は抑止前の検出数。findings 以上になる。"""
        result = run_review_agent_core(NG_DOC)
        assert result.detected_raw >= len(result.findings)
        assert result.detected_raw == len(result.findings) + result.summary.suppressed


# =============================================================================
# ④ / ④' — Ground と 誤検知抑止・救済
# =============================================================================

class TestGroundAndSuppress:

    def test_high_support_rate_confirms_findings(self, review_stub):
        review_stub.groundedness.support_rate = 0.95
        review_stub.groundedness.verified = True
        # 重大リスク語を含まない文を使う。含めると ⑤ の強制 high が
        # status を review_required へ引き上げるため、④' の判定が見えなくなる。
        result = run_review_agent_core(MEDIUM_DOC)

        assert result.findings
        assert all(f.status == "confirmed" for f in result.findings)
        assert result.summary.confirmed == len(result.findings)

    def test_critical_keyword_overrides_confirmed_to_review_required(self, review_stub):
        """高支持率で confirmed になっても、重大リスク語があれば人が見る。"""
        review_stub.groundedness.support_rate = 0.95
        review_stub.mention = "claim"
        result = run_review_agent_core("業界No.1の品質です。")

        assert result.findings
        assert all(f.status == "review_required" for f in result.findings)
        assert result.summary.confirmed == 0

    def test_low_support_rate_with_contradiction_suppresses(self, review_stub):
        """支持率が低く、かつ規程と矛盾しているものは救済せず落とす。"""
        review_stub.groundedness.support_rate = 0.1
        review_stub.groundedness.verified = True
        review_stub.groundedness.has_contradiction = True
        result = run_review_agent_core("業界No.1の品質です。")

        assert result.findings == []
        assert result.summary.suppressed > 0
        assert result.rescued == 0

    def test_low_support_without_contradiction_is_rescued(self, review_stub):
        """矛盾なし・根拠ありなら保留として残す（指摘を消す方向のミスを避ける）。"""
        review_stub.groundedness.support_rate = 0.1
        review_stub.groundedness.verified = True
        review_stub.groundedness.has_contradiction = False
        result = run_review_agent_core("業界No.1の品質です。")

        assert result.findings, "救済されず指摘が消えている"
        assert all(f.status == "review_required" for f in result.findings)
        assert result.rescued == len(result.findings)

    def test_unverified_becomes_review_required_not_suppressed(self, review_stub):
        """検証不能は Support では escalate だが、Review では人に見せる。"""
        review_stub.groundedness.verified = False
        review_stub.groundedness.support_rate = 0.0
        result = run_review_agent_core("業界No.1の品質です。")

        assert result.findings
        assert all(f.status == "review_required" for f in result.findings)
        assert result.summary.suppressed == 0

    def test_verifier_receives_the_finding_message_as_the_claim(self, review_stub):
        """④ が検証するのは「回答」ではなく「指摘文」。"""
        run_review_agent_core("業界No.1の品質です。")
        assert review_stub.verify_calls
        _query, message, sources = review_stub.verify_calls[0]
        assert "抵触するおそれ" in message
        assert sources, "根拠テキストが検証器へ渡っていない"

    def test_suppressed_findings_carry_a_reason(self, review_stub):
        review_stub.groundedness.support_rate = 0.1
        review_stub.groundedness.has_contradiction = True
        result = run_review_agent_core("業界No.1の品質です。")
        # 抑止されたものは findings に載らないが、件数は summary に残る
        assert result.summary.suppressed > 0
        assert len(result.findings) == 0


# =============================================================================
# ⑤ Severity — 重大リスク語による強制 high
# =============================================================================

class TestSeverity:

    def test_critical_keyword_forces_high_and_review_required(self, review_stub):
        review_stub.mention = "claim"       # 主張として使っている
        result = run_review_agent_core("業界No.1の品質です。")

        assert result.findings
        forced = [f for f in result.findings if f.forced]
        assert forced, "重大リスク語があるのに強制 high が働いていない"
        assert all(f.severity == "high" for f in forced)
        assert all(f.status == "review_required" for f in forced)
        assert result.forced_high == len(forced)

    def test_negated_keyword_does_not_force_high(self, review_stub):
        """『No.1とは言いません』のような否定・引用は強制しない（過検知抑止）。"""
        review_stub.mention = "negation"
        result = run_review_agent_core("業界No.1とは申しません。")

        assert all(not f.forced for f in result.findings)
        assert result.forced_high == 0

    def test_summary_counts_match_findings(self, review_stub):
        result = run_review_agent_core(NG_DOC)
        summary = result.summary
        assert summary.high + summary.medium + summary.low == len(result.findings)
        assert summary.confirmed + summary.review_required == len(result.findings)


# =============================================================================
# ⑥ Web 裏取り
# =============================================================================

class TestWebCrosscheck:

    def test_web_is_skipped_when_disabled(self, review_stub):
        events: List[SupportEvent] = []
        result = run_review_agent_core(NG_DOC, use_web=False, emit=_collect(events))

        assert result.used_web is False
        assert _steps(events, "web", "skipped")
        assert not [n for n, _kw in review_stub.tool_calls if n == "web_search"]

    def test_web_marks_checked_but_creates_no_new_finding(self, review_stub):
        """Web は裏取り専用。指摘は増えない（出典の信頼性を担保できないため）。"""
        review_stub.web_output = [{"title": "改正情報", "snippet": "..."}]
        without_web = run_review_agent_core(NG_DOC, use_web=False)
        with_web = run_review_agent_core(NG_DOC, use_web=True)

        assert len(with_web.findings) == len(without_web.findings)
        assert with_web.used_web is True
        assert any(f.web_checked for f in with_web.findings)


# =============================================================================
# ⑦ Action
# =============================================================================

class TestAction:

    def test_no_findings_means_no_action(self, review_stub):
        events: List[SupportEvent] = []
        result = run_review_agent_core("本日は晴天なり。", do_action=True,
                                       emit=_collect(events))
        # 特商法の always_check が走るが、既定検出器では違反にならない
        if not result.findings:
            assert result.action is None
            assert _steps(events, "action", "skipped")
            assert review_stub.action_calls == []

    def test_do_action_false_skips_action(self, review_stub):
        events: List[SupportEvent] = []
        result = run_review_agent_core(NG_DOC, do_action=False, emit=_collect(events))
        assert result.action is None
        assert result.action_result is None
        assert _steps(events, "action", "skipped")
        assert review_stub.action_calls == []

    def test_high_severity_escalates_without_confirmation(self, review_stub):
        review_stub.mention = "claim"       # 重大リスク語 → 強制 high
        result = run_review_agent_core(NG_DOC, do_action=True)

        assert result.summary.high > 0
        assert result.action is not None
        assert result.action.action_type == "escalate_to_human"
        # 引き継ぎそのものなので承認を待たない
        assert result.action.requires_confirmation is False
        assert review_stub.action_calls[0][0] == "escalate_to_human"

    def test_medium_only_creates_a_ticket_with_confirmation(self, review_stub):
        """high がゼロなら起票（要承認）。承認されれば実行される。"""
        result = _run_with_medium_only(review_stub)

        assert result.summary.high == 0
        assert result.action.action_type == "create_ticket"
        assert result.action.requires_confirmation is True
        assert review_stub.action_calls[0][0] == "create_ticket"

    def test_cancelled_confirmation_reports_and_does_not_execute(self, review_stub):
        """CONFIRM を却下したら実行しない（create_ticket は要承認）。"""
        review_stub.confirm_continues = False
        result = _run_with_medium_only(review_stub)

        assert result.action is not None
        assert result.action.action_type == "create_ticket"
        assert result.action.requires_confirmation is True
        assert "キャンセル" in result.action_result
        assert review_stub.action_calls == []

    def test_report_is_attached_to_action_args(self, review_stub):
        result = run_review_agent_core(NG_DOC, do_action=True)
        assert result.action is not None
        report = result.action.args["report"]
        assert result.document_title in report
        for finding in result.findings:
            assert finding.rule_title in report


def _run_with_medium_only(review_stub) -> ReviewResult:
    """重大リスク語を含まない違反文書でレビューする（high が出ない経路）。"""
    return run_review_agent_core(MEDIUM_DOC, do_action=True)


class TestDecideReviewAction:
    """`_decide_review_action` の分岐（純関数として単体で固定する）。"""

    def test_no_findings_returns_none(self):
        assert _decide_review_action(ReviewResult(document_title="t")) is None

    def test_high_returns_escalate_without_confirmation(self):
        result = ReviewResult(document_title="t")
        result.findings = [_finding(severity="high")]
        result.summary.high = 1
        action = _decide_review_action(result)
        assert action.action_type == "escalate_to_human"
        assert action.requires_confirmation is False

    def test_no_high_returns_ticket_with_confirmation(self):
        result = ReviewResult(document_title="t")
        result.findings = [_finding(severity="medium")]
        action = _decide_review_action(result)
        assert action.action_type == "create_ticket"
        assert action.requires_confirmation is True


def _finding(**overrides):
    from backend.app.core.review_agent import ReviewFinding

    defaults = dict(
        finding_id="f001", segment_id="s001", excerpt="No.1", start=0, end=4,
        rule_id="keihyo-01", rule_title="優良誤認", category="表示", law="景品表示法",
        article="第5条第1号", message="指摘", suggestion="修正",
    )
    defaults.update(overrides)
    return ReviewFinding(**defaults)


# =============================================================================
# シリアライズ / ジョブ基盤との接続
# =============================================================================

def test_review_result_to_dict_is_json_serializable(review_stub):
    import json

    result = run_review_agent_core(NG_DOC)
    payload = review_result_to_dict(result)
    json.dumps(payload, ensure_ascii=False)   # 例外が出なければよい

    assert set(payload) >= {
        "document_title", "ruleset", "segments", "findings", "summary",
        "segments_total", "rules_evaluated", "detected_raw", "rescued",
        "forced_high", "truncated",
    }


def test_review_params_is_registered_with_the_job_manager(review_stub):
    """`ReviewParams` を渡すだけで review runner が解決される（登録漏れ防止）。"""
    import time

    from backend.app.core.jobs import JobManager

    manager = JobManager()
    job = manager.start(ReviewParams(document=NG_DOC, document_title="LP案"))

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not job.done:
        time.sleep(0.01)

    assert job.done, f"ジョブが終了しない: status={job.status}"
    assert job.kind == "review"
    assert job.status == "completed", [e for e in job.events if e["type"] == "error"]
    assert job.result["document_title"] == "LP案"


def test_build_report_lists_every_finding():
    result = ReviewResult(document_title="LP案", ruleset="ec_ad")
    result.findings = [
        _finding(finding_id="f001", rule_title="優良誤認"),
        _finding(finding_id="f002", rule_title="打消し表示", rule_id="keihyo-02"),
    ]
    report = _build_report(result)
    assert "優良誤認" in report
    assert "打消し表示" in report
    assert "LP案" in report


@pytest.mark.parametrize("step", REVIEW_STEP_IDS)
def test_step_ids_are_unique(step):
    assert REVIEW_STEP_IDS.count(step) == 1


def test_segment_dataclass_defaults():
    segment = Segment(segment_id="s001", text="本文", start=0, end=2)
    assert segment.kind == "paragraph"


def test_max_llm_calls_is_a_positive_guard():
    assert MAX_LLM_CALLS > 0

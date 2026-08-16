# backend/tests/test_web_only_needs_a_verdict.py
"""**出典が Web のみ」だけを理由にエスカレしない**ことを固定するテスト。

## 背景（実測）

`force_judge=web_only` は「候補句が無い回答も**第 2 段の判定に掛ける**」ための
トリガとして入れたもので、判定結果ではない。ところが判定が得られなかったとき
（`judge(...)` が `None`）に escalate へ倒していたため、実質は

    出典が Web のみ ⇒ 内容によらず常に有人対応

という無条件ルールになっていた。

そして本リポジトリの既定は `judges.enabled=false` である
（`config/grace_config.yml`。ローカル LLM では 1 判定に 90〜250 秒かかり、実測で
約 13 分を無駄にしたため意図的に切ってある）。つまり判定は**常に**得られない。

実測 2026-08-17 01:22 の実行はこの経路で escalate していた:

    [no-info] 実質回答判定（gemma4:e4b）: 判定なし（判定器が無効…のため実行せず）
    [gate] 情報なし回答を検知（出典が Web のみ）→ 有人対応へエスカレーション

ここで固定すること:
  1. 候補句なし × 判定なし → **escalate しない**（Web のみは理由にならない）
  2. 候補句あり × 判定なし → 従来どおり escalate（第 1 段が既に疑っている）
  3. 判定が得られたときの挙動は不変（answered→維持 / no_info→escalate）
  4. ログが「判定が得られなかった」を「answered」と書かないこと
"""
from __future__ import annotations

from types import SimpleNamespace

from backend.app.core.gates import _detect_no_info_answer, create_no_info_judge
from backend.app.core.support_agent import (
    AUTO_PROCEED,
    SupportEvent,
    run_support_agent_core,
)

QUERY = "明日の東京の天気は？"
# 実測の回答。実質的な案内を含むが「見当たりません」も含む
ANSWER_WITH_MARKER = (
    "Web 検索結果によると、東京の天気予報を確認できます。"
    "その他の情報源からは、明日に特化した詳細な天気予報の記述は見当たりませんでした。"
)
ANSWER_WITHOUT_MARKER = (
    "Web 検索結果によると、東京の天気予報として今日・明日の天気を確認できます。"
)

NO_VERDICT = lambda _q, _a: None          # noqa: E731  判定器が無効／失敗
ANSWERED = lambda _q, _a: False           # noqa: E731
NO_INFO = lambda _q, _a: True             # noqa: E731


def collect(events):
    return lambda e: events.append(e)


# =============================================================================
# ① 判定が無いとき、Web のみは escalate の理由にならない
# =============================================================================

class TestWebOnlyAloneDoesNotEscalate:

    def test_no_marker_and_no_verdict_keeps_the_answer(self):
        no_info, marker = _detect_no_info_answer(
            QUERY, ANSWER_WITHOUT_MARKER, NO_VERDICT, force_judge=True,
        )

        assert no_info is False, (
            "判定を得ていないのに『出典が Web のみ』だけで有人対応へ回している"
        )
        assert marker is None

    def test_disabled_judge_keeps_the_answer(self):
        """judges.enabled=false（本リポジトリの既定）でも同じであること。"""
        judge = create_no_info_judge(
            SimpleNamespace(judges=SimpleNamespace(enabled=False))
        )

        no_info, _marker = _detect_no_info_answer(
            QUERY, ANSWER_WITHOUT_MARKER, judge, force_judge=True,
        )

        assert no_info is False


# =============================================================================
# ② 候補句がある場合は従来どおり（安全側を緩めない）
# =============================================================================

class TestMarkerPathIsUnchanged:

    def test_marker_and_no_verdict_still_escalates(self):
        """第 1 段が「情報なし回答らしい」と言っているので安全側を維持する。"""
        no_info, marker = _detect_no_info_answer(
            QUERY, ANSWER_WITH_MARKER, NO_VERDICT, force_judge=True,
        )

        assert no_info is True
        assert marker == "見当たりません"

    def test_marker_without_force_judge_still_escalates(self):
        no_info, marker = _detect_no_info_answer(
            QUERY, ANSWER_WITH_MARKER, NO_VERDICT, force_judge=False,
        )

        assert (no_info, marker) == (True, "見当たりません")


# =============================================================================
# ③ 判定が得られたときの挙動は変えていない
# =============================================================================

class TestVerdictStillWins:

    def test_answered_keeps_the_answer(self):
        assert _detect_no_info_answer(
            QUERY, ANSWER_WITH_MARKER, ANSWERED, force_judge=True,
        )[0] is False

    def test_no_info_escalates_even_without_marker(self):
        """判定器が「情報なし」と言えば、候補句が無くても escalate。"""
        no_info, marker = _detect_no_info_answer(
            QUERY, ANSWER_WITHOUT_MARKER, NO_INFO, force_judge=True,
        )

        assert (no_info, marker) == (True, None)

    def test_no_judge_object_still_passes_through(self):
        assert _detect_no_info_answer(
            QUERY, ANSWER_WITH_MARKER, None, force_judge=True,
        ) == (False, "見当たりません")

    def test_no_marker_no_force_judge_skips_the_second_stage(self):
        calls = []

        no_info, marker = _detect_no_info_answer(
            QUERY, ANSWER_WITHOUT_MARKER,
            lambda q, a: calls.append((q, a)), force_judge=False,
        )

        assert (no_info, marker) == (False, None)
        assert calls == [], "判定に掛ける条件でないのに LLM を呼んでいる"


# =============================================================================
# ④ ログが「判定なし」を「answered」と書かない
# =============================================================================

class TestGateLogIsHonest:

    def _gate_logs(self, events):
        return [e.message for e in events
                if e.type == "log" and e.step == "no_info" and "[gate]" in e.message]

    def _no_info_event(self, events):
        return [e for e in events if e.type == "step"
                and e.step == "no_info" and e.status == "finished"][0]

    def test_missing_verdict_is_not_reported_as_answered(self, pipeline_stub):
        """判定器が無効（既定）＋出典が Web のみ＋候補句なし。"""
        pipeline_stub.answer = ANSWER_WITHOUT_MARKER
        pipeline_stub.sources = ["https://weather.yahoo.co.jp/weather/jp/13/"]
        pipeline_stub.no_info_verdict = None      # 判定なし
        events: list[SupportEvent] = []
        result = run_support_agent_core(
            QUERY, emit=collect(events), confirm=lambda _r: AUTO_PROCEED,
        )

        assert result.decision == "answer"
        assert result.no_info_detected is False
        [entry] = self._gate_logs(events)
        assert "判定が得られなかった" in entry
        assert "answered" not in entry, "判定していないのに answered と書いている"
        assert self._no_info_event(events).data["verdict_missing"] is True

    def test_answered_verdict_is_still_reported_as_answered(self, pipeline_stub):
        pipeline_stub.answer = ANSWER_WITHOUT_MARKER
        pipeline_stub.sources = ["https://weather.yahoo.co.jp/weather/jp/13/"]
        pipeline_stub.no_info_verdict = False     # answered
        events: list[SupportEvent] = []
        run_support_agent_core(
            QUERY, emit=collect(events), confirm=lambda _r: AUTO_PROCEED,
        )

        [entry] = self._gate_logs(events)
        assert "実質回答（answered）" in entry
        assert self._no_info_event(events).data["verdict_missing"] is False

    def test_marker_path_still_escalates_end_to_end(self, pipeline_stub):
        """実測 02:12 の再現: 候補句あり＋判定なし → 従来どおり escalate。"""
        pipeline_stub.answer = ANSWER_WITH_MARKER
        pipeline_stub.sources = ["https://weather.yahoo.co.jp/weather/jp/13/"]
        pipeline_stub.no_info_verdict = None
        events: list[SupportEvent] = []
        result = run_support_agent_core(
            QUERY, emit=collect(events), confirm=lambda _r: AUTO_PROCEED,
        )

        assert result.decision == "escalate"
        assert result.no_info_detected is True
        assert any("候補句 '見当たりません'" in m for m in self._gate_logs(events))

# backend/tests/test_no_info_judge_failure_reason.py
"""④' 実質回答判定が**なぜ**判定できなかったのかを追えることを固定するテスト。

## 背景（実測）

「明日の東京の天気は？」の実行ログ（UI）には、こうとしか出ていなかった:

    [no-info] 実質回答判定（gemma4:e4b）: 判定失敗
    [gate] 情報なし回答を検知（出典が Web のみ）→ 有人対応へエスカレーション

理由は `create_no_info_judge` が `sys.stderr` へ print するだけだったため、
**emit 経由の実行ログ（UI・SSE）には結果しか残らなかった。**

これが問題になるのは ④' の設計上の性質による:

- 出典が Web のみ（社内根拠ゼロ）の回答は `force_judge=True` で判定が必須
- 判定できない（None）ときは安全側に倒して escalate

つまり**判定器が失敗し続けると、Web フォールバックで得た回答は内容によらず
全件が有人対応へ回る**。「安全側に倒れた」のか「判定器が壊れている」のかを
区別するには失敗理由が要るが、それが実行記録に無かった。

さらに `judges.enabled=false`（設定で切っている）ときも同じ None を返すため、
**一度も実行していないのに「判定失敗」と表示**されていた。

ここで固定すること:
  1. 判定できなかった理由が `on_failure(kind, detail)` で呼び出し側へ渡ること
  2. 「無効」「想定外の出力」「例外」が種別として区別できること
  3. ③' のステップログ・イベントに理由が載ること
  4. 無効時は「判定失敗」ではなく「判定なし」と表示されること
  5. **判定そのものの結果は変えていないこと**（安全側 escalate は維持）

⚠️ LLM には接続しない（`client.models.generate_content` を差し替える）。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.app.core.gates import (
    JUDGE_DISABLED,
    JUDGE_EXCEPTION,
    JUDGE_UNEXPECTED_OUTPUT,
    _detect_no_info_answer,
    create_no_info_judge,
)
from backend.app.core.support_agent import (
    AUTO_PROCEED,
    SupportEvent,
    run_support_agent_core,
)

QUERY = "明日の東京の天気は？"
WEB_ANSWER = "Web 検索結果によると、東京の天気予報を見られると記載されています。"


def collect(events):
    return lambda e: events.append(e)


# =============================================================================
# ① 理由が呼び出し側へ渡る
# =============================================================================

class TestFailureReasonIsReported:

    def test_unexpected_output_reports_the_text(self):
        """書式を守らない応答は、本文つきで報告されること。"""
        failures = []
        judge = _judge(text="うーん、判断が難しいですね", on_failure=_record(failures))

        assert judge(QUERY, WEB_ANSWER) is None
        [(kind, detail)] = failures
        assert kind == JUDGE_UNEXPECTED_OUTPUT
        assert "判断が難しい" in detail, (
            "何が返ってきたのか分からないと、書式不履行か空応答か切り分けられない"
        )

    def test_empty_output_is_distinguishable_from_other_text(self):
        """空応答（ローカル LLM の打ち切り）が「空」と分かること。"""
        failures = []
        judge = _judge(text="", on_failure=_record(failures))

        judge(QUERY, WEB_ANSWER)

        [(kind, detail)] = failures
        assert kind == JUDGE_UNEXPECTED_OUTPUT
        assert "''" in detail

    def test_exception_reports_type_and_message(self):
        failures = []
        judge = _judge(
            exc=TimeoutError("Request timed out after 180s"),
            on_failure=_record(failures),
        )

        assert judge(QUERY, WEB_ANSWER) is None
        [(kind, detail)] = failures
        assert kind == JUDGE_EXCEPTION
        assert "TimeoutError" in detail
        assert "180s" in detail, "型名だけでは接続断とタイムアウトを取り違える"

    def test_disabled_judge_reports_that_it_never_ran(self):
        failures = []
        config = SimpleNamespace(judges=SimpleNamespace(enabled=False))
        judge = create_no_info_judge(config, on_failure=_record(failures))

        assert judge(QUERY, WEB_ANSWER) is None
        [(kind, detail)] = failures
        assert kind == JUDGE_DISABLED
        assert "judges.enabled=false" in detail

    def test_long_reason_is_truncated(self):
        failures = []
        judge = _judge(text="あ" * 500, on_failure=_record(failures))

        judge(QUERY, WEB_ANSWER)

        [(_kind, detail)] = failures
        assert len(detail) < 200
        assert "…" in detail

    def test_success_reports_nothing(self):
        failures = []
        judge = _judge(text="answered", on_failure=_record(failures))

        assert judge(QUERY, WEB_ANSWER) is False
        assert failures == []


# =============================================================================
# ② 判定そのものは変えていない
# =============================================================================

class TestVerdictIsUnchanged:
    """観測を足しただけで、安全側の判断は従来どおりであること。"""

    def test_answered_and_no_info_still_parse(self):
        assert _judge(text="answered")(QUERY, WEB_ANSWER) is False
        assert _judge(text="no_info")(QUERY, WEB_ANSWER) is True
        assert _judge(text="NO-INFO")(QUERY, WEB_ANSWER) is True

    def test_failure_still_escalates_on_web_only(self):
        """出典が Web のみなら、判定失敗は従来どおり escalate（安全側）。"""
        judge = _judge(exc=TimeoutError("timeout"))

        no_info, marker = _detect_no_info_answer(
            QUERY, WEB_ANSWER, judge, force_judge=True,
        )

        assert no_info is True
        assert marker is None

    def test_disabled_judge_still_escalates_on_web_only(self):
        config = SimpleNamespace(judges=SimpleNamespace(enabled=False))
        judge = create_no_info_judge(config)

        no_info, _marker = _detect_no_info_answer(
            QUERY, WEB_ANSWER, judge, force_judge=True,
        )

        assert no_info is True

    def test_no_marker_and_no_force_still_skips_the_judge(self):
        """候補句なし・force なしなら LLM を呼ばない（コストの前提を守る）。"""
        calls = []
        judge = lambda q, a: calls.append((q, a))  # noqa: E731

        no_info, marker = _detect_no_info_answer(QUERY, WEB_ANSWER, judge)

        assert (no_info, marker) == (False, None)
        assert calls == []


# =============================================================================
# ③ 実行記録（ステップログ・イベント）から読める
# =============================================================================

class TestReasonReachesTheRunLog:

    def _no_info_logs(self, events):
        # step="no_info" には ④' ゲート自身のログも流れるので、判定器の行だけ拾う
        return [e for e in events
                if e.type == "log" and e.step == "no_info"
                and "実質回答判定" in e.message]

    def test_failure_detail_appears_in_the_step_log(self, pipeline_stub, monkeypatch):
        """実測で欠けていた情報が、UI の実行ログに出ること。"""
        monkeypatch.setattr(
            "backend.app.core.support_agent.create_no_info_judge",
            _factory(JUDGE_EXCEPTION, "実質回答判定に失敗（TimeoutError: timed out）"),
        )
        pipeline_stub.sources = ["https://weather.yahoo.co.jp/weather/jp/13/"]
        events: list[SupportEvent] = []
        run_support_agent_core(
            QUERY, emit=collect(events), confirm=lambda _r: AUTO_PROCEED,
        )

        [entry] = self._no_info_logs(events)
        assert "判定失敗" in entry.message
        assert "TimeoutError" in entry.message
        assert entry.data["failure_kind"] == JUDGE_EXCEPTION

    def test_disabled_is_labelled_as_not_judged(self, pipeline_stub, monkeypatch):
        """**無効を「失敗」と書かない。** 実行していないものは失敗していない。"""
        monkeypatch.setattr(
            "backend.app.core.support_agent.create_no_info_judge",
            _factory(JUDGE_DISABLED, "判定器が無効（judges.enabled=false）のため実行せず"),
        )
        pipeline_stub.sources = ["https://weather.yahoo.co.jp/weather/jp/13/"]
        events: list[SupportEvent] = []
        run_support_agent_core(
            QUERY, emit=collect(events), confirm=lambda _r: AUTO_PROCEED,
        )

        [entry] = self._no_info_logs(events)
        assert "判定なし" in entry.message
        assert "判定失敗" not in entry.message
        assert entry.data["failure_kind"] == JUDGE_DISABLED

    def test_successful_judgement_carries_no_reason(self, pipeline_stub):
        """判定できたときに理由欄が付かないこと（ログを汚さない）。"""
        # 出典を Web のみにして ④' の判定を必須にする（force_judge=True）
        pipeline_stub.sources = ["https://weather.yahoo.co.jp/weather/jp/13/"]
        events: list[SupportEvent] = []
        run_support_agent_core(
            QUERY, emit=collect(events), confirm=lambda _r: AUTO_PROCEED,
        )

        entries = self._no_info_logs(events)
        assert entries, "④' が走っていない"
        for entry in entries:
            assert entry.data["failure_kind"] is None
            assert entry.data["failure_detail"] is None
            # 判定結果で終わる＝理由の括弧が後ろに付いていない
            # （文中の「（モデル名）」は常に入るので、末尾で判定する）
            assert entry.message.rstrip().endswith("answered")


# =============================================================================
# ヘルパ
# =============================================================================

def _record(sink):
    return lambda kind, detail: sink.append((kind, detail))


def _judge(*, text=None, exc=None, on_failure=None):
    """LLM に触れない実質回答判定器を作る。"""
    client = MagicMock()
    if exc is not None:
        client.models.generate_content.side_effect = exc
    else:
        response = MagicMock()
        response.text = text
        client.models.generate_content.return_value = response

    config = SimpleNamespace(judges=SimpleNamespace(enabled=True))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("grace.llm_compat.create_chat_client", lambda _c: client)
        return create_no_info_judge(config, on_failure=on_failure)


def _factory(kind, detail):
    """`create_no_info_judge` の差し替え。必ず判定不能（None）を返す。"""
    def factory(_config, on_failure=None):
        def judge(_q, _a):
            if on_failure is not None:
                on_failure(kind, detail)
            return None
        return judge
    return factory

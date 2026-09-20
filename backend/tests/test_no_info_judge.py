# backend/tests/test_no_info_judge.py
"""④' 実質回答判定の**理由・エスカレ条件・使用モデル**を固定するテスト。

## 背景（grace_v2_local で実測、本リポジトリにも同じコードが残っていた）

### ① なぜ判定できなかったのかが実行記録に残らなかった

判定器は理由を `sys.stderr` へ print するだけで、`emit` 経由の実行ログ
（UI・SSE）には `判定失敗` という結果しか出なかった。出典が Web のみの回答は
`force_judge=True` で判定が必須になるため、判定器が失敗し続けているのか
たまたま 1 回落ちたのかを区別する必要がある。

### ② 「出典が Web のみ」だけでエスカレしていた

`force_judge=web_only` は「候補句が無い回答も**第 2 段の判定に掛ける**」ための
トリガであって、判定結果ではない。ところが判定が得られなかったとき
（`judge` が `None`）に escalate へ倒していたため、実質は

    出典が Web のみ ⇒ 内容によらず常に有人対応

という無条件ルールになっていた。判定は通常得られるので普段は表に出ないが、
**API 障害時に「Web 由来の回答が全件エスカレ」する**。

### ③ 判定系のモデル名が config を見ていなかった

`INTENT_MODEL` は `verticals.py` のリテラル定数で `grace_config.yml` を見ない。
現時点では yml の `light_model` とたまたま同じ値だが、**yml を書き換えても
判定系だけが取り残される**（同じ値の二重管理）。

ここで固定すること:
  1. 判定できなかった理由が `on_failure(kind, detail)` で呼び出し側へ渡ること
  2. 候補句なし × 判定なしでは escalate しないこと（候補句ありは据え置き）
  3. ログが「判定が得られなかった」を「answered」と書かないこと
  4. 判定系のモデルが config（yml）から解決されること

⚠️ LLM には接続しない（`client.models.generate_content` を差し替える）。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.app.core.gates import (
    JUDGE_EXCEPTION,
    JUDGE_UNEXPECTED_OUTPUT,
    _detect_no_info_answer,
    create_intent_classifier,
    create_no_info_judge,
    judge_model,
)
from backend.app.core.review_gates import (
    create_mention_classifier,
    create_vacuous_judge,
)
from backend.app.core.support_agent import (
    AUTO_PROCEED,
    SupportEvent,
    run_support_agent_core,
)
from backend.app.core.verticals import INTENT_MODEL

QUERY = "明日の東京の天気は？"
WEB_ANSWER = "Web 検索結果によると、東京の天気予報を見られると記載されています。"
ANSWER_WITH_MARKER = WEB_ANSWER + "その他の詳細は見当たりませんでした。"


def collect(events):
    return lambda e: events.append(e)


def _record(sink):
    return lambda kind, detail: sink.append((kind, detail))


# ⚠️ 本リポジトリの LLM はローカル LLM（Ollama）。判定系の既定 `INTENT_MODEL` は
#    `config.get_default_ollama_model()` を指す。ここで使うモデル名は
#    「config の値が INTENT_MODEL より優先されるか」を見るための材料であり、
#    既定と**別の名前**であればよい。
OTHER_MODEL = "gemma4:26b-mlx"


def _config(light_model=INTENT_MODEL):
    return SimpleNamespace(llm=SimpleNamespace(light_model=light_model))


def _judge(text=None, exc=None, on_failure=None):
    """LLM 応答を固定した判定器を作る。"""
    client = MagicMock()
    if exc is not None:
        client.models.generate_content.side_effect = exc
    else:
        client.models.generate_content.return_value = SimpleNamespace(text=text)
    import grace.llm_compat as compat
    original = compat.create_chat_client
    compat.create_chat_client = lambda _c: client
    try:
        return create_no_info_judge(_config(), on_failure=on_failure)
    finally:
        compat.create_chat_client = original


# =============================================================================
# ① 判定できなかった理由が渡る
# =============================================================================

class TestFailureReasonIsReported:

    def test_unexpected_output_reports_the_text(self):
        failures = []
        judge = _judge(text="うーん、判断が難しいですね", on_failure=_record(failures))

        assert judge(QUERY, WEB_ANSWER) is None
        [(kind, detail)] = failures
        assert kind == JUDGE_UNEXPECTED_OUTPUT
        assert "判断が難しい" in detail

    def test_empty_output_is_distinguishable(self):
        failures = []
        judge = _judge(text="", on_failure=_record(failures))

        judge(QUERY, WEB_ANSWER)

        [(kind, detail)] = failures
        assert kind == JUDGE_UNEXPECTED_OUTPUT
        assert "''" in detail

    def test_exception_reports_type_and_message(self):
        failures = []
        judge = _judge(exc=TimeoutError("Request timed out after 180s"),
                       on_failure=_record(failures))

        assert judge(QUERY, WEB_ANSWER) is None
        [(kind, detail)] = failures
        assert kind == JUDGE_EXCEPTION
        assert "TimeoutError" in detail
        assert "180s" in detail, "型名だけでは接続断とタイムアウトを取り違える"

    def test_success_reports_nothing(self):
        failures = []
        judge = _judge(text="answered", on_failure=_record(failures))

        assert judge(QUERY, WEB_ANSWER) is False
        assert failures == []


# =============================================================================
# ② 「出典が Web のみ」だけではエスカレしない
# =============================================================================

class TestWebOnlyAloneDoesNotEscalate:

    NO_VERDICT = staticmethod(lambda _q, _a: None)
    ANSWERED = staticmethod(lambda _q, _a: False)
    NO_INFO = staticmethod(lambda _q, _a: True)

    def test_no_marker_and_no_verdict_keeps_the_answer(self):
        no_info, marker = _detect_no_info_answer(
            QUERY, WEB_ANSWER, self.NO_VERDICT, force_judge=True,
        )

        assert no_info is False, (
            "判定を得ていないのに『出典が Web のみ』だけで有人対応へ回している"
        )
        assert marker is None

    def test_marker_and_no_verdict_still_escalates(self):
        """第 1 段が「情報なし回答らしい」と言っているので安全側を維持する。"""
        no_info, marker = _detect_no_info_answer(
            QUERY, ANSWER_WITH_MARKER, self.NO_VERDICT, force_judge=True,
        )

        assert (no_info, marker) == (True, "見当たりません")

    def test_answered_keeps_the_answer(self):
        assert _detect_no_info_answer(
            QUERY, ANSWER_WITH_MARKER, self.ANSWERED, force_judge=True,
        )[0] is False

    def test_no_info_escalates_even_without_marker(self):
        """判定器が「情報なし」と言えば、候補句が無くても escalate。"""
        assert _detect_no_info_answer(
            QUERY, WEB_ANSWER, self.NO_INFO, force_judge=True,
        ) == (True, None)

    def test_no_marker_no_force_judge_skips_the_second_stage(self):
        calls = []

        result = _detect_no_info_answer(
            QUERY, WEB_ANSWER, lambda q, a: calls.append((q, a)), force_judge=False,
        )

        assert result == (False, None)
        assert calls == [], "判定に掛ける条件でないのに LLM を呼んでいる"


# =============================================================================
# ③ ログが誠実であること
# =============================================================================

class TestGateLogIsHonest:

    def _gate_logs(self, events):
        return [e.message for e in events
                if e.type == "log" and e.step == "no_info" and "[gate]" in e.message]

    def _no_info_event(self, events):
        return [e for e in events if e.type == "step"
                and e.step == "no_info" and e.status == "finished"][0]

    def test_missing_verdict_is_not_reported_as_answered(self, pipeline_stub):
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
        pipeline_stub.sources = ["https://weather.yahoo.co.jp/weather/jp/13/"]
        pipeline_stub.no_info_verdict = False
        events: list[SupportEvent] = []
        run_support_agent_core(
            QUERY, emit=collect(events), confirm=lambda _r: AUTO_PROCEED,
        )

        [entry] = self._gate_logs(events)
        assert "実質回答（answered）" in entry
        assert self._no_info_event(events).data["verdict_missing"] is False


# =============================================================================
# ④ 判定系のモデルは config から解決する
# =============================================================================

class TestJudgeModelResolution:

    def test_config_wins(self):
        assert judge_model(_config(OTHER_MODEL)) == OTHER_MODEL

    def test_falls_back_when_config_has_no_llm(self):
        assert judge_model(SimpleNamespace()) == INTENT_MODEL

    def test_falls_back_when_light_model_is_empty(self):
        assert judge_model(_config("")) == INTENT_MODEL

    @pytest.mark.parametrize(
        "factory, call",
        [
            (create_intent_classifier, lambda j: j("返品したい")),
            (create_no_info_judge, lambda j: j("質問", "回答")),
            (create_mention_classifier, lambda j: j("業界No.1です")),
            (create_vacuous_judge, lambda j: j("特に問題ありません")),
        ],
    )
    def test_all_judges_use_the_config_model(self, factory, call, monkeypatch):
        client = MagicMock()
        client.models.generate_content.return_value = SimpleNamespace(text="answered")
        monkeypatch.setattr("grace.llm_compat.create_chat_client", lambda _c: client)

        call(factory(_config(OTHER_MODEL)))

        [kwargs] = [c.kwargs for c in client.models.generate_content.call_args_list]
        assert kwargs["model"] == OTHER_MODEL, (
            f"{factory.__name__} が config のモデルを使っていない"
            "（INTENT_MODEL に固定されている）"
        )

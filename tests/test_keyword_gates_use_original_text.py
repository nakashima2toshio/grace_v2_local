# tests/test_keyword_gates_use_original_text.py
"""④ 強制エスカレ（A5）と ⑥ アクションの決定（A6）は、利用者が書いた**原文**で語を照合する。

## なぜ必要か

0-(A) の質問の整理（`question_refiner.refine_question`）は、回答用の質問文（`Topic.answer_question`）を
LLM が書き直す。指示語を解決するための書き直しだが、言い換えで原文の語が消えることがある
（「落ちています」→「利用できない」、「返品」→「送り返す」）。

`_should_force_escalate` / `_decide_action` は `escalate_keywords` / `action_map` の語の部分一致で候補を
決めるので、書き直した文で照合すると、原文にエスカレ語・アクション語があっても候補にならない
（強制エスカレしない・起票しない）。照合は原文で行う。

LLM・planner・executor はスタブ（`tests/conftest.py`）。
"""
from __future__ import annotations

from backend.app.core.question_refiner import Aspect, Sentence, Topic
from backend.app.core.support_agent import AUTO_PROCEED, run_support_agent_core


def _rewritten(original: str, answer: str) -> Topic:
    """原文を 1 話題・1 観点にし、回答用の質問文だけを言い換えた整理結果。"""
    return Topic(
        answer_question=answer,
        sentences=[Sentence(text=original, aspect="本題", relation="主")],
        aspects=[Aspect(name="本題", search_query=answer, web_query=answer, wants_latest_web=False)],
        in_scope=True,
    )


class TestForcedEscalateUsesOriginalText:
    def test_言い換えでエスカレ語が消えても原文で強制エスカレする(self, pipeline_stub):
        original = "サービスが落ちています。復旧の見込みを教えて"
        pipeline_stub.topics = [_rewritten(original, "サービスを利用できない状態からの復旧見込みを教えてください。")]
        pipeline_stub.intent = "incident"

        result = run_support_agent_core(original, confirm=lambda _r: AUTO_PROCEED,
                                        vertical="saas", use_web=False)

        assert result.forced_escalate is True
        assert result.decision == "escalate"

    def test_原文にエスカレ語が無ければ強制エスカレしない(self, pipeline_stub):
        # 言い換えた文にだけエスカレ語（「課金」）が入っても、原文に無ければ発動しない
        original = "宛名を変更したい"
        pipeline_stub.topics = [_rewritten(original, "課金の宛名を変更する方法を教えてください。")]
        pipeline_stub.intent = "request"

        result = run_support_agent_core(original, confirm=lambda _r: AUTO_PROCEED,
                                        vertical="saas", use_web=False)

        assert result.forced_escalate is False


class TestActionUsesOriginalText:
    def test_言い換えでアクション語が消えても原文で起票する(self, pipeline_stub):
        original = "返品したいです"
        pipeline_stub.topics = [_rewritten(original, "購入した商品を送り返す手続きを教えてください。")]
        pipeline_stub.intent = "request"

        result = run_support_agent_core(original, confirm=lambda _r: AUTO_PROCEED,
                                        vertical="ec", use_web=False)

        assert result.action is not None
        assert result.action.action_type == "create_ticket"
        assert result.action.args.get("matched") == "返品"

# backend/tests/test_executor_reasoning_and_memory.py
"""executor の 2 つの副作用を守るテスト（実測ログから起こした回帰防止）。

1. **ask_user の結果を reasoning の参照情報に混ぜない**
   検索が空振りしたときに動的挿入される ask_user は「十分な情報が見つかりません
   でした」という**内部の問いかけ**を output に持つ。これを reasoning へ渡すと、
   回答生成 LLM が内部の泣き言を参照情報として読み、回答へ引き写しうる。

2. **補助ステップの空振りをコレクションの失敗として記録しない**
   RAG スコアが一次閾値に届かないと web_search / ask_user が動的挿入される。
   Web が落ちているだけで実行メモリに「失敗」が刻まれ、以降の planner の
   コレクション優先順位を毒していた（実測 2026-08-29・クラウド版:
   支持率 1.00・decision=answer なのに gov_faq_anthropic が success=False）。
"""
from __future__ import annotations

from types import SimpleNamespace

from grace.executor import Executor
from grace.schemas import ExecutionPlan, PlanStep, StepResult


def _plan(steps) -> ExecutionPlan:
    return ExecutionPlan(
        original_query="住民票の写しの取り方は？",
        complexity=0.5,
        estimated_steps=len(steps),
        requires_confirmation=False,
        steps=steps,
        success_criteria="ユーザーの質問に適切に回答できている",
    )


def _step(step_id, action, *, dynamic=False, depends_on=None) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        action=action,
        dynamic=dynamic,
        description=f"{action} step",
        depends_on=depends_on or [],
        expected_output="out",
    )


def _result(step_id, status="success", output="ok") -> StepResult:
    return StepResult(step_id=step_id, status=status, output=output, confidence=0.8)


class TestDynamicFlag:
    def test_既定は計画どおりのステップ(self):
        assert _step(1, "rag_search").dynamic is False

    def test_動的挿入は明示できる(self):
        assert _step(101, "web_search", dynamic=True).dynamic is True


class TestFinalAnswerOf:
    def test_最後の成功したreasoningの出力を返す(self):
        state = SimpleNamespace(
            plan=_plan([_step(1, "rag_search"), _step(2, "reasoning")]),
            step_results={1: _result(1), 2: _result(2, output="回答本文")},
        )
        assert Executor._final_answer_of(state) == "回答本文"

    def test_reasoningが失敗していればNone(self):
        state = SimpleNamespace(
            plan=_plan([_step(1, "rag_search"), _step(2, "reasoning")]),
            step_results={1: _result(1), 2: _result(2, status="failed", output=None)},
        )
        assert Executor._final_answer_of(state) is None

    def test_reasoningが無ければNone(self):
        state = SimpleNamespace(
            plan=_plan([_step(1, "rag_search")]),
            step_results={1: _result(1)},
        )
        assert Executor._final_answer_of(state) is None


class TestRecordMemorySuccess:
    """実行メモリへ「成功」を記録する条件。"""

    def _run(self, plan, step_results):
        recorded = {}

        class MemoryStub:
            def record_many(self, query, collections, success, confidence):
                recorded.update(
                    query=query, collections=list(collections),
                    success=success, confidence=confidence,
                )

        executor = Executor.__new__(Executor)          # __init__ を通さず最小構成
        executor._memory = MemoryStub()
        state = SimpleNamespace(
            plan=plan, step_results=step_results,
            used_collections=["gov_faq_anthropic"], overall_confidence=0.91,
        )
        executor._record_memory(state)
        return recorded

    def test_動的webが失敗しても最終回答があれば成功(self):
        """これが回帰の本体。Web の空振りで RAG コレクションを罰しない。"""
        plan = _plan([
            _step(1, "rag_search"),
            _step(101, "web_search", dynamic=True),
            _step(201, "ask_user", dynamic=True),
            _step(2, "reasoning"),
        ])
        results = {
            1: _result(1),
            101: _result(101, status="failed", output=None),
            201: _result(201),
            2: _result(2, output="回答本文"),
        }
        assert self._run(plan, results)["success"] is True

    def test_計画どおりのステップが失敗すれば失敗(self):
        plan = _plan([_step(1, "rag_search"), _step(2, "reasoning")])
        results = {1: _result(1, status="failed", output=None), 2: _result(2)}
        assert self._run(plan, results)["success"] is False

    def test_最終回答が無ければ失敗(self):
        """全ステップ success でも、答えに辿り着いていなければ成功ではない。"""
        plan = _plan([_step(1, "rag_search")])
        results = {1: _result(1)}
        assert self._run(plan, results)["success"] is False

    def test_コレクション未使用なら記録しない(self):
        recorded = {}

        class MemoryStub:
            def record_many(self, **kwargs):
                recorded.update(kwargs)

        executor = Executor.__new__(Executor)
        executor._memory = MemoryStub()
        state = SimpleNamespace(
            plan=_plan([_step(1, "reasoning")]),
            step_results={1: _result(1)},
            used_collections=[], overall_confidence=0.5,
        )
        executor._record_memory(state)
        assert recorded == {}


class TestReasoningContext:
    """reasoning へ渡す参照情報の組み立て（`_prepare_tool_kwargs`）。"""

    ASK_USER_OUTPUT = (
        "{'question': '「住民票の写しの取り方は？」について検索しましたが、"
        "十分な情報が見つかりませんでした。', 'urgency': 'blocking', "
        "'awaiting_response': True}"
    )

    def _kwargs(self, plan, step_results):
        executor = Executor.__new__(Executor)
        executor.config = SimpleNamespace(
            executor=SimpleNamespace(
                reasoning_min_rag_score=0.64, reasoning_max_sources=8
            ),
        )
        state = SimpleNamespace(plan=plan, step_results=step_results)
        reasoning_step = next(s for s in plan.steps if s.action == "reasoning")
        return executor._prepare_tool_kwargs(reasoning_step, state)

    def test_ask_userの出力は参照情報に入らない(self):
        """内部の「情報が見つかりませんでした」を回答へ引き写させない。"""
        plan = _plan([
            _step(1, "rag_search"),
            _step(201, "ask_user", dynamic=True),
            _step(2, "reasoning", depends_on=[1]),
        ])
        results = {
            1: _result(1, output="検索メモ"),
            201: _result(201, output=self.ASK_USER_OUTPUT),
            2: _result(2, output=""),
        }
        context = self._kwargs(plan, results).get("context", "")
        assert "十分な情報が見つかりませんでした" not in context
        assert "awaiting_response" not in context

    def test_ask_user以外の結果は従来どおり入る(self):
        plan = _plan([_step(1, "rag_search"), _step(2, "reasoning", depends_on=[1])])
        results = {1: _result(1, output="検索メモ"), 2: _result(2, output="")}
        assert "検索メモ" in self._kwargs(plan, results).get("context", "")

    def test_元の質問がreasoningへ渡る(self):
        """step.query が空でも description を質問にしない（回答が汎用要約になる）。"""
        plan = _plan([_step(1, "rag_search"), _step(2, "reasoning", depends_on=[1])])
        results = {1: _result(1, output="検索メモ")}
        assert self._kwargs(plan, results)["query"] == "住民票の写しの取り方は？"

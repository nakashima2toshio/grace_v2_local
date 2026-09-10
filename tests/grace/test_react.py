"""S3 ハイブリッド ReAct のテスト（LLM はモック / フォールバック）。"""

from unittest.mock import MagicMock, patch

import pytest

from grace.executor import Executor
from grace.schemas import (
    AgentThought,
    ExecutionPlan,
    PlanStep,
    Scratchpad,
)
from grace.tools import ToolRegistry, ToolResult


# ---------------------------------------------------------------------------
# スキーマ
# ---------------------------------------------------------------------------
class TestReactSchemas:
    def test_scratchpad_add_and_prompt(self):
        sp = Scratchpad()
        assert "まだ何も" in sp.as_prompt()
        sp.add(action="rag_search", observation="検索結果A", confidence=0.8, query="q")
        sp.add(action="reasoning", observation="回答", confidence=0.9)
        prompt = sp.as_prompt()
        assert "rag_search" in prompt and "reasoning" in prompt
        assert sp.last_confidence() == 0.9
        assert len(sp.entries) == 2

    def test_scratchpad_truncates_long_observation(self):
        sp = Scratchpad()
        sp.add(action="rag_search", observation="x" * 1000, confidence=0.5)
        assert "省略" in sp.entries[0].observation
        assert len(sp.entries[0].observation) < 1000

    def test_agent_thought_defaults(self):
        t = AgentThought()
        assert t.next_action == "reasoning"
        assert t.is_final is False


# ---------------------------------------------------------------------------
# 共通フィクスチャ
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_tool_registry():
    registry = MagicMock(spec=ToolRegistry)
    rag_tool = MagicMock()
    rag_tool.execute.return_value = ToolResult(
        success=True,
        output=[{"id": 1, "score": 0.9, "payload": {"question": "Q1", "answer": "A1"}}],
        confidence_factors={"result_count": 1, "avg_score": 0.9, "score_variance": 0.0},
    )
    reasoning_tool = MagicMock()
    reasoning_tool.execute.return_value = ToolResult(
        success=True,
        output="最終回答です",
        confidence_factors={"has_sources": True, "source_count": 1},
    )

    def get_tool(name):
        return {"rag_search": rag_tool, "reasoning": reasoning_tool}.get(name)

    registry.get.side_effect = get_tool
    return registry


def _plan(complexity: float) -> ExecutionPlan:
    return ExecutionPlan(
        original_query="複雑な質問",
        complexity=complexity,
        estimated_steps=2,
        requires_confirmation=False,
        steps=[
            PlanStep(step_id=1, action="rag_search", description="検索",
                     query="複雑な質問", expected_output="結果"),
            PlanStep(step_id=2, action="reasoning", description="回答生成",
                     depends_on=[1], expected_output="回答"),
        ],
        success_criteria="回答できている",
        plan_id="react-test",
    )


# ---------------------------------------------------------------------------
# ディスパッチ（複雑度による分岐）
# ---------------------------------------------------------------------------
class TestDispatch:
    def test_complex_query_routes_to_react(self, mock_tool_registry):
        executor = Executor(tool_registry=mock_tool_registry)
        with patch.object(Executor, "_decide_next_action") as mock_decide:
            mock_decide.side_effect = [
                AgentThought(next_action="rag_search", query="複雑な質問"),
                AgentThought(next_action="reasoning", is_final=True),
            ]
            result = executor.execute_plan(_plan(0.8))  # >= 0.7 → ReAct
        assert mock_decide.called           # ReAct ループが使われた
        assert 0.0 <= result.overall_confidence <= 1.0

    def test_simple_query_uses_static_path(self, mock_tool_registry):
        executor = Executor(tool_registry=mock_tool_registry)
        with patch.object(Executor, "_decide_next_action") as mock_decide, \
                patch.object(Executor, "_evaluate_rag_relevance", return_value=True):
            result = executor.execute_plan(_plan(0.3))  # < 0.7 → 静的パス
        assert not mock_decide.called       # ReAct は使われない
        assert result.final_answer is not None


# ---------------------------------------------------------------------------
# ReAct ループ本体
# ---------------------------------------------------------------------------
class TestReactLoop:
    def test_loop_executes_and_finishes(self, mock_tool_registry):
        executor = Executor(tool_registry=mock_tool_registry)
        with patch.object(Executor, "_decide_next_action") as mock_decide:
            mock_decide.side_effect = [
                AgentThought(next_action="rag_search", query="複雑な質問"),
                AgentThought(next_action="reasoning", is_final=True),
            ]
            result = executor.execute_plan(_plan(0.9))
        assert result.final_answer == "最終回答です"
        # rag_search → reasoning の2ステップが実行されている
        assert len(result.step_results) >= 2

    def test_finish_without_answer_appends_final_reasoning(self, mock_tool_registry):
        """finish が先に来ても、回答未生成なら最終 reasoning を1回補う。"""
        executor = Executor(tool_registry=mock_tool_registry)
        with patch.object(Executor, "_decide_next_action") as mock_decide:
            mock_decide.side_effect = [AgentThought(next_action="finish", is_final=True)]
            result = executor.execute_plan(_plan(0.8))
        assert result.final_answer == "最終回答です"


# ---------------------------------------------------------------------------
# _decide_next_action のフォールバック（LLM 不在）
# ---------------------------------------------------------------------------
class TestDecideNextActionFallback:
    def test_fallback_follows_initial_plan(self, mock_tool_registry):
        executor = Executor(tool_registry=mock_tool_registry)
        # LLM 呼び出しを強制的に失敗させる
        executor._react_client = MagicMock()
        executor._react_client.models.generate_content.side_effect = Exception("no api")

        plan = _plan(0.8)
        queue = list(plan.steps)
        t1 = executor._decide_next_action(plan, Scratchpad(), queue)
        assert t1.next_action == "rag_search"      # 初期計画の1手目
        t2 = executor._decide_next_action(plan, Scratchpad(), queue)
        assert t2.next_action == "reasoning"        # 2手目
        assert t2.is_final is True
        t3 = executor._decide_next_action(plan, Scratchpad(), queue)
        assert t3.next_action == "finish"           # 消化済み → 終了

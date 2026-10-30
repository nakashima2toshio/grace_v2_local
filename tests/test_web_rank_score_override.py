"""Web 検索の順位スコアで、LLM の確信度を上書きしないこと（バグ C）。

## なぜ必要か

`ConfidenceCalculator.llm_calculate` は、検索ステップで最高スコアが 0.7 を超えると、
LLM が付けた確信度を検索スコアで上書きしていた（LLM が下げすぎるのを防ぐため）。

社内検索（Qdrant）のスコアはコサイン類似度なので、この上書きには意味がある。
しかし Web 検索のスコアは**順位から作った値**（1 位 = 1.0、最下位 = 0.5。
`grace/tools.py::WebSearchTool._parse_to_rag_format`）で、内容が質問に合うかとは関係ない。
そのため Web 検索のステップは、LLM が何点を付けても**毎回 1.0** に上書きされていた。

上書きは、スコアが類似度であるステップ（`search_score_is_similarity=True`。rag_search）に限る。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import grace.confidence as confidence_module
from grace.confidence import ConfidenceCalculator, ConfidenceFactors
from grace.config import GraceConfig


@pytest.fixture
def calculator(monkeypatch):
    """LLM の評価を 0.4 に固定した ConfidenceCalculator。"""
    evaluator = SimpleNamespace(
        evaluate_with_factors=lambda **_kw: {"score": 0.4, "reason": "内容が質問に十分合っていない"})
    monkeypatch.setattr(confidence_module, "create_llm_evaluator", lambda **_kw: evaluator)
    return ConfidenceCalculator(GraceConfig())


def _factors(max_score, *, similarity):
    return ConfidenceFactors(search_result_count=5, search_avg_score=max_score,
                             search_max_score=max_score, is_search_step=True,
                             search_score_is_similarity=similarity)


def test_Web検索の順位スコアでは上書きしない(calculator):
    score = calculator.llm_calculate(_factors(1.0, similarity=False))

    assert score.score == 0.4
    assert "検索スコア" not in score.reason


def test_社内検索の類似度が高ければ従来どおり上書きする(calculator):
    score = calculator.llm_calculate(_factors(0.85, similarity=True))

    assert score.score == 0.85
    assert "検索スコア 0.8500 を優先" in score.reason


def test_executorはrag_searchだけを類似度として渡す():
    """Executor が組み立てる factors の印（`_calculate_step_confidence` 系の入口）。"""
    from grace.executor import ExecutionState, Executor
    from grace.schemas import ExecutionPlan, PlanStep
    from grace.tools import ToolResult

    executor = Executor.__new__(Executor)
    executor.config = GraceConfig()
    result = ToolResult(success=True, output=[], confidence_factors={
        "result_count": 3, "avg_score": 0.9, "max_score": 1.0, "score_variance": 0.0})

    def factors_for(action):
        step = PlanStep(step_id=1, action=action, description="x", expected_output="x")
        state = ExecutionState(plan=ExecutionPlan(
            original_query="q", complexity=0.5, estimated_steps=1, requires_confirmation=False,
            success_criteria="x", steps=[step]))
        return executor._build_confidence_factors(result, step, state)

    assert factors_for("rag_search").search_score_is_similarity is True
    assert factors_for("web_search").search_score_is_similarity is False

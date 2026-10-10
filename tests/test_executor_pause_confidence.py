"""介入で一時停止したときも、回答があれば全体信頼度が計算されることを守るテスト。

## なぜ必要か

executor は、ステップの信頼度が ESCALATE 帯（confirm しきい値未満）だと一時停止して
結果を返す。この分岐は通常の終了処理（全体信頼度の計算）より前に `return` するため、
以前は `overall_confidence` が初期値 0.0 のまま返っていた。回答が生成済みで
groundedness の支持率が 1.00 でも画面の「全体信頼度」が 0.00 になる
（grace_v2 の実測 2026-09-29「明日の東京の天気と、台風の状況は？」。本リポジトリへは 2026-10-10 に移植）。

本リポジトリの既定（`judges.step_confidence_llm=false`）ではステップ信頼度をヒューリスティック
（`ConfidenceCalculator.calculate`）で出すので、`calculate` と `llm_calculate` の両方を差し替え、
設定に関係なく reasoning ステップだけが ESCALATE 帯になるようにしている。

LLM（Ollama）・Qdrant・API キーには依存しない（ツールとスコアラをスタブに差し替える）。
"""

from types import SimpleNamespace

import pytest

from grace.confidence import ConfidenceScore
from grace.executor import Executor
from grace.schemas import ExecutionPlan, PlanStep
from grace.tools import ToolResult


def _web_items(n: int = 9):
    return [
        {
            "score": 1.0 - i * 0.1,
            "payload": {"answer": f"snippet {i}", "source": f"https://ex/{i}", "title": "t"},
            "collection": "web_search",
        }
        for i in range(n)
    ]


class _Tool:
    def __init__(self, result: ToolResult):
        self._result = result

    def execute(self, **_kwargs) -> ToolResult:
        return self._result


def _registry(rag: ToolResult, reasoning: ToolResult):
    web = ToolResult(
        success=True,
        output=_web_items(),
        confidence_factors={
            "result_count": 9, "avg_score": 0.6, "max_score": 1.0, "min_score": 0.2,
            "score_variance": 0.07, "top_score": 1.0, "score_spread": 0.8,
        },
    )
    tools = {"rag_search": _Tool(rag), "web_search": _Tool(web), "reasoning": _Tool(reasoning)}
    return SimpleNamespace(get=tools.get, list_tools=lambda: list(tools))


def _rag(score: float) -> ToolResult:
    return ToolResult(
        success=True,
        output=[{"score": score, "payload": {"answer": "x", "source": "a.csv"},
                 "collection": "ec_policy_anthropic"}],
        confidence_factors={
            "result_count": 1, "avg_score": score, "max_score": score, "min_score": score,
            "score_variance": 0.0, "top_score": score, "score_spread": 0.0,
        },
    )


def _plan() -> ExecutionPlan:
    query = "明日の東京の天気と、台風の状況は？"
    return ExecutionPlan(
        original_query=query, complexity=0.5, estimated_steps=2,
        requires_confirmation=False, success_criteria="x",
        steps=[
            PlanStep(step_id=1, action="rag_search", description="関連情報を検索", query=query,
                     collection="ec_policy_anthropic", depends_on=[], expected_output="x",
                     fallback="web_search", timeout_seconds=30),
            PlanStep(step_id=2, action="reasoning", description="回答生成", query=None,
                     collection=None, depends_on=[1], expected_output="x", timeout_seconds=30),
        ],
    )


@pytest.fixture
def make_executor(monkeypatch):
    # クライアントの生成だけが必要（実際には呼ばない）。Embedding 用の鍵は CI に無い。
    monkeypatch.setenv("GOOGLE_API_KEY", "dummy")

    def _make(rag: ToolResult, reasoning_score: float | None):
        from grace.config import get_config

        reasoning = ToolResult(success=True, output="回答本文です",
                               confidence_factors={"source_count": 9})
        ex = Executor(config=get_config(), tool_registry=_registry(rag, reasoning),
                      enable_replan=True)
        ex._memory = None  # 実行メモリ（ファイル）へ書かない

        orig = ex.confidence_calculator.calculate

        def scored(factors, **_kw):
            score = orig(factors)
            if reasoning_score is not None and not factors.is_search_step:
                # reasoning ステップだけ低評価（回答が「見当たりません」型のとき相当）
                return ConfidenceScore(score=reasoning_score, factors=factors,
                                       breakdown=score.breakdown, reason="低評価")
            return score

        # 既定はヒューリスティック（calculate）、judges.step_confidence_llm=true なら llm_calculate
        ex.confidence_calculator.calculate = scored
        ex.confidence_calculator.llm_calculate = scored
        ex.llm_evaluator.evaluate_final = lambda **_kw: SimpleNamespace(
            self_eval_score=0.8, coverage_score=0.5, reason="")
        ex.groundedness_verifier.verify = lambda _q, _a, _s: SimpleNamespace(
            verified=True, supported=9, contradicted=0, total=9, support_rate=1.0,
            has_contradiction=False, reason="")
        ex._evaluate_rag_relevance = lambda **_kw: False
        return ex

    return _make


def test_answer_with_paused_reasoning_step_keeps_overall_confidence(make_executor):
    """reasoning ステップの評価が ESCALATE 帯でも、回答があれば全体信頼度は 0 にならない。"""
    ex = make_executor(_rag(0.65), reasoning_score=0.2)

    result = ex.execute(_plan())

    assert result.final_answer == "回答本文です"
    # groundedness 支持率 1.0 が主成分なので、低くとも 0.5 を下回らない
    assert result.overall_confidence >= 0.5, (
        f"回答があり支持率 1.00 なのに全体信頼度が {result.overall_confidence}"
    )


def test_normal_completion_is_unchanged(make_executor):
    """停止しない通常経路の全体信頼度は従来どおり計算される。"""
    ex = make_executor(_rag(0.65), reasoning_score=None)

    result = ex.execute(_plan())

    assert result.final_answer == "回答本文です"
    assert result.overall_confidence >= 0.5


def test_paused_before_any_answer_stays_zero(make_executor):
    """回答が生成される前に止まったときは、計算対象が無いので 0.0（回答未生成）のまま。"""
    ex = make_executor(_rag(0.31), reasoning_score=None)  # RAG 段階で ESCALATE 帯

    result = ex.execute(_plan())

    assert result.final_answer is None
    assert result.overall_confidence == 0.0

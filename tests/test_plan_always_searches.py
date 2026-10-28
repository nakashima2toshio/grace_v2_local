"""Planner の計画に**必ず社内検索（rag_search）が入る**ことを守るテスト。

## なぜ必要か（実測 2026-10-22・Mac の ./run_dev.sh）

基本版タブに次の 3 行を 1 つの問い合わせとして送ると、社内検索が一度も呼ばれず、
`cc_news_2per_anthropic` にある情報が使われないまま ⑤ Web → escalate になった::

    Jed York CEOが新しいGMとコーチを探す背景には、どのような経緯がありますか？
    その件の経緯を詳しく教えて
    最新ニュースを検索して、49ersの新GMとヘッドコーチの人事を教えて

原因は 2 つ。

1. **曖昧判定で検索ごと止めていた。** `is_ambiguous_query()` が「その件」等を見つけると、
   rag_search を含まない ask_user だけの計画を返していた。しかもその聞き返しは
   画面につながっていない（`on_intervention_required` を渡す呼び出し元が無い）ので、
   「検索もしない・聞き返しもしない」行き止まりだった。2026-10-22 に条件を狭めたが
   （#323）、「住民票の写しを取りたい。その件の手数料は？」のような普通の質問が
   まだ止まっていた。判定ごと削除した。
2. **LLM 計画は rag_search を省くことがある。** 「最新ニュースを検索して」を含む質問は
   LLM が計画を作り、実測（2026-10-15）では web_search → reasoning だけだった。
   計画の後処理で rag_search を先頭に足し、ask_user（画面に出せない）を外す。

検索で当たらなかったときは後段（Web で補う・④' 情報なし → 有人対応）に任せる。

LLM・Qdrant・API キーには依存しない。
"""
from __future__ import annotations

import json

import pytest

from grace.config import GraceConfig
from grace.planner import Planner
from grace.schemas import ExecutionPlan

SCREEN_INPUT = (
    "Jed York CEOが新しいGMとコーチを探す背景には、どのような経緯がありますか？\n"
    "その件の経緯を詳しく教えて\n"
    "最新ニュースを検索して、49ersの新GMとヘッドコーチの人事を教えて"
)

# 以前は「曖昧」とされて検索されなかった質問
FORMERLY_AMBIGUOUS = [
    "住民票の写しを取りたい。その件の手数料はいくらですか？",
    "領収書の再発行について。その件の手続きを教えて",
    "あの件のGMの人事は？",
    "その件の経緯を詳しく教えて",
]


def _step(step_id, action, depends_on=(), query=None):
    return {
        "step_id": step_id, "action": action, "description": action,
        "query": query, "collection": None, "depends_on": list(depends_on),
        "expected_output": "x", "fallback": None, "timeout_seconds": 60,
    }


def _llm_plan(*steps):
    return json.dumps({
        "original_query": SCREEN_INPUT, "complexity": 0.3,
        "estimated_steps": len(steps), "requires_confirmation": False,
        "steps": list(steps), "success_criteria": "回答できている",
        "created_at": None, "plan_id": None,
    }, ensure_ascii=False)


# 実測（2026-10-15）の LLM 計画と同じ形: 社内検索が無い
WEB_ONLY = _llm_plan(
    _step(1, "web_search", query="49ers GM"),
    _step(2, "reasoning", depends_on=[1]),
)


def _planner(llm_plan_json: str = WEB_ONLY) -> Planner:
    """LLM・Qdrant を呼ばない Planner。LLM 計画は `llm_plan_json` を返す。"""
    planner = Planner.__new__(Planner)
    planner.config = GraceConfig()
    planner._memory = None
    planner._get_available_collections = lambda: []
    planner.estimate_complexity_with_llm = lambda _q: 0.4
    planner._generate_plan_with_retry = (
        lambda *_a, **_kw: ExecutionPlan.model_validate_json(llm_plan_json)
    )
    return planner


def _actions(plan):
    return [s.action for s in plan.steps]


def _assert_searches_internally(plan, query):
    rag = [s for s in plan.steps if s.action == "rag_search"]
    assert rag, f"rag_search が無い: {_actions(plan)}"
    assert rag[0].query == query
    assert "ask_user" not in _actions(plan)
    reasoning = [s for s in plan.steps if s.action == "reasoning"]
    assert reasoning and rag[0].step_id in reasoning[-1].depends_on


class TestEveryPlanSearchesInternally:
    def test_screen_input(self):
        plan = _planner().create_plan(SCREEN_INPUT)

        _assert_searches_internally(plan, SCREEN_INPUT)

    @pytest.mark.parametrize("query", FORMERLY_AMBIGUOUS)
    def test_formerly_ambiguous_queries(self, query):
        plan = _planner().create_plan(query)

        _assert_searches_internally(plan, query)


class TestLlmPlanIsCompleted:
    def test_web_only_plan_gets_rag_search_first_and_keeps_web(self):
        plan = _planner(WEB_ONLY).create_plan(SCREEN_INPUT)

        assert _actions(plan) == ["rag_search", "web_search", "reasoning"]
        assert [s.step_id for s in plan.steps] == [1, 2, 3]
        assert plan.steps[2].depends_on == [1, 2]
        assert plan.estimated_steps == 3

    def test_ask_user_steps_are_removed(self):
        """聞き返しは画面に出せない（Support の入口は on_intervention_required を渡さない）。"""
        ask_only = _llm_plan(_step(1, "ask_user", query="どの件ですか？"))

        plan = _planner(ask_only).create_plan(SCREEN_INPUT)

        assert _actions(plan) == ["rag_search", "reasoning"]
        assert plan.steps[1].depends_on == [1]

    def test_plan_that_already_searches_is_left_alone(self):
        rag_first = _llm_plan(
            _step(1, "rag_search", query=SCREEN_INPUT),
            _step(2, "web_search", query="49ers GM"),
            _step(3, "reasoning", depends_on=[1, 2]),
        )

        plan = _planner(rag_first).create_plan(SCREEN_INPUT)

        assert _actions(plan) == ["rag_search", "web_search", "reasoning"]
        assert plan.steps[2].depends_on == [1, 2]

    def test_disabled_rag_search_is_not_added(self):
        """rag_search を設定で禁止しているなら足さない（禁止を素通りしない）。"""
        planner = _planner(WEB_ONLY)
        planner.config.tools.disabled = ["rag_search"]

        plan = planner.create_plan(SCREEN_INPUT)

        assert "rag_search" not in _actions(plan)


def test_planner_no_longer_has_a_clarification_shortcut():
    """曖昧判定で計画を変える経路を戻さない。"""
    import grace.planner as planner_module

    assert not hasattr(planner_module, "is_ambiguous_query")
    assert not hasattr(Planner, "_create_clarification_plan")


# =============================================================================
# 計画 → 実行: executor が実際に rag_search を呼ぶ
# =============================================================================

class _CountingTool:
    def __init__(self, result):
        self.queries = []
        self._result = result

    def execute(self, **kwargs):
        self.queries.append(kwargs.get("query"))
        return self._result


def test_executor_actually_calls_rag_search_for_the_screen_input(monkeypatch):
    """画面の 3 行で作った計画を Executor で流すと、社内検索が 1 回・原文のまま呼ばれる。"""
    import copy
    from types import SimpleNamespace

    from grace.config import get_config
    from grace.executor import Executor
    from grace.tools import ToolResult

    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
    monkeypatch.setenv("GOOGLE_API_KEY", "dummy")
    factors = {"result_count": 1, "avg_score": 0.8, "max_score": 0.8, "min_score": 0.8,
               "score_variance": 0.0, "top_score": 0.8, "score_spread": 0.0}
    tools = {
        "rag_search": _CountingTool(ToolResult(
            success=True, confidence_factors=factors,
            output=[{"score": 0.8, "payload": {"answer": "49ers の GM 人事", "source": "cc_news.csv"},
                     "collection": "cc_news_2per_anthropic"}])),
        "web_search": _CountingTool(ToolResult(
            success=True, confidence_factors=factors,
            output=[{"score": 0.9, "payload": {"answer": "記事", "source": "https://example.com/a",
                                               "title": "t"}, "collection": "web_search"}])),
        "reasoning": _CountingTool(ToolResult(success=True, output="回答本文",
                                              confidence_factors={"source_count": 1})),
        "ask_user": _CountingTool(ToolResult(success=True, output="ユーザー応答")),
    }
    config = copy.deepcopy(get_config())
    executor = Executor(config=config, tool_registry=SimpleNamespace(
        get=tools.get, list_tools=lambda: list(tools)), enable_replan=False)
    executor._memory = None
    executor.confidence_calculator.llm_calculate = (
        lambda factors, **_kw: executor.confidence_calculator.calculate(factors))
    executor.llm_evaluator.evaluate_final = lambda *_a, **_kw: SimpleNamespace(
        self_eval_score=0.8, coverage_score=0.5, reason="")
    executor.groundedness_verifier.verify = lambda _q, _a, _s: SimpleNamespace(
        verified=True, supported=1, contradicted=0, total=1, support_rate=1.0,
        has_contradiction=False, reason="")
    executor._evaluate_rag_relevance = lambda **_kw: True

    plan = _planner(WEB_ONLY).create_plan(SCREEN_INPUT)
    executor.execute(plan)

    assert tools["rag_search"].queries == [SCREEN_INPUT]
    assert tools["ask_user"].queries == []
    assert len(tools["reasoning"].queries) == 1


def _plan_rag_web_reasoning():
    from grace.schemas import PlanStep

    def step(i, action, deps=()):
        return PlanStep(step_id=i, action=action, description=action, query=SCREEN_INPUT,
                        collection=None, depends_on=list(deps), expected_output="x",
                        timeout_seconds=30)
    return ExecutionPlan(original_query=SCREEN_INPUT, complexity=0.4, estimated_steps=3,
                         requires_confirmation=False, success_criteria="x",
                         steps=[step(1, "rag_search"), step(2, "web_search"),
                                step(3, "reasoning", [1, 2])])


def _skip_web_executor(monkeypatch, web_disabled):
    """RAG が十分（0.8）な Executor。web_search は飛ばされる。"""
    import copy
    from types import SimpleNamespace

    from grace.config import get_config
    from grace.executor import Executor
    from grace.tools import ToolResult

    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
    monkeypatch.setenv("GOOGLE_API_KEY", "dummy")
    factors = {"result_count": 1, "avg_score": 0.8, "max_score": 0.8, "min_score": 0.8,
               "score_variance": 0.0, "top_score": 0.8, "score_spread": 0.0}
    tools = {
        "rag_search": _CountingTool(ToolResult(
            success=True, confidence_factors=factors,
            output=[{"score": 0.8, "payload": {"answer": "社内", "source": "a.csv"},
                     "collection": "cc_news_2per_anthropic"}])),
        "web_search": _CountingTool(ToolResult(success=True, output=[], confidence_factors=factors)),
        "reasoning": _CountingTool(ToolResult(success=True, output="回答本文",
                                              confidence_factors={"source_count": 1})),
        "ask_user": _CountingTool(ToolResult(success=True, output="ユーザー応答")),
    }
    config = copy.deepcopy(get_config())
    if web_disabled:
        config.tools.disabled = [*config.tools.disabled, "web_search"]
    executor = Executor(config=config, tool_registry=SimpleNamespace(
        get=tools.get, list_tools=lambda: list(tools)), enable_replan=False)
    executor._memory = None
    executor.confidence_calculator.llm_calculate = (
        lambda factors, **_kw: executor.confidence_calculator.calculate(factors))
    executor.llm_evaluator.evaluate_final = lambda *_a, **_kw: SimpleNamespace(
        self_eval_score=0.8, coverage_score=0.5, reason="")
    executor.groundedness_verifier.verify = lambda _q, _a, _s: SimpleNamespace(
        verified=True, supported=1, contradicted=0, total=1, support_rate=1.0,
        has_contradiction=False, reason="")
    executor._evaluate_rag_relevance = lambda **_kw: True
    return executor, tools


@pytest.mark.parametrize("web_disabled", [False, True], ids=["rag_sufficient", "web_off"])
def test_reasoning_runs_when_its_web_dependency_was_skipped(monkeypatch, web_disabled):
    """意図して飛ばした web_search に依存していても、回答生成は飛ばさない。"""
    executor, tools = _skip_web_executor(monkeypatch, web_disabled)

    result = executor.execute(_plan_rag_web_reasoning())

    if web_disabled:
        assert tools["web_search"].queries == []
    # Web 許可時は並列プリフェッチが先に呼ぶことがある（結果は使わない・既存の仕様）
    assert len(tools["reasoning"].queries) == 1
    assert result.final_answer == "回答本文"

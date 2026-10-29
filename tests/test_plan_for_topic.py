"""0-(A) で整理した話題から作る計画（`Planner.create_plan_for_topic`）と、その実行（Executor）。

- 観点ごとに社内検索（検索文 = `search_query`）、利用者が最新情報を求めた観点には Web 検索
  （検索語 = `web_query`・`required=True`）、最後に回答生成（質問文 = `answer_question`）を置く
- 社内検索が十分でも `required` の Web 検索は飛ばさない
  （以前は社内検索のスコアが十分だと、「最新ニュースを検索して」と頼まれても Web 検索を飛ばした）
- 社内検索が足りずに Web 検索を差し込むとき・失敗時に Web へ落ちるときは、社内検索用の文ではなく
  `web_query` を使う。同じ語の `required` の Web 検索が計画にあれば差し込まない（同じ検索を 2 回しない）
- この計画は ReAct（検索語を LLM が決め直す）へ回さない（`execute(plan, allow_react=False)`）

LLM・Qdrant・API キーには依存しない（Executor の LLM 評価は差し替える）。
"""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from backend.app.core.question_refiner import Aspect, Sentence, Topic
from grace.config import GraceConfig, get_config
from grace.executor import Executor
from grace.planner import Planner
from grace.schemas import ExecutionPlan, PlanStep
from grace.tools import ToolResult

ANSWER = "Jed York CEOが新しいGMとコーチを探す背景の経緯と、49ersの新GMとヘッドコーチの人事を教えてください。"
S1, W1 = "Jed York CEOが新しいGMとコーチを探す背景の経緯", "Jed York CEO GM コーチ 経緯"
S2, W2 = "49ersの新しいGMとヘッドコーチの人事", "49ers 新GM ヘッドコーチ 人事"


def _topic(*aspects):
    return Topic(
        answer_question=ANSWER,
        sentences=[Sentence(text=ANSWER, aspect=aspects[0][0], relation="主")],
        aspects=[Aspect(name=n, search_query=s, web_query=w, wants_latest_web=latest)
                 for n, s, w, latest in aspects],
    )


SCREEN_TOPIC = _topic(("経緯", S1, W1, False), ("人事", S2, W2, True))


def _planner() -> Planner:
    """LLM・Qdrant を呼ばない Planner（`client` を持たないので、LLM を呼べば AttributeError）。"""
    planner = Planner.__new__(Planner)
    planner.config = GraceConfig()
    planner._memory = None
    return planner


class TestCreatePlanForTopic:
    def test_観点ごとに検索し_最新情報を求めた観点はWeb検索も置く(self):
        plan = _planner().create_plan_for_topic(SCREEN_TOPIC)

        assert [(s.action, s.query) for s in plan.steps] == [
            ("rag_search", S1), ("rag_search", S2), ("web_search", W2), ("reasoning", None)]
        assert [s.step_id for s in plan.steps] == [1, 2, 3, 4]
        assert [s.web_query for s in plan.steps[:2]] == [W1, W2]
        assert [s.fallback for s in plan.steps[:2]] == ["web_search", "web_search"]
        assert plan.steps[2].required is True
        assert plan.original_query == ANSWER
        assert plan.estimated_steps == 4

    def test_回答生成は依存を持たない(self):
        """観点の 1 つ（Web 検索など）が失敗しただけで回答が作られなくなるのを防ぐ。"""
        plan = _planner().create_plan_for_topic(SCREEN_TOPIC)

        assert plan.steps[-1].depends_on == []

    def test_社内検索を禁止していれば置かない(self):
        planner = _planner()
        planner.config.tools.disabled = ["rag_search"]

        plan = planner.create_plan_for_topic(SCREEN_TOPIC)

        assert [s.action for s in plan.steps] == ["web_search", "reasoning"]


# =============================================================================
# 実行
# =============================================================================

class _Tool:
    def __init__(self, result=None, raises=None):
        self.queries = []
        self._result = result
        self._raises = raises

    def execute(self, **kwargs):
        self.queries.append(kwargs.get("query"))
        if self._raises:
            raise self._raises
        return self._result


def _factors(score):
    return {"result_count": 1, "avg_score": score, "max_score": score, "min_score": score,
            "score_variance": 0.0, "top_score": score, "score_spread": 0.0}


def _executor(monkeypatch, *, rag_score=0.8, rag_raises=None, web_disabled=False):
    monkeypatch.setenv("GOOGLE_API_KEY", "dummy")
    tools = {
        "rag_search": _Tool(ToolResult(
            success=True, confidence_factors=_factors(rag_score),
            output=[{"score": rag_score, "payload": {"answer": "社内", "source": "a.csv"},
                     "collection": "cc_news_2per_anthropic"}]), raises=rag_raises),
        "web_search": _Tool(ToolResult(
            success=True, confidence_factors=_factors(0.9),
            output=[{"score": 0.9, "payload": {"answer": "記事", "source": "https://example.com/a",
                                               "title": "t"}, "collection": "web_search"}])),
        "reasoning": _Tool(ToolResult(success=True, output="回答本文",
                                      confidence_factors={"source_count": 1})),
        "ask_user": _Tool(ToolResult(success=True, output="ユーザー応答")),
    }
    config = copy.deepcopy(get_config())
    # 並列の先行実行を止め、どの経路で Web 検索が呼ばれたかを確かめられるようにする
    config.executor.parallel_search = False
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


def _plan(*steps):
    return ExecutionPlan(original_query=ANSWER, complexity=0.5, estimated_steps=len(steps),
                         requires_confirmation=False, success_criteria="x", steps=list(steps))


def _rag(step_id, query, web_query):
    return PlanStep(step_id=step_id, action="rag_search", description="社内検索", query=query,
                    web_query=web_query, fallback="web_search", expected_output="x")


def _web(step_id, query, required):
    return PlanStep(step_id=step_id, action="web_search", description="Web", query=query,
                    required=required, expected_output="x")


def _reasoning(step_id):
    return PlanStep(step_id=step_id, action="reasoning", description="回答", expected_output="x")


class TestRequiredWebSearch:
    def test_社内検索が十分でも利用者が求めたWeb検索は行う(self, monkeypatch):
        executor, tools = _executor(monkeypatch, rag_score=0.8)

        result = executor.execute(_plan(_rag(1, S2, W2), _web(2, W2, required=True), _reasoning(3)),
                                  allow_react=False)

        assert tools["web_search"].queries == [W2]
        assert result.final_answer == "回答本文"

    def test_求められていないWeb検索は従来どおり飛ばす(self, monkeypatch):
        executor, tools = _executor(monkeypatch, rag_score=0.8)

        executor.execute(_plan(_rag(1, S2, W2), _web(2, W2, required=False), _reasoning(3)),
                         allow_react=False)

        assert tools["web_search"].queries == []

    def test_Web検索がOFFならrequiredでも行わない(self, monkeypatch):
        """use_web=False は「内部 RAG のみ」（`Executor._web_search_allowed`）。"""
        executor, tools = _executor(monkeypatch, rag_score=0.8, web_disabled=True)

        result = executor.execute(_plan(_rag(1, S2, W2), _web(2, W2, required=True), _reasoning(3)),
                                  allow_react=False)

        assert tools["web_search"].queries == []
        assert result.final_answer == "回答本文"


class TestWebQueryIsUsed:
    def test_社内検索が足りないときはweb_queryで検索する(self, monkeypatch):
        executor, tools = _executor(monkeypatch, rag_score=0.3)

        executor.execute(_plan(_rag(1, S1, W1), _reasoning(2)), allow_react=False)

        assert tools["rag_search"].queries == [S1]
        assert tools["web_search"].queries == [W1]

    def test_同じ語のrequiredのWeb検索が計画にあれば差し込まない(self, monkeypatch):
        executor, tools = _executor(monkeypatch, rag_score=0.3)

        executor.execute(_plan(_rag(1, S2, W2), _web(2, W2, required=True), _reasoning(3)),
                         allow_react=False)

        assert tools["web_search"].queries == [W2]

    def test_社内検索が失敗してWebへ落ちるときもweb_queryを使う(self, monkeypatch):
        executor, tools = _executor(monkeypatch, rag_raises=RuntimeError("Qdrant down"))

        executor.execute(_plan(_rag(1, S1, W1), _reasoning(2)), allow_react=False)

        assert tools["web_search"].queries[0] == W1


class TestLowConfidenceSearchDoesNotStopTheAnswer:
    def test_1つの観点の検索が低信頼でも回答生成まで進む(self, monkeypatch):
        """非対話（画面からの実行）では、検索ステップの ESCALATE で計画を止めない。

        止めると、他の観点で見つかった社内の結果ごと回答が捨てられる。
        """
        executor, tools = _executor(monkeypatch, rag_score=0.3)   # 信頼度 0.3 = ESCALATE 帯

        result = executor.execute(
            _plan(_rag(1, S1, W1), _rag(2, S2, W2), _web(3, W2, required=True), _reasoning(4)),
            allow_react=False)

        assert tools["rag_search"].queries == [S1, S2]
        assert len(tools["reasoning"].queries) == 1
        assert result.final_answer == "回答本文"


@pytest.mark.parametrize("allow_react, expected", [(False, "static"), (True, "react")])
def test_allow_reactがFalseなら複雑度が高くてもReActへ回さない(monkeypatch, allow_react, expected):
    executor, _tools = _executor(monkeypatch)
    executor.config.executor.react_enabled = True
    called = []

    def fake(kind):
        def gen(_plan):
            called.append(kind)
            return SimpleNamespace(final_answer="x")
            yield  # ジェネレータにする（ここには来ない）
        return gen

    monkeypatch.setattr(executor, "execute_react_generator", fake("react"))
    monkeypatch.setattr(executor, "execute_plan_generator", fake("static"))
    plan = _plan(_rag(1, S1, W1), _reasoning(2))
    plan.complexity = 0.9

    executor.execute(plan, allow_react=allow_react)

    assert called == [expected]

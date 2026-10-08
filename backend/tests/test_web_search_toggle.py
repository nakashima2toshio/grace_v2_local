"""「Web フォールバック OFF」（`use_web=False`）で、Web を一切検索しないことを守るテスト。

## なぜ必要か（実測 2026-10-04・Mac の E2E）

画面のトグルは「Web フォールバック（オフで内部RAGのみ）」と書いてあるのに、
`use_web=False` が止めていたのは ⑤ Web フォールバックだけだった。② Execute の
executor は、RAG のスコアが `rag_sufficient_score`（0.7）未満だと**自分で**
`web_search` を差し込む。E2E の「サービスが落ちています」（saas・use_web=False）で
無関係な URL が 9 件（魚の「マス」の Wikipedia、TV 番組ページなど）出典に並んだ。

executor が Web 検索へ入る経路は 1 つではないので、全部を塞ぐ:

| 経路 | 塞ぎ方 |
|---|---|
| RAG スコア不足時の動的挿入 | 挿入しない（`ask_user` も挿入しない。内部 RAG の結果で進む） |
| planner が計画した `web_search` ステップ | SKIPPED にする |
| 並列プリフェッチ | バッチに入れない |
| ステップ失敗時の `fallback="web_search"` | 実行しない |
| ReAct で LLM が `web_search` を選ぶ | 実行せず「無効」と観測させる。プロンプトの選択肢からも外す |

LLM・Qdrant・API キーには依存しない（ツールとスコアラをスタブに差し替える）。
"""

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from grace.executor import Executor
from grace.schemas import AgentThought, ExecutionPlan, PlanStep
from grace.tools import ToolResult


class _CountingTool:
    def __init__(self, result=None, raises=None):
        self.calls = 0
        self._result = result
        self._raises = raises

    def execute(self, **_kwargs):
        self.calls += 1
        if self._raises:
            raise self._raises
        return self._result


def _rag(score: float) -> ToolResult:
    return ToolResult(
        success=True,
        output=[{"score": score, "payload": {"answer": "社内の回答", "source": "a.csv"},
                 "collection": "saas_docs_anthropic"}],
        confidence_factors={
            "result_count": 1, "avg_score": score, "max_score": score, "min_score": score,
            "score_variance": 0.0, "top_score": score, "score_spread": 0.0,
        },
    )


def _web() -> ToolResult:
    return ToolResult(
        success=True,
        output=[{"score": 0.9, "payload": {"answer": "無関係", "source": "https://example.com/x",
                                           "title": "t"}, "collection": "web_search"}],
        confidence_factors={"result_count": 1, "avg_score": 0.9, "max_score": 0.9,
                            "min_score": 0.9, "score_variance": 0.0, "top_score": 0.9,
                            "score_spread": 0.0},
    )


def _step(step_id, action, depends_on=(), fallback=None, query="サービスが落ちています"):
    return PlanStep(step_id=step_id, action=action, description=action, query=query,
                    collection=None, depends_on=list(depends_on), expected_output="x",
                    fallback=fallback, timeout_seconds=30)


def _plan(steps, complexity=0.3) -> ExecutionPlan:
    return ExecutionPlan(original_query="サービスが落ちています", complexity=complexity,
                         estimated_steps=len(steps), requires_confirmation=False,
                         success_criteria="x", steps=steps)


@pytest.fixture
def make_executor(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "dummy")

    def _make(rag_tool, web_disabled: bool):
        from grace.config import get_config

        config = copy.deepcopy(get_config())
        if web_disabled:
            config.tools.disabled = [*config.tools.disabled, "web_search"]
        tools = {
            "rag_search": rag_tool,
            "web_search": _CountingTool(_web()),
            "reasoning": _CountingTool(ToolResult(success=True, output="回答本文",
                                                  confidence_factors={"source_count": 1})),
            "ask_user": _CountingTool(ToolResult(success=True, output="ユーザー応答")),
        }
        registry = SimpleNamespace(get=tools.get, list_tools=lambda: list(tools))
        ex = Executor(config=config, tool_registry=registry, enable_replan=False)
        ex._memory = None
        ex.confidence_calculator.llm_calculate = (
            lambda factors, **_kw: ex.confidence_calculator.calculate(factors)
        )
        ex.llm_evaluator.evaluate_final = lambda *_a, **_kw: SimpleNamespace(
            self_eval_score=0.8, coverage_score=0.5, reason="")
        ex.groundedness_verifier.verify = lambda _q, _a, _s: SimpleNamespace(
            verified=True, supported=1, contradicted=0, total=1, support_rate=1.0,
            has_contradiction=False, reason="")
        ex._evaluate_rag_relevance = lambda **_kw: True
        return ex, tools

    return _make


# ----------------------------------------------------------------------
# 既定（Web 許可）の挙動は変えない
# ----------------------------------------------------------------------
def test_low_rag_score_still_triggers_web_search_when_allowed(make_executor):
    ex, tools = make_executor(_CountingTool(_rag(0.5)), web_disabled=False)

    ex.execute(_plan([_step(1, "rag_search"), _step(2, "reasoning", depends_on=[1])]))

    assert tools["web_search"].calls == 1


# ----------------------------------------------------------------------
# Web 不許可（use_web=False）
# ----------------------------------------------------------------------
def test_low_rag_score_does_not_insert_web_search_when_disabled(make_executor):
    ex, tools = make_executor(_CountingTool(_rag(0.5)), web_disabled=True)

    result = ex.execute(_plan([_step(1, "rag_search"), _step(2, "reasoning", depends_on=[1])]))

    assert tools["web_search"].calls == 0
    # Web の代わりにユーザーへの質問を差し込まない（内部 RAG の結果で回答まで進む）
    assert tools["ask_user"].calls == 0
    assert result.final_answer == "回答本文"


def test_planned_web_search_step_is_skipped_when_disabled(make_executor):
    """planner が計画した web_search は実行しない（依存の無い並列プリフェッチ経路も含む）。"""
    ex, tools = make_executor(_CountingTool(_rag(0.9)), web_disabled=True)
    plan = _plan([
        _step(1, "rag_search"),
        _step(2, "web_search"),                 # 依存なし → 並列プリフェッチの対象になりうる
        _step(3, "reasoning", depends_on=[1]),
    ])

    result = ex.execute(plan)

    assert tools["web_search"].calls == 0
    assert result.final_answer == "回答本文"


def test_plan_starting_with_web_search_skips_it_when_disabled(make_executor):
    """RAG を経ない web_search（先頭ステップ）も実行しない。

    上のケースは RAG のスコアが十分なため、既存の「パターン(1)」が先に web_search を
    SKIPPED にしてしまい、ループ側の判定まで届かない。こちらはループ側だけで守る。
    """
    ex, tools = make_executor(_CountingTool(_rag(0.9)), web_disabled=True)

    result = ex.execute(_plan([_step(1, "web_search"), _step(2, "reasoning")]))

    assert tools["web_search"].calls == 0
    assert result.final_answer == "回答本文"


def test_failed_rag_does_not_fall_back_to_web_when_disabled(make_executor):
    ex, tools = make_executor(_CountingTool(raises=RuntimeError("qdrant down")), web_disabled=True)

    ex.execute(_plan([_step(1, "rag_search", fallback="web_search"),
                      _step(2, "reasoning", depends_on=[1])]))

    assert tools["web_search"].calls == 0


def test_react_does_not_run_web_search_when_disabled(make_executor):
    ex, tools = make_executor(_CountingTool(_rag(0.9)), web_disabled=True)
    thoughts = iter([
        AgentThought(reasoning="Web で調べる", next_action="web_search", query="障害"),
        AgentThought(reasoning="回答する", next_action="reasoning", is_final=True),
        AgentThought(reasoning="終わり", next_action="finish"),
    ])
    seen_scratchpads = []

    def decide(_plan, scratchpad, _queue):
        seen_scratchpads.append(scratchpad.as_prompt())
        return next(thoughts)

    ex._decide_next_action = decide

    ex.execute(_plan([_step(1, "rag_search"), _step(2, "reasoning", depends_on=[1])],
                     complexity=0.95))

    assert tools["web_search"].calls == 0
    # 選んだ web_search が「無効」として観測に残り、次の判断で LLM に伝わる
    assert any("Web 検索は無効" in s for s in seen_scratchpads[1:])


def test_react_prompt_hides_web_search_when_disabled(make_executor):
    allowed, _ = make_executor(_CountingTool(_rag(0.9)), web_disabled=False)
    disabled, _ = make_executor(_CountingTool(_rag(0.9)), web_disabled=True)

    assert "- web_search" in allowed._react_prompt_template()
    assert "- web_search" not in disabled._react_prompt_template()
    assert "- rag_search" in disabled._react_prompt_template()


# ----------------------------------------------------------------------
# Support コアの配線: use_web=False が executor の設定まで届く
# ----------------------------------------------------------------------
@pytest.mark.parametrize("use_web, disabled", [(True, False), (False, True)])
def test_support_core_disables_web_search_for_executor(monkeypatch, use_web, disabled):
    from backend.app.core.support_agent import run_support_agent_core
    from backend.tests.conftest import (
        PipelineStub,
        install_pipeline_stub,
        make_config_stub,
    )

    config = make_config_stub()
    config.tools = SimpleNamespace(enabled=["rag_search", "web_search", "reasoning"], disabled=[])
    stub = PipelineStub(config=config)
    install_pipeline_stub(monkeypatch, stub)
    seen = {}
    real_executor = __import__("backend.app.core.support_agent", fromlist=["x"]).create_executor

    def capture(cfg, registry):
        seen["disabled"] = list(cfg.tools.disabled)
        return real_executor(cfg, registry)

    monkeypatch.setattr("backend.app.core.support_agent.create_executor", capture)

    run_support_agent_core("パスワードを忘れました", use_web=use_web, do_action=False)

    assert ("web_search" in seen["disabled"]) is disabled
    # リクエストごとのコピーに対して行う（共有の設定を書き換えない）
    assert config.tools.disabled == []

# ----------------------------------------------------------------------
# Web 許可時: 採用した RAG 結果で、無条件に Web を検索しない（rag_sufficient_score）
# ----------------------------------------------------------------------
# RAG で社内ナレッジを採用したのに、無条件で Web も検索してしまう不具合を守るテスト。
#
# ## なぜ必要か
#
# `qdrant.rag_sufficient_score`（RAG が十分かのしきい値）が 0.7 だった一方で、RAG 検索ツールは
# `executor.reasoning_min_rag_score`（0.64）以上の結果を**推論に使い、出典にも載せる**。
# そのため 0.64〜0.7 の結果は「社内ナレッジとして使うのに、Web 検索も無条件で挟む」状態だった。
#
# - 0.64 は実測値（`config/grace_config.yml` のコメント: 範囲内の質問 n=12 の最小 0.6650・
#   範囲外 n=5 の最大 0.6190）。**範囲内の質問でも 0.665 まで下がる**ので、0.7 では
#   社内ナレッジで答えられる質問でも Web 検索が走る
# - 再測定 2026-10-05（n=14 / 18）でも範囲内の最小は 0.7062 と 0.7 に近く、範囲内と範囲外は重なる
#   （`config/grace_config.yml` のコメント）。しきい値 1 本では分けられないので、採用後の判断は
#   LLM の適合性チェックに任せる
# - ⚠️ grace_v2 の E2E の saas で無関係な URL が並んだ件（2026-10-04・Web OFF の修正前）の原因が
#   この帯だったとは確かめていない（素の質問の最高スコアは 0.7062。executor は planner が書き換えた query で検索する）
#
# しきい値を採用の下限にそろえると、採用した結果は LLM の適合性チェック
# （`_evaluate_rag_relevance`）が Web の要否を決める。採用できない結果（0.64 未満）は従来どおり Web へ。
#
# LLM・Qdrant・API キーには依存しない。
def _run_rag_then_reasoning(ex):
    return ex.execute(_plan([_step(1, "rag_search"), _step(2, "reasoning", depends_on=[1])]))


def test_adopted_rag_result_does_not_force_web_search(make_executor):
    """採用された（0.64 以上の）結果で、内容が質問に合っていれば Web を検索しない。"""
    ex, tools = make_executor(_CountingTool(_rag(0.665)), web_disabled=False)

    _run_rag_then_reasoning(ex)

    assert tools["web_search"].calls == 0


def test_adopted_but_irrelevant_rag_result_still_searches_web(make_executor):
    """採用されても、適合性チェックが「合っていない」と言えば Web を検索する。"""
    ex, tools = make_executor(_CountingTool(_rag(0.665)), web_disabled=False)
    ex._evaluate_rag_relevance = lambda **_kw: False

    _run_rag_then_reasoning(ex)

    assert tools["web_search"].calls == 1


def test_low_rag_score_still_searches_web(make_executor):
    ex, tools = make_executor(_CountingTool(_rag(0.5)), web_disabled=False)

    _run_rag_then_reasoning(ex)

    assert tools["web_search"].calls == 1


def test_sufficient_score_does_not_exceed_adoption_floor():
    """不変条件: 推論・出典に採用する結果で、無条件の Web 検索を起こさない。

    `rag_sufficient_score` を `reasoning_min_rag_score` より上げると、その間の結果は
    「社内ナレッジとして使うのに Web も検索する」状態に戻る。既定値と設定ファイルの両方を見る。
    """
    from grace.config import ExecutorConfig, QdrantConfig

    assert QdrantConfig().rag_sufficient_score <= ExecutorConfig().reasoning_min_rag_score

    yml = yaml.safe_load((Path(__file__).resolve().parents[2] / "config" / "grace_config.yml")
                         .read_text(encoding="utf-8"))
    assert yml["qdrant"]["rag_sufficient_score"] <= yml["executor"]["reasoning_min_rag_score"]

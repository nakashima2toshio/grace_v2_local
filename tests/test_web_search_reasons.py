"""Executor が Web 検索した**理由**を記録し、画面のログに実際の理由が出ること（バグ B）。

## なぜ必要か

`support_agent.py` は、出典に `[Web]` が 1 件でもあれば「executor が動的 Web 検索を使用
（RAG スコア不足のため）」とログに出していた。理由は確かめておらず、決め打ちだった。

- 社内検索のスコアは十分でも、LLM が「質問に合わない」と判定して Web 検索することがある
- 社内検索が失敗（例外）して Web 検索に切り替わることがある
- 0-(A) の観点で利用者が「最新ニュースを検索して」と頼んだ Web 検索（`required`）は、
  社内検索の結果と関係なく行う

どれも同じ「RAG スコア不足のため」と表示され、なぜ Web を見たのかを画面から追えなかった。
Executor が実行した Web 検索を `ExecutionResult.web_searches`（検索語・理由）に残し、
support_agent はそれをそのまま出す。

LLM・Qdrant・API キーには依存しない（`tests/test_plan_for_topic.py` の Executor を使う）。
"""
from __future__ import annotations

from backend.app.core.support_agent import SupportEvent, run_support_agent_core
from tests.test_plan_for_topic import (
    S1,
    S2,
    W1,
    W2,
    _executor,
    _plan,
    _rag,
    _reasoning,
    _web,
)


def _reasons(result):
    return [(w["query"], w["reason"]) for w in result.web_searches]


class TestExecutorRecordsWhyItSearchedTheWeb:
    def test_社内検索のスコア不足(self, monkeypatch):
        executor, _tools = _executor(monkeypatch, rag_score=0.3)

        result = executor.execute(_plan(_rag(1, S1, W1), _reasoning(2)), allow_react=False)

        [(query, reason)] = _reasons(result)
        assert query == W1
        assert "スコア" in reason and "0.30" in reason

    def test_スコアは十分だが質問に合わないとLLMが判定(self, monkeypatch):
        executor, _tools = _executor(monkeypatch, rag_score=0.8)
        executor._evaluate_rag_relevance = lambda **_kw: False

        result = executor.execute(_plan(_rag(1, S1, W1), _reasoning(2)), allow_react=False)

        [(query, reason)] = _reasons(result)
        assert query == W1
        assert "合わない" in reason
        assert "スコア不足" not in reason

    def test_利用者が最新情報を求めた観点(self, monkeypatch):
        executor, _tools = _executor(monkeypatch, rag_score=0.8)

        result = executor.execute(
            _plan(_rag(1, S2, W2), _web(2, W2, required=True), _reasoning(3)), allow_react=False)

        [(query, reason)] = _reasons(result)
        assert query == W2
        assert "利用者" in reason

    def test_社内検索が失敗(self, monkeypatch):
        executor, _tools = _executor(monkeypatch, rag_raises=RuntimeError("Qdrant down"))

        result = executor.execute(_plan(_rag(1, S1, W1), _reasoning(2)), allow_react=False)

        [(query, reason)] = _reasons(result)
        assert query == W1
        assert "失敗" in reason

    def test_Web検索しなければ空(self, monkeypatch):
        executor, _tools = _executor(monkeypatch, rag_score=0.8)

        result = executor.execute(_plan(_rag(1, S1, W1), _reasoning(2)), allow_react=False)

        assert result.web_searches == []


class TestSupportAgentLogsTheRecordedReason:
    def _logs(self, events):
        return [e.message for e in events if e.type == "log" and e.step == "execute"]

    def test_記録された理由をそのまま出し_決め打ちの理由を出さない(self, pipeline_stub):
        pipeline_stub.sources = ["https://example.com/news"]
        pipeline_stub.web_searches = [
            {"step_id": 2, "query": W2, "reason": "利用者が最新情報を求めた観点（人事）"}]
        events: list[SupportEvent] = []

        run_support_agent_core("q", emit=events.append, do_action=False)

        logs = "\n".join(self._logs(events))
        assert f"「{W2}」" in logs
        assert "利用者が最新情報を求めた観点（人事）" in logs
        assert "RAG スコア不足のため" not in logs

    def test_実行イベントにも載る(self, pipeline_stub):
        pipeline_stub.web_searches = [{"step_id": 101, "query": W1, "reason": "社内検索のスコア不足"}]
        events: list[SupportEvent] = []

        run_support_agent_core("q", emit=events.append, do_action=False)

        finished = [e for e in events
                    if e.type == "step" and e.step == "execute" and e.status == "finished"]
        assert finished[0].data["web_searches"] == pipeline_stub.web_searches


class TestReplanFallbackKeepsTheFailureVisible:
    """リプランで社内検索を Web 検索へ置き換えても、社内検索が失敗したことが見えること（バグ A）。

    `ReplanManager._apply_fallback` は失敗した rag_search を**同じステップ番号のまま** web_search に
    置き換え、説明は「[代替] 社内検索: 経緯」だけだった。画面ではそのステップが成功したように見え、
    社内検索が失敗したことも理由も残らなかった。検索語にも社内検索用の文を使っていた。
    """

    def _fallback_step(self):
        from unittest.mock import patch

        from grace.replan import ReplanContext, ReplanManager, ReplanTrigger
        from tests.test_plan_for_topic import _plan as plan_of

        with patch("grace.replan.create_planner"):
            manager = ReplanManager()
        context = ReplanContext(trigger=ReplanTrigger.STEP_FAILED, original_query="q",
                                failed_step_id=1, error_message="Qdrant に接続できません")
        new_plan = manager._apply_fallback(context, plan_of(_rag(1, S1, W1), _reasoning(2)))
        return new_plan.steps[0]

    def test_説明に失敗と理由を書く(self):
        step = self._fallback_step()

        assert step.action == "web_search"
        assert "rag_search が失敗" in step.description
        assert "Qdrant に接続できません" in step.description

    def test_Web検索語を使う(self):
        assert self._fallback_step().query == W1

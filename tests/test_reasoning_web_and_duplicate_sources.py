"""回答生成（reasoning）に渡す情報源の中身（バグ D・F）。

## D: Web 結果のタイトルが渡っていなかった・順位スコアを「信頼度」と書いていた

`ReasoningTool._build_prompt` は情報源ごとに「信頼度」「コレクション」「Q/A（本文）」「出典」を並べるが、
Web 検索の結果では次の 2 つが問題だった。

- **タイトルを渡していなかった。** Web の結果の本文は検索エンジンの抜粋（数十〜百数十字）で、
  何の記事かはタイトルにしか書かれていないことが多い。タイトルが無いと、回答生成の LLM は
  抜粋だけで記事の主題を推測することになる。
- **順位から作ったスコアを「信頼度」と書いていた。** Web のスコアは 1 位 = 1.00、最下位 = 0.50 の
  順位スコアで（`WebSearchTool._parse_to_rag_format`）、内容が正しいかとは関係ない。
  「信頼度: 1.00」と書くと、LLM は 1 位の記事を確かな情報として扱う（バグ C と同じ取り違え）。
  Web の結果には「検索順位」として出す。

## F: 同じ情報源が何度も渡っていた

`Executor._prepare_tool_kwargs` は、成功した全ステップの結果をそのまま並べていた。0-(A) の観点ごとに
社内検索するようになると、観点 2 つが同じチャンクを引くことがある。Web も、計画の Web 検索と
差し込みの Web 検索や、観点ごとの検索で同じ記事が返る。同じ情報源が 2 回並ぶと、回答生成の LLM が
その内容を重く扱い、プロンプトも長くなる。同じ情報源（出典と本文が同じもの）は 1 回だけ渡す
（`grace.executor._dedupe_sources`。grace_v2_local に先にあった実装を移植した）。
"""
from __future__ import annotations

from types import SimpleNamespace

from grace.config import GraceConfig
from grace.tools import ReasoningTool

WEB = {"score": 1.0, "collection": "web_search", "payload": {
    "question": "", "answer": "49ers は新 GM に…を指名した。", "content": "",
    "source": "https://example.com/49ers-gm", "title": "49ers name new general manager"}}
RAG = {"score": 0.85, "collection": "cc_news_2per_anthropic", "payload": {
    "question": "Jed York は何を発表した？", "answer": "GM とコーチを探すと発表した。",
    "source": "cc_news_2per.csv"}}


def _prompt(sources):
    tool = ReasoningTool.__new__(ReasoningTool)
    tool.config = SimpleNamespace(llm=SimpleNamespace(prompt_addendum="", prompt_closing=""))
    return tool._build_prompt("49ers の人事は？", None, sources)


def _block(prompt, i):
    """情報源 i の行だけを取り出す。"""
    start = prompt.index(f"--- 情報源 {i} ")
    end = prompt.find("--- 情報源 ", start + 1)
    end = prompt.find("###", start) if end == -1 else end
    return prompt[start:end]


class TestWebSourceInPrompt:
    def test_Webの結果はタイトルを渡す(self):
        assert "タイトル: 49ers name new general manager" in _block(_prompt([RAG, WEB]), 2)

    def test_Webの結果は順位スコアを信頼度と書かない(self):
        web = _block(_prompt([RAG, WEB]), 2)

        assert "信頼度" not in web
        assert "検索順位" in web

    def test_社内の結果は従来どおり類似度を出す(self):
        rag = _block(_prompt([RAG, WEB]), 1)

        assert "信頼度: 0.85" in rag
        assert "タイトル" not in rag


class TestDuplicateSourcesAreDropped:
    def _executor_kwargs(self, step_outputs):
        """成功ステップの出力を並べて、reasoning に渡す引数を作る。"""
        from grace.executor import ExecutionState, Executor
        from grace.schemas import ExecutionPlan, PlanStep, StepResult

        steps = [PlanStep(step_id=i, action="rag_search", description="x", expected_output="x")
                 for i in range(1, len(step_outputs) + 1)]
        reasoning = PlanStep(step_id=len(steps) + 1, action="reasoning", description="回答",
                             expected_output="x")
        state = ExecutionState(plan=ExecutionPlan(
            original_query="q", complexity=0.5, estimated_steps=len(steps) + 1,
            requires_confirmation=False, success_criteria="x", steps=[*steps, reasoning]))
        for step, output in zip(steps, step_outputs):
            state.step_results[step.step_id] = StepResult(
                step_id=step.step_id, status="success", output=str(output), confidence=0.8)
        executor = Executor.__new__(Executor)
        executor.config = GraceConfig()
        return executor._prepare_tool_kwargs(reasoning, state)

    def test_2つの観点が同じチャンクを引いても1回だけ渡す(self):
        kwargs = self._executor_kwargs([[RAG], [RAG, WEB]])

        assert kwargs["sources"] == [RAG, WEB]

    def test_出典が同じでも本文が違えば別の情報源(self):
        other = {**RAG, "payload": {**RAG["payload"], "answer": "別のチャンク"}}

        kwargs = self._executor_kwargs([[RAG], [other]])

        assert kwargs["sources"] == [RAG, other]

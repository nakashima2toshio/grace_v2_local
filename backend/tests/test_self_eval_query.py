# backend/tests/test_self_eval_query.py
"""ステップ信頼度の自己評価に**ユーザーの質問を渡す**ことを固定するテスト。

## 背景（grace_v2 で実測 2026-08-17 11:10 / 11:41。本リポジトリにも同じコードが残っていた）

`evaluate_with_factors` の評価基準 1 はこうなっている。

    1. 検索品質: 質問に対する回答の根拠となる情報が十分にマッチしているか。

ところがプロンプトに渡していたのは**ステップの説明**（「関連情報を検索」
「[動的挿入] RAGスコア不足のためWeb検索を実行」）だけで、**ユーザーの質問その
ものが入っていなかった**。評価器は何を探していたのか分からないまま採点していた。

実測の評価理由:

    「具体的な検索クエリが不明確なため、本当に「関連情報」が適切に検索された
      のか…判断しづらい」
    「RAGスコア不足の原因となった元の質問が空文字列（''）であるため、
      検索意図が不明確であり…」

後者の「空文字列」は評価器の作話（実際には query は渡っている）だが、
**質問が見えていないという指摘自体は正しい**。この信頼度は回答ゲートの判断
材料になるので、判定できないまま採点されるのは困る。

ここで固定すること:
  1. 質問がプロンプトに載ること
  2. 質問が無いときは見出しごと出さない（空の見出しでさらに混乱させない）
  3. ステップ固有のクエリを優先し、無ければ元の質問へ落ちること
  4. 既存の評価要素（統計・出力・目的）は落としていないこと

⚠️ LLM には接続しない。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from config import get_default_ollama_model
from grace.confidence import ConfidenceFactors, LLMSelfEvaluator

# ⚠️ 本リポジトリの LLM はローカル LLM（Ollama）。判定系も同じモデルを使う。
LIGHT_MODEL = get_default_ollama_model()
QUERY = "明日の東京の天気は？"
DESCRIPTION = "関連情報を検索"


def _evaluator():
    ev = LLMSelfEvaluator.__new__(LLMSelfEvaluator)
    ev.config = SimpleNamespace(
        llm=SimpleNamespace(light_model=LIGHT_MODEL),
    )
    ev.model_name = LIGHT_MODEL
    client = MagicMock()
    client.models.generate_content.return_value = SimpleNamespace(
        text='{"score": 0.8, "reason": "ok"}'
    )
    ev.client = client
    return ev, client


def _prompt(query=QUERY, description=DESCRIPTION):
    ev, client = _evaluator()
    ev.evaluate_with_factors(
        description=description,
        output="検索結果の本文",
        factors=ConfidenceFactors(
            search_result_count=5, search_max_score=0.8, search_avg_score=0.68,
            source_agreement=1.0, source_count=1, tool_success_rate=1.0,
        ),
        query=query,
    )
    [call] = client.models.generate_content.call_args_list
    return call.kwargs.get("contents") or call.args[0]


class TestQueryReachesThePrompt:

    def test_query_is_included(self):
        prompt = _prompt()

        assert QUERY in prompt, (
            "評価基準 1 は質問との一致を問うのに、質問がプロンプトに無い"
        )
        assert "【ユーザーの質問】" in prompt

    def test_no_query_omits_the_heading(self):
        """空の見出しでさらに混乱させない。"""
        prompt = _prompt(query="")

        assert "【ユーザーの質問】" not in prompt

    def test_step_description_is_kept(self):
        prompt = _prompt()

        assert DESCRIPTION in prompt
        assert "【ステップの目的】" in prompt

    def test_existing_sections_survive(self):
        """統計・出力・評価基準を落としていないこと。"""
        prompt = _prompt()

        for section in ("【実行結果（ツールの出力）】", "【統計データ（Factors）】",
                        "【評価基準】", "検索品質", "ソース一致度"):
            assert section in prompt


class TestExecutorPassesTheQuery:

    def test_step_query_is_preferred(self):
        """ステップ固有のクエリがあればそれを使う。"""
        from grace.confidence import ConfidenceCalculator

        calc = ConfidenceCalculator.__new__(ConfidenceCalculator)
        calc.config = SimpleNamespace(
            llm=SimpleNamespace(light_model=LIGHT_MODEL),
        )
        seen = {}

        class _Evaluator:
            def evaluate_with_factors(self, description, output, factors, query=""):
                seen["query"] = query
                return {"score": 0.8, "reason": "ok"}

        import grace.confidence as mod
        original = mod.create_llm_evaluator
        mod.create_llm_evaluator = lambda **_kw: _Evaluator()
        try:
            calc.llm_calculate(
                factors=ConfidenceFactors(is_search_step=True),
                step_description=DESCRIPTION,
                tool_output="out",
                query=QUERY,
            )
        finally:
            mod.create_llm_evaluator = original

        assert seen["query"] == QUERY

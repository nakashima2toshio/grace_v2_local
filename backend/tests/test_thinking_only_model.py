# backend/tests/test_thinking_only_model.py
"""思考しか返さないモデルと、それが引き起こす増幅ループを止めるテスト。

## 観測された事実（推測ではない）

P0-1 で入れた診断ログが原因を確定させた。空応答時のログ:

    finish_reason=length, max_tokens=4096, completion_tokens=2766,
    prompt_tokens=1330, response_format=なし,
    thinking=10007 chars (key=reasoning),
    message_keys=['reasoning', 'role']      ← content が **存在しない**

`content` というキー自体が応答に無く、生成した 10007 文字はすべて
`reasoning`（思考）に入っていた。本文には 1 文字も到達していない。

⚠️ これは以前の推測を **2 つとも否定**した:
  - 「JSON スキーマの出力が枠に収まらない」→ `response_format=なし` の
    素のテキスト生成でも同じく空。JSON は無関係だった。
  - 「出力が 1024 に収まらない」→ 512 / 4096 / 8192 のいずれでも同じ。
    枠が主因ではなく、**思考が枠を食い尽くす**のが主因だった。

## 増幅ループ（34 分の内訳）

本文が返らない ⇒ reasoning 失敗 ⇒ リプラン、が 3 回。そのたびに

  1. LLM 計画生成を試みる → 空 → 2 リトライ（1 回 140〜165 秒）→ 計 約 17 分
  2. フォールバック計画が `rag_search` を 1 本ずつ足す
     （steps=3 → 4 → 5。すべて同じクエリ・同じコレクション）

いずれも問題を解決しないので、リプラン上限まで必ず走り切る。

⚠️ 実際の Ollama サーバへは接続しない。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from grace.planner import Planner
from grace.replan import ReplanContext, ReplanManager, ReplanTrigger
from grace.schemas import ExecutionPlan, PlanStep
from helper.helper_llm import OllamaClient

# =============================================================================
# ① 思考を抑止する（reasoning_effort）
# =============================================================================

class TestReasoningEffort:

    def test_sent_by_default(self):
        """既定で思考抑止を要求すること（本文を返させるのが目的）。"""
        client, create = _client()
        client.generate_content("こんにちは")

        assert create.call_args.kwargs["reasoning_effort"] == "none"

    def test_disabled_by_env_value(self, monkeypatch):
        client, create = _client(reasoning_effort="off")
        client.generate_content("こんにちは")

        assert "reasoning_effort" not in create.call_args.kwargs

    def test_falls_back_when_server_rejects_the_param(self):
        """未対応の Ollama では自動的に外して再送すること。"""
        client, create = _client()
        create.side_effect = [
            _bad_request("unknown parameter: reasoning_effort"),
            _completion(content="やあ"),
        ]

        assert client.generate_content("こんにちは") == "やあ"
        assert create.call_count == 2
        assert "reasoning_effort" not in create.call_args.kwargs
        assert client.reasoning_effort is None, "2 回目以降は送らないこと"

    def test_does_not_retry_on_timeout(self):
        """タイムアウトまで握り潰して投げ直さないこと（無駄な 180 秒を防ぐ）。"""
        client, create = _client()
        create.side_effect = TimeoutError("Request timed out")

        with pytest.raises(TimeoutError):
            client.generate_content("こんにちは")
        assert create.call_count == 1


# =============================================================================
# ② 「思考だけ」を検出して上位へ伝える
# =============================================================================

class TestThinkingOnlyDetection:

    def test_flags_thinking_only_response(self):
        """本文 0 文字 + 思考あり = 投げ直しても無駄、と記録する。"""
        client, create = _client()
        create.return_value = _completion(content="", reasoning="考えた" * 100)

        assert client.generate_content("Q") == ""
        assert client.last_thinking_only is True

    def test_not_flagged_when_content_present(self):
        client, create = _client()
        create.return_value = _completion(content="答え", reasoning="考えた")

        client.generate_content("Q")
        assert client.last_thinking_only is False

    def test_not_flagged_when_empty_without_thinking(self):
        """思考も無い空応答は別の障害（フラグを立てない）。"""
        client, create = _client()
        create.return_value = _completion(content="")

        client.generate_content("Q")
        assert client.last_thinking_only is False

    def test_flag_is_reset_between_calls(self):
        client, create = _client()
        create.return_value = _completion(content="", reasoning="考えた")
        client.generate_content("Q")
        assert client.last_thinking_only is True

        create.return_value = _completion(content="答え")
        client.generate_content("Q")
        assert client.last_thinking_only is False


# =============================================================================
# ③ LLM 計画生成が倒れたら以降は試さない
# =============================================================================

class TestLlmPlanDisabling:

    def test_enabled_before_any_failure(self):
        planner = _planner()
        assert planner._llm_plan_disabled is False

    def test_disabled_after_llm_plan_failure(self):
        """空応答で倒れたら以降ルールベースに固定すること。"""
        planner = _planner()
        with patch.object(planner, "estimate_complexity_with_llm",
                          side_effect=RuntimeError("empty response")):
            planner._create_llm_plan("明日の東京の天気は？")

        assert planner._llm_plan_disabled is True

    def test_replan_skips_llm_after_disabling(self):
        """無効化後は context_hints があっても LLM を呼ばないこと。

        実測ではリプラン 3 回でこの経路に **約 17 分**を費やし、結果は
        3 回とも同じフォールバック計画だった。
        """
        planner = _planner()
        planner._llm_plan_disabled = True

        with patch.object(planner, "_create_llm_plan") as llm_plan:
            plan = planner.create_plan("明日の東京の天気は？",
                                       context_hints="前回 reasoning が失敗しました")

        llm_plan.assert_not_called()
        assert plan.steps, "ルールベース計画は返ること"


# =============================================================================
# ④ reasoning 失敗のリプランで検索を足さない
# =============================================================================

class TestRedundantSearchSteps:
    """steps=3 → 4 → 5 と同じ検索が積み上がっていた件。"""

    def _manager(self) -> ReplanManager:
        with patch("grace.replan.create_planner", return_value=MagicMock()):
            return ReplanManager()

    def _context(self, failed_step_id: int) -> ReplanContext:
        return ReplanContext(
            trigger=ReplanTrigger.STEP_FAILED,
            original_query="明日の東京の天気は？",
            failed_step_id=failed_step_id,
        )

    def _plan(self) -> ExecutionPlan:
        return ExecutionPlan(
            original_query="明日の東京の天気は？",
            complexity=0.5,
            estimated_steps=2,
            requires_confirmation=False,
            success_criteria="質問に回答できている",
            steps=[
                _step(1, "rag_search"),
                _step(2, "reasoning", depends_on=[1]),
            ],
        )

    def _new_steps(self) -> list[PlanStep]:
        return [
            _step(1, "rag_search"),
            _step(2, "reasoning", depends_on=[1]),
        ]

    def test_drops_search_when_reasoning_failed(self):
        plan = self._plan()
        kept = self._manager()._drop_redundant_search_steps(
            self._new_steps(), self._context(2), plan, [plan.steps[0]],
        )

        assert [s.action for s in kept] == ["reasoning"], (
            "reasoning の失敗に検索の追加は効かない（同じクエリ・同じ結果）"
        )

    def test_keeps_search_when_search_failed(self):
        """検索自体が failed なら、やり直しは正当。"""
        plan = self._plan()
        kept = self._manager()._drop_redundant_search_steps(
            self._new_steps(), self._context(1), plan, [],
        )

        assert [s.action for s in kept] == ["rag_search", "reasoning"]

    def test_keeps_search_when_nothing_searched_yet(self):
        """完了済みに検索が無いなら落とさない（情報が取れなくなる）。"""
        plan = self._plan()
        kept = self._manager()._drop_redundant_search_steps(
            self._new_steps(), self._context(2), plan, [],
        )

        assert [s.action for s in kept] == ["rag_search", "reasoning"]

    def test_keeps_all_when_everything_would_be_dropped(self):
        """全部落ちて計画が空になるくらいなら、そのまま通す。"""
        plan = self._plan()
        only_search = [_step(1, "rag_search")]
        kept = self._manager()._drop_redundant_search_steps(
            only_search, self._context(2), plan, [plan.steps[0]],
        )

        assert [s.action for s in kept] == ["rag_search"]

    def test_unknown_failed_step_is_left_alone(self):
        plan = self._plan()
        kept = self._manager()._drop_redundant_search_steps(
            self._new_steps(), self._context(99), plan, [plan.steps[0]],
        )

        assert len(kept) == 2


# =============================================================================
# helpers
# =============================================================================

def _client(**kwargs) -> tuple[OllamaClient, MagicMock]:
    """実 HTTP を持たない OllamaClient と、その create モックを返す。"""
    client = OllamaClient(**kwargs)
    create = MagicMock(return_value=_completion(content="ok"))
    client.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    return client, create


def _completion(content: str = "", reasoning: str = "") -> SimpleNamespace:
    fields = {"role": "assistant", "content": content}
    if reasoning:
        fields["reasoning"] = reasoning
    message = _Message(fields)
    return SimpleNamespace(
        choices=[SimpleNamespace(finish_reason="length", message=message)],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=200),
    )


class _Message(SimpleNamespace):
    def __init__(self, fields: dict):
        super().__init__(**fields)
        self._fields = fields

    def model_dump(self) -> dict:
        return dict(self._fields)


def _bad_request(message: str) -> Exception:
    exc = Exception(message)
    exc.status_code = 400
    return exc


def _step(step_id: int, action: str, depends_on=None) -> PlanStep:
    return PlanStep(
        step_id=step_id, action=action, description=f"{action} ステップ",
        query="明日の東京の天気は？" if action.endswith("search") else None,
        depends_on=depends_on or [], expected_output="結果",
    )


def _planner() -> Planner:
    with patch("grace.planner.create_chat_client", return_value=MagicMock()):
        return Planner()

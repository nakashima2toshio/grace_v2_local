"""
GRACE Planner Integration Tests
実際のローカル LLM（Ollama）を呼び出して Planner の動作確認を行うテスト

本リポジトリの LLM は Ollama（`config/grace_config.yml` の `llm.provider: "ollama"` →
`grace/llm_compat.create_chat_client`）。Gemini は Embedding 専用なので、
このテストの実行条件に Gemini の API キーは関係しない。

⚠️ Planner は LLM 呼び出しに失敗すると**黙って代替値へ倒れる**
（計画 → `_create_fallback_plan`）。代替値でも構造の検証は通ってしまうため、
テストは「LLM が実際に答えたこと」も確かめる。
また `create_plan` は、リプランの補足が無ければ LLM を使わない（ルールベース計画）ので、
計画のテストは `planner.force_llm_plan` を有効にした設定のコピーで回す。

[Usage]: RUN_AGENT_INTEGRATION=1 pytest -vs tests/grace/test_planner_integration.py
"""

import os
import socket
from urllib.parse import urlparse

import pytest

from config import OllamaConfig
from grace.config import get_config
from grace.planner import Planner
from grace.schemas import ExecutionPlan


def _ollama_is_live() -> bool:
    """OllamaConfig.BASE_URL のホスト・ポートへ短 timeout で接続できるか。"""
    parsed = urlparse(OllamaConfig.BASE_URL)
    try:
        with socket.create_connection(
            (parsed.hostname or "localhost", parsed.port or 11434), timeout=1.0
        ):
            return True
    except OSError:
        return False


# 実 LLM を使うので、明示的に有効化したときだけ走らせる
# （tests/grace/test_executor_integration.py と同じ規約）
@pytest.mark.skipif(
    os.getenv("RUN_AGENT_INTEGRATION") != "1",
    reason="実 Ollama を使う統合テスト。RUN_AGENT_INTEGRATION=1 で実行する",
)
@pytest.mark.skipif(
    not _ollama_is_live(),
    reason=f"Ollama ({OllamaConfig.BASE_URL}) へ接続できない。`ollama serve` を確認",
)
class TestPlannerIntegration:
    """実際のLLMを使用した統合テスト"""

    def test_create_plan_real_llm(self):
        """実際のLLMを使って計画生成ができるか確認"""
        # ⚠️ 既定では、リプランの補足が無ければルールベース計画を返し、LLM を一度も呼ばない。
        #    force_llm_plan で必ず LLM 計画生成を通す。共有の設定は汚さない。
        config = get_config().model_copy(deep=True)
        config.planner.force_llm_plan = True
        planner = Planner(config=config)

        query = "スペイン語の文法と単語はそれぞれ何語の影響を強く受けていますか？　日本語の影響は受けていますか？"
        print(f"\nSending query to LLM: {query}")

        plan = planner.create_plan(query)

        print(f"\nGenerated Plan ID: {plan.plan_id}")
        print(f"Plan JSON:\n{plan.model_dump_json(indent=2)}")

        # LLM 計画が失敗すると `_llm_plan_disabled` が立ち、代替計画が返る。
        # 代替計画でも下の構造検証は通るので、先にこれで落とす。
        assert not planner._llm_plan_disabled, (
            "LLM による計画生成に失敗し、代替計画（_create_fallback_plan）が返った"
        )

        # 結果の検証（内容は変動するので、構造が正しいかチェック）
        assert isinstance(plan, ExecutionPlan)
        assert plan.original_query == query
        assert len(plan.steps) > 0
        assert plan.complexity > 0.0

        # 最後のステップは必ず reasoning であるはず
        assert plan.steps[-1].action == "reasoning"

# backend/tests/agents/test_agent_service_paris_income.py
"""ReAct エージェントが「指定したコレクションだけを検索する」ことの統合テスト。

⚠️ **実 Ollama・実 Qdrant・登録済み `wikipedia_ja` コレクションが要る。**
既定では実行しない。走らせるときは明示的に:

    RUN_AGENT_INTEGRATION=1 uv run pytest \
      backend/tests/agents/test_agent_service_paris_income.py -q -s

## なぜ opt-in にしてあるか（2026-09-10）

移設した時点のこのファイルは **CI に置いてはいけない形**だった:

  1. `assert` が 1 つも無く、✅ / 💥 を print するだけ。**何が起きても pass** する
  2. `if not os.getenv("ANTHROPIC_API_KEY"): return` — 移植前（Anthropic）の名残。
     本リポジトリの LLM は Ollama（CLAUDE.md §3）なのでキーの有無は関係ない。
     しかも `return` なので、スキップしたときも「passed」と表示される
  3. 既定モデルが `claude-sonnet-4-6`。これを **Ollama へ投げる**ので、
     キーを持っている開発者の手元では必ず 404 になる:

         openai.NotFoundError: 404 - model 'claude-sonnet-4-6' not found

  4. 実ネットワーク呼び出しなので遅い（この 1 件でスイート全体が
     23 秒 → 約 6 分になっていた）

CI には Ollama も Qdrant も無いので緑に見えていた。**環境で結果が変わる**
テストであり、`test_chunking_model_reaches_chunker` /
`test_rag_relaxed_adoption` と同じ構図（PR #80 参照）。
"""
from __future__ import annotations

import os
import socket
from typing import Optional
from urllib.parse import urlparse

import pytest

from config import OllamaConfig, get_default_ollama_model

# ⚠️ `services.agent_service` は module 先頭で import しない。
# 依存の先に spacy がおり、**スキップされる場合でも**収集時に約 25 秒かかる。
# 実行するときだけ読み込む。

TARGET_COLLECTION = "wikipedia_ja"
QUESTION = (
    "パリ市の平均世帯所得は、フランス全体の平均と比べてどうですか？多いですか？"
    "また、日本と比較するとどうですか？"
)


def _port_is_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _ollama_is_live() -> bool:
    parsed = urlparse(OllamaConfig.BASE_URL)
    return _port_is_open(parsed.hostname or "localhost", parsed.port or 11434)


def _qdrant_is_live() -> bool:
    host = os.environ.get("QDRANT_HOST", "localhost")
    try:
        port = int(os.environ.get("QDRANT_PORT", "6333"))
    except ValueError:
        port = 6333
    return _port_is_open(host, port)


@pytest.mark.skipif(
    os.getenv("RUN_AGENT_INTEGRATION") != "1",
    reason="実 Ollama・実 Qdrant を使う統合テスト。RUN_AGENT_INTEGRATION=1 で実行する",
)
@pytest.mark.skipif(
    not _ollama_is_live(),
    reason=f"Ollama ({OllamaConfig.BASE_URL}) へ接続できない。`ollama serve` を確認",
)
@pytest.mark.skipif(
    not _qdrant_is_live(),
    reason="Qdrant (localhost:6333) へ接続できない。docker-compose を確認",
)
def test_agent_searches_only_the_selected_collection():
    """`selected_collections` で指定したコレクションだけを検索すること。

    ⚠️ モデル名は `config.py::get_default_ollama_model()` から取る。
    以前は `claude-sonnet-4-6` を既定にしており、それを Ollama へ投げて
    404 になっていた。
    """
    from services.agent_service import ReActAgent

    model_name = os.getenv("AGENT_MODEL_NAME") or get_default_ollama_model()
    agent = ReActAgent(selected_collections=[TARGET_COLLECTION], model_name=model_name)

    searched_collections: list[Optional[str]] = []
    final_answer: Optional[str] = None

    for event in agent.execute_turn(QUESTION):
        if event.get("type") == "tool_call":
            if event.get("name") == "search_rag_knowledge_base":
                searched_collections.append((event.get("args") or {}).get("collection_name"))
        elif event.get("type") == "final_answer":
            final_answer = event.get("content")

    assert searched_collections, (
        "search_rag_knowledge_base が 1 度も呼ばれていない"
        "（ツール定義がエージェントへ渡っていない可能性）"
    )
    assert set(searched_collections) == {TARGET_COLLECTION}, (
        f"指定外のコレクションを検索した: {sorted(set(searched_collections))}"
    )
    assert final_answer, "final_answer イベントが来ていない"

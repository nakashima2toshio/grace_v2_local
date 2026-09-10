# tests/services/test_agent_service.py
# [MIGRATION gemini→anthropic] genai (chats.create/send_message/function_call) ベースの
#   モックから、Anthropic (create_llm_client + generate_with_tools/ToolUseResponse) ベースへ更新。
from unittest.mock import MagicMock, patch

import pytest

from helper.helper_llm import ToolUseResponse
from services.agent_service import ReActAgent


# ---------------------------------------------------------------------------
# Anthropic Tool Use 用のレスポンスビルダー
#   generate_with_tools(...) は ToolUseResponse(text, tool_calls, stop_reason,
#   assistant_message) を返す。stop_reason=="tool_use" でツール呼び出しを検出する。
# ---------------------------------------------------------------------------
def make_text_response(text: str) -> ToolUseResponse:
    return ToolUseResponse(
        text=text,
        tool_calls=[],
        stop_reason="end_turn",
        assistant_message={"role": "assistant", "content": text},
    )


def make_tool_use_response(text: str, name: str, tool_input: dict, tool_id: str = "tool_1") -> ToolUseResponse:
    tool_calls = [{"name": name, "input": tool_input, "id": tool_id}]
    return ToolUseResponse(
        text=text,
        tool_calls=tool_calls,
        stop_reason="tool_use",
        assistant_message={"role": "assistant", "content": [{"type": "text", "text": text}]},
    )


@pytest.fixture
def mock_llm():
    """services.agent_service.create_llm_client をモックし、generate_with_tools を制御する。"""
    mock_client = MagicMock()
    with patch("services.agent_service.create_llm_client", return_value=mock_client):
        yield mock_client


@pytest.fixture
def mock_agent_tools():
    with patch("services.agent_service.search_rag_knowledge_base") as mock_search, \
         patch("services.agent_service.list_rag_collections") as mock_list:
        yield mock_search, mock_list


class TestReActAgent:

    def test_init(self, mock_llm):
        """ReActAgent の初期化（Anthropic: create_llm_client）"""
        agent = ReActAgent(selected_collections=["coll1"], model_name="claude-sonnet-4-6")

        assert agent.selected_collections == ["coll1"]
        assert agent.model_name == "claude-sonnet-4-6"
        assert agent.thought_log == []
        # Anthropic クライアントが生成され、Tool Use 定義が input_schema 形式で構築される
        assert agent.llm is mock_llm
        assert agent.tools[0]["name"] == "search_rag_knowledge_base"
        assert "input_schema" in agent.tools[0]

    def test_execute_turn_simple_answer(self, mock_llm):
        """モデルが直接回答を返すケース（ツール呼び出しなし）"""
        agent = ReActAgent(selected_collections=[], model_name="claude-sonnet-4-6")

        # ReAct: テキストのみ（end_turn）→ Reflection: Final Answer
        mock_llm.generate_with_tools.side_effect = [
            make_text_response("Thought: I know the answer.\nAnswer: The answer is 42."),
            make_text_response("Reflection complete.\nFinal Answer: The answer is 42."),
        ]

        events = list(agent.execute_turn("What is the meaning of life?"))

        event_types = [e["type"] for e in events]
        assert "log" in event_types
        assert "final_text" in event_types
        assert "final_answer" in event_types

        final_event = events[-1]
        assert final_event["type"] == "final_answer"
        assert final_event["content"] == "The answer is 42."

    def test_execute_turn_with_tool_call(self, mock_llm, mock_agent_tools):
        """ツール呼び出しを伴う execute_turn"""
        mock_search, mock_list = mock_agent_tools

        with patch.dict('services.agent_service.TOOLS_MAP', {
            'search_rag_knowledge_base': mock_search,
            'list_rag_collections': mock_list,
        }):
            agent = ReActAgent(selected_collections=["coll1"], model_name="claude-sonnet-4-6")

            # 1. tool_use → 2. end_turn(回答) → 3. Reflection
            mock_llm.generate_with_tools.side_effect = [
                make_tool_use_response(
                    "Thought: I need to search.",
                    "search_rag_knowledge_base",
                    {"query": "test query", "collection_name": "coll1"},
                ),
                make_text_response("Thought: I found it.\nAnswer: The result is X."),
                make_text_response("Final Answer: The result is X."),
            ]

            # search は cached ラッパー経由で呼ばれるためそちらをモック
            with patch("services.agent_service.search_rag_knowledge_base_cached",
                       return_value="Search Result Content") as mock_search_cached:
                events = list(agent.execute_turn("Search for test."))

            mock_search_cached.assert_called_once()
            _, kwargs = mock_search_cached.call_args
            assert kwargs["query"] == "test query"
            assert kwargs["collection_name"] == "coll1"

            types = [e["type"] for e in events]
            assert "tool_call" in types
            assert "tool_result" in types
            assert "final_answer" in types

            assert "Thought: I need to search." in agent.thought_log[0]

    def test_format_final_answer(self, mock_llm):
        agent = ReActAgent(selected_collections=[], model_name="claude-sonnet-4-6")

        assert agent._format_final_answer("Answer: Yes") == "Yes"
        assert agent._format_final_answer("Thought: Hmmm\nAnswer: Yes") == "Yes"
        assert agent._format_final_answer("Thought: Just a thought") == "Just a thought"
        assert agent._format_final_answer("考え: 日本語で") == "日本語で"
        assert agent._format_final_answer("Raw text") == "Raw text"

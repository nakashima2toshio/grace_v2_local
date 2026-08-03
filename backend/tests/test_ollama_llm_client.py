# backend/tests/test_ollama_llm_client.py
"""`helper.helper_llm.OllamaClient` の ReAct 互換性テスト。

本リポジトリの LLM をローカル（Ollama）へ移す移植で、いちばん壊れやすいのが
**ReAct ループの戻り値インターフェース**である。

`services/agent_service.py` の ReAct ループは Anthropic 版の前提で書かれている:

  - 戻り値は `ToolUseResponse`（NamedTuple）
  - ツール継続の判定は `stop_reason == "tool_use"`
  - `result.assistant_message` をそのまま会話履歴へ追記する
  - ツール結果は `{"role":"user","content":[{"type":"tool_result", ...}]}` で積む

一方 Ollama（OpenAI 互換）のネイティブ表現はこれと異なる
（`finish_reason=="tool_calls"` / `{"role":"tool","tool_call_id":...}`）。
`OllamaClient` はこの差を内部で吸収し、**呼び出しサイトを無改造で動かす**こと
を保証する。本テストはその保証が壊れていないことを検証する。

⚠️ 実際の Ollama サーバには接続しない。OpenAI SDK クライアントをスタブへ
差し替えて、送信されたリクエストと返した戻り値だけを検証する。
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from helper.helper_llm import (
    OllamaClient,
    ToolUseResponse,
    _parse_text_tool_calls,
    _resolve_schema_refs,
    _to_openai_messages,
)

# services/agent_service.py が組み立てるツール定義と同じ形（Anthropic 形式）
TOOLS = [
    {
        "name": "search_rag_knowledge_base",
        "description": "社内ドキュメントを検索する。",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    }
]


class _StubCompletions:
    """`client.chat.completions.create` のスタブ。"""

    def __init__(self, responses: List[Any]):
        self._responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("スタブの応答が尽きた（想定より多く呼ばれている）")
        return self._responses.pop(0)


def _make_response(content, tool_calls=None, finish_reason="stop"):
    """OpenAI SDK の ChatCompletion 相当を最小構成で作る。"""
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    usage = SimpleNamespace(prompt_tokens=11, completion_tokens=22)
    return SimpleNamespace(choices=[choice], usage=usage)


def _make_tool_call(call_id: str, name: str, arguments: Dict[str, Any]):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


@pytest.fixture
def client_with(monkeypatch):
    """応答列を与えて OllamaClient を組み立てるファクトリ。"""

    def _build(responses: List[Any]) -> tuple:
        # __init__ が openai.OpenAI() を呼ぶため、生成自体を差し替える
        stub = _StubCompletions(responses)
        fake_sdk_client = SimpleNamespace(chat=SimpleNamespace(completions=stub))
        monkeypatch.setattr(
            "helper.helper_llm.OpenAI", lambda **_kwargs: fake_sdk_client
        )
        return OllamaClient(default_model="gemma4:e4b"), stub

    return _build


# =============================================================================
# ReAct 戻り値インターフェース（最重要）
# =============================================================================

class TestToolUseResponseContract:
    """`generate_with_tools()` が Anthropic 版と同じ契約を守ること。"""

    def test_returns_tool_use_response_namedtuple(self, client_with):
        client, _ = client_with([_make_response("こんにちは")])
        result = client.generate_with_tools(
            messages=[{"role": "user", "content": "やあ"}], tools=[]
        )
        assert isinstance(result, ToolUseResponse)

    def test_tool_calls_normalize_finish_reason_to_tool_use(self, client_with):
        """Ollama の finish_reason="tool_calls" を "tool_use" へ正規化すること。

        agent_service.py は `result.stop_reason != "tool_use"` でループを抜ける。
        正規化を忘れると **1 度もツールを呼ばずに終了する**。
        """
        client, _ = client_with([
            _make_response(
                None,
                tool_calls=[_make_tool_call("call_1", "search_rag_knowledge_base",
                                            {"query": "住民票"})],
                finish_reason="tool_calls",
            )
        ])
        result = client.generate_with_tools(
            messages=[{"role": "user", "content": "住民票の取り方は？"}], tools=TOOLS
        )

        assert result.stop_reason == "tool_use"
        assert result.tool_calls == [
            {"name": "search_rag_knowledge_base", "input": {"query": "住民票"},
             "id": "call_1"}
        ]

    def test_plain_answer_reports_end_turn(self, client_with):
        client, _ = client_with([_make_response("回答です", finish_reason="stop")])
        result = client.generate_with_tools(
            messages=[{"role": "user", "content": "質問"}], tools=TOOLS
        )
        assert result.stop_reason == "end_turn"
        assert result.tool_calls == []
        assert result.text == "回答です"

    def test_assistant_message_is_replayable(self, client_with):
        """`assistant_message` を履歴へ積み直しても送信形式が壊れないこと。

        agent_service.py は戻り値の `assistant_message` をそのまま
        `self._messages` へ append し、次ターンでそれを渡してくる。
        """
        client, stub = client_with([
            _make_response(
                "検索します",
                tool_calls=[_make_tool_call("call_1", "search_rag_knowledge_base",
                                            {"query": "住民票"})],
                finish_reason="tool_calls",
            ),
            _make_response("最終回答"),
        ])
        first = client.generate_with_tools(
            messages=[{"role": "user", "content": "住民票の取り方は？"}], tools=TOOLS
        )

        # agent_service.py と同じ積み方を再現する
        history: List[Dict[str, Any]] = [{"role": "user", "content": "住民票の取り方は？"}]
        history.append(first.assistant_message)
        history.append({
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": first.tool_calls[0]["id"],
                "content": "検索結果テキスト",
            }],
        })

        client.generate_with_tools(messages=history, tools=TOOLS)

        sent = stub.calls[1]["messages"]
        # assistant は OpenAI 形式（tool_calls 付き）で素通しされる
        assistant = [m for m in sent if m["role"] == "assistant"][0]
        assert assistant["tool_calls"][0]["function"]["name"] == "search_rag_knowledge_base"
        # tool_result ブロックは role="tool" メッセージへ展開される
        tool_msgs = [m for m in sent if m["role"] == "tool"]
        assert tool_msgs == [
            {"role": "tool", "tool_call_id": "call_1", "content": "検索結果テキスト"}
        ]

    def test_tools_are_converted_to_openai_function_format(self, client_with):
        """Anthropic の input_schema → OpenAI の function.parameters へ変換すること。"""
        client, stub = client_with([_make_response("ok")])
        client.generate_with_tools(
            messages=[{"role": "user", "content": "q"}], tools=TOOLS
        )
        sent_tools = stub.calls[0]["tools"]
        assert sent_tools[0]["type"] == "function"
        assert sent_tools[0]["function"]["name"] == "search_rag_knowledge_base"
        assert sent_tools[0]["function"]["parameters"] == TOOLS[0]["input_schema"]
        # Ollama は max_tokens のみ対応（max_output_tokens を送ってはいけない）
        assert "max_tokens" in stub.calls[0]
        assert "max_output_tokens" not in stub.calls[0]

    def test_system_prompt_becomes_system_message(self, client_with):
        client, stub = client_with([_make_response("ok")])
        client.generate_with_tools(
            messages=[{"role": "user", "content": "q"}], tools=[], system="あなたは..."
        )
        assert stub.calls[0]["messages"][0] == {"role": "system", "content": "あなたは..."}

    def test_empty_tools_sends_no_tools_parameter(self, client_with):
        """Reflection フェーズ（tools=[]）でツールを送らないこと。"""
        client, stub = client_with([_make_response("推敲後の回答")])
        result = client.generate_with_tools(
            messages=[{"role": "user", "content": "q"}], tools=[]
        )
        assert "tools" not in stub.calls[0]
        assert result.text == "推敲後の回答"


class TestTextBasedToolCallFallback:
    """ローカルモデルがツール呼び出しをテキストで返す場合のフォールバック。"""

    def test_text_tool_call_is_recovered(self, client_with):
        """gemma4:e4b は tool_calls=None のまま本文にツール呼び出しを書くことがある。"""
        client, _ = client_with([
            _make_response(
                'Action:search_rag_knowledge_base{query:<|"|>住民票<|"|>}',
                tool_calls=None,
                finish_reason="stop",
            )
        ])
        result = client.generate_with_tools(
            messages=[{"role": "user", "content": "住民票の取り方は？"}], tools=TOOLS
        )
        assert result.stop_reason == "tool_use"
        assert result.tool_calls[0]["name"] == "search_rag_knowledge_base"
        assert result.tool_calls[0]["input"] == {"query": "住民票"}

    def test_empty_response_retries_without_tools(self, client_with):
        """tools 指定で空応答になるモデル向けに tools 無しで再試行すること。"""
        client, stub = client_with([
            _make_response(None, tool_calls=None, finish_reason="stop"),
            _make_response("再試行後の回答"),
        ])
        result = client.generate_with_tools(
            messages=[{"role": "user", "content": "q"}], tools=TOOLS
        )
        assert len(stub.calls) == 2
        assert "tools" not in stub.calls[1]
        assert result.text == "再試行後の回答"
        assert result.stop_reason == "end_turn"


# =============================================================================
# 補助関数
# =============================================================================

class TestParseTextToolCalls:

    def test_json_dict_format(self):
        parsed = _parse_text_tool_calls(
            'よし、{"name": "search_rag_knowledge_base", "parameters": {"query": "住民票"}} を使う'
        )
        assert parsed[0]["name"] == "search_rag_knowledge_base"
        assert parsed[0]["input"] == {"query": "住民票"}

    def test_action_args_format(self):
        parsed = _parse_text_tool_calls(
            'Action: search_rag_knowledge_base Args: {"query": "住民票"}'
        )
        assert parsed[0]["input"] == {"query": "住民票"}

    def test_plain_text_yields_nothing(self):
        assert _parse_text_tool_calls("これは普通の回答です。") == []


class TestResolveSchemaRefs:
    """$ref/$defs を展開しないとローカルモデルがスキーマをオウム返しする。"""

    def test_refs_are_inlined_and_defs_removed(self):
        schema = {
            "$defs": {"Step": {"type": "object",
                               "properties": {"name": {"type": "string"}}}},
            "type": "object",
            "properties": {"steps": {"type": "array",
                                     "items": {"$ref": "#/$defs/Step"}}},
        }
        flat = _resolve_schema_refs(schema)
        assert "$defs" not in flat
        assert flat["properties"]["steps"]["items"] == {
            "type": "object", "properties": {"name": {"type": "string"}}
        }

    def test_self_reference_terminates(self):
        """自己参照スキーマでも無限再帰しないこと。"""
        schema = {
            "$defs": {"Node": {"type": "object",
                               "properties": {"child": {"$ref": "#/$defs/Node"}}}},
            "$ref": "#/$defs/Node",
        }
        assert _resolve_schema_refs(schema)  # 例外を出さずに返る


class TestMessageConversion:

    def test_openai_format_messages_pass_through(self):
        messages = [
            {"role": "user", "content": "質問"},
            {"role": "assistant", "content": "回答"},
            {"role": "tool", "tool_call_id": "call_1", "content": "結果"},
        ]
        assert _to_openai_messages(messages) == messages

    def test_anthropic_blocks_are_converted(self):
        """Anthropic SDK 由来のブロック形式（オブジェクト）も変換できること。"""
        blocks = [
            SimpleNamespace(type="text", text="検索します"),
            SimpleNamespace(type="tool_use", id="call_1",
                            name="search_rag_knowledge_base", input={"query": "住民票"}),
        ]
        converted = _to_openai_messages([{"role": "assistant", "content": blocks}])
        assert converted[0]["content"] == "検索します"
        assert converted[0]["tool_calls"][0]["id"] == "call_1"
        assert json.loads(
            converted[0]["tool_calls"][0]["function"]["arguments"]
        ) == {"query": "住民票"}

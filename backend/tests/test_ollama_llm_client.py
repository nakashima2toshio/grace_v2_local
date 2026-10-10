# backend/tests/test_ollama_llm_client.py
"""`helper.helper_llm` の Ollama 向け補助関数のテスト。

`_resolve_schema_refs()` は `OllamaClient.generate_structured()` が使う（$ref/$defs を展開しないと、
ローカルモデルがスキーマ定義そのものをオウム返しする）。

かつてここには `OllamaClient.generate_with_tools()`（Tool Use。Anthropic 版と同じ `ToolUseResponse` を
返す契約）のテストもあったが、唯一の呼び出し元だった Legacy ReAct（`services/agent_service.py`）とともに
2026-10-10 に機能ごと削除した。

⚠️ 実際の Ollama サーバには接続しない。
"""
from __future__ import annotations

from helper.helper_llm import _resolve_schema_refs


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

# backend/tests/test_empty_response_diagnostics.py
"""空応答のログが「断定」ではなく「観測」を出すことを固定するテスト。

## 何を守っているのか

本文 0 文字で返ってきたとき、以前のログはこう**断定**していた:

    「モデルが出力を終端できていません。枠を上げても直らない挙動です。」

だが同一実行のログがこれを否定した:

    17:42:29  evaluate_final       → 成功（JSON+schema・枠 1024）
    17:44:04  groundedness verify  → 空  （JSON+schema・枠 1024）

同じ枠・同じ JSON モード・同じモデルで片方は通り片方は空になる。効いて
いるのは枠そのものではなく **その呼び出しが要求する出力量**だった。
「枠を上げても無駄」は誤った一般化で、しかもその誤りに気づけなかったのは
**判断材料をログに出していなかった**からである。

具体的に足りなかったもの:

1. `completion_tokens` — 本当に枠まで生成したのか（0 なら話が逆になる）
2. 思考フィールドを `reasoning_content` **1 つしか見ていなかった**。
   openai SDK の応答モデルは `extra="allow"` なので、Ollama が `thinking`
   など別キーで返していれば中身があっても「thinking=0 chars」と誤報する
3. `message` が実際に持つキー — 生成されたものがどこへ行ったのか

ここではこの 3 つが必ずログに出ること、および `length` の解釈が
`completion_tokens` と突き合わせて分岐することを固定する。

⚠️ 実際の Ollama サーバへは接続しない。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from helper.helper_llm import OllamaClient

# =============================================================================
# ① 思考フィールドは候補キーを全部見る
# =============================================================================

class TestThinkingExtraction:

    @pytest.mark.parametrize("key", ["reasoning_content", "thinking", "reasoning"])
    def test_finds_thinking_under_any_known_key(self, key):
        """提供側がどのキー名で返しても拾えること。"""
        message = SimpleNamespace(content="", **{key: "考えた内容"})
        text, found_key = OllamaClient._extract_thinking(message)

        assert text == "考えた内容"
        assert found_key == key

    def test_returns_empty_when_absent(self):
        message = SimpleNamespace(content="")
        assert OllamaClient._extract_thinking(message) == ("", None)

    def test_ignores_empty_and_non_string_values(self):
        """空文字や None を「思考あり」と誤認しないこと。"""
        message = SimpleNamespace(content="", reasoning_content="", thinking=None,
                                  reasoning=123)
        assert OllamaClient._extract_thinking(message) == ("", None)

    def test_reasoning_content_takes_precedence(self):
        message = SimpleNamespace(content="", reasoning_content="A", thinking="B")
        assert OllamaClient._extract_thinking(message)[1] == "reasoning_content"


# =============================================================================
# ② message のキー一覧を出す（中身がどこへ行ったのか分かるように）
# =============================================================================

class TestMessageKeys:

    def test_lists_populated_keys_from_pydantic_model(self):
        message = _PydanticLikeMessage({"role": "assistant", "content": "",
                                        "thinking": "xyz", "tool_calls": None})
        assert OllamaClient._message_keys(message) == ["role", "thinking"]

    def test_falls_back_to_vars_without_model_dump(self):
        message = SimpleNamespace(role="assistant", content="")
        assert OllamaClient._message_keys(message) == ["content", "role"]

    def test_survives_a_broken_model_dump(self):
        """応答形状は提供側依存。model_dump が壊れてもログを落とさない。

        フォールバックは中身の有無で絞らない（空文字の content も出す）。
        原因不明の状況で使う経路なので、持っているものは全部見せる。
        """
        message = _ExplodingMessage()
        assert OllamaClient._message_keys(message) == ["content", "role"]


# =============================================================================
# ③ length の解釈は completion_tokens と突き合わせて分岐する
# =============================================================================

class TestEmptyContentLog:
    """⚠️ 「枠を上げても直らない」と**断定しない**こと。"""

    def test_reports_completion_and_prompt_tokens(self, caplog):
        """推測でなく観測を出す。トークン数が無いと原因を特定できない。"""
        self._log(caplog, finish_reason="length", completion_tokens=1024,
                  prompt_tokens=3200, max_tokens=1024)

        message = caplog.records[0].message
        assert "completion_tokens=1024" in message
        assert "prompt_tokens=3200" in message

    def test_length_at_budget_says_output_does_not_fit(self, caplog):
        """枠まで生成した = 出力が枠に入っていない、と言う。"""
        self._log(caplog, finish_reason="length", completion_tokens=1024,
                  max_tokens=1024)

        message = caplog.records[0].message
        assert "出力が枠に収まっていません" in message
        assert "1024/1024" in message
        # 旧ログの誤った断定が復活していないこと
        assert "枠を上げても直らない" not in message

    def test_length_below_budget_points_elsewhere(self, caplog):
        """枠に届いていないのに length なら、枠ではなく提供側を疑わせる。"""
        self._log(caplog, finish_reason="length", completion_tokens=12,
                  max_tokens=1024)

        message = caplog.records[0].message
        assert "枠に届いていません" in message
        assert "12/1024" in message

    def test_thinking_branch_names_the_key(self, caplog):
        """どのキーに入っていたのかまで出す（次の調査を迷わせない）。"""
        self._log(caplog, finish_reason="length", completion_tokens=1024,
                  max_tokens=1024, thinking_key="thinking")

        message = caplog.records[0].message
        assert "key=thinking" in message
        assert "思考（thinking）だけを返し" in message

    def test_records_response_format(self, caplog):
        """JSON 制約下だったかどうかは切り分けに効く。"""
        self._log(caplog, finish_reason="length", completion_tokens=1024,
                  max_tokens=1024, response_format={"type": "json_object"})
        assert "response_format=json_object" in caplog.records[0].message

    def test_reports_no_response_format(self, caplog):
        self._log(caplog, finish_reason="stop", completion_tokens=0, max_tokens=1024)
        assert "response_format=なし" in caplog.records[0].message

    def test_lists_message_keys(self, caplog):
        self._log(caplog, finish_reason="length", completion_tokens=1024,
                  max_tokens=1024, thinking_key="thinking")
        assert "message_keys=" in caplog.records[0].message

    @staticmethod
    def _log(caplog, *, finish_reason, completion_tokens, max_tokens,
             prompt_tokens=0, thinking_key=None, response_format=None):
        fields = {"role": "assistant", "content": ""}
        if thinking_key:
            fields[thinking_key] = "考えた内容"
        choice = SimpleNamespace(
            finish_reason=finish_reason, message=_PydanticLikeMessage(fields),
        )
        caplog.clear()
        with caplog.at_level("WARNING", logger="helper.helper_llm"):
            OllamaClient._log_empty_content(
                choice, "gemma4:26b-a4b-it-qat", max_tokens,
                completion_tokens=completion_tokens,
                prompt_tokens=prompt_tokens,
                response_format=response_format,
            )
        assert caplog.records, "空応答は必ず warning を残すこと"


# =============================================================================
# helpers
# =============================================================================

class _PydanticLikeMessage(SimpleNamespace):
    """openai SDK の応答 message（model_dump を持つ）を模す。"""

    def __init__(self, fields: dict):
        super().__init__(**fields)
        self._fields = fields

    def model_dump(self) -> dict:
        return dict(self._fields)


class _ExplodingMessage(SimpleNamespace):
    def __init__(self):
        super().__init__(role="assistant", content="")

    def model_dump(self):
        raise RuntimeError("応答形状が想定外")

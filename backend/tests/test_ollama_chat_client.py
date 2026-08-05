# backend/tests/test_ollama_chat_client.py
"""`grace.llm_compat` の Ollama 経路（GRACE 本体の LLM 入口）のテスト。

GRACE 本体（planner / executor / confidence / tools）と backend の判定ゲートは
**すべて `create_chat_client(config)` を通る**。ここが Ollama を向いていなければ
「ローカル LLM へ切り替えた」ことにならないため、既定プロバイダーの解決と、
genai 互換レイヤが Ollama 向けに正しく引数を組み立てるかを検証する。

⚠️ 実際の Ollama サーバには接続しない。`OllamaGenaiClient._client`
（helper_llm.OllamaClient）をスパイへ差し替え、渡された引数だけを検証する。
"""
from __future__ import annotations

import pytest

from grace.llm_compat import (
    AnthropicGenaiClient,
    OllamaGenaiClient,
    create_chat_client,
)


def _cfg(**llm):
    from grace.config import GraceConfig, LLMConfig

    return GraceConfig(llm=LLMConfig(**llm))


class _SpyOllamaClient:
    """helper_llm.OllamaClient.generate_content のスパイ。"""

    def __init__(self, text: str = "ok"):
        self.kwargs: dict | None = None
        self.prompt: str | None = None
        self._text = text

    def generate_content(self, prompt, **kwargs):
        self.prompt = prompt
        self.kwargs = kwargs
        return self._text


def _call(config: dict | None, *, text: str = "ok", model: str = "gemma4:e4b"):
    """Ollama クライアントを差し替えて generate_content の引数を捕まえる。"""
    client = OllamaGenaiClient(default_model=model)
    spy = _SpyOllamaClient(text)
    client._client = spy
    response = client.models.generate_content(contents="q", config=config)
    return spy, response


# =============================================================================
# プロバイダー解決（ここが本体の切り替えポイント）
# =============================================================================

class TestProviderResolution:

    def test_default_is_ollama(self):
        """config なしの既定が Ollama であること。"""
        assert isinstance(create_chat_client(), OllamaGenaiClient)

    def test_config_default_is_ollama(self):
        """GraceConfig の既定（provider 未指定）が Ollama であること。"""
        assert isinstance(create_chat_client(_cfg()), OllamaGenaiClient)

    def test_config_model_is_used(self):
        client = create_chat_client(_cfg(model="qwen2.5:7b"))
        assert client._default_model == "qwen2.5:7b"

    def test_anthropic_still_reachable(self):
        """後方互換: provider を明示すれば Anthropic 経路も使える。"""
        client = create_chat_client(_cfg(provider="anthropic", model="claude-sonnet-4-6"))
        assert isinstance(client, AnthropicGenaiClient)

    def test_base_url_from_config_is_wired(self):
        """config.ollama.base_url が実際にクライアントへ渡ること。

        （読まれない設定を置かないための回帰テスト）
        """
        config = _cfg()
        config.ollama.base_url = "http://gpu-box:11434/v1"

        client = create_chat_client(config)

        assert client._base_url == "http://gpu-box:11434/v1"


# =============================================================================
# genai 互換レイヤ → Ollama の引数変換
# =============================================================================

class TestArgumentTranslation:

    def test_max_output_tokens_becomes_max_tokens(self):
        """Ollama は max_output_tokens に非対応。max_tokens へ渡すこと。"""
        spy, _ = _call({"max_output_tokens": 512, "temperature": 0.0})

        assert spy.kwargs["max_tokens"] == 512
        assert "max_output_tokens" not in spy.kwargs
        assert spy.kwargs["temperature"] == 0.0

    def test_thinking_budget_is_ignored(self):
        """Ollama に拡張思考は無い。thinking を送らず温度も落とさないこと。

        Anthropic 経路では thinking 有効時に temperature を落とす。同じ扱いを
        してしまうと、ローカル LLM で温度指定が黙って無視される。
        """
        spy, _ = _call({"temperature": 0.0, "thinking_budget_tokens": 4000})

        assert "thinking" not in spy.kwargs
        assert spy.kwargs["temperature"] == 0.0

    def test_model_override_wins(self):
        client = OllamaGenaiClient(default_model="gemma4:e4b")
        spy = _SpyOllamaClient()
        client._client = spy

        client.models.generate_content(model="llama3.2", contents="q", config=None)

        assert spy.kwargs["model"] == "llama3.2"

    def test_default_max_tokens_when_unspecified(self):
        spy, _ = _call(None)
        assert spy.kwargs["max_tokens"] == 4096

    def test_plain_text_mode_sends_no_response_format(self):
        spy, _ = _call({"max_output_tokens": 256})
        assert "response_format" not in spy.kwargs
        assert "system" not in spy.kwargs


class TestJsonMode:

    def test_json_mime_enables_json_object_mode(self):
        spy, _ = _call({"response_mime_type": "application/json"}, text='{"a": 1}')

        assert spy.kwargs["response_format"] == {"type": "json_object"}
        assert "JSON" in spy.kwargs["system"]

    def test_schema_hint_is_embedded_in_system_prompt(self):
        from pydantic import BaseModel

        class Answer(BaseModel):
            score: float

        spy, _ = _call({"response_schema": Answer}, text='{"score": 0.5}')

        assert spy.kwargs["response_format"] == {"type": "json_object"}
        assert "score" in spy.kwargs["system"]

    @pytest.mark.parametrize("raw,expected", [
        ('```json\n{"a": 1}\n```', '{"a": 1}'),
        ('了解しました。{"a": 1} です。', '{"a": 1}'),
        ('{"a": 1}', '{"a": 1}'),
    ])
    def test_fences_and_prose_are_stripped(self, raw, expected):
        """呼び出し側は response.text をそのまま json.loads する。

        ローカル LLM は JSON モードでもコードフェンスや前置きを付けることが
        あるため、ここで本体だけを取り出す。
        """
        _, response = _call({"response_mime_type": "application/json"}, text=raw)

        assert response.text == expected

    def test_plain_text_is_not_stripped(self):
        """JSON モードでないときは本文を加工しないこと。"""
        _, response = _call({"max_output_tokens": 256}, text="こんにちは。{ではない}")

        assert response.text == "こんにちは。{ではない}"


class TestResponseShape:

    def test_response_exposes_genai_compatible_attributes(self):
        """呼び出しサイトは .text / .parsed / .usage_metadata を参照する。"""
        _, response = _call(None, text="answer")

        assert response.text == "answer"
        assert response.parsed is None
        assert response.usage_metadata.prompt_token_count == 0
        assert response.usage_metadata.candidates_token_count == 0

    def test_none_content_becomes_empty_string(self):
        """本文が None でも呼び出し側の `.strip()` が落ちないこと。"""
        _, response = _call(None, text=None)

        assert response.text == ""

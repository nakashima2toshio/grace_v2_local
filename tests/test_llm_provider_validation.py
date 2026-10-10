"""GRACE 本体の LLM プロバイダ名（`config.llm.provider`）の検証を固定するテスト。

2026-09-26 まで、`grace.llm_compat.create_chat_client()` は未知のプロバイダ名を
最後の分岐で黙って Ollama にしていた。`grace_config.yml` に `provider: "anthropc"` のような
打ち間違いがあっても、エラーにならずに Ollama で走る（`llm.provider` は設定の読み込み時にも
検証されない）。`helper.helper_llm.create_llm_client()` と揃えて、未知の名前は ValueError にした。

LLM クライアントは遅延生成なので、実 Ollama / API キーは不要。
"""

import pytest

from grace.config import GraceConfig, LLMConfig
from grace.llm_compat import OllamaGenaiClient, create_chat_client


def _config(provider: str) -> GraceConfig:
    return GraceConfig(llm=LLMConfig(provider=provider))


@pytest.mark.parametrize("provider", ["anthropc", "olama", "openai"])
def test_unknown_provider_is_rejected(provider):
    """未知のプロバイダ名は、黙って Ollama へ倒さずに ValueError。"""
    with pytest.raises(ValueError, match=provider):
        create_chat_client(_config(provider))


@pytest.mark.parametrize("provider", ["ollama", "Ollama"])
def test_ollama_still_works(provider):
    """既定の ollama（大文字小文字は問わない）は従来どおり OllamaGenaiClient。"""
    assert isinstance(create_chat_client(_config(provider)), OllamaGenaiClient)


def test_no_config_defaults_to_ollama():
    """config なしは従来どおり Ollama。"""
    assert isinstance(create_chat_client(None), OllamaGenaiClient)

# backend/tests/test_no_anthropic_path.py
"""本リポジトリに Anthropic の LLM 経路が無いことを固定する。

本リポジトリの LLM は Embedding（Gemini）以外すべてローカルの Ollama である。
2026-10-08 まで `provider="anthropic"` を明示したときだけ動く「後方互換」の経路
（`helper_llm.AnthropicClient` / `llm_compat.AnthropicGenaiClient`・Claude の
モデル表・`anthropic` パッケージ）が残っており、grace_v2 のモデル変更に合わせて
その表を「揃える」誤った変更まで入った。経路ごと削除したので、戻らないよう検査する。

⚠️ Qdrant のコレクション名（`*_anthropic`）は grace_v2 と共用している実データの
名前なので対象外（変えると両リポジトリの検索が壊れる）。
"""
from __future__ import annotations

import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_helper_llm_rejects_anthropic_provider():
    from helper.helper_llm import create_llm_client

    with pytest.raises(ValueError, match="未知の LLM プロバイダ"):
        create_llm_client("anthropic")


def test_chat_client_rejects_anthropic_provider():
    from grace.config import GraceConfig, LLMConfig
    from grace.llm_compat import create_chat_client

    for name in ("anthropic", "claude"):
        with pytest.raises(ValueError, match="未知の LLM プロバイダ"):
            create_chat_client(GraceConfig(llm=LLMConfig(provider=name)))


def _model_tables():
    import config
    from helper import helper_llm
    from services import token_service

    return {
        "config.ModelConfig.AVAILABLE_MODELS": config.ModelConfig.AVAILABLE_MODELS,
        "config.ModelConfig.MODEL_PRICING": config.ModelConfig.MODEL_PRICING,
        "config.ModelConfig.MODEL_LIMITS": config.ModelConfig.MODEL_LIMITS,
        "helper_llm.LLM_MODELS": helper_llm.LLM_MODELS,
        "helper_llm.LLM_PRICING": helper_llm.LLM_PRICING,
        "helper_llm.LLM_LIMITS": helper_llm.LLM_LIMITS,
        "token_service.MODEL_ENCODINGS": token_service.MODEL_ENCODINGS,
        "token_service.LLM_PRICING": token_service.LLM_PRICING,
        "token_service.MODEL_LIMITS": token_service.MODEL_LIMITS,
    }


@pytest.mark.parametrize("name", list(_model_tables()))
def test_model_tables_have_no_claude_models(name):
    table = _model_tables()[name]
    claude = [m for m in table if str(m).startswith("claude")]
    assert claude == [], f"{name} に Claude のモデル名が残っている: {claude}"


def test_anthropic_package_is_not_a_dependency():
    for rel in ("pyproject.toml", "requirements.txt", ".github/workflows/ci.yml"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        lines = [
            line for line in text.splitlines()
            if "anthropic" in line.lower() and not line.lstrip().startswith("#")
        ]
        assert lines == [], f"{rel} が anthropic パッケージを入れている: {lines}"


def _source_files():
    skip = {".venv", ".git", "node_modules", "logs", "tests"}
    for path in ROOT.rglob("*.py"):
        if skip.intersection(path.relative_to(ROOT).parts):
            continue
        yield path


def test_no_source_imports_anthropic():
    offenders = []
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(n == "anthropic" or n.startswith("anthropic.") for n in names):
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], f"anthropic を import している: {offenders}"

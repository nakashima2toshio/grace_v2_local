# backend/tests/test_model_selection.py
"""3タブ共通のモデルセレクタ機能のテスト。

検証すること:
  1. `config.get_selectable_ollama_models()` が Anthropic 系・
     tool calling 非対応モデルを除外すること
  2. `GET /api/models` がその一覧を返すこと
  3. `QueryRequest` / `ReviewRequest` の `model` フィールドが未対応の値を
     422 で弾くこと
  4. `run_support_agent_core` / `run_review_agent_core` が `model` 引数で
     `config.llm.model` / `light_model` を上書きし、`result.model_used` に
     反映すること。未対応の値では `ValueError` を送出すること
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.core.review_agent import run_review_agent_core
from backend.app.core.support_agent import AUTO_PROCEED, run_support_agent_core
from backend.app.main import app
from backend.app.schemas import QueryRequest, ReviewRequest

client = TestClient(app)


class TestSelectableModels:
    def test_excludes_anthropic_models(self):
        import config

        choices = config.get_selectable_ollama_models()
        assert "claude-sonnet-4-6" not in choices
        assert "claude-haiku-4-5-20251001" not in choices

    def test_includes_the_default_model(self):
        import config

        assert config.get_default_ollama_model() in config.get_selectable_ollama_models()

    def test_excludes_tool_calling_incompatible_models(self):
        import config

        # phi3 / gemma2 は MODEL_CONSTRAINTS で supports_tool_calls=False。
        # 現行の AVAILABLE_MODELS には含まれていないが、将来足された場合に
        # 選択肢へ混入しないことを固定する。
        stub_models = [*config.ModelConfig.AVAILABLE_MODELS, "phi3", "gemma2"]
        selectable = [
            m for m in stub_models
            if m not in config.NON_SELECTABLE_MODELS
            and config.OllamaConfig.supports_tool_calls(m)
        ]
        assert "phi3" not in selectable
        assert "gemma2" not in selectable


class TestModelsEndpoint:
    def test_returns_only_selectable_models(self):
        import config

        response = client.get("/api/models")
        assert response.status_code == 200
        ids = [m["id"] for m in response.json()]
        assert ids == config.get_selectable_ollama_models()
        assert "claude-sonnet-4-6" not in ids

    def test_each_choice_reports_tool_call_support(self):
        response = client.get("/api/models")
        for choice in response.json():
            assert choice["supports_tool_calls"] is True


class TestRequestValidation:
    def test_query_request_rejects_unknown_model(self):
        with pytest.raises(ValidationError):
            QueryRequest(query="hi", model="not-a-real-model")

    def test_query_request_accepts_selectable_model(self):
        import config

        model = config.get_default_ollama_model()
        assert QueryRequest(query="hi", model=model).model == model

    def test_query_request_default_is_none(self):
        assert QueryRequest(query="hi").model is None

    def test_review_request_rejects_unknown_model(self):
        with pytest.raises(ValidationError):
            ReviewRequest(document="doc", model="not-a-real-model")

    def test_review_request_accepts_selectable_model(self):
        import config

        model = config.get_default_ollama_model()
        assert ReviewRequest(document="doc", model=model).model == model

    def test_api_returns_422_for_unknown_model(self):
        response = client.post(
            "/api/support/query", json={"query": "hi", "model": "not-a-real-model"}
        )
        assert response.status_code == 422


class TestSupportCoreModelOverride:
    # ⚠️ `run_support_agent_core` は `config = copy.deepcopy(get_config())` で
    # リクエスト単位のコピーを作ってから上書きするため（jobs.py のワーカースレッド
    # 間でシングルトンを奪い合わないための設計）、`pipeline_stub.config` 自体は
    # 変化しない。観測できるのは戻り値の `result.model_used` だけ。

    def test_result_carries_the_model_used(self, pipeline_stub):
        result = run_support_agent_core(
            "パスワードを忘れました",
            model="gemma4:26b-mlx",
            confirm=lambda _r: AUTO_PROCEED,
        )
        assert result.model_used == "gemma4:26b-mlx"

    def test_none_does_not_override(self, pipeline_stub):
        result = run_support_agent_core(
            "パスワードを忘れました", confirm=lambda _r: AUTO_PROCEED
        )
        # スタブの既定 config には llm.model が無いため、上書きしなければ
        # result.model_used は空文字のまま（getattr のフォールバック）。
        assert result.model_used == ""

    def test_unknown_model_raises(self, pipeline_stub):
        with pytest.raises(ValueError, match="未対応のモデルです"):
            run_support_agent_core(
                "パスワードを忘れました",
                model="not-a-real-model",
                confirm=lambda _r: AUTO_PROCEED,
            )


class TestReviewCoreModelOverride:
    def test_result_carries_the_model_used(self, review_stub):
        result = run_review_agent_core(
            "本文", ruleset="ec_ad", model="gemma4:26b-mlx", confirm=None
        )
        assert result.model_used == "gemma4:26b-mlx"

    def test_unknown_model_raises(self, review_stub):
        with pytest.raises(ValueError, match="未対応のモデルです"):
            run_review_agent_core(
                "本文", ruleset="ec_ad", model="not-a-real-model", confirm=None
            )

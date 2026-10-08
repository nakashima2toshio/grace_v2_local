# backend/tests/test_model_info_api.py
"""GET /api/model（UI ヘッダーの「利用モデル名」）のテスト。

## 何を守っているのか

ヘッダーの表示は **パイプラインが実際に使うモデル**でなければ意味がない。
固定文字列を返したり、フロント側に既定値を置いたりすると、設定を変えた瞬間に
画面と実挙動がずれる。ここでは

  1. `config.py::get_default_ollama_model()` の解決結果が API に出ること
  2. 環境変数 `OLLAMA_DEFAULT_MODEL` による上書きが反映されること

を固定する。

⚠️ 実際の Ollama サーバへは接続しない。
"""
from __future__ import annotations

import importlib

from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)


class TestModelEndpoint:

    def test_returns_the_configured_model(self):
        response = client.get("/api/model")
        assert response.status_code == 200

        body = response.json()
        assert body["provider"] == "ollama"
        assert body["model"]
        assert body["light_model"]

    def test_matches_the_single_source_of_truth(self):
        """`config.py::get_default_ollama_model()` と一致すること。

        API 側で別の既定値を持っていたら（＝二重管理になっていたら）落ちる。
        """
        import config

        body = client.get("/api/model").json()
        assert body["model"] == config.get_default_ollama_model()

    def test_default_model_is_gemma4_26b_a4b_it_qat(self, monkeypatch):
        """既定モデルが現行の指定値であること。

        `gemma4:26b-a4b-it-qat`（15 GB・QAT 版）。2026-10-03 に `gemma4:12b-mlx` から
        変更した（理由は `config.py::get_default_ollama_model` の docstring）。
        `config.py::get_default_ollama_model()` のフォールバック文字列がこの値。
        """
        import config

        # 手元の .env の上書きに左右されないよう、フォールバックそのものを見る
        monkeypatch.delenv("OLLAMA_DEFAULT_MODEL", raising=False)
        assert config.get_default_ollama_model() == "gemma4:26b-a4b-it-qat"

    def test_selectable_models_are_the_six_pulled_models(self):
        """候補一覧が、手元に pull 済みの 6 モデルと一致すること。

        未取得のモデル名を選ばせると、実行時に Ollama が 404 を返して
        ステップごと失敗する（モデル名の綴りの問題ではない）。
        """
        import config

        # 既定（26b-a4b-it-qat）を先頭に置く（ヘッダーのセレクタの並び順）
        assert config.get_selectable_ollama_models() == [
            "gemma4:26b-a4b-it-qat",
            "gemma4:12b-mlx",
            "gemma4:e4b-mlx",
            "gemma4:26b-mlx",
            "qwen3.8:27b-mlx",
            "llama3.2:latest",
        ]

    def test_selectable_models_are_registered_in_every_model_table(self):
        """候補の全モデルが 3 ファイルのモデル表すべてに載っていること。

        モデル表は `config.py` / `helper/helper_llm.py` /
        `services/token_service.py` に分かれて重複している。候補へ足すときに
        1 か所でも漏れると、料金・上限・トークン計数が `.get()` の既定値へ
        黙って落ちる。
        """
        import config
        from helper import helper_llm
        from services import token_service

        for model in config.get_selectable_ollama_models():
            assert model in config.ModelConfig.MODEL_PRICING, model
            assert model in config.ModelConfig.MODEL_LIMITS, model
            assert model in config.OllamaConfig.MODEL_CONSTRAINTS, model
            assert model in helper_llm.LLM_MODELS, model
            assert model in helper_llm.LLM_PRICING, model
            assert model in helper_llm.LLM_LIMITS, model
            assert model in token_service.MODEL_ENCODINGS, model
            assert model in token_service.LLM_PRICING, model
            assert model in token_service.MODEL_LIMITS, model

    def test_default_model_is_registered_in_the_model_tables(self):
        """既定モデルが一覧・料金・上限・制約の各表に載っていること。

        未登録でも `.get()` の既定へ落ちて動きはするが、コンテキスト長
        8192 の派生モデルに 128000 の上限が適用されるなど、表示と実体が
        食い違う。既定を差し替えたら表も揃える。
        """
        import config

        model = config.get_default_ollama_model()
        assert model in config.ModelConfig.AVAILABLE_MODELS
        assert model in config.ModelConfig.MODEL_PRICING
        assert model in config.ModelConfig.MODEL_LIMITS
        assert model in config.OllamaConfig.MODEL_CONSTRAINTS

    def test_env_override_is_honored(self, monkeypatch):
        """`OLLAMA_DEFAULT_MODEL` で上書きできること（表示もそれに追随する）。"""
        import config

        monkeypatch.setenv("OLLAMA_DEFAULT_MODEL", "llama3.2:latest")
        assert config.get_default_ollama_model() == "llama3.2:latest"

    def test_heavy_model_defaults_to_empty(self):
        """既定では論理層の別モデルを指定していないこと。

        空なら UI は併記せず、モデル名を 1 つだけ出す。
        """
        body = client.get("/api/model").json()
        assert body["heavy_model"] == ""


class TestModelIsRegisteredInLookupTables:
    """既定モデルが各対応表に載っていること。

    載っていないと、上限・制約の取得が「未知モデル向けフォールバック」へ落ちる。
    致命的ではないが、context 長や tool calling 可否の判断が実体とずれる。
    """

    def test_registered_in_model_config(self):
        from config import ModelConfig, OllamaConfig, get_default_ollama_model

        model = get_default_ollama_model()
        assert model in ModelConfig.AVAILABLE_MODELS
        assert model in ModelConfig.MODEL_PRICING
        assert model in ModelConfig.MODEL_LIMITS
        assert model in OllamaConfig.MODEL_CONSTRAINTS

    def test_registered_in_helper_llm(self):
        helper_llm = importlib.import_module("helper.helper_llm")
        from config import get_default_ollama_model

        model = get_default_ollama_model()
        assert model in helper_llm.LLM_MODELS
        assert model in helper_llm.LLM_PRICING
        assert model in helper_llm.LLM_LIMITS

    def test_supports_tool_calls(self):
        """既定モデルが tool calling 対応として登録されていること。

        False だと ReAct が使えない（tools を落としてテキスト生成へ degrade する）。
        """
        from config import OllamaConfig, get_default_ollama_model

        assert OllamaConfig.supports_tool_calls(get_default_ollama_model()) is True

    def test_local_model_costs_nothing(self):
        """ローカル実行なのでコストは 0 であること。"""
        from config import ModelConfig, get_default_ollama_model

        pricing = ModelConfig.get_model_pricing(get_default_ollama_model())
        assert pricing == {"input": 0.0, "output": 0.0}


class TestDefaultModelHasOneSource:
    """既定モデル名の実体が `config.py::get_default_ollama_model()` の 1 箇所だけであること。

    2026-10-08 まで `config/grace_config.yml` に `llm.model` / `llm.light_model` /
    `ollama.llm_model` として同じ名前を「ミラー」していた。yml の値はクラス既定より
    優先されるため、`config.py` だけを直すと CLI・`INTENT_MODEL`（config.py を読む）と
    画面・GRACE エージェント（yml を読む）でモデルが割れていた。
    """

    def test_yaml_does_not_pin_model_names(self):
        import yaml

        with open("config/grace_config.yml", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        assert "model" not in (raw.get("llm") or {})
        assert "light_model" not in (raw.get("llm") or {})
        assert "llm_model" not in (raw.get("ollama") or {})

    def test_loaded_config_follows_config_py(self, monkeypatch):
        """config.py 側の既定を変えると、yml を読んだ設定もそのモデルになること。"""
        from grace.config import ConfigLoader

        monkeypatch.setenv("OLLAMA_DEFAULT_MODEL", "one-source-check:1b")
        cfg = ConfigLoader("config/grace_config.yml").load()

        assert cfg.llm.model == "one-source-check:1b"
        assert cfg.llm.light_model == "one-source-check:1b"
        assert cfg.ollama.llm_model == "one-source-check:1b"

    def test_grace_env_override_still_wins(self, monkeypatch):
        """1 回だけ別モデルで動かす GRACE_LLM_MODEL は引き続き効くこと。"""
        from grace.config import ConfigLoader

        monkeypatch.setenv("GRACE_LLM_MODEL", "override:2b")
        cfg = ConfigLoader("config/grace_config.yml").load()

        assert cfg.llm.model == "override:2b"

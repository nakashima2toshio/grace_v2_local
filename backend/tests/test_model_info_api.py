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

    def test_default_model_is_gemma4_e4b_ctx8k(self):
        """既定モデルが現行の指定値であること。

        ⚠️ `gemma4-e4b-ctx8k` は `ollama pull` できる公開モデルではなく、
           `gemma4:e4b` から num_ctx を 8192 へ広げて作る派生モデル
           （`ollama create`）。Ollama 既定の num_ctx 4096 だと、プロンプト
           2163 トークンに対して生成へ 1933 トークンしか残らず、思考で
           使い切って本文が 0 文字になる。
        """
        import config

        assert config.get_default_ollama_model() == "gemma4-e4b-ctx8k"

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

        monkeypatch.setenv("OLLAMA_DEFAULT_MODEL", "llama3.2")
        assert config.get_default_ollama_model() == "llama3.2"

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


class TestGraceConfigMirrorsTheDefault:
    """`config/grace_config.yml` のミラー値が config.py と揃っていること。

    yml の冒頭方針が「記載値は現行の既定値と一致させる（差分＝意図的な変更）」
    なので、ズレたら方針違反として検出する。
    """

    def test_yaml_mirrors_config_py(self):
        import yaml

        from config import get_default_ollama_model

        with open("config/grace_config.yml", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        expected = get_default_ollama_model()
        assert raw["llm"]["model"] == expected
        assert raw["llm"]["light_model"] == expected
        assert raw["ollama"]["llm_model"] == expected

"""E2E の事前確認（`conftest._preflight`）のテスト。**API を呼ばないので CI でも走る。**

`_preflight` は `GRACE_E2E=1` と Ollama が揃った Mac でしか動かないので、
中のコードが壊れていても CI でもクラウド VM でも気づけない。

実測 2026-10-04（Mac）: grace_v2 から持ち込んだ `ModelConfig.EMBEDDING_DIMS` は
本リポジトリに無く（次元は `GeminiConfig.EMBEDDING_DIMS`）、AttributeError が
「キーが無効か、ネットワークで拒否」と表示されて 6 件すべてが ERROR になった。
Ollama と Embedding をスタブにして、事前確認そのものが通ることを確かめる。
"""

from types import SimpleNamespace

import pytest

from backend.tests.e2e import conftest as e2e_conftest
from config import GeminiConfig, get_default_ollama_model


@pytest.fixture
def stub_services(monkeypatch):
    import httpx

    import qdrant_client_wrapper

    def install(dims=GeminiConfig.EMBEDDING_DIMS, model=None):
        listed = {"data": [{"id": model or get_default_ollama_model()}]}
        monkeypatch.setattr(httpx, "get", lambda *_a, **_kw: SimpleNamespace(json=lambda: listed))
        monkeypatch.setattr(qdrant_client_wrapper, "embed_query", lambda _text: [0.0] * dims)

    monkeypatch.delenv("GRACE_E2E_MODEL", raising=False)
    return install


def test_preflight_passes_when_ollama_and_embedding_work(stub_services):
    stub_services()

    e2e_conftest._preflight()


def test_preflight_fails_when_model_is_not_pulled(stub_services):
    stub_services(model="other:latest")

    with pytest.raises(pytest.fail.Exception, match="pull されていない"):
        e2e_conftest._preflight()


def test_preflight_reports_wrong_dimensions_as_such(stub_services):
    """次元違いを「キーが無効」と取り違えて表示しない。"""
    stub_services(dims=768)

    with pytest.raises(pytest.fail.Exception, match="次元が 768") as exc:
        e2e_conftest._preflight()
    assert "キーが無効" not in str(exc.value)


@pytest.mark.parametrize("override", [None, "override-model"])
def test_report_records_the_model_actually_used(override):
    """レポートには実際のモデル名を残す（以前は「(config llm.model)」としか残らなかった）。"""
    from grace.config import get_config

    llm = get_config().llm
    models = e2e_conftest._resolved_models({"model": override})

    assert models == {"model": override or llm.model, "light_model": override or llm.light_model}
    assert "(" not in models["model"]

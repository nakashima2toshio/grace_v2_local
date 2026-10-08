"""後方互換の Anthropic 経路の表に Claude Haiku 5.5（`claude-haiku-5-5`）が載っていることを固定するテスト。

本リポジトリの LLM はすべて Ollama で、Anthropic のモデル名は `provider="anthropic"` を
明示したときだけ使う後方互換（grace_v2 との A/B 用）である。grace_v2 は 2026-10-08 に
軽量モデルを Haiku 4.5 から Haiku 5.5 へ切り替えたので、こちらの表も揃える。

⚠️ Anthropic のモデル名は画面の選択肢に出さない（`NON_SELECTABLE_MODELS`）。UI のリクエストは
`provider="ollama"` のまま `model` だけを差し替えるので、選ばせるとローカル Ollama に
存在しないモデル名を投げて失敗する。
"""

import importlib

import pytest

HAIKU_55 = "claude-haiku-5-5"


def test_haiku_5_5_is_known_but_not_selectable():
    import config

    assert HAIKU_55 in config.ModelConfig.AVAILABLE_MODELS
    assert HAIKU_55 in config.NON_SELECTABLE_MODELS
    assert HAIKU_55 not in config.get_selectable_ollama_models()


def test_haiku_5_5_rejects_temperature():
    """Haiku 5.5 は temperature が 1 以外 400（grace_v2 の NO_TEMPERATURE_MODELS と同じ）。"""
    import config

    assert not config.supports_temperature(HAIKU_55)


@pytest.mark.parametrize(
    "table_path",
    [
        "config.ModelConfig.MODEL_PRICING",
        "config.ModelConfig.MODEL_LIMITS",
        "helper.helper_llm.LLM_PRICING",
        "helper.helper_llm.LLM_LIMITS",
        "services.token_service.LLM_PRICING",
        "services.token_service.MODEL_LIMITS",
    ],
)
def test_haiku_5_5_has_rows_in_every_model_table(table_path):
    """表に無いと `.get()` が汎用の既定値へ黙って落ちる。"""
    # 最後の要素（と ModelConfig のようなクラス名）以外がモジュール。長い方から import を試す
    parts = table_path.split(".")
    for cut in range(len(parts) - 1, 0, -1):
        try:
            obj = importlib.import_module(".".join(parts[:cut]))
        except ModuleNotFoundError:
            continue
        for attr in parts[cut:]:
            obj = getattr(obj, attr)
        break
    assert HAIKU_55 in obj, f"{table_path} に {HAIKU_55} の行が無い"

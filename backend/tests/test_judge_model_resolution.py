# backend/tests/test_judge_model_resolution.py
"""判定系が使うモデル名が**設定（yml）から解決される**ことを固定するテスト。

## 背景（実測）

2026-08-17 02:12 の実行ログでは、同じリクエストの中でモデル名が食い違っていた:

    grace.planner    - Planner initialized with model: gemma4-e4b-ctx8k
    grace.confidence - GroundednessVerifier initialized with model: gemma4-e4b-ctx8k
    [no-info] 実質回答判定（gemma4:e4b）: 判定なし（…）      ← ここだけ違う

原因は**解決経路が 2 本ある**こと:

| 対象 | 解決経路 |
|---|---|
| planner / reasoning / groundedness | `grace/config.py` → `config/grace_config.yml` の `llm.model` / `llm.light_model` |
| 判定系（意図分類・情報なし判定） | `verticals.INTENT_MODEL` = `config.py::get_default_ollama_model()`（環境変数 `OLLAMA_DEFAULT_MODEL` かフォールバック文字列を **import 時に**畳み込む） |

`INTENT_MODEL` は yml を一切見ないので、

- yml の `light_model` を書き換えても判定系には効かない
- 環境変数を設定すると判定系だけが動き、yml 側は動かない

という食い違いが起きる。実害は `judges.enabled=true` に戻したときに出る:
派生元の `gemma4:e4b` は `num_ctx` が既定の 4096 で、8192 へ広げた
`gemma4-e4b-ctx8k` とは別物である。判定系のプロンプトは回答本文を丸ごと含むため、
4096 だと枠を使い切って本文 0 文字（空応答）になりうる。

ここで固定すること:
  1. 判定系のモデルは config（yml）の `llm.light_model` から解決されること
  2. config から解決できないときだけ `INTENT_MODEL` へ落ちること
  3. Support / Review の 4 つの判定器すべてが同じ解決を使うこと
  4. ログに出るモデル名が**実際に呼ばれたモデル**と一致すること

⚠️ LLM には接続しない（`create_chat_client` を差し替える）。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.app.core.gates import (
    create_intent_classifier,
    create_no_info_judge,
    judge_model,
)
from backend.app.core.review_gates import (
    create_mention_classifier,
    create_vacuous_judge,
)
from backend.app.core.support_agent import (
    AUTO_PROCEED,
    SupportEvent,
    run_support_agent_core,
)
from backend.app.core.verticals import INTENT_MODEL

YML_MODEL = "gemma4-e4b-ctx8k"      # grace_config.yml の light_model 相当
STALE_MODEL = "gemma4:e4b"          # 環境変数だけを見た INTENT_MODEL 相当（num_ctx 4096）


def collect(events):
    return lambda e: events.append(e)


def _config(light_model=YML_MODEL, enabled=True):
    return SimpleNamespace(
        llm=SimpleNamespace(light_model=light_model),
        judges=SimpleNamespace(enabled=enabled),
    )


# =============================================================================
# ① 解決規則
# =============================================================================

class TestJudgeModelResolution:

    def test_config_wins(self):
        assert judge_model(_config()) == YML_MODEL

    def test_falls_back_when_config_has_no_llm(self):
        """`llm` を持たないスタブ config でも落ちないこと。"""
        assert judge_model(SimpleNamespace()) == INTENT_MODEL

    def test_falls_back_when_light_model_is_empty(self):
        assert judge_model(_config(light_model="")) == INTENT_MODEL

    def test_falls_back_when_light_model_is_none(self):
        assert judge_model(_config(light_model=None)) == INTENT_MODEL

    def test_a_different_config_value_is_honoured(self):
        """yml を書き換えたら判定系にも効くこと（これが効かないのが元の不具合）。"""
        assert judge_model(_config(light_model="qwen2.5:7b")) == "qwen2.5:7b"


# =============================================================================
# ② 4 つの判定器すべてが config のモデルを呼ぶ
# =============================================================================

class TestAllJudgesUseTheResolvedModel:

    @pytest.mark.parametrize(
        "factory, call",
        [
            (create_intent_classifier, lambda j: j("返品したい")),
            (create_no_info_judge, lambda j: j("質問", "回答")),
            (create_mention_classifier, lambda j: j("業界No.1です")),
            (create_vacuous_judge, lambda j: j("特に問題ありません")),
        ],
    )
    def test_generate_content_is_called_with_the_config_model(
        self, factory, call, monkeypatch
    ):
        client = MagicMock()
        client.models.generate_content.return_value = SimpleNamespace(text="answered")
        monkeypatch.setattr(
            "grace.llm_compat.create_chat_client", lambda _c: client
        )

        call(factory(_config(light_model="qwen2.5:7b")))

        [kwargs] = [c.kwargs for c in client.models.generate_content.call_args_list]
        assert kwargs["model"] == "qwen2.5:7b", (
            f"{factory.__name__} が config のモデルを使っていない"
            "（INTENT_MODEL に固定されている）"
        )


# =============================================================================
# ③ ログのモデル名が実体と一致する
# =============================================================================

class TestLoggedModelMatchesTheRealOne:

    def _judge_log(self, events):
        return [e.message for e in events
                if e.type == "log" and "実質回答判定" in e.message]

    def test_log_shows_the_configured_model(self, pipeline_stub, monkeypatch):
        """表示と実体がずれると原因調査が空振りするので、config の値を出す。"""
        monkeypatch.setattr(
            "backend.app.core.support_agent.judge_model", lambda _c: "qwen2.5:7b"
        )
        pipeline_stub.sources = ["https://weather.yahoo.co.jp/weather/jp/13/"]
        events: list[SupportEvent] = []
        run_support_agent_core(
            "明日の東京の天気は？", emit=collect(events),
            confirm=lambda _r: AUTO_PROCEED,
        )

        [entry] = self._judge_log(events)
        assert "qwen2.5:7b" in entry
        assert STALE_MODEL not in entry

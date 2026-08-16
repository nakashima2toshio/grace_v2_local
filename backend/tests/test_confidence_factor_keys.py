# backend/tests/test_confidence_factor_keys.py
"""検索ツールが返す統計の **キー名** を固定するテスト。

## 背景（実測）

`Executor._build_confidence_factors` はこう読む:

    search_max_score      = factors.get("max_score", factors.get("avg_score", 0.0))
    search_score_variance = factors.get("score_variance", 1.0)

キー名が違うツールがあっても例外にならず、**黙って既定値へ落ちる**。
`WebSearchTool` は `top_score` / `score_spread` を返していたため、Web
ステップだけがこう評価されていた:

    Initial factors : {'avg_score': 0.6, 'top_score': 1.0, 'score_spread': 0.8}
    ConfidenceFactors: search_max_score=0.6      ← avg が入る（実際は 1.0）
                       search_score_variance=1.0 ← 既定（実際は 0.02）

最高スコアが平均に潰れ、ばらつきは常に最悪値。結果として Web ステップの
信頼度が不当に低く出る（実測の `[CONFIRM] 66.6%`）。RAG 側は正準名を
返していたので、**Web だけが静かに壊れていた**。

ここでは 2 つを固定する:
  1. Web ツールが正準キー（`max_score` / `score_variance`）を返すこと
  2. 正準キーが欠けたら **warning が出る**こと（次の不一致を沈黙させない）

⚠️ ネットワークにも LLM にも接続しない。
"""
from __future__ import annotations

import pytest

from grace.executor import Executor
from grace.schemas import PlanStep
from grace.tools import RAGSearchTool, WebSearchTool

# 実測の Web 検索スコア（SerpAPI の順位由来: 1.0, 0.9, … 0.2）
MEASURED_WEB_SCORES = [1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2]


# =============================================================================
# ① Web ツールが正準キーを返す
# =============================================================================

class TestWebFactorsUseCanonicalKeys:

    def test_emits_max_score(self):
        factors = _web_factors(MEASURED_WEB_SCORES)
        assert factors["max_score"] == pytest.approx(1.0), (
            "executor が読むのは max_score。top_score だけでは avg に潰れる"
        )

    def test_emits_score_variance(self):
        factors = _web_factors(MEASURED_WEB_SCORES)
        assert factors["score_variance"] == pytest.approx(0.06666, rel=1e-3)
        assert factors["score_variance"] != 1.0, "既定値のままでは意味がない"

    def test_max_score_is_not_the_average(self):
        """実測の壊れ方（1.0 が 0.6 に潰れる）を直接固定する。"""
        factors = _web_factors(MEASURED_WEB_SCORES)
        assert factors["max_score"] != factors["avg_score"]

    def test_keeps_legacy_keys_for_logs(self):
        """`top_score` / `score_spread` は表示・ログ互換のため残すこと。

        `score_spread` は range であって variance ではないので、
        `score_variance` へ流用せず別キーで併存させる。
        """
        factors = _web_factors(MEASURED_WEB_SCORES)
        assert factors["top_score"] == pytest.approx(1.0)
        assert factors["score_spread"] == pytest.approx(0.8)
        assert factors["score_variance"] != factors["score_spread"]

    def test_single_result_has_zero_variance(self):
        assert _web_factors([0.7])["score_variance"] == 0.0

    def test_empty_results_use_the_worst_variance(self):
        """0 件は「最悪のばらつき」既定（RAG 側と同じ扱い）。"""
        factors = _web_factors([])
        assert factors["result_count"] == 0
        assert factors["max_score"] == 0.0
        assert factors["score_variance"] == 1.0

    def test_web_and_rag_agree_on_key_names(self):
        """2 つのツールが同じ語彙を使っていること（乖離の再発防止）。"""
        rag = RAGSearchTool.__new__(RAGSearchTool)._calculate_confidence_factors([0.9, 0.5])
        web = _web_factors([0.9, 0.5])

        for key in ("result_count", "avg_score", "max_score", "min_score", "score_variance"):
            assert key in rag, f"RAG に {key} が無い"
            assert key in web, f"Web に {key} が無い"


# =============================================================================
# ② キーが欠けたら黙らない
# =============================================================================

class TestMissingKeyWarning:

    def test_warns_when_max_score_is_missing(self, caplog):
        with caplog.at_level("WARNING", logger="grace.executor"):
            _warn({"result_count": 9, "avg_score": 0.6, "top_score": 1.0}, "web_search")

        assert any("max_score" in r.message for r in caplog.records), (
            "キー不一致が warning に出ない＝次の乖離もまた静かに壊れる"
        )

    def test_warning_names_the_step_and_received_keys(self, caplog):
        with caplog.at_level("WARNING", logger="grace.executor"):
            _warn({"result_count": 5, "avg_score": 0.5}, "web_search", step_id=101)

        message = caplog.records[0].message
        assert "101" in message and "web_search" in message
        assert "avg_score" in message, "受領キーが出ないと原因を追えない"

    def test_silent_when_canonical_keys_are_present(self, caplog):
        with caplog.at_level("WARNING", logger="grace.executor"):
            _warn(_web_factors(MEASURED_WEB_SCORES), "web_search")

        assert not caplog.records

    def test_silent_for_non_search_steps(self, caplog):
        """reasoning 等は検索統計を持たないので警告しない。"""
        with caplog.at_level("WARNING", logger="grace.executor"):
            _warn({"has_sources": True, "answer_length": 984}, "reasoning")

        assert not caplog.records

    def test_silent_when_there_are_no_results(self, caplog):
        """0 件のときは統計が無くて当然（別の経路で扱う）。"""
        with caplog.at_level("WARNING", logger="grace.executor"):
            _warn({"result_count": 0}, "rag_search")

        assert not caplog.records


# =============================================================================
# helpers
# =============================================================================

def _web_factors(scores: list) -> dict:
    tool = WebSearchTool.__new__(WebSearchTool)
    tool.backend = "serpapi"
    return tool._calculate_confidence_factors(scores)


def _warn(factors: dict, action: str, step_id: int = 1) -> None:
    executor = Executor.__new__(Executor)
    step = PlanStep(
        step_id=step_id,
        action=action,
        description="テスト",
        expected_output="テスト",
    )
    executor._warn_on_missing_score_keys(factors, step)

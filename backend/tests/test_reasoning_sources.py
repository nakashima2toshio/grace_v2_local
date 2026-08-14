# backend/tests/test_reasoning_sources.py
"""reasoning へ渡す参照情報と、リプランのタイムアウト引き継ぎのテスト。

## 何を守っているのか

実測（gemma4:26b-a4b-it-qat / 「明日の東京の天気は？」）で 42 分かけて
回答ゼロに終わった経路を、3 つの独立した回帰として固定する。

1. **リプランで `timeout_seconds` が落ちる** — `_adjust_step_ids` が
   PlanStep を作り直すとき引き継ぎを忘れ、初回計画の 240 秒に対して
   リプラン後だけ 30 秒になっていた。ローカル LLM の reasoning は
   1 呼び出し 90〜250 秒なので、**リプランするたびに必ずタイムアウト**した。
2. **参照情報が累積する** — reasoning は step_results 全体から集めるため、
   リプランのたびに同じ検索結果が積み上がる。実測では同じ Web 結果 9 件が
   4 回ずつ並び、計 56 情報源になっていた。
3. **無関係な RAG 結果がプロンプトを占める** — 「緩和閾値で採用」された
   スコア 0.52 の結果（AI の変遷・インドネシア首都移転…）が天気の質問の
   情報源 1〜5 を占めていた。
"""
from __future__ import annotations

from grace.config import ExecutorConfig, GraceConfig, PlannerConfig
from grace.executor import (
    Executor,
    _dedupe_sources,
    _filter_low_relevance_sources,
)
from grace.replan import ReplanManager
from grace.schemas import PlanStep


def _web(score: float, url: str, body: str = "本文") -> dict:
    return {
        "score": score,
        "payload": {"answer": body, "source": url, "title": "t"},
        "collection": "web_search",
    }


def _rag(score: float, body: str, source: str = "qa.csv") -> dict:
    return {"score": score, "payload": {"answer": body, "source": source}}


# =============================================================================
# ① リプランでタイムアウトを落とさない
# =============================================================================

class TestReplanKeepsTimeout:

    def _adjust(self, steps):
        manager = ReplanManager(config=GraceConfig())
        return manager._adjust_step_ids(steps, start_id=2, completed_count=1)

    def test_timeout_is_carried_over(self):
        """**引き継がないと 30 秒に戻り reasoning が必ず落ちる。**"""
        original = PlanStep(
            step_id=1, action="reasoning", description="d",
            expected_output="o", timeout_seconds=240,
        )
        assert self._adjust([original])[0].timeout_seconds == 240

    def test_none_stays_none(self):
        """未指定は未指定のまま（Executor が設定へ落とす）。"""
        original = PlanStep(
            step_id=1, action="reasoning", description="d", expected_output="o",
        )
        assert self._adjust([original])[0].timeout_seconds is None


class TestPlanStepTimeoutDefault:

    def test_default_is_none_not_thirty(self):
        """既定を固定秒数にしない。

        30 が既定だと、引き継ぎ漏れのたびに 30 秒へ戻る。None なら
        Executor が `planner.step_timeout_seconds` へ落ちるだけで済む。
        """
        step = PlanStep(step_id=1, action="reasoning", description="d", expected_output="o")
        assert step.timeout_seconds is None

    def test_accepts_values_above_300(self):
        """旧 `le=300` を撤廃していること。

        `llm.timeout` を 300 以上へ上げると、不変条件（LLM 側 < ステップ側）
        を保つためステップ側が 300 を超える。上限があると計画生成が
        ValidationError で落ちてしまう。
        """
        step = PlanStep(
            step_id=1, action="reasoning", description="d",
            expected_output="o", timeout_seconds=600,
        )
        assert step.timeout_seconds == 600


class TestExecutorStepTimeout:

    def _executor(self, step_timeout: int = 240) -> Executor:
        cfg = GraceConfig(planner=PlannerConfig(step_timeout_seconds=step_timeout))
        return Executor.__new__(Executor).__class__._step_timeout.__get__(
            type("_", (), {"config": cfg})()
        )

    def test_none_falls_back_to_config(self):
        """**未指定を「無制限」にしない。**

        無制限にすると、引き継ぎを忘れたステップが永久に返らなくなる。
        """
        step = PlanStep(step_id=1, action="reasoning", description="d", expected_output="o")
        assert self._executor(240)(step) == 240

    def test_explicit_value_wins(self):
        step = PlanStep(
            step_id=1, action="reasoning", description="d",
            expected_output="o", timeout_seconds=99,
        )
        assert self._executor(240)(step) == 99


# =============================================================================
# ② 参照情報の重複除去と上限
# =============================================================================

class TestDedupeSources:

    def test_removes_exact_duplicates(self):
        """リプランで同じ結果が積み上がるのを止める。"""
        a, b = _web(1.0, "https://a"), _web(0.9, "https://b")
        assert _dedupe_sources([a, b, a, b, a, b], limit=0) == [a, b]

    def test_preserves_order(self):
        a, b, c = _web(1.0, "https://a"), _web(0.9, "https://b"), _web(0.8, "https://c")
        assert _dedupe_sources([a, b, c, a], limit=0) == [a, b, c]

    def test_same_url_different_snippet_is_kept(self):
        """同一ページの別スニペットは別物として残す。"""
        a1 = _web(1.0, "https://a", body="今日は雨")
        a2 = _web(0.9, "https://a", body="明日は曇り")
        assert len(_dedupe_sources([a1, a2], limit=0)) == 2

    def test_applies_limit(self):
        sources = [_web(1.0, f"https://{i}") for i in range(30)]
        assert len(_dedupe_sources(sources, limit=20)) == 20

    def test_limit_zero_means_unlimited(self):
        sources = [_web(1.0, f"https://{i}") for i in range(30)]
        assert len(_dedupe_sources(sources, limit=0)) == 30

    def test_unknown_shapes_pass_through(self):
        """形式が違うだけの要素を捨てない。"""
        assert _dedupe_sources(["文字列", 42, None], limit=0) == ["文字列", 42, None]

    def test_realistic_replan_accumulation(self):
        """実測の形: 同じ 9 件が 4 回積み上がる → 9 件へ戻す。"""
        one_round = [_web(1.0 - i * 0.1, f"https://s{i}") for i in range(9)]
        accumulated = one_round * 4

        assert len(accumulated) == 36
        assert len(_dedupe_sources(accumulated, limit=20)) == 9


# =============================================================================
# ③ 無関係な RAG 結果を reasoning へ渡さない
# =============================================================================

class TestFilterLowRelevance:

    def test_drops_low_score_rag(self):
        """緩和閾値で拾われた無関係な RAG 結果を落とす。"""
        noise = _rag(0.52, "AI の変遷について")
        good = _rag(0.80, "返品は 30 日以内")
        assert _filter_low_relevance_sources([noise, good], 0.55) == [good]

    def test_keeps_web_regardless_of_score(self):
        """**Web の score は順位由来（1.0〜0.2）で尺度が違う。**

        RAG と同じ閾値を当てると有用な下位 Web 結果まで落ちる。
        """
        low_web = _web(0.2, "https://tokyo-np.co.jp")
        assert _filter_low_relevance_sources([low_web], 0.55) == [low_web]

    def test_never_returns_empty(self):
        """全件除外になるならフィルタを諦める。

        参照情報ゼロで reasoning を走らせても「情報がありません」しか出ない。
        """
        noise = [_rag(0.50, "x"), _rag(0.51, "y")]
        assert _filter_low_relevance_sources(noise, 0.55) == noise

    def test_unknown_shapes_pass_through(self):
        assert _filter_low_relevance_sources(["文字列"], 0.55) == ["文字列"]

    def test_realistic_weather_case(self):
        """実測の形: 天気の質問に RAG のノイズ 5 件 ＋ Web 9 件。"""
        rag_noise = [_rag(0.52 + i * 0.005, f"無関係 {i}") for i in range(5)]
        web = [_web(1.0 - i * 0.1, f"https://w{i}") for i in range(9)]

        kept = _filter_low_relevance_sources(rag_noise + web, 0.55)
        assert kept == web, "無関係な RAG が残っている"


class TestExecutorConfigDefaults:

    def test_limit_and_threshold_are_configurable(self):
        cfg = ExecutorConfig()
        assert cfg.reasoning_max_sources > 0
        assert 0.0 < cfg.reasoning_min_rag_score < 1.0

    def test_threshold_is_above_the_relaxed_rag_floor(self):
        """緩和閾値（0.5）で拾われた結果を素通ししない値であること。"""
        assert ExecutorConfig().reasoning_min_rag_score > 0.5

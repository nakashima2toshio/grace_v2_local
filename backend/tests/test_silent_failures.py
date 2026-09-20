# backend/tests/test_silent_failures.py
"""**例外を出さずに静かに壊れる 2 経路**を固定するテスト。

## 背景（grace_v2_local で実測、本リポジトリにも同じコードが残っていた）

どちらも「動いているように見えるのに間違った結果を出す」種類の不具合で、
気づく手がかりが無いのが本質的な問題である。

### ① 検索統計のキー名が食い違っていた

Executor は `max_score` / `score_variance` を読むが、`WebSearchTool` は
`top_score` / `score_spread` を返していた。**キー名が違っても例外にならず
黙って既定値へ落ちる**ため、実測ではこうなっていた:

    Initial factors  : {'avg_score': 0.6, 'top_score': 1.0, 'score_spread': 0.8}
    ConfidenceFactors: search_max_score=0.6      ← avg が入る（実際は 1.0）
                       search_score_variance=1.0 ← 既定（実際は 0.067）

最高スコアが平均に潰れ、ばらつきは常に最悪値。`RAGSearchTool` は正準名を
返していたので **Web ステップだけが壊れていた**。ログには両方の値が出て
いたのに、食い違いを指摘するものが無かった。

### ② sparse クライアントの初期化失敗が記憶されなかった

成功だけをキャッシュしていたため、初期化に失敗すると毎回モデル構築を
やり直していた。呼び出し側は例外を `logger.debug` で握り潰すので、
コレクション数 × リプラン回数ぶんの再ダウンロードが**誰にも見えないまま**
繰り返される。

ここで固定すること:
  1. WebSearchTool が正準キーを返すこと（互換キーも併存）
  2. 統計値が実際に正しいこと（max が avg に潰れない）
  3. 正準キーが欠けたら警告が出ること（次の乖離を沈黙させない）
  4. sparse の初期化失敗が記憶され、再構築を試みないこと
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from grace.executor import Executor
from grace.schemas import PlanStep
from grace.tools import RAGSearchTool, WebSearchTool

SCORES = [1.0, 0.9, 0.6]


# =============================================================================
# ① 検索統計のキー
# =============================================================================

class TestWebSearchReturnsCanonicalKeys:

    def _factors(self, scores=SCORES):
        return WebSearchTool()._calculate_confidence_factors(list(scores))

    def test_canonical_keys_are_present(self):
        factors = self._factors()

        assert "max_score" in factors, "Executor が読むキーが無い（黙って既定値になる）"
        assert "score_variance" in factors

    def test_max_score_is_the_real_maximum(self):
        """ここが avg に潰れていたのが実測の不具合。"""
        assert self._factors()["max_score"] == 1.0

    def test_variance_is_computed_not_defaulted(self):
        factors = self._factors()

        assert factors["score_variance"] < 1.0, "既定の最悪値のまま落ちている"
        assert factors["score_variance"] == pytest.approx(0.02888, abs=1e-4)

    def test_legacy_keys_are_kept_for_logs(self):
        """既存ログとの互換のため top_score / score_spread も残す。"""
        factors = self._factors()

        assert factors["top_score"] == 1.0
        assert factors["score_spread"] == pytest.approx(0.4)

    def test_empty_result_also_carries_canonical_keys(self):
        factors = self._factors([])

        assert factors["max_score"] == 0.0
        assert factors["score_variance"] == 1.0

    def test_rag_and_web_agree_on_key_names(self):
        """2 つの検索ツールで正準キーが揃っていること。"""
        rag = RAGSearchTool()._calculate_confidence_factors(list(SCORES))
        web = self._factors()

        for key in ("result_count", "avg_score", "max_score", "min_score", "score_variance"):
            assert key in rag and key in web, f"{key} がどちらかに無い"


# =============================================================================
# ② 欠損キーの番人
# =============================================================================

class TestMissingKeysAreReported:

    def _step(self, action="web_search"):
        return PlanStep(step_id=1, action=action, description="検索", query="q",
                        expected_output="検索結果")

    def test_missing_canonical_keys_warn(self, caplog):
        executor = Executor.__new__(Executor)   # __init__ を通さず番人だけ試す
        factors = {"result_count": 3, "avg_score": 0.6, "top_score": 1.0}

        with caplog.at_level(logging.WARNING):
            executor._warn_on_missing_score_keys(factors, self._step())

        assert "max_score" in caplog.text
        assert "score_variance" in caplog.text
        assert "既定値で評価します" in caplog.text

    def test_canonical_factors_are_silent(self, caplog):
        executor = Executor.__new__(Executor)
        # ツール構築は INFO ログを出すので caplog の外で済ませる
        factors = WebSearchTool()._calculate_confidence_factors(list(SCORES))
        caplog.clear()

        with caplog.at_level(logging.WARNING):
            executor._warn_on_missing_score_keys(factors, self._step())

        assert caplog.text == ""

    def test_non_search_steps_are_ignored(self, caplog):
        """推論ステップは統計を持たないので警告しない（ログを汚さない）。"""
        executor = Executor.__new__(Executor)

        with caplog.at_level(logging.WARNING):
            executor._warn_on_missing_score_keys({"result_count": 0}, self._step("reasoning"))

        assert caplog.text == ""

    def test_zero_result_search_is_ignored(self, caplog):
        """0 件の検索は統計が無くて当然。"""
        executor = Executor.__new__(Executor)

        with caplog.at_level(logging.WARNING):
            executor._warn_on_missing_score_keys({"result_count": 0}, self._step())

        assert caplog.text == ""


# =============================================================================
# ③ sparse クライアントの失敗キャッシュ
# =============================================================================

class TestSparseInitFailureIsRemembered:

    def setup_method(self):
        from helper.helper_embedding_sparse import reset_sparse_embedding_client_cache
        reset_sparse_embedding_client_cache()

    teardown_method = setup_method

    def test_second_call_does_not_rebuild(self):
        """失敗後にモデル構築を試さないこと（再ダウンロードの嵐を止める）。"""
        from helper import helper_embedding_sparse as mod

        boom = RuntimeError("Local file sizes do not match the metadata")
        with patch.object(mod, "SparseEmbeddingClient", side_effect=boom) as ctor:
            for _ in range(3):
                with pytest.raises(RuntimeError):
                    mod.get_sparse_embedding_client("prithivida/Splade_PP_en_v1")

        assert ctor.call_count == 1, (
            f"初期化を {ctor.call_count} 回試みている（失敗が記憶されていない）"
        )

    def test_the_same_exception_is_raised(self):
        from helper import helper_embedding_sparse as mod

        boom = RuntimeError("cache is corrupted")
        with patch.object(mod, "SparseEmbeddingClient", side_effect=boom):
            with pytest.raises(RuntimeError) as first:
                mod.get_sparse_embedding_client("m")
            with pytest.raises(RuntimeError) as second:
                mod.get_sparse_embedding_client("m")

        assert first.value is second.value

    def test_warning_is_emitted_only_once(self, caplog):
        """初回だけ原因を出す。以降は黙る（ログを埋めない）。"""
        from helper import helper_embedding_sparse as mod

        with patch.object(mod, "SparseEmbeddingClient", side_effect=RuntimeError("boom")):
            with caplog.at_level(logging.WARNING):
                for _ in range(3):
                    with pytest.raises(RuntimeError):
                        mod.get_sparse_embedding_client("m")

        assert caplog.text.count("Sparse Embedding の初期化に失敗") == 1
        assert "dense 検索のみで動作します" in caplog.text

    def test_success_is_still_cached(self):
        from helper import helper_embedding_sparse as mod

        client = SimpleNamespace(model_name="m")
        with patch.object(mod, "SparseEmbeddingClient", return_value=client) as ctor:
            first = mod.get_sparse_embedding_client("m")
            second = mod.get_sparse_embedding_client("m")

        assert first is second is client
        assert ctor.call_count == 1

    def test_a_different_model_is_built(self):
        """モデル名が変われば作り直すこと（従来の挙動を壊さない）。"""
        from helper import helper_embedding_sparse as mod

        with patch.object(mod, "SparseEmbeddingClient",
                          side_effect=lambda model_name: SimpleNamespace(model_name=model_name)):
            first = mod.get_sparse_embedding_client("a")
            second = mod.get_sparse_embedding_client("b")

        assert first.model_name == "a"
        assert second.model_name == "b"

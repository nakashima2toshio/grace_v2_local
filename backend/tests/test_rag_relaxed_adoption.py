# backend/tests/test_rag_relaxed_adoption.py
"""緩和閾値でしか拾えなかった RAG 結果の **採用ルール** を固定するテスト。

## 背景

`agent_tools` は出典不足のとき一次閾値（0.7）を緩和閾値（0.5）まで下げて
結果を返す（P-04）。`RAGSearchTool` は「一次閾値に届くコレクション」を
即採用し、緩和のみの結果は保留して探索を続ける。

その保留と採用に 2 つの欠陥があった。実測（「明日の東京の天気は？」）:

    wikipedia_ja_5per      0.5375  ← 採用されていた（最初に検索されたから）
    cc_news_2per_anthropic 0.6658  ← 最高スコアなのに破棄
    fineweb_edu_ja_5per    0.6058  ← 破棄
    ec_faq_anthropic       0.6009  ← 破棄
    （10 コレクション中の最下位が採用されていた）

### 欠陥 A: 保留が「最初の 1 つ」だった

`if not fallback_results:` のため、後続にどれだけ関連度の高いコレクションが
あっても捨てていた。選択基準が「関連度」ではなく「検索順」になっていた。

### 欠陥 B: スコアがいくつでも無条件に採用していた

社内ナレッジに存在しない話題でも 0.53 の無関係文書（AI・インドネシア首都
移転・著作権…）が採用され、

  - 出典一覧に「社内 qa_pairs_combined_chunks.csv」が並ぶ
    ＝ 社内ナレッジを根拠にしたように見える（いちばんまずい）
  - groundedness の検証ソースに無関係文書が混ざる
  - step confidence が低スコアに引きずられる

しかも `executor.reasoning_min_rag_score`(0.55) により **reasoning プロンプトへは
1 件も渡っていなかった**。回答に寄与せず、出典としてだけ出ていた。

そこで採用の下限を `reasoning_min_rag_score` と**同じ値**にし、
「推論に使えない文書は引用もしない」という不変条件にした。

⚠️ Qdrant にも LLM にも接続しない（検索関数をスタブに差し替える）。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from grace.config import GraceConfig
from grace.tools import RAGSearchTool

# 実測に合わせた各コレクションの Top スコア（「明日の東京の天気は？」）
MEASURED_TOP_SCORES = {
    "wikipedia_ja_5per": 0.5375,        # 最初に検索される
    "cc_news_2per_anthropic": 0.6658,   # 最高
    "cc_news_2per_gemini": 0.5592,
    "gov_faq_anthropic": 0.5569,
    "saas_api_anthropic": 0.5679,
    "saas_docs_anthropic": 0.5797,
    "ec_faq_anthropic": 0.6009,
    "fineweb_edu_ja_5per": 0.6058,
    "ec_policy_anthropic": 0.5751,
    "gov_laws_anthropic": 0.5587,
}


# =============================================================================
# ① 保留は「最高スコア」で選ぶ（検索順ではない）
# =============================================================================

class TestFallbackPicksTheBestCollection:

    def test_picks_highest_scoring_collection_not_the_first(self):
        """実測シナリオ: 最下位ではなく最高スコアのコレクションを採用する。"""
        result = _run(MEASURED_TOP_SCORES, min_adopt_score=0.5)

        assert result.success is True
        assert _top_score(result) == pytest.approx(0.6658), (
            "検索順ではなく関連度で選ぶこと（旧実装は 0.5375 を採用していた）"
        )

    def test_order_does_not_change_the_outcome(self):
        """検索順を入れ替えても同じコレクションが選ばれること。"""
        reversed_scores = dict(reversed(list(MEASURED_TOP_SCORES.items())))

        forward = _run(MEASURED_TOP_SCORES, min_adopt_score=0.5)
        backward = _run(reversed_scores, min_adopt_score=0.5)

        assert _top_score(forward) == _top_score(backward) == pytest.approx(0.6658)

    def test_primary_hit_still_wins_immediately(self):
        """一次閾値(0.7)に届くコレクションがあれば、緩和結果より優先すること。

        ⚠️ 即採用（break）なので、後続にさらに高い緩和結果があっても関係ない。
        """
        scores = {"wikipedia_ja_5per": 0.5375, "gov_faq_anthropic": 0.80}
        result = _run(scores, min_adopt_score=0.5)

        assert _top_score(result) == pytest.approx(0.80)

    def test_single_relaxed_collection_is_still_adopted(self):
        """候補が 1 つだけでも従来どおり採用されること（P-04 の意図を壊さない）。"""
        result = _run({"gov_faq_anthropic": 0.60}, min_adopt_score=0.55)
        assert _top_score(result) == pytest.approx(0.60)


# =============================================================================
# ② 推論に使えない文書は引用もしない
# =============================================================================

class TestAdoptionFloor:

    def test_rejects_everything_below_the_reasoning_threshold(self):
        """全コレクションが下限未満なら 0 件で返すこと。

        実測の「明日の東京の天気は？」は社内ナレッジに該当が無い。最良でも
        0.6658 で、下限 0.7 なら 1 件も採用してはいけない。
        """
        result = _run(MEASURED_TOP_SCORES, min_adopt_score=0.7)

        assert result.output == [] or not result.output, (
            "社内ナレッジに該当が無いなら出典 0 件が正しい"
        )

    def test_adopts_when_the_best_clears_the_floor(self):
        result = _run(MEASURED_TOP_SCORES, min_adopt_score=0.55)
        assert _top_score(result) == pytest.approx(0.6658)

    def test_floor_is_the_reasoning_threshold_not_a_new_constant(self):
        """下限は `executor.reasoning_min_rag_score` をそのまま使うこと。

        別々の定数にすると「推論には使わないが引用はする」という食い違いが
        戻ってくる。設定を動かせば採用側も追随することを確認する。
        """
        scores = {"gov_faq_anthropic": 0.60}

        assert _top_score(_run(scores, min_adopt_score=0.55)) == pytest.approx(0.60)
        assert not _run(scores, min_adopt_score=0.65).output

    def test_boundary_is_inclusive(self):
        """下限ちょうどは採用する（`>=`）。"""
        result = _run({"gov_faq_anthropic": 0.55}, min_adopt_score=0.55)
        assert _top_score(result) == pytest.approx(0.55)

    def test_no_results_at_all_is_not_an_error(self):
        """どのコレクションも 0 件なら、素直に 0 件で返すこと。"""
        result = _run({}, min_adopt_score=0.55)
        assert not result.output


# =============================================================================
# helpers
# =============================================================================

def _run(top_scores: dict, *, min_adopt_score: float):
    """コレクションごとの Top スコアを与えて `RAGSearchTool.execute()` を回す。"""
    config = GraceConfig()
    config.executor.reasoning_min_rag_score = min_adopt_score
    config.qdrant.restrict_to_collection = False
    config.qdrant.allowed_collections = []

    tool = RAGSearchTool.__new__(RAGSearchTool)
    tool.config = config
    tool.qdrant_url = config.qdrant.url
    tool._client = None
    tool.keyword_extractor = None

    def _search(_query: str, collection: str):
        score = top_scores.get(collection)
        if score is None:
            return []
        # 下位モジュールはスコア降順で返す。2 件目以降は少し低い値にする。
        return [
            {"score": score, "id": 1, "payload": {"answer": "本文", "source": "x.csv"}},
            {"score": score - 0.01, "id": 2, "payload": {"answer": "本文2", "source": "x.csv"}},
        ]

    with patch.object(
        RAGSearchTool, "_get_all_collections_dynamic", return_value=list(top_scores)
    ), patch("agent_tools.search_rag_knowledge_base_structured", side_effect=_search):
        return tool.execute(query="明日の東京の天気は？")


def _top_score(result) -> float:
    assert result.output, "結果が空（採用されていない）"
    return result.output[0]["score"]


def _unused() -> SimpleNamespace:  # pragma: no cover - import 明示用
    return SimpleNamespace()

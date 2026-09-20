# backend/tests/test_rag_adoption.py
"""**無関係な文書を「社内ナレッジ」として採用しない**ことを固定するテスト。

## 背景（実測 2026-08-17 11:10「明日の東京の天気は？」）

社内ナレッジに天気の情報は無い。にもかかわらず、AI の変遷・インドネシア首都
移転・著作権・地理学・日本語学の Q&A が **`--- 情報源 1 【社内】 ---` として
reasoning プロンプトに入り**、出典一覧の先頭にも
`社内 qa_pairs_combined_chunks.csv` として載っていた。

原因は 3 つで、**どれか 1 つを直しても症状は消えない**。

### ① 保留が「最初に検索した 1 つ」だった

    if not fallback_results:      # ← 後続がどれだけ高くても捨てる
        fallback_results = results

実測の Top スコア:

    wikipedia_ja_5per      0.5375  ← 採用（最初に検索されたから）
    cc_news_2per_anthropic 0.6658  ← 最高スコアなのに破棄
    fineweb_edu_ja_5per    0.6058  ← 破棄
    ec_faq_anthropic       0.6009  ← 破棄

12 コレクション中の**最下位**が採用されていた。選択基準が「関連度」ではなく
「検索順」になっていた。

### ② スコアがいくつでも無条件に採用していた

12 コレクションすべてが一次閾値 0.7 に届かない＝社内ナレッジに該当なし、
という判定自体は正しかった。にもかかわらず最後に緩和結果を採用していた。

### ③ 汎用コーパスが横断検索に入っていた

`allowed_collections`（許可リスト）は業界プロファイル指定時にしか注入されない。
業界指定なしの基本版では空のままなので、Qdrant のコレクションが全部対象になる。
汎用コーパスは話題の幅が広く、**どんな質問にも 0.5〜0.6 台で当たる**。

## 不変条件

    推論に使えない文書は、出典としても採用しない。

採用の下限は `executor.reasoning_min_rag_score` と同じ値を共有する。別々の
定数に分けると 2 箇所が食い違い、「回答には 1 文字も寄与しないのに出典として
だけ表示される」状態が再発する。

⚠️ Qdrant にも LLM にも接続しない（検索関数を差し替える）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from grace.config import ExecutorConfig
from grace.tools import RAGSearchTool

# 実測のコレクションと Top スコア
MEASURED = {
    "wikipedia_ja_5per": 0.5375,
    "cc_news_2per_anthropic": 0.6658,
    "fineweb_edu_ja_5per": 0.6058,
    "ec_faq_anthropic": 0.6009,
    "gov_faq_anthropic": 0.5569,
}


def _hit(collection, score):
    return {
        "score": score,
        "payload": {
            "domain": collection,
            "question": f"{collection} の無関係な質問",
            "answer": "無関係な回答",
            "source": "qa_pairs_combined_chunks.csv",
        },
    }


def _tool(min_score=0.64, excluded=None):
    tool = RAGSearchTool.__new__(RAGSearchTool)
    tool.config = SimpleNamespace(
        qdrant=SimpleNamespace(
            url="http://localhost:6333",
            collection_name="customer_support_faq",
            restrict_to_collection=False,
            allowed_collections=[],
            excluded_collections=(
                ["cc_news", "fineweb", "wikipedia", "livedoor", "japanese_text"]
                if excluded is None else excluded
            ),
        ),
        executor=SimpleNamespace(reasoning_min_rag_score=min_score),
    )
    tool.keyword_extractor = None
    return tool


def _run(tool, scores, monkeypatch, query="明日の東京の天気は？"):
    """コレクションごとの Top スコアを固定して execute を回す。"""
    # ⚠️ 本リポジトリの `_get_all_collections_dynamic` は `apply_exclusions` を取り、
    #    **除外をこの中で適用する**（grace_v2 は execute() 側で適用する）。
    #    そのため、ここを素通りのスタブにすると除外が一切効かず、
    #    「3 つ揃って実測ケースが直る」を確かめられない。
    #    Qdrant への問い合わせ（候補の収集）だけを固定し、
    #    **除外は本物の `_apply_excluded_collections` に通す**。
    def _dynamic(self, apply_exclusions: bool = True):
        candidates = list(scores)
        if apply_exclusions:
            excluded = list(
                getattr(self.config.qdrant, "excluded_collections", None) or []
            )
            candidates = RAGSearchTool._apply_excluded_collections(candidates, excluded)
        return candidates

    monkeypatch.setattr(RAGSearchTool, "_get_all_collections_dynamic", _dynamic)
    monkeypatch.setattr(
        "agent_tools.search_rag_knowledge_base_structured",
        lambda _q, col, **_kw: [_hit(col, scores[col])] if col in scores else [],
    )
    return tool.execute(query=query)


# =============================================================================
# ① 保留は最高スコアのコレクション
# =============================================================================

class TestFallbackPicksTheBestCollection:

    def test_highest_score_wins_not_search_order(self, monkeypatch):
        """検索順の先頭（0.5375）ではなく最高スコア（0.6658）を保留する。"""
        tool = _tool(min_score=0.0, excluded=[])   # 採用下限と除外は無効化して選択だけ見る

        result = _run(tool, MEASURED, monkeypatch)

        assert result.success
        [hit] = result.output
        assert hit["score"] == pytest.approx(0.6658), (
            "検索順の先頭が採用されている（選択基準が関連度になっていない）"
        )
        assert hit["payload"]["domain"] == "cc_news_2per_anthropic"

    def test_order_does_not_matter(self, monkeypatch):
        """並び順を変えても同じコレクションが選ばれること。"""
        tool = _tool(min_score=0.0, excluded=[])
        reversed_scores = dict(reversed(list(MEASURED.items())))

        [hit] = _run(tool, reversed_scores, monkeypatch).output

        assert hit["payload"]["domain"] == "cc_news_2per_anthropic"


# =============================================================================
# ② 採用の下限（推論に使えない文書は引用もしない）
# =============================================================================

class TestAdoptionFloor:

    def test_all_below_floor_returns_nothing(self, monkeypatch):
        """実測どおり全コレクションが下限未満なら 0 件で返す。"""
        tool = _tool(min_score=0.7, excluded=[])

        result = _run(tool, MEASURED, monkeypatch)

        assert result.output == [], (
            "無関係文書が社内ナレッジとして採用されている"
        )

    def test_above_floor_is_adopted(self, monkeypatch):
        """下限を超えていれば従来どおり採用する（出典ゼロを救う意図は残す）。"""
        tool = _tool(min_score=0.6, excluded=[])

        [hit] = _run(tool, MEASURED, monkeypatch).output

        assert hit["score"] == pytest.approx(0.6658)

    def test_floor_is_shared_with_reasoning(self, monkeypatch):
        """⚠️ 不変条件: 推論に使えない文書は出典としても採用しない。

        採用下限は `executor.reasoning_min_rag_score` そのものを読むこと。
        別々の定数に分けると 2 箇所が食い違う。
        """
        tool = _tool(min_score=0.99, excluded=[])

        assert _run(tool, MEASURED, monkeypatch).output == []

    def test_missing_executor_config_uses_the_default(self, monkeypatch):
        """`executor` を持たない config スタブでも落ちないこと。"""
        tool = _tool(excluded=[])
        tool.config = SimpleNamespace(
            qdrant=tool.config.qdrant,          # executor を持たない
        )

        result = _run(tool, MEASURED, monkeypatch)

        # 既定 0.64 が使われるので 0.6658 は通る
        assert result.output and result.output[0]["score"] == pytest.approx(0.6658)

    def test_primary_threshold_hit_is_unaffected(self, monkeypatch):
        """一次閾値（0.7）に届く結果は従来どおり即採用（下限判定を通らない）。"""
        tool = _tool(min_score=0.99, excluded=[])

        [hit] = _run(tool, {"gov_faq_anthropic": 0.85}, monkeypatch).output

        assert hit["score"] == pytest.approx(0.85)


# =============================================================================
# ③ 汎用コーパスの除外
# =============================================================================

class TestExcludedCollections:
    """⚠️ **本リポジトリの `_apply_excluded_collections` は
    `(candidates, excluded)` を取る staticmethod** である（除外リストは
    呼び出し側＝`_get_all_collections_dynamic` が渡す）。grace_v2 は
    `(candidates, protected=None)` のインスタンスメソッドで、除外リストを
    自分で config から引く形。**固定したい挙動は同じ**なので、テストは
    本リポジトリのシグネチャに合わせてある。

    「明示指定（`collection` 引数）・プロファイルの許可リストは除外しない」は
    本リポジトリでは `execute()` 側の構造（`apply_exclusions=not allowed` と、
    明示指定を除外にかけないこと）で担保されるため、
    `backend/tests/test_memory_exclusion.py::TestNamedCollectionsAreNotExcluded`
    で実経路ごと固定している。
    """

    EXCLUDED = ["cc_news", "fineweb", "wikipedia", "livedoor", "japanese_text"]

    def test_generic_corpora_are_dropped(self):
        kept = RAGSearchTool._apply_excluded_collections(list(MEASURED), self.EXCLUDED)

        assert kept == ["ec_faq_anthropic", "gov_faq_anthropic"]

    def test_partial_match(self):
        """"cc_news" は cc_news_2per_anthropic 等のサフィックス付きにも一致する。"""
        kept = RAGSearchTool._apply_excluded_collections(
            ["cc_news_2per", "cc_news_2per_768", "cc_news_2per_gemini", "saas_api_anthropic"],
            self.EXCLUDED,
        )

        assert kept == ["saas_api_anthropic"]

    def test_all_excluded_falls_back_to_no_filter(self):
        """全件除外になる環境では検索が丸ごと死なないようにする。"""
        kept = RAGSearchTool._apply_excluded_collections(
            ["wikipedia_ja_5per", "cc_news_2per"], self.EXCLUDED,
        )

        assert kept == ["wikipedia_ja_5per", "cc_news_2per"]

    def test_empty_setting_is_a_no_op(self):
        assert RAGSearchTool._apply_excluded_collections(list(MEASURED), []) == list(MEASURED)


# =============================================================================
# ④ 3 つ揃って実測ケースが直る
# =============================================================================

class TestMeasuredCaseIsFixed:

    def test_weather_query_yields_no_internal_source(self, monkeypatch):
        """実測の再現: 既定設定で社内出典が 0 件になること。

        ⚠️ 3 つのうちどれか 1 つでも欠けると、この検証は通らない:
          - 最高スコア選択のみ → cc_news 0.6658 が採用される
          - 除外のみ          → ec_faq 0.6009 が採用される
          - 下限のみ          → cc_news 0.6658 が通過する
        """
        tool = _tool()   # 既定: 下限 0.64 + 汎用コーパス除外

        result = _run(tool, MEASURED, monkeypatch)

        assert result.output == [], (
            f"社内ナレッジに該当が無いのに {len(result.output)} 件採用している"
        )

    def test_a_real_hit_still_survives(self, monkeypatch):
        """業務コレクションに本当に該当があれば従来どおり返すこと。"""
        tool = _tool()

        [hit] = _run(
            tool, {**MEASURED, "gov_faq_anthropic": 0.72},
            monkeypatch, query="住民票の写しの取り方は？",
        ).output

        assert hit["payload"]["domain"] == "gov_faq_anthropic"

    def test_default_config_matches_the_documented_value(self):
        """既定値が実測値（0.64）から静かにずれていないこと。"""
        assert ExecutorConfig().reasoning_min_rag_score == 0.64

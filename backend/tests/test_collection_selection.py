# backend/tests/test_collection_selection.py
"""緩和結果が一次ヒットを握り潰さないことの回帰テスト（P-04 の回帰修正）。

背景:
P-04（コサイン類似度の二段構え）を入れた直後、gov プロファイルで次の回帰が出た。

    🔍 wikipedia_ja_5per → 緩和 0.5 で 3 件（著作権・インドネシア等の無関係文書）
    → `if results: break` で打ち切り
    → 正解のある gov_faq_anthropic（score 0.80）が**一度も検索されない**

原因は「緩和でしか拾えなかった低関連の結果」を一次ヒットと同等に扱って
break していたこと。修正後は、一次閾値に届く結果を含むコレクションだけを即採用し、
緩和のみの結果はフォールバックとして保留する。

`RAGSearchTool.execute` は `agent_tools.search_rag_knowledge_base_structured` を
関数内で遅延 import するため、monkeypatch で差し替えて Qdrant なしで検証できる。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_tools import COSINE_SIMILARITY_THRESHOLD


def _hit(score, label):
    return {"score": score, "payload": {"answer": label, "source": f"{label}.csv"}}


@pytest.fixture()
def rag_tool(monkeypatch):
    """Qdrant に触れない RAGSearchTool を組み立てる。"""
    from grace.tools import RAGSearchTool

    config = SimpleNamespace(
        qdrant=SimpleNamespace(
            url="http://localhost:6333",
            allowed_collections=[],
            search_priority=["wikipedia_ja", "gov_laws", "gov_faq"],
            restrict_to_collection=False,
            collection_name="dummy",
        ),
    )
    tool = RAGSearchTool.__new__(RAGSearchTool)  # __init__ の Qdrant 初期化を回避
    tool.config = config
    tool._client = None
    tool.keyword_extractor = None
    return tool


def _install_search_stub(monkeypatch, responses):
    """コレクション名 → 検索結果 のスタブを差し込み、呼び出し順を記録する。"""
    called = []

    def fake_search(query, collection_name, *a, **kw):
        called.append(collection_name)
        return responses.get(collection_name, [])

    monkeypatch.setattr(
        "agent_tools.search_rag_knowledge_base_structured", fake_search
    )
    return called


def _run(tool, monkeypatch, responses, candidates):
    """候補コレクションを固定して execute を走らせる（Qdrant へは触れない）。"""
    called = _install_search_stub(monkeypatch, responses)
    # Qdrant からの動的コレクション取得を固定リストへ差し替え
    monkeypatch.setattr(
        type(tool), "_get_all_collections_dynamic", lambda self: list(candidates)
    )
    # 許可リストの絞り込みは順序を保ったまま素通しにする
    monkeypatch.setattr(
        type(tool), "_apply_allowed_collections", staticmethod(lambda c, a: list(c))
    )
    result = tool.execute(query="住民票の写しの取り方は？")
    return result, called


def test_relaxed_hit_does_not_shadow_primary_hit(rag_tool, monkeypatch):
    """実測回帰の再現: 緩和のみの wikipedia が正解の gov_faq を握り潰さない。"""
    responses = {
        # 緩和でしか拾えない無関係文書（実測と同じスコア帯）
        "wikipedia_ja_5per": [_hit(0.5358, "著作権GFDL"), _hit(0.5197, "インドネシア")],
        "gov_laws_anthropic": [],
        # 一次閾値に届く正解
        "gov_faq_anthropic": [_hit(0.8011, "住民票")],
    }
    candidates = ["wikipedia_ja_5per", "gov_laws_anthropic", "gov_faq_anthropic"]

    result, called = _run(rag_tool, monkeypatch, responses, candidates)

    # gov_faq まで探索が到達していること（打ち切られていない）
    assert "gov_faq_anthropic" in called
    # 正解が採用されていること
    assert result.output[0]["payload"]["answer"] == "住民票"
    assert result.output[0]["score"] >= COSINE_SIMILARITY_THRESHOLD


def test_primary_hit_stops_search_immediately(rag_tool, monkeypatch):
    """一次ヒットのあるコレクションで即採用し、以降は検索しない（無駄打ちしない）。"""
    responses = {
        "gov_faq_anthropic": [_hit(0.8011, "住民票")],
        "wikipedia_ja_5per": [_hit(0.90, "呼ばれないはず")],
    }
    candidates = ["gov_faq_anthropic", "wikipedia_ja_5per"]

    result, called = _run(rag_tool, monkeypatch, responses, candidates)

    assert called == ["gov_faq_anthropic"]
    assert result.output[0]["payload"]["answer"] == "住民票"


def test_primary_hit_keeps_relaxed_extras_from_same_collection(rag_tool, monkeypatch):
    """一次ヒットのあるコレクションでは緩和分も一緒に採用される（P-04 の効果を維持）。"""
    responses = {
        "gov_faq_anthropic": [
            _hit(0.8011, "住民票"), _hit(0.68, "関連1"), _hit(0.66, "関連2"),
        ],
    }
    result, called = _run(
        rag_tool, monkeypatch, responses, ["gov_faq_anthropic"]
    )

    assert len(result.output) == 3
    assert [r["payload"]["answer"] for r in result.output] == ["住民票", "関連1", "関連2"]


def test_falls_back_to_relaxed_when_no_primary_anywhere(rag_tool, monkeypatch):
    """どのコレクションも一次に届かない場合は保留した緩和結果を採用する（出典ゼロを救う）。"""
    responses = {
        "wikipedia_ja_5per": [_hit(0.55, "緩和A")],
        "gov_faq_anthropic": [_hit(0.52, "緩和B")],
    }
    candidates = ["wikipedia_ja_5per", "gov_faq_anthropic"]

    result, called = _run(rag_tool, monkeypatch, responses, candidates)

    # 全コレクションを探索したうえで、最初の緩和結果を採用
    assert called == candidates
    assert result.output[0]["payload"]["answer"] == "緩和A"


def test_no_results_anywhere_returns_failure(rag_tool, monkeypatch):
    """全コレクションで 0 件なら失敗を返す（従来どおり）。"""
    result, called = _run(
        rag_tool, monkeypatch, {}, ["wikipedia_ja_5per", "gov_faq_anthropic"]
    )

    assert result.success is False
    assert result.output == []


# ---------------------------------------------------------------------------
# P-03: 検索順序は業界プロファイルの並びを優先する
#
# candidates は汎用の config.qdrant.search_priority 順（既定で wikipedia_ja が先頭）
# で渡ってくる。そのまま絞り込むとプロファイルの意図した優先順位が失われ、
# 実測では正解のある gov_faq が最後に評価されていた。
# ---------------------------------------------------------------------------

def _scoped(candidates, allowed):
    from grace.tools import RAGSearchTool

    return RAGSearchTool._apply_allowed_collections(candidates, allowed)


def test_allowed_order_wins_over_candidate_order():
    """実測ケース: プロファイル順（gov_faq 優先）が search_priority 順に勝つ。"""
    candidates = ["wikipedia_ja_5per", "gov_laws_anthropic", "gov_faq_anthropic"]
    allowed = ["gov_faq_anthropic", "gov_laws_anthropic", "wikipedia_ja"]

    assert _scoped(candidates, allowed) == [
        "gov_faq_anthropic", "gov_laws_anthropic", "wikipedia_ja_5per",
    ]


def test_partial_match_still_works():
    """部分一致（"wikipedia_ja" → "wikipedia_ja_5per"）は従来どおり効く。"""
    assert _scoped(["wikipedia_ja_5per"], ["wikipedia_ja"]) == ["wikipedia_ja_5per"]


def test_multiple_candidates_for_one_allowed_keep_candidate_order():
    """1 つの許可キーワードに複数一致する場合は candidates 側の並びを保つ。"""
    candidates = ["cc_news_2per", "cc_news_2per_anthropic"]
    assert _scoped(candidates, ["cc_news"]) == candidates


def test_no_duplicates_when_allowed_entries_overlap():
    """許可リストが重複的でも候補は重複しない。"""
    candidates = ["gov_faq_anthropic"]
    assert _scoped(candidates, ["gov", "gov_faq", "gov_faq_anthropic"]) == candidates


def test_empty_allowed_returns_candidates_unchanged():
    """許可リストが空なら制限なし（順序もそのまま）。"""
    candidates = ["a", "b"]
    assert _scoped(candidates, []) == candidates


def test_no_match_falls_back_to_all_candidates():
    """一致が 1 つも無ければ制限を適用しない（未登録環境でも動く）。"""
    candidates = ["wikipedia_ja_5per"]
    assert _scoped(candidates, ["gov_faq_anthropic"]) == candidates

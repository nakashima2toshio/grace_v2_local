# backend/tests/test_excluded_collections.py
"""横断フォールバックから **汎用コーパスを外す** 設定を固定するテスト。

## 実測が示したこと（`scripts/measure_rag_threshold.py --vertical all`）

| | n | 最小 | 中央 | 最大 |
|---|--:|--:|--:|--:|
| in_scope（拾いたい） | 12 | **0.6650** | 0.7714 | 0.8253 |
| out_scope（拾いたくない） | 5 | 0.6507 | 0.6658 | **0.7054** |

`FP シーリング 0.7054 >= TP フロア 0.6650` なので、**閾値をどこに置いても
取りこぼすか誤採用するかになる。**

ところが Top を取ったコレクションを見ると分離は明確だった。

    in_scope  … 12/12 が gov_* / saas_* / ec_*     （業務コレクション）
    out_scope …  5/5 が cc_news_* / fineweb_*      （検証用の汎用コーパス）

重なりを作っているのは業務データではない。`cc_news_2per_anthropic` は
ニュース記事の Q&A なので、時事的な質問すべてに中程度にマッチする
（天気 0.6658 / 株価 0.7054 / ノーベル賞 0.6578 / 為替 0.6813）。

### とくにまずいのは株価の 0.7054

一次閾値 0.7 を **超える**ため、緩和採用ではなく「十分なヒット」として
即採用され、`RAG score sufficient` で **Web 裏取りまで飛ばされる**。
古いニュース記事だけで「今日の株価」に答える経路に入る。

閾値では直せない（0.6650 を守ると 0.7054 が通る）ので、スコープ側で外す。

## ここで固定すること

1. 既定で汎用コーパスが横断候補から落ちること
2. **業界プロファイルが範囲を指定しているときは除外を重ねない**
   （名指しされたものは通す）
3. 除外で候補が 0 件になるなら除外しない（未登録環境でデモを殺さない）

⚠️ Qdrant にも Embedding にも接続しない。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from grace.config import GraceConfig, QdrantConfig
from grace.tools import RAGSearchTool

# 実測環境のコレクション（次元フィルタ通過後の 12 件）
MEASURED_COLLECTIONS = [
    "wikipedia_ja_5per", "cc_news_2per_768", "cc_news_2per_gemini", "cc_news_2per",
    "cc_news_2per_anthropic", "saas_docs_anthropic", "fineweb_edu_ja_5per",
    "saas_api_anthropic", "ec_faq_anthropic", "gov_faq_anthropic",
    "gov_laws_anthropic", "ec_policy_anthropic",
]
BUSINESS = [
    "saas_docs_anthropic", "saas_api_anthropic", "ec_faq_anthropic",
    "gov_faq_anthropic", "gov_laws_anthropic", "ec_policy_anthropic",
]


# =============================================================================
# ① 既定の除外
# =============================================================================

class TestDefaultExclusions:

    def test_default_drops_the_generic_corpora(self):
        """実測で FP を作っていた 6 件が落ちること。"""
        kept = _filter(MEASURED_COLLECTIONS)

        assert sorted(kept) == sorted(BUSINESS)

    def test_cc_news_variants_all_go(self):
        """同一元データの 6 バリアントをまとめて外せること（部分一致）。"""
        variants = [
            "cc_news_2per", "cc_news_2per_768", "cc_news_2per_anthropic",
            "cc_news_2per_gemini", "cc_news_2per_ollama", "cc_news_100_ollama",
        ]
        assert _filter(variants + ["gov_faq_anthropic"]) == ["gov_faq_anthropic"]

    def test_business_collections_are_never_dropped(self):
        for name in BUSINESS:
            assert _filter([name]) == [name], f"{name} が落ちている"

    def test_config_default_lists_the_measured_offenders(self):
        """実測で Top を取った汎用コーパスが既定に入っていること。"""
        excluded = QdrantConfig().excluded_collections

        assert "cc_news" in excluded, "FP 5 件中 4 件の Top"
        assert "fineweb" in excluded, "FP 5 件中 1 件の Top"

    def test_exclusion_is_logged(self, caplog):
        """何が外れたかログに出ること（出ないと「なぜ出典が減ったか」が追えない）。"""
        with caplog.at_level("INFO", logger="grace.tools"):
            _filter(MEASURED_COLLECTIONS)

        messages = " ".join(r.message for r in caplog.records)
        assert "横断検索から除外" in messages
        assert "cc_news_2per_anthropic" in messages


# =============================================================================
# ② 除外しない場合
# =============================================================================

class TestExclusionIsScoped:

    def test_empty_list_keeps_everything(self):
        assert _filter(MEASURED_COLLECTIONS, excluded=[]) == MEASURED_COLLECTIONS

    def test_does_not_empty_the_candidate_list(self, caplog):
        """全部消えるなら除外しない。

        業務コレクション未登録の環境では汎用コーパスしか無いことがある。
        そこで候補を 0 件にすると、「登録前だから出ない」のか「除外設定の
        せいで出ない」のかが利用者に区別できなくなる。
        """
        generic_only = ["wikipedia_ja_5per", "cc_news_2per_anthropic"]

        with caplog.at_level("WARNING", logger="grace.tools"):
            kept = _filter(generic_only)

        assert kept == generic_only
        assert any("0 件" in r.message for r in caplog.records), (
            "除外を見送ったことを警告すること"
        )

    def test_partial_match_semantics(self):
        """判定は `search_priority` / `allowed_collections` と同じ部分一致。"""
        assert _filter(["my_cc_news_backup", "gov_faq_anthropic"]) == ["gov_faq_anthropic"]

    def test_no_candidates_is_not_an_error(self):
        assert _filter([]) == []


# =============================================================================
# ③ 業界プロファイル指定時は除外を重ねない
# =============================================================================

class TestVerticalScopeWins:
    """`allowed_collections` があるときは、それがスコープそのもの。

    gov プロファイルは `wikipedia_ja` を明示的に含む（専用コレクション登録まで
    の代替）。ここに除外を重ねると、**プロファイルが名指ししたものが消える**。
    """

    def test_exclusions_are_skipped_when_a_profile_scopes_the_search(self):
        called = {}

        def _dynamic(self, apply_exclusions=True):
            called["apply_exclusions"] = apply_exclusions
            return list(MEASURED_COLLECTIONS)

        _run_execute(_dynamic, allowed=["gov_faq_anthropic", "wikipedia_ja"])

        assert called["apply_exclusions"] is False

    def test_exclusions_apply_when_there_is_no_scope(self):
        called = {}

        def _dynamic(self, apply_exclusions=True):
            called["apply_exclusions"] = apply_exclusions
            return list(MEASURED_COLLECTIONS)

        _run_execute(_dynamic, allowed=[])

        assert called["apply_exclusions"] is True

    def test_profile_named_collection_survives(self):
        """gov が名指しした wikipedia が候補に残ること。"""
        searched = []

        def _dynamic(self, apply_exclusions=True):
            return list(MEASURED_COLLECTIONS)

        _run_execute(_dynamic, allowed=["gov_faq_anthropic", "wikipedia_ja"],
                     record=searched)

        assert "wikipedia_ja_5per" in searched
        assert "cc_news_2per_anthropic" not in searched, "許可外は従来どおり落ちる"


# =============================================================================
# ④ 明示指定は常に通る
# =============================================================================

class TestExplicitCollectionWins:

    def test_named_collection_is_searched_even_if_excluded(self):
        """`rag_search(collection=...)` で名指しされたものは除外しない。"""
        searched = []

        def _dynamic(self, apply_exclusions=True):
            return [c for c in MEASURED_COLLECTIONS if c in BUSINESS]

        _run_execute(_dynamic, allowed=[], record=searched,
                     collection="cc_news_2per_anthropic")

        assert searched[0] == "cc_news_2per_anthropic"


# =============================================================================
# ⑤ キャッシュ
# =============================================================================

class TestCacheKeyIncludesExclusions:

    def test_changing_the_exclusions_does_not_return_a_stale_list(self):
        """除外設定を変えたのに古い一覧が返らないこと。"""
        RAGSearchTool.clear_collections_cache()

        first = _dynamic_with(excluded=["cc_news", "fineweb", "wikipedia"])
        second = _dynamic_with(excluded=[])

        assert "cc_news_2per_anthropic" not in first
        assert "cc_news_2per_anthropic" in second


# =============================================================================
# helpers
# =============================================================================

def _filter(candidates, excluded=None):
    if excluded is None:
        excluded = QdrantConfig().excluded_collections
    return RAGSearchTool._apply_excluded_collections(list(candidates), list(excluded))


def _tool(allowed=None, excluded=None):
    config = GraceConfig()
    config.qdrant.restrict_to_collection = False
    config.qdrant.allowed_collections = list(allowed or [])
    if excluded is not None:
        config.qdrant.excluded_collections = list(excluded)

    tool = RAGSearchTool.__new__(RAGSearchTool)
    tool.config = config
    tool.qdrant_url = config.qdrant.url
    tool._client = None
    tool.keyword_extractor = None
    return tool


def _run_execute(dynamic_fn, *, allowed, record=None, collection=None):
    """`execute()` を回して、どのコレクションが検索されたかを記録する。"""
    tool = _tool(allowed=allowed)

    def _search(_query, target, **_kwargs):
        if record is not None:
            record.append(target)
        return []

    with patch.object(RAGSearchTool, "_get_all_collections_dynamic", dynamic_fn), \
         patch("agent_tools.search_rag_knowledge_base_structured", side_effect=_search), \
         patch.object(RAGSearchTool, "_embed_query_once", return_value=(None, None)):
        tool.execute(query="テスト", collection=collection)


def _dynamic_with(excluded):
    """次元フィルタをスタブして `_get_all_collections_dynamic` を通す。"""
    tool = _tool(excluded=excluded)

    class _Coll:
        def __init__(self, name):
            self.name = name

    class _Resp:
        collections = [_Coll(n) for n in MEASURED_COLLECTIONS]

    class _Count:
        count = 10

    tool._client = type(
        "C", (), {
            "get_collections": lambda _self: _Resp(),
            "count": lambda _self, _n, exact=False: _Count(),
        },
    )()

    with patch.object(RAGSearchTool, "_collection_dense_dim", return_value=None):
        return tool._get_all_collections_dynamic()


@pytest.fixture(autouse=True)
def _clear_cache():
    RAGSearchTool.clear_collections_cache()
    yield
    RAGSearchTool.clear_collections_cache()

# tests/test_missing_collection_not_searched.py
"""Qdrant に無いコレクションを `collection`（名指し）で渡されても、検索しに行かない。

## 背景（実測 2026-11-03・Mac の E2E `test_saas_outage_is_escalated_by_keyword`）

    [memory] prioritized collection for query: saas_docs_anthropic
    RAGSearchTool: Search candidates: ['saas_docs_anthropic', 'saas_api_anthropic']
    ERROR agent_tools: RAGツールエラー: コレクション 'saas_docs_anthropic' はQdrantサーバーに存在しません。

実行メモリ（`logs/grace_memory.jsonl`）は、そのコレクションがあった頃の実績から
`saas_docs_anthropic` を推す。Planner はそれを `PlanStep.collection` に入れ、
RAGSearchTool は「名指し」として候補の先頭に置いていた。横断候補
（`_get_all_collections_dynamic`）は Qdrant に実在し次元が合い空でないものだけなのに、
名指しだけはその確認を通っていなかった。結果、毎回 ERROR とトレースバックが出て、
E2E は「API 呼び出しが失敗した」として落ちる（検索自体は残りのコレクションで続く）。

名指しでも、検索できないコレクション（Qdrant に無い・次元違い・空）は外す。
`restrict_to_collection`（1 つに固定する設定）は運用者の明示的な指定なので外さない
（無ければエラーで知らせる）。

⚠️ LLM・Qdrant・Embedding には接続しない。
"""
from __future__ import annotations

from types import SimpleNamespace

from grace.tools import RAGSearchTool

# Qdrant に実在する（検索できる）コレクション。saas_docs_anthropic は消えている
EXISTING = ["saas_api_anthropic", "gov_faq_anthropic"]
SAAS = ["saas_docs_anthropic", "saas_api_anthropic"]


def _tool(*, restrict=False, allowed=None):
    tool = RAGSearchTool.__new__(RAGSearchTool)
    tool.config = SimpleNamespace(
        qdrant=SimpleNamespace(
            url="http://localhost:6333",
            collection_name="saas_docs_anthropic",
            restrict_to_collection=restrict,
            allowed_collections=SAAS if allowed is None else allowed,
            excluded_collections=[],
        ),
        executor=SimpleNamespace(reasoning_min_rag_score=0.64),
    )
    tool.keyword_extractor = None
    return tool


def _stub_qdrant(monkeypatch):
    # grace_v2_local の版は apply_exclusions を受け取るので **kw で受ける
    monkeypatch.setattr(
        RAGSearchTool, "_get_all_collections_dynamic", lambda _s, **_kw: list(EXISTING),
    )


class TestMissingNamedCollection:
    def test_消えたコレクションを名指しされても候補に入れない(self, monkeypatch):
        _stub_qdrant(monkeypatch)

        assert _tool().search_candidates(collection="saas_docs_anthropic") == [
            "saas_api_anthropic",
        ]

    def test_実在するコレクションの名指しは従来どおり先頭(self, monkeypatch):
        _stub_qdrant(monkeypatch)

        assert _tool(allowed=[]).search_candidates(collection="gov_faq_anthropic")[0] \
            == "gov_faq_anthropic"

    def test_executeは消えたコレクションを検索しに行かない(self, monkeypatch):
        """実経路（execute）で、存在確認の ERROR を出す検索関数が呼ばれないこと。"""
        _stub_qdrant(monkeypatch)
        monkeypatch.setattr(
            RAGSearchTool, "_embed_query_once", lambda *_a, **_k: (None, None),
        )
        called: list[str] = []
        monkeypatch.setattr(
            "agent_tools.search_rag_knowledge_base_structured",
            lambda _q, col, **_k: called.append(col) or [],
        )

        _tool().execute(query="サービスが落ちています", collection="saas_docs_anthropic")

        assert "saas_docs_anthropic" not in called
        assert called == ["saas_api_anthropic"]

    def test_restrict_to_collectionの指定は外さない(self, monkeypatch):
        """1 つに固定する設定は運用者の明示指定。無ければ検索時のエラーで知らせる。"""
        _stub_qdrant(monkeypatch)

        assert _tool(restrict=True).search_candidates(collection="saas_docs_anthropic") == [
            "saas_docs_anthropic",
        ]

# backend/tests/test_qdrant_unavailable.py
"""Qdrant 停止時の挙動の回帰テスト。

## 何が起きていたか（実測 2026-09-02・GRACE-Review 実行）

Qdrant を起動せずに実行すると、次の 2 つが同時に起きた。

1. **無関係なコレクションを検索し続けた。**
   `_get_all_collections_dynamic` が接続失敗時に
   `config.qdrant.search_priority`（＝実在の裏付けが無い希望リスト）を返し、
   それが `allowed_collections`（`ec_ad_rules_*` 等）と 1 つも一致しないため
   `_apply_allowed_collections` が**スコープ制限を解除**。結果として
   `wikipedia_ja` / `livedoor` / `cc_news` / `japanese_text` を順に叩き、
   そのすべてが同じ接続エラーで落ちた。
2. **同じスタックトレースが 33 回出た。** 原因（Qdrant 未起動）が埋もれた。

## ここで固定すること

- 接続失敗は「コレクション 0 件」と**別物**として判定できること
- 接続失敗時に希望リストへフォールバック**しない**こと（空を返す）
- 検索先が 0 件なら、存在しないコレクションを叩かずに理由付きで打ち切ること

⚠️ 実際の Qdrant へは接続しない。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def _refused() -> Exception:
    """qdrant-client が Qdrant 停止時に送出する例外連鎖を模す。"""

    class ConnectError(Exception):
        pass

    class ResponseHandlingException(Exception):
        pass

    try:
        try:
            raise ConnectError("[Errno 61] Connection refused")
        except ConnectError as inner:
            raise ResponseHandlingException("[Errno 61] Connection refused") from inner
    except ResponseHandlingException as e:
        return e


class TestConnectionErrorDetection:
    def test_接続拒否を接続エラーと判定する(self):
        from agent_tools import is_qdrant_connection_error

        assert is_qdrant_connection_error(_refused()) is True

    def test_連鎖の内側だけが接続エラーでも検出する(self):
        from agent_tools import is_qdrant_connection_error

        try:
            raise RuntimeError("wrapped") from _refused()
        except RuntimeError as e:
            assert is_qdrant_connection_error(e) is True

    def test_無関係な例外は接続エラーにしない(self):
        from agent_tools import is_qdrant_connection_error

        assert is_qdrant_connection_error(ValueError("次元不一致")) is False


class TestCollectionsCache:
    def test_接続不能なら例外にする(self, monkeypatch):
        """空リストを返すと「コレクションが無い」と誤解されるため送出する。"""
        import agent_tools

        monkeypatch.setattr(agent_tools, "_collections_cache", None, raising=False)
        monkeypatch.setattr(agent_tools, "_collections_cache_time", 0.0, raising=False)

        def boom():
            raise _refused()

        monkeypatch.setattr(
            agent_tools, "client", SimpleNamespace(get_collections=boom), raising=False
        )
        with pytest.raises(agent_tools.QdrantConnectionError):
            agent_tools.get_existing_collections_cached()


def _tool():
    from grace.tools import RAGSearchTool

    config = SimpleNamespace(
        qdrant=SimpleNamespace(
            url="http://localhost:6333",
            allowed_collections=["ec_ad_rules", "ec_policy"],
            search_priority=["wikipedia_ja", "livedoor", "cc_news", "japanese_text"],
            excluded_collections=[],
            restrict_to_collection=False,
            collection_name="dummy",
        ),
        embedding=SimpleNamespace(dimensions=3072),
    )
    tool = RAGSearchTool.__new__(RAGSearchTool)  # __init__ の Qdrant 初期化を回避
    tool.config = config
    tool.qdrant_url = "http://localhost:6333"
    tool._client = None
    tool.keyword_extractor = None
    return tool


class TestDynamicCollections:
    def test_接続不能なら希望リストへ倒さない(self, monkeypatch):
        """`search_priority` は実在の裏付けが無い。返すとスコープ制限が外れる。"""
        from grace.tools import RAGSearchTool

        RAGSearchTool.clear_collections_cache()
        tool = _tool()

        def boom():
            raise _refused()

        monkeypatch.setattr(
            type(tool), "client", property(lambda self: SimpleNamespace(get_collections=boom))
        )
        assert tool._get_all_collections_dynamic() == []


class TestExecuteStopsEarly:
    def test_検索先が無ければ叩かずに打ち切る(self, monkeypatch):
        """存在しないコレクションを順に叩いても同じ失敗を繰り返すだけ。"""
        from grace.tools import RAGSearchTool

        RAGSearchTool.clear_collections_cache()
        tool = _tool()
        called: list = []

        def fake_search(query, collection_name, *a, **kw):
            called.append(collection_name)
            return []

        monkeypatch.setattr(
            "agent_tools.search_rag_knowledge_base_structured", fake_search
        )
        monkeypatch.setattr(
            type(tool), "_get_all_collections_dynamic", lambda self, **_kw: []
        )

        result = tool.execute(query="返品条件は？")

        assert result.success is False
        assert "Qdrant" in result.error or "コレクション" in result.error
        assert called == []

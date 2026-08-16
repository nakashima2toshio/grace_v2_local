# backend/tests/test_query_vector_reuse.py
"""同じクエリを **コレクション数ぶん埋め込み直さない** ことを固定するテスト。

## 背景（実測）

`RAGSearchTool.execute` は全コレクションを順に舐める。`agent_tools` 側は
`precomputed_query_vector` を渡さないとその都度クエリを埋め込むため、
1 質問で Embedding API がコレクション数ぶん飛んでいた:

    20:11:13→20:11:23  POST …:batchEmbedContents ×12（すべて同じクエリ）

クエリベクトルはコレクションに依存しないので、12 回とも結果は同じ。
外部 API（Gemini）なので待ち時間だけでなく課金にも効く。

## 併せて固定するもの（P2-1）

IPO ログを `logger.info` と `print` の両方で出していたため、コンソールに
同じ JSON が 2 回並んでいた（root logger が stdout に出すため）。

⚠️ Qdrant にも Embedding API にも接続しない（両方スタブに差し替える）。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from grace.config import GraceConfig
from grace.tools import RAGSearchTool

COLLECTIONS = [f"col_{i}" for i in range(12)]
DUMMY_VECTOR = [0.1] * 8
DUMMY_SPARSE = {"indices": [1, 2], "values": [0.5, 0.5]}


# =============================================================================
# ① 埋め込みは 1 回だけ
# =============================================================================

class TestQueryVectorIsComputedOnce:

    def test_embeds_once_for_many_collections(self):
        calls = _run(COLLECTIONS)

        assert calls["dense"] == 1, (
            f"クエリを {calls['dense']} 回埋め込んでいる"
            f"（{len(COLLECTIONS)} コレクションでも 1 回で足りる）"
        )

    def test_sparse_is_also_computed_once(self):
        calls = _run(COLLECTIONS)
        assert calls["sparse"] == 1

    def test_every_collection_receives_the_same_vector(self):
        calls = _run(COLLECTIONS)

        assert len(calls["passed_vectors"]) == len(COLLECTIONS)
        assert all(v is DUMMY_VECTOR for v in calls["passed_vectors"]), (
            "使い回していない（下位が自前で再計算する）"
        )

    def test_single_collection_delegates_to_the_caller(self):
        """1 コレクションなら経路を増やさない（呼び出し回数は同じ 1 回）。"""
        calls = _run(["col_0"])

        assert calls["dense"] == 0
        assert calls["passed_vectors"] == [], "precomputed を渡さず下位に任せる"


# =============================================================================
# ② 失敗しても検索は止まらない
# =============================================================================

class TestDegradesGracefully:

    def test_dense_failure_falls_back_to_per_collection(self, caplog):
        """埋め込みに失敗したら、最適化が無い従来動作へ戻るだけであること。"""
        with caplog.at_level("WARNING", logger="grace.tools"):
            calls = _run(COLLECTIONS, dense_error=RuntimeError("embed failed"))

        assert calls["searched"] == len(COLLECTIONS), "検索そのものは続けること"
        assert calls["passed_vectors"] == [], "壊れたベクトルを配らないこと"
        assert any("事前埋め込みに失敗" in r.message for r in caplog.records)

    def test_sparse_failure_still_reuses_the_dense_vector(self):
        """sparse は任意。落ちても dense の使い回しは維持する。"""
        calls = _run(COLLECTIONS, sparse_error=RuntimeError("no fastembed"))

        assert calls["dense"] == 1
        assert len(calls["passed_vectors"]) == len(COLLECTIONS)
        assert calls["passed_sparse"] == [], "sparse は渡さない（下位が dense へ倒れる）"


# =============================================================================
# ③ コンソール出力を二重にしない（P2-1）
# =============================================================================

class TestNoDuplicateConsoleOutput:

    def test_ipo_output_is_not_printed(self, capsys):
        """`print` を併用しないこと。

        root logger が stdout に出すため、`logger.info` と `print` の両方を
        呼ぶとコンソールに同じ JSON が 2 回並ぶ（実測ログが倍に膨れていた）。
        """
        _run(COLLECTIONS, score=0.80)   # 採用されないと IPO ログ自体が出ない

        assert "[RAG SEARCH IPO: OUTPUT]" not in capsys.readouterr().out

    def test_ipo_output_still_goes_to_the_log(self, caplog):
        """消したのは print であって、ログ自体は残すこと。"""
        with caplog.at_level("INFO", logger="grace.tools"):
            _run(COLLECTIONS, score=0.80)

        hits = [r for r in caplog.records if "[RAG SEARCH IPO: OUTPUT]" in r.message]
        assert len(hits) == 1, f"IPO ログが {len(hits)} 件（1 件であるべき）"

    def test_per_collection_progress_is_not_printed(self, capsys):
        _run(COLLECTIONS)
        assert "🔍 Searching collection" not in capsys.readouterr().out


# =============================================================================
# helpers
# =============================================================================

def _run(collections, *, dense_error=None, sparse_error=None, score=0.30) -> dict:
    """コレクション一覧を与えて `RAGSearchTool.execute()` を回し、呼び出しを数える。

    `score` 既定の 0.30 は採用の下限（0.55）未満なので、探索が途中で
    打ち切られず**全コレクションを舐める**（埋め込み回数を数えたいため）。
    採用後の経路（IPO ログ等）を見たいときは 0.80 を渡す。
    """
    calls = {
        "dense": 0, "sparse": 0, "searched": 0,
        "passed_vectors": [], "passed_sparse": [],
    }

    def _embed_query(_query):
        calls["dense"] += 1
        if dense_error:
            raise dense_error
        return DUMMY_VECTOR

    def _embed_sparse(_query):
        calls["sparse"] += 1
        if sparse_error:
            raise sparse_error
        return DUMMY_SPARSE

    def _search(_query, collection, **kwargs):
        calls["searched"] += 1
        if "precomputed_query_vector" in kwargs:
            calls["passed_vectors"].append(kwargs["precomputed_query_vector"])
        if "precomputed_sparse_vector" in kwargs:
            calls["passed_sparse"].append(kwargs["precomputed_sparse_vector"])
        return [{"score": score, "id": 1, "payload": {"answer": "本文", "source": "x.csv"}}]

    config = GraceConfig()
    config.qdrant.restrict_to_collection = False
    config.qdrant.allowed_collections = []

    tool = RAGSearchTool.__new__(RAGSearchTool)
    tool.config = config
    tool.qdrant_url = config.qdrant.url
    tool._client = None
    tool.keyword_extractor = None

    with patch.object(
        RAGSearchTool, "_get_all_collections_dynamic", return_value=list(collections)
    ), patch("agent_tools.search_rag_knowledge_base_structured", side_effect=_search), \
         patch("qdrant_client_wrapper.embed_query", side_effect=_embed_query), \
         patch("qdrant_client_wrapper.embed_sparse_query_unified", side_effect=_embed_sparse):
        tool.execute(query="明日の東京の天気は？")

    return calls


@pytest.fixture(autouse=True)
def _quiet(caplog):
    yield

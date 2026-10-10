"""`GeminiEmbedding.embed_texts()` のバッチ呼び出しを固定するテスト。

`gemini-embedding-2`（マルチモーダル対応）は、`embed_content(contents=[文字列, ...])` を
**1 つの入力の複数パート**として扱い、N 件送ってもベクトルを 1 本しか返さない
（2026-09-26 に google-genai 2.25.0 と実 API で確認: 3 件 → 001 は 3 本、2 は 1 本）。
以前の `embed_texts()` は文字列リストをそのまま渡し、返ってきた本数を確かめずに足していた。
そのため 2 へ切り替えると、100 件のバッチが 1 本になり、以降のテキストとベクトルの対応が
すべてずれたまま Qdrant 登録が「成功」していた。

スタブは実 API のこの挙動をまねる（文字列リスト → 1 本、Content リスト → N 本）。
"""

from types import SimpleNamespace

import helper.helper_embedding as he
from helper.helper_embedding import GeminiEmbedding, separate_contents


class _Embedding2Like:
    """gemini-embedding-2 の実測どおりに振る舞う `client.models`。"""

    def __init__(self):
        self.calls = []

    def embed_content(self, model, contents, config=None):
        self.calls.append(contents)
        if isinstance(contents, list) and contents and isinstance(contents[0], str):
            # 文字列リストは 1 入力扱い → 1 本
            return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.0, 0.0, 0.0])])
        return SimpleNamespace(
            embeddings=[
                SimpleNamespace(values=[float(len(c.parts[0].text)), 1.0, 1.0]) for c in contents
            ]
        )


def _client(models):
    emb = GeminiEmbedding.__new__(GeminiEmbedding)
    emb.client = SimpleNamespace(models=models)
    emb.model = "stub-embedding"
    emb._dims = 3
    return emb


def test_embed_texts_returns_one_vector_per_text(monkeypatch):
    monkeypatch.setattr(he.time, "sleep", lambda _s: None)
    texts = ["a", "bb", "ccc"]
    vectors = _client(_Embedding2Like()).embed_texts(texts)
    assert len(vectors) == len(texts)
    # 並びがテキストと対応している（本文から決まる値）
    assert [v[0] for v in vectors] == [1.0, 2.0, 3.0]


def test_embed_texts_rejects_count_mismatch(monkeypatch, caplog):
    """件数が合わない応答は足さない（ゼロ埋めでも対応は保つ）。"""
    monkeypatch.setattr(he.time, "sleep", lambda _s: None)

    class _Short:
        def embed_content(self, model, contents, config=None):
            return SimpleNamespace(embeddings=[SimpleNamespace(values=[1.0, 1.0, 1.0])])

    vectors = _client(_Short()).embed_texts(["a", "b", "c"])
    assert len(vectors) == 3
    assert "件数が入力と一致しません" in caplog.text


def test_separate_contents_wraps_each_text():
    contents = separate_contents(["x", "y"])
    assert [c.parts[0].text for c in contents] == ["x", "y"]
    assert all(len(c.parts) == 1 for c in contents)

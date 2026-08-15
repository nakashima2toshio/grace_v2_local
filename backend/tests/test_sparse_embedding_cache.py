# backend/tests/test_sparse_embedding_cache.py
"""Sparse Embedding の初期化失敗を **1 回で諦める** ことを固定するテスト。

## 何を守っているのか

`get_sparse_embedding_client()` は成功だけをキャッシュしていた。構築が例外を
投げると `_sparse_client_instance` は None のままなので、呼び出しのたびに
モデル構築＝HuggingFace へのダウンロードをやり直していた。

呼び出し側（`agent_tools.py`）はこの例外を `logger.debug` で握り潰すため、

    検索のたびに再ダウンロード → 「Local file sizes do not match the
    metadata」警告 → 失敗 → debug ログで沈黙

を コレクション数 × リプラン回数 ぶん繰り返す。実測ログを警告で埋めていた
のがこれで、しかも本当の原因（例外の中身）はどこにも出ていなかった。

ここでは次の 2 点を固定する:
  1. 失敗もキャッシュし、2 回目以降は**構築を試みない**
  2. 初回だけ warning で実際の例外を出す（原因が見える）

⚠️ 実際に fastembed のモデルをダウンロードすることはない。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from helper import helper_embedding_sparse as hes


@pytest.fixture(autouse=True)
def _clean_cache():
    """テスト間でキャッシュを持ち越さない。"""
    hes.reset_sparse_embedding_client_cache()
    yield
    hes.reset_sparse_embedding_client_cache()


class _Boom(RuntimeError):
    pass


class TestNegativeCache:

    def test_failure_is_attempted_only_once(self):
        """2 回目以降は構築（＝再ダウンロード）を試みないこと。"""
        with patch.object(
            hes, "SparseEmbeddingClient", side_effect=_Boom("download failed")
        ) as ctor:
            for _ in range(5):
                with pytest.raises(_Boom):
                    hes.get_sparse_embedding_client()

        assert ctor.call_count == 1, (
            f"失敗をキャッシュしていない（{ctor.call_count} 回構築を試みた）＝"
            "検索のたびに HuggingFace へ再ダウンロードする"
        )

    def test_same_exception_is_reraised(self):
        """呼び出し側が例外の中身で分岐できるよう、同じ例外を返す。"""
        boom = _Boom("download failed")
        with patch.object(hes, "SparseEmbeddingClient", side_effect=boom):
            with pytest.raises(_Boom) as first:
                hes.get_sparse_embedding_client()
            with pytest.raises(_Boom) as second:
                hes.get_sparse_embedding_client()

        assert first.value is boom
        assert second.value is boom

    def test_warns_once_with_the_real_cause(self, caplog):
        """原因が debug に沈まず、初回だけ warning で出ること。"""
        with caplog.at_level("WARNING", logger=hes.__name__):
            with patch.object(
                hes, "SparseEmbeddingClient", side_effect=_Boom("download failed")
            ):
                for _ in range(3):
                    with pytest.raises(_Boom):
                        hes.get_sparse_embedding_client()

        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1, f"warning が {len(warnings)} 件（1 件であるべき）"
        assert "download failed" in warnings[0].message

    def test_failure_is_per_model(self):
        """別モデルを指定したときは改めて試す。"""
        with patch.object(
            hes, "SparseEmbeddingClient", side_effect=_Boom("x")
        ) as ctor:
            with pytest.raises(_Boom):
                hes.get_sparse_embedding_client("model-a")
            with pytest.raises(_Boom):
                hes.get_sparse_embedding_client("model-b")
            with pytest.raises(_Boom):
                hes.get_sparse_embedding_client("model-a")

        assert ctor.call_count == 2


class TestPositiveCache:
    """成功時の従来挙動（シングルトン）を壊していないこと。"""

    def test_success_is_built_once(self):
        built = _fake_client(hes.DEFAULT_SPARSE_MODEL)
        with patch.object(hes, "SparseEmbeddingClient", return_value=built) as ctor:
            first = hes.get_sparse_embedding_client()
            second = hes.get_sparse_embedding_client()

        assert first is second is built
        assert ctor.call_count == 1

    def test_none_resolves_to_default_model(self):
        built = _fake_client(hes.DEFAULT_SPARSE_MODEL)
        with patch.object(hes, "SparseEmbeddingClient", return_value=built) as ctor:
            hes.get_sparse_embedding_client(None)
            hes.get_sparse_embedding_client()

        assert ctor.call_count == 1
        assert ctor.call_args.kwargs["model_name"] == hes.DEFAULT_SPARSE_MODEL

    def test_switching_model_rebuilds(self):
        with patch.object(hes, "SparseEmbeddingClient") as ctor:
            ctor.side_effect = lambda model_name: _fake_client(model_name)
            a = hes.get_sparse_embedding_client("model-a")
            b = hes.get_sparse_embedding_client("model-b")

        assert a is not b
        assert ctor.call_count == 2

    def test_reset_clears_both_caches(self):
        with patch.object(hes, "SparseEmbeddingClient", side_effect=_Boom("x")):
            with pytest.raises(_Boom):
                hes.get_sparse_embedding_client()

        hes.reset_sparse_embedding_client_cache()

        built = _fake_client(hes.DEFAULT_SPARSE_MODEL)
        with patch.object(hes, "SparseEmbeddingClient", return_value=built) as ctor:
            assert hes.get_sparse_embedding_client() is built
            assert ctor.call_count == 1


def _fake_client(model_name: str):
    class _Fake:
        pass

    fake = _Fake()
    fake.model_name = model_name
    return fake

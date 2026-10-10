"""qa_qdrant.register_to_qdrant（CSV → Qdrant 登録）の結合テスト。

本番の登録経路をそのまま実 Qdrant へ流す。差し替えるのは 2 点だけ:

- ``embed_texts_for_qdrant`` … Gemini を呼ばず、テキストから決まる固定ベクトルを返す
- ``create_qdrant_client`` … ``GRACE_IT_QDRANT_URL`` を尊重するため fixture のクライアントを返す

登録後の件数・ペイロード・再登録の冪等性を実 Qdrant 側から確かめる。
"""

import hashlib

import pandas as pd
import pytest

from config import GeminiConfig
from qa_qdrant import register_to_qdrant as reg

pytestmark = pytest.mark.integration


def _fake_vector(text: str) -> list:
    """テキストごとに決まる 3072 次元のベクトル（実 Embedding の代わり）。"""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    head = [b / 255.0 + 0.01 for b in digest]
    return (head * (GeminiConfig.EMBEDDING_DIMS // len(head) + 1))[: GeminiConfig.EMBEDDING_DIMS]


@pytest.fixture
def live_register(monkeypatch, qdrant_client):
    embed_calls = []

    def fake_embed(texts, *args, **kwargs):
        embed_calls.append(list(texts))
        return [_fake_vector(t) for t in texts]

    monkeypatch.setattr(reg, "embed_texts_for_qdrant", fake_embed)
    monkeypatch.setattr(reg, "create_qdrant_client", lambda *a, **k: qdrant_client)
    return embed_calls


def _write_csv(tmp_path, rows, name="qa_pairs_sample_20260101_120000.csv"):
    path = tmp_path / name
    pd.DataFrame(rows, columns=["question", "answer"]).to_csv(path, index=False)
    return str(path)


ROWS = [
    ("返品はできますか？", "到着後 8 日以内なら可能です。"),
    ("送料はいくら？", "全国一律 500 円です。"),
    ("返品はできますか？", "到着後 8 日以内なら可能です。"),  # 重複行
    ("支払い方法は？", "クレジットカードと代引きです。"),
]


def _register(path, collection, **kwargs):
    return reg.register_to_qdrant(
        input_file=path, collection_name=collection, create_ui_csv=False, **kwargs
    )


def test_registers_unique_rows_with_embedding_metadata(qdrant_client, temp_collection, live_register, tmp_path):
    path = _write_csv(tmp_path, ROWS)

    assert _register(path, temp_collection, recreate=True) is True

    assert qdrant_client.count(temp_collection, exact=True).count == 3
    # 重複行は Embedding 前に落とす（費用）。件数だけだと、内容ベース ID で同じ点に
    # 上書きされるため重複除去が無くても 3 件になり、検出できない。
    assert sum(len(c) for c in live_register) == 3
    points, _ = qdrant_client.scroll(temp_collection, limit=10)
    payload = points[0].payload
    assert payload["embedding_provider"] == "gemini"
    assert payload["embedding_model"] == GeminiConfig.EMBEDDING_MODEL
    assert payload["domain"] == temp_collection
    # ファイル名の日時サフィックスは落として記録する
    assert payload["source"] == "qa_pairs_sample.csv"
    params = qdrant_client.get_collection(temp_collection).config.params
    assert params.vectors.size == GeminiConfig.EMBEDDING_DIMS


def test_reregistering_without_recreate_is_idempotent(qdrant_client, temp_collection, live_register, tmp_path):
    path = _write_csv(tmp_path, ROWS)

    assert _register(path, temp_collection, recreate=True) is True
    assert _register(path, temp_collection, recreate=False) is True

    assert qdrant_client.count(temp_collection, exact=True).count == 3


def test_small_batches_register_every_row(qdrant_client, temp_collection, live_register, tmp_path):
    """batch_size=1 で Embedding 先読みのパイプラインを複数周させても取りこぼさない。"""
    rows = [(f"質問{i}", f"回答{i}") for i in range(7)]
    path = _write_csv(tmp_path, rows)

    assert _register(path, temp_collection, recreate=True, batch_size=1, embed_workers=2) is True

    assert qdrant_client.count(temp_collection, exact=True).count == 7
    assert sum(len(c) for c in live_register) == 7


def test_registered_points_are_searchable_by_their_own_vector(qdrant_client, temp_collection, live_register, tmp_path):
    """登録したベクトルで検索すると、その Q/A が 1 位で返る（ベクトルとペイロードの対応）。"""
    import qdrant_client_wrapper as wrapper

    path = _write_csv(tmp_path, ROWS)
    assert _register(path, temp_collection, recreate=True) is True

    q, a = ROWS[1]
    hits = wrapper.search_collection(qdrant_client, temp_collection, _fake_vector(f"{q}\n{a}"), limit=1)

    assert hits[0]["payload"]["question"] == q

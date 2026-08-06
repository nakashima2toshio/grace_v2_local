# backend/tests/test_data_pipeline.py
"""データ準備パイプラインのラッパ層（`services/data_pipeline_service.py`）のテスト。

**実 Qdrant は不要**（CI の必須条件）。Qdrant を触る関数にはスタブクライアントを渡す。

対象:
- `services/data_pipeline_service.py` — パス検証・DataFrame 変換・削除
- `backend/app/api/qdrant.py` — 一覧・詳細・ポイント・ヘルス・ファイル一覧
"""
from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from services.data_pipeline_service import (
    ALLOWED_INPUT_DIRS,
    PathNotAllowedError,
    collection_columns,
    collection_exists,
    dataframe_to_records,
    delete_collection,
    list_input_files,
    resolve_allowed_dir,
    resolve_input_file,
)

client = TestClient(app)


# =============================================================================
# スタブ
# =============================================================================

class _StubCollection:
    def __init__(self, name: str):
        self.name = name


class _StubCollectionsResponse:
    def __init__(self, names):
        self.collections = [_StubCollection(n) for n in names]


class StubQdrantClient:
    """`QdrantClient` の最小スタブ。呼ばれたメソッドを記録する。"""

    def __init__(self, names=("faq_anthropic", "gov_anthropic")):
        self._names = list(names)
        self.deleted: list[str] = []
        self.raise_on_get_collections = False

    def get_collections(self):
        if self.raise_on_get_collections:
            raise ConnectionError("Qdrant に接続できません")
        return _StubCollectionsResponse(self._names)

    def delete_collection(self, collection_name: str):
        if collection_name not in self._names:
            raise ValueError(f"not found: {collection_name}")
        self._names.remove(collection_name)
        self.deleted.append(collection_name)


@pytest.fixture
def stub_client(monkeypatch):
    """API が引く `get_qdrant_client` をスタブへ差し替える。"""
    stub = StubQdrantClient()
    import qdrant_client_wrapper

    monkeypatch.setattr(qdrant_client_wrapper, "get_qdrant_client", lambda: stub)
    return stub


# =============================================================================
# パス検証（ホワイトリスト＋resolve の二段）
# =============================================================================

@pytest.mark.parametrize("bad", ["etc", "../etc", "OUTPUT/..", "", "/etc", "logs"])
def test_resolve_allowed_dir_rejects_outside(bad):
    """許可ディレクトリ以外は拒否する。"""
    with pytest.raises(PathNotAllowedError):
        resolve_allowed_dir(bad)


def test_resolve_allowed_dir_accepts_whitelisted():
    for name in ALLOWED_INPUT_DIRS:
        assert resolve_allowed_dir(name).name == name


@pytest.mark.parametrize(
    "bad",
    [
        "a.csv",                    # ディレクトリ指定なし
        "OUTPUT/../../etc/passwd",  # 区切りが多すぎる
        "OUTPUT/",                  # ファイル名が空
        "a/b/c.csv",                # 階層が深い
        "OUTPUT/..",                # 親を指す
        "logs/app.log",             # 許可外ディレクトリ
    ],
)
def test_resolve_input_file_rejects_traversal(bad):
    """`ディレクトリ名/ファイル名` 以外の形は通さない。"""
    with pytest.raises((PathNotAllowedError, FileNotFoundError)):
        resolve_input_file(bad)


def test_resolve_input_file_reads_real_file(tmp_path):
    """許可ディレクトリ内の実ファイルは解決できる。"""
    (tmp_path / "OUTPUT").mkdir()
    target = tmp_path / "OUTPUT" / "sample.csv"
    target.write_text("Text\nあ\n", encoding="utf-8")

    resolved = resolve_input_file("OUTPUT/sample.csv", base=tmp_path)
    assert resolved == target.resolve()


def test_list_input_files_sorted_by_mtime(tmp_path):
    """更新日時の降順で返し、対象外の拡張子は含めない。"""
    out = tmp_path / "OUTPUT"
    out.mkdir()
    (out / "old.csv").write_text("a", encoding="utf-8")
    (out / "new.csv").write_text("b", encoding="utf-8")
    (out / "ignored.json").write_text("{}", encoding="utf-8")
    # mtime を明示的に差をつける
    import os

    os.utime(out / "old.csv", (1_000_000, 1_000_000))
    os.utime(out / "new.csv", (2_000_000, 2_000_000))

    files = list_input_files("OUTPUT", base=tmp_path)
    assert [f["name"] for f in files] == ["new.csv", "old.csv"]
    # 絶対パスは返さない
    assert all(f["path"].startswith("OUTPUT/") for f in files)


def test_list_input_files_missing_dir_is_empty(tmp_path):
    """ディレクトリが無くてもエラーにせず空リストを返す。"""
    assert list_input_files("qa_output", base=tmp_path) == []


# =============================================================================
# DataFrame → JSON
# =============================================================================

def test_dataframe_to_records_replaces_nan_with_none():
    """**NaN を None に寄せる。** JSON に NaN は無く、そのままだと不正な JSON になる。"""
    df = pd.DataFrame([{"ID": 1, "q": "あ"}, {"ID": 2, "extra": 5}])
    rows = dataframe_to_records(df)

    assert rows[0]["extra"] is None
    assert not any(
        isinstance(v, float) and v != v  # NaN は自分自身と等しくない
        for row in rows
        for v in row.values()
    )


def test_dataframe_to_records_handles_empty_and_none():
    assert dataframe_to_records(pd.DataFrame()) == []
    assert dataframe_to_records(None) == []


def test_collection_columns_preserves_first_seen_order():
    """列はコレクションごとに違うので、出現順で並べる。"""
    rows = [
        {"ID": 1, "question": "あ"},
        {"ID": 2, "answer": "い", "question": "う"},
    ]
    assert collection_columns(rows) == ["ID", "question", "answer"]


# =============================================================================
# コレクション操作
# =============================================================================

def test_delete_collection_success():
    stub = StubQdrantClient()
    assert delete_collection(stub, "faq_anthropic") is True
    assert stub.deleted == ["faq_anthropic"]


def test_delete_collection_returns_false_on_error():
    """存在しない・失敗しても**例外を投げず False**（呼び出し側で扱う）。"""
    stub = StubQdrantClient()
    assert delete_collection(stub, "missing") is False
    assert stub.deleted == []


def test_collection_exists():
    stub = StubQdrantClient()
    assert collection_exists(stub, "faq_anthropic") is True
    assert collection_exists(stub, "missing") is False


def test_collection_exists_false_on_connection_error():
    """接続失敗時も例外を投げずに False を返す。"""
    stub = StubQdrantClient()
    stub.raise_on_get_collections = True
    assert collection_exists(stub, "faq_anthropic") is False
# =============================================================================
# API
# =============================================================================

def test_health_returns_200_even_when_qdrant_down(monkeypatch):
    """**Qdrant が落ちていても 200。** 画面で案内を出し分けるため 503 にしない。"""
    import services.qdrant_service as qs

    class DownChecker:
        def check_qdrant(self):
            return False, "Qdrant に接続できません", None

    monkeypatch.setattr(qs, "QdrantHealthChecker", DownChecker)

    response = client.get("/api/qdrant/health")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert "接続できません" in body["message"]


def test_list_collections(stub_client, monkeypatch):
    import services.qdrant_service as qs

    monkeypatch.setattr(
        qs,
        "get_all_collections",
        lambda _c: [
            {"name": "faq_anthropic", "points_count": 120, "status": "green"},
            {"name": "gov_anthropic", "points_count": 0, "status": "Error"},
        ],
    )

    response = client.get("/api/qdrant/collections")
    assert response.status_code == 200
    body = response.json()
    assert [c["name"] for c in body] == ["faq_anthropic", "gov_anthropic"]
    assert body[0]["points_count"] == 120


def test_get_collection_404_when_missing(stub_client):
    response = client.get("/api/qdrant/collections/does_not_exist")
    assert response.status_code == 404


def test_get_collection_points_returns_columns(stub_client, monkeypatch):
    """payload のキーが可変なので、列名を別に返す。"""
    import services.qdrant_service as qs

    class StubFetcher:
        def __init__(self, _client):
            pass

        def fetch_collection_points(self, _name, limit=50):
            return pd.DataFrame([
                {"ID": 1, "question": "あ"},
                {"ID": 2, "question": "い", "answer": "う"},
            ])

    monkeypatch.setattr(qs, "QdrantDataFetcher", StubFetcher)

    response = client.get("/api/qdrant/collections/faq_anthropic/points?limit=2")
    assert response.status_code == 200
    body = response.json()
    assert body["columns"] == ["ID", "question", "answer"]
    assert len(body["rows"]) == 2
    # 欠けたキーは None で埋まる（NaN ではない）
    assert body["rows"][0]["answer"] is None


def test_get_collection_points_limit_is_validated(stub_client):
    """limit の範囲外は 422（1〜500）。"""
    assert client.get("/api/qdrant/collections/faq_anthropic/points?limit=0").status_code == 422
    assert client.get("/api/qdrant/collections/faq_anthropic/points?limit=9999").status_code == 422


def test_list_files_rejects_disallowed_dir():
    """許可外ディレクトリは 400（500 にしない）。"""
    response = client.get("/api/files?dir=logs")
    assert response.status_code == 400
    assert "許可されていない" in response.json()["detail"]


def test_list_files_returns_allowed_dirs():
    """画面がディレクトリ選択肢を作れるよう、許可一覧を同梱する。"""
    response = client.get("/api/files?dir=OUTPUT")
    assert response.status_code == 200
    body = response.json()
    assert body["dir"] == "OUTPUT"
    assert set(body["allowed_dirs"]) == set(ALLOWED_INPUT_DIRS)

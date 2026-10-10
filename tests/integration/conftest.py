"""結合テスト（実 Qdrant / 実 Redis）の共通フィクスチャ。

``tests`` の他のテストはスタブで外部依存を消すが、ここに置くテストは
**docker-compose の Qdrant / Redis に実際に接続する**。API キーは使わない
（Embedding は固定ベクトル、LLM は差し替えた生成器で代用する）。

- サービスが起動していなければ **skip**（CI・未起動のローカルを赤にしない）。
  理由に起動コマンドを出す。
- クラウド VM（Claude Code on the web）では ``.claude/hooks/session-start.sh`` が
  セッション開始時に両方を起動するので、そのまま走る。
- 共用 Qdrant（grace_v2 と同じもの）を壊さないよう、作るコレクションは
  ``grace_it_<乱数>`` だけで、テストごとに必ず削除する。既存コレクションには触れない。
- Redis は **db 15** を使う（Celery の既定 db 0 と分ける）。Mac で常駐している
  本物のワーカーにテストのタスクを拾わせないため。

環境変数:
    GRACE_SKIP_INTEGRATION=1   起動していても結合テストを skip する
    GRACE_IT_QDRANT_URL        既定 ``config.QdrantConfig.URL``（http://localhost:6333）
    GRACE_IT_REDIS_URL         既定 ``redis://localhost:6379/15``
"""

import os
import socket
import uuid
from urllib.parse import urlparse

import pytest

from config import QdrantConfig

START_HINT = "docker compose -f docker-compose/docker-compose.yml up -d"
QDRANT_URL = os.getenv("GRACE_IT_QDRANT_URL", QdrantConfig.URL)
REDIS_URL = os.getenv("GRACE_IT_REDIS_URL", "redis://localhost:6379/15")
COLLECTION_PREFIX = "grace_it_"


def _reachable(url: str, default_port: int) -> bool:
    parsed = urlparse(url)
    try:
        with socket.create_connection(
            (parsed.hostname or "localhost", parsed.port or default_port), timeout=1.0
        ):
            return True
    except OSError:
        return False


def _skip_if_disabled() -> None:
    if os.getenv("GRACE_SKIP_INTEGRATION") == "1":
        pytest.skip("GRACE_SKIP_INTEGRATION=1 のため結合テストを skip")


@pytest.fixture(scope="session")
def qdrant_client():
    """実 Qdrant へのクライアント。接続できなければ skip。"""
    _skip_if_disabled()
    if not _reachable(QDRANT_URL, 6333):
        pytest.skip(f"Qdrant に接続できない（{QDRANT_URL}）。起動: {START_HINT}")

    from qdrant_client import QdrantClient

    client = QdrantClient(url=QDRANT_URL, timeout=10)
    client.get_collections()  # ポートは開いていても応答しない場合はここで落とす
    yield client
    client.close()


@pytest.fixture
def temp_collection(qdrant_client):
    """テスト専用のコレクション名。テスト後に必ず削除する。"""
    import qdrant_client_wrapper

    name = f"{COLLECTION_PREFIX}{uuid.uuid4().hex[:12]}"
    yield name
    # search_collection() はコレクション名でベクトル設定をキャッシュする
    qdrant_client_wrapper._vector_config_cache.pop(name, None)
    if qdrant_client.collection_exists(name):
        qdrant_client.delete_collection(name)


@pytest.fixture(scope="session")
def redis_url():
    """実 Redis（db 15）の URL。接続できなければ skip。"""
    _skip_if_disabled()
    if not _reachable(REDIS_URL, 6379):
        pytest.skip(f"Redis に接続できない（{REDIS_URL}）。起動: {START_HINT}")

    import redis

    redis.Redis.from_url(REDIS_URL, socket_timeout=2).ping()
    return REDIS_URL

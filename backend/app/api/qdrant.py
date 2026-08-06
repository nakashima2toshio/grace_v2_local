# backend/app/api/qdrant.py
"""Qdrant コレクションの参照 API（読み取り専用）。

チャンキング → Q/A 生成 → Qdrant 登録 というデータ準備パイプラインのうち、
**副作用のない参照系**だけをここに置く。登録・削除はジョブ基盤（`core/jobs.py`）と
HITL CONFIRM を経由するため、別ルータになる。

実処理は `services/qdrant_service.py` が持つ。本モジュールは
`services/data_pipeline_service.py` を挟んで JSON 化するだけの薄い層である。

## Qdrant 未起動時の扱い

`GET /api/qdrant/health` は **Qdrant が落ちていても 200 を返し**、
`available: false` と理由を本文に載せる。503 にすると画面側でエラーバナーと
「Qdrant を起動してください」という案内を出し分けられないため。
一方、一覧・詳細は Qdrant が要るので 503 を返す。
"""
from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, HTTPException, Query

from backend.app.schemas import (
    CollectionDetail,
    CollectionInfo,
    CollectionPoints,
    InputFileInfo,
    InputFileListResponse,
    QdrantHealth,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["qdrant"])


def _get_client():
    """Qdrant クライアントを取得する。接続不可なら 503。

    `qdrant_client_wrapper.get_qdrant_client()` はシングルトンを返すが、
    **生成時点では接続確認をしない**（実際にリクエストを送るまで失敗が分からない）。
    そのため呼び出し側で例外を捕まえて 503 に変換する。
    """
    from qdrant_client_wrapper import get_qdrant_client

    try:
        return get_qdrant_client()
    except Exception as e:
        logger.error(f"Qdrant クライアントの生成に失敗: {e}")
        raise HTTPException(
            status_code=503,
            detail=(
                "Qdrant へ接続できません。"
                "docker-compose -f docker-compose/docker-compose.yml up -d で起動してください。"
            ),
        )


@router.get("/qdrant/health", response_model=QdrantHealth)
def qdrant_health() -> QdrantHealth:
    """Qdrant の稼働確認。**落ちていても 200 を返す**（本文の available で判定）。"""
    from services.qdrant_service import QDRANT_CONFIG, QdrantHealthChecker

    url = QDRANT_CONFIG.get("url")
    try:
        checker = QdrantHealthChecker()
        available, message, info = checker.check_qdrant()
    except Exception as e:
        return QdrantHealth(available=False, message=f"確認に失敗しました: {e}", url=url)

    collections_count = None
    if isinstance(info, dict):
        collections = info.get("collections")
        if isinstance(collections, list):
            collections_count = len(collections)
        elif isinstance(info.get("collections_count"), int):
            collections_count = info["collections_count"]

    return QdrantHealth(
        available=available,
        message=message,
        url=url,
        collections_count=collections_count,
    )


@router.get("/qdrant/collections", response_model=List[CollectionInfo])
def list_collections() -> List[CollectionInfo]:
    """コレクション一覧（名前・件数・ステータス）。"""
    from services.qdrant_service import get_all_collections

    client = _get_client()
    try:
        collections = get_all_collections(client)
    except Exception as e:
        logger.error(f"コレクション一覧の取得に失敗: {e}")
        raise HTTPException(status_code=503, detail=f"Qdrant からの取得に失敗しました: {e}")

    return [
        CollectionInfo(
            name=str(c.get("name", "")),
            points_count=int(c.get("points_count") or 0),
            status=str(c.get("status", "unknown")),
        )
        for c in collections
    ]


@router.get("/qdrant/collections/{name}", response_model=CollectionDetail)
def get_collection(name: str) -> CollectionDetail:
    """コレクションの詳細（ベクトル設定＋データ元の集計）。"""
    from services.data_pipeline_service import collection_exists
    from services.qdrant_service import QdrantDataFetcher

    client = _get_client()
    if not collection_exists(client, name):
        raise HTTPException(status_code=404, detail=f"コレクションが存在しません: {name}")

    fetcher = QdrantDataFetcher(client)
    # fetch_* は例外を投げず {"error": ...} を返す実装なので、そのまま拾って載せる
    info = fetcher.fetch_collection_info(name)
    if "error" in info:
        return CollectionDetail(name=name, error=str(info["error"]))

    source_info = fetcher.fetch_collection_source_info(name)
    config = info.get("config") or {}

    return CollectionDetail(
        name=name,
        points_count=int(info.get("points_count") or 0),
        vectors_count=info.get("vectors_count"),
        indexed_vectors=info.get("indexed_vectors"),
        status=str(info.get("status", "unknown")),
        vector_size=config.get("vector_size"),
        distance=config.get("distance"),
        sources=source_info.get("sources", {}) if "error" not in source_info else {},
        sample_size=int(source_info.get("sample_size") or 0),
        error=source_info.get("error"),
    )


@router.get("/qdrant/collections/{name}/points", response_model=CollectionPoints)
def get_collection_points(
    name: str,
    limit: int = Query(default=50, ge=1, le=500, description="取得件数"),
) -> CollectionPoints:
    """コレクションのポイントをプレビューする。

    payload のキーはコレクションごとに違うため、列名を `columns` として別に返す
    （画面はこの順で列を並べる）。長い文字列は `fetch_collection_points` 側で
    200 文字に切り詰められている。
    """
    from services.data_pipeline_service import collection_columns, dataframe_to_records
    from services.qdrant_service import QdrantDataFetcher

    client = _get_client()
    from services.data_pipeline_service import collection_exists

    if not collection_exists(client, name):
        raise HTTPException(status_code=404, detail=f"コレクションが存在しません: {name}")

    fetcher = QdrantDataFetcher(client)
    df = fetcher.fetch_collection_points(name, limit=limit)
    rows = dataframe_to_records(df)

    return CollectionPoints(
        name=name,
        columns=collection_columns(rows),
        rows=rows,
        limit=limit,
    )


@router.get("/files", response_model=InputFileListResponse)
def list_files(
    dir: str = Query(default="OUTPUT", description="許可ディレクトリ名"),
) -> InputFileListResponse:
    """入力ファイルの候補を列挙する（チャンキング・登録の入力選択用）。

    許可ディレクトリのホワイトリスト外を指定すると 400。
    絶対パスは返さず `ディレクトリ名/ファイル名` 形式に限定する。
    """
    from services.data_pipeline_service import (
        ALLOWED_INPUT_DIRS,
        PathNotAllowedError,
        list_input_files,
    )

    try:
        files = list_input_files(dir)
    except PathNotAllowedError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return InputFileListResponse(
        dir=dir,
        allowed_dirs=list(ALLOWED_INPUT_DIRS),
        files=[InputFileInfo(**f) for f in files],
    )

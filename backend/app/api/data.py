# backend/app/api/data.py
"""データ準備パイプラインのジョブ API（チャンキング / 登録 / 削除）。

`api/support.py` `api/review.py` と**構造は同一**。違うのはジョブのパラメータ型と
結果の形だけで、ジョブ基盤・SSE・HITL ブリッジは `core/jobs.py` をそのまま使う。

| エンドポイント | ジョブ | CONFIRM |
|---|---|---|
| `POST /api/chunking/run` | `ChunkingParams` | なし（非破壊） |
| `POST /api/qdrant/register` | `RegisterParams` | `recreate=True` のときだけ |
| `POST /api/qdrant/delete` | `DeleteParams` | **常に** |

SSE と HITL 応答は 3 種で共通のエンドポイント（`/api/data/stream/{job_id}`、
`/api/data/confirm/{job_id}`）にまとめてある。ジョブ種別ごとに分けても
中身が同じになるため。

⚠️ `backend.app.core.data_jobs` の import には副作用がある — import 時に
`register_runner()` が 3 件走る。パラメータ型を使う以上この import は必ず
発生するので、登録漏れは構造的に起きない（`review_agent.py` と同じ方式）。
"""
from __future__ import annotations

import json
from typing import Iterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from backend.app.core.data_jobs import ChunkingParams, DeleteParams, RegisterParams
from backend.app.core.jobs import job_manager
from backend.app.schemas import (
    ChunkingRequest,
    ConfirmRequest,
    ConfirmResponse,
    DataJobStatusResponse,
    DeleteCollectionsRequest,
    QueryAccepted,
    RegisterRequest,
)

router = APIRouter(prefix="/api", tags=["data"])

# 3 種で共通の SSE URL（ジョブ種別は job.kind が持つ）
_STREAM_URL = "/api/data/stream/{job_id}"


@router.post("/chunking/run", response_model=QueryAccepted, status_code=202)
def run_chunking(request: ChunkingRequest) -> QueryAccepted:
    """チャンク化ジョブを起動する（非破壊なので承認なし）。

    入力ファイルの検証は runner 側で行う（許可ディレクトリ外・不在なら
    error イベントを流してジョブが失敗する）。ここで 400 を返さないのは、
    起動と検証の責務を runner に寄せて 3 種の API を同じ形にするため。
    """
    job = job_manager.start(ChunkingParams(
        input_file=request.input_file,
        output_dir=request.output_dir,
        model=request.model,
        workers=request.workers,
        block_size=request.block_size,
        text_column=request.text_column,
        max_rows=request.max_rows,
        combine_rows=request.combine_rows,
        resume=request.resume,
        verbose=request.verbose,
    ))
    return QueryAccepted(job_id=job.job_id, stream_url=_STREAM_URL.format(job_id=job.job_id))


@router.post("/qdrant/register", response_model=QueryAccepted, status_code=202)
def register_collection(request: RegisterRequest) -> QueryAccepted:
    """Q/A CSV を Qdrant へ登録するジョブを起動する。

    ⚠️ `recreate=True` のときだけ intervention イベントが流れる。
    フロントは既存の `ConfirmModal` で承認を返す。
    """
    job = job_manager.start(RegisterParams(
        input_file=request.input_file,
        collection=request.collection,
        recreate=request.recreate,
        batch_size=request.batch_size,
        embed_workers=request.embed_workers,
        text_col=request.text_col,
        domain=request.domain,
        max_docs=request.max_docs,
        provider=request.provider,
        normalize_filename=request.normalize_filename,
        create_ui_csv=request.create_ui_csv,
        ui_output_dir=request.ui_output_dir,
        verbose=request.verbose,
    ))
    return QueryAccepted(job_id=job.job_id, stream_url=_STREAM_URL.format(job_id=job.job_id))


@router.post("/qdrant/delete", response_model=QueryAccepted, status_code=202)
def delete_collections(request: DeleteCollectionsRequest) -> QueryAccepted:
    """コレクション削除ジョブを起動する。**必ず承認を求める。**

    HTTP の `DELETE` メソッドにしていないのは、承認を経ずに消える経路を
    作らないため。削除は不可逆なので、必ず intervention → 承認 → 実行を通す。
    """
    job = job_manager.start(DeleteParams(
        collections=list(request.collections),
        verbose=request.verbose,
    ))
    return QueryAccepted(job_id=job.job_id, stream_url=_STREAM_URL.format(job_id=job.job_id))


@router.get("/data/stream/{job_id}")
def stream_events(job_id: str) -> StreamingResponse:
    """進捗を SSE で逐次配信する（形式は Support / Review と完全に同一）。

    既存モジュールの `logging` 出力は `core/job_logs.py` が横取りして
    log イベントとして流れてくる。イベントは常に先頭からリプレイされるため、
    再接続しても取りこぼさない。
    """
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    def sse() -> Iterator[str]:
        for event in job.stream_events():
            if event is None:  # keepalive（プロキシ・ブラウザのタイムアウト回避）
                yield ": keepalive\n\n"
                continue
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'done', 'status': job.status}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/data/confirm/{job_id}", response_model=ConfirmResponse)
def confirm_intervention(job_id: str, request: ConfirmRequest) -> ConfirmResponse:
    """HITL CONFIRM への応答（承認 / 拒否）を注入する。

    拒否・タイムアウトの場合、削除も再作成も**実行されない**（安全側）。
    """
    status = job_manager.confirm(job_id, request.intervention_id, request.approve)
    if status == "not_found":
        raise HTTPException(status_code=404, detail="job not found")
    return ConfirmResponse(status=status)


@router.get("/data/result/{job_id}", response_model=DataJobStatusResponse)
def get_result(job_id: str) -> DataJobStatusResponse:
    """ジョブの状態と結果を返す（ポーリング用フォールバック）。

    結果の形はジョブ種別で違うため、`result` は素の dict のまま返し
    `kind` で判別させる。
    """
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return DataJobStatusResponse(
        job_id=job.job_id, kind=job.kind, status=job.status, result=job.result
    )

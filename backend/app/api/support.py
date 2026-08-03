# backend/app/api/support.py
"""サポート問い合わせ API（ジョブ起動 / SSE 進捗 / HITL 応答 / 結果取得）。"""
from __future__ import annotations

import json
from typing import Iterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from backend.app.core.jobs import JobParams, job_manager
from backend.app.schemas import (
    ConfirmRequest,
    ConfirmResponse,
    JobStatusResponse,
    QueryAccepted,
    QueryRequest,
)

router = APIRouter(prefix="/api/support", tags=["support"])


@router.post("/query", response_model=QueryAccepted, status_code=202)
def start_query(request: QueryRequest) -> QueryAccepted:
    """問い合わせジョブを起動する。進捗は stream_url の SSE で配信される。"""
    job = job_manager.start(JobParams(
        query=request.query,
        vertical=request.vertical,
        dry_run=request.dry_run,
        use_web=request.use_web,
        do_action=request.do_action,
        verbose=request.verbose,
        identity=request.identity,
    ))
    return QueryAccepted(job_id=job.job_id, stream_url=f"/api/support/stream/{job.job_id}")


@router.get("/stream/{job_id}")
def stream_events(job_id: str) -> StreamingResponse:
    """ステップ進捗（①〜⑥）を SSE で逐次配信する。

    イベントは常に先頭からリプレイされるため、再接続しても取りこぼさない。
    各メッセージは `data: {SupportEventModel の JSON}` 形式（イベント名なし）。
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
        # 終端: フロントが EventSource を閉じるための番兵
        yield f"data: {json.dumps({'type': 'done', 'status': job.status}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/confirm/{job_id}", response_model=ConfirmResponse)
def confirm_intervention(job_id: str, request: ConfirmRequest) -> ConfirmResponse:
    """HITL CONFIRM への応答（承認 / 拒否）を注入する。

    approve=True で PROCEED（アクション実行）、False で CANCEL。
    タイムアウト済み・対象なしの場合は not_waiting / not_found を返す。
    """
    status = job_manager.confirm(job_id, request.intervention_id, request.approve)
    if status == "not_found":
        raise HTTPException(status_code=404, detail="job not found")
    return ConfirmResponse(status=status)


@router.get("/result/{job_id}", response_model=JobStatusResponse)
def get_result(job_id: str) -> JobStatusResponse:
    """ジョブの状態と最終結果（SupportResult）を返す（ポーリング用フォールバック）。"""
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return JobStatusResponse(job_id=job.job_id, status=job.status, result=job.result)

# backend/app/api/review.py
"""文書レビュー API（ジョブ起動 / SSE 進捗 / HITL 応答 / 結果取得）。

設計: backend/docs/review_agent_spec.md §7。

`api/support.py` と**構造は同一**で、違うのはジョブのパラメータ型
（`ReviewParams`）と結果の型（`ReviewResultModel`）だけ。ジョブ基盤・SSE・
HITL ブリッジは STEP3 で汎用化済みの `jobs.py` をそのまま使う
（`job_manager.start(ReviewParams(...))` で review runner が型解決される）。

⚠️ `backend.app.core.review_agent` の import には副作用がある — import 時に
`register_runner(ReviewParams, ...)` が走る。`ReviewParams` を使う以上この
import は必ず発生するので、登録漏れは構造的に起きない（設計書 §6.3）。
"""
from __future__ import annotations

import json
from typing import Iterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from backend.app.core.jobs import job_manager
from backend.app.core.review_agent import ReviewParams
from backend.app.schemas import (
    ConfirmRequest,
    ConfirmResponse,
    QueryAccepted,
    ReviewJobStatusResponse,
    ReviewRequest,
)

router = APIRouter(prefix="/api/review", tags=["review"])


@router.post("/submit", response_model=QueryAccepted, status_code=202)
def submit_document(request: ReviewRequest) -> QueryAccepted:
    """レビュージョブを起動する。進捗は stream_url の SSE で配信される。

    文書長の上限（`MAX_DOCUMENT_CHARS`）超過は Pydantic が 422 で弾く。
    """
    job = job_manager.start(ReviewParams(
        document=request.document,
        document_title=request.document_title,
        ruleset=request.ruleset,
        use_web=request.use_web,
        do_action=request.do_action,
        dry_run=request.dry_run,
        verbose=request.verbose,
    ))
    return QueryAccepted(job_id=job.job_id, stream_url=f"/api/review/stream/{job.job_id}")


@router.get("/stream/{job_id}")
def stream_events(job_id: str) -> StreamingResponse:
    """ステップ進捗（S1・①〜⑦）を SSE で逐次配信する。

    形式は Support と完全に同一（`data: {SupportEventModel の JSON}`・
    イベント名なし・末尾に done 番兵）。フロントは同じパーサを使える。
    イベントは常に先頭からリプレイされるため、再接続しても取りこぼさない。
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

    approve=True で PROCEED（起票・差し戻しの実行）、False で CANCEL。
    タイムアウト済み・対象なしの場合は not_waiting / not_found を返す。
    """
    status = job_manager.confirm(job_id, request.intervention_id, request.approve)
    if status == "not_found":
        raise HTTPException(status_code=404, detail="job not found")
    return ConfirmResponse(status=status)


@router.get("/result/{job_id}", response_model=ReviewJobStatusResponse)
def get_result(job_id: str) -> ReviewJobStatusResponse:
    """ジョブの状態と最終結果（ReviewResult）を返す（ポーリング用フォールバック）。"""
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return ReviewJobStatusResponse(job_id=job.job_id, status=job.status, result=job.result)

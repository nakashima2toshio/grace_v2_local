# backend/app/core/intervention_bridge.py
"""HITL ↔ フロントエンド承認の非同期ブリッジ。

`grace.intervention.InterventionHandler` の `on_confirm` / `on_escalate` は
**同期コールバック**（InterventionRequest → InterventionResponse）である。
Web 化ではパイプラインをワーカースレッドで実行し、CONFIRM に達したら

1. `intervention` イベントを SSE ストリームへ流す（フロントはモーダル表示）
2. `POST /api/support/confirm/{job_id}` の応答が来るまで `threading.Event` で待つ
3. タイムアウトしたら**安全側＝実行せずエスカレーション**
   （CANCEL + timeout_reached=True。`_perform_action` が有人対応へ倒す）

CLI 版の自動承認（`_AUTO_PROCEED`）は Web 側へ持ち込まない（受け入れ条件 §5-2）。
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

from backend.app.core.support_agent import SupportEvent
from grace import InterventionAction, InterventionRequest, InterventionResponse

# 承認待ちの既定タイムアウト（秒）。grace.config の intervention.default_timeout
# （既定 300 秒）より優先度の低いフォールバックとしてのみ使う。
DEFAULT_CONFIRM_TIMEOUT = 300


@dataclass
class PendingIntervention:
    """フロントエンドの応答待ちの CONFIRM/ESCALATE。"""

    intervention_id: str
    request: InterventionRequest
    ready: threading.Event = field(default_factory=threading.Event)
    response: Optional[InterventionResponse] = None


class InterventionBridge:
    """1 ジョブ分の HITL 承認待ちを仲介する。

    パイプライン（ワーカースレッド）側からは `resolver` を
    `create_intervention_handler(on_confirm=..., on_escalate=...)` に渡し、
    API（イベントループ）側からは `resolve()` で応答を注入する。
    """

    def __init__(
        self,
        emit: Callable[[SupportEvent], None],
        timeout_seconds: Optional[float] = None,
    ):
        self._emit = emit
        self._timeout = timeout_seconds
        self._lock = threading.Lock()
        self._pending: Optional[PendingIntervention] = None

    @property
    def pending(self) -> Optional[PendingIntervention]:
        return self._pending

    def resolver(self, request: InterventionRequest) -> InterventionResponse:
        """ワーカースレッドから呼ばれる同期リゾルバ（承認が来るまでブロック）。"""
        pending = PendingIntervention(
            intervention_id=uuid.uuid4().hex[:12], request=request
        )
        with self._lock:
            self._pending = pending

        timeout = self._timeout or request.timeout_seconds or DEFAULT_CONFIRM_TIMEOUT
        self._emit(SupportEvent(
            type="intervention",
            step="action",
            status="waiting",
            message=request.message,
            data={
                "intervention_id": pending.intervention_id,
                "level": str(request.level.value),
                "reason": request.reason,
                "options": request.options,
                "confidence_score": request.confidence_score,
                "timeout_seconds": timeout,
            },
        ))

        answered = pending.ready.wait(timeout)
        with self._lock:
            self._pending = None

        if not answered or pending.response is None:
            # タイムアウト → 安全側: 実行せずエスカレーション（escalate に倒す）
            self._emit(SupportEvent(
                type="intervention",
                step="action",
                status="timeout",
                message="承認待ちがタイムアウトしました → 安全側（実行せず有人対応へ）",
                data={"intervention_id": pending.intervention_id},
            ))
            return InterventionResponse(
                action=InterventionAction.CANCEL, timeout_reached=True
            )

        self._emit(SupportEvent(
            type="intervention",
            step="action",
            status="resolved",
            data={
                "intervention_id": pending.intervention_id,
                "action": pending.response.action.value,
            },
        ))
        return pending.response

    def resolve(self, intervention_id: str, approve: bool) -> bool:
        """API 側から承認/拒否を注入する。対象が待機中でなければ False。"""
        with self._lock:
            pending = self._pending
            if pending is None or pending.intervention_id != intervention_id:
                return False
            pending.response = InterventionResponse(
                action=InterventionAction.PROCEED if approve
                else InterventionAction.CANCEL
            )
            pending.ready.set()
            return True

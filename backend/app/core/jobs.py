# backend/app/core/jobs.py
"""エージェント実行のジョブ管理（インメモリ）。

1 リクエスト = 1 ジョブ。ジョブはワーカースレッドで**実行関数（runner）**を呼び、
進捗イベントを蓄積する。SSE 購読者はイベント列を先頭から追いかける
（再接続・途中購読でも全イベントをリプレイできる）。ローカル開発用の
シングルプロセス前提で、永続化はしない。

## runner 注入方式（設計: backend/docs/review_agent_spec.md §6）

当初は `run_support_agent_core` を直接呼んでいたが、GRACE-Review（文書レビュー）を
同じジョブ基盤へ乗せるため、実行関数を差し替え可能にした。

    runner(params, emit, confirm) -> Optional[Dict[str, Any]]

`start()` は runner 省略時、`params` の型から `_RUNNERS` を引いて既定 runner を
解決する。**`api/support.py` の `job_manager.start(JobParams(...))` は無変更で動く。**

runner の登録は `register_runner()` で行う。Support の runner は本モジュールが
自分で登録し、Review の runner は `review_agent.py` が import 時に登録する
（`ReviewParams` を構築するには `review_agent` の import が必要なため、
登録漏れは構造的に起きない）。この形にすることで `jobs.py` は Review 側の
モジュールを一切知らずに済み、循環 import も発生しない。
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from backend.app.core.intervention_bridge import InterventionBridge
from backend.app.core.support_agent import (
    ConfirmFn,
    EmitFn,
    SupportEvent,
    result_to_dict,
    run_support_agent_core,
)

logger = logging.getLogger(__name__)

# 完了済みジョブをメモリに保持する上限（超えたら古い完了ジョブから破棄）
MAX_FINISHED_JOBS = 50

# ジョブの実行関数。戻り dict がそのまま `Job.result` になる（None = 失敗）。
JobRunner = Callable[[Any, EmitFn, ConfirmFn], Optional[Dict[str, Any]]]

# params の型 → (runner, kind) の登録テーブル。register_runner() で登録する。
_RUNNERS: Dict[type, Tuple[JobRunner, str]] = {}


def register_runner(params_type: type, runner: JobRunner, kind: str) -> None:
    """params の型に対する既定 runner を登録する。

    Args:
        params_type: `start()` へ渡されるパラメータの型（`JobParams` 等）
        runner: `(params, emit, confirm) -> Optional[dict]`
        kind: ジョブ種別のラベル（ログ・スレッド名・`Job.kind` に使う）
    """
    _RUNNERS[params_type] = (runner, kind)


def _resolve_runner(params: Any) -> Tuple[JobRunner, str]:
    """params の型から既定 runner を解決する。未登録なら TypeError。"""
    for params_type, entry in _RUNNERS.items():
        if isinstance(params, params_type):
            return entry
    raise TypeError(
        f"未登録の params 型です: {type(params).__name__}。"
        "register_runner() で登録するか、start(params, runner=...) を使ってください。"
    )


@dataclass
class JobParams:
    """POST /api/support/query のパラメータ（CLI 引数と 1:1 対応）。"""

    query: str
    vertical: Optional[str] = None
    dry_run: bool = True
    use_web: bool = True
    do_action: bool = True
    verbose: bool = False
    # 本人確認の識別子（CLI の --identity KEY=VALUE 相当）。
    # None は「提示なし」。照合されるのは require_identity のプロファイルのときだけで、
    # さらに dry_run=True ではデモ照合が値を見ないため、実照合は
    # 「ec ＋ dry_run=False ＋ SUPPORT_IDENTITY_FILE 設定」の経路に限られる。
    identity: Optional[Dict[str, str]] = None


@dataclass
class Job:
    """実行中/完了のジョブ。イベント列と最終結果を保持する。

    `params` の型はジョブ種別によって異なる（`JobParams` / `ReviewParams` 等）ため
    `Any`。どの runner で実行するかは `runner` / `kind` が保持する。
    """

    job_id: str
    params: Any
    kind: str = "support"              # "support" / "review" / …
    runner: Optional[JobRunner] = None
    status: str = "running"            # running / completed / failed
    events: List[Dict[str, Any]] = field(default_factory=list)
    result: Optional[Dict[str, Any]] = None
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    cond: threading.Condition = field(default_factory=threading.Condition)
    bridge: Optional[InterventionBridge] = None

    def emit(self, event: SupportEvent) -> None:
        """コアからの進捗イベントを蓄積し、SSE 購読者を起こす。"""
        record = {"seq": len(self.events), "ts": time.time(), **asdict(event)}
        with self.cond:
            self.events.append(record)
            self.cond.notify_all()

    def finish(self, status: str, result: Optional[Dict[str, Any]] = None) -> None:
        with self.cond:
            self.status = status
            self.result = result
            self.finished_at = time.time()
            self.cond.notify_all()

    @property
    def done(self) -> bool:
        return self.status != "running"

    def stream_events(self, poll_timeout: float = 15.0) -> Iterator[Optional[Dict[str, Any]]]:
        """イベントを先頭から順に返すブロッキングイテレータ。

        新イベントが `poll_timeout` 秒来ない場合は None を返す
        （SSE 側は keepalive コメントを送って接続維持する）。
        ジョブ完了かつ全イベント配信済みで終了する。
        """
        index = 0
        while True:
            with self.cond:
                if index >= len(self.events) and not self.done:
                    self.cond.wait(timeout=poll_timeout)
                if index < len(self.events):
                    event = self.events[index]
                    index += 1
                else:
                    if self.done:
                        return
                    event = None  # タイムアウト → keepalive
            yield event


class JobManager:
    """ジョブの生成・参照・HITL 応答の注入を担う（インメモリ・スレッドセーフ）。"""

    def __init__(self):
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()

    def start(
        self,
        params: Any,
        runner: Optional[JobRunner] = None,
        kind: Optional[str] = None,
    ) -> Job:
        """ジョブを起動する。

        Args:
            params: 実行パラメータ。runner 省略時はこの型で runner を解決する。
            runner: 実行関数。省略時は `_RUNNERS` から解決（未登録なら TypeError）。
            kind: ジョブ種別のラベル。省略時は登録時の kind、runner 明示時は "custom"。

        既存の `start(JobParams(...))`（引数 1 個）はそのまま動く。
        """
        if runner is None:
            runner, resolved_kind = _resolve_runner(params)
            kind = kind or resolved_kind
        else:
            kind = kind or "custom"

        job = Job(
            job_id=uuid.uuid4().hex[:12], params=params, kind=kind, runner=runner
        )
        job.bridge = InterventionBridge(emit=job.emit)
        with self._lock:
            self._gc_finished_locked()
            self._jobs[job.job_id] = job
        thread = threading.Thread(
            target=self._run, args=(job,), name=f"{kind}-job-{job.job_id}", daemon=True
        )
        thread.start()
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def confirm(self, job_id: str, intervention_id: str, approve: bool) -> str:
        """HITL 応答を注入する。戻り値: "resolved" / "not_found" / "not_waiting"。"""
        job = self.get(job_id)
        if job is None:
            return "not_found"
        if job.bridge is None or not job.bridge.resolve(intervention_id, approve):
            return "not_waiting"
        return "resolved"

    def _run(self, job: Job) -> None:
        try:
            result = job.runner(job.params, job.emit, job.bridge.resolver)
        except Exception as e:  # Qdrant 未起動・LLM タイムアウト等をイベントで配信
            logger.exception(f"{job.kind} job {job.job_id} failed")
            job.emit(SupportEvent(
                type="error",
                message=f"❌ 実行に失敗しました: {type(e).__name__}: {e}",
                data={"hint": "Qdrant の起動と .env の API キーを確認してください。"},
            ))
            job.finish("failed")
            return
        if result is None:  # APIキー未設定等（error イベントは runner 側で emit 済み）
            job.finish("failed")
        else:
            job.finish("completed", result)

    def _gc_finished_locked(self) -> None:
        """完了ジョブが増えすぎたら古い順に破棄する（呼び出し側で lock 保持）。"""
        finished = sorted(
            (j for j in self._jobs.values() if j.done),
            key=lambda j: j.finished_at or 0,
        )
        for job in finished[: max(0, len(finished) - MAX_FINISHED_JOBS)]:
            self._jobs.pop(job.job_id, None)


# =============================================================================
# Support の runner（既定登録）
# =============================================================================

def _support_runner(
    params: JobParams, emit: EmitFn, confirm: ConfirmFn
) -> Optional[Dict[str, Any]]:
    """`JobParams` → `run_support_agent_core` の呼び出し。

    従来 `JobManager._run` に直書きされていた処理を切り出したもの。
    `identity` は当初 `None` 固定だったが、画面から本人確認の識別子を
    渡せるようにしたため `params.identity` を素通しする（未指定なら None）。
    """
    result = run_support_agent_core(
        params.query,
        verbose=params.verbose,
        use_web=params.use_web,
        do_action=params.do_action,
        dry_run=params.dry_run,
        vertical=params.vertical,
        identity=params.identity,
        emit=emit,
        confirm=confirm,
    )
    return result_to_dict(result) if result is not None else None


register_runner(JobParams, _support_runner, "support")


# 後方互換エイリアス。既存 import（`from ... import SupportJob`）を壊さない。
SupportJob = Job

# アプリ全体で共有するシングルトン（ローカル・シングルプロセス前提）
job_manager = JobManager()

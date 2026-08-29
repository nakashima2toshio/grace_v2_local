# backend/tests/test_done_event_timing.py
"""SSE 終端イベント（`jobs.done_event`）が実行時刻を運ぶこと。

## なぜこのテストがあるか

フロントは実行の開始・完了時刻を**サーバ時計**から取る
（`frontend/src/state/elapsed.ts::applyServerEvent`）。終端イベントが時刻を
持たないと、サーバ側の完了時刻が永久に埋まらず「完了 … ／ 所要 …」の行が
まるごと消える。実測 2026-08-29 にこれが起きた。

開始時刻に **`created_at`（POST 受付時刻）** を使うのも同じ理由である。
最初のイベントが出るのは、ツール・planner・executor の生成が終わったあとで、
ローカル LLM では受付から十数秒かかる。そこを落とすと、利用者が実際に待った
時間より短い所要時間が表示される。
"""
from __future__ import annotations

from backend.app.core.jobs import Job, done_event


def _job(**kwargs) -> Job:
    return Job(job_id="j1", params=None, **kwargs)


class TestDoneEvent:
    def test_完了時刻を運ぶ(self):
        job = _job()
        job.finish("completed", {"answer": "ok"})
        event = done_event(job)
        assert event["type"] == "done"
        assert event["status"] == "completed"
        assert event["ts"] == job.finished_at
        assert event["ts"] is not None, "これが None だと完了行が消える"

    def test_開始は受付時刻であって最初のイベント時刻ではない(self):
        job = _job()
        job.finish("completed", None)
        assert done_event(job)["started_at"] == job.created_at
        assert job.created_at <= job.finished_at

    def test_実行中は完了時刻がNone(self):
        """終端イベントは決着後にしか流れないが、値の由来を固定しておく。"""
        job = _job()
        assert done_event(job)["ts"] is None
        assert done_event(job)["started_at"] == job.created_at

    def test_失敗でも時刻を運ぶ(self):
        job = _job()
        job.finish("failed", None)
        event = done_event(job)
        assert event["status"] == "failed"
        assert event["ts"] is not None
        assert event["started_at"] is not None

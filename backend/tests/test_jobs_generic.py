# backend/tests/test_jobs_generic.py
"""ジョブ基盤（backend/app/core/jobs.py）の汎用化に対するテスト。

設計: backend/docs/review_agent_spec.md §6。

本モジュールは 2 種類のテストを持つ。

1. **回帰ガード**（`TestSupportRegression`）— 汎用化の前後で Support の挙動が
   変わらないことを固定する。汎用化前のコードでも通る（それが目的）。
2. **新挙動**（`TestRunnerInjection` / `TestRunnerRegistry`）— runner 注入方式が
   動くことを検証する。**汎用化前のコードでは失敗する**。

`jobs.py` は稼働中の Support が通る唯一の共有経路であり、ここを壊すと
Web API も CLI も止まる。そのため Review 側の実装（STEP4）とは PR を分け、
このテストで回帰を固めてから先へ進む。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from backend.app.core.jobs import (
    JobManager,
    JobParams,
    register_runner,
)
from backend.app.core.support_agent import SupportEvent


def _wait_until(predicate, timeout: float = 3.0) -> bool:
    """条件が成立するまで待つ（ワーカースレッドの完了待ち）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _drain(job) -> None:
    """ジョブが終了状態になるまで待つ。"""
    assert _wait_until(lambda: job.done), f"ジョブが終了しない: status={job.status}"


# =============================================================================
# 回帰ガード: 汎用化しても Support の挙動が変わらない
# =============================================================================

class TestSupportRegression:
    """`api/support.py` からの呼び出し形（`start(JobParams(...))`）を固定する。"""

    def test_start_with_job_params_runs_support_core(self, monkeypatch):
        """引数 1 個の start(JobParams) が従来どおり Support コアを呼ぶ。"""
        calls: List[Dict[str, Any]] = []

        def fake_core(query, **kwargs):
            calls.append({"query": query, **kwargs})
            kwargs["emit"](SupportEvent(type="log", message="running"))
            return SimpleNamespace(answer="ok", decision="answer")

        monkeypatch.setattr(
            "backend.app.core.jobs.run_support_agent_core", fake_core
        )
        monkeypatch.setattr(
            "backend.app.core.jobs.result_to_dict", lambda r: {"answer": r.answer}
        )

        manager = JobManager()
        job = manager.start(JobParams(
            query="パスワードを忘れました",
            vertical="gov",
            dry_run=True,
            use_web=False,
            do_action=False,
            verbose=True,
        ))
        _drain(job)

        assert len(calls) == 1, "Support コアが 1 回だけ呼ばれること"
        call = calls[0]
        # api/support.py が渡す全パラメータがコアへ素通しされること
        assert call["query"] == "パスワードを忘れました"
        assert call["vertical"] == "gov"
        assert call["dry_run"] is True
        assert call["use_web"] is False
        assert call["do_action"] is False
        assert call["verbose"] is True
        assert call["identity"] is None
        assert callable(call["emit"])
        assert callable(call["confirm"])

        assert job.status == "completed"
        assert job.result == {"answer": "ok"}
        # emit されたイベントが SSE 用に蓄積されること
        assert any(e["type"] == "log" for e in job.events)

    def test_identity_is_passed_through_to_core(self, monkeypatch):
        """`JobParams.identity` がコアへ素通しされること。

        当初 `_support_runner` は `identity=None` を直書きしていた（画面から
        本人確認の識別子を渡せなかった）。ここを固定して再発を防ぐ。
        """
        calls: List[Dict[str, Any]] = []

        def fake_core(query, **kwargs):
            calls.append({"query": query, **kwargs})
            return SimpleNamespace(answer="ok", decision="answer")

        monkeypatch.setattr(
            "backend.app.core.jobs.run_support_agent_core", fake_core
        )
        monkeypatch.setattr(
            "backend.app.core.jobs.result_to_dict", lambda r: {"answer": r.answer}
        )

        manager = JobManager()
        identity = {"order_id": "1001", "email": "a@example.com"}
        job = manager.start(JobParams(
            query="返品したい", vertical="ec", identity=identity,
        ))
        _drain(job)

        assert calls[0]["identity"] == identity
        assert job.status == "completed"

    def test_identity_defaults_to_none(self, monkeypatch):
        """未指定なら None のまま（従来の呼び出し形を壊さない）。"""
        calls: List[Dict[str, Any]] = []

        monkeypatch.setattr(
            "backend.app.core.jobs.run_support_agent_core",
            lambda query, **kwargs: (
                calls.append(kwargs), SimpleNamespace(answer="ok")
            )[1],
        )
        monkeypatch.setattr(
            "backend.app.core.jobs.result_to_dict", lambda r: {"answer": r.answer}
        )

        manager = JobManager()
        _drain(manager.start(JobParams(query="パスワードを忘れました")))

        assert calls[0]["identity"] is None

    def test_core_returning_none_marks_failed(self, monkeypatch):
        """コアが None（APIキー未設定）を返したら failed。"""
        monkeypatch.setattr(
            "backend.app.core.jobs.run_support_agent_core",
            lambda *a, **k: None,
        )
        manager = JobManager()
        job = manager.start(JobParams(query="q"))
        _drain(job)
        assert job.status == "failed"
        assert job.result is None

    def test_core_exception_emits_error_event_and_fails(self, monkeypatch):
        """コアが例外を投げたら error イベントを流して failed（例外は伝播させない）。"""

        def boom(*_a, **_k):
            raise RuntimeError("qdrant down")

        monkeypatch.setattr("backend.app.core.jobs.run_support_agent_core", boom)
        manager = JobManager()
        job = manager.start(JobParams(query="q"))
        _drain(job)

        assert job.status == "failed"
        errors = [e for e in job.events if e["type"] == "error"]
        assert len(errors) == 1
        assert "RuntimeError" in errors[0]["message"]
        assert "qdrant down" in errors[0]["message"]
        assert errors[0]["data"]["hint"]

    def test_get_and_confirm_are_unchanged(self, monkeypatch):
        """get / confirm の戻り値の約束が変わらない。"""
        monkeypatch.setattr(
            "backend.app.core.jobs.run_support_agent_core",
            lambda *a, **k: None,
        )
        manager = JobManager()
        job = manager.start(JobParams(query="q"))
        _drain(job)

        assert manager.get(job.job_id) is job
        assert manager.get("does-not-exist") is None
        assert manager.confirm("does-not-exist", "iv", True) == "not_found"
        # 承認待ちが無い状態での confirm は not_waiting
        assert manager.confirm(job.job_id, "iv", True) == "not_waiting"

    def test_events_carry_seq_and_ts(self, monkeypatch):
        """SSE のリプレイに必要な seq / ts が付与される。"""
        def fake_core(query, **kwargs):
            kwargs["emit"](SupportEvent(type="log", message="a"))
            kwargs["emit"](SupportEvent(type="log", message="b"))
            return None

        monkeypatch.setattr("backend.app.core.jobs.run_support_agent_core", fake_core)
        manager = JobManager()
        job = manager.start(JobParams(query="q"))
        _drain(job)

        assert [e["seq"] for e in job.events] == [0, 1]
        assert all(isinstance(e["ts"], float) for e in job.events)


# =============================================================================
# 新挙動: runner 注入
# =============================================================================

@dataclass
class _FakeParams:
    """Support でも Review でもない、テスト専用のパラメータ型。"""

    value: str = "x"


class TestRunnerInjection:
    """`start(params, runner=...)` で任意の実行関数を差し込める。"""

    def test_explicit_runner_is_called_with_params_emit_confirm(self):
        """runner は (params, emit, confirm) を受け取り、戻り dict が result になる。"""
        seen: Dict[str, Any] = {}

        def runner(params, emit, confirm):
            seen["params"] = params
            seen["emit_callable"] = callable(emit)
            seen["confirm_callable"] = callable(confirm)
            emit(SupportEvent(type="log", message="from runner"))
            return {"ok": True}

        manager = JobManager()
        params = _FakeParams(value="hello")
        job = manager.start(params, runner=runner, kind="fake")
        _drain(job)

        assert seen["params"] is params
        assert seen["emit_callable"] is True
        assert seen["confirm_callable"] is True
        assert job.status == "completed"
        assert job.result == {"ok": True}
        assert job.kind == "fake"
        assert any(e["message"] == "from runner" for e in job.events)

    def test_explicit_runner_returning_none_marks_failed(self):
        manager = JobManager()
        job = manager.start(_FakeParams(), runner=lambda p, e, c: None, kind="fake")
        _drain(job)
        assert job.status == "failed"

    def test_explicit_runner_exception_is_contained(self):
        """任意 runner の例外も error イベント＋failed に変換される。"""

        def boom(_p, _e, _c):
            raise ValueError("runner exploded")

        manager = JobManager()
        job = manager.start(_FakeParams(), runner=boom, kind="fake")
        _drain(job)

        assert job.status == "failed"
        errors = [e for e in job.events if e["type"] == "error"]
        assert len(errors) == 1
        assert "ValueError" in errors[0]["message"]

    def test_support_job_kind_defaults_to_support(self, monkeypatch):
        """JobParams で起動したジョブの kind は "support"。"""
        monkeypatch.setattr(
            "backend.app.core.jobs.run_support_agent_core", lambda *a, **k: None
        )
        manager = JobManager()
        job = manager.start(JobParams(query="q"))
        _drain(job)
        assert job.kind == "support"


class TestRunnerRegistry:
    """params の型から既定 runner を解決する登録テーブル。"""

    def test_registered_runner_is_resolved_by_params_type(self):
        """register_runner した型は runner 省略で解決される。"""
        called: List[str] = []

        def runner(params, _emit, _confirm):
            called.append(params.value)
            return {"done": params.value}

        register_runner(_FakeParams, runner, "fake")
        try:
            manager = JobManager()
            job = manager.start(_FakeParams(value="resolved"))
            _drain(job)
        finally:
            # 他テストへ影響させないよう登録を戻す
            from backend.app.core.jobs import _RUNNERS
            _RUNNERS.pop(_FakeParams, None)

        assert called == ["resolved"]
        assert job.kind == "fake"
        assert job.result == {"done": "resolved"}

    def test_unknown_params_type_raises_type_error(self):
        """未登録の型を runner 省略で渡したら TypeError（黙って動かさない）。"""
        manager = JobManager()
        with pytest.raises(TypeError) as excinfo:
            manager.start(object())
        assert "runner" in str(excinfo.value).lower() or "params" in str(excinfo.value)

    def test_support_runner_is_registered_at_import(self):
        """Support の runner はモジュール読み込み時に登録済み。"""
        from backend.app.core.jobs import _RUNNERS
        assert JobParams in _RUNNERS
        runner, kind = _RUNNERS[JobParams]
        assert callable(runner)
        assert kind == "support"


class TestBackwardCompatAlias:
    """既存 import を壊さないためのエイリアス。"""

    def test_support_job_alias_points_to_job(self):
        from backend.app.core.jobs import Job, SupportJob
        assert SupportJob is Job

    def test_job_manager_singleton_still_exported(self):
        from backend.app.core.jobs import job_manager
        assert isinstance(job_manager, JobManager)


# =============================================================================
# ストリーム（SSE 供給）が job の種類に依らず動く
# =============================================================================

def test_stream_events_replays_from_head_for_any_runner():
    """任意 runner のジョブでもイベントは先頭からリプレイされる。"""

    def runner(_params, emit, _confirm):
        for i in range(3):
            emit(SupportEvent(type="log", message=f"e{i}"))
        return {"ok": True}

    manager = JobManager()
    job = manager.start(_FakeParams(), runner=runner, kind="fake")
    _drain(job)

    # 完了後に購読しても全イベントが取れる
    messages = [e["message"] for e in job.stream_events() if e is not None]
    assert messages == ["e0", "e1", "e2"]


def test_finished_jobs_are_garbage_collected():
    """完了ジョブが MAX_FINISHED_JOBS を超えたら古い順に破棄される。"""
    from backend.app.core.jobs import MAX_FINISHED_JOBS

    manager = JobManager()
    jobs = []
    for i in range(MAX_FINISHED_JOBS + 5):
        job = manager.start(
            _FakeParams(value=str(i)), runner=lambda p, e, c: {"i": p.value}, kind="fake"
        )
        _drain(job)
        jobs.append(job)

    remaining = [j for j in jobs if manager.get(j.job_id) is not None]
    # GC は start() の先頭で走るため、直後に追加される 1 件分だけ上限を超えうる
    # （汎用化前からの仕様。ここは変えていない）。
    assert len(remaining) <= MAX_FINISHED_JOBS + 1
    assert len(remaining) < len(jobs), "GC が効いていない"
    # 破棄されるのは古い方
    assert manager.get(jobs[-1].job_id) is not None
    assert manager.get(jobs[0].job_id) is None


@pytest.fixture(autouse=True)
def _isolate_registry():
    """テスト間で登録テーブルの汚染を防ぐ。"""
    from backend.app.core.jobs import _RUNNERS
    snapshot = dict(_RUNNERS)
    yield
    _RUNNERS.clear()
    _RUNNERS.update(snapshot)

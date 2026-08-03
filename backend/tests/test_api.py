# backend/tests/test_api.py
"""FastAPI（backend.app.main）の結合テスト。外部依存はスタブ。

- POST /api/support/query → ジョブ受付
- GET  /api/support/stream/{job_id} → SSE で全イベント＋done 番兵
- POST /api/support/confirm/{job_id} → HITL 応答の注入
- GET  /api/support/result/{job_id} → SupportResult の JSON
- GET  /api/verticals, /api/health
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from backend.app.core.jobs import job_manager
from backend.app.main import app

client = TestClient(app)


def _wait(predicate, timeout=10.0, message="条件が満たされなかった"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError(message)


def _find_intervention(job):
    for ev in list(job.events):
        if ev["type"] == "intervention" and ev.get("status") == "waiting":
            return ev
    return None


def _read_stream(job_id):
    """SSE を最後（done 番兵）まで読み、data JSON のリストを返す。"""
    payloads = []
    with client.stream("GET", f"/api/support/stream/{job_id}") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            payloads.append(json.loads(line[len("data: "):]))
            if payloads[-1].get("type") == "done":
                break
    return payloads


class TestSupportApi:
    def test_query_stream_confirm_result_roundtrip(self, pipeline_stub):
        """代表ケース（ec「返品したい」）: 本人確認 → CONFIRM 承認 → ドライラン実行。"""
        response = client.post("/api/support/query", json={
            "query": "返品したい", "vertical": "ec", "dry_run": True,
        })
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        assert response.json()["stream_url"] == f"/api/support/stream/{job_id}"

        # HITL: intervention(waiting) が来るまでアクションは実行されない
        job = job_manager.get(job_id)
        event = _wait(lambda: _find_intervention(job),
                      message="intervention イベントが来なかった")
        assert job.status == "running"

        confirm = client.post(f"/api/support/confirm/{job_id}", json={
            "intervention_id": event["data"]["intervention_id"], "approve": True,
        })
        assert confirm.status_code == 200
        assert confirm.json()["status"] == "resolved"

        _wait(lambda: job.done, message="ジョブが完了しなかった")
        result = client.get(f"/api/support/result/{job_id}").json()
        assert result["status"] == "completed"
        assert result["result"]["decision"] == "answer"
        assert result["result"]["action"]["action_type"] == "create_ticket"
        assert "[DRY-RUN]" in result["result"]["action_result"]
        assert result["result"]["identity_checked"] is True

        # SSE: 完了後でも全イベントをリプレイでき、done 番兵で終わる
        payloads = _read_stream(job_id)
        types = [p["type"] for p in payloads]
        assert "intervention" in types and "result" in types
        assert types[-1] == "done"
        steps = [(p["step"], p["status"]) for p in payloads if p["type"] == "step"]
        assert ("plan", "started") in steps and ("action", "finished") in steps
        # seq は 0 起点の通し番号（リプレイの取りこぼし検知用）
        seqs = [p["seq"] for p in payloads if "seq" in p]
        assert seqs == list(range(len(seqs)))

    def test_confirm_reject_cancels_action(self, pipeline_stub):
        response = client.post("/api/support/query", json={
            "query": "返品したい", "vertical": "ec",
        })
        job = job_manager.get(response.json()["job_id"])
        event = _wait(lambda: _find_intervention(job))
        client.post(f"/api/support/confirm/{job.job_id}", json={
            "intervention_id": event["data"]["intervention_id"], "approve": False,
        })
        _wait(lambda: job.done)
        assert "キャンセル" in job.result["action_result"]
        assert "[DRY-RUN]" not in job.result["action_result"]

    def test_confirm_unknown_job_returns_404(self, pipeline_stub):
        response = client.post("/api/support/confirm/nonexistent", json={
            "intervention_id": "x", "approve": True,
        })
        assert response.status_code == 404

    def test_confirm_after_completion_is_not_waiting(self, pipeline_stub):
        response = client.post("/api/support/query", json={
            "query": "返品したい", "vertical": "ec",
        })
        job = job_manager.get(response.json()["job_id"])
        event = _wait(lambda: _find_intervention(job))
        client.post(f"/api/support/confirm/{job.job_id}", json={
            "intervention_id": event["data"]["intervention_id"], "approve": True,
        })
        _wait(lambda: job.done)
        again = client.post(f"/api/support/confirm/{job.job_id}", json={
            "intervention_id": event["data"]["intervention_id"], "approve": True,
        })
        assert again.json()["status"] == "not_waiting"

    def test_stream_unknown_job_returns_404(self):
        response = client.get("/api/support/stream/nonexistent")
        assert response.status_code == 404

    def test_failed_job_reports_error_event(self, pipeline_stub, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        response = client.post("/api/support/query", json={"query": "テスト"})
        job = job_manager.get(response.json()["job_id"])
        _wait(lambda: job.done)
        assert job.status == "failed"
        payloads = _read_stream(job.job_id)
        assert any(p["type"] == "error" for p in payloads)
        assert payloads[-1] == {"type": "done", "status": "failed"}

    def test_query_validation_rejects_empty(self):
        response = client.post("/api/support/query", json={"query": ""})
        assert response.status_code == 422

    def test_identity_reaches_job_params(self, pipeline_stub):
        """`--identity` 相当の識別子が API → JobParams まで届くこと。"""
        response = client.post("/api/support/query", json={
            "query": "返品したい", "vertical": "ec",
            "identity": {"order_id": "1001", "email": "a@example.com"},
        })
        assert response.status_code == 202
        job = job_manager.get(response.json()["job_id"])
        assert job.params.identity == {"order_id": "1001", "email": "a@example.com"}

    def test_identity_is_optional(self, pipeline_stub):
        """未指定でも 202（従来のリクエスト形を壊さない）。"""
        response = client.post("/api/support/query", json={"query": "返品したい"})
        assert response.status_code == 202
        job = job_manager.get(response.json()["job_id"])
        assert job.params.identity is None


class TestMetaApi:
    def test_verticals_lists_builtin_profiles(self):
        response = client.get("/api/verticals")
        assert response.status_code == 200
        verticals = {v["id"]: v for v in response.json()}
        assert set(verticals) == {"gov", "saas", "ec"}
        assert verticals["ec"]["require_identity"] is True
        assert verticals["gov"]["notify_th"] == pytest.approx(0.8)
        assert "返品" in verticals["ec"]["action_map"]

    def test_health(self):
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

# backend/tests/test_review_api.py
"""文書レビュー API（backend.app.api.review / meta）の結合テスト。外部依存はスタブ。

設計: backend/docs/review_agent_spec.md §7。

- POST /api/review/submit → ジョブ受付（422 ガード含む）
- GET  /api/review/stream/{job_id} → SSE で全イベント＋done 番兵
- POST /api/review/confirm/{job_id} → HITL 応答の注入
- GET  /api/review/result/{job_id} → ReviewResult の JSON
- GET  /api/rulesets

⚠️ Support 側のエンドポイントが**一切変わっていない**ことも併せて固定する
（`jobs.py` を共有しているため、Review の追加で壊れうる）。
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from backend.app.core.jobs import job_manager
from backend.app.main import app
from backend.app.schemas import MAX_DOCUMENT_CHARS

client = TestClient(app)

# 景表法（No.1）と薬機法（治る）に触れる文書。既定スタブ検出器が指摘を出す。
NG_DOC = "当社の化粧品は業界No.1の実力。使えばシミが治ると評判です。"
# 重大リスク語を含まない違反文（強制 high を経由しない＝create_ticket 側）
MEDIUM_DOC = "今だけ期間限定の特別価格です。"


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
    with client.stream("GET", f"/api/review/stream/{job_id}") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            payloads.append(json.loads(line[len("data: "):]))
            if payloads[-1].get("type") == "done":
                break
    return payloads


def _submit(**overrides):
    body = {"document": NG_DOC, "document_title": "LP案"}
    body.update(overrides)
    return client.post("/api/review/submit", json=body)


class TestReviewApi:

    def test_submit_stream_result_roundtrip(self, review_stub):
        """代表ケース: 投入 → SSE 進捗 → 結果取得。"""
        response = _submit()
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        assert response.json()["stream_url"] == f"/api/review/stream/{job_id}"

        job = job_manager.get(job_id)
        _wait(lambda: job.done, message="ジョブが完了しなかった")

        result = client.get(f"/api/review/result/{job_id}").json()
        assert result["status"] == "completed"
        payload = result["result"]
        assert payload["document_title"] == "LP案"
        assert payload["ruleset"] == "ec_ad"
        assert payload["findings"], "指摘が 1 件も返っていない"
        assert payload["segments_total"] >= 1

        # SSE: 完了後でも全イベントをリプレイでき、done 番兵で終わる
        payloads = _read_stream(job_id)
        types = [p["type"] for p in payloads]
        assert "result" in types
        assert types[-1] == "done"
        steps = [(p["step"], p["status"]) for p in payloads if p["type"] == "step"]
        assert ("segment", "started") in steps
        assert ("severity", "finished") in steps
        # seq は 0 起点の通し番号（リプレイの取りこぼし検知用）
        seqs = [p["seq"] for p in payloads if "seq" in p]
        assert seqs == list(range(len(seqs)))

    def test_result_findings_match_the_response_schema(self, review_stub):
        """指摘のフィールドが `ReviewFindingModel` を満たす（フロントの契約）。"""
        job_id = _submit().json()["job_id"]
        _wait(lambda: job_manager.get(job_id).done)
        finding = client.get(f"/api/review/result/{job_id}").json()["result"]["findings"][0]

        assert set(finding) >= {
            "finding_id", "segment_id", "excerpt", "start", "end",
            "rule_id", "rule_title", "category", "law", "article",
            "message", "suggestion", "severity", "confidence", "citations",
            "status", "forced", "suppress_reason", "web_checked",
        }
        assert finding["severity"] in {"high", "medium", "low"}
        assert finding["status"] in {"confirmed", "review_required", "suppressed"}
        # オフセットは原文を指す（UI のハイライトが直接これに依存する）
        assert NG_DOC[finding["start"]:finding["end"]] == finding["excerpt"]

    def test_summary_is_returned(self, review_stub):
        job_id = _submit().json()["job_id"]
        _wait(lambda: job_manager.get(job_id).done)
        summary = client.get(f"/api/review/result/{job_id}").json()["result"]["summary"]

        assert set(summary) == {
            "high", "medium", "low", "confirmed", "review_required", "suppressed"
        }
        assert summary["high"] + summary["medium"] + summary["low"] >= 1

    def test_confirm_approve_executes_action(self, review_hitl_stub):
        """HITL: intervention(waiting) が来るまでアクションは実行されない。"""
        # high が出ると escalate_to_human（承認不要）になるので medium 文書を使う
        response = _submit(document=MEDIUM_DOC, document_title="バナー案")
        job = job_manager.get(response.json()["job_id"])

        event = _wait(lambda: _find_intervention(job),
                      message="intervention イベントが来なかった")
        assert job.status == "running"

        confirm = client.post(f"/api/review/confirm/{job.job_id}", json={
            "intervention_id": event["data"]["intervention_id"], "approve": True,
        })
        assert confirm.status_code == 200
        assert confirm.json()["status"] == "resolved"

        _wait(lambda: job.done)
        result = client.get(f"/api/review/result/{job.job_id}").json()["result"]
        assert result["action"]["action_type"] == "create_ticket"
        assert "[DRY-RUN]" in result["action_result"]

    def test_confirm_reject_cancels_action(self, review_hitl_stub):
        response = _submit(document=MEDIUM_DOC)
        job = job_manager.get(response.json()["job_id"])
        event = _wait(lambda: _find_intervention(job))
        client.post(f"/api/review/confirm/{job.job_id}", json={
            "intervention_id": event["data"]["intervention_id"], "approve": False,
        })
        _wait(lambda: job.done)
        assert "キャンセル" in job.result["action_result"]
        assert "[DRY-RUN]" not in job.result["action_result"]

    def test_confirm_unknown_job_returns_404(self):
        response = client.post("/api/review/confirm/nonexistent", json={
            "intervention_id": "x", "approve": True,
        })
        assert response.status_code == 404

    def test_stream_unknown_job_returns_404(self):
        assert client.get("/api/review/stream/nonexistent").status_code == 404

    def test_result_unknown_job_returns_404(self):
        assert client.get("/api/review/result/nonexistent").status_code == 404

    def test_failed_job_reports_error_event(self, review_stub, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        job = job_manager.get(_submit().json()["job_id"])
        _wait(lambda: job.done)

        assert job.status == "failed"
        payloads = _read_stream(job.job_id)
        assert any(p["type"] == "error" for p in payloads)
        assert payloads[-1] == {"type": "done", "status": "failed"}

    def test_do_action_false_skips_action(self, review_stub):
        job_id = _submit(do_action=False).json()["job_id"]
        _wait(lambda: job_manager.get(job_id).done)
        result = client.get(f"/api/review/result/{job_id}").json()["result"]
        assert result["action"] is None


class TestSubmitValidation:
    """入力段のガード（設計書 §7.3）。"""

    def test_empty_document_is_rejected(self):
        assert client.post("/api/review/submit", json={"document": ""}).status_code == 422

    def test_missing_document_is_rejected(self):
        assert client.post("/api/review/submit", json={}).status_code == 422

    def test_document_over_the_limit_is_rejected(self):
        """50,000 文字超は 422。組合せ爆発を入力段で止める。"""
        response = client.post("/api/review/submit", json={
            "document": "あ" * (MAX_DOCUMENT_CHARS + 1),
        })
        assert response.status_code == 422

    def test_document_at_the_limit_is_accepted(self, review_stub):
        response = client.post("/api/review/submit", json={
            "document": "あ" * MAX_DOCUMENT_CHARS,
        })
        assert response.status_code == 202

    def test_unknown_ruleset_is_rejected(self):
        response = client.post("/api/review/submit", json={
            "document": NG_DOC, "ruleset": "no-such-ruleset",
        })
        assert response.status_code == 422

    def test_defaults_match_the_spec(self, review_stub):
        """既定値: use_web=False / do_action=True / dry_run=True。

        `use_web` の既定は Support（ON）と**逆**。条文が一次情報であり、
        Web 検索は速度・コストに見合わないため。
        """
        job = job_manager.get(_submit().json()["job_id"])
        assert job.params.use_web is False
        assert job.params.do_action is True
        assert job.params.dry_run is True
        assert job.params.ruleset == "ec_ad"
        assert job.kind == "review"


class TestRuleSetsApi:

    def test_rulesets_lists_builtin_rulesets(self):
        response = client.get("/api/rulesets")
        assert response.status_code == 200
        rulesets = {r["id"]: r for r in response.json()}

        assert set(rulesets) == {"ec_ad"}
        ec_ad = rulesets["ec_ad"]
        assert ec_ad["name"] == "EC広告表示"
        assert ec_ad["rule_count"] == 21
        assert ec_ad["always_check_count"] == 6
        assert ec_ad["laws"] == sorted(["景品表示法", "特定商取引法", "医薬品医療機器等法"])
        assert ec_ad["notify_th"] == pytest.approx(0.85)
        assert ec_ad["confirm_th"] == pytest.approx(0.60)
        assert "No.1" in ec_ad["critical_keywords"]

    def test_rule_descriptions_are_not_exposed(self):
        """ルール本文は LLM プロンプト用。UI へは返さない。"""
        ec_ad = client.get("/api/rulesets").json()[0]
        assert "rules" not in ec_ad
        assert "description" not in ec_ad


class TestSupportIsUnaffected:
    """ジョブ基盤を共有しているため、Support の回帰をここでも押さえる。"""

    def test_support_query_still_works(self, pipeline_stub):
        response = client.post("/api/support/query", json={
            "query": "返品したい", "vertical": "ec",
        })
        assert response.status_code == 202
        job = job_manager.get(response.json()["job_id"])
        assert job.kind == "support"

    def test_verticals_endpoint_is_unchanged(self):
        verticals = {v["id"]: v for v in client.get("/api/verticals").json()}
        assert set(verticals) == {"gov", "saas", "ec"}

    def test_review_and_support_routes_are_distinct(self):
        """公開スキーマ（OpenAPI）で両系統が並存していることを見る。

        `app.routes` を直接覗くと FastAPI の内部表現に依存する
        （0.140 で include_router 済みルータが `_IncludedRouter` に包まれ、
        `.path` を持たなくなった）。CI は fastapi を固定していないので、
        バージョン非依存な公開契約側で判定する。
        """
        paths = set(app.openapi()["paths"])
        assert "/api/support/query" in paths
        assert "/api/review/submit" in paths
        assert "/api/rulesets" in paths

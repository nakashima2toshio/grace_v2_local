# tests/test_review_confidence_and_parallel_retrieve.py
"""Review の 2 つの回帰を固定するテスト。

## A. neutral が混ざる指摘の確信度が 1.00 になっていた（実測 2026-09-29 yakki-04）

`support_rate = supported / (supported + contradicted)` は neutral を分母から外す。
「3 主張中 2 主張だけ判定でき、その 2 つが supported」でも 1.00 になり、
判定できなかった主張が確信度に出ない。Support と同じ減衰（`damp_support_rate`）を
Review にも掛ける。neutral が無ければ減衰しない。

## B. ② Retrieve が直列だった（実測 2026-09-30: 8 検索で約 5 秒）

検索を並列化する。ただし**ログの順序は逐次実行と同じ**であること。

⚠️ LLM にも Qdrant にも接続しない。
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

from backend.app.core.review_agent import run_review_agent_core
from backend.app.core.review_gates import DetectVerdict
from grace.confidence import damp_support_rate

DOCUMENT = "使い続ければシミが治ると評判で、副作用がないので誰でも安心してお使いいただけます。"


def _violates(_text, rule, _evidence):
    return DetectVerdict(violates=True, message=f"{rule.title}に抵触", suggestion="修正", excerpt="")


class TestDampSupportRate:

    CC = SimpleNamespace(groundedness_coverage_strength=0.3, groundedness_coverage_target=0.8)

    def test_no_neutral_means_no_damping(self):
        g = SimpleNamespace(support_rate=1.0, supported=3, contradicted=0, total=3)
        assert damp_support_rate(g, self.CC) == 1.0

    def test_neutral_lowers_the_rate(self):
        g = SimpleNamespace(support_rate=1.0, supported=2, contradicted=0, total=3)
        assert 0.9 < damp_support_rate(g, self.CC) < 1.0

    def test_missing_config_keeps_the_raw_rate(self):
        g = SimpleNamespace(support_rate=1.0, supported=2, contradicted=0, total=3)
        assert damp_support_rate(g, None) == 1.0


class TestReviewConfidence:

    def test_neutral_claim_is_reflected_in_confidence(self, review_stub):
        review_stub.config.confidence.groundedness_coverage_strength = 0.3
        review_stub.config.confidence.groundedness_coverage_target = 0.8
        review_stub.detect = _violates
        g = review_stub.groundedness
        g.support_rate, g.supported, g.contradicted, g.total = 1.0, 2, 0, 3

        result = run_review_agent_core(DOCUMENT)

        assert result.findings
        assert all(0.9 < f.confidence < 1.0 for f in result.findings)

    def test_all_decided_stays_at_one(self, review_stub):
        review_stub.config.confidence.groundedness_coverage_strength = 0.3
        review_stub.config.confidence.groundedness_coverage_target = 0.8
        review_stub.detect = _violates
        g = review_stub.groundedness
        g.support_rate, g.supported, g.contradicted, g.total = 1.0, 3, 0, 3

        result = run_review_agent_core(DOCUMENT)

        assert result.findings and all(f.confidence == 1.0 for f in result.findings)


class TestParallelRetrieve:

    def _run(self, monkeypatch, review_stub, workers):
        monkeypatch.setenv("GRACE_REVIEW_WORKERS", workers)
        review_stub.detect = _violates
        logs = []
        run_review_agent_core(DOCUMENT, emit=lambda ev: logs.append(
            (ev.type, ev.step, ev.message)))
        return logs

    def test_retrievals_overlap(self, review_stub, monkeypatch):
        """rag_search が同時に走ること（直列なら Barrier が解けず失敗）。"""
        monkeypatch.setenv("GRACE_REVIEW_WORKERS", "4")
        barrier = threading.Barrier(2, timeout=5)
        lock = threading.Lock()
        seen = [0]
        # スタブの tool_execute は stub.tool_calls へ追記するだけなので、
        # list を差し替えて append 時に先頭 2 件だけ待ち合わせる。
        class _Waiting(list):
            def append(self, item):
                super().append(item)
                if item[0] == "rag_search":
                    with lock:
                        seen[0] += 1
                        mine = seen[0]
                    if mine <= 2:
                        barrier.wait()

        review_stub.tool_calls = _Waiting()
        review_stub.detect = _violates
        result = run_review_agent_core(DOCUMENT)
        assert result is not None and result.findings

    def test_log_order_matches_serial_run(self, review_stub, monkeypatch):
        serial = self._run(monkeypatch, review_stub, "1")
        parallel = self._run(monkeypatch, review_stub, "4")
        assert [m for _t, s, m in serial if s == "retrieve"] == [
            m for _t, s, m in parallel if s == "retrieve"
        ]

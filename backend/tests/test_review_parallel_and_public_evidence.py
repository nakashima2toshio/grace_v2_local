# backend/tests/test_review_parallel_and_public_evidence.py
"""Review の 2 つの回帰を固定するテスト。

## A. ルール説明の「指示文」が根拠・条文引用へ漏れていた（実測 2026-09-29）

規程コレクションから根拠を引けないと、`RuleItem.description` を根拠に使う。
`description` の 2 段落目以降は ③ Detect の LLM への**指示文**
（「⚠️ …violates=true とすること」「指摘する: / 指摘しない:」）なので、
これが画面の「根拠」・条文引用にそのまま出ていた（yakki-02 / yakki-04）。
根拠へ出すのは第 1 段落（`public_description`）だけにする。

## B. ③ Detect + ④ Ground が全件直列で遅かった（69 秒）

判定ごとの LLM 待ちを並列化する。ただし**結果の並び・ID は逐次実行と同じ**であること。

⚠️ LLM にも Qdrant にも接続しない。
"""
from __future__ import annotations

import threading

from backend.app.core.review_agent import run_review_agent_core
from backend.app.core.review_gates import DetectVerdict
from backend.app.core.rulesets import get_ruleset

DOCUMENT = (
    "当社の美容液は業界No.1の実力です。"
    "使い続ければシミが治ると評判で、副作用がないので誰でも安心してお使いいただけます。"
)


def _rule(rule_id: str):
    return get_ruleset("ec_ad").rule_by_id(rule_id)


class TestPublicEvidence:

    def test_public_description_is_first_paragraph_only(self):
        for rule_id in ("yakki-02", "yakki-04"):
            rule = _rule(rule_id)
            pub = rule.public_description()
            assert pub and pub == rule.description.split("\n")[0].strip()
            assert "violates=true" not in pub
            assert "指摘する:" not in pub and "指摘しない" not in pub

    def test_citation_fallback_has_no_directive(self):
        for rule_id in ("yakki-02", "yakki-04"):
            citation = _rule(rule_id).citation()
            assert "violates=true" not in citation
            assert "指摘しない" not in citation
            assert _rule(rule_id).title in citation

    def test_directive_never_reaches_ground_or_result(self, review_stub):
        """RAG 0 件（条文フォールバック）でも、指示文は根拠にも指摘の引用にも出ない。"""
        review_stub.rag_output = []
        result = run_review_agent_core(DOCUMENT)

        assert result is not None and result.findings
        for _q, _m, sources in review_stub.verify_calls:
            assert not any("violates=true" in s or "指摘しない" in s for s in sources)
        for finding in result.findings:
            assert not any(
                "violates=true" in c or "指摘しない" in c for c in finding.citations
            )

    def test_detect_still_receives_the_full_criteria(self, review_stub):
        """③ Detect の判定基準（description 全文）は従来どおり渡る。"""
        assert "violates=true" in _rule("yakki-02").description


class TestParallelJudging:

    @staticmethod
    def _always_violates(_text, rule, _evidence):
        return DetectVerdict(
            violates=True, message=f"{rule.title}に抵触", suggestion="修正", excerpt="",
        )

    def test_detect_calls_overlap(self, review_stub, monkeypatch):
        """2 件以上の detect が同時に走ること（直列なら Barrier が解けず失敗）。"""
        monkeypatch.setenv("GRACE_REVIEW_WORKERS", "4")
        barrier = threading.Barrier(2, timeout=5)
        lock = threading.Lock()
        waiting = [0]

        def detect(text, rule, evidence):
            with lock:
                waiting[0] += 1
                mine = waiting[0]
            if mine <= 2:  # 先頭 2 件だけ待ち合わせる（直列だとタイムアウトする）
                barrier.wait()
            return self._always_violates(text, rule, evidence)

        review_stub.detect = detect
        result = run_review_agent_core(DOCUMENT)
        assert result is not None and result.findings

    def test_order_and_ids_match_serial_run(self, review_stub, monkeypatch):
        monkeypatch.setenv("GRACE_REVIEW_WORKERS", "1")
        review_stub.detect = self._always_violates
        serial = run_review_agent_core(DOCUMENT)

        monkeypatch.setenv("GRACE_REVIEW_WORKERS", "4")
        parallel = run_review_agent_core(DOCUMENT)

        def key(r):
            return [(f.finding_id, f.rule_id, f.segment_id, f.status) for f in r.findings]

        assert serial.findings and key(serial) == key(parallel)
        ids = [f.finding_id for f in parallel.findings]
        assert ids == [f"f{i:03d}" for i in range(1, len(ids) + 1)]

    def test_invalid_worker_setting_falls_back(self, review_stub, monkeypatch):
        monkeypatch.setenv("GRACE_REVIEW_WORKERS", "abc")
        review_stub.detect = self._always_violates
        assert run_review_agent_core(DOCUMENT).findings
        monkeypatch.setenv("GRACE_REVIEW_WORKERS", "0")
        assert run_review_agent_core(DOCUMENT).findings

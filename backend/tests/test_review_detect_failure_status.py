# backend/tests/test_review_detect_failure_status.py
"""**Detect が失敗した指摘を「確定」にしない**ことを固定するテスト。

## 背景（実測 2026-08-31 / GRACE-Review を 3 回実行）

Detect の第2段が全件 404 で落ち、コンソールには次が並んだ:

    [detect] 判定に失敗（tokusho-01 / NotFoundError）→ 安全側（要確認）

`_build_finding` は `verdict is None` のとき定型文

    「販売価格・送料の明示」に該当する可能性があります（自動判定に失敗したため要確認）

を message に入れて指摘を残す。これは *安全側に倒した保留* である。

ところが後段の groundedness は、この定型文を規程に照らして
`supported=1.00` と支持してしまう。定型文はルール名を言い換えただけなので、
条文からはほぼ必ず読み取れてしまうためである。結果、画面には

    重大 販売価格・送料の明示 特定商取引法 第11条 **確定**
    「…（自動判定に失敗したため要確認）」  確信度 1.00

という**自己矛盾した表示**が出ていた（適正な LP を入れた OK 例でも 9 件中 8 件が
「確定」と出た）。読む人は「確定」を信じるので、これは「安全側へ倒す」という
設計意図を打ち消してしまう。

ここで固定すること:
  1. Detect 失敗の指摘は `confirmed` にならない（`review_required` に留まる）
  2. Detect が成功した指摘の挙動は変わらない（従来どおり `confirmed` になる）
  3. Detect 失敗でも指摘自体は消えない（見落としを作らない）

⚠️ LLM にも Qdrant にも接続しない。
"""
from __future__ import annotations

from types import SimpleNamespace

from backend.app.core.review_agent import run_review_agent_core

# 重大リスク語を含まない文書（強制 high と混ざらないようにする）。
DOC = "初回は無料でご提供します。"


def _supported_gres(n: int = 3):
    """全主張が supported（＝支持率 1.00）の groundedness 応答。"""
    return SimpleNamespace(
        support_rate=1.0, supported=n, contradicted=0, total=n,
        has_contradiction=False, verified=True, reason="", claims=[],
    )


def _run(review_stub):
    events: list = []
    result = run_review_agent_core(DOC, emit=events.append, verbose=True)
    return result, events


class TestDetectFailureIsNeverConfirmed:

    def test_判定に失敗した指摘は確定にならない(self, review_stub):
        review_stub.detect = lambda _t, _r, _e: None      # LLM 判定失敗
        review_stub.groundedness = _supported_gres()

        result, _events = _run(review_stub)

        assert result.findings, "判定に失敗した指摘が消えている"
        for finding in result.findings:
            assert finding.status != "confirmed", (
                f"「{finding.message}」が確定になっている"
            )
            assert finding.status == "review_required"

    def test_確定にしなかったことをログに残す(self, review_stub):
        review_stub.detect = lambda _t, _r, _e: None
        review_stub.groundedness = _supported_gres()

        _result, events = _run(review_stub)

        logs = [e.message for e in events if e.type == "log" and e.step == "ground"]
        assert any("確定にしません" in m for m in logs), logs

    def test_判定できた指摘は従来どおり確定になる(self, review_stub):
        """⚠️ 回帰よけ。失敗時だけを抑えるのであって、全部を要確認にはしない。"""
        review_stub.groundedness = _supported_gres()   # detect は既定（成功）

        result, _events = _run(review_stub)

        assert result.findings
        assert any(f.status == "confirmed" for f in result.findings), (
            "判定できた指摘まで確定から落ちている"
        )

# tests/test_contradiction_marks_unconfirmed.py
"""根拠検証で「矛盾」と判定された主張がある回答には、未確認の注記を付ける。

## 背景（実測 2026-11-07・Mac・本リポジトリの基本版タブ）

    "contradicted_claims": ["新GMとしてマイク・ボルゴンジ氏が就任した。"]
    "support_rate": 0.9091, "decision": "answer", "warning": false

ボルゴンジ氏はタイタンズの GM で、49ers の GM ではない。根拠検証（③）はこの主張を
「矛盾」と判定していたのに、回答ゲート（④）は支持率（10/11 = 0.91）だけで判定するため、
`notify` しきい値を超えて**注記なしの回答**として表示された。結果の `contradiction` は
⑤ の「社内×Web の食い違い」を表す別の旗で、③ の矛盾はどこにも出ていなかった。

支持率が高くても、矛盾と判定された主張が 1 件でもあれば `warning=True`（未確認の注記）にする。
escalate への倒し方は変えない（矛盾ありはもともと救済しない・`_should_rescue_unaffirmed`）。

⚠️ LLM・Qdrant・Embedding には接続しない（`conftest.pipeline_stub`）。
"""
from __future__ import annotations

from backend.app.core.gates import _answer_gate
from backend.app.core.support_agent import AUTO_PROCEED, run_support_agent_core
from tests.conftest import GroundednessStub

NOTIFY, CONFIRM = 0.7, 0.4


class TestAnswerGate:
    def test_支持率が高くても矛盾があれば未確認の注記を付ける(self):
        assert _answer_gate(0.91, True, 4, NOTIFY, CONFIRM, has_contradiction=True) == (
            "answer", True,
        )

    def test_矛盾がなければ従来どおり注記なし(self):
        assert _answer_gate(0.91, True, 4, NOTIFY, CONFIRM, has_contradiction=False) == (
            "answer", False,
        )
        # 引数を省略した呼び出しも従来どおり
        assert _answer_gate(0.91, True, 4, NOTIFY, CONFIRM) == ("answer", False)

    def test_escalateの判定は変えない(self):
        assert _answer_gate(0.2, True, 4, NOTIFY, CONFIRM, has_contradiction=True) == (
            "escalate", False,
        )
        assert _answer_gate(0.91, False, 4, NOTIFY, CONFIRM, has_contradiction=True) == (
            "escalate", False,
        )


class TestPipeline:
    def test_矛盾ありの内部回答は注記つきで表示される(self, pipeline_stub):
        pipeline_stub.groundedness = GroundednessStub(
            support_rate=0.9091, supported=10, contradicted=1, total=12,
            verified=True, has_contradiction=True,
        )
        core = run_support_agent_core(
            "パスワードを忘れました", confirm=lambda _r: AUTO_PROCEED,
        )

        assert core.decision == "answer"
        assert core.warning is True, "③ で矛盾が見つかったのに注記なしで表示された"

    def test_矛盾なしの内部回答は注記なし(self, pipeline_stub):
        core = run_support_agent_core(
            "パスワードを忘れました", confirm=lambda _r: AUTO_PROCEED,
        )

        assert core.decision == "answer"
        assert core.warning is False

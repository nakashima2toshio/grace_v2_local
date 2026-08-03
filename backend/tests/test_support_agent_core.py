# backend/tests/test_support_agent_core.py
"""イベント駆動コア（run_support_agent_core）と CLI ラッパの同等性テスト。

外部依存はスタブ（conftest.install_pipeline_stub）。検証すること:
- CLI 版 `run_support_agent()` とコアが同一の SupportResult を返す（処理の同等性）
- 代表シナリオの decision / 出典 / アクション判定（受け入れ条件 §5-1 の縮約版）
- ステップイベント（①〜⑥、④'・スキップ含む）が期待どおり流れる
- HITL: 承認・拒否・タイムアウトでアクション実行が制御される（§5-2）
"""
from __future__ import annotations

import threading

from agent_support_example import run_support_agent
from backend.app.core.intervention_bridge import InterventionBridge
from backend.app.core.support_agent import (
    AUTO_PROCEED,
    SupportEvent,
    run_support_agent_core,
)
from backend.tests.conftest import GroundednessStub


def collect(events):
    return lambda e: events.append(e)


class TestCliCoreEquivalence:
    """CLI ラッパとコアが同一の判定結果を返すこと（移行の受け入れ条件 §1-3）。"""

    def test_default_password_query(self, pipeline_stub, capsys):
        cli = run_support_agent("パスワードを忘れました")
        core = run_support_agent_core(
            "パスワードを忘れました", confirm=lambda _r: AUTO_PROCEED
        )
        assert cli == core
        assert cli.decision == "answer"
        assert cli.citations == ["[社内] faq.md"]
        assert cli.action is not None and cli.action.action_type == "send_reply"
        assert "[DRY-RUN]" in cli.action_result

    def test_forced_escalate_saas_incident(self, pipeline_stub, capsys):
        pipeline_stub.intent = "incident"  # 「サービスが落ちています」→ incident
        kwargs = dict(vertical="saas", use_web=True)
        cli = run_support_agent("サービスが落ちています", **kwargs)
        core = run_support_agent_core(
            "サービスが落ちています", confirm=lambda _r: AUTO_PROCEED, **kwargs
        )
        assert cli == core
        assert cli.decision == "escalate"
        assert cli.forced_escalate is True
        assert cli.intent == "incident"
        assert cli.action.action_type == "escalate_to_human"
        # 強制エスカレでは ⑤ Web フォールバックは走らない
        assert cli.used_web is False

    def test_no_info_gate_escalates(self, pipeline_stub, capsys):
        """④' 範囲外質問: 「見つかりません」型の回答は answer を通過させない。"""
        pipeline_stub.answer = "該当する情報は見つかりませんでした。お問い合わせ窓口へご連絡ください。"
        pipeline_stub.no_info_verdict = True
        kwargs = dict(vertical="saas", use_web=False)
        cli = run_support_agent("来期の売上見込みは？", **kwargs)
        core = run_support_agent_core(
            "来期の売上見込みは？", confirm=lambda _r: AUTO_PROCEED, **kwargs
        )
        assert cli == core
        assert cli.decision == "escalate"
        assert cli.no_info_detected is True
        assert cli.action.action_type == "escalate_to_human"


class TestStepEvents:
    """ステップ進捗イベント（UI タイムラインの契約）。"""

    def _step_status(self, events, step):
        return [e.status for e in events if e.type == "step" and e.step == step]

    def test_answer_flow_emits_expected_steps(self, pipeline_stub):
        events: list[SupportEvent] = []
        result = run_support_agent_core(
            "パスワードを忘れました", emit=collect(events),
            confirm=lambda _r: AUTO_PROCEED,
        )
        assert result.decision == "answer"
        assert self._step_status(events, "profile") == ["skipped"]
        for step in ("plan", "execute", "confidence", "gate"):
            assert self._step_status(events, step) == ["started", "finished"], step
        assert self._step_status(events, "web") == ["skipped"]
        # 出典が [社内] で候補句なし → ④' は started/finished（判定は素通し）
        assert self._step_status(events, "no_info") == ["started", "finished"]
        assert self._step_status(events, "action") == ["started", "finished"]
        # 最終イベントは result（SupportResult の dict）
        assert events[-1].type == "result"
        assert events[-1].data["decision"] == "answer"

    def test_gate_event_carries_decision_payload(self, pipeline_stub):
        events: list[SupportEvent] = []
        pipeline_stub.intent = "incident"
        run_support_agent_core(
            "サービスが落ちています", vertical="saas",
            emit=collect(events), confirm=lambda _r: AUTO_PROCEED,
        )
        gate = [e for e in events if e.type == "step"
                and e.step == "gate" and e.status == "finished"][0]
        assert gate.data["decision"] == "escalate"
        assert gate.data["forced_escalate"] is True
        assert gate.data["matched_keyword"] == "落ち"
        assert gate.data["intent"] == "incident"
        profile = [e for e in events if e.type == "step"
                   and e.step == "profile" and e.status == "finished"][0]
        assert profile.data["vertical"] == "saas"

    def test_missing_api_key_emits_error(self, pipeline_stub, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        events: list[SupportEvent] = []
        result = run_support_agent_core("テスト", emit=collect(events))
        assert result is None
        assert any(e.type == "error" and "ANTHROPIC_API_KEY" in e.message for e in events)


class TestHitlConfirmFlow:
    """⑥ HITL CONFIRM: 画面承認なしにアクションが実行されないこと（§5-2）。"""

    def _run_with_bridge(self, query, bridge, **kwargs):
        holder = {}

        def target():
            holder["result"] = run_support_agent_core(
                query, confirm=bridge.resolver, **kwargs
            )

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        return thread, holder

    def _wait_intervention(self, events, timeout=5.0):
        import time

        deadline = time.time() + timeout
        while time.time() < deadline:
            for e in list(events):
                if e.type == "intervention" and e.status == "waiting":
                    return e
            time.sleep(0.01)
        raise AssertionError("intervention イベントが流れなかった")

    def test_approve_executes_action(self, pipeline_stub):
        events: list[SupportEvent] = []
        bridge = InterventionBridge(emit=collect(events))
        thread, holder = self._run_with_bridge(
            "返品したい", bridge, vertical="ec", emit=collect(events)
        )
        ev = self._wait_intervention(events)
        bridge.resolve(ev.data["intervention_id"], approve=True)
        thread.join(timeout=10)
        result = holder["result"]
        assert result.action.action_type == "create_ticket"
        assert "[DRY-RUN]" in result.action_result
        assert result.identity_checked is True  # ec は本人確認必須

    def test_reject_cancels_action(self, pipeline_stub):
        events: list[SupportEvent] = []
        bridge = InterventionBridge(emit=collect(events))
        thread, holder = self._run_with_bridge(
            "返品したい", bridge, vertical="ec", emit=collect(events)
        )
        ev = self._wait_intervention(events)
        bridge.resolve(ev.data["intervention_id"], approve=False)
        thread.join(timeout=10)
        result = holder["result"]
        assert "キャンセル" in result.action_result
        assert "[DRY-RUN]" not in result.action_result

    def test_timeout_escalates_without_executing(self, pipeline_stub):
        """承認タイムアウト → 安全側（実行せず有人対応へエスカレーション）。"""
        events: list[SupportEvent] = []
        bridge = InterventionBridge(emit=collect(events), timeout_seconds=0.1)
        thread, holder = self._run_with_bridge(
            "返品したい", bridge, vertical="ec", emit=collect(events)
        )
        thread.join(timeout=10)
        result = holder["result"]
        assert "タイムアウト" in result.action_result
        assert "エスカレーション" in result.action_result
        assert "[DRY-RUN]" not in result.action_result

    def test_default_confirm_is_auto_proceed_for_cli_only(self, pipeline_stub):
        """confirm 未指定（CLI 相当）は自動承認で完走する（既定ドライランのため安全）。"""
        result = run_support_agent_core("返品したい", vertical="ec")
        assert "[DRY-RUN]" in result.action_result

    def test_escalate_action_executes_without_confirmation(self, pipeline_stub):
        """escalate_to_human は承認不要: 承認が来なくてもタイムアウトせず引き継ぎを実行する。

        承認を要求すると、タイムアウト時に「有人対応への引き継ぎ」自体が実行されず
        宙に浮く（#4）。承認を経由せず直接実行されることを固定する。
        """
        pipeline_stub.intent = "incident"  # 「サービスが落ちています」→ 強制エスカレ
        events: list[SupportEvent] = []
        # 解決されないブリッジ（短いタイムアウト）でも、escalate は待たされない。
        bridge = InterventionBridge(emit=collect(events), timeout_seconds=0.1)
        thread, holder = self._run_with_bridge(
            "サービスが落ちています", bridge, vertical="saas", emit=collect(events)
        )
        thread.join(timeout=10)
        result = holder["result"]
        assert result.decision == "escalate"
        assert result.action.action_type == "escalate_to_human"
        assert result.action.requires_confirmation is False
        assert "[DRY-RUN]" in result.action_result  # 承認を経由せず直接実行された
        assert "タイムアウト" not in result.action_result


class TestWebFallbackEvents:
    """⑤ Web フォールバックのイベント（web_reused の明示を含む）。"""

    def test_web_fallback_runs_when_internal_escalates(self, pipeline_stub):
        pipeline_stub.groundedness = GroundednessStub(
            support_rate=0.1, supported=0, contradicted=1, total=3,
            verified=True, has_contradiction=True,  # 矛盾あり → ④救済なし
        )
        pipeline_stub.web_output = [
            {"payload": {"title": "公式ガイド", "source": "https://example.com/g",
                         "answer": "手順の本文"}},
        ]
        events: list[SupportEvent] = []
        result = run_support_agent_core(
            "パスワードを忘れました", emit=collect(events),
            confirm=lambda _r: AUTO_PROCEED,
        )
        web = [e for e in events if e.type == "step"
               and e.step == "web" and e.status == "finished"][0]
        assert web.data["web_reused"] is False
        assert result.used_web is True
        assert any(c.startswith("[Web] 公式ガイド") for c in result.citations)

# backend/tests/test_intervention_bridge.py
"""InterventionBridge（HITL ↔ フロント承認の非同期ブリッジ）の単体テスト。

API キー・Qdrant 不要。ワーカースレッドで resolver を呼び、メインスレッドから
resolve() を注入して、承認 / 拒否 / タイムアウト / ID 不一致 の各経路を固定する。
"""
from __future__ import annotations

import threading

from backend.app.core.intervention_bridge import InterventionBridge
from grace import InterventionAction, InterventionRequest
from grace.confidence import InterventionLevel


def _make_request(timeout_seconds: int = 300) -> InterventionRequest:
    return InterventionRequest(
        level=InterventionLevel.CONFIRM,
        message="アクション実行前の確認: create_ticket",
        reason="テスト",
        timeout_seconds=timeout_seconds,
    )


def _run_resolver_in_thread(bridge: InterventionBridge, request: InterventionRequest):
    """resolver をワーカースレッドで実行し、(thread, result_holder) を返す。"""
    holder = {}

    def target():
        holder["response"] = bridge.resolver(request)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, holder


def _wait_intervention_event(events, timeout: float = 5.0) -> dict:
    """emit された intervention(waiting) イベントが現れるまで待つ。"""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        for ev in list(events):
            if ev.type == "intervention" and ev.status == "waiting":
                return ev
        time.sleep(0.01)
    raise AssertionError("intervention イベントが emit されなかった")


class TestInterventionBridge:
    def test_approve_returns_proceed(self):
        events = []
        bridge = InterventionBridge(emit=events.append)
        thread, holder = _run_resolver_in_thread(bridge, _make_request())

        ev = _wait_intervention_event(events)
        intervention_id = ev.data["intervention_id"]
        assert bridge.resolve(intervention_id, approve=True) is True

        thread.join(timeout=5)
        assert holder["response"].action == InterventionAction.PROCEED
        assert holder["response"].timeout_reached is False
        # 解決イベントが流れる
        assert any(e.type == "intervention" and e.status == "resolved" for e in events)

    def test_reject_returns_cancel(self):
        events = []
        bridge = InterventionBridge(emit=events.append)
        thread, holder = _run_resolver_in_thread(bridge, _make_request())

        ev = _wait_intervention_event(events)
        assert bridge.resolve(ev.data["intervention_id"], approve=False) is True

        thread.join(timeout=5)
        assert holder["response"].action == InterventionAction.CANCEL
        assert holder["response"].timeout_reached is False

    def test_timeout_falls_back_to_cancel_escalate(self):
        """タイムアウト時は安全側（CANCEL + timeout_reached）＝実行せず有人対応へ。"""
        events = []
        bridge = InterventionBridge(emit=events.append, timeout_seconds=0.1)
        thread, holder = _run_resolver_in_thread(bridge, _make_request())

        thread.join(timeout=5)
        assert holder["response"].action == InterventionAction.CANCEL
        assert holder["response"].timeout_reached is True
        assert any(e.type == "intervention" and e.status == "timeout" for e in events)

    def test_unknown_intervention_id_is_rejected(self):
        events = []
        bridge = InterventionBridge(emit=events.append)
        thread, holder = _run_resolver_in_thread(bridge, _make_request())

        _wait_intervention_event(events)
        assert bridge.resolve("no-such-id", approve=True) is False

        # 正しい ID なら解決できる（誤 ID が状態を壊していない）
        ev = [e for e in events if e.type == "intervention"][0]
        assert bridge.resolve(ev.data["intervention_id"], approve=True) is True
        thread.join(timeout=5)
        assert holder["response"].action == InterventionAction.PROCEED

    def test_resolve_without_pending_returns_false(self):
        bridge = InterventionBridge(emit=lambda _e: None)
        assert bridge.resolve("anything", approve=True) is False

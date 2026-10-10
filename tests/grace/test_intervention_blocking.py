"""ブロッキング execute_plan の介入自動進行（_should_pause_for_intervention）の単体テスト。

実 LLM / Qdrant 非依存。介入の一時停止判定ロジックのみを検証する。
- ESCALATE: 常に停止
- CONFIRM : 対話モードかつ非ブロッキング時のみ停止（ブロッキングは自動進行）
"""
from unittest.mock import MagicMock, patch

from grace.confidence import InterventionLevel
from grace.config import GraceConfig
from grace.executor import Executor
from grace.tools import ToolRegistry


def _executor(interactive=True):
    cfg = GraceConfig()
    cfg.intervention.interactive = interactive
    # ツール登録の副作用（Qdrant 等）を避けるためレジストリはモック
    registry = MagicMock(spec=ToolRegistry)
    registry.list_tools.return_value = []
    with patch("grace.executor.create_chat_client", return_value=MagicMock()):
        return Executor(tool_registry=registry, config=cfg)


class TestShouldPauseForIntervention:
    def test_escalate_always_pauses(self):
        ex = _executor(interactive=True)
        ex._noninteractive = False
        assert ex._should_pause_for_intervention(InterventionLevel.ESCALATE) is True
        ex._noninteractive = True  # ブロッキングでも ESCALATE は停止
        assert ex._should_pause_for_intervention(InterventionLevel.ESCALATE) is True

    def test_confirm_pauses_in_interactive_nonblocking(self):
        ex = _executor(interactive=True)
        ex._noninteractive = False
        assert ex._should_pause_for_intervention(InterventionLevel.CONFIRM) is True

    def test_confirm_autoproceeds_when_blocking(self):
        ex = _executor(interactive=True)
        ex._noninteractive = True  # execute_plan（ブロッキング）中
        assert ex._should_pause_for_intervention(InterventionLevel.CONFIRM) is False

    def test_confirm_autoproceeds_when_not_interactive(self):
        ex = _executor(interactive=False)
        ex._noninteractive = False
        assert ex._should_pause_for_intervention(InterventionLevel.CONFIRM) is False

    def test_silent_notify_never_pause(self):
        ex = _executor(interactive=True)
        ex._noninteractive = False
        assert ex._should_pause_for_intervention(InterventionLevel.SILENT) is False
        assert ex._should_pause_for_intervention(InterventionLevel.NOTIFY) is False

    def test_execute_plan_sets_and_resets_noninteractive(self):
        """execute_plan は実行中だけ _noninteractive=True、終了後 False に戻す。"""
        ex = _executor(interactive=True)
        assert ex._noninteractive is False
        # _dispatch_generator をモックして即終了させ、フラグ復帰を確認
        with patch.object(ex, "_dispatch_generator") as mock_disp:
            def _empty_gen(_plan):
                captured["during"] = ex._noninteractive
                return
                yield  # generator にする
            captured = {}
            mock_disp.side_effect = _empty_gen
            ex.execute_plan(MagicMock())
        assert captured.get("during") is True   # 実行中は非対話
        assert ex._noninteractive is False       # 終了後は復帰

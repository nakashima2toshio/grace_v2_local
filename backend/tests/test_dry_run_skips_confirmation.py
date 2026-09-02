# backend/tests/test_dry_run_skips_confirmation.py
"""dry-run では HITL CONFIRM を求めないことの回帰テスト。

## 何が起きていたか（実測 2026-09-03・GRACE-Review 実行）

dry-run（起票せずログのみ・既定 ON）で実行したのに、`create_ticket` は
`requires_confirmation=True` なので承認モーダルが出た。押されないまま
`intervention.default_timeout`（300 秒）が切れ、

    承認待ちがタイムアウトしたため 'create_ticket' は実行せず、
    有人対応へエスカレーションします

となった。**所要 8 分 22 秒のうち 5 分ちょうどがこの空転**で、実処理は
3 分 23 秒だった（最後の LLM 呼び出し 00:21:02 → 完了 00:26:02）。

承認は「取り消せない操作の前に人を挟む」ための仕組みである。副作用ゼロの
dry-run で求めても目的を果たさず、待ち時間と誤ったエスカレーションだけが残る。

## ここで固定すること

- 副作用のないバックエンド（dry-run）では承認を求めず実行すること
- 副作用のあるバックエンドでは従来どおり承認を経由すること
- `has_side_effects` を持たないバックエンドは**安全側（承認あり）**に倒れること
"""
from __future__ import annotations

from typing import Dict

from backend.app.core.support_agent import _perform_action
from grace.intervention import InterventionAction, InterventionResponse
from support_actions import (
    ActionOutcome,
    DryRunActionBackend,
    PseudoActionBackend,
    create_action_backend,
)


class _Action:
    """`ActionRequest` の最小スタブ。"""

    def __init__(self, requires_confirmation: bool = True):
        self.action_type = "create_ticket"
        self.args: Dict = {"summary": "テスト"}
        self.requires_confirmation = requires_confirmation


class _Handler:
    """承認が求められたかどうかを記録するハンドラ。

    `should_continue=False` / `timeout_reached=True` を返す＝**承認が来なかった**
    状況を模す。承認を求めていれば実行されず、求めていなければ実行される。
    """

    def __init__(self):
        self.calls = 0

    def handle(self, _decision):
        self.calls += 1
        # CANCEL + timeout_reached=True ＝ 承認が来ないままタイムアウトした状態
        # （`should_continue` は action から導出されるプロパティ）。
        return InterventionResponse(
            action=InterventionAction.CANCEL, timeout_reached=True
        )


class _NoAttrBackend:
    """`has_side_effects` を宣言していないバックエンド（将来追加された想定）。"""

    name = "unknown"

    def execute(self, action_type: str, args: Dict) -> ActionOutcome:
        return ActionOutcome(success=True, message="実行しました", backend=self.name)


class TestDryRunSkipsConfirmation:
    def test_dry_runでは承認を求めずに実行する(self):
        handler = _Handler()
        message = _perform_action(_Action(), handler, DryRunActionBackend())

        assert handler.calls == 0, "dry-run で承認を求めている"
        assert "DRY-RUN" in message
        assert "タイムアウト" not in message

    def test_副作用のあるバックエンドでは承認を経由する(self):
        handler = _Handler()
        message = _perform_action(_Action(), handler, PseudoActionBackend())

        assert handler.calls == 1, "副作用のあるバックエンドで承認を飛ばしている"
        assert "タイムアウト" in message

    def test_属性の無いバックエンドは安全側に倒れる(self):
        handler = _Handler()
        message = _perform_action(_Action(), handler, _NoAttrBackend())

        assert handler.calls == 1
        assert "タイムアウト" in message

    def test_承認不要のアクションは元から承認を経由しない(self):
        """escalate_to_human 等。dry-run 判定とは独立であることを固定する。"""
        handler = _Handler()
        message = _perform_action(
            _Action(requires_confirmation=False), handler, PseudoActionBackend()
        )

        assert handler.calls == 0
        assert "タイムアウト" not in message


class TestBackendSideEffectFlags:
    def test_dry_runバックエンドだけが副作用なしを宣言する(self):
        assert create_action_backend(dry_run=True).has_side_effects is False
        assert PseudoActionBackend().has_side_effects is True

    def test_既定は安全側のTrue(self):
        from support_actions import ActionBackend

        assert ActionBackend.has_side_effects is True

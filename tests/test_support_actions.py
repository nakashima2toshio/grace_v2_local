# tests/test_support_actions.py
"""support_actions（実 ActionTool バックエンド＋本人確認）のテスト（次工程候補④）。

ネットワーク・LLM 不要（Webhook は requests をモック）。
- バックエンド選択: dry_run → dry-run / URL あり → webhook / なし → pseudo
- Webhook: JSON POST・Bearer トークン・失敗時は例外にせず失敗 Outcome
- 本人確認: demo（dry-run）/ CSV 台帳照合 / 照合手段なしは常に未確認（安全側）
- _perform_action 統合: 未確認なら CONFIRM・バックエンドに到達しない
"""
from unittest.mock import MagicMock, patch

import pytest
import requests

from support_actions import (
    CsvIdentityChecker,
    DryRunActionBackend,
    PseudoActionBackend,
    WebhookActionBackend,
    create_action_backend,
    create_identity_verifier,
)

IDENTITY_CSV = "order_id,email,name\n1001,taro@example.com,山田太郎\n1002,HANAKO@example.com,佐藤花子\n"


class TestCreateActionBackend:
    def test_dry_run_selects_dry_run_backend(self):
        assert isinstance(create_action_backend(dry_run=True), DryRunActionBackend)

    def test_real_mode_with_url_selects_webhook(self):
        backend = create_action_backend(
            dry_run=False, webhook_url="https://hooks.example.com/x", webhook_token="t")
        assert isinstance(backend, WebhookActionBackend)
        assert backend.token == "t"

    def test_real_mode_without_url_selects_pseudo(self):
        backend = create_action_backend(dry_run=False, webhook_url="")
        assert isinstance(backend, PseudoActionBackend)

    @patch.dict("os.environ", {"SUPPORT_ACTION_WEBHOOK_URL": "https://env.example.com/hook",
                               "SUPPORT_ACTION_WEBHOOK_TOKEN": "env-token"})
    def test_env_vars_are_used_when_args_omitted(self):
        backend = create_action_backend(dry_run=False)
        assert isinstance(backend, WebhookActionBackend)
        assert backend.url == "https://env.example.com/hook"
        assert backend.token == "env-token"


class TestBackendExecution:
    def test_dry_run_has_no_side_effect_and_says_so(self):
        outcome = DryRunActionBackend().execute("create_ticket", {"query": "q"})
        assert outcome.success and outcome.backend == "dry-run"
        assert "[DRY-RUN]" in outcome.message

    def test_pseudo_mentions_how_to_enable_real_integration(self):
        outcome = PseudoActionBackend().execute("create_ticket", {})
        assert outcome.success and outcome.backend == "pseudo"
        assert "SUPPORT_ACTION_WEBHOOK_URL" in outcome.message

    def test_webhook_posts_json_with_bearer_token(self):
        backend = WebhookActionBackend("https://hooks.example.com/x", token="secret")
        resp = MagicMock(status_code=200)
        resp.raise_for_status.return_value = None
        with patch("requests.post", return_value=resp) as post:
            outcome = backend.execute("create_ticket", {"subject": "返品"})
        assert outcome.success and outcome.backend == "webhook"
        kwargs = post.call_args.kwargs
        assert kwargs["json"] == {"action_type": "create_ticket", "args": {"subject": "返品"}}
        assert kwargs["headers"]["Authorization"] == "Bearer secret"

    def test_webhook_failure_returns_failed_outcome_not_exception(self):
        backend = WebhookActionBackend("https://hooks.example.com/x")
        with patch("requests.post",
                   side_effect=requests.exceptions.ConnectionError("down")):
            outcome = backend.execute("create_ticket", {})
        assert outcome.success is False
        assert "有人対応" in outcome.message

    def test_webhook_requires_url(self):
        with pytest.raises(ValueError):
            WebhookActionBackend("")


class TestIdentityVerifier:
    def test_dry_run_demo_verifier_always_verifies(self):
        verifier = create_identity_verifier(dry_run=True)
        result = verifier.verify(None)
        assert result.verified is True and result.method == "demo"

    def test_real_mode_without_ledger_never_verifies(self):
        with patch.dict("os.environ", {"SUPPORT_IDENTITY_FILE": ""}):
            verifier = create_identity_verifier(dry_run=False)
        result = verifier.verify({"order_id": "1001", "email": "taro@example.com"})
        assert result.verified is False and result.method == "none"
        assert "SUPPORT_IDENTITY_FILE" in result.detail

    def test_csv_checker_verifies_matching_record(self, tmp_path):
        ledger = tmp_path / "customers.csv"
        ledger.write_text(IDENTITY_CSV, encoding="utf-8")
        verifier = create_identity_verifier(dry_run=False, identity_file=str(ledger))
        assert verifier.method == "csv"
        ok = verifier.verify({"order_id": "1001", "email": "taro@example.com"})
        assert ok.verified is True

    def test_csv_checker_rejects_mismatch_and_missing_fields(self, tmp_path):
        ledger = tmp_path / "customers.csv"
        ledger.write_text(IDENTITY_CSV, encoding="utf-8")
        verifier = create_identity_verifier(dry_run=False, identity_file=str(ledger))
        # 不一致（別注文の email）
        ng = verifier.verify({"order_id": "1001", "email": "hanako@example.com"})
        assert ng.verified is False
        # 識別子不足
        missing = verifier.verify({"order_id": "1001"})
        assert missing.verified is False and "email" in missing.detail

    def test_csv_checker_email_is_case_insensitive(self, tmp_path):
        ledger = tmp_path / "customers.csv"
        ledger.write_text(IDENTITY_CSV, encoding="utf-8")
        checker = CsvIdentityChecker(ledger)
        assert checker({"order_id": "1002", "email": "hanako@example.com"}) is True

    def test_missing_ledger_file_raises(self):
        with pytest.raises(FileNotFoundError):
            CsvIdentityChecker("/no/such/file.csv")


class TestPerformActionIntegration:
    """_perform_action（agent_support_example）と support_actions の統合。"""

    def _handler_proceed(self):
        from grace import InterventionAction, InterventionResponse
        handler = MagicMock()
        handler.handle.return_value = InterventionResponse(
            action=InterventionAction.PROCEED)
        return handler

    def test_unverified_identity_blocks_backend_and_confirm(self):
        from agent_support_example import ActionRequest, _perform_action
        backend = MagicMock()
        verifier = create_identity_verifier(dry_run=False, identity_file="")
        handler = self._handler_proceed()

        message = _perform_action(
            ActionRequest("create_ticket", {"query": "返品したい"}),
            handler, backend, identity_verifier=verifier, identity=None,
        )
        assert "本人確認が完了しない" in message and "有人対応" in message
        backend.execute.assert_not_called()
        handler.handle.assert_not_called()

    def test_verified_identity_confirms_then_executes_backend(self):
        from agent_support_example import ActionRequest, _perform_action
        backend = DryRunActionBackend()
        verifier = create_identity_verifier(dry_run=True)
        handler = self._handler_proceed()

        message = _perform_action(
            ActionRequest("create_ticket", {"query": "返品したい"}),
            handler, backend, identity_verifier=verifier, identity=None,
        )
        assert "[DRY-RUN]" in message
        handler.handle.assert_called_once()

    def test_no_identity_verifier_skips_identity_step(self):
        from agent_support_example import ActionRequest, _perform_action
        backend = DryRunActionBackend()
        handler = self._handler_proceed()

        message = _perform_action(
            ActionRequest("send_reply", {"template": "password_reset"}),
            handler, backend,
        )
        assert "[DRY-RUN]" in message

    def test_confirm_rejection_cancels_action(self):
        from agent_support_example import ActionRequest, _perform_action
        from grace import InterventionAction, InterventionResponse
        backend = MagicMock()
        handler = MagicMock()
        handler.handle.return_value = InterventionResponse(
            action=InterventionAction.CANCEL)

        message = _perform_action(
            ActionRequest("create_ticket", {}), handler, backend,
        )
        assert "キャンセル" in message
        backend.execute.assert_not_called()

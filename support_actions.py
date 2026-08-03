# support_actions.py
"""GRACE-Support のアクション実行バックエンドと本人確認フロー（次工程候補④）。

`agent_support_example.py` の ⑥ Action から使う。従来の「擬似・ドライラン」を
差し替え可能なバックエンド抽象に拡張し、本人確認を「確認済みとして続行」の
表示だけから実際の照合ステップへ引き上げる。

バックエンドの選択（`create_action_backend`）:

| 条件 | バックエンド | 副作用 |
|---|---|---|
| dry_run=True（既定・eval もこれ） | DryRunActionBackend | なし（ログのみ） |
| dry_run=False ＋ SUPPORT_ACTION_WEBHOOK_URL 設定 | WebhookActionBackend | 設定先へ HTTP POST |
| dry_run=False ＋ URL 未設定 | PseudoActionBackend | なし（擬似実行と明示） |

Webhook は Zendesk・Slack・自社チケットシステム等の受け口に共通で使える
汎用連携（JSON POST・Bearer トークン任意）。特定 SaaS への直接 API 連携が
必要になったら ActionBackend を追加実装する。

本人確認（`create_identity_verifier`）:

- dry_run=True: デモ照合（常に確認済み扱い。従来挙動・eval の KPI を維持）
- dry_run=False ＋ SUPPORT_IDENTITY_FILE 設定: CSV 台帳（order_id,email）と
  提示された識別子を照合。一致した場合のみ verified
- dry_run=False ＋ 台帳未設定: 照合手段がないため常に未確認（安全側 =
  アクションは実行せず有人対応へ）
"""
from __future__ import annotations

import csv
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)

# Webhook 連携の設定（環境変数。SERPAPI_KEY 等と同じ流儀で秘匿情報は env に置く）
ENV_WEBHOOK_URL = "SUPPORT_ACTION_WEBHOOK_URL"
ENV_WEBHOOK_TOKEN = "SUPPORT_ACTION_WEBHOOK_TOKEN"
ENV_IDENTITY_FILE = "SUPPORT_IDENTITY_FILE"

# 本人確認で照合する識別子（CSV 台帳のカラム名と一致させる）
IDENTITY_FIELDS = ("order_id", "email")


@dataclass
class ActionOutcome:
    """アクション実行の結果。"""

    success: bool
    message: str
    backend: str


class ActionBackend(ABC):
    """アクション実行バックエンドの共通インターフェース。"""

    name: str = "base"

    @abstractmethod
    def execute(self, action_type: str, args: Dict) -> ActionOutcome:
        """アクションを実行して結果を返す。例外は投げず ActionOutcome で表現する。"""


class DryRunActionBackend(ActionBackend):
    """副作用ゼロのドライラン（既定・eval 用）。"""

    name = "dry-run"

    def execute(self, action_type: str, args: Dict) -> ActionOutcome:
        return ActionOutcome(
            success=True,
            message=f"[DRY-RUN] '{action_type}' を実行（ログのみ・args={args}）",
            backend=self.name,
        )


class PseudoActionBackend(ActionBackend):
    """擬似実行（実連携が未設定のとき dry_run=False で使う。副作用なしを明示）。"""

    name = "pseudo"

    def execute(self, action_type: str, args: Dict) -> ActionOutcome:
        return ActionOutcome(
            success=True,
            message=(f"'{action_type}' を擬似実行しました（実連携未設定・"
                     f"{ENV_WEBHOOK_URL} を設定すると Webhook 連携・args={args}）"),
            backend=self.name,
        )


class WebhookActionBackend(ActionBackend):
    """汎用 Webhook 連携（実 ActionTool）。

    設定した URL へ {"action_type": ..., "args": {...}} を JSON POST する。
    Zendesk 等の SaaS は直接 API ではなく、自社の受け口（サーバレス関数・
    Zapier/Make・社内 API Gateway 等）を挟む前提の汎用連携。
    """

    name = "webhook"

    def __init__(self, url: str, token: str = "", timeout: int = 10):
        if not url:
            raise ValueError("Webhook URL が空です")
        self.url = url
        self.token = token
        self.timeout = timeout

    def execute(self, action_type: str, args: Dict) -> ActionOutcome:
        import requests

        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            resp = requests.post(
                self.url,
                json={"action_type": action_type, "args": args},
                headers=headers,
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Webhook action failed: {e}")
            return ActionOutcome(
                success=False,
                message=f"'{action_type}' の Webhook 連携に失敗しました（{type(e).__name__}: {e}）"
                        "→ 有人対応へ引き継いでください",
                backend=self.name,
            )
        return ActionOutcome(
            success=True,
            message=f"'{action_type}' を Webhook 連携で実行しました（HTTP {resp.status_code}）",
            backend=self.name,
        )


def create_action_backend(
    dry_run: bool,
    webhook_url: Optional[str] = None,
    webhook_token: Optional[str] = None,
) -> ActionBackend:
    """実行モードと設定からアクションバックエンドを選択する。

    webhook_url / webhook_token 未指定時は環境変数
    SUPPORT_ACTION_WEBHOOK_URL / SUPPORT_ACTION_WEBHOOK_TOKEN を参照する。
    """
    if dry_run:
        return DryRunActionBackend()
    url = webhook_url if webhook_url is not None else os.environ.get(ENV_WEBHOOK_URL, "")
    if url:
        token = (webhook_token if webhook_token is not None
                 else os.environ.get(ENV_WEBHOOK_TOKEN, ""))
        return WebhookActionBackend(url, token)
    return PseudoActionBackend()


# =============================================================================
# 本人確認（identity verification）
# =============================================================================

@dataclass
class IdentityResult:
    """本人確認の結果。"""

    verified: bool
    method: str          # "demo" / "csv" / "none"
    detail: str = ""


@dataclass
class IdentityVerifier:
    """本人確認フロー。提示された識別子を checker で照合する。

    checker が None の場合は照合手段がないため常に未確認（安全側）。
    """

    checker: Optional[Callable[[Dict[str, str]], bool]] = None
    method: str = "none"
    required_fields: tuple = field(default=IDENTITY_FIELDS)

    def verify(self, provided: Optional[Dict[str, str]]) -> IdentityResult:
        if self.checker is None:
            return IdentityResult(
                False, "none",
                f"照合手段が未設定です（{ENV_IDENTITY_FILE} に顧客台帳 CSV を設定）",
            )
        provided = provided or {}
        missing = [f for f in self.required_fields if not provided.get(f)]
        if missing:
            return IdentityResult(
                False, self.method, f"識別子が不足しています: {', '.join(missing)}"
            )
        if self.checker(provided):
            return IdentityResult(True, self.method, "識別子が台帳と一致しました")
        return IdentityResult(False, self.method, "識別子が台帳と一致しません")


def _demo_checker(_provided: Dict[str, str]) -> bool:
    return True


class CsvIdentityChecker:
    """CSV 顧客台帳（ヘッダーに IDENTITY_FIELDS を含む）との照合。

    実運用では CRM・注文 DB への照会に置き換える想定の最小実装。
    照合は required_fields 全カラムの完全一致（前後空白は無視・email は小文字化）。
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"顧客台帳 CSV が見つかりません: {self.path}")

    @staticmethod
    def _norm(field_name: str, value: str) -> str:
        value = (value or "").strip()
        return value.lower() if field_name == "email" else value

    def __call__(self, provided: Dict[str, str]) -> bool:
        with self.path.open(encoding="utf-8") as f:
            for record in csv.DictReader(f):
                if all(
                    self._norm(name, record.get(name, ""))
                    == self._norm(name, provided.get(name, ""))
                    for name in IDENTITY_FIELDS
                ):
                    return True
        return False


def create_identity_verifier(
    dry_run: bool,
    identity_file: Optional[str] = None,
) -> IdentityVerifier:
    """実行モードと設定から本人確認フローを構成する。

    - dry_run=True: デモ照合（常に確認済み扱い。eval・デモの従来挙動を維持）
    - dry_run=False: SUPPORT_IDENTITY_FILE（または identity_file）の CSV 台帳で照合。
      未設定なら checker なし＝常に未確認（アクションは実行されない）
    """
    if dry_run:
        # デモ照合は識別子の提示自体を求めない（従来の「確認済みとして続行」挙動を維持）
        return IdentityVerifier(checker=_demo_checker, method="demo", required_fields=())
    path = identity_file if identity_file is not None else os.environ.get(ENV_IDENTITY_FILE, "")
    if path:
        return IdentityVerifier(checker=CsvIdentityChecker(path), method="csv")
    return IdentityVerifier(checker=None, method="none")

# backend/app/api/meta.py
"""メタ情報 API（業界プロファイル一覧・ルールセット一覧・ヘルスチェック）。"""
from __future__ import annotations

import os
from typing import Dict, List

from fastapi import APIRouter

from backend.app.core.rulesets import RULESETS
from backend.app.core.verticals import PROFILES
from backend.app.schemas import RuleSetInfo, VerticalInfo

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/verticals", response_model=List[VerticalInfo])
def list_verticals() -> List[VerticalInfo]:
    """UI のプロファイルセレクタ用に、組み込み業界プロファイルを返す。"""
    return [
        VerticalInfo(
            id=key,
            name=profile.name,
            collections=list(profile.collections),
            escalate_keywords=list(profile.escalate_keywords),
            action_map=dict(profile.action_map),
            require_identity=profile.require_identity,
            notify_th=profile.notify_th,
            confirm_th=profile.confirm_th,
            prompt_addendum=profile.prompt_addendum,
        )
        for key, profile in PROFILES.items()
    ]


@router.get("/rulesets", response_model=List[RuleSetInfo])
def list_rulesets() -> List[RuleSetInfo]:
    """UI のルールセットセレクタ用に、組み込みルールセットを返す。

    ルール本文（`RuleItem.description`）は LLM プロンプト用で UI では使わないため
    返さない。件数と対象法令だけを出して、選択の判断に足りる情報にとどめる。
    """
    return [
        RuleSetInfo(
            id=key,
            name=ruleset.name,
            collections=list(ruleset.collections),
            rule_count=len(ruleset.rules),
            always_check_count=len(ruleset.always_check_rules),
            laws=sorted({rule.law for rule in ruleset.rules}),
            critical_keywords=list(ruleset.critical_keywords),
            action_map=dict(ruleset.action_map),
            notify_th=ruleset.notify_th,
            confirm_th=ruleset.confirm_th,
            prompt_addendum=ruleset.prompt_addendum,
        )
        for key, ruleset in RULESETS.items()
    ]


@router.get("/health")
def health() -> Dict[str, object]:
    """稼働確認と実行前提（APIキー設定有無）の可視化。"""
    return {
        "status": "ok",
        "anthropic_api_key": bool(os.getenv("ANTHROPIC_API_KEY")),
        "google_api_key": bool(os.getenv("GOOGLE_API_KEY")),
    }

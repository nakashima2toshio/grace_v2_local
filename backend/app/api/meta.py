# backend/app/api/meta.py
"""メタ情報 API（業界プロファイル一覧・ルールセット一覧・ヘルスチェック）。"""
from __future__ import annotations

import os
from typing import Dict, List

from fastapi import APIRouter

from backend.app.core.rulesets import RULESETS
from backend.app.core.verticals import PROFILES
from backend.app.schemas import ModelChoice, ModelInfo, RuleSetInfo, VerticalInfo
from config import OllamaConfig, get_selectable_ollama_models
from grace.config import get_config

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/models", response_model=List[ModelChoice])
def list_models() -> List[ModelChoice]:
    """3タブ共通のモデルセレクタ用の選択肢一覧を返す。

    `config.py::get_selectable_ollama_models()` で絞り込み済み
    （Anthropic 系・tool calling 非対応モデルは含まない）。
    """
    return [
        ModelChoice(
            id=m,
            supports_tool_calls=OllamaConfig.supports_tool_calls(m),
            notes=OllamaConfig.get_model_constraints(m).get("notes", ""),
        )
        for m in get_selectable_ollama_models()
    ]


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


@router.get("/model", response_model=ModelInfo)
def model_info() -> ModelInfo:
    """UI のヘッダーに出す「利用モデル名」を返す。

    ⚠️ 表示用の固定文字列を返さないこと。**パイプラインが実際に使う値**を
    `get_config()` から読む。既定は `config.py::get_default_ollama_model()`
    だが、`config/grace_config.yml` や環境変数（`OLLAMA_DEFAULT_MODEL` /
    `GRACE_LLM_MODEL`）で上書きされうるため、ここで解決済みの値を返さないと
    画面の表示と実挙動がずれる。
    """
    llm = get_config().llm
    return ModelInfo(
        provider=llm.provider,
        model=llm.model,
        light_model=llm.light_model,
        heavy_model=llm.heavy_model,
    )


@router.get("/health")
def health() -> Dict[str, object]:
    """稼働確認と実行前提の可視化。

    LLM はローカル（Ollama）実行のため API キーを持たない。ここで可視化するのは
    Embedding（検索）用の `GOOGLE_API_KEY` のみ。
    """
    return {
        "status": "ok",
        "google_api_key": bool(os.getenv("GOOGLE_API_KEY")),
    }

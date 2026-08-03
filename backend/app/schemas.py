# backend/app/schemas.py
"""API のリクエスト/レスポンス/イベントの Pydantic スキーマ。

`SupportResult`（backend/app/core/support_agent.py の dataclass）を JSON 化した
ものが `SupportResultModel`。ステップ進捗は SSE（GET /api/support/stream/{job_id}）
で `SupportEventModel` 形式の JSON として逐次配信される。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    """POST /api/support/query（CLI 引数と 1:1 対応）。"""

    query: str = Field(min_length=1, description="問い合わせ内容（チャット入力）")
    vertical: Optional[Literal["gov", "saas", "ec"]] = Field(
        default=None, description="業界プロファイル（--vertical 相当）")
    dry_run: bool = Field(default=True, description="アクションのドライラン（既定 ON）")
    use_web: bool = Field(default=True, description="Web フォールバック（--no-web 相当の逆）")
    do_action: bool = Field(default=True, description="アクション実行（--no-action 相当の逆）")
    verbose: bool = Field(default=False, description="詳細ログ（-v 相当）")
    identity: Optional[Dict[str, str]] = Field(
        default=None,
        description=(
            "本人確認の識別子（--identity KEY=VALUE 相当。例 {\"order_id\": \"1001\", "
            "\"email\": \"a@example.com\"}）。実際に照合されるのは "
            "require_identity のプロファイル（ec）かつ dry_run=False かつ "
            "SUPPORT_IDENTITY_FILE 設定時のみ"
        ),
    )


class QueryAccepted(BaseModel):
    """ジョブ受付レスポンス。"""

    job_id: str
    stream_url: str


class ConfirmRequest(BaseModel):
    """POST /api/support/confirm/{job_id}（HITL CONFIRM への応答）。"""

    intervention_id: str
    approve: bool


class ConfirmResponse(BaseModel):
    status: Literal["resolved", "not_found", "not_waiting"]


class ActionRequestModel(BaseModel):
    action_type: str
    args: Dict[str, Any] = Field(default_factory=dict)
    requires_confirmation: bool = True


class SupportResultModel(BaseModel):
    """`SupportResult` の JSON 表現（GET /api/support/result/{job_id}）。"""

    answer: Optional[str] = None
    citations: List[str] = Field(default_factory=list)
    groundedness: float = 0.0
    groundedness_decided: int = 0
    decision: Literal["answer", "escalate"] = "escalate"
    warning: bool = False
    used_web: bool = False
    source_agreement: Optional[float] = None
    contradiction: bool = False
    action: Optional[ActionRequestModel] = None
    action_result: Optional[str] = None
    vertical: Optional[str] = None
    overall_confidence: float = 0.0
    intent: Optional[str] = None
    forced_escalate: bool = False
    identity_checked: bool = False
    no_info_detected: bool = False
    web_reused: bool = False


class JobStatusResponse(BaseModel):
    """GET /api/support/result/{job_id}。"""

    job_id: str
    status: Literal["running", "completed", "failed"]
    result: Optional[SupportResultModel] = None


class SupportEventModel(BaseModel):
    """SSE で配信される進捗イベント（core.SupportEvent ＋ 通し番号/時刻）。"""

    seq: int
    ts: float
    type: Literal["step", "log", "intervention", "result", "error"]
    step: Optional[str] = None
    status: Optional[str] = None
    title: str = ""
    message: str = ""
    data: Dict[str, Any] = Field(default_factory=dict)


class VerticalInfo(BaseModel):
    """GET /api/verticals の 1 要素。"""

    id: str
    name: str
    collections: List[str]
    escalate_keywords: List[str]
    action_map: Dict[str, str]
    require_identity: bool
    notify_th: Optional[float] = None
    confirm_th: Optional[float] = None
    prompt_addendum: str = ""


# =============================================================================
# GRACE-Review（文書レビュー）
#
# 設計: backend/docs/review_agent_spec.md §7。`QueryAccepted` / `ConfirmRequest` /
# `ConfirmResponse` は Support と共用し、結果の型だけ新設する。
# =============================================================================

# 入力段のガード。セグメント数 × ルール数の LLM 呼び出しが発散しないようにする
# （コア側の MAX_SEGMENTS / MAX_LLM_CALLS と二重に効かせる）。超過は 422。
MAX_DOCUMENT_CHARS = 50_000

Severity = Literal["high", "medium", "low"]
FindingStatus = Literal["confirmed", "review_required", "suppressed"]


class ReviewRequest(BaseModel):
    """POST /api/review/submit（CLI 引数と 1:1 対応）。"""

    document: str = Field(
        min_length=1, max_length=MAX_DOCUMENT_CHARS, description="点検対象の文書")
    document_title: str = Field(default="無題", description="表示用タイトル")
    ruleset: Optional[Literal["ec_ad"]] = Field(
        default="ec_ad", description="適用するルールセット")
    # Support（既定 ON）と違い既定は OFF。文書レビューは条文が一次情報であり、
    # Web 検索は速度・コストに対して得るものが小さい。
    use_web: bool = Field(default=False, description="Web で法改正を裏取り（既定 OFF）")
    do_action: bool = Field(default=True, description="アクション実行（--no-action 相当の逆）")
    dry_run: bool = Field(default=True, description="アクションのドライラン（既定 ON）")
    verbose: bool = Field(default=False, description="詳細ログ（-v 相当）")


class SegmentModel(BaseModel):
    """検査単位。`start` / `end` は**原文**の文字オフセット（UI のハイライト用）。"""

    segment_id: str
    text: str
    start: int
    end: int
    kind: str = "paragraph"


class ReviewFindingModel(BaseModel):
    """1 件の指摘（UI の指摘カード 1 枚）。"""

    finding_id: str
    segment_id: str
    excerpt: str
    start: int
    end: int

    rule_id: str
    rule_title: str
    category: str
    law: str
    article: str

    message: str
    suggestion: str

    severity: Severity = "medium"
    confidence: float = 0.0
    citations: List[str] = Field(default_factory=list)

    status: FindingStatus = "review_required"
    forced: bool = False
    suppress_reason: Optional[str] = None
    web_checked: bool = False


class FindingSummaryModel(BaseModel):
    high: int = 0
    medium: int = 0
    low: int = 0
    confirmed: int = 0
    review_required: int = 0
    suppressed: int = 0


class ReviewResultModel(BaseModel):
    """`ReviewResult` の JSON 表現（GET /api/review/result/{job_id}）。"""

    document_title: str
    ruleset: Optional[str] = None
    segments: List[SegmentModel] = Field(default_factory=list)
    findings: List[ReviewFindingModel] = Field(default_factory=list)
    summary: FindingSummaryModel = Field(default_factory=FindingSummaryModel)
    used_web: bool = False
    action: Optional[ActionRequestModel] = None
    action_result: Optional[str] = None
    # --- KPI 計測用メタデータ ---
    segments_total: int = 0
    rules_evaluated: int = 0
    detected_raw: int = 0
    rescued: int = 0
    forced_high: int = 0
    truncated: bool = False


class ReviewJobStatusResponse(BaseModel):
    """GET /api/review/result/{job_id}。"""

    job_id: str
    status: Literal["running", "completed", "failed"]
    result: Optional[ReviewResultModel] = None


class RuleSetInfo(BaseModel):
    """GET /api/rulesets の 1 要素（`VerticalInfo` と同型の位置づけ）。"""

    id: str
    name: str
    collections: List[str]
    rule_count: int
    always_check_count: int
    laws: List[str]
    critical_keywords: List[str]
    action_map: Dict[str, str]
    notify_th: float
    confirm_th: float
    prompt_addendum: str = ""

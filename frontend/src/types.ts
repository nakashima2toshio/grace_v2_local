// バックエンド（backend/app/schemas.py）と対応する型定義。

export type Decision = 'answer' | 'escalate';

/** SSE（/api/support/stream/{job_id}）で届く進捗イベント。 */
export interface SupportEvent {
  seq?: number;
  ts?: number;
  type: 'step' | 'log' | 'intervention' | 'result' | 'error' | 'done';
  step?: string | null;
  status?: string | null;
  title?: string;
  message?: string;
  data?: Record<string, unknown>;
}

export interface ActionRequestInfo {
  action_type: string;
  args: Record<string, unknown>;
  requires_confirmation: boolean;
}

/** SupportResult（backend/app/core/support_agent.py）の JSON 表現。 */
export interface SupportResult {
  answer: string | null;
  citations: string[];
  groundedness: number;
  groundedness_decided: number;
  decision: Decision;
  warning: boolean;
  used_web: boolean;
  source_agreement: number | null;
  contradiction: boolean;
  action: ActionRequestInfo | null;
  action_result: string | null;
  vertical: string | null;
  overall_confidence: number;
  intent: string | null;
  forced_escalate: boolean;
  identity_checked: boolean;
  no_info_detected: boolean;
  web_reused: boolean;
}

export interface VerticalInfo {
  id: string;
  name: string;
  collections: string[];
  escalate_keywords: string[];
  action_map: Record<string, string>;
  require_identity: boolean;
  notify_th: number | null;
  confirm_th: number | null;
  prompt_addendum: string;
}

/** HITL CONFIRM の承認待ち（intervention イベントの data）。 */
export interface InterventionInfo {
  intervention_id: string;
  message: string;
  reason?: string | null;
  options?: string[] | null;
  confidence_score?: number | null;
  timeout_seconds?: number;
}

export interface QueryParams {
  query: string;
  vertical: string | null;
  dry_run: boolean;
  use_web: boolean;
  do_action: boolean;
  verbose: boolean;
  /**
   * 本人確認の識別子（CLI の --identity KEY=VALUE 相当）。
   * 実際に照合されるのは require_identity のプロファイル（ec）かつ
   * dry_run=false かつ SUPPORT_IDENTITY_FILE 設定時のみ。
   */
  identity?: Record<string, string> | null;
}

// ===========================================================================
// GRACE-Review（文書レビュー）
//
// SSE のイベント形式は Support と同一なので `SupportEvent` を共用する。
// 異なるのは result の型だけ（backend/app/schemas.py の Review 節と 1:1）。
// ===========================================================================

export type Severity = 'high' | 'medium' | 'low';
export type FindingStatus = 'confirmed' | 'review_required' | 'suppressed';

/** 検査単位。start/end は**原文**の文字オフセット（ハイライト用）。 */
export interface Segment {
  segment_id: string;
  text: string;
  start: number;
  end: number;
  kind: string;
}

/** 1 件の指摘（指摘カード 1 枚）。 */
export interface ReviewFinding {
  finding_id: string;
  segment_id: string;
  excerpt: string;
  start: number;
  end: number;

  rule_id: string;
  rule_title: string;
  category: string;
  law: string;
  article: string;

  message: string;
  suggestion: string;

  severity: Severity;
  confidence: number;
  citations: string[];

  status: FindingStatus;
  forced: boolean;
  suppress_reason: string | null;
  web_checked: boolean;
}

export interface FindingSummary {
  high: number;
  medium: number;
  low: number;
  confirmed: number;
  review_required: number;
  suppressed: number;
}

/** ReviewResult（backend/app/core/review_agent.py）の JSON 表現。 */
export interface ReviewResult {
  document_title: string;
  ruleset: string | null;
  segments: Segment[];
  findings: ReviewFinding[];
  summary: FindingSummary;
  used_web: boolean;
  action: ActionRequestInfo | null;
  action_result: string | null;
  segments_total: number;
  rules_evaluated: number;
  detected_raw: number;
  rescued: number;
  forced_high: number;
  truncated: boolean;
}

export interface RuleSetInfo {
  id: string;
  name: string;
  collections: string[];
  rule_count: number;
  always_check_count: number;
  laws: string[];
  critical_keywords: string[];
  action_map: Record<string, string>;
  notify_th: number;
  confirm_th: number;
  prompt_addendum: string;
}

export interface ReviewParams {
  document: string;
  document_title: string;
  ruleset: string | null;
  use_web: boolean;
  do_action: boolean;
  dry_run: boolean;
  verbose: boolean;
}

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

// ===========================================================================
// データ準備パイプライン（チャンキング → Q/A 生成 → Qdrant 登録 → コレクション管理）
//
// backend/app/schemas.py の同名クラスと 1:1。エージェント 2 種とは別系統で、
// 「データを準備する」側の型。
// ===========================================================================

/** GET /api/qdrant/health。**Qdrant が落ちていても 200** が返る。 */
export interface QdrantHealth {
  available: boolean;
  message: string;
  url: string | null;
  collections_count: number | null;
}

/** GET /api/qdrant/collections の 1 要素。 */
export interface CollectionInfo {
  name: string;
  points_count: number;
  status: string;
}

/**
 * GET /api/qdrant/collections/{name}。
 * `vector_size` / `distance` は Named vectors 構成だと object になりうるため unknown。
 */
export interface CollectionDetail {
  name: string;
  points_count: number;
  vectors_count: number | null;
  indexed_vectors: number | null;
  status: string;
  vector_size: unknown;
  distance: unknown;
  sources: Record<string, CollectionSource>;
  sample_size: number;
  error: string | null;
}

/** payload の source を集計した 1 件（`fetch_collection_source_info`）。 */
export interface CollectionSource {
  sample_count: number;
  method: string;
  domain: string;
  estimated_total?: number;
  percentage?: number;
}

/**
 * GET /api/qdrant/collections/{name}/points。
 * **payload のキーはコレクションごとに違う**ため列は固定できない。
 * `columns` の順で `rows` を描画する。
 */
export interface CollectionPoints {
  name: string;
  columns: string[];
  rows: Array<Record<string, unknown>>;
  limit: number;
}

export interface InputFileInfo {
  name: string;
  /** 'ディレクトリ名/ファイル名' 形式。絶対パスは返らない */
  path: string;
  size: number;
  /** UNIX epoch 秒（Python の st_mtime） */
  modified: number;
  suffix: string;
}

export interface InputFileListResponse {
  dir: string;
  allowed_dirs: string[];
  files: InputFileInfo[];
}

/** POST /api/chunking/run（CLI 引数と 1:1）。 */
export interface ChunkingParams {
  input_file: string;
  output_dir: string;
  model: string;
  workers: number;
  block_size: number;
  text_column: string | null;
  max_rows: number | null;
  combine_rows: boolean;
  resume: string | null;
  verbose: boolean;
}

/**
 * POST /api/qdrant/register。
 * ⚠️ `recreate: true` は既存コレクションを削除して作り直す（要承認）。
 */
export interface RegisterParams {
  input_file: string;
  collection: string;
  recreate: boolean;
  batch_size: number;
  embed_workers: number;
  text_col: string | null;
  domain: string | null;
  max_docs: number | null;
  provider: string;
  normalize_filename: boolean;
  create_ui_csv: boolean;
  ui_output_dir: string;
  verbose: boolean;
}

/** データ準備ジョブの種別。SSE のステップ ID 集合がこれで決まる。 */
export type DataJobKind = 'chunking' | 'register' | 'delete';

/**
 * データ準備ジョブの結果。**種別によって形が違う**ため、
 * バックエンドは素の dict で返し `kind` で判別させる。
 */
export interface DataJobResult {
  kind: DataJobKind;
  // chunking
  input_file?: string;
  output_file?: string;
  chunks?: number;
  chars?: number;
  model?: string;
  // register
  collection?: string;
  registered?: boolean;
  points?: number;
  points_before?: number;
  recreate?: boolean;
  // delete
  deleted?: string[];
  failed?: string[];
  missing?: string[];
  total_points?: number;
  // 共通（承認されなかった場合）
  cancelled?: boolean;
  reason?: string;
}

/**
 * GET /api/data/result/{job_id}。
 * ジョブが存在するかの確認（再購読前のチェック）にも使う。
 */
export interface DataJobStatusResponse {
  job_id: string;
  kind: string;
  status: 'running' | 'completed' | 'failed';
  result: DataJobResult | null;
}

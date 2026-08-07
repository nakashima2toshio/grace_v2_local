// データ準備ジョブ（チャンキング / 登録 / 削除）の SSE イベントを畳み込む reducer。
//
// `jobReducer.ts`（Support）・`reviewReducer.ts`（Review）と**同じ形**。
// 違うのは 2 点だけ:
//
// 1. **ステップ ID 集合がジョブ種別で変わる。** Support / Review は 1 種類しか
//    扱わないので定数で済むが、こちらは chunking / register / delete で
//    ステップが違うため `kind` から引く。
// 2. 結果の型が `DataJobResult`（種別で形が違う素の dict）。
//
// 純関数・副作用ゼロ（vitest で検証する）。
import type { DataJobKind, DataJobResult, InterventionInfo, SupportEvent } from '../types';

// --- ステップ定義（backend/app/core/data_jobs.py の *_STEP_IDS と 1:1）---------

export const CHUNKING_STEP_IDS = ['load', 'chunk', 'save'] as const;
export const REGISTER_STEP_IDS = ['prepare', 'confirm', 'embed', 'upsert'] as const;
export const DELETE_STEP_IDS = ['inspect', 'confirm', 'delete'] as const;

export const CHUNKING_STEP_LABELS: Record<string, string> = {
  load: '① 入力読み込み（CSV / テキスト）',
  chunk: '② セマンティックチャンク化（LLM・3 段階）',
  save: '③ CSV 出力',
};

export const REGISTER_STEP_LABELS: Record<string, string> = {
  prepare: '① 入力検証・コレクション名の決定',
  confirm: '② HITL CONFIRM（recreate 時のみ）',
  embed: '③ Embedding 生成',
  upsert: '④ Qdrant へ登録',
};

export const DELETE_STEP_LABELS: Record<string, string> = {
  inspect: '① 削除対象の確認',
  confirm: '② HITL CONFIRM（承認が必要）',
  delete: '③ 削除実行',
};

/** ジョブ種別 → ステップ ID 列。 */
export function stepIdsFor(kind: DataJobKind): readonly string[] {
  switch (kind) {
    case 'chunking':
      return CHUNKING_STEP_IDS;
    case 'register':
      return REGISTER_STEP_IDS;
    case 'delete':
      return DELETE_STEP_IDS;
    default:
      return [];
  }
}

/** ジョブ種別 → ステップ表示名。 */
export function stepLabelsFor(kind: DataJobKind): Record<string, string> {
  switch (kind) {
    case 'chunking':
      return CHUNKING_STEP_LABELS;
    case 'register':
      return REGISTER_STEP_LABELS;
    case 'delete':
      return DELETE_STEP_LABELS;
    default:
      return {};
  }
}

// --- 状態 ---------------------------------------------------------------------

export type StepStatus = 'pending' | 'running' | 'done' | 'skipped';

export interface DataStepState {
  id: string;
  status: StepStatus;
  logs: string[];
  data: Record<string, unknown>;
}

export type DataPhase = 'idle' | 'running' | 'completed' | 'failed';

export interface DataJobState {
  jobId: string | null;
  kind: DataJobKind;
  phase: DataPhase;
  steps: Record<string, DataStepState>;
  intervention: InterventionInfo | null;
  result: DataJobResult | null;
  error: string | null;
  logs: string[];
}

export type DataAction =
  | { type: 'started'; jobId: string; kind: DataJobKind }
  | { type: 'event'; event: SupportEvent }
  | { type: 'confirm_sent' }
  | { type: 'failed'; message: string }
  | { type: 'reset' };

function emptySteps(kind: DataJobKind): Record<string, DataStepState> {
  const steps: Record<string, DataStepState> = {};
  for (const id of stepIdsFor(kind)) {
    steps[id] = { id, status: 'pending', logs: [], data: {} };
  }
  return steps;
}

export function initialDataState(kind: DataJobKind = 'chunking'): DataJobState {
  return {
    jobId: null,
    kind,
    phase: 'idle',
    steps: emptySteps(kind),
    intervention: null,
    result: null,
    error: null,
    logs: [],
  };
}

function updateStep(
  state: DataJobState,
  stepId: string,
  patch: Partial<DataStepState>,
): DataJobState {
  const current = state.steps[stepId];
  // 未知のステップ ID は無視する（バックエンドが増やしてフロントが未追随でも壊れない）
  if (!current) return state;
  return {
    ...state,
    steps: {
      ...state.steps,
      [stepId]: {
        ...current,
        status: patch.status ?? current.status,
        logs: patch.logs ?? current.logs,
        data: patch.data ?? current.data,
      },
    },
  };
}

function applyEvent(state: DataJobState, event: SupportEvent): DataJobState {
  switch (event.type) {
    case 'step': {
      const stepId = event.step ?? '';
      const status: StepStatus =
        event.status === 'started'
          ? 'running'
          : event.status === 'skipped'
            ? 'skipped'
            : 'done';
      // ⚠️ data は「置換」であって「マージ」ではない（Support / Review と同じ）。
      // step_finished の data が step_started の data を丸ごと上書きする。
      return updateStep(state, stepId, {
        status,
        data: (event.data ?? {}) as Record<string, unknown>,
      });
    }
    case 'log': {
      const message = event.message ?? '';
      const stepId = event.step ?? '';
      const step = state.steps[stepId];
      if (step) {
        return updateStep(state, stepId, { logs: [...step.logs, message] });
      }
      // ステップに紐づかないログは「その他のログ」へ。捨てない
      return { ...state, logs: [...state.logs, message] };
    }
    case 'intervention': {
      const data = (event.data ?? {}) as unknown as InterventionInfo;
      if (event.status === 'waiting') {
        return {
          ...state,
          intervention: { ...data, message: event.message ?? '' },
        };
      }
      return { ...state, intervention: null };
    }
    case 'result':
      return { ...state, result: (event.data ?? null) as DataJobResult | null };
    case 'error':
      return { ...state, phase: 'failed', error: event.message ?? '不明なエラー' };
    case 'done':
      return {
        ...state,
        phase: state.phase === 'failed' ? 'failed' : 'completed',
        intervention: null,
      };
    default:
      return state;
  }
}

export function dataReducer(state: DataJobState, action: DataAction): DataJobState {
  switch (action.type) {
    case 'started':
      return {
        ...initialDataState(action.kind),
        jobId: action.jobId,
        kind: action.kind,
        phase: 'running',
      };
    case 'event':
      return applyEvent(state, action.event);
    case 'confirm_sent':
      return { ...state, intervention: null };
    case 'failed':
      return { ...state, phase: 'failed', error: action.message };
    case 'reset':
      return initialDataState(state.kind);
    default:
      return state;
  }
}

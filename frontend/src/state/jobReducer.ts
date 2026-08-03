// SSE イベント列を UI 状態（ステップタイムライン・承認待ち・最終結果）へ畳み込む
// 純 reducer。副作用ゼロ（vitest で単体テスト可能）。
import type {
  InterventionInfo,
  SupportEvent,
  SupportResult,
} from '../types';

/** バックエンドのステップ ID（backend/app/core/support_agent.py の STEP_IDS）。 */
export const STEP_IDS = [
  'profile',
  'plan',
  'execute',
  'confidence',
  'gate',
  'web',
  'no_info',
  'action',
] as const;

export type StepId = (typeof STEP_IDS)[number];

export const STEP_LABELS: Record<StepId, string> = {
  profile: '業界プロファイル適用',
  plan: '① Plan（planner）',
  execute: '② Execute（内部RAG → reasoning）',
  confidence: '③ Groundedness（根拠検証）',
  gate: '④ 回答ゲート＋強制エスカレ＋救済',
  web: '⑤ Web フォールバック',
  no_info: "④' 情報なし回答検知",
  action: '⑥ Action（本人確認 → HITL CONFIRM → 実行）',
};

export type StepStatus = 'pending' | 'running' | 'done' | 'skipped';

export interface StepState {
  id: StepId;
  status: StepStatus;
  logs: string[];
  data: Record<string, unknown>;
}

export type JobPhase = 'idle' | 'running' | 'completed' | 'failed';

export interface JobState {
  jobId: string | null;
  phase: JobPhase;
  steps: Record<StepId, StepState>;
  intervention: InterventionInfo | null;
  result: SupportResult | null;
  error: string | null;
  logs: string[];
}

export type JobAction =
  | { type: 'started'; jobId: string }
  | { type: 'event'; event: SupportEvent }
  | { type: 'confirm_sent' }
  | { type: 'failed'; message: string }
  | { type: 'reset' };

function emptySteps(): Record<StepId, StepState> {
  const steps = {} as Record<StepId, StepState>;
  for (const id of STEP_IDS) {
    steps[id] = { id, status: 'pending', logs: [], data: {} };
  }
  return steps;
}

export const initialJobState: JobState = {
  jobId: null,
  phase: 'idle',
  steps: emptySteps(),
  intervention: null,
  result: null,
  error: null,
  logs: [],
};

function isStepId(step: string | null | undefined): step is StepId {
  return !!step && (STEP_IDS as readonly string[]).includes(step);
}

function updateStep(
  state: JobState,
  step: StepId,
  patch: Partial<StepState>,
): JobState {
  const current = state.steps[step];
  return {
    ...state,
    steps: {
      ...state.steps,
      [step]: {
        ...current,
        ...patch,
        logs: patch.logs ?? current.logs,
        data: patch.data ? { ...current.data, ...patch.data } : current.data,
      },
    },
  };
}

function applyEvent(state: JobState, event: SupportEvent): JobState {
  switch (event.type) {
    case 'step': {
      if (!isStepId(event.step)) return state;
      const status: StepStatus =
        event.status === 'started'
          ? 'running'
          : event.status === 'skipped'
            ? 'skipped'
            : 'done';
      return updateStep(state, event.step, {
        status,
        data: (event.data ?? {}) as Record<string, unknown>,
      });
    }
    case 'log': {
      const message = event.message ?? '';
      if (isStepId(event.step)) {
        const step = state.steps[event.step];
        return updateStep(state, event.step, { logs: [...step.logs, message] });
      }
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
      // resolved / timeout → モーダルを閉じる
      return { ...state, intervention: null };
    }
    case 'result':
      return {
        ...state,
        result: (event.data ?? null) as unknown as SupportResult,
      };
    case 'error':
      return { ...state, error: event.message ?? '実行に失敗しました' };
    case 'done':
      return {
        ...state,
        intervention: null,
        phase: event.status === 'failed' ? 'failed' : 'completed',
      };
    default:
      return state;
  }
}

export function jobReducer(state: JobState, action: JobAction): JobState {
  switch (action.type) {
    case 'started':
      return { ...initialJobState, jobId: action.jobId, phase: 'running', steps: emptySteps() };
    case 'event':
      return applyEvent(state, action.event);
    case 'confirm_sent':
      return { ...state, intervention: null };
    case 'failed':
      return { ...state, phase: 'failed', error: action.message };
    case 'reset':
      return { ...initialJobState, steps: emptySteps() };
    default:
      return state;
  }
}

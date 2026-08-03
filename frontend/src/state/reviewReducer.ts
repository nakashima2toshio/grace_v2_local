// GRACE-Review の SSE イベント列を UI 状態へ畳み込む純 reducer。副作用ゼロ。
//
// 設計: backend/docs/review_agent_spec.md §8.3。
// `jobReducer` を**ジェネリック化せず**、Review 用に薄い reducer を新設している。
// 構造は同じでも result の型（ReviewResult / SupportResult）が異なるだけなので、
// 無理な共通化はしない方針。SSE のイベント形式は Support と同一のため
// `SupportEvent` はそのまま共用する。
import type { InterventionInfo, ReviewResult, SupportEvent } from '../types';

/** バックエンドのステップ ID（backend/app/core/review_agent.py の REVIEW_STEP_IDS）。 */
export const REVIEW_STEP_IDS = [
  'ruleset',
  'segment',
  'retrieve',
  'detect',
  'ground',
  'suppress',
  'web',
  'severity',
  'action',
] as const;

export type ReviewStepId = (typeof REVIEW_STEP_IDS)[number];

// 番号は Support のパイプラインとの対応を示す呼称。実行順とは一致しない
// （Support で ④' が ⑤ の後に来るのと同じ）。
export const REVIEW_STEP_LABELS: Record<ReviewStepId, string> = {
  ruleset: 'S1 ルールセット適用',
  segment: '① Segment（文書を検査単位へ分割）',
  retrieve: '② Retrieve（規程を RAG 検索）',
  detect: '③ Detect（二段判定で違反候補を検出）',
  ground: '④ Ground（指摘の根拠を検証）',
  suppress: "④' Suppress（誤検知抑止 + 救済）",
  web: '⑥ Web 裏取り（法改正・ガイドライン更新）',
  severity: '⑤ Severity（重大度の確定＋強制 high）',
  action: '⑦ Action（レポート → HITL CONFIRM → 実行）',
};

export type StepStatus = 'pending' | 'running' | 'done' | 'skipped';

export interface ReviewStepState {
  id: ReviewStepId;
  status: StepStatus;
  logs: string[];
  data: Record<string, unknown>;
}

export type ReviewPhase = 'idle' | 'running' | 'completed' | 'failed';

export interface ReviewJobState {
  jobId: string | null;
  phase: ReviewPhase;
  /** 実行対象の原文。ハイライト描画に使う（結果を待たずに表示できる）。 */
  document: string;
  documentTitle: string;
  steps: Record<ReviewStepId, ReviewStepState>;
  intervention: InterventionInfo | null;
  result: ReviewResult | null;
  error: string | null;
  logs: string[];
  /** UI 上で選択中の指摘（原文ハイライト ⇔ 指摘カードの相互ジャンプ用）。 */
  selectedFindingId: string | null;
}

export type ReviewAction =
  | { type: 'started'; jobId: string; document: string; documentTitle: string }
  | { type: 'event'; event: SupportEvent }
  | { type: 'confirm_sent' }
  | { type: 'select_finding'; findingId: string | null }
  | { type: 'failed'; message: string }
  | { type: 'reset' };

function emptySteps(): Record<ReviewStepId, ReviewStepState> {
  const steps = {} as Record<ReviewStepId, ReviewStepState>;
  for (const id of REVIEW_STEP_IDS) {
    steps[id] = { id, status: 'pending', logs: [], data: {} };
  }
  return steps;
}

export const initialReviewState: ReviewJobState = {
  jobId: null,
  phase: 'idle',
  document: '',
  documentTitle: '',
  steps: emptySteps(),
  intervention: null,
  result: null,
  error: null,
  logs: [],
  selectedFindingId: null,
};

function isReviewStepId(step: string | null | undefined): step is ReviewStepId {
  return !!step && (REVIEW_STEP_IDS as readonly string[]).includes(step);
}

function updateStep(
  state: ReviewJobState,
  step: ReviewStepId,
  patch: Partial<ReviewStepState>,
): ReviewJobState {
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

function applyEvent(state: ReviewJobState, event: SupportEvent): ReviewJobState {
  switch (event.type) {
    case 'step': {
      if (!isReviewStepId(event.step)) return state;
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
      if (isReviewStepId(event.step)) {
        const step = state.steps[event.step];
        return updateStep(state, event.step, { logs: [...step.logs, message] });
      }
      return { ...state, logs: [...state.logs, message] };
    }
    case 'intervention': {
      const data = (event.data ?? {}) as unknown as InterventionInfo;
      if (event.status === 'waiting') {
        return { ...state, intervention: { ...data, message: event.message ?? '' } };
      }
      // resolved / timeout → モーダルを閉じる
      return { ...state, intervention: null };
    }
    case 'result':
      return { ...state, result: (event.data ?? null) as unknown as ReviewResult };
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

export function reviewReducer(
  state: ReviewJobState,
  action: ReviewAction,
): ReviewJobState {
  switch (action.type) {
    case 'started':
      return {
        ...initialReviewState,
        jobId: action.jobId,
        phase: 'running',
        document: action.document,
        documentTitle: action.documentTitle,
        steps: emptySteps(),
      };
    case 'event':
      return applyEvent(state, action.event);
    case 'confirm_sent':
      return { ...state, intervention: null };
    case 'select_finding':
      return { ...state, selectedFindingId: action.findingId };
    case 'failed':
      return { ...state, phase: 'failed', error: action.message };
    case 'reset':
      return { ...initialReviewState, steps: emptySteps() };
    default:
      return state;
  }
}

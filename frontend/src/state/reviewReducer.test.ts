// reviewReducer（SSE イベント列 → UI 状態）の単体テスト。
import { describe, expect, it } from 'vitest';
import type { ReviewResult, SupportEvent } from '../types';
import {
  REVIEW_STEP_IDS,
  REVIEW_STEP_LABELS,
  initialReviewState,
  reviewReducer,
  type ReviewJobState,
} from './reviewReducer';

function apply(state: ReviewJobState, ...events: SupportEvent[]): ReviewJobState {
  return events.reduce((s, event) => reviewReducer(s, { type: 'event', event }), state);
}

function started(document = '業界No.1の品質です。'): ReviewJobState {
  return reviewReducer(initialReviewState, {
    type: 'started',
    jobId: 'job1',
    document,
    documentTitle: 'LP案',
  });
}

const RESULT: ReviewResult = {
  document_title: 'LP案',
  ruleset: 'ec_ad',
  segments: [],
  findings: [],
  summary: {
    high: 1,
    medium: 0,
    low: 0,
    confirmed: 0,
    review_required: 1,
    suppressed: 2,
  },
  used_web: false,
  action: null,
  action_result: null,
  segments_total: 1,
  rules_evaluated: 7,
  detected_raw: 3,
  rescued: 0,
  forced_high: 1,
  truncated: false,
};

describe('reviewReducer', () => {
  it('started で実行中状態に初期化され、原文を保持する', () => {
    const state = started();
    expect(state.phase).toBe('running');
    expect(state.jobId).toBe('job1');
    expect(state.document).toBe('業界No.1の品質です。');
    expect(state.documentTitle).toBe('LP案');
    expect(state.steps.segment.status).toBe('pending');
  });

  it('step イベントで running → done / skipped が反映される', () => {
    let state = started();
    state = apply(state, { type: 'step', step: 'segment', status: 'started' });
    expect(state.steps.segment.status).toBe('running');

    state = apply(
      state,
      { type: 'step', step: 'segment', status: 'finished', data: { segments: 4 } },
      { type: 'step', step: 'web', status: 'skipped', data: { reason: '無効' } },
    );
    expect(state.steps.segment.status).toBe('done');
    expect(state.steps.segment.data.segments).toBe(4);
    expect(state.steps.web.status).toBe('skipped');
  });

  it('未知のステップ ID は無視される（Support のステップが混ざっても壊れない）', () => {
    const state = apply(started(), { type: 'step', step: 'plan', status: 'started' });
    expect(state).toEqual(started());
  });

  it('log イベントはステップ配下に積まれ、step 無しは全体ログへ回る', () => {
    const state = apply(
      started(),
      { type: 'log', step: 'detect', message: '候補 3 ルール' },
      { type: 'log', message: 'ステップ外のログ' },
    );
    expect(state.steps.detect.logs).toEqual(['候補 3 ルール']);
    expect(state.logs).toEqual(['ステップ外のログ']);
  });

  it('result イベントで ReviewResult が入る', () => {
    const state = apply(started(), {
      type: 'result',
      data: RESULT as unknown as Record<string, unknown>,
    });
    expect(state.result?.summary.high).toBe(1);
    expect(state.result?.summary.suppressed).toBe(2);
    expect(state.result?.rules_evaluated).toBe(7);
  });

  it('intervention(waiting) でモーダル用の状態が立ち、resolved で閉じる', () => {
    let state = apply(started(), {
      type: 'intervention',
      status: 'waiting',
      message: '起票してよいですか',
      data: { intervention_id: 'iv1' },
    });
    expect(state.intervention?.intervention_id).toBe('iv1');
    expect(state.intervention?.message).toBe('起票してよいですか');

    state = apply(state, { type: 'intervention', status: 'resolved' });
    expect(state.intervention).toBeNull();
  });

  it('confirm_sent でモーダルを閉じる（応答の往復を待たせない）', () => {
    let state = apply(started(), {
      type: 'intervention',
      status: 'waiting',
      data: { intervention_id: 'iv1' },
    });
    state = reviewReducer(state, { type: 'confirm_sent' });
    expect(state.intervention).toBeNull();
  });

  it('done で completed / failed が確定し、承認待ちは解除される', () => {
    const ok = apply(started(), { type: 'done', status: 'completed' });
    expect(ok.phase).toBe('completed');

    const ng = apply(
      started(),
      { type: 'intervention', status: 'waiting', data: { intervention_id: 'iv1' } },
      { type: 'done', status: 'failed' },
    );
    expect(ng.phase).toBe('failed');
    expect(ng.intervention).toBeNull();
  });

  it('error イベントでメッセージを保持する', () => {
    const state = apply(started(), {
      type: 'error',
      message: '⚠️ ANTHROPIC_API_KEY が未設定です。',
    });
    expect(state.error).toContain('ANTHROPIC_API_KEY');
  });

  it('select_finding で選択中の指摘が切り替わる', () => {
    let state = reviewReducer(started(), { type: 'select_finding', findingId: 'f001' });
    expect(state.selectedFindingId).toBe('f001');
    state = reviewReducer(state, { type: 'select_finding', findingId: null });
    expect(state.selectedFindingId).toBeNull();
  });

  it('failed アクションで phase とエラーが立つ（SSE 切断時など）', () => {
    const state = reviewReducer(started(), { type: 'failed', message: '切断されました' });
    expect(state.phase).toBe('failed');
    expect(state.error).toBe('切断されました');
  });

  it('reset で初期状態へ戻る', () => {
    const state = reviewReducer(started(), { type: 'reset' });
    expect(state).toEqual(initialReviewState);
  });

  it('全ステップにラベルが定義されている（UI の表示漏れ防止）', () => {
    for (const id of REVIEW_STEP_IDS) {
      expect(REVIEW_STEP_LABELS[id]).toBeTruthy();
    }
    expect(Object.keys(REVIEW_STEP_LABELS)).toHaveLength(REVIEW_STEP_IDS.length);
  });
});

// jobReducer（SSE イベント列 → UI 状態）の単体テスト。
import { describe, expect, it } from 'vitest';
import type { SupportEvent } from '../types';
import {
  initialJobState,
  jobReducer,
  type JobState,
} from './jobReducer';

function apply(state: JobState, ...events: SupportEvent[]): JobState {
  return events.reduce((s, event) => jobReducer(s, { type: 'event', event }), state);
}

function started(): JobState {
  return jobReducer(initialJobState, { type: 'started', jobId: 'job1' });
}

describe('jobReducer', () => {
  it('started で実行中状態に初期化される', () => {
    const state = started();
    expect(state.phase).toBe('running');
    expect(state.jobId).toBe('job1');
    expect(state.steps.plan.status).toBe('pending');
  });

  it('step イベントで running → done / skipped が反映される', () => {
    let state = started();
    state = apply(
      state,
      { type: 'step', step: 'plan', status: 'started', title: '① Plan' },
    );
    expect(state.steps.plan.status).toBe('running');
    state = apply(
      state,
      { type: 'step', step: 'plan', status: 'finished', data: { steps: 2 } },
      { type: 'step', step: 'web', status: 'skipped', data: { reason: '内部回答で確定' } },
    );
    expect(state.steps.plan.status).toBe('done');
    expect(state.steps.plan.data.steps).toBe(2);
    expect(state.steps.web.status).toBe('skipped');
  });

  it('log イベントはステップ別に蓄積される', () => {
    let state = started();
    state = apply(
      state,
      { type: 'log', step: 'execute', message: 'step1: success' },
      { type: 'log', step: 'execute', message: 'step2: success' },
      { type: 'log', message: '全体ログ' },
    );
    expect(state.steps.execute.logs).toEqual(['step1: success', 'step2: success']);
    expect(state.logs).toEqual(['全体ログ']);
  });

  it('intervention waiting でモーダル用状態になり、resolved で閉じる', () => {
    let state = started();
    state = apply(state, {
      type: 'intervention',
      step: 'action',
      status: 'waiting',
      message: '続行しますか？',
      data: { intervention_id: 'iv1', timeout_seconds: 300 },
    });
    expect(state.intervention?.intervention_id).toBe('iv1');
    expect(state.intervention?.message).toBe('続行しますか？');

    state = apply(state, {
      type: 'intervention',
      step: 'action',
      status: 'resolved',
      data: { intervention_id: 'iv1', action: 'proceed' },
    });
    expect(state.intervention).toBeNull();
  });

  it('result と done で完了状態になる', () => {
    let state = started();
    state = apply(
      state,
      { type: 'result', data: { decision: 'answer', citations: ['[社内] faq.md'] } },
      { type: 'done', status: 'completed' },
    );
    expect(state.phase).toBe('completed');
    expect(state.result?.decision).toBe('answer');
  });

  it('error イベントと done(failed) で失敗状態になる', () => {
    let state = started();
    state = apply(
      state,
      { type: 'error', message: 'ANTHROPIC_API_KEY が未設定です' },
      { type: 'done', status: 'failed' },
    );
    expect(state.phase).toBe('failed');
    expect(state.error).toContain('ANTHROPIC_API_KEY');
  });

  it('リプレイ（同じイベント列の再適用）でも状態が壊れない', () => {
    const events: SupportEvent[] = [
      { type: 'step', step: 'plan', status: 'started' },
      { type: 'step', step: 'plan', status: 'finished' },
      { type: 'result', data: { decision: 'escalate' } },
      { type: 'done', status: 'completed' },
    ];
    const once = apply(started(), ...events);
    const twice = apply(once, ...events);
    expect(twice.steps.plan.status).toBe('done');
    expect(twice.phase).toBe('completed');
  });
});

import { describe, expect, it } from 'vitest';

import {
  dataReducer,
  initialDataState,
  stepIdsFor,
  stepLabelsFor,
  type DataJobState,
} from './dataReducer';
import type { SupportEvent } from '../types';

function started(kind: 'chunking' | 'register' | 'delete' = 'chunking'): DataJobState {
  return dataReducer(initialDataState(kind), { type: 'started', jobId: 'job1', kind });
}

function apply(state: DataJobState, event: SupportEvent): DataJobState {
  return dataReducer(state, { type: 'event', event });
}

describe('stepIdsFor / stepLabelsFor', () => {
  it('ジョブ種別ごとにステップが違う', () => {
    expect(stepIdsFor('chunking')).toEqual(['load', 'chunk', 'save']);
    expect(stepIdsFor('register')).toEqual(['prepare', 'confirm', 'embed', 'upsert']);
    expect(stepIdsFor('delete')).toEqual(['inspect', 'confirm', 'delete']);
  });

  it('全ステップにラベルがある（ラベル漏れがあると空欄で表示される）', () => {
    for (const kind of ['chunking', 'register', 'delete'] as const) {
      const labels = stepLabelsFor(kind);
      for (const id of stepIdsFor(kind)) {
        expect(labels[id], `${kind}.${id} のラベルが無い`).toBeTruthy();
      }
    }
  });
});

describe('initialDataState', () => {
  it('全ステップが pending で始まる', () => {
    const state = initialDataState('register');
    expect(Object.keys(state.steps)).toEqual(['prepare', 'confirm', 'embed', 'upsert']);
    expect(Object.values(state.steps).every((s) => s.status === 'pending')).toBe(true);
    expect(state.phase).toBe('idle');
  });
});

describe('started', () => {
  it('ジョブ ID を保持して running になる', () => {
    const state = started('delete');
    expect(state.jobId).toBe('job1');
    expect(state.kind).toBe('delete');
    expect(state.phase).toBe('running');
    expect(Object.keys(state.steps)).toEqual(['inspect', 'confirm', 'delete']);
  });

  it('再実行で前回の結果が消える', () => {
    let state = started();
    state = apply(state, { type: 'result', data: { kind: 'chunking', chunks: 5 } });
    state = dataReducer(state, { type: 'started', jobId: 'job2', kind: 'chunking' });
    expect(state.result).toBeNull();
    expect(state.jobId).toBe('job2');
  });
});

describe('step イベント', () => {
  it('started は running、finished は done、skipped は skipped', () => {
    let state = started('register');
    state = apply(state, { type: 'step', step: 'prepare', status: 'started' });
    expect(state.steps.prepare.status).toBe('running');

    state = apply(state, { type: 'step', step: 'prepare', status: 'finished' });
    expect(state.steps.prepare.status).toBe('done');

    state = apply(state, { type: 'step', step: 'confirm', status: 'skipped' });
    expect(state.steps.confirm.status).toBe('skipped');
  });

  it('**data はマージではなく置換**（Support / Review と同じ）', () => {
    let state = started('register');
    state = apply(state, {
      type: 'step',
      step: 'prepare',
      status: 'started',
      data: { collection: 'c', recreate: true },
    });
    state = apply(state, {
      type: 'step',
      step: 'prepare',
      status: 'finished',
      data: { exists: false },
    });
    // started 時の collection は残らない
    expect(state.steps.prepare.data).toEqual({ exists: false });
  });

  it('未知のステップ ID は無視する（バックエンドが増やしても壊れない）', () => {
    let state = started('chunking');
    const before = state.steps;
    state = apply(state, { type: 'step', step: 'unknown_step', status: 'started' });
    expect(state.steps).toBe(before);
  });
});

describe('log イベント', () => {
  it('ステップに紐づくログはそのステップへ積む', () => {
    let state = started('chunking');
    state = apply(state, { type: 'log', step: 'chunk', message: '分割中' });
    state = apply(state, { type: 'log', step: 'chunk', message: '結合中' });
    expect(state.steps.chunk.logs).toEqual(['分割中', '結合中']);
  });

  it('**ステップに紐づかないログは捨てずに その他 へ回す**', () => {
    let state = started('chunking');
    state = apply(state, { type: 'log', message: '全体ログ' });
    expect(state.logs).toEqual(['全体ログ']);
  });

  it('未知のステップ ID のログも捨てない', () => {
    let state = started('chunking');
    state = apply(state, { type: 'log', step: 'unknown', message: '迷子のログ' });
    expect(state.logs).toEqual(['迷子のログ']);
  });
});

describe('intervention イベント', () => {
  it('waiting でモーダル用の状態が入る', () => {
    let state = started('delete');
    state = apply(state, {
      type: 'intervention',
      status: 'waiting',
      message: '削除しますか？',
      data: { intervention_id: 'iv1', timeout_seconds: 300 },
    });
    expect(state.intervention?.intervention_id).toBe('iv1');
    expect(state.intervention?.message).toBe('削除しますか？');
  });

  it('confirm_sent でモーダルが閉じる', () => {
    let state = started('delete');
    state = apply(state, {
      type: 'intervention',
      status: 'waiting',
      message: 'x',
      data: { intervention_id: 'iv1' },
    });
    state = dataReducer(state, { type: 'confirm_sent' });
    expect(state.intervention).toBeNull();
  });

  it('waiting 以外の intervention はモーダルを閉じる', () => {
    let state = started('delete');
    state = apply(state, {
      type: 'intervention',
      status: 'waiting',
      message: 'x',
      data: { intervention_id: 'iv1' },
    });
    state = apply(state, { type: 'intervention', status: 'resolved' });
    expect(state.intervention).toBeNull();
  });
});

describe('result / error / done', () => {
  it('result を保持する', () => {
    let state = started('delete');
    state = apply(state, {
      type: 'result',
      data: { kind: 'delete', deleted: ['a'], cancelled: false },
    });
    expect(state.result?.deleted).toEqual(['a']);
  });

  it('error で failed になる', () => {
    let state = started();
    state = apply(state, { type: 'error', message: 'Qdrant に接続できません' });
    expect(state.phase).toBe('failed');
    expect(state.error).toBe('Qdrant に接続できません');
  });

  it('done で completed になる', () => {
    let state = started();
    state = apply(state, { type: 'done' });
    expect(state.phase).toBe('completed');
  });

  it('**error の後の done は failed のまま**（成功に見せない）', () => {
    let state = started();
    state = apply(state, { type: 'error', message: '失敗' });
    state = apply(state, { type: 'done' });
    expect(state.phase).toBe('failed');
  });

  it('done で承認待ちが残らない', () => {
    let state = started('delete');
    state = apply(state, {
      type: 'intervention',
      status: 'waiting',
      message: 'x',
      data: { intervention_id: 'iv1' },
    });
    state = apply(state, { type: 'done' });
    expect(state.intervention).toBeNull();
  });
});

describe('failed / reset', () => {
  it('failed アクションで失敗にする（SSE 切断など）', () => {
    const state = dataReducer(started(), { type: 'failed', message: '切断されました' });
    expect(state.phase).toBe('failed');
    expect(state.error).toBe('切断されました');
  });

  it('reset は種別を保ったまま初期状態へ戻す', () => {
    let state = started('register');
    state = apply(state, { type: 'step', step: 'prepare', status: 'finished' });
    state = dataReducer(state, { type: 'reset' });
    expect(state.phase).toBe('idle');
    expect(state.kind).toBe('register');
    expect(state.steps.prepare.status).toBe('pending');
  });
});

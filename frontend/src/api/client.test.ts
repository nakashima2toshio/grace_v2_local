// subscribeStream の再接続を固定するテスト。
//
// ## 背景（2026-10-08 の実例）
//
// ローカル LLM のチャンク化（約 70 分）で、Step 2 が 38 分間ログを 1 行も出さない間に
// 画面への配信が止まった。バックエンドは最後まで処理して CSV を書き終えていたのに、
// 画面は「② セマンティックチャンク化」の途中のまま動かず、**赤いエラー枠も出なかった**
// （EventSource が `onerror` を出さずに黙って止まった）。
//
// 以前の subscribeStream は
//   - 黙って止まった接続を検知できない（keepalive はコメント行で、JS から見えない）
//   - `onerror` が来ても再接続せず、即エラー表示で終わる
// の 2 点で、バックエンドが持っている「先頭からのリプレイ」を使えていなかった。
//
// node 環境（vitest）に EventSource は無いので、最小の偽物を差し込む。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { SupportEvent } from '../types';
import { subscribeStream } from './client';
import { STREAM_STALL_MS } from '../state/streamWatch';

type Listener = (message: { data: string }) => void;

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;

  readonly url: string;
  readyState = FakeEventSource.OPEN;
  onmessage: Listener | null = null;
  onerror: (() => void) | null = null;
  private listeners = new Map<string, Listener[]>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: Listener): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  close(): void {
    this.readyState = FakeEventSource.CLOSED;
  }

  // --- テストから操作する ---
  emit(event: Record<string, unknown>): void {
    this.onmessage?.({ data: JSON.stringify(event) });
  }

  keepalive(): void {
    for (const listener of this.listeners.get('keepalive') ?? []) listener({ data: '{}' });
  }

  fail(): void {
    this.readyState = FakeEventSource.CONNECTING;
    this.onerror?.();
  }
}

const latest = () => FakeEventSource.instances[FakeEventSource.instances.length - 1];

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.useFakeTimers();
  vi.stubGlobal('EventSource', FakeEventSource);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function collect() {
  const events: SupportEvent[] = [];
  const errors: string[] = [];
  return {
    events,
    errors,
    onEvent: (e: SupportEvent) => events.push(e),
    onError: (m: string) => errors.push(m),
  };
}

describe('subscribeStream の再接続', () => {
  it('黙って止まった接続を検知して張り直し、リプレイ分は二重に渡さない', () => {
    const c = collect();
    subscribeStream('job1', c.onEvent, c.onError, 'data');
    const first = latest();
    first.emit({ seq: 0, type: 'step_started', step: 'load' });
    first.emit({ seq: 1, type: 'log', step: 'chunk', message: '入力: 122 段落' });

    // 何も来ないまま（keepalive も来ない）しきい値を超える
    vi.advanceTimersByTime(STREAM_STALL_MS + 15_000);

    expect(FakeEventSource.instances.length).toBeGreaterThanOrEqual(2);
    const second = latest();
    expect(first.readyState).toBe(FakeEventSource.CLOSED);
    expect(second.url).toBe('/api/data/stream/job1');

    // バックエンドは先頭からリプレイする
    second.emit({ seq: 0, type: 'step_started', step: 'load' });
    second.emit({ seq: 1, type: 'log', step: 'chunk', message: '入力: 122 段落' });
    second.emit({ seq: 2, type: 'step_finished', step: 'chunk' });
    second.emit({ type: 'done', status: 'completed' });

    expect(c.events.map((e) => e.seq ?? e.type)).toEqual([0, 1, 2, 'done']);
    expect(c.errors).toEqual([]);
  });

  it('keepalive が届いている間は張り直さない', () => {
    const c = collect();
    subscribeStream('job1', c.onEvent, c.onError, 'data');
    const only = latest();
    for (let i = 0; i < 20; i += 1) {
      vi.advanceTimersByTime(15_000);
      only.keepalive();
    }
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(c.errors).toEqual([]);
  });

  it('onerror では即エラーにせず張り直し、続きを受け取れる', () => {
    const c = collect();
    subscribeStream('job1', c.onEvent, c.onError, 'review');
    latest().emit({ seq: 0, type: 'step_started', step: 'segment' });
    latest().fail();
    expect(c.errors).toEqual([]);

    vi.advanceTimersByTime(30_000);
    const again = latest();
    expect(FakeEventSource.instances.length).toBe(2);
    again.emit({ seq: 0, type: 'step_started', step: 'segment' });
    again.emit({ seq: 1, type: 'step_finished', step: 'segment' });
    expect(c.events.map((e) => e.seq)).toEqual([0, 1]);
  });

  it('つながらない状態が続けばエラーを 1 回だけ出してあきらめる', () => {
    const c = collect();
    subscribeStream('gone', c.onEvent, c.onError, 'data');
    for (let i = 0; i < 20; i += 1) {
      latest().fail();
      vi.advanceTimersByTime(30_000);
    }
    expect(c.errors).toHaveLength(1);
    expect(c.errors[0]).toContain('進捗ストリームが切断されました');
    const count = FakeEventSource.instances.length;
    vi.advanceTimersByTime(10 * STREAM_STALL_MS);
    expect(FakeEventSource.instances).toHaveLength(count);
  });

  it('done を受けたら閉じて、以後は張り直さない', () => {
    const c = collect();
    subscribeStream('job1', c.onEvent, c.onError, 'support');
    const only = latest();
    only.emit({ seq: 0, type: 'step_started', step: 'plan' });
    only.emit({ type: 'done', status: 'completed' });
    expect(only.readyState).toBe(FakeEventSource.CLOSED);
    vi.advanceTimersByTime(10 * STREAM_STALL_MS);
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(c.errors).toEqual([]);
  });

  it('購読解除のあとは張り直さない', () => {
    const c = collect();
    const unsubscribe = subscribeStream('job1', c.onEvent, c.onError, 'data');
    unsubscribe();
    expect(latest().readyState).toBe(FakeEventSource.CLOSED);
    vi.advanceTimersByTime(10 * STREAM_STALL_MS);
    expect(FakeEventSource.instances).toHaveLength(1);
  });
});

// サーバ権威タイムスタンプの純関数テスト（elapsed.ts の追加分）。
import { describe, expect, it } from 'vitest';

import {
  applyServerEvent,
  elapsedMs,
  EMPTY_TIMING,
  preferServerTiming,
  secondsToMs,
  startTiming,
} from './elapsed';

describe('secondsToMs', () => {
  it('エポック秒をミリ秒へ直す', () => {
    expect(secondsToMs(1_800_000_000)).toBe(1_800_000_000_000);
  });

  it('未定義・null・非有限は null（推測値を作らない）', () => {
    expect(secondsToMs(undefined)).toBeNull();
    expect(secondsToMs(null)).toBeNull();
    expect(secondsToMs(Number.NaN)).toBeNull();
  });
});

describe('applyServerEvent', () => {
  it('最初のイベントの ts を開始時刻にする', () => {
    const next = applyServerEvent(EMPTY_TIMING, { type: 'step', ts: 100 });
    expect(next.startedAt).toBe(100_000);
    expect(next.finishedAt).toBeNull();
  });

  it('2 件目以降では開始時刻を更新しない', () => {
    const first = applyServerEvent(EMPTY_TIMING, { type: 'step', ts: 100 });
    const second = applyServerEvent(first, { type: 'log', ts: 140 });
    expect(second.startedAt).toBe(100_000);
  });

  it('done で完了時刻が入る', () => {
    const started = applyServerEvent(EMPTY_TIMING, { type: 'step', ts: 100 });
    const done = applyServerEvent(started, { type: 'done', ts: 260 });
    expect(done.finishedAt).toBe(260_000);
    expect(elapsedMs(done)).toBe(160_000);
  });

  it('done を 2 回受けても完了時刻は動かない', () => {
    const started = applyServerEvent(EMPTY_TIMING, { type: 'step', ts: 100 });
    const done = applyServerEvent(started, { type: 'done', ts: 260 });
    expect(applyServerEvent(done, { type: 'done', ts: 999 })).toBe(done);
  });

  it('ts が無いイベントは同じ参照を返す（再レンダーを起こさない）', () => {
    expect(applyServerEvent(EMPTY_TIMING, { type: 'step' })).toBe(EMPTY_TIMING);
  });

  it('再購読（リプレイ）でも開始・完了の両方が復元される', () => {
    // タブを離れて戻った経路: ブラウザ側は送信の瞬間を見ていない。
    const replay = [
      { type: 'step', ts: 1000 },
      { type: 'log', ts: 1020 },
      { type: 'result', ts: 4600 },
      { type: 'done', ts: 4600 },
    ];
    const timing = replay.reduce(applyServerEvent, EMPTY_TIMING);
    expect(elapsedMs(timing)).toBe(3_600_000);
  });
});

describe('preferServerTiming', () => {
  it('サーバ側が取れていればサーバの組を丸ごと使う', () => {
    const server = { startedAt: 1000, finishedAt: 5000 };
    const client = { startedAt: 900, finishedAt: 5200 };
    expect(preferServerTiming(server, client)).toBe(server);
  });

  it('サーバ側が無ければブラウザの組を使う', () => {
    const client = startTiming(1234);
    expect(preferServerTiming(EMPTY_TIMING, client)).toBe(client);
  });

  it('時計を混ぜない（サーバ開始 × ブラウザ完了にしない）', () => {
    const server = { startedAt: 1000, finishedAt: null };
    const client = { startedAt: 900, finishedAt: 5200 };
    // サーバ側の開始が取れているので完了未確定でもサーバの組。
    // 混ぜると 2 つの時計の差がそのまま所要時間の誤差になる。
    expect(preferServerTiming(server, client).finishedAt).toBeNull();
  });
});

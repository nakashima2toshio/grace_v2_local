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

  it('実行中（両方とも完了未確定）はサーバ組', () => {
    const server = { startedAt: 1000, finishedAt: null };
    const client = { startedAt: 900, finishedAt: null };
    // 時計を混ぜない: 開始がサーバで取れているならサーバの組を丸ごと使う。
    expect(preferServerTiming(server, client)).toBe(server);
  });

  it('サーバだけ完了を知らないならブラウザ組へ倒す', () => {
    const server = { startedAt: 1000, finishedAt: null };
    const client = { startedAt: 900, finishedAt: 5200 };
    // ⚠️ ここでサーバ組を返すと完了行が消える（2026-08-29 の回帰）。
    // 時計の差より「決着したのに完了時刻が出ない」ほうが害が大きい。
    expect(preferServerTiming(server, client)).toBe(client);
  });
});

// ===========================================================================
// 回帰: 完了行が消えないこと（2026-08-29）
//
// 終端イベントが時刻を持たなかったため、サーバ側の完了時刻が永久に埋まらず、
// `preferServerTiming` がサーバ組を返して「完了 … ／ 所要 …」がまるごと
// 消えていた。バックエンド（`jobs.done_event`）とフロントの両方で塞ぐ。
// ===========================================================================

describe('done イベントの時刻', () => {
  it('started_at で開始が受付時刻へ上書きされる', () => {
    // 最初のイベント（1100）は暫定。受付は 1000 で、その差が初期化時間。
    const provisional = applyServerEvent(EMPTY_TIMING, { type: 'step', ts: 1100 });
    expect(provisional.startedAt).toBe(1_100_000);

    const done = applyServerEvent(provisional, {
      type: 'done',
      ts: 1160,
      started_at: 1000,
    });
    expect(done.startedAt).toBe(1_000_000);
    expect(done.finishedAt).toBe(1_160_000);
    // 初期化の 100 秒を落とさない
    expect(elapsedMs(done)).toBe(160_000);
  });

  it('done に ts があれば完了時刻が必ず入る', () => {
    const timing = applyServerEvent(EMPTY_TIMING, {
      type: 'done',
      ts: 500,
      started_at: 440,
    });
    expect(timing.finishedAt).toBe(500_000);
  });

  it('時刻を持たない done でも完了行を消さない（旧バックエンド互換）', () => {
    // サーバ側は開始だけ判っていて完了は判らない。ブラウザ側は決着を知っている。
    const server = applyServerEvent(EMPTY_TIMING, { type: 'step', ts: 100 });
    const client = { startedAt: 99_000, finishedAt: 260_000 };
    const shown = preferServerTiming(server, client);
    expect(shown.finishedAt).not.toBeNull();
    expect(elapsedMs(shown)).not.toBeNull();
  });

  it('両方が完了を知っていればサーバ組（時計を混ぜない）', () => {
    const server = { startedAt: 100_000, finishedAt: 260_000 };
    const client = { startedAt: 99_000, finishedAt: 261_000 };
    expect(preferServerTiming(server, client)).toBe(server);
  });
});

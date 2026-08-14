import { describe, expect, it } from 'vitest';

import {
  EMPTY_TIMING,
  elapsedMs,
  finishTiming,
  formatClock,
  formatDuration,
  startTiming,
} from './elapsed';

describe('startTiming', () => {
  it('開始時刻を記録する', () => {
    expect(startTiming(1000)).toEqual({ startedAt: 1000, finishedAt: null });
  });

  it('**前回の完了時刻を消す**（再実行で前回の完了が残らない）', () => {
    // 直前の実行が完了している状態で再送信しても、開始だけの状態に戻る
    expect(startTiming(9000)).toEqual({ startedAt: 9000, finishedAt: null });
  });
});

describe('finishTiming', () => {
  it('完了時刻を入れる', () => {
    expect(finishTiming({ startedAt: 1000, finishedAt: null }, 4000)).toEqual({
      startedAt: 1000,
      finishedAt: 4000,
    });
  });

  it('**既に確定していれば据え置く**（完了時刻が後ろへずれない）', () => {
    const done = { startedAt: 1000, finishedAt: 4000 };
    expect(finishTiming(done, 9999)).toEqual(done);
  });

  it('**据え置くときは同じ参照を返す**（setState が再レンダーを起こさない）', () => {
    const done = { startedAt: 1000, finishedAt: 4000 };
    expect(finishTiming(done, 9999)).toBe(done);
  });

  it('開始していなければ何もしない（再購読で開始時刻が不明なケース）', () => {
    expect(finishTiming(EMPTY_TIMING, 4000)).toBe(EMPTY_TIMING);
  });
});

describe('formatClock', () => {
  it('年月日と時分秒をゼロ詰めで並べる', () => {
    // ローカル時刻で組み立てるので、ローカル時刻で期待値を作る
    const d = new Date(2026, 7, 13, 10, 24, 35); // 2026-08-13 10:24:35
    expect(formatClock(d.getTime())).toBe('2026-08-13 10:24:35');
  });

  it('1 桁の月日時分秒を 2 桁にする', () => {
    const d = new Date(2026, 0, 5, 9, 8, 7); // 2026-01-05 09:08:07
    expect(formatClock(d.getTime())).toBe('2026-01-05 09:08:07');
  });

  it('真夜中も 00:00:00 として出す', () => {
    const d = new Date(2026, 11, 31, 0, 0, 0);
    expect(formatClock(d.getTime())).toBe('2026-12-31 00:00:00');
  });
});

describe('formatDuration', () => {
  it('秒だけの短い実行', () => {
    expect(formatDuration(7_000)).toBe('00:00:07');
  });

  it('分と秒', () => {
    expect(formatDuration(83_000)).toBe('00:01:23');
  });

  it('時・分・秒', () => {
    expect(formatDuration(3_723_000)).toBe('01:02:03');
  });

  it('**秒未満は切り捨てる**（1.9 秒は 1 秒）', () => {
    expect(formatDuration(1_900)).toBe('00:00:01');
  });

  it('0 は 00:00:00', () => {
    expect(formatDuration(0)).toBe('00:00:00');
  });

  it('**負の値は 00:00:00 に倒す**（端末の時刻が巻き戻った場合の保険）', () => {
    expect(formatDuration(-5_000)).toBe('00:00:00');
  });

  it('100 時間を超えても時の桁を切らない', () => {
    expect(formatDuration(100 * 3600 * 1000)).toBe('100:00:00');
  });
});

describe('elapsedMs', () => {
  it('両端が揃っていれば差を返す', () => {
    expect(elapsedMs({ startedAt: 1_000, finishedAt: 4_500 })).toBe(3_500);
  });

  it('**開始が不明なら null**（所要時間を出さない）', () => {
    expect(elapsedMs({ startedAt: null, finishedAt: 4_500 })).toBeNull();
  });

  it('未完了なら null', () => {
    expect(elapsedMs({ startedAt: 1_000, finishedAt: null })).toBeNull();
  });

  it('未実行なら null', () => {
    expect(elapsedMs(EMPTY_TIMING)).toBeNull();
  });
});

describe('開始 → 完了の一連の流れ', () => {
  it('送信 → 決着 で所要時間が出る', () => {
    const started = startTiming(1_000);
    expect(elapsedMs(started)).toBeNull(); // 実行中は出さない

    const finished = finishTiming(started, 84_000);
    expect(elapsedMs(finished)).toBe(83_000);
    expect(formatDuration(elapsedMs(finished)!)).toBe('00:01:23');
  });

  it('再送信すると前回の完了時刻が消え、所要時間も出なくなる', () => {
    const finished = finishTiming(startTiming(1_000), 84_000);
    const again = startTiming(200_000);

    expect(again.finishedAt).toBeNull();
    expect(elapsedMs(again)).toBeNull();
    expect(finished.finishedAt).toBe(84_000); // 元の値は壊さない（純関数）
  });
});

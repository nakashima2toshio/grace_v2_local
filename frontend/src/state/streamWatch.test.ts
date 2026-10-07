import { describe, expect, it } from 'vitest';

import {
  isReplayedEvent,
  isStreamStalled,
  retryDelayMs,
  STREAM_CHECK_INTERVAL_MS,
  STREAM_STALL_MS,
} from './streamWatch';

describe('isStreamStalled', () => {
  it('しきい値ちょうどまでは止まっていない、超えたら止まっている', () => {
    expect(isStreamStalled(0, STREAM_STALL_MS)).toBe(false);
    expect(isStreamStalled(0, STREAM_STALL_MS + 1)).toBe(true);
  });

  it('しきい値はバックエンドの keepalive（15 秒）を何回か取りこぼしても誤判定しない長さ', () => {
    expect(STREAM_STALL_MS).toBeGreaterThanOrEqual(3 * 15_000);
    expect(STREAM_CHECK_INTERVAL_MS).toBeLessThan(STREAM_STALL_MS);
  });

  it('しきい値を引数で変えられる', () => {
    expect(isStreamStalled(1_000, 3_000, 1_000)).toBe(true);
    expect(isStreamStalled(1_000, 1_500, 1_000)).toBe(false);
  });
});

describe('isReplayedEvent', () => {
  it('まだ何も渡していなければ seq 0 から渡す', () => {
    expect(isReplayedEvent(0, -1)).toBe(false);
  });

  it('渡した番号以下はリプレイ分として捨てる', () => {
    expect(isReplayedEvent(3, 5)).toBe(true);
    expect(isReplayedEvent(5, 5)).toBe(true);
    expect(isReplayedEvent(6, 5)).toBe(false);
  });

  it('seq の無いイベント（終端の done）は常に渡す', () => {
    expect(isReplayedEvent(undefined, 100)).toBe(false);
  });
});

describe('retryDelayMs', () => {
  it('1, 2, 4, 8 秒と延ばし、10 秒で頭打ち', () => {
    expect([0, 1, 2, 3, 4, 10].map(retryDelayMs)).toEqual([
      1_000, 2_000, 4_000, 8_000, 10_000, 10_000,
    ]);
  });

  it('負の回数は 1 秒として扱う', () => {
    expect(retryDelayMs(-1)).toBe(1_000);
  });
});

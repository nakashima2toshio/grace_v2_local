// ジョブの開始・完了時刻を保持するフック。4 つのタブすべてで共用する。
//
// **判断と整形は `elapsed.ts` の純関数が持つ**。ここがやるのは
// 「`Date.now()` を呼ぶ」「`useState` に載せる」「phase を見て決着を検知する」の 3 つだけで、
// 分岐らしい分岐を持たない（＝ここにテストすべきロジックを置かない）。
import { useCallback, useEffect, useState } from 'react';

import {
  applyServerEvent,
  EMPTY_TIMING,
  finishTiming,
  preferServerTiming,
  type ServerTimedEvent,
  startTiming,
  type JobTiming,
} from './elapsed';

/** 決着した状態。3 つの reducer で phase の型名は違うが、値はこの 2 つで共通。 */
const SETTLED = new Set(['completed', 'failed']);

/**
 * @param phase 監視対象の reducer の phase（`idle` / `running` / `completed` / `failed`）
 * @returns `[timing, begin, observe]` — `begin()` を送信時に、`observe(event)` を
 *   SSE イベント受信のたびに呼ぶ。完了の記録は自動。
 *
 * ブラウザ時計（`begin` / phase 監視）とサーバ時計（`observe`）の 2 組を別々に
 * 持ち、**サーバ側が取れていればそちらを丸ごと使う**（`preferServerTiming`）。
 * 再購読・リロードでブラウザ側の開始時刻を失っても所要時間が出せる。
 */
export function useJobTiming(
  phase: string,
): [JobTiming, () => void, (event: ServerTimedEvent) => void] {
  const [timing, setTiming] = useState<JobTiming>(EMPTY_TIMING);
  const [serverTiming, setServerTiming] = useState<JobTiming>(EMPTY_TIMING);

  // 決着したら 1 度だけ完了時刻を入れる。
  // finishTiming が二重確定を弾き、据え置き時は同じ参照を返すので、
  // この effect が何度走っても完了時刻は動かない。
  useEffect(() => {
    if (!SETTLED.has(phase)) return;
    setTiming((prev) => finishTiming(prev, Date.now()));
  }, [phase]);

  const begin = useCallback(() => {
    setTiming(startTiming(Date.now()));
    setServerTiming(EMPTY_TIMING);
  }, []);

  // SSE イベントごとに呼ぶ。ts を持たないイベントでは applyServerEvent が
  // 同じ参照を返すため、再レンダーは起きない。
  const observe = useCallback((event: ServerTimedEvent) => {
    setServerTiming((prev) => applyServerEvent(prev, event));
  }, []);

  return [preferServerTiming(serverTiming, timing), begin, observe];
}

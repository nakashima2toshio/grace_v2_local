// ジョブの開始・完了時刻を保持するフック。4 つのタブすべてで共用する。
//
// **判断と整形は `elapsed.ts` の純関数が持つ**。ここがやるのは
// 「`Date.now()` を呼ぶ」「`useState` に載せる」「phase を見て決着を検知する」の 3 つだけで、
// 分岐らしい分岐を持たない（＝ここにテストすべきロジックを置かない）。
import { useCallback, useEffect, useState } from 'react';

import { EMPTY_TIMING, finishTiming, startTiming, type JobTiming } from './elapsed';

/** 決着した状態。3 つの reducer で phase の型名は違うが、値はこの 2 つで共通。 */
const SETTLED = new Set(['completed', 'failed']);

/**
 * @param phase 監視対象の reducer の phase（`idle` / `running` / `completed` / `failed`）
 * @returns `[timing, begin]` — `begin()` を送信時に呼ぶ。完了の記録は自動
 */
export function useJobTiming(phase: string): [JobTiming, () => void] {
  const [timing, setTiming] = useState<JobTiming>(EMPTY_TIMING);

  // 決着したら 1 度だけ完了時刻を入れる。
  // finishTiming が二重確定を弾き、据え置き時は同じ参照を返すので、
  // この effect が何度走っても完了時刻は動かない。
  useEffect(() => {
    if (!SETTLED.has(phase)) return;
    setTiming((prev) => finishTiming(prev, Date.now()));
  }, [phase]);

  const begin = useCallback(() => setTiming(startTiming(Date.now())), []);

  return [timing, begin];
}

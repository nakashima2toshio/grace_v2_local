// ジョブの開始・完了時刻と所要時間を扱う純関数。
//
// ## 何のためにあるか
//
// 「送信してから答えが出るまでどれくらい掛かったのか」は、Web 裏取りの有無や
// 業界プロファイルの違いを比べるときに効いてくる。従来はどこにも出ておらず、
// 体感でしか分からなかった。
//
// ## なぜ reducer に持たせないのか
//
// `jobReducer` / `reviewReducer` / `dataReducer` は**純関数**である
// （同じ入力なら同じ出力・副作用ゼロ）。時刻の取得は `Date.now()` という
// **呼ぶたびに違う値を返す副作用**なので、reducer の中で呼ぶと純粋性が壊れる。
// StrictMode は reducer を 2 回呼ぶため、開発時と本番で値が変わることにもなる。
//
// そこで時刻は reducer の外（パネルの `useState`）で持ち、
// **判断と整形だけをこのファイルの純関数へ**出している。
// `Date.now()` を呼ぶのは呼び出し側の 1 箇所だけ（`useJobTiming.ts`）。

/** 1 回の実行の開始・完了時刻（エポックミリ秒）。未確定は null。 */
export interface JobTiming {
  startedAt: number | null;
  finishedAt: number | null;
}

/** 未実行の状態。 */
export const EMPTY_TIMING: JobTiming = { startedAt: null, finishedAt: null };

/**
 * 送信時に呼ぶ。開始時刻を記録し、**前回の完了時刻を消す**。
 * 消さないと、再実行中に前回の「完了」が残って混乱する。
 */
export function startTiming(now: number): JobTiming {
  return { startedAt: now, finishedAt: null };
}

/**
 * 決着（completed / failed）したときに呼ぶ。
 *
 * - 開始していない（`startedAt === null`）なら何もしない。
 *   タブを離れて戻ったときの再購読など、開始時刻を知らない経路がある。
 * - **既に確定していれば据え置き、同じ参照を返す。**
 *   phase を監視する `useEffect` は再レンダーで複数回走りうるため、
 *   ここで弾かないと完了時刻が後ろへずれていく。同一参照なので
 *   `setState` は再レンダーを起こさない。
 */
export function finishTiming(prev: JobTiming, now: number): JobTiming {
  if (prev.startedAt === null) return prev;
  if (prev.finishedAt !== null) return prev;
  return { ...prev, finishedAt: now };
}

/** 2 桁ゼロ詰め。 */
function pad2(value: number): string {
  return String(value).padStart(2, '0');
}

/**
 * 時刻を `2026-08-13 10:24:35` の形にする（**ローカル時刻**）。
 *
 * `toLocaleString()` を使わないのは、実行環境のロケール設定で書式が変わり
 * テストが安定しないため。桁を自分で組み立てて固定する。
 */
export function formatClock(ms: number): string {
  const d = new Date(ms);
  const date = `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
  return `${date} ${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}

/**
 * 所要時間を `00:01:23`（時:分:秒）の形にする。
 *
 * - 秒未満は切り捨てる
 * - **負の値は 00:00:00 に倒す**（端末の時刻が巻き戻った場合の保険）
 * - 100 時間を超えたら時の桁が 3 桁になる（頭を切らない）
 */
export function formatDuration(ms: number): string {
  const totalSec = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(totalSec / 3600);
  const minutes = Math.floor((totalSec % 3600) / 60);
  const seconds = totalSec % 60;
  return `${pad2(hours)}:${pad2(minutes)}:${pad2(seconds)}`;
}

/**
 * 所要ミリ秒。**両端が揃っていなければ null**（＝所要時間は出さない）。
 *
 * 再購読で開始時刻が分からない場合に「完了時刻だけ出して所要は伏せる」
 * という表示ができるようにしている。推測値を出すより無い方がよい。
 */
export function elapsedMs(timing: JobTiming): number | null {
  if (timing.startedAt === null || timing.finishedAt === null) return null;
  return timing.finishedAt - timing.startedAt;
}

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

// ===========================================================================
// サーバ権威のタイムスタンプ
//
// 上の `startTiming` / `finishTiming` はブラウザの `Date.now()` を使う。
// これには 2 つの穴がある。
//
//   1. **再購読では開始時刻が取れない。** タブを離れて戻る経路（`activeJobs`）
//      では送信の瞬間を見ていないため、`startedAt` が null のままになり、
//      完了しても所要時間を出せない（`elapsedMs` が null）。
//      ローカル LLM は 1 周が長く、この経路を踏む機会が多い。
//   2. **リロードで失われる。** 実行中にページを再読み込みすると同様。
//
// SSE のイベントは 1 件ごとに**サーバ時計の `ts`（エポック秒）**を持ち、
// 再購読時は先頭からリプレイされる（`backend/app/core/jobs.py::stream_events`）。
// そこからサーバ側の開始・完了時刻を組み立て、あればそちらを正とする。
// ===========================================================================

/** `applyServerEvent` が見るイベントの最小形（React・API の型に依存させない）。 */
export interface ServerTimedEvent {
  type: string;
  /** イベントの発生時刻（エポック秒）。`done` では実行の完了時刻。 */
  ts?: number;
  /**
   * `done` イベントだけが持つ、ジョブの**受付時刻**（エポック秒）。
   *
   * ⚠️ 最初のイベントの `ts` を開始時刻の代用にしてはいけない（暫定値としてのみ使う）。
   * 受付から最初のイベントまでに、ツール・planner・executor の生成で十数秒かかる。
   * そこを落とすと、利用者が実際に待った時間より短く見える。
   */
  started_at?: number | null;
}

/** エポック秒（サーバの `ts`）→ ミリ秒。無ければ null。 */
export function secondsToMs(ts?: number | null): number | null {
  if (ts === undefined || ts === null || !Number.isFinite(ts)) return null;
  return ts * 1000;
}

/**
 * サーバのイベント時刻を timing へ畳み込む。
 *
 * - 開始時刻は、まず**最初に見たイベント**の ts を暫定で置き、`done` の
 *   `started_at`（受付時刻）が来たらそれで**上書きする**。実行中は暫定値しか
 *   無いが、決着すれば必ず受付時刻に揃う。
 * - 完了時刻は `done` の ts（以降は更新しない）
 * - 読める時刻が 1 つも無いイベントでは**同じ参照を返す**ので、
 *   `setState` が余計な再レンダーを起こさない
 */
export function applyServerEvent(prev: JobTiming, event: ServerTimedEvent): JobTiming {
  const ms = secondsToMs(event.ts);
  const acceptedAt = event.type === 'done' ? secondsToMs(event.started_at) : null;
  if (ms === null && acceptedAt === null) return prev;

  // 受付時刻が最優先。次に暫定（最初のイベント）。既に確定していれば据え置き。
  let startedAt = prev.startedAt;
  if (acceptedAt !== null) {
    startedAt = acceptedAt;
  } else if (startedAt === null) {
    startedAt = ms;
  }
  const finishedAt =
    event.type === 'done' && prev.finishedAt === null && ms !== null
      ? ms
      : prev.finishedAt;

  if (startedAt === prev.startedAt && finishedAt === prev.finishedAt) return prev;
  return { startedAt, finishedAt };
}

/**
 * 表示に使う timing を選ぶ。
 *
 * ⚠️ **片方ずつ混ぜない。** サーバの完了時刻とブラウザの開始時刻を引き算すると、
 * 2 つの時計の差がそのまま所要時間の誤差になる。サーバ側の開始時刻が取れて
 * いるならサーバの組を丸ごと使い、取れていなければブラウザの組を丸ごと使う。
 */
export function preferServerTiming(server: JobTiming, client: JobTiming): JobTiming {
  if (server.startedAt === null) return client;
  // ⚠️ **サーバ側が完了を知らないのに、ブラウザ側が知っている場合はブラウザの組を使う。**
  // ここでサーバ組を返すと「完了 … ／ 所要 …」の行がまるごと消える。実測 2026-08-29 に
  // これが起きた（終端イベントが時刻を持っておらず、サーバ側の完了が永久に埋まらなかった）。
  // 決着した実行の完了時刻を出さないより、2 つの時計のわずかな差のほうが害が小さい。
  if (server.finishedAt === null && client.finishedAt !== null) return client;
  return server;
}

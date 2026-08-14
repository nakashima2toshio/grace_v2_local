// 実行の開始時刻・完了時刻・所要時間を出す 2 つの行。4 つのタブで共用する。
//
//   JobStartLine  — 送信直後から出る。「開始 2026-08-13 10:24:35」
//   JobFinishLine — 決着後に結果の末尾へ出る。「完了 … ／ 所要 00:01:23」
//
// どちらも**出せない情報は黙って省く**。とくに所要時間は、タブを離れて戻った
// 再購読のように開始時刻を知らない経路があるため、`elapsedMs` が null なら
// 完了時刻だけを出す（推測値を出さない）。
import { elapsedMs, formatClock, formatDuration, type JobTiming } from '../state/elapsed';

/** `<time>` の machine-readable 属性。読み上げと将来の集計のために付ける。 */
function isoOf(ms: number): string {
  return new Date(ms).toISOString();
}

/** 送信した時刻。実行中も完了後も出したままにする（比較の起点になるため）。 */
export function JobStartLine({ timing }: { timing: JobTiming }) {
  if (timing.startedAt === null) return null;
  return (
    <p className="job-clock job-clock-start">
      <span className="job-clock-label">開始</span>
      <time className="job-clock-value" dateTime={isoOf(timing.startedAt)}>
        {formatClock(timing.startedAt)}
      </time>
    </p>
  );
}

/** 完了した時刻と所要時間。結果の一番下に置く。 */
export function JobFinishLine({ timing }: { timing: JobTiming }) {
  if (timing.finishedAt === null) return null;
  const ms = elapsedMs(timing);
  return (
    <p className="job-clock job-clock-finish">
      <span className="job-clock-label">完了</span>
      <time className="job-clock-value" dateTime={isoOf(timing.finishedAt)}>
        {formatClock(timing.finishedAt)}
      </time>
      {ms !== null && (
        <>
          <span className="job-clock-label">所要</span>
          <span className="job-clock-value job-clock-elapsed">{formatDuration(ms)}</span>
        </>
      )}
    </p>
  );
}

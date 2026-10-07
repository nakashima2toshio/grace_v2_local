// 進捗ストリーム（SSE）を見張って張り直すための判断を集めた純関数群。
//
// `api/client.ts::subscribeStream` が使う。タイマーと EventSource の生成は
// subscribeStream 側に残し、「いつ張り直すか」「どのイベントを捨てるか」を
// ここで決める（CLAUDE.md §6: 判断は state/ の純関数へ）。
//
// ## なぜ張り直しが要るか（2026-10-08 の実例）
//
// ローカル LLM のチャンク化（約 70 分）で、Step 2 が 38 分間ログを出さない間に
// 画面への配信が止まった。バックエンドは最後まで処理していたのに、画面は途中のまま
// 動かず、`onerror` も来なかった（赤いエラー枠が出ない）。タブやディスプレイが
// 長く裏に回ると、ブラウザ・OS が接続を黙って止めることがある。
//
// バックエンドの `Job.stream_events()` は**つなぎ直すと先頭からリプレイする**。
// だから「止まったら張り直す」「リプレイ分は `seq` で読み飛ばす」だけで、
// 各パネルの reducer を変えずに続きから表示できる。

/**
 * これだけ何も届かなければ「止まった」とみなして張り直す（ミリ秒）。
 *
 * バックエンドは新イベントが無い間も 15 秒ごとに keepalive を送る
 * （`backend/app/core/jobs.py::SSE_KEEPALIVE`）。4 回ぶん届かなければ異常。
 * ⚠️ keepalive は JS から見える**名前付きイベント**でなければならない。
 * コメント行（`: keepalive`）は EventSource が捨てるので、生きている接続まで
 * 「止まった」と誤判定し、1 分ごとに張り直すことになる。
 */
export const STREAM_STALL_MS = 60_000;

/** 何も受け取れないまま張り直しに失敗し続けたら、この回数であきらめてエラーを出す。 */
export const STREAM_MAX_RETRIES = 5;

/** 止まっていないかを確かめる間隔（ミリ秒）。 */
export const STREAM_CHECK_INTERVAL_MS = 10_000;

/** 最後に何か（イベント・keepalive）が届いてから `stallMs` を超えたか。 */
export function isStreamStalled(
  lastActivityMs: number,
  nowMs: number,
  stallMs: number = STREAM_STALL_MS,
): boolean {
  return nowMs - lastActivityMs > stallMs;
}

/**
 * 張り直したときのリプレイで、**すでに渡したイベント**か。
 *
 * イベントには `Job.emit()` が振った通し番号 `seq`（0 始まり）がある。
 * 終端の `done` には `seq` が無く、常に渡す（受けたら購読を閉じるので二重にならない）。
 *
 * @param lastSeq これまでに渡した最大の `seq`。まだ何も渡していなければ -1
 */
export function isReplayedEvent(seq: number | undefined, lastSeq: number): boolean {
  return typeof seq === 'number' && seq <= lastSeq;
}

/** 張り直すまでの待ち時間（ミリ秒）。1, 2, 4, 8 秒…と延ばし、10 秒で頭打ち。 */
export function retryDelayMs(attempt: number): number {
  return Math.min(10_000, 1_000 * 2 ** Math.max(0, attempt));
}

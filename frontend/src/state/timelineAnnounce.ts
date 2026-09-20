// タイムラインの進捗を、支援技術へ読み上げる 1 行にまとめる純関数。
//
// `<ol>` 全体に `aria-live` を張るとログ 1 行ごとに読み上げが走って実用にならない。
// **いま動いているステップ名だけ**を伝える小さなライブ領域に流すため、
// 何を読ませるかの判断をここに切り出してテスト可能にしている。

/** `TimelineStep` のうち、アナウンス判定に要る部分だけ。 */
export interface AnnounceableStep {
  status: 'pending' | 'running' | 'done' | 'skipped';
}

/**
 * 読み上げる文言を決める。
 *
 * - 走っているステップがあれば「実行中: {ラベル}」
 * - 全ステップが決着（done / skipped）していれば「{タイトル}が完了しました」
 * - まだ何も始まっていなければ空文字（＝読み上げない）
 *
 * @param stepIds 描画順のステップ ID
 * @param steps ID → ステップ状態
 * @param labels ID → 表示名
 * @param title 節見出し（完了時の文言に使う）
 */
export function timelineAnnouncement(
  stepIds: readonly string[],
  steps: Record<string, AnnounceableStep>,
  labels: Record<string, string>,
  title: string,
): string {
  const runningId = stepIds.find((id) => steps[id]?.status === 'running');
  if (runningId) {
    // ラベル未定義でも ID を出す（無言になるより手掛かりを残す）
    return `実行中: ${labels[runningId] ?? runningId}`;
  }

  if (stepIds.length === 0) return '';

  const allSettled = stepIds.every((id) => {
    const status = steps[id]?.status;
    return status === 'done' || status === 'skipped';
  });
  return allSettled ? `${title}が完了しました` : '';
}

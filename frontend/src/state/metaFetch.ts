// メタ情報（業界プロファイル / ルールセット）取得の失敗を、対処可能な文言へ変換する純関数。
//
// ## なぜ必要か
//
// これらのセレクタは `.catch(() => setState([]))` でエラーを**完全に握りつぶして**いた。
// バックエンド（:8000）が起動していないと、画面には
//
//   業界プロファイル: [（なし）]        ← 選択肢が 1 つも無い
//   ルールセット:     []                ← 空
//
// としか出ず、**なぜ選べないのかがユーザーに一切伝わらない**。
// 「機能が壊れている」と誤解される典型的な silent failure だった。
//
// 空配列に倒すこと自体は正しい（古い選択肢を残すより安全）。
// 足りなかったのは「なぜ空なのか」を伝えることである。

/** 取得対象の表示名。エラー文言に埋め込む。 */
export type MetaKind = '業界プロファイル' | 'ルールセット';

/**
 * バックエンドへ到達できていない可能性が高いエラーか。
 *
 * - Vite の dev プロキシは、転送先へ繋げないとき **500** を返す（ECONNREFUSED）
 * - プロキシを経由しない構成では `fetch` 自体が `TypeError` で落ちる
 *
 * どちらも「サーバ側の論理エラー」ではなく「そもそも届いていない」に該当する。
 * ただし 500 は本物のサーバエラーでもありうるので、**断定はしない**
 * （文言は「確認してください」に留める）。
 */
export function looksUnreachable(error: unknown): boolean {
  if (error instanceof TypeError) return true; // Failed to fetch
  const message = error instanceof Error ? error.message : String(error);
  return message.includes('(500)') || message.includes('(502)') || message.includes('(504)');
}

/**
 * 取得失敗を、次にやることが分かる 1 文へ変換する。
 *
 * @param error catch で受け取った値（Error とは限らない）
 * @param kind 取得しようとしていたもの
 */
export function metaErrorMessage(error: unknown, kind: MetaKind): string {
  const detail = error instanceof Error ? error.message : String(error);
  if (looksUnreachable(error)) {
    return (
      `${kind}を取得できませんでした。バックエンド（http://localhost:8000）が` +
      `起動しているか確認してください。` +
      `リポジトリルートで ./run_dev.sh を実行すると backend と frontend が同時に起動します。` +
      `（詳細: ${detail}）`
    );
  }
  return `${kind}を取得できませんでした。（詳細: ${detail}）`;
}

// 結果欄が「いま入力欄にある文書」の結果かどうかの判定（GRACE-Review）。
//
// 実測 2026-10-03: サンプルのボタン（「OK 例」など）は入力欄の文書を差し替えるだけで
// 実行はしない。結果欄には前回（表記漏れLP案）の結果が残ったままになり、
// 「OK 例が NG になった」と読み違えられた。開始時刻・文字数・タイトルを突き合わせない
// と気づけない。結果が古いことを画面に出す。

/** 結果が古いときに出す文言（支援技術にも読ませる）。 */
export const STALE_RESULT_NOTICE =
  'この結果は前にチェックした文書のものです。入力欄の文書はまだチェックしていません。' +
  '「表示チェックを実行」を押してください。';

/**
 * 結果が入力欄の文書と食い違っているか。
 *
 * @param checkedDocument 結果を出した文書（送信した文書）。未送信なら空文字
 * @param draftDocument   いま入力欄にある文書。まだ受け取っていなければ null
 * @param running         実行中か（実行中は結果をこれから出すので古いとは言わない）
 */
export function isResultStale(
  checkedDocument: string,
  draftDocument: string | null,
  running: boolean,
): boolean {
  if (running || draftDocument === null || !checkedDocument) return false;
  // 前後の空白だけの違いは同じ文書とみなす（送信時に変えていないので念のため）
  return draftDocument.trim() !== checkedDocument.trim();
}

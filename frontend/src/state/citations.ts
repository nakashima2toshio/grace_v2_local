// 出典文字列の解析。表示（AnswerCard）から切り出した純関数。
//
// バックエンド（backend/app/core/gates.py）が作る形式:
//
//   Web : "[Web] 東京の天気（https://weathernews.jp/onebox/tenki/tokyo/）"
//   Web : "[Web] 東京の天気"                     ← URL が取れなかった場合
//   社内: "[社内] qa_pairs_combined_chunks.csv"
//
// ⚠️ ここを純関数にしているのは、`vite.config.ts` の vitest 設定が
// `include: ['src/**/*.test.ts']` で **`.test.tsx` を収集しない**ため。
// コンポーネントのレンダリングテストが書けないので、解析ロジックだけを
// テスト可能な形で外へ出す。

/** 解析済みの出典 1 件。 */
export interface ParsedCitation {
  /** Web 検索由来か、社内ナレッジ由来か。 */
  kind: 'web' | 'internal';
  /** 表示用のラベル（タイトル or ファイル名）。URL は含まない。 */
  label: string;
  /** リンク先。取り出せなければ null（この場合はラベルだけ出す）。 */
  url: string | null;
}

// 末尾の「（https://…）」を URL として取り出す。全角・半角の括弧に対応する。
const TRAILING_URL_RE = /[（(]\s*(https?:\/\/[^\s）)]+)\s*[）)]\s*$/;

/**
 * 出典 1 件を解析する。
 *
 * URL は**末尾の括弧から取り出す**。本文中の URL を拾わないのは、
 * タイトル自体に URL が含まれる場合に誤ってリンク化しないため。
 */
export function parseCitation(text: string): ParsedCitation {
  const trimmed = text.trim();
  const kind: ParsedCitation['kind'] = trimmed.startsWith('[Web]') ? 'web' : 'internal';
  const body = trimmed.replace(/^\[(Web|社内)\]\s*/, '');

  const match = body.match(TRAILING_URL_RE);
  if (match) {
    const label = body.slice(0, match.index).trim();
    // ラベルが空（＝URL しか無い）なら URL 自体を表示に使う
    return { kind, label: label || match[1], url: match[1] };
  }
  return { kind, label: body, url: null };
}

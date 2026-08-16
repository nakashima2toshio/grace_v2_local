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

/** 出典リスト全体の内訳。表示文言が「社内」を名乗ってよいかの判断に使う。 */
export type CitationSourceMix = 'internal' | 'web' | 'mixed' | 'none';

/**
 * 出典リストが社内・Web のどちらに由来するかを判定する。
 *
 * ⚠️ **実測の誤りに対する修正である。**
 * 「明日の東京の天気は？」の実行で、社内 RAG が 0 件（採用閾値 0.64 未達）で
 * 出典 9 件がすべて Web だったにもかかわらず、エスカレ時の固定文言が
 * 「以下は**社内ナレッジ**に基づく参考情報です」と表示された。
 * Web で得た情報を社内ナレッジと偽らないという方針（`grace/tools.py` の
 * 出典ルール 3）は LLM の生成文だけでなく、**アプリ側の固定文言にも適用する**。
 */
export function citationSourceMix(citations: string[]): CitationSourceMix {
  let hasWeb = false;
  let hasInternal = false;
  for (const citation of citations) {
    if (parseCitation(citation).kind === 'web') hasWeb = true;
    else hasInternal = true;
  }
  if (hasWeb && hasInternal) return 'mixed';
  if (hasWeb) return 'web';
  if (hasInternal) return 'internal';
  return 'none';
}

/**
 * エスカレ時に、生成済みの回答を「参考情報」として添えるときの前置き。
 *
 * 出典の実際の内訳から文言を決める（固定文言にしない）。出典ゼロで
 * ここに来るのは強制エスカレ（エスカレ語検知）のときだけなので、
 * その場合は出典を名乗らない。
 */
export function escalateReferenceNotice(citations: string[]): string {
  const tail = '方針により有人対応へ引き継ぎます。';
  switch (citationSourceMix(citations)) {
    case 'internal':
      return `以下は社内ナレッジに基づく参考情報です。${tail}`;
    case 'web':
      return `以下は Web 検索結果に基づく参考情報です（社内ナレッジには該当がありませんでした）。${tail}`;
    case 'mixed':
      return `以下は社内ナレッジと Web 検索結果に基づく参考情報です。${tail}`;
    case 'none':
      return `以下は参考情報です（出典は取得できていません）。${tail}`;
  }
}

/**
 * 矛盾検知時の注意書き。
 *
 * 内部×Web の相互検証だけでなく Web 内どうしの矛盾でも `contradiction` が
 * 立つため、社内出典が 1 件も無いのに「社内ナレッジと Web 情報で食い違い」と
 * 書いてしまうことがある。社内・Web が揃っているときだけそう名乗る。
 */
export function contradictionNotice(citations: string[]): string {
  return citationSourceMix(citations) === 'mixed'
    ? '⚠️ 注意: 社内ナレッジと Web 情報で食い違いの可能性があります。'
    : '⚠️ 注意: 複数の情報源の間で食い違いの可能性があります。';
}

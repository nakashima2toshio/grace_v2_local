// 文書の文字数と上限の関係から、表示文言とアナウンス文言を決める純関数。
//
// ## なぜ純関数に出すか
//
// 「上限を超えたら何をどう伝えるか」は判断であり、コンポーネントに残すと
// テストできない（vitest は `.test.tsx` を収集しない。CLAUDE.md §6）。
//
// ## アナウンスが 1 回で済む理由
//
// 支援技術は `aria-live` 領域の**テキストが変わったとき**に読み上げる。
// 超過中の文言を**長さに依存しない固定文**にしてあるので、超過したまま
// 入力を続けても読み上げは繰り返されない（超えた瞬間に 1 回だけ鳴る）。
// ここに文字数を混ぜると、**1 打鍵ごとに読み上げが走って実用にならない**。

/** 判定の結果。表示とアナウンスの両方をここで決める。 */
export interface DocumentLimit {
  /** 現在の文字数 */
  length: number;
  /** 上限（`backend/app/schemas.py` の MAX_DOCUMENT_CHARS と一致させる） */
  max: number;
  /** 上限を超えているか。送信の可否と `aria-invalid` に使う */
  over: boolean;
  /** カウンタに表示する文言（視覚用） */
  label: string;
  /**
   * 支援技術へ読ませる文言。`null` ならライブ領域を空にする（＝読み上げない）。
   * **超過中は長さを含まない固定文**にして、再読み上げを防ぐ。
   */
  announcement: string | null;
}

/**
 * 文字数と上限から表示・アナウンスを決める。
 *
 * @param text 入力中の文書
 * @param max  上限文字数
 */
export function documentLimit(text: string, max: number): DocumentLimit {
  const length = text.length;
  const over = length > max;
  const counts = `${length.toLocaleString()} / ${max.toLocaleString()} 文字`;
  return {
    length,
    max,
    over,
    label: over ? `${counts}（上限を超えています。分割して実行してください）` : counts,
    // ⚠️ ここに文字数を入れないこと（入れると 1 打鍵ごとに読み上げが走る）。
    announcement: over
      ? '文字数が上限を超えています。分割して実行してください。'
      : null,
  };
}

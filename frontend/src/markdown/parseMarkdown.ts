// 軽量 Markdown パーサ（依存ライブラリなし・node 環境でテスト可能な純関数）。
//
// GRACE-Support の回答本文で使われる Markdown サブセットを、React で描画できる
// ブロック AST へ変換する。対応: 見出し(#..######)・水平線(---)・箇条書き(- / *)・
// 番号付きリスト(1.)・引用(>)・GFM テーブル(| ... |)・段落。インラインは
// 太字(**)・インラインコード(`)・リンク([text](url)) に対応する。
//
// 「描画」は React コンポーネント（Markdown.tsx）が担当し、本モジュールは
// 副作用のない解析だけを行う（テスト容易性のため）。

export type Inline =
  | { type: 'text'; value: string }
  | { type: 'bold'; value: string }
  | { type: 'code'; value: string }
  | { type: 'link'; value: string; href: string };

export type Block =
  | { type: 'heading'; level: number; inline: Inline[] }
  | { type: 'paragraph'; lines: Inline[][] }
  | { type: 'hr' }
  | { type: 'list'; ordered: boolean; items: Inline[][] }
  | { type: 'blockquote'; lines: Inline[][] }
  | { type: 'table'; header: Inline[][]; rows: Inline[][][] };

const HEADING_RE = /^(#{1,6})\s+(.*)$/;
const HR_RE = /^\s*([-*_])(?:\s*\1){2,}\s*$/;
const UL_RE = /^\s*[-*]\s+(.*)$/;
const OL_RE = /^\s*\d+\.\s+(.*)$/;
const QUOTE_RE = /^\s*>\s?(.*)$/;
const TABLE_ROW_RE = /^\s*\|(.+)\|\s*$/;
const TABLE_SEP_RE = /^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$/;

/** インライン Markdown（**bold** / `code` / [text](url)）をトークン列へ分解する。 */
export function parseInline(text: string): Inline[] {
  const tokens: Inline[] = [];
  let rest = text;
  // 太字 → コード → リンク の順で最初に一致した記法を切り出す
  const pattern = /(\*\*([^*]+)\*\*)|(`([^`]+)`)|(\[([^\]]+)\]\(([^)]+)\))/;
  while (rest.length > 0) {
    const m = pattern.exec(rest);
    if (!m || m.index === undefined) {
      tokens.push({ type: 'text', value: rest });
      break;
    }
    if (m.index > 0) {
      tokens.push({ type: 'text', value: rest.slice(0, m.index) });
    }
    if (m[1] !== undefined) {
      tokens.push({ type: 'bold', value: m[2] });
    } else if (m[3] !== undefined) {
      tokens.push({ type: 'code', value: m[4] });
    } else if (m[5] !== undefined) {
      tokens.push({ type: 'link', value: m[6], href: m[7] });
    }
    rest = rest.slice(m.index + m[0].length);
  }
  return tokens.length > 0 ? tokens : [{ type: 'text', value: '' }];
}

/** テーブル行（| a | b |）をセルのインライン配列へ分解する。 */
function parseTableCells(line: string): Inline[][] {
  const inner = line.replace(/^\s*\|/, '').replace(/\|\s*$/, '');
  return inner.split('|').map((cell) => parseInline(cell.trim()));
}

/** Markdown 文字列をブロック AST へ変換する。 */
export function parseMarkdown(source: string): Block[] {
  const lines = (source ?? '').replace(/\r\n?/g, '\n').split('\n');
  const blocks: Block[] = [];
  let paragraph: Inline[][] = [];

  const flushParagraph = () => {
    if (paragraph.length > 0) {
      blocks.push({ type: 'paragraph', lines: paragraph });
      paragraph = [];
    }
  };

  let i = 0;
  while (i < lines.length) {
    const line = lines[i];

    // 空行 → 段落の区切り
    if (line.trim() === '') {
      flushParagraph();
      i += 1;
      continue;
    }

    // 水平線
    if (HR_RE.test(line)) {
      flushParagraph();
      blocks.push({ type: 'hr' });
      i += 1;
      continue;
    }

    // 見出し
    const heading = HEADING_RE.exec(line);
    if (heading) {
      flushParagraph();
      blocks.push({
        type: 'heading',
        level: heading[1].length,
        inline: parseInline(heading[2].trim()),
      });
      i += 1;
      continue;
    }

    // テーブル（現在行が | ... |、次行が区切り行）
    if (
      TABLE_ROW_RE.test(line) &&
      i + 1 < lines.length &&
      TABLE_SEP_RE.test(lines[i + 1]) &&
      lines[i + 1].includes('-')
    ) {
      flushParagraph();
      const header = parseTableCells(line);
      const rows: Inline[][][] = [];
      i += 2; // ヘッダ行 + 区切り行をスキップ
      while (i < lines.length && TABLE_ROW_RE.test(lines[i])) {
        rows.push(parseTableCells(lines[i]));
        i += 1;
      }
      blocks.push({ type: 'table', header, rows });
      continue;
    }

    // 箇条書き（- / *）
    if (UL_RE.test(line)) {
      flushParagraph();
      const items: Inline[][] = [];
      while (i < lines.length && UL_RE.test(lines[i])) {
        items.push(parseInline(UL_RE.exec(lines[i])![1].trim()));
        i += 1;
      }
      blocks.push({ type: 'list', ordered: false, items });
      continue;
    }

    // 番号付きリスト（1.）
    if (OL_RE.test(line)) {
      flushParagraph();
      const items: Inline[][] = [];
      while (i < lines.length && OL_RE.test(lines[i])) {
        items.push(parseInline(OL_RE.exec(lines[i])![1].trim()));
        i += 1;
      }
      blocks.push({ type: 'list', ordered: true, items });
      continue;
    }

    // 引用（>）
    if (QUOTE_RE.test(line)) {
      flushParagraph();
      const quoteLines: Inline[][] = [];
      while (i < lines.length && QUOTE_RE.test(lines[i])) {
        quoteLines.push(parseInline(QUOTE_RE.exec(lines[i])![1].trim()));
        i += 1;
      }
      blocks.push({ type: 'blockquote', lines: quoteLines });
      continue;
    }

    // 段落（連続する通常行を <br> で連結する）
    paragraph.push(parseInline(line.trim()));
    i += 1;
  }

  flushParagraph();
  return blocks;
}

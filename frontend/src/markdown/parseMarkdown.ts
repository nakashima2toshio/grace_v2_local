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

/** リストの 1 項目。`children` は字下げされた入れ子リスト。 */
export interface ListItem {
  inline: Inline[];
  children?: ListBlock;
}

export interface ListBlock {
  type: 'list';
  ordered: boolean;
  items: ListItem[];
}

export type Block =
  | { type: 'heading'; level: number; inline: Inline[] }
  | { type: 'paragraph'; lines: Inline[][] }
  | { type: 'hr' }
  | ListBlock
  | { type: 'blockquote'; lines: Inline[][] }
  | { type: 'table'; header: Inline[][]; rows: Inline[][][] };

const HEADING_RE = /^(#{1,6})\s+(.*)$/;
const HR_RE = /^\s*([-*_])(?:\s*\1){2,}\s*$/;
const UL_RE = /^([ \t]*)[-*][ \t]+(.*)$/;
const OL_RE = /^([ \t]*)\d+\.[ \t]+(.*)$/;
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

/** 行がリスト項目なら字下げ幅・種別・本文を返す。違えば null。 */
function matchListItem(
  line: string,
): { indent: number; ordered: boolean; text: string } | null {
  const ul = UL_RE.exec(line);
  if (ul) return { indent: ul[1].length, ordered: false, text: ul[2] };
  const ol = OL_RE.exec(line);
  if (ol) return { indent: ol[1].length, ordered: true, text: ol[2] };
  return null;
}

/**
 * 字下げ幅 `indent` のリストを 1 ブロック分解析する。
 *
 * ⚠️ **字下げを捨てない。** 以前は `/^\s*[-*]\s+/` で先頭空白ごと読み飛ばし、
 * 項目の型も `Inline[][]`（平坦）だったため、**入れ子リストが兄弟項目へ潰れて**
 * いた。実測 2026-08-30（本リポジトリ）で、生成側は
 *
 *     *   **取得方法**
 *         *   市役所本庁舎・各区役所の窓口
 *
 * と階層を出していたのに、画面では「取得方法」と「市役所…」が同列に並んでいた。
 *
 * ⚠️ **継続行（マーカーの無い字下げ行）も捨てない。** 実測（姉妹リポジトリ grace_v2）の
 *
 *     - **窓口での取得**
 *       市役所本庁舎・各区役所の窓口でお手続きいただけます。
 *
 * は、2 行目がリストを打ち切って段落になり、**箇条書き 1 個 → 段落 → 箇条書き
 * 1 個**とブツ切りに描画されていた。項目本文へ連結する。
 */
function parseList(
  lines: string[],
  start: number,
  indent: number,
): { block: ListBlock; next: number } {
  const first = matchListItem(lines[start])!;
  const ordered = first.ordered;
  const items: ListItem[] = [];
  let i = start;

  while (i < lines.length) {
    const line = lines[i];
    if (line.trim() === '') break;
    if (HR_RE.test(line)) break;          // --- は項目ではなく水平線

    const item = matchListItem(line);
    if (item) {
      if (item.indent < indent) break;              // 親のレベルへ戻った
      if (item.indent > indent) {                   // 入れ子
        if (items.length === 0) break;
        const nested = parseList(lines, i, item.indent);
        items[items.length - 1].children = nested.block;
        i = nested.next;
        continue;
      }
      if (item.ordered !== ordered) break;          // 記法が変われば別ブロック
      items.push({ inline: parseInline(item.text.trim()) });
      i += 1;
      continue;
    }

    // マーカーの無い行 = 継続行。字下げされている場合だけ直前の項目へ連結する
    // （字下げが無い行は本文の続きなので、リストを終える）。
    const firstNonSpace = line.search(/\S/);
    if (items.length === 0 || firstNonSpace <= indent) break;
    const last = items[items.length - 1];
    last.inline = [
      ...last.inline,
      { type: 'text', value: ' ' },
      ...parseInline(line.trim()),
    ];
    i += 1;
  }

  return { block: { type: 'list', ordered, items }, next: i };
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

    // 箇条書き（- / *）と番号付きリスト（1.）— 入れ子と継続行に対応
    const listStart = matchListItem(line);
    if (listStart) {
      flushParagraph();
      const { block, next } = parseList(lines, i, listStart.indent);
      blocks.push(block);
      i = next;
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

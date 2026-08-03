// parseMarkdown（Markdown → ブロック AST）の単体テスト。node 環境で実行。
import { describe, expect, it } from 'vitest';
import { parseInline, parseMarkdown } from './parseMarkdown';

describe('parseInline', () => {
  it('太字・インラインコード・リンクを分解する', () => {
    expect(parseInline('通常 **太字** と `code` と [link](https://x)')).toEqual([
      { type: 'text', value: '通常 ' },
      { type: 'bold', value: '太字' },
      { type: 'text', value: ' と ' },
      { type: 'code', value: 'code' },
      { type: 'text', value: ' と ' },
      { type: 'link', value: 'link', href: 'https://x' },
    ]);
  });

  it('記法が無ければ 1 つの text になる', () => {
    expect(parseInline('ただの文')).toEqual([{ type: 'text', value: 'ただの文' }]);
  });
});

describe('parseMarkdown', () => {
  it('見出しをレベル付きで解析する', () => {
    const blocks = parseMarkdown('## タイトル\n### サブ');
    expect(blocks).toEqual([
      { type: 'heading', level: 2, inline: [{ type: 'text', value: 'タイトル' }] },
      { type: 'heading', level: 3, inline: [{ type: 'text', value: 'サブ' }] },
    ]);
  });

  it('水平線を hr にする', () => {
    expect(parseMarkdown('---')).toEqual([{ type: 'hr' }]);
  });

  it('箇条書きをリストにまとめる', () => {
    const blocks = parseMarkdown('- one\n- two');
    expect(blocks).toEqual([
      {
        type: 'list',
        ordered: false,
        items: [
          [{ type: 'text', value: 'one' }],
          [{ type: 'text', value: 'two' }],
        ],
      },
    ]);
  });

  it('番号付きリストを ordered=true にする', () => {
    const blocks = parseMarkdown('1. a\n2. b');
    expect(blocks[0]).toMatchObject({ type: 'list', ordered: true });
  });

  it('GFM テーブルをヘッダと行に分解する', () => {
    const md = '| 方法 | 備考 |\n|------|------|\n| **窓口** | 受付 |';
    const blocks = parseMarkdown(md);
    expect(blocks).toHaveLength(1);
    const table = blocks[0];
    expect(table.type).toBe('table');
    if (table.type === 'table') {
      expect(table.header).toEqual([
        [{ type: 'text', value: '方法' }],
        [{ type: 'text', value: '備考' }],
      ]);
      expect(table.rows).toEqual([
        [[{ type: 'bold', value: '窓口' }], [{ type: 'text', value: '受付' }]],
      ]);
    }
  });

  it('引用を blockquote にする', () => {
    const blocks = parseMarkdown('> 注意\n> 続き');
    expect(blocks[0]).toMatchObject({ type: 'blockquote' });
  });

  it('段落は空行で区切られ、連続行は同一段落になる', () => {
    const blocks = parseMarkdown('行1\n行2\n\n別段落');
    expect(blocks).toHaveLength(2);
    expect(blocks[0]).toMatchObject({ type: 'paragraph' });
    if (blocks[0].type === 'paragraph') {
      expect(blocks[0].lines).toHaveLength(2);
    }
    expect(blocks[1]).toMatchObject({ type: 'paragraph' });
  });

  it('空文字列は空配列を返す', () => {
    expect(parseMarkdown('')).toEqual([]);
  });
});

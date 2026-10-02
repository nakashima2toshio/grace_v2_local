// 原文ハイライト分割（highlight.ts）の単体テスト。
//
// ここが壊れると原文の文字が欠ける・重複するという最も分かりにくい不具合になるため、
// 「連結すると原文に戻る」ことを全ケースで確認する。
import { describe, expect, it } from 'vitest';
import type { ReviewFinding, Severity } from '../types';
import {
  buildHighlights,
  documentViewHeading,
  pointsIntoDocument,
  resolveOverlaps,
} from './highlight';

function finding(
  id: string,
  start: number,
  end: number,
  severity: Severity = 'medium',
): ReviewFinding {
  return {
    finding_id: id,
    segment_id: 's001',
    excerpt: '',
    start,
    end,
    rule_id: 'keihyo-01',
    rule_title: '優良誤認表示',
    category: '優良誤認',
    law: '景品表示法',
    article: '第5条第1号',
    message: '指摘',
    suggestion: '修正案',
    severity,
    confidence: 0.9,
    citations: [],
    status: 'review_required',
    forced: false,
    suppress_reason: null,
    web_checked: false,
  };
}

const DOC = '当社の商品は業界No.1の品質です。';

describe('buildHighlights', () => {
  it('指摘が無ければ原文 1 個の断片になる', () => {
    expect(buildHighlights(DOC, [])).toEqual([
      { text: DOC, findingId: null, severity: null },
    ]);
  });

  it('空文書は空配列（余計な空断片を作らない）', () => {
    expect(buildHighlights('', [])).toEqual([]);
  });

  it('1 件の指摘が 前 / 該当 / 後 の 3 断片に分割される', () => {
    const pieces = buildHighlights(DOC, [finding('f001', 6, 12, 'high')]);
    expect(pieces.map((p) => p.text)).toEqual(['当社の商品は', '業界No.1', 'の品質です。']);
    expect(pieces[1].findingId).toBe('f001');
    expect(pieces[1].severity).toBe('high');
    expect(pieces[0].findingId).toBeNull();
  });

  it('先頭・末尾に接する指摘でも空断片を作らない', () => {
    const head = buildHighlights(DOC, [finding('f001', 0, 6)]);
    expect(head).toHaveLength(2);
    expect(head[0].findingId).toBe('f001');

    const tail = buildHighlights(DOC, [finding('f001', 6, DOC.length)]);
    expect(tail).toHaveLength(2);
    expect(tail[1].findingId).toBe('f001');
  });

  it('複数の指摘が start 昇順に並ぶ（入力順に依存しない）', () => {
    const pieces = buildHighlights(DOC, [
      finding('f002', 12, 16),
      finding('f001', 0, 6),
    ]);
    const ids = pieces.map((p) => p.findingId).filter(Boolean);
    expect(ids).toEqual(['f001', 'f002']);
  });

  it('連結すると必ず原文に戻る（文字の欠落・重複がない）', () => {
    const cases: ReviewFinding[][] = [
      [],
      [finding('f001', 6, 12)],
      [finding('f001', 0, 6), finding('f002', 12, 16)],
      [finding('f001', 6, 12, 'low'), finding('f002', 8, 14, 'high')], // 重なり
      [finding('f001', 0, DOC.length)],
    ];
    for (const findings of cases) {
      const joined = buildHighlights(DOC, findings)
        .map((p) => p.text)
        .join('');
      expect(joined).toBe(DOC);
    }
  });

  it('範囲外のオフセットは無視され、本文は欠落しない', () => {
    const pieces = buildHighlights(DOC, [finding('f001', 6, 999)]);
    expect(pieces.map((p) => p.text).join('')).toBe(DOC);
    expect(pieces.every((p) => p.findingId === null)).toBe(true);
  });

  it('start >= end の空スパンは捨てる', () => {
    const pieces = buildHighlights(DOC, [finding('f001', 6, 6)]);
    expect(pieces).toEqual([{ text: DOC, findingId: null, severity: null }]);
  });
});

describe('resolveOverlaps', () => {
  it('重なった指摘は severity の高い方を残す', () => {
    const kept = resolveOverlaps([
      finding('low', 0, 10, 'low'),
      finding('high', 5, 15, 'high'),
    ]);
    expect(kept.map((f) => f.finding_id)).toEqual(['high']);
  });

  it('severity が同じなら先に始まる方を残す（先勝ち）', () => {
    const kept = resolveOverlaps([
      finding('first', 0, 10, 'medium'),
      finding('second', 5, 15, 'medium'),
    ]);
    expect(kept.map((f) => f.finding_id)).toEqual(['first']);
  });

  it('隣接（end == 次の start）は重なりではない', () => {
    const kept = resolveOverlaps([finding('a', 0, 5), finding('b', 5, 10)]);
    expect(kept).toHaveLength(2);
  });

  it('重ならない指摘はすべて残る', () => {
    const kept = resolveOverlaps([
      finding('a', 0, 5),
      finding('b', 10, 15),
      finding('c', 20, 25),
    ]);
    expect(kept).toHaveLength(3);
  });

  it('入力配列を破壊しない（純関数）', () => {
    const input = [finding('b', 10, 15), finding('a', 0, 5)];
    resolveOverlaps(input);
    expect(input.map((f) => f.finding_id)).toEqual(['b', 'a']);
  });
});

describe('documentViewHeading（原文ペインの見出し）', () => {
  // 表記漏れは原文に場所が無く start = end = 0 で届く
  const missing = (id: string) => finding(id, 0, 0);

  it('指摘 0 件', () => {
    expect(documentViewHeading(DOC, [])).toBe('原文（指摘なし）');
  });

  it('すべて原文の箇所を指すなら従来どおり「N 箇所を指摘」', () => {
    expect(documentViewHeading(DOC, [finding('a', 0, 2), finding('b', 6, 11)])).toBe(
      '原文（2 箇所を指摘）',
    );
  });

  it('**表記漏れが混ざると件数を分ける**（実測 2026-10-02: 4 件中 1 件だけハイライト）', () => {
    const findings = [missing('a'), missing('b'), missing('c'), finding('d', 6, 11)];
    expect(documentViewHeading(DOC, findings)).toBe('原文（指摘 4 件のうち 1 件が原文の箇所）');
  });

  it('すべて表記漏れなら「該当箇所なし」', () => {
    expect(documentViewHeading(DOC, [missing('a'), missing('b')])).toBe(
      '原文（指摘 2 件・原文に該当箇所なし）',
    );
  });

  it('範囲外のオフセットは原文の箇所として数えない（ハイライトされないため）', () => {
    expect(pointsIntoDocument(DOC, finding('x', 0, DOC.length + 5))).toBe(false);
    expect(pointsIntoDocument(DOC, finding('y', 0, DOC.length))).toBe(true);
    expect(documentViewHeading(DOC, [finding('x', 0, DOC.length + 5)])).toBe(
      '原文（指摘 1 件・原文に該当箇所なし）',
    );
  });
});

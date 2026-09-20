// documentLimit の純関数テスト。
import { describe, expect, it } from 'vitest';

import { documentLimit } from './documentLimit';

const MAX = 50000;

describe('documentLimit', () => {
  it('空文字は 0 / 上限 を返し、超過していない', () => {
    const r = documentLimit('', MAX);
    expect(r.length).toBe(0);
    expect(r.over).toBe(false);
    expect(r.label).toBe('0 / 50,000 文字');
  });

  it('上限ちょうどは超過ではない（境界）', () => {
    const r = documentLimit('あ'.repeat(MAX), MAX);
    expect(r.length).toBe(MAX);
    expect(r.over).toBe(false);
    expect(r.announcement).toBeNull();
  });

  it('上限 +1 で超過になる（境界）', () => {
    const r = documentLimit('あ'.repeat(MAX + 1), MAX);
    expect(r.over).toBe(true);
  });

  it('超過していないときはアナウンスしない', () => {
    expect(documentLimit('短い文書', MAX).announcement).toBeNull();
  });

  it('超過したらアナウンス文言を返す', () => {
    const r = documentLimit('あ'.repeat(MAX + 1), MAX);
    expect(r.announcement).toBe('文字数が上限を超えています。分割して実行してください。');
  });

  it('⭐ 超過中のアナウンス文言は長さに依存しない（再読み上げを防ぐ）', () => {
    // ここが変わると 1 打鍵ごとに読み上げが走るため、明示的に固定する。
    const a = documentLimit('あ'.repeat(MAX + 1), MAX).announcement;
    const b = documentLimit('あ'.repeat(MAX + 999), MAX).announcement;
    expect(a).toBe(b);
  });

  it('表示ラベルは超過時に対処方法を添える', () => {
    const r = documentLimit('あ'.repeat(MAX + 1), MAX);
    expect(r.label).toContain('（上限を超えています。分割して実行してください）');
  });

  it('表示ラベルは千区切りを付ける', () => {
    expect(documentLimit('あ'.repeat(1234), MAX).label).toBe('1,234 / 50,000 文字');
  });

  it('上限を変えても境界が追随する', () => {
    expect(documentLimit('abcd', 4).over).toBe(false);
    expect(documentLimit('abcde', 4).over).toBe(true);
  });

  it('サロゲートペアは String.length と同じ数え方（API 側の検証と揃える）', () => {
    // backend は len(str) で数えるため、UI もここでは合わせない（過剰に賢くしない）。
    const r = documentLimit('😀', MAX);
    expect(r.length).toBe(2);
  });
});

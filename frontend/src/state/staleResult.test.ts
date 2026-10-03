// 結果欄が古いかの判定（staleResult.ts）の単体テスト。
import { describe, expect, it } from 'vitest';
import { isResultStale, STALE_RESULT_NOTICE } from './staleResult';

const MISSING_LP = '販売価格: 4,980円（税込）\n返品: 商品到着後8日以内';
const OK_LP = '販売価格: 4,980円（税込）\n送料: 全国一律600円（税込）\n返品: 商品到着後14日以内';

describe('isResultStale', () => {
  it('**サンプルを切り替えただけ（未実行）なら古い**（実測 2026-10-03 の読み違え）', () => {
    expect(isResultStale(MISSING_LP, OK_LP, false)).toBe(true);
  });

  it('同じ文書なら古くない', () => {
    expect(isResultStale(OK_LP, OK_LP, false)).toBe(false);
  });

  it('前後の空白だけの違いは同じとみなす', () => {
    expect(isResultStale(OK_LP, `  ${OK_LP}\n`, false)).toBe(false);
  });

  it('実行中は古いと言わない（これから新しい結果が出る）', () => {
    expect(isResultStale(MISSING_LP, OK_LP, true)).toBe(false);
  });

  it('まだ何も実行していない／入力欄の値を受け取っていないときは出さない', () => {
    expect(isResultStale('', OK_LP, false)).toBe(false);
    expect(isResultStale(MISSING_LP, null, false)).toBe(false);
  });

  it('文言は「まだチェックしていない」ことと次の操作を伝える', () => {
    expect(STALE_RESULT_NOTICE).toContain('まだチェックしていません');
    expect(STALE_RESULT_NOTICE).toContain('表示チェックを実行');
  });
});

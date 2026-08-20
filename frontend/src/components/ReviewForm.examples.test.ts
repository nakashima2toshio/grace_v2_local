// GRACE-Review の入力サンプル（例チップ）が、ラベルどおりの結果を出す中身に
// なっていることを固定する。
//
// ## 背景（実測 2026-08-20 15:00）
//
// 「OK 例（特商法表記あり）」というラベルのサンプルが、実際には特商法 第11条 が
// 求める 3 項目を欠いており、さらに返品期限が社内規程（14日）より短い 8日 だった。
// 押すと必ず 4 件の指摘が出る。
//
//     [HIGH]   販売価格・送料の明示     … 送料の記載が無い          → tokusho-01
//     [MEDIUM] 代金の支払時期・方法     … 支払方法の記載が無い      → tokusho-02
//     [MEDIUM] 商品の引渡時期           … 発送時期の記載が無い      → tokusho-03
//     [MEDIUM] 表示内容と社内規程の不一致 … 規程 14日 に対し広告 8日 → policy-01
//
// 指摘はすべて**正しい**。誤っていたのは「OK 例」というラベルのほうである。
// 「OK 例」を押して指摘が出ると、**製品の不具合と区別がつかない**。実際、
// この取り違えのせいで検証が何度も空転した。
//
// ここで固定すること:
//   1. OK 例が特商法 第11条 の必須項目をすべて含むこと
//   2. OK 例の返品条件が社内規程（14日）と食い違わないこと
//   3. NG 例が「NG」を名乗っていること（ラベルと中身の一致）
//   4. サンプルが上限文字数を超えないこと
import { describe, expect, it } from 'vitest';
import { EXAMPLES } from './ReviewForm';

const byLabel = (needle: string) => {
  const found = EXAMPLES.filter((e) => e.label.includes(needle));
  expect(found.length, `ラベルに「${needle}」を含むサンプルが 1 件ではない`).toBe(1);
  return found[0];
};

describe('入力サンプル', () => {
  it('OK 例と NG 例が揃っている', () => {
    expect(EXAMPLES.filter((e) => e.label.startsWith('OK 例'))).toHaveLength(1);
    expect(EXAMPLES.filter((e) => e.label.startsWith('NG 例'))).toHaveLength(2);
  });

  it('ラベルは OK / NG のどちらかで始まる', () => {
    for (const example of EXAMPLES) {
      expect(example.label).toMatch(/^(OK|NG) 例/);
    }
  });

  it('本文とタイトルが空でない', () => {
    for (const example of EXAMPLES) {
      expect(example.document.trim().length, example.label).toBeGreaterThan(0);
      expect(example.title.trim().length, example.label).toBeGreaterThan(0);
    }
  });

  it('上限文字数（50,000）に収まる', () => {
    for (const example of EXAMPLES) {
      expect(example.document.length, example.label).toBeLessThanOrEqual(50000);
    }
  });
});

describe('OK 例（指摘 0 件を期待）', () => {
  // 特商法 第11条 の必須項目。1 つでも欠けると対応する always_check ルールが発火する。
  const REQUIRED: Array<[string, string]> = [
    ['販売業者', 'tokusho-05 事業者名'],
    ['所在地', 'tokusho-05 住所'],
    ['電話番号', 'tokusho-05 連絡先'],
    ['販売価格', 'tokusho-01 販売価格'],
    ['送料', 'tokusho-01 送料'],
    ['お支払い方法', 'tokusho-02 支払時期・方法'],
    ['発送時期', 'tokusho-03 引渡時期'],
    ['返品', 'tokusho-04 返品特約'],
  ];

  it.each(REQUIRED)('「%s」を含む（%s）', (term) => {
    expect(byLabel('OK 例').document).toContain(term);
  });

  it('前払い／後払いの別が読み取れる', () => {
    // tokusho-02 は決済手段だけでなく「前払い／後払いの別」も求める。
    expect(byLabel('OK 例').document).toMatch(/前払い|後払い/);
  });

  it('返品期限が社内規程と同じ 14日 である', () => {
    // ⚠️ 8日 にすると規程（14日）より顧客に不利になり policy-01 が発火する。
    const document = byLabel('OK 例').document;

    expect(document).toContain('14日以内');
    expect(document).not.toContain('8日以内');
  });
});

describe('NG 例（表記漏れ・規程不一致）', () => {
  const document = byLabel('表記漏れ・規程不一致').document;

  it('特商法の必須項目が欠けている（表記漏れを再現する）', () => {
    expect(document).not.toContain('送料:');
    expect(document).not.toContain('お支払い方法');
    expect(document).not.toContain('発送時期');
  });

  it('返品期限が規程より短い 8日 である（規程不一致を再現する）', () => {
    expect(document).toContain('8日以内');
  });

  it('OK 例と取り違えられないラベルになっている', () => {
    // 以前は同じ中身が「OK 例（特商法表記あり）」を名乗っていた。
    expect(byLabel('表記漏れ・規程不一致').label).not.toContain('OK');
  });
});

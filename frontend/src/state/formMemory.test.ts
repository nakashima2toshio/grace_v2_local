import { beforeEach, describe, expect, it } from 'vitest';

import {
  DEFAULT_QUERY_FORM,
  DEFAULT_REVIEW_FORM,
  clearFormMemory,
  recallQueryForm,
  recallReviewForm,
  rememberQueryForm,
  rememberReviewForm,
} from './formMemory';

describe('QueryForm の記憶', () => {
  beforeEach(() => {
    clearFormMemory();
  });

  it('未記録なら既定値（初回マウントは素の状態から始まる）', () => {
    expect(recallQueryForm('basic')).toEqual(DEFAULT_QUERY_FORM);
    expect(recallQueryForm('vertical')).toEqual(DEFAULT_QUERY_FORM);
  });

  it('**チェックを変えた状態が残る**（タブを戻しても既定へ復帰しない）', () => {
    rememberQueryForm('vertical', {
      ...DEFAULT_QUERY_FORM,
      dryRun: true,
      useWeb: false,
      doAction: false,
      verbose: true,
    });

    const restored = recallQueryForm('vertical');
    expect(restored.dryRun).toBe(true);
    expect(restored.useWeb).toBe(false);
    expect(restored.doAction).toBe(false);
    expect(restored.verbose).toBe(true);
  });

  it('**入力したテキストも残る**（問い合わせ文・識別子）', () => {
    rememberQueryForm('vertical', {
      ...DEFAULT_QUERY_FORM,
      query: '返品したい',
      vertical: 'ec',
      orderId: '1001',
      email: 'a@example.com',
    });

    expect(recallQueryForm('vertical')).toMatchObject({
      query: '返品したい',
      vertical: 'ec',
      orderId: '1001',
      email: 'a@example.com',
    });
  });

  it('**基本版と GRACE-Support は独立している**（片方の設定が漏れない）', () => {
    rememberQueryForm('basic', { ...DEFAULT_QUERY_FORM, dryRun: true });

    // 基本版で dry-run を入れても、GRACE-Support 側は既定（OFF）のまま
    expect(recallQueryForm('basic').dryRun).toBe(true);
    expect(recallQueryForm('vertical').dryRun).toBe(false);
  });

  it('同じキーへの再保存は上書き（最後の入力が勝つ）', () => {
    rememberQueryForm('basic', { ...DEFAULT_QUERY_FORM, query: '古い' });
    rememberQueryForm('basic', { ...DEFAULT_QUERY_FORM, query: '新しい' });
    expect(recallQueryForm('basic').query).toBe('新しい');
  });

  it('既定値オブジェクトは書き換わらない（recall の結果を直接変更しても汚染しない）', () => {
    // recall は未記録時に共有の DEFAULT を返す。呼び出し側は state 初期値としてしか
    // 使わない前提だが、万一 remember 経由で戻ってきても DEFAULT が壊れないこと
    rememberQueryForm('basic', { ...recallQueryForm('basic'), dryRun: true });
    expect(DEFAULT_QUERY_FORM.dryRun).toBe(false);
  });
});

describe('ReviewForm の記憶', () => {
  beforeEach(() => {
    clearFormMemory();
  });

  it('未記録なら既定値（ruleset は ec_ad・Web 裏取りは ON）', () => {
    expect(recallReviewForm()).toEqual(DEFAULT_REVIEW_FORM);
    expect(DEFAULT_REVIEW_FORM.ruleset).toBe('ec_ad');
    expect(DEFAULT_REVIEW_FORM.useWeb).toBe(true);
  });

  it('**チェックの変更が残る**（dry-run を入れたまま戻ってこられる）', () => {
    rememberReviewForm({
      ...DEFAULT_REVIEW_FORM,
      useWeb: false,
      dryRun: true,
      verbose: true,
    });

    expect(recallReviewForm()).toMatchObject({
      useWeb: false,
      dryRun: true,
      verbose: true,
    });
  });

  it('**貼り付けた文書が消えない**（これが失われるのが一番痛い）', () => {
    const document = '当社の美容液は業界No.1の実力です。';
    rememberReviewForm({ ...DEFAULT_REVIEW_FORM, document, title: '化粧品LP案' });

    expect(recallReviewForm().document).toBe(document);
    expect(recallReviewForm().title).toBe('化粧品LP案');
  });

  it('選んだ ruleset が残る', () => {
    rememberReviewForm({ ...DEFAULT_REVIEW_FORM, ruleset: 'other' });
    expect(recallReviewForm().ruleset).toBe('other');
  });
});

describe('clearFormMemory', () => {
  it('両方まとめて消える（テストの独立性のため）', () => {
    rememberQueryForm('basic', { ...DEFAULT_QUERY_FORM, query: 'x' });
    rememberQueryForm('vertical', { ...DEFAULT_QUERY_FORM, query: 'y' });
    rememberReviewForm({ ...DEFAULT_REVIEW_FORM, document: 'z' });

    clearFormMemory();

    expect(recallQueryForm('basic')).toEqual(DEFAULT_QUERY_FORM);
    expect(recallQueryForm('vertical')).toEqual(DEFAULT_QUERY_FORM);
    expect(recallReviewForm()).toEqual(DEFAULT_REVIEW_FORM);
  });
});

describe('既定値がコンポーネントの初期値と一致していること', () => {
  // ここがずれると「初回だけ違う値で始まる」という分かりにくいバグになる。
  // QueryForm.tsx / ReviewForm.tsx の useState 初期値と 1:1 で対応させる。
  it('QueryForm: dry-run OFF・Web ON・アクション ON・詳細ログ OFF', () => {
    expect(DEFAULT_QUERY_FORM).toEqual({
      query: '',
      vertical: '',
      dryRun: false,
      verbose: false,
      useWeb: true,
      doAction: true,
      orderId: '',
      email: '',
    });
  });

  it('ReviewForm: ruleset=ec_ad・Web ON・dry-run OFF・詳細ログ OFF', () => {
    expect(DEFAULT_REVIEW_FORM).toEqual({
      document: '',
      title: '',
      ruleset: 'ec_ad',
      useWeb: true,
      dryRun: false,
      verbose: false,
    });
  });
});

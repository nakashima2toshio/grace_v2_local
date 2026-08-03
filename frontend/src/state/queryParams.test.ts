import { describe, expect, it } from 'vitest';
import {
  buildQueryParams,
  identityNote,
  isIdentityActive,
  type QueryFormState,
} from './queryParams';

/** 既定は「Support タブ・何も入力していない」状態。各テストで必要な分だけ上書きする。 */
const base: QueryFormState = {
  query: 'パスワードを忘れました',
  vertical: '',
  useWeb: true,
  doAction: true,
  dryRun: true,
  verbose: false,
  orderId: '',
  email: '',
  showVertical: true,
};

describe('buildQueryParams', () => {
  it('既定の状態を CLI 既定と同じペイロードにする', () => {
    expect(buildQueryParams(base)).toEqual({
      query: 'パスワードを忘れました',
      vertical: null,
      dry_run: true,
      use_web: true,
      do_action: true,
      verbose: false,
      identity: null,
    });
  });

  it('query の前後の空白を落とす', () => {
    expect(buildQueryParams({ ...base, query: '  返品したい  ' }).query).toBe('返品したい');
  });

  it('Support タブでは選択した vertical を送る', () => {
    expect(buildQueryParams({ ...base, vertical: 'ec' }).vertical).toBe('ec');
  });

  it('vertical 未選択（空文字）は null にする', () => {
    expect(buildQueryParams({ ...base, vertical: '' }).vertical).toBeNull();
  });

  it('基本版タブでは vertical を選んでいても常に null にする', () => {
    // 「基本版＝業界特化なし」を守る要。ここが壊れると基本版が Support と同じになる。
    const params = buildQueryParams({ ...base, showVertical: false, vertical: 'ec' });
    expect(params.vertical).toBeNull();
  });

  it('トグルをそのまま反映する（--no-web / --no-action 相当）', () => {
    const params = buildQueryParams({
      ...base, useWeb: false, doAction: false, dryRun: false, verbose: true,
    });
    expect(params).toMatchObject({
      use_web: false, do_action: false, dry_run: false, verbose: true,
    });
  });
});

describe('buildQueryParams — identity', () => {
  it('両方空なら null（空文字の辞書を送らない）', () => {
    expect(buildQueryParams({ ...base, orderId: '', email: '' }).identity).toBeNull();
  });

  it('空白だけなら null（trim 後に判定する）', () => {
    expect(buildQueryParams({ ...base, orderId: '   ', email: ' ' }).identity).toBeNull();
  });

  it('片方だけでも入力があれば送る（もう片方は空文字）', () => {
    expect(buildQueryParams({ ...base, orderId: '1001' }).identity).toEqual({
      order_id: '1001', email: '',
    });
  });

  it('両方入力すると両方送る', () => {
    expect(
      buildQueryParams({ ...base, orderId: '1001', email: 'a@example.com' }).identity,
    ).toEqual({ order_id: '1001', email: 'a@example.com' });
  });

  it('識別子の前後の空白を落とす', () => {
    expect(
      buildQueryParams({ ...base, orderId: ' 1001 ', email: ' a@example.com ' }).identity,
    ).toEqual({ order_id: '1001', email: 'a@example.com' });
  });

  it('基本版タブでも入力があれば送る（サーバ側で無視される）', () => {
    // 送信自体は妨げない。照合しないことは UI の注記とコア側の分岐で担保する。
    const params = buildQueryParams({ ...base, showVertical: false, orderId: '1001' });
    expect(params.identity).toEqual({ order_id: '1001', email: '' });
    expect(params.vertical).toBeNull();
  });
});

describe('isIdentityActive', () => {
  it('Support タブ＋require_identity なら有効', () => {
    expect(isIdentityActive(true, true)).toBe(true);
  });

  it('require_identity=false のプロファイルでは無効', () => {
    expect(isIdentityActive(true, false)).toBe(false);
  });

  it('プロファイル未選択（undefined）では無効', () => {
    expect(isIdentityActive(true, undefined)).toBe(false);
  });

  it('基本版タブでは require_identity に関係なく無効', () => {
    expect(isIdentityActive(false, true)).toBe(false);
  });
});

describe('identityNote', () => {
  it('無効なら「本人確認を行いません」', () => {
    expect(identityNote(false, true)).toContain('本人確認を行いません');
    expect(identityNote(false, false)).toContain('本人確認を行いません');
  });

  it('有効＋dry-run ON なら「入力値は照合に使われません」', () => {
    expect(identityNote(true, true)).toContain('照合に使われません');
  });

  it('有効＋dry-run OFF なら台帳照合の説明', () => {
    expect(identityNote(true, false)).toContain('SUPPORT_IDENTITY_FILE');
  });
});

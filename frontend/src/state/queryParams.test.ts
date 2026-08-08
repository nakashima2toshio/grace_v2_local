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

/** 本人確認が起動する状態（ec 相当）。識別子まわりはここを基準にする。 */
const identityBase: QueryFormState = { ...base, vertical: 'ec', requireIdentity: true };

describe('buildQueryParams — identity', () => {
  it('両方空なら null（空文字の辞書を送らない）', () => {
    expect(buildQueryParams({ ...identityBase, orderId: '', email: '' }).identity).toBeNull();
  });

  it('空白だけなら null（trim 後に判定する）', () => {
    expect(buildQueryParams({ ...identityBase, orderId: '   ', email: ' ' }).identity).toBeNull();
  });

  it('片方だけでも入力があれば送る（もう片方は空文字）', () => {
    expect(buildQueryParams({ ...identityBase, orderId: '1001' }).identity).toEqual({
      order_id: '1001', email: '',
    });
  });

  it('両方入力すると両方送る', () => {
    expect(
      buildQueryParams({ ...identityBase, orderId: '1001', email: 'a@example.com' }).identity,
    ).toEqual({ order_id: '1001', email: 'a@example.com' });
  });

  it('識別子の前後の空白を落とす', () => {
    expect(
      buildQueryParams({ ...identityBase, orderId: ' 1001 ', email: ' a@example.com ' }).identity,
    ).toEqual({ order_id: '1001', email: 'a@example.com' });
  });
});

// 画面が「無効」と表示している欄の値を送らないこと。`buildQueryParams` は DOM ではなく
// state からペイロードを組むため、`fieldset disabled` による HTML の保護が効かない。
// ec で入力 → 別プロファイルへ切り替えると欄には値が残るので、ここで落とす必要がある。
describe('buildQueryParams — 無効な識別子欄は送らない', () => {
  it('require_identity=false のプロファイル（gov）では送らない', () => {
    const params = buildQueryParams({
      ...base, vertical: 'gov', requireIdentity: false, orderId: '1001', email: 'a@example.com',
    });
    expect(params.identity).toBeNull();
    expect(params.vertical).toBe('gov');
  });

  it('プロファイル未選択（undefined）では送らない', () => {
    expect(buildQueryParams({ ...base, orderId: '1001' }).identity).toBeNull();
  });

  it('基本版タブでは require_identity に関係なく送らない', () => {
    const params = buildQueryParams({
      ...base, showVertical: false, requireIdentity: true, orderId: '1001',
    });
    expect(params.identity).toBeNull();
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
  const noteBase = {
    showVertical: true, vertical: 'ec', requireIdentity: true,
    dryRun: true, identityVerticals: ['ec'],
  };

  it('有効＋dry-run ON なら「入力値は照合に使われません」', () => {
    expect(identityNote(noteBase)).toContain('照合に使われません');
  });

  it('有効＋dry-run OFF なら台帳照合の説明', () => {
    expect(identityNote({ ...noteBase, dryRun: false })).toContain('SUPPORT_IDENTITY_FILE');
  });

  // 以前は無効側が 1 文しか無く、未選択でも基本版でも
  // 「このプロファイルは require_identity=false」と出していた（存在しない
  // プロファイルのせいにしていた）。設定ごとに理由を言い分ける。
  it('require_identity=false のプロファイルは、そのプロファイル名を挙げる', () => {
    const note = identityNote({ ...noteBase, vertical: 'gov', requireIdentity: false });
    expect(note).toContain('gov');
    expect(note).toContain('require_identity=false');
  });

  it('プロファイル未選択では「このプロファイル」のせいにしない', () => {
    const note = identityNote({ ...noteBase, vertical: '', requireIdentity: undefined });
    expect(note).toContain('未選択');
    expect(note).not.toContain('require_identity=false');
  });

  it('基本版タブでは「基本版だから」と説明する', () => {
    const note = identityNote({ ...noteBase, showVertical: false, vertical: '', requireIdentity: undefined });
    expect(note).toContain('基本版');
    expect(note).not.toContain('require_identity=false');
  });

  it('無効なときは、どのプロファイルなら有効かを併記する', () => {
    const note = identityNote({
      ...noteBase, vertical: 'gov', requireIdentity: false, identityVerticals: ['ec'],
    });
    expect(note).toContain('本人確認を行うプロファイル: ec');
  });

  // 基本版タブは /api/verticals を引かないので一覧が空になる。空を「該当なし」と
  // 断定すると（実際には ec がある）新たな嘘になるため、併記そのものを省く。
  it('一覧が空のときは、該当プロファイルの有無を断定しない', () => {
    const note = identityNote({
      ...noteBase, vertical: 'gov', requireIdentity: false, identityVerticals: [],
    });
    expect(note).toContain('require_identity=false');
    expect(note).not.toContain('本人確認を行うプロファイル');
  });
});

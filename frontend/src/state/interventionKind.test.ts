import { describe, expect, it } from 'vitest';

import { interventionKind, MULTI_QUESTION_REASON } from './interventionKind';

describe('interventionKind', () => {
  it('理由と選択肢が揃えば質問選択', () => {
    expect(
      interventionKind({ reason: MULTI_QUESTION_REASON, options: ['A は？', 'B は？'] }),
    ).toBe('question');
  });

  it('選択肢が無ければ質問選択にしない（空の N 択を出さない）', () => {
    expect(interventionKind({ reason: MULTI_QUESTION_REASON, options: [] })).toBe('action');
    expect(interventionKind({ reason: MULTI_QUESTION_REASON })).toBe('action');
  });

  it('理由が違えば選択肢があってもアクション承認のまま', () => {
    expect(interventionKind({ reason: 'アクション実行前の確認', options: ['x'] })).toBe(
      'action',
    );
  });

  it('理由なし（従来のアクション承認）はアクション', () => {
    expect(interventionKind({})).toBe('action');
    expect(interventionKind({ reason: null, options: null })).toBe('action');
  });
});

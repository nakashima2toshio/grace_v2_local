import { describe, expect, it } from 'vitest';

import { isSubmitKey, type SubmitKeyEvent } from './submitKey';

/** 何も押していない状態。各テストで必要な分だけ上書きする。 */
const base: SubmitKeyEvent = {
  key: 'Enter',
  ctrlKey: false,
  metaKey: false,
  isComposing: false,
};

describe('isSubmitKey', () => {
  it('Ctrl+Enter は送信', () => {
    expect(isSubmitKey({ ...base, ctrlKey: true })).toBe(true);
  });

  it('⌘+Enter（macOS）は送信', () => {
    expect(isSubmitKey({ ...base, metaKey: true })).toBe(true);
  });

  // ここが単一行 input との決定的な違い。textarea では Enter は改行であって
  // 送信ではない。ここが true に戻ると、改行しようとして実行が走る。
  it('Enter 単独は送信しない（改行のまま）', () => {
    expect(isSubmitKey(base)).toBe(false);
  });

  // 「Shift+Enter で送信」を期待して押すユーザーがいるが、本フォームでは改行。
  it('Shift+Enter は送信しない（改行のまま）', () => {
    expect(isSubmitKey({ ...base, shiftKey: true })).toBe(false);
  });

  it('Ctrl+Shift+Enter は送信する（Shift は送信可否に影響しない）', () => {
    expect(isSubmitKey({ ...base, ctrlKey: true, shiftKey: true })).toBe(true);
  });

  it('Enter 以外のキーは修飾キー付きでも送信しない', () => {
    expect(isSubmitKey({ ...base, key: 'a', ctrlKey: true })).toBe(false);
    expect(isSubmitKey({ ...base, key: 'Escape', metaKey: true })).toBe(false);
    expect(isSubmitKey({ ...base, key: ' ', ctrlKey: true })).toBe(false);
  });

  // 日本語入力の要。変換確定の Enter を送信と取り違えると、変換途中の文章が
  // そのまま実行される。修飾キー付き Enter を確定に使う IME もあるため、
  // isComposing は Ctrl/⌘ より優先する。
  describe('IME 変換中', () => {
    it('変換中の Ctrl+Enter は送信しない', () => {
      expect(isSubmitKey({ ...base, ctrlKey: true, isComposing: true })).toBe(false);
    });

    it('変換中の ⌘+Enter は送信しない', () => {
      expect(isSubmitKey({ ...base, metaKey: true, isComposing: true })).toBe(false);
    });

    it('変換中の Enter 単独も当然送信しない', () => {
      expect(isSubmitKey({ ...base, isComposing: true })).toBe(false);
    });
  });

  it('isComposing 未指定（省略）は変換中でないとみなす', () => {
    expect(isSubmitKey({ key: 'Enter', ctrlKey: true, metaKey: false })).toBe(true);
  });
});

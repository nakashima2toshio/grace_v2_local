import { describe, expect, it } from 'vitest';

import { isTabKey, nextFocusIndex } from './focusTrap';

describe('isTabKey', () => {
  it('Tab はフォーカス移動', () => {
    expect(isTabKey({ key: 'Tab' })).toBe(true);
  });

  it('Shift+Tab もフォーカス移動（逆方向）', () => {
    expect(isTabKey({ key: 'Tab', shiftKey: true })).toBe(true);
  });

  it('Tab 以外は false', () => {
    expect(isTabKey({ key: 'Enter' })).toBe(false);
    expect(isTabKey({ key: 'Escape' })).toBe(false);
  });

  // Ctrl+Tab（タブ切替）などブラウザ側の操作は奪わない。
  it('Shift 以外の修飾キー付きは false', () => {
    expect(isTabKey({ key: 'Tab', ctrlKey: true })).toBe(false);
    expect(isTabKey({ key: 'Tab', metaKey: true })).toBe(false);
    expect(isTabKey({ key: 'Tab', altKey: true })).toBe(false);
  });
});

describe('nextFocusIndex', () => {
  it('前進する', () => {
    expect(nextFocusIndex(3, 0)).toBe(1);
    expect(nextFocusIndex(3, 1)).toBe(2);
  });

  // ここが「閉じ込め」の本体。最後の次は外へ出さずに先頭へ戻す。
  it('末尾の次は先頭へ巻き戻す', () => {
    expect(nextFocusIndex(3, 2)).toBe(0);
  });

  it('Shift+Tab は後退する', () => {
    expect(nextFocusIndex(3, 2, true)).toBe(1);
    expect(nextFocusIndex(3, 1, true)).toBe(0);
  });

  it('先頭の前は末尾へ巻き戻す', () => {
    expect(nextFocusIndex(3, 0, true)).toBe(2);
  });

  // フォーカスがモーダル外にある状態から戻ってこられること。
  it('現在位置が不明（-1）なら、前進は先頭・後退は末尾', () => {
    expect(nextFocusIndex(3, -1)).toBe(0);
    expect(nextFocusIndex(3, -1, true)).toBe(2);
  });

  it('範囲外の添字も不明扱い', () => {
    expect(nextFocusIndex(3, 99)).toBe(0);
    expect(nextFocusIndex(3, 99, true)).toBe(2);
  });

  it('要素が 1 つだけなら常に自分自身（外へ出さない）', () => {
    expect(nextFocusIndex(1, 0)).toBe(0);
    expect(nextFocusIndex(1, 0, true)).toBe(0);
  });

  it('対象が無ければ -1（移動先なし）', () => {
    expect(nextFocusIndex(0, -1)).toBe(-1);
    expect(nextFocusIndex(-5, 0)).toBe(-1);
  });
});

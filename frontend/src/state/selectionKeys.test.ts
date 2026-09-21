import { describe, expect, it } from 'vitest';

import { isActivationKey, toggleSelection, type ActivationKeyEvent } from './selectionKeys';

/** 何も押していない状態。各テストで必要な分だけ上書きする。 */
const base: ActivationKeyEvent = {
  key: 'Enter',
  ctrlKey: false,
  metaKey: false,
  altKey: false,
  shiftKey: false,
  isComposing: false,
};

describe('isActivationKey', () => {
  it('Enter はクリック相当', () => {
    expect(isActivationKey(base)).toBe(true);
  });

  it('Space はクリック相当', () => {
    expect(isActivationKey({ ...base, key: ' ' })).toBe(true);
  });

  it('それ以外のキーは発火しない', () => {
    expect(isActivationKey({ ...base, key: 'a' })).toBe(false);
    expect(isActivationKey({ ...base, key: 'Escape' })).toBe(false);
    expect(isActivationKey({ ...base, key: 'Tab' })).toBe(false);
    expect(isActivationKey({ ...base, key: 'ArrowDown' })).toBe(false);
  });

  // ブラウザ／アプリ側のショートカットを奪わないこと。
  it('修飾キー付きは発火しない', () => {
    expect(isActivationKey({ ...base, ctrlKey: true })).toBe(false);
    expect(isActivationKey({ ...base, metaKey: true })).toBe(false);
    expect(isActivationKey({ ...base, altKey: true })).toBe(false);
    expect(isActivationKey({ ...base, shiftKey: true })).toBe(false);
    expect(isActivationKey({ ...base, key: ' ', ctrlKey: true })).toBe(false);
  });

  // ここが true に戻ると、日本語変換の確定 Enter で選択が動く。
  it('IME 変換中は発火しない', () => {
    expect(isActivationKey({ ...base, isComposing: true })).toBe(false);
    expect(isActivationKey({ ...base, key: ' ', isComposing: true })).toBe(false);
  });

  it('省略されたフィールドは押されていない扱い', () => {
    expect(isActivationKey({ key: 'Enter' })).toBe(true);
  });
});

describe('toggleSelection', () => {
  it('未選択なら選択する', () => {
    expect(toggleSelection(null, 'f1')).toBe('f1');
  });

  it('別の指摘を選ぶと乗り換える', () => {
    expect(toggleSelection('f1', 'f2')).toBe('f2');
  });

  // 原文ハイライトと指摘カードで規則を揃えるための核心。
  it('同じ指摘をもう一度選ぶと解除する', () => {
    expect(toggleSelection('f1', 'f1')).toBe(null);
  });
});

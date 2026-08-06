import { describe, expect, it, vi } from 'vitest';

import { handleTabKeyDown, nextTabIndex } from './tabKeys';

describe('nextTabIndex', () => {
  it('右 / 下で次のタブへ進む', () => {
    expect(nextTabIndex(0, 4, 'ArrowRight')).toBe(1);
    expect(nextTabIndex(0, 4, 'ArrowDown')).toBe(1);
  });

  it('左 / 上で前のタブへ戻る', () => {
    expect(nextTabIndex(2, 4, 'ArrowLeft')).toBe(1);
    expect(nextTabIndex(2, 4, 'ArrowUp')).toBe(1);
  });

  it('末尾から右で先頭へ回り込む', () => {
    expect(nextTabIndex(3, 4, 'ArrowRight')).toBe(0);
  });

  it('先頭から左で末尾へ回り込む', () => {
    expect(nextTabIndex(0, 4, 'ArrowLeft')).toBe(3);
  });

  it('Home / End で端へ飛ぶ', () => {
    expect(nextTabIndex(2, 4, 'Home')).toBe(0);
    expect(nextTabIndex(1, 4, 'End')).toBe(3);
  });

  it('移動しないキーは null', () => {
    expect(nextTabIndex(0, 4, 'Enter')).toBeNull();
    expect(nextTabIndex(0, 4, ' ')).toBeNull();
    expect(nextTabIndex(0, 4, 'Tab')).toBeNull();
    expect(nextTabIndex(0, 4, 'a')).toBeNull();
  });

  it('タブが 0 個なら null（ゼロ除算・NaN を防ぐ）', () => {
    expect(nextTabIndex(0, 0, 'ArrowRight')).toBeNull();
    expect(nextTabIndex(0, 0, 'Home')).toBeNull();
  });

  it('タブが 1 個なら自分自身を返す（回り込みが自分に戻る）', () => {
    expect(nextTabIndex(0, 1, 'ArrowRight')).toBe(0);
    expect(nextTabIndex(0, 1, 'ArrowLeft')).toBe(0);
  });
});

describe('handleTabKeyDown', () => {
  function fakeEvent(key: string) {
    return { key, preventDefault: vi.fn() };
  }

  it('移動するときは preventDefault を呼ぶ', () => {
    // ArrowDown のページスクロールや Home/End のページ端ジャンプを止める
    const event = fakeEvent('ArrowRight');
    expect(handleTabKeyDown(event, 0, 4)).toBe(1);
    expect(event.preventDefault).toHaveBeenCalledOnce();
  });

  it('移動しないキーでは preventDefault を呼ばない', () => {
    const event = fakeEvent('Enter');
    expect(handleTabKeyDown(event, 0, 4)).toBeNull();
    expect(event.preventDefault).not.toHaveBeenCalled();
  });

  it('**同じ位置に留まるなら移動扱いにしない**', () => {
    // タブ 1 個で ArrowRight。自分自身に戻るだけなので再描画もフォーカス移動も不要
    const event = fakeEvent('ArrowRight');
    expect(handleTabKeyDown(event, 0, 1)).toBeNull();
    expect(event.preventDefault).not.toHaveBeenCalled();
  });

  it('Home に既にいるなら移動扱いにしない', () => {
    const event = fakeEvent('Home');
    expect(handleTabKeyDown(event, 0, 4)).toBeNull();
    expect(event.preventDefault).not.toHaveBeenCalled();
  });
});

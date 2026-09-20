import { describe, expect, it } from 'vitest';

import { timelineAnnouncement, type AnnounceableStep } from './timelineAnnounce';

const IDS = ['load', 'chunk', 'save'] as const;
const LABELS: Record<string, string> = {
  load: '① 入力読み込み',
  chunk: '② チャンク化',
  save: '③ CSV 出力',
};

function steps(...statuses: Array<AnnounceableStep['status']>): Record<string, AnnounceableStep> {
  return Object.fromEntries(IDS.map((id, i) => [id, { status: statuses[i] }]));
}

describe('timelineAnnouncement', () => {
  it('走っているステップ名を読み上げる', () => {
    const result = timelineAnnouncement(IDS, steps('done', 'running', 'pending'), LABELS, 'x');
    expect(result).toBe('実行中: ② チャンク化');
  });

  it('**最初に見つかった running を採用する**（同時に 2 つ running でも 1 つだけ）', () => {
    const result = timelineAnnouncement(IDS, steps('running', 'running', 'pending'), LABELS, 'x');
    expect(result).toBe('実行中: ① 入力読み込み');
  });

  it('全ステップが done なら完了を伝える', () => {
    const result = timelineAnnouncement(IDS, steps('done', 'done', 'done'), LABELS, 'ステップトレース');
    expect(result).toBe('ステップトレースが完了しました');
  });

  it('**skipped も決着扱い**（スキップされたステップがあっても完了と言える）', () => {
    const result = timelineAnnouncement(IDS, steps('done', 'skipped', 'done'), LABELS, '削除の進捗');
    expect(result).toBe('削除の進捗が完了しました');
  });

  it('まだ何も始まっていなければ空文字（読み上げない）', () => {
    expect(timelineAnnouncement(IDS, steps('pending', 'pending', 'pending'), LABELS, 'x')).toBe('');
  });

  it('途中で止まっている（running が無く pending が残る）なら空文字', () => {
    // 失敗して中断した状態。「完了しました」と言うのは誤り
    expect(timelineAnnouncement(IDS, steps('done', 'done', 'pending'), LABELS, 'x')).toBe('');
  });

  it('ステップが 0 個なら空文字', () => {
    expect(timelineAnnouncement([], {}, LABELS, 'x')).toBe('');
  });

  it('ラベル未定義なら ID を出す（無言にしない）', () => {
    const result = timelineAnnouncement(['unknown'], { unknown: { status: 'running' } }, {}, 'x');
    expect(result).toBe('実行中: unknown');
  });

  it('steps に無い ID が混ざっても落ちない', () => {
    // stepIds と steps は同じ定数から作られるので通常は起きないが、
    // 型上は Record<string, ...> なので防御しておく
    expect(() => timelineAnnouncement(['missing'], {}, LABELS, 'x')).not.toThrow();
    expect(timelineAnnouncement(['missing'], {}, LABELS, 'x')).toBe('');
  });
});

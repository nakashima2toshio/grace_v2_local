import { describe, expect, it } from 'vitest';

import type { ModelChoice } from '../types';
import { MODEL_LABEL_PREFIX, modelOptionLabel } from './modelLabel';

describe('MODEL_LABEL_PREFIX', () => {
  it('見出しは「利用モデル名：」', () => {
    expect(MODEL_LABEL_PREFIX).toBe('利用モデル名：');
  });
});

describe('modelOptionLabel', () => {
  const base: ModelChoice = {
    id: 'gemma4:12b-mlx',
    supports_tool_calls: true,
    notes: 'デフォルト。MLX 版 12B（7.7 GB）',
  };

  it('notes をラベルへ畳み込む', () => {
    expect(modelOptionLabel(base)).toBe('gemma4:12b-mlx — デフォルト。MLX 版 12B（7.7 GB）');
  });

  it('notes が空なら id だけ（区切りだけが残らない）', () => {
    expect(modelOptionLabel({ ...base, notes: '' })).toBe('gemma4:12b-mlx');
    expect(modelOptionLabel({ ...base, notes: '   ' })).toBe('gemma4:12b-mlx');
  });

  // ReAct 経路で使えないことは選ぶ前に分かる必要がある。
  it('tool calling 非対応なら先頭に出す', () => {
    const label = modelOptionLabel({ id: 'phi3:latest', supports_tool_calls: false, notes: '軽量' });
    expect(label).toBe('phi3:latest — tool calling 非対応 / 軽量');
  });

  // config.py 側の notes が既に触れている場合、重ねると二重になる。
  it('notes が既に tool calling に触れていれば重ねない', () => {
    const label = modelOptionLabel({
      id: 'gemma2:latest',
      supports_tool_calls: false,
      notes: 'tool calling 非対応。ReAct には使えない',
    });
    expect(label).toBe('gemma2:latest — tool calling 非対応。ReAct には使えない');
  });

  it('非対応かつ notes が空なら理由だけ出す', () => {
    expect(modelOptionLabel({ id: 'x', supports_tool_calls: false, notes: '' }))
      .toBe('x — tool calling 非対応');
  });

  it('対応しているモデルには何も付け足さない', () => {
    expect(modelOptionLabel({ ...base, notes: '' })).not.toMatch(/tool/i);
  });

  // フロントにモデル名を持たない規約（このファイル冒頭の ⚠️）と同じ理由。
  it('id と notes は引数のものをそのまま使う', () => {
    const label = modelOptionLabel({ id: 'unknown:1b', supports_tool_calls: true, notes: 'メモ' });
    expect(label).toBe('unknown:1b — メモ');
  });
});

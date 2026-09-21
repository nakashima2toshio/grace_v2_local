import { describe, expect, it } from 'vitest';

import type { ModelChoice } from '../types';
import {
  DEFAULT_OPTION_FALLBACK,
  MODEL_LABEL_PREFIX,
  defaultOptionLabel,
  modelOptionLabel,
  formatModelLabel,
} from './modelLabel';
import type { ModelInfo } from '../types';

function info(overrides: Partial<ModelInfo> = {}): ModelInfo {
  return {
    provider: 'ollama',
    model: 'gemma4:12b-mlx',
    light_model: 'gemma4:12b-mlx',
    heavy_model: '',
    ...overrides,
  };
}

describe('formatModelLabel', () => {
  it('モデル名をそのまま返す', () => {
    expect(formatModelLabel(info())).toBe('gemma4:12b-mlx');
  });

  it('**取得前・取得失敗は null**（ヘッダーに何も出さない）', () => {
    // バックエンド未起動でもタブ操作はできるべきなので、エラー表示はしない
    expect(formatModelLabel(null)).toBeNull();
  });

  it('**モデル名が空なら null**（見出しだけが出るのを防ぐ）', () => {
    expect(formatModelLabel(info({ model: '' }))).toBeNull();
    expect(formatModelLabel(info({ model: '   ' }))).toBeNull();
  });

  it('前後の空白を落とす', () => {
    expect(formatModelLabel(info({ model: '  llama3.2:latest  ' }))).toBe('llama3.2:latest');
  });

  it('heavy_model が未設定なら併記しない（既定の状態）', () => {
    expect(formatModelLabel(info({ heavy_model: '' }))).toBe('gemma4:12b-mlx');
  });

  it('heavy_model が model と同じなら併記しない', () => {
    const label = formatModelLabel(
      info({ model: 'llama3.2:latest', heavy_model: 'llama3.2:latest' }),
    );
    expect(label).toBe('llama3.2:latest');
  });

  it('**heavy_model が異なるときは併記する**（実挙動について嘘をつかない）', () => {
    const label = formatModelLabel(
      info({ model: 'gemma4:26b-mlx', heavy_model: 'gemma4:12b-mlx' }),
    );
    expect(label).toBe('gemma4:26b-mlx（論理層: gemma4:12b-mlx）');
  });

  it('heavy_model が空白だけなら未設定として扱う', () => {
    expect(formatModelLabel(info({ heavy_model: '   ' }))).toBe('gemma4:12b-mlx');
  });
});

describe('MODEL_LABEL_PREFIX', () => {
  it('見出しは「利用モデル名：」', () => {
    expect(MODEL_LABEL_PREFIX).toBe('利用モデル名：');
  });
});

describe('defaultOptionLabel', () => {
  it('既定モデル名が分かるなら**名前まで出す**', () => {
    expect(defaultOptionLabel('gemma4:12b-mlx')).toBe('（既定値: gemma4:12b-mlx）');
  });

  it('前後の空白は落とす', () => {
    expect(defaultOptionLabel('  llama3.2:latest  ')).toBe('（既定値: llama3.2:latest）');
  });

  it('未取得（空文字）なら「（既定値）」のまま', () => {
    expect(defaultOptionLabel('')).toBe(DEFAULT_OPTION_FALLBACK);
    expect(defaultOptionLabel('   ')).toBe(DEFAULT_OPTION_FALLBACK);
  });

  it('フロントに既定モデル名を持たない（値は必ず引数から来る）', () => {
    // 引数以外の出どころがあると、設定を変えたときに画面が嘘をつく
    expect(DEFAULT_OPTION_FALLBACK).not.toMatch(/gemma|llama|qwen|claude|gpt/);
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

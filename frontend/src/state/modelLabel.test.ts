import { describe, expect, it } from 'vitest';

import {
  DEFAULT_OPTION_FALLBACK,
  MODEL_LABEL_PREFIX,
  defaultOptionLabel,
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

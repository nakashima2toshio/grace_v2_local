import { describe, expect, it } from 'vitest';
import {
  INITIAL_HEADER_MODELS,
  headerModelOptions,
  headerSelectValue,
  headerSlots,
  heavyModelNote,
} from './headerModel';
import type { ModelChoice, ModelInfo } from '../types';

const choice = (id: string, notes = '', supports_tool_calls = true): ModelChoice => ({
  id,
  supports_tool_calls,
  notes,
});

const info: ModelInfo = {
  provider: 'ollama',
  model: 'gemma4:12b-mlx',
  light_model: 'gemma4:12b-mlx',
  heavy_model: '',
};

describe('headerSlots', () => {
  it.each(['basic', 'support', 'review'] as const)(
    'エージェントのタブ（%s）はセレクタ 1 つ・既定は model',
    (tab) => {
      expect(headerSlots(tab, info)).toEqual([
        { slot: tab, label: '利用モデル名：', defaultModel: 'gemma4:12b-mlx', showHeavy: true },
      ]);
    },
  );

  it('データ管理タブは工程ごとに 2 つ（既定はどちらも model）', () => {
    expect(headerSlots('data', info)).toEqual([
      { slot: 'chunking', label: '① チャンキング：', defaultModel: 'gemma4:12b-mlx', showHeavy: false },
      { slot: 'qa', label: '② Q/A 作成：', defaultModel: 'gemma4:12b-mlx', showHeavy: false },
    ]);
  });

  it('既定モデルが未取得なら既定値は空文字（セレクタ自体は出す）', () => {
    expect(headerSlots('data', null).map((s) => s.defaultModel)).toEqual(['', '']);
    expect(headerSlots('basic', null)[0].defaultModel).toBe('');
  });
});

describe('INITIAL_HEADER_MODELS', () => {
  it('初期状態はすべて未選択（= サーバーの既定値）', () => {
    expect(INITIAL_HEADER_MODELS).toEqual({
      basic: '',
      support: '',
      review: '',
      chunking: '',
      qa: '',
    });
  });
});

describe('headerSelectValue', () => {
  it('未選択ならサーバーの既定モデル名を表示する（空欄にしない）', () => {
    expect(headerSelectValue('', 'gemma4:12b-mlx')).toBe('gemma4:12b-mlx');
  });

  it('選んだモデルがあればそれを表示する', () => {
    expect(headerSelectValue('llama3.2:latest', 'gemma4:12b-mlx')).toBe('llama3.2:latest');
  });

  it('既定値も未取得なら空文字', () => {
    expect(headerSelectValue('', '')).toBe('');
  });
});

describe('headerModelOptions', () => {
  const models = [choice('gemma4:12b-mlx', 'デフォルト'), choice('llama3.2:latest')];

  it('一覧の順に modelOptionLabel のラベルを付ける', () => {
    expect(headerModelOptions(models, 'gemma4:12b-mlx')).toEqual([
      { id: 'gemma4:12b-mlx', label: 'gemma4:12b-mlx — デフォルト' },
      { id: 'llama3.2:latest', label: 'llama3.2:latest' },
    ]);
  });

  it('既定モデルが一覧に無ければ先頭に足す（表示が別モデルへずれない）', () => {
    const options = headerModelOptions(models, 'gemma4:26b-mlx');
    expect(options[0]).toEqual({ id: 'gemma4:26b-mlx', label: 'gemma4:26b-mlx' });
    expect(options).toHaveLength(3);
  });

  it('既定モデルが未取得なら足さない', () => {
    expect(headerModelOptions(models, '')).toHaveLength(2);
  });

  it('一覧が取れなくても既定モデルだけは出す', () => {
    expect(headerModelOptions([], 'gemma4:12b-mlx')).toEqual([
      { id: 'gemma4:12b-mlx', label: 'gemma4:12b-mlx' },
    ]);
  });
});

describe('heavyModelNote', () => {
  it('論理層のモデルが無ければ注記しない', () => {
    expect(heavyModelNote('gemma4:12b-mlx', '')).toBeNull();
  });

  it('同じモデルなら注記しない', () => {
    expect(heavyModelNote('gemma4:12b-mlx', 'gemma4:12b-mlx')).toBeNull();
  });

  it('別モデルなら注記する（隠すとヘッダーが実挙動について嘘をつく）', () => {
    expect(heavyModelNote('gemma4:12b-mlx', 'gemma4:26b-mlx')).toBe('（論理層: gemma4:26b-mlx）');
  });
});

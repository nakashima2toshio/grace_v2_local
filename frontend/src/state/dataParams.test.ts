import { describe, expect, it } from 'vitest';

import {
  buildChunkingParams,
  buildQaParams,
  buildRegisterParams,
  canSubmitChunking,
  canSubmitQa,
  canSubmitRegister,
  fileOptionLabel,
  formatFileSize,
  formatModified,
  modelOverride,
  suggestCollectionName,
  toOptionalNumber,
  toOptionalString,
  type ChunkingFormState,
  type QaFormState,
  type RegisterFormState,
} from './dataParams';

const chunkingBase: ChunkingFormState = {
  inputFile: 'OUTPUT/cc_news.csv',
  outputDir: 'output_chunked',
  model: 'gemma4:12b-mlx',
  workers: 8,
  blockSize: 1000,
  textColumn: '',
  maxRows: '',
  combineRows: false,
  resume: '',
  verbose: false,
};

const registerBase: RegisterFormState = {
  inputFile: 'qa_output/cc_news_qa.csv',
  collection: 'cc_news_qa',
  recreate: false,
  batchSize: 100,
  embedWorkers: 2,
  textCol: '',
  domain: '',
  maxDocs: '',
  verbose: false,
};

describe('toOptionalNumber', () => {
  it('空欄は null になる（0 にしない）', () => {
    // Number('') は 0 になる。そのまま送ると「最大 0 件」という指定になってしまう
    expect(toOptionalNumber('')).toBeNull();
    expect(toOptionalNumber('   ')).toBeNull();
  });

  it('数値文字列は数値になる', () => {
    expect(toOptionalNumber('100')).toBe(100);
    expect(toOptionalNumber(' 42 ')).toBe(42);
  });

  it('数値にならない文字列は null', () => {
    expect(toOptionalNumber('abc')).toBeNull();
  });
});

describe('toOptionalString', () => {
  it('空文字と空白のみは null', () => {
    expect(toOptionalString('')).toBeNull();
    expect(toOptionalString('   ')).toBeNull();
  });

  it('前後の空白を落とす', () => {
    expect(toOptionalString('  question  ')).toBe('question');
  });
});

describe('buildChunkingParams', () => {
  it('既定値でパラメータを組み立てる', () => {
    const params = buildChunkingParams(chunkingBase);
    expect(params.input_file).toBe('OUTPUT/cc_news.csv');
    expect(params.output_dir).toBe('output_chunked');
    expect(params.text_column).toBeNull();
    expect(params.max_rows).toBeNull();
    expect(params.resume).toBeNull();
  });

  it('入力ファイルの前後空白を落とす', () => {
    const params = buildChunkingParams({ ...chunkingBase, inputFile: '  OUTPUT/a.csv  ' });
    expect(params.input_file).toBe('OUTPUT/a.csv');
  });

  it('出力ディレクトリが空なら既定へ戻す', () => {
    const params = buildChunkingParams({ ...chunkingBase, outputDir: '   ' });
    expect(params.output_dir).toBe('output_chunked');
  });

  it('省略可能な項目を指定できる', () => {
    const params = buildChunkingParams({
      ...chunkingBase,
      textColumn: 'Text',
      maxRows: '500',
      combineRows: true,
      resume: 'job123',
    });
    expect(params.text_column).toBe('Text');
    expect(params.max_rows).toBe(500);
    expect(params.combine_rows).toBe(true);
    expect(params.resume).toBe('job123');
  });
});

describe('buildRegisterParams', () => {
  it('既定値でパラメータを組み立てる', () => {
    const params = buildRegisterParams(registerBase);
    expect(params.collection).toBe('cc_news_qa');
    expect(params.recreate).toBe(false);
    expect(params.max_docs).toBeNull();
  });

  it('Embedding プロバイダは gemini 固定', () => {
    // CLAUDE.md のプロバイダ方針: Embedding は Gemini、LLM は Anthropic
    const params = buildRegisterParams(registerBase);
    expect(params.provider).toBe('gemini');
  });

  it('recreate を渡せる（バックエンドで承認が要る）', () => {
    const params = buildRegisterParams({ ...registerBase, recreate: true });
    expect(params.recreate).toBe(true);
  });

  it('コレクション名の前後空白を落とす', () => {
    const params = buildRegisterParams({ ...registerBase, collection: '  c  ' });
    expect(params.collection).toBe('c');
  });
});

describe('canSubmitChunking', () => {
  it('入力ファイルが空なら送信できない', () => {
    expect(canSubmitChunking({ ...chunkingBase, inputFile: '' }, false)).toBe(false);
    expect(canSubmitChunking({ ...chunkingBase, inputFile: '   ' }, false)).toBe(false);
  });

  it('実行中は送信できない（二重送信の防止）', () => {
    expect(canSubmitChunking(chunkingBase, true)).toBe(false);
  });

  it('入力があり実行中でなければ送信できる', () => {
    expect(canSubmitChunking(chunkingBase, false)).toBe(true);
  });
});

describe('canSubmitRegister', () => {
  it('コレクション名が空なら送信できない', () => {
    expect(canSubmitRegister({ ...registerBase, collection: '' }, false)).toBe(false);
  });

  it('入力ファイルが空なら送信できない', () => {
    expect(canSubmitRegister({ ...registerBase, inputFile: '' }, false)).toBe(false);
  });

  it('両方揃えば送信できる', () => {
    expect(canSubmitRegister(registerBase, false)).toBe(true);
  });
});

describe('suggestCollectionName', () => {
  it('ディレクトリと拡張子を落とす', () => {
    expect(suggestCollectionName('qa_output/cc_news_1per_qa.csv')).toBe('cc_news_1per_qa');
  });

  it('サフィックスを勝手に付けない（命名規約はユーザーが決める）', () => {
    expect(suggestCollectionName('qa_output/faq.csv')).not.toContain('_anthropic');
  });

  it('空文字でも落ちない', () => {
    expect(suggestCollectionName('')).toBe('');
  });
});

describe('formatFileSize', () => {
  it('単位を切り替える', () => {
    expect(formatFileSize(512)).toBe('512 B');
    expect(formatFileSize(2048)).toBe('2.0 KB');
    expect(formatFileSize(5 * 1024 * 1024)).toBe('5.0 MB');
  });
});

describe('formatModified', () => {
  it('不正な値でも落ちない', () => {
    expect(formatModified(Number.NaN)).toBe('-');
  });

  it('epoch 秒を文字列にする', () => {
    // Python の st_mtime は「秒」。ミリ秒として扱うと 1970 年になる
    const result = formatModified(1_700_000_000);
    expect(result).toContain('2023');
  });
});

describe('fileOptionLabel', () => {
  it('ファイル名とサイズを並べる', () => {
    const label = fileOptionLabel({
      name: 'a.csv',
      path: 'OUTPUT/a.csv',
      size: 2048,
      modified: 1_700_000_000,
      suffix: '.csv',
    });
    expect(label).toBe('a.csv（2.0 KB）');
  });
});


const qaBase: QaFormState = {
  inputFile: 'output_chunked/cc_news_chunks.csv',
  outputDir: 'qa_output/pipeline',
  model: '',
  maxDocs: '',
  useCelery: false,
  concurrency: 8,
  batchChunks: 3,
  analyzeCoverage: true,
  verbose: false,
};

describe('modelOverride', () => {
  it('空欄なら model キーごと落とす', () => {
    // ⚠️ 空文字を送るとサーバー側の既定値解決（default_factory /
    //    get_default_ollama_model）が働かず、空のモデル名で LLM を呼びに行く
    expect(modelOverride('')).toEqual({});
    expect(modelOverride('   ')).toEqual({});
  });

  it('指定があれば trim して渡す', () => {
    expect(modelOverride('  gemma4:12b-mlx ')).toEqual({ model: 'gemma4:12b-mlx' });
  });
});

describe('buildQaParams', () => {
  it('（既定値）を選んだときは model を送らない', () => {
    const params = buildQaParams(qaBase);
    expect('model' in params).toBe(false);
  });

  it('モデルを選んだときだけ model が入る', () => {
    const params = buildQaParams({ ...qaBase, model: 'gemma4:26b-mlx' });
    expect(params.model).toBe('gemma4:26b-mlx');
  });

  it('出力ディレクトリが空なら既定値へ倒す', () => {
    expect(buildQaParams({ ...qaBase, outputDir: '   ' }).output_dir).toBe('qa_output/pipeline');
  });

  it('最大チャンク数の空欄は null（0 にしない）', () => {
    expect(buildQaParams(qaBase).max_docs).toBeNull();
    expect(buildQaParams({ ...qaBase, maxDocs: '50' }).max_docs).toBe(50);
  });

  it('Celery とカバレージのフラグをそのまま渡す', () => {
    const params = buildQaParams({ ...qaBase, useCelery: true, analyzeCoverage: false });
    expect(params.use_celery).toBe(true);
    expect(params.analyze_coverage).toBe(false);
    expect(params.concurrency).toBe(8);
    expect(params.batch_chunks).toBe(3);
  });

  it('入力ファイルは trim する', () => {
    expect(buildQaParams({ ...qaBase, inputFile: '  a/b.csv  ' }).input_file).toBe('a/b.csv');
  });
});

describe('canSubmitQa', () => {
  it('入力ファイルが要る（コレクション名は不要）', () => {
    expect(canSubmitQa(qaBase, false)).toBe(true);
    expect(canSubmitQa({ ...qaBase, inputFile: '' }, false)).toBe(false);
    expect(canSubmitQa({ ...qaBase, inputFile: '   ' }, false)).toBe(false);
  });

  it('実行中は押せない', () => {
    expect(canSubmitQa(qaBase, true)).toBe(false);
  });
});

describe('buildChunkingParams（モデル省略）', () => {
  it('（既定値）を選んだときは model を送らない', () => {
    const params = buildChunkingParams({ ...chunkingBase, model: '' });
    expect('model' in params).toBe(false);
  });
});

// データ準備フォームの入力 → API パラメータ を組み立てる純関数群。
//
// `queryParams.ts` と同じ方針で、**JSX から切り出してテスト可能にする**。
// 数値の空欄・トリム・null 化の扱いはここに集約する（フォーム側で散らさない）。
import type { ChunkingParams, InputFileInfo, RegisterParams } from '../types';

// 入力ファイルのブラウズ先。backend の ALLOWED_INPUT_DIRS と 1:1。
// パイプラインの流れ順に並べる（生データ → チャンク → Q/A）。
export const INPUT_DIRS = ['OUTPUT', 'output_chunked', 'qa_output', 'datasets'] as const;
export type InputDir = (typeof INPUT_DIRS)[number];

export const INPUT_DIR_LABELS: Record<string, string> = {
  OUTPUT: 'OUTPUT（生データ）',
  output_chunked: 'output_chunked（チャンク済み）',
  qa_output: 'qa_output（Q/A 生成済み）',
  datasets: 'datasets（ダウンロード）',
};

/**
 * 空欄の数値入力を null にする。
 *
 * `<input type="number">` は空欄のとき `''` を返す。`Number('')` は **0** に
 * なってしまうため、そのまま送ると「最大 0 件」という意図しない指定になる。
 */
export function toOptionalNumber(value: string): number | null {
  const trimmed = value.trim();
  if (trimmed === '') return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

/** 空文字を null にする（省略可能な文字列パラメータ用）。 */
export function toOptionalString(value: string): string | null {
  const trimmed = value.trim();
  return trimmed === '' ? null : trimmed;
}

export interface ChunkingFormState {
  inputFile: string;
  outputDir: string;
  model: string;
  workers: number;
  blockSize: number;
  textColumn: string;
  maxRows: string;
  combineRows: boolean;
  resume: string;
  verbose: boolean;
}

export function buildChunkingParams(state: ChunkingFormState): ChunkingParams {
  return {
    input_file: state.inputFile.trim(),
    output_dir: state.outputDir.trim() || 'output_chunked',
    model: state.model,
    workers: state.workers,
    block_size: state.blockSize,
    text_column: toOptionalString(state.textColumn),
    max_rows: toOptionalNumber(state.maxRows),
    combine_rows: state.combineRows,
    resume: toOptionalString(state.resume),
    verbose: state.verbose,
  };
}

export interface RegisterFormState {
  inputFile: string;
  collection: string;
  recreate: boolean;
  batchSize: number;
  embedWorkers: number;
  textCol: string;
  domain: string;
  maxDocs: string;
  verbose: boolean;
}

export function buildRegisterParams(state: RegisterFormState): RegisterParams {
  return {
    input_file: state.inputFile.trim(),
    collection: state.collection.trim(),
    recreate: state.recreate,
    batch_size: state.batchSize,
    embed_workers: state.embedWorkers,
    text_col: toOptionalString(state.textCol),
    domain: toOptionalString(state.domain),
    max_docs: toOptionalNumber(state.maxDocs),
    // Embedding は Gemini 固定（CLAUDE.md のプロバイダ方針。LLM 用途とは別系統）
    provider: 'gemini',
    normalize_filename: true,
    create_ui_csv: true,
    ui_output_dir: 'qa_output',
    verbose: state.verbose,
  };
}

/** 送信ボタンを押せるか（チャンク化）。 */
export function canSubmitChunking(state: ChunkingFormState, running: boolean): boolean {
  return !running && state.inputFile.trim() !== '';
}

/** 送信ボタンを押せるか（登録）。入力ファイルとコレクション名の両方が要る。 */
export function canSubmitRegister(state: RegisterFormState, running: boolean): boolean {
  return !running && state.inputFile.trim() !== '' && state.collection.trim() !== '';
}

/**
 * 入力ファイル名からコレクション名の既定値を作る。
 *
 * `qa_output/cc_news_1per_qa.csv` → `cc_news_1per_qa`
 * 拡張子とディレクトリを落とすだけ。**サフィックス（_anthropic 等）は付けない**
 * （命名規約はプロジェクトによって違うため、ユーザーに決めさせる）。
 */
export function suggestCollectionName(inputFile: string): string {
  const fileName = inputFile.split('/').pop() ?? '';
  return fileName.replace(/\.[^.]+$/, '');
}

/** バイト数を人間が読める形にする。 */
export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** UNIX epoch 秒（Python の st_mtime）を表示用の文字列にする。 */
export function formatModified(epochSeconds: number): string {
  const date = new Date(epochSeconds * 1000);
  if (Number.isNaN(date.getTime())) return '-';
  return date.toLocaleString('ja-JP', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/** ファイル選択セレクタの表示ラベル。 */
export function fileOptionLabel(file: InputFileInfo): string {
  return `${file.name}（${formatFileSize(file.size)}）`;
}

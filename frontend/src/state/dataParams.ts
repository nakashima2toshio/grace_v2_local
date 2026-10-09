// データ準備フォームの入力 → API パラメータ を組み立てる純関数群。
//
// `queryParams.ts` と同じ方針で、**JSX から切り出してテスト可能にする**。
// 数値の空欄・トリム・null 化の扱いはここに集約する（フォーム側で散らさない）。
import type { ChunkingParams, InputFileInfo, QaParams, RegisterParams } from '../types';

// 入力ファイルのブラウズ先。backend の ALLOWED_INPUT_DIRS と 1:1。
// パイプラインの流れ順に並べる（生データ → チャンク → Q/A）。
export const INPUT_DIRS = ['OUTPUT', 'output_chunked', 'qa_output', 'datasets'] as const;
export type InputDir = (typeof INPUT_DIRS)[number];

// チャンク化の既定の並列ワーカー数。backend の
// `config.py::get_default_chunking_workers()` と同じ値を持つ。
//
// ⚠️ **8 に戻さないこと。** Ollama は既定で 1 本ずつしか処理しないため、
// 8 本投げても 7 本はキューで待つだけで、待ち時間が各リクエストの
// タイムアウトを食いつぶす。実測（2026-09-11 / gemma4:12b-mlx / 55 ブロック）
// では 509 秒で 7 ブロックしか進まず、単発の 62.7 秒/ブロックと変わらないまま
// 後続がタイムアウトして機械的分割のフォールバックへ落ちた。
//
// 上げてよいのは `OLLAMA_NUM_PARALLEL` を上げて ollama serve を再起動した
// ときだけ（スロットごとの KV キャッシュ分だけメモリが増える）。
export const DEFAULT_CHUNKING_WORKERS = 1;

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

/**
 * モデル指定を「上書きするときだけ」オブジェクトへ足すヘルパ。
 *
 * ヘッダーのモデルセレクタが未選択のときは空文字が来る。空文字をそのまま送ると
 * サーバー側の既定値解決が働かないため、**キーごと省略する**。
 */
export function modelOverride(model: string): { model?: string } {
  const trimmed = model.trim();
  return trimmed === '' ? {} : { model: trimmed };
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

/** チャンク化の出力先ディレクトリの既定（`buildChunkingParams` と同じ）。 */
export const DEFAULT_CHUNKING_OUTPUT_DIR = 'output_chunked';

/** チャンク化ジョブが書き出す 2 ファイルのパス。 */
export interface ChunkingOutputFiles {
  /** メタデータ付き CSV（`<入力の stem>_chunks.csv`）。Q/A 作成の入力になる。 */
  main: string;
  /** Text 列だけの簡易 CSV（`<入力の stem>_chunks_simple.csv`）。 */
  simple: string;
}

/**
 * チャンク化の出力ファイル名を、実行前に画面へ出すために求める。
 *
 * ⚠️ **バックエンドと同じ規則で作ること。** 出力名を決めるのは
 * `chunking/csv_text_to_chunks_text_csv.py` の 2 か所で、ここはその写しである。
 *   - `generate_output_filename()`: `os.path.join(output_dir, Path(input_file).stem + "_chunks.csv")`
 *   - `save_chunks_as_csv(save_simple_csv=True)`: 同じ場所に `<stem>_chunks_simple.csv`
 * 規則を変えるときは両方を直す（画面の表示と実際のファイルが食い違う）。
 *
 * `stem` は Python の `PurePath.stem` と同じく、**最後の 1 つ**の拡張子だけを落とす
 * （先頭のドットや末尾のドットは拡張子とみなさない）。
 *
 * @returns 入力ファイルが未選択なら `null`
 */
export function chunkingOutputFiles(
  inputFile: string,
  outputDir: string,
): ChunkingOutputFiles | null {
  const name = inputFile.trim().split('/').pop() ?? '';
  if (name === '') return null;
  const dot = name.lastIndexOf('.');
  const stem = dot > 0 && dot < name.length - 1 ? name.slice(0, dot) : name;
  const dir = outputDir.trim() || DEFAULT_CHUNKING_OUTPUT_DIR;
  const prefix = dir.endsWith('/') ? dir : `${dir}/`;
  return {
    main: `${prefix}${stem}_chunks.csv`,
    simple: `${prefix}${stem}_chunks_simple.csv`,
  };
}

export function buildChunkingParams(state: ChunkingFormState): ChunkingParams {
  return {
    input_file: state.inputFile.trim(),
    output_dir: state.outputDir.trim() || DEFAULT_CHUNKING_OUTPUT_DIR,
    // ⚠️ **空欄なら `model` キーごと落とす。**
    // 空文字を送るとサーバーの既定値（`default_factory=get_default_ollama_model`）が
    // 働かず、空のモデル名でローカル LLM を呼びに行ってしまう。
    ...modelOverride(state.model),
    workers: state.workers,
    block_size: state.blockSize,
    text_column: toOptionalString(state.textColumn),
    max_rows: toOptionalNumber(state.maxRows),
    combine_rows: state.combineRows,
    resume: toOptionalString(state.resume),
    verbose: state.verbose,
  };
}

export interface QaFormState {
  inputFile: string;
  outputDir: string;
  model: string;
  maxDocs: string;
  useCelery: boolean;
  /** 表示用（起動コマンドとログ）。実際の並列数はワーカー起動時の -c で決まる。 */
  concurrency: number;
  analyzeCoverage: boolean;
  verbose: boolean;
}

export function buildQaParams(state: QaFormState): QaParams {
  return {
    input_file: state.inputFile.trim(),
    // ⚠️ 既定は `qa_output` 直下。入れ子にすると GET /api/files が拾わず、
    // 「③ Qdrant 登録」の選択肢に出てこない（backend の既定と揃える）
    output_dir: state.outputDir.trim() || 'qa_output',
    // 空欄なら `model` キーごと落とす（チャンク化と同じ理由）。既定値は
    // config.py::get_default_ollama_model() の 1 箇所で管理する
    ...modelOverride(state.model),
    max_docs: toOptionalNumber(state.maxDocs),
    use_celery: state.useCelery,
    concurrency: state.concurrency,
    analyze_coverage: state.analyzeCoverage,
    verbose: state.verbose,
  };
}

/** 送信ボタンを押せるか（Q/A 生成）。 */
export function canSubmitQa(state: QaFormState, running: boolean): boolean {
  return !running && state.inputFile.trim() !== '';
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

// チャンキング / Qdrant 登録の実行パネル。
//
// `SupportPanel` と同じ構造（フォーム → ジョブ起動 → SSE 購読 → Timeline → 結果）。
// 2 つのジョブで共通なのはこの器で、フォームの中身だけ `variant` で切り替える。
//
// ⚠️ 登録で `recreate: true` を選ぶと intervention が飛んでくる。
// 承認 UI は Support / Review と同じ `ConfirmModal` を使う。
//
// タブを離れるとアンマウントされて SSE 購読が切れるが、`activeJobs` に
// `job_id` を残しておき、再マウント時に購読し直す。バックエンドの
// `stream_events()` は常にイベントを先頭からリプレイするため、
// 再購読するだけでタイムラインごと復元される。
import { useCallback, useEffect, useReducer, useRef, useState } from 'react';

import {
  confirmDataIntervention,
  fetchDataJobStatus,
  fetchInputFiles,
  startChunking,
  startRegister,
  subscribeStream,
} from '../api/client';
import { forgetJob, recallJob, rememberJob } from '../state/activeJobs';
import {
  buildChunkingParams,
  buildRegisterParams,
  canSubmitChunking,
  canSubmitRegister,
  fileOptionLabel,
  formatModified,
  INPUT_DIR_LABELS,
  INPUT_DIRS,
  suggestCollectionName,
  type ChunkingFormState,
  type RegisterFormState,
} from '../state/dataParams';
import { dataReducer, initialDataState, stepIdsFor, stepLabelsFor } from '../state/dataReducer';
import type { DataJobKind, InputFileInfo } from '../types';
import { ConfirmModal } from './ConfirmModal';
import { Timeline } from './Timeline';

export type DataJobVariant = 'chunking' | 'register';

/** バリアントごとの既定入力ディレクトリ（パイプラインの流れに沿う）。 */
const DEFAULT_DIR: Record<DataJobVariant, string> = {
  chunking: 'OUTPUT',
  register: 'qa_output',
};

export function DataJobPanel({ variant }: { variant: DataJobVariant }) {
  const kind: DataJobKind = variant;

  // --- ファイル選択 ---------------------------------------------------------
  const [dir, setDir] = useState<string>(DEFAULT_DIR[variant]);
  const [files, setFiles] = useState<InputFileInfo[]>([]);
  const [inputFile, setInputFile] = useState('');

  // --- チャンキング用 -------------------------------------------------------
  const [outputDir, setOutputDir] = useState('output_chunked');
  const [model, setModel] = useState('claude-haiku-4-5');
  const [workers, setWorkers] = useState(8);
  const [blockSize, setBlockSize] = useState(1000);
  const [textColumn, setTextColumn] = useState('');
  const [maxRows, setMaxRows] = useState('');
  const [combineRows, setCombineRows] = useState(false);

  // --- 登録用 ---------------------------------------------------------------
  const [collection, setCollection] = useState('');
  const [recreate, setRecreate] = useState(false);
  const [batchSize, setBatchSize] = useState(100);
  const [embedWorkers, setEmbedWorkers] = useState(2);
  const [maxDocs, setMaxDocs] = useState('');

  const [verbose, setVerbose] = useState(false);

  const [state, dispatch] = useReducer(dataReducer, kind, initialDataState);
  const [confirming, setConfirming] = useState(false);
  const unsubscribeRef = useRef<(() => void) | null>(null);

  // ディレクトリを変えたらファイル一覧を取り直す。
  // 早期 return でも必ずクリーンアップを返す（SSE の解除漏れ防止）
  useEffect(() => {
    let cancelled = false;
    void fetchInputFiles(dir)
      .then((response) => {
        if (!cancelled) setFiles(response.files);
      })
      .catch(() => {
        if (!cancelled) setFiles([]);
      });
    return () => {
      cancelled = true;
    };
  }, [dir]);

  // 購読を張り直す共通処理。起動直後と再マウント時の両方から使う
  const subscribe = useCallback(
    (jobId: string) => {
      unsubscribeRef.current?.();
      unsubscribeRef.current = subscribeStream(
        jobId,
        (e) => dispatch({ type: 'event', event: e }),
        (message) => dispatch({ type: 'failed', message }),
        'data',
      );
    },
    [],
  );

  // 再マウント時、前回のジョブがまだ生きていれば購読し直す。
  // **SSE へ直接つなぐ前に存在確認する** — 完了ジョブは 50 件で GC されるため、
  // 消えた job_id に EventSource でつなぐと onerror が「切断されました」という
  // 誤ったエラーになる。
  useEffect(() => {
    const remembered = recallJob(kind);
    if (!remembered) return () => unsubscribeRef.current?.();

    let cancelled = false;
    void fetchDataJobStatus(remembered)
      .then(() => {
        if (cancelled) return;
        dispatch({ type: 'started', jobId: remembered, kind });
        subscribe(remembered);
      })
      .catch(() => {
        // 404（GC 済み・サーバ再起動）。黙って忘れて初期状態から始める
        if (!cancelled) forgetJob(kind);
      });

    return () => {
      cancelled = true;
      unsubscribeRef.current?.();
    };
  }, [kind, subscribe]);

  const chunkingState: ChunkingFormState = {
    inputFile,
    outputDir,
    model,
    workers,
    blockSize,
    textColumn,
    maxRows,
    combineRows,
    resume: '',
    verbose,
  };

  const registerState: RegisterFormState = {
    inputFile,
    collection,
    recreate,
    batchSize,
    embedWorkers,
    textCol: '',
    domain: '',
    maxDocs,
    verbose,
  };

  const running = state.phase === 'running';
  const canSubmit =
    variant === 'chunking'
      ? canSubmitChunking(chunkingState, running)
      : canSubmitRegister(registerState, running);

  const submit = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      if (!canSubmit) return;
      unsubscribeRef.current?.();
      try {
        const { job_id } =
          variant === 'chunking'
            ? await startChunking(buildChunkingParams(chunkingState))
            : await startRegister(buildRegisterParams(registerState));
        rememberJob(kind, job_id);
        dispatch({ type: 'started', jobId: job_id, kind });
        subscribe(job_id);
      } catch (error) {
        dispatch({
          type: 'failed',
          message: error instanceof Error ? error.message : String(error),
        });
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [canSubmit, variant, kind, chunkingState, registerState, subscribe],
  );

  const respond = useCallback(
    async (approve: boolean) => {
      if (!state.jobId || !state.intervention) return;
      setConfirming(true);
      try {
        await confirmDataIntervention(state.jobId, state.intervention.intervention_id, approve);
        dispatch({ type: 'confirm_sent' });
      } catch (error) {
        dispatch({
          type: 'failed',
          message: error instanceof Error ? error.message : String(error),
        });
      } finally {
        setConfirming(false);
      }
    },
    [state.jobId, state.intervention],
  );

  const selectFile = (path: string) => {
    setInputFile(path);
    // 登録タブでは、コレクション名が未入力ならファイル名から補完する
    if (variant === 'register' && collection.trim() === '') {
      setCollection(suggestCollectionName(path));
    }
  };

  return (
    <>
      <form className="query-form" onSubmit={submit}>
        <div className="query-row">
          <label>
            入力ディレクトリ
            <select value={dir} onChange={(e) => setDir(e.target.value)} disabled={running}>
              {INPUT_DIRS.map((d) => (
                <option key={d} value={d}>
                  {INPUT_DIR_LABELS[d] ?? d}
                </option>
              ))}
            </select>
          </label>
          <label className="grow">
            入力ファイル
            <select
              value={inputFile}
              onChange={(e) => selectFile(e.target.value)}
              disabled={running}
            >
              <option value="">— 選択してください —</option>
              {files.map((file) => (
                <option key={file.path} value={file.path}>
                  {fileOptionLabel(file)}
                </option>
              ))}
            </select>
          </label>
        </div>

        {files.length === 0 && (
          <p className="notice">
            {dir} に対象ファイル（.csv / .txt）がありません。ディレクトリを変えてください。
          </p>
        )}
        {inputFile && (
          <p className="file-meta">
            選択中: <code>{inputFile}</code>
            {(() => {
              const file = files.find((f) => f.path === inputFile);
              return file ? `（更新 ${formatModified(file.modified)}）` : '';
            })()}
          </p>
        )}

        {variant === 'chunking' ? (
          <>
            <div className="query-row">
              <label>
                出力ディレクトリ
                <input
                  value={outputDir}
                  onChange={(e) => setOutputDir(e.target.value)}
                  disabled={running}
                />
              </label>
              <label>
                モデル
                <input
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  disabled={running}
                />
              </label>
            </div>
            <div className="query-row">
              <label>
                並列ワーカー数
                <input
                  type="number"
                  min={1}
                  max={32}
                  value={workers}
                  onChange={(e) => setWorkers(Number(e.target.value))}
                  disabled={running}
                />
              </label>
              <label>
                ブロックサイズ（文字）
                <input
                  type="number"
                  min={100}
                  max={8000}
                  value={blockSize}
                  onChange={(e) => setBlockSize(Number(e.target.value))}
                  disabled={running}
                />
              </label>
              <label>
                テキストカラム（CSV）
                <input
                  value={textColumn}
                  onChange={(e) => setTextColumn(e.target.value)}
                  placeholder="自動検出"
                  disabled={running}
                />
              </label>
              <label>
                最大行数
                <input
                  type="number"
                  min={1}
                  value={maxRows}
                  onChange={(e) => setMaxRows(e.target.value)}
                  placeholder="全件"
                  disabled={running}
                />
              </label>
            </div>
            <div className="query-toggles">
              <label>
                <input
                  type="checkbox"
                  checked={combineRows}
                  onChange={(e) => setCombineRows(e.target.checked)}
                  disabled={running}
                />
                CSV 全行を結合する
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={verbose}
                  onChange={(e) => setVerbose(e.target.checked)}
                  disabled={running}
                />
                詳細ログ
              </label>
            </div>
          </>
        ) : (
          <>
            <div className="query-row">
              <label className="grow">
                コレクション名
                <input
                  value={collection}
                  onChange={(e) => setCollection(e.target.value)}
                  placeholder="例: cc_news_1per_qa"
                  disabled={running}
                />
              </label>
              <label>
                バッチサイズ
                <input
                  type="number"
                  min={1}
                  max={1000}
                  value={batchSize}
                  onChange={(e) => setBatchSize(Number(e.target.value))}
                  disabled={running}
                />
              </label>
              <label>
                Embedding 並列数
                <input
                  type="number"
                  min={1}
                  max={16}
                  value={embedWorkers}
                  onChange={(e) => setEmbedWorkers(Number(e.target.value))}
                  disabled={running}
                />
              </label>
              <label>
                最大件数
                <input
                  type="number"
                  min={1}
                  value={maxDocs}
                  onChange={(e) => setMaxDocs(e.target.value)}
                  placeholder="全件"
                  disabled={running}
                />
              </label>
            </div>
            <div className="query-toggles">
              <label className={recreate ? 'danger-toggle' : ''}>
                <input
                  type="checkbox"
                  checked={recreate}
                  onChange={(e) => setRecreate(e.target.checked)}
                  disabled={running}
                />
                既存コレクションを作り直す（recreate）
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={verbose}
                  onChange={(e) => setVerbose(e.target.checked)}
                  disabled={running}
                />
                詳細ログ
              </label>
            </div>
            {recreate && (
              <p className="notice">
                ⚠️ 既存の同名コレクションを削除して作り直します。実行前に承認を求めます。
              </p>
            )}
            <p className="notice">
              Embedding は Gemini（<code>gemini-embedding-001</code>・3072 次元）を使います。
              <code>GOOGLE_API_KEY</code> が必要です。
            </p>
          </>
        )}

        <button type="submit" disabled={!canSubmit}>
          {running ? '実行中…' : variant === 'chunking' ? 'チャンク化を実行' : 'Qdrant へ登録'}
        </button>
      </form>

      {state.error && (
        <div className="error-banner" role="alert">
          {state.error}
        </div>
      )}
      {running && !state.intervention && (
        <div className="running-banner">
          実行中… 進捗は下のタイムラインに逐次表示されます
        </div>
      )}

      {state.phase !== 'idle' && (
        <Timeline
          title="ステップトレース"
          stepIds={stepIdsFor(kind)}
          labels={stepLabelsFor(kind)}
          steps={state.steps}
          logs={state.logs}
          badges={(step) => {
            const badges: string[] = [];
            const data = step.data;
            if (step.status === 'skipped' && typeof data.reason === 'string') {
              badges.push(`スキップ: ${data.reason}`);
            }
            if (step.id === 'load' && step.status === 'done' && typeof data.chars === 'number') {
              badges.push(`${data.chars.toLocaleString()} 文字`);
            }
            if (step.id === 'chunk' && step.status === 'done' && typeof data.chunks === 'number') {
              badges.push(`${data.chunks} チャンク`);
            }
            if (step.id === 'prepare' && step.status === 'done') {
              badges.push(data.exists === true ? '既存コレクション' : '新規作成');
              if (typeof data.existing_points === 'number' && data.existing_points > 0) {
                badges.push(`既存 ${data.existing_points.toLocaleString()} 件`);
              }
            }
            if (step.id === 'confirm' && step.status === 'done') {
              badges.push(data.approved === true ? '承認済み' : '中止');
            }
            if (step.id === 'upsert' && step.status === 'done' && typeof data.points === 'number') {
              badges.push(`登録後 ${data.points.toLocaleString()} 件`);
            }
            return badges;
          }}
        />
      )}

      {state.result && (
        <section className="answer-card answer">
          <div className="answer-header">
            <span className="decision-badge answer">
              {state.result.cancelled ? '中止' : '完了'}
            </span>
          </div>
          {state.result.cancelled ? (
            <p className="notice">実行されませんでした（{state.result.reason}）。</p>
          ) : variant === 'chunking' ? (
            <dl className="metrics">
              <div>
                <dt>生成チャンク数</dt>
                <dd>{state.result.chunks?.toLocaleString() ?? '-'}</dd>
              </div>
              <div>
                <dt>入力文字数</dt>
                <dd>{state.result.chars?.toLocaleString() ?? '-'}</dd>
              </div>
              <div>
                <dt>出力ファイル</dt>
                <dd>
                  <code>{state.result.output_file ?? '-'}</code>
                </dd>
              </div>
            </dl>
          ) : (
            <dl className="metrics">
              <div>
                <dt>コレクション</dt>
                <dd>
                  <code>{state.result.collection ?? '-'}</code>
                </dd>
              </div>
              <div>
                <dt>登録後のポイント数</dt>
                <dd>{state.result.points?.toLocaleString() ?? '-'}</dd>
              </div>
              <div>
                <dt>登録前</dt>
                <dd>{state.result.points_before?.toLocaleString() ?? '0'}</dd>
              </div>
            </dl>
          )}
        </section>
      )}

      {state.intervention && (
        <ConfirmModal
          intervention={state.intervention}
          actionStep={state.steps.confirm}
          submitting={confirming}
          onRespond={respond}
        />
      )}
    </>
  );
}

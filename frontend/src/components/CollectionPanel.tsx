// コレクション管理: 一覧・詳細プレビュー・削除（HITL CONFIRM 経由）。
//
// 削除は不可逆なので、単発の DELETE ではなく**ジョブ + 承認モーダル**を通す。
// 承認 UI は Support / Review と同じ `ConfirmModal` を再利用する。
//
// サブタブを離れるとアンマウントされるが、`activeJobs` に job_id を残して
// 再マウント時に購読し直す（承認待ちのまま見失わないようにするため）。
import { useCallback, useEffect, useReducer, useRef, useState } from 'react';

import {
  confirmDataIntervention,
  fetchCollectionDetail,
  fetchCollectionPoints,
  fetchCollections,
  fetchDataJobStatus,
  fetchQdrantHealth,
  startDelete,
  subscribeStream,
} from '../api/client';
import { forgetJob, recallJob, rememberJob } from '../state/activeJobs';
import { dataReducer, initialDataState, stepIdsFor, stepLabelsFor } from '../state/dataReducer';
import type {
  CollectionDetail,
  CollectionInfo,
  CollectionPoints,
  QdrantHealth,
} from '../types';
import { ConfirmModal } from './ConfirmModal';
import { Timeline } from './Timeline';

export function CollectionPanel() {
  const [health, setHealth] = useState<QdrantHealth | null>(null);
  const [collections, setCollections] = useState<CollectionInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<CollectionDetail | null>(null);
  const [points, setPoints] = useState<CollectionPoints | null>(null);
  const [checked, setChecked] = useState<Set<string>>(new Set());

  const [state, dispatch] = useReducer(dataReducer, 'delete', initialDataState);
  const [confirming, setConfirming] = useState(false);
  const unsubscribeRef = useRef<(() => void) | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const [healthResult, collectionsResult] = await Promise.all([
        fetchQdrantHealth(),
        fetchCollections().catch(() => [] as CollectionInfo[]),
      ]);
      setHealth(healthResult);
      setCollections(collectionsResult);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }, []);

  const subscribe = useCallback((jobId: string) => {
    unsubscribeRef.current?.();
    unsubscribeRef.current = subscribeStream(
      jobId,
      (event) => dispatch({ type: 'event', event }),
      (message) => dispatch({ type: 'failed', message }),
      'data',
    );
  }, []);

  // 初回ロード。SSE の購読解除も忘れずに返す
  useEffect(() => {
    void reload();
    return () => unsubscribeRef.current?.();
  }, [reload]);

  // 再マウント時、前回の削除ジョブがまだ生きていれば購読し直す。
  // **承認待ちのまま離脱して戻ったときに、モーダルを取り戻せるようにする。**
  // SSE へ直接つなぐ前に存在確認するのは、GC 済みの job_id だと onerror が
  // 「切断されました」という誤ったエラーになるため。
  useEffect(() => {
    const remembered = recallJob('delete');
    if (!remembered) return;

    let cancelled = false;
    void fetchDataJobStatus(remembered)
      .then(() => {
        if (cancelled) return;
        dispatch({ type: 'started', jobId: remembered, kind: 'delete' });
        subscribe(remembered);
      })
      .catch(() => {
        if (!cancelled) forgetJob('delete');
      });

    return () => {
      cancelled = true;
    };
  }, [subscribe]);

  // 選択が変わったら詳細とプレビューを取り直す
  useEffect(() => {
    if (!selected) {
      setDetail(null);
      setPoints(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const [d, p] = await Promise.all([
          fetchCollectionDetail(selected),
          fetchCollectionPoints(selected, 20),
        ]);
        // 取得中に選択が変わったら結果を捨てる（古い応答で上書きしない）
        if (cancelled) return;
        setDetail(d);
        setPoints(p);
      } catch {
        if (!cancelled) {
          setDetail(null);
          setPoints(null);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selected]);

  const toggleChecked = (name: string) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const runDelete = useCallback(async () => {
    const targets = Array.from(checked);
    if (targets.length === 0) return;
    unsubscribeRef.current?.();
    try {
      const { job_id } = await startDelete(targets);
      rememberJob('delete', job_id);
      dispatch({ type: 'started', jobId: job_id, kind: 'delete' });
      subscribe(job_id);
    } catch (error) {
      dispatch({
        type: 'failed',
        message: error instanceof Error ? error.message : String(error),
      });
    }
  }, [checked, subscribe]);

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

  // 削除完了後は一覧を取り直し、選択とチェックを解除する
  useEffect(() => {
    if (state.phase !== 'completed' || !state.result) return;
    // 完了したら記憶を捨てる。残すと、GC 後に戻ったとき無駄な問い合わせが走る
    forgetJob('delete');
    // 中止（承認を拒否）なら何も変わっていないので一覧を触らない
    if (state.result.cancelled) return;
    setChecked(new Set());
    setSelected(null);
    void reload();
  }, [state.phase, state.result, reload]);

  const running = state.phase === 'running';

  return (
    <>
      <section className="collection-toolbar">
        <button type="button" onClick={() => void reload()} disabled={loading || running}>
          {loading ? '読み込み中…' : '再読み込み'}
        </button>
        <button
          type="button"
          className="danger"
          onClick={() => void runDelete()}
          disabled={checked.size === 0 || running}
          title="削除には承認が必要です"
        >
          選択した {checked.size} 件を削除
        </button>
        {health && (
          <span className={health.available ? 'health-ok' : 'health-ng'}>
            Qdrant: {health.available ? '稼働中' : '停止'}
            {health.url ? `（${health.url}）` : ''}
          </span>
        )}
      </section>

      {health && !health.available && (
        <div className="warn-banner" role="alert">
          ⚠️ Qdrant に接続できません（{health.message}）。<br />
          <code>docker-compose -f docker-compose/docker-compose.yml up -d</code> で起動してください。
        </div>
      )}
      {loadError && (
        <div className="error-banner" role="alert">
          {loadError}
        </div>
      )}
      {state.error && (
        <div className="error-banner" role="alert">
          {state.error}
        </div>
      )}

      <section className="collection-list">
        <h2>コレクション（{collections.length}）</h2>
        {collections.length === 0 ? (
          <p className="finding-empty">
            コレクションがありません。「Qdrant 登録」タブから登録してください。
          </p>
        ) : (
          <table className="collection-table">
            <thead>
              <tr>
                <th scope="col">選択</th>
                <th scope="col">コレクション名</th>
                <th scope="col">ポイント数</th>
                <th scope="col">状態</th>
              </tr>
            </thead>
            <tbody>
              {collections.map((collection) => (
                <tr
                  key={collection.name}
                  className={selected === collection.name ? 'selected' : ''}
                >
                  <td>
                    <input
                      type="checkbox"
                      checked={checked.has(collection.name)}
                      onChange={() => toggleChecked(collection.name)}
                      aria-label={`${collection.name} を削除対象に選ぶ`}
                      disabled={running}
                    />
                  </td>
                  <td>
                    <button
                      type="button"
                      className="link-button"
                      onClick={() =>
                        setSelected(selected === collection.name ? null : collection.name)
                      }
                    >
                      {collection.name}
                    </button>
                  </td>
                  <td className="num">{collection.points_count.toLocaleString()}</td>
                  <td>{collection.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {detail && (
        <section className="collection-detail">
          <h2>{detail.name} の詳細</h2>
          {detail.error ? (
            <p className="notice">詳細を取得できませんでした: {detail.error}</p>
          ) : (
            <dl className="metrics">
              <div>
                <dt>ポイント数</dt>
                <dd>{detail.points_count.toLocaleString()}</dd>
              </div>
              <div>
                <dt>ベクトル次元</dt>
                <dd>{String(detail.vector_size ?? '-')}</dd>
              </div>
              <div>
                <dt>距離関数</dt>
                <dd>{String(detail.distance ?? '-')}</dd>
              </div>
              <div>
                <dt>状態</dt>
                <dd>{detail.status}</dd>
              </div>
            </dl>
          )}
          {Object.keys(detail.sources).length > 0 && (
            <details className="finding-citations" open>
              <summary>データ元（{Object.keys(detail.sources).length}）</summary>
              <ul>
                {Object.entries(detail.sources).map(([source, stats]) => (
                  <li key={source}>
                    {source} — 推定 {(stats.estimated_total ?? 0).toLocaleString()} 件
                    {typeof stats.percentage === 'number'
                      ? `（${stats.percentage.toFixed(1)}%）`
                      : ''}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </section>
      )}

      {points && points.rows.length > 0 && (
        <section className="collection-points">
          <h2>データプレビュー（先頭 {points.rows.length} 件）</h2>
          {/* 列はコレクションごとに違うので columns の順で描く */}
          <div className="markdown-table-wrap">
            <table>
              <thead>
                <tr>
                  {points.columns.map((column) => (
                    <th key={column} scope="col">
                      {column}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {points.rows.map((row, index) => (
                  <tr key={index}>
                    {points.columns.map((column) => (
                      <td key={column}>{row[column] == null ? '' : String(row[column])}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {state.phase !== 'idle' && (
        <Timeline
          title="削除の進捗"
          stepIds={stepIdsFor('delete')}
          labels={stepLabelsFor('delete')}
          steps={state.steps}
          logs={state.logs}
          badges={(step) => {
            const badges: string[] = [];
            const data = step.data;
            if (step.id === 'inspect' && step.status === 'done') {
              if (typeof data.total_points === 'number') {
                badges.push(`対象 ${data.total_points.toLocaleString()} 件`);
              }
            }
            if (step.id === 'confirm' && step.status === 'done') {
              badges.push(data.approved === true ? '承認済み' : '中止');
            }
            if (step.id === 'delete' && step.status === 'done') {
              const deleted = Array.isArray(data.deleted) ? data.deleted.length : 0;
              badges.push(`削除 ${deleted} 件`);
            }
            return badges;
          }}
        />
      )}

      {state.result?.cancelled && (
        <div className="warn-banner">削除は実行されませんでした（{state.result.reason}）。</div>
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

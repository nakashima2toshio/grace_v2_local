// 問い合わせ → 回答 のタブ本体。**基本版タブと GRACE-Support タブで共用**する。
//
// 両者はまったく同じパイプライン（run_support_agent_core）を通り、違いは
// **業界特化（VerticalProfile）を使うかどうか**だけ。そのため画面を 2 つに
// 複製せず、`variant` で振り分ける。
//
//   variant="basic"    — 業界特化なし。vertical は常に null（素のパイプライン）
//   variant="vertical" — 業界プロファイル（gov / saas / ec）を選べる
//
// ⚠️ ここを 2 コンポーネントへ複製しないこと。同一パイプラインの画面が 2 つに
//    なると、README の操作対応表もテストも二重管理になる。
import { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import {
  confirmIntervention,
  fetchVerticals,
  startQuery,
  subscribeStream,
} from '../api/client';
import { initialJobState, jobReducer } from '../state/jobReducer';
import type { QueryParams, VerticalInfo } from '../types';
import { AnswerCard } from './AnswerCard';
import { ConfirmModal } from './ConfirmModal';
import { QueryForm } from './QueryForm';
import { StepTimeline } from './StepTimeline';

export type SupportVariant = 'basic' | 'vertical';

const LEAD: Record<SupportVariant, string> = {
  basic:
    '業界特化なしの素のパイプライン: 内部RAG＋出典 / Web裏取り・相互検証 / アクション＋HITL 承認',
  vertical:
    '内部RAG＋出典 / Web裏取り・相互検証 / アクション＋HITL 承認（業界プロファイル適用）',
};

export function SupportPanel({ variant = 'vertical' }: { variant?: SupportVariant }) {
  const [state, dispatch] = useReducer(jobReducer, initialJobState);
  const [verticals, setVerticals] = useState<VerticalInfo[]>([]);
  const [confirming, setConfirming] = useState(false);
  const unsubscribeRef = useRef<(() => void) | null>(null);
  const showVertical = variant === 'vertical';

  useEffect(() => {
    // 基本版は業界プロファイルを使わないので取得しない。
    if (!showVertical) return () => unsubscribeRef.current?.();
    fetchVerticals()
      .then(setVerticals)
      .catch(() => setVerticals([]));
    return () => unsubscribeRef.current?.();
  }, [showVertical]);

  const submit = useCallback(async (params: QueryParams) => {
    unsubscribeRef.current?.();
    try {
      const { job_id } = await startQuery(params);
      dispatch({ type: 'started', jobId: job_id });
      unsubscribeRef.current = subscribeStream(
        job_id,
        (event) => dispatch({ type: 'event', event }),
        (message) => dispatch({ type: 'failed', message }),
        'support',
      );
    } catch (error) {
      dispatch({
        type: 'failed',
        message: error instanceof Error ? error.message : String(error),
      });
    }
  }, []);

  const respond = useCallback(
    async (approve: boolean) => {
      if (!state.jobId || !state.intervention) return;
      setConfirming(true);
      try {
        await confirmIntervention(state.jobId, state.intervention.intervention_id, approve);
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

  return (
    <>
      <p className="panel-lead">{LEAD[variant]}</p>

      <QueryForm
        verticals={verticals}
        running={state.phase === 'running'}
        onSubmit={submit}
        showVertical={showVertical}
      />

      {state.error && <div className="error-banner">{state.error}</div>}
      {state.phase === 'running' && !state.intervention && (
        <div className="running-banner">
          実行中… ステップ進捗は下のタイムラインに逐次表示されます
        </div>
      )}

      <StepTimeline state={state} />
      {state.result && <AnswerCard result={state.result} />}

      {state.intervention && (
        <ConfirmModal
          intervention={state.intervention}
          actionStep={state.steps.action}
          submitting={confirming}
          onRespond={respond}
        />
      )}
    </>
  );
}

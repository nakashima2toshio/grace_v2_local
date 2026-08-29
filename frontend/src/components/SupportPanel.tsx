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
  fetchModels,
  fetchVerticals,
  startQuery,
  subscribeStream,
} from '../api/client';
import { interventionKind } from '../state/interventionKind';
import { initialJobState, jobReducer } from '../state/jobReducer';
import type { ModelChoice, QueryParams, VerticalInfo } from '../types';
import { AnswerCard } from './AnswerCard';
import { useJobTiming } from '../state/useJobTiming';
import { ConfirmModal } from './ConfirmModal';
import { JobFinishLine, JobStartLine } from './JobClock';
import { QueryForm } from './QueryForm';
import { QuestionSelectModal } from './QuestionSelectModal';
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
  // 開始・完了時刻。完了の記録は phase の決着を見て自動で入る（useJobTiming）。
  const [timing, beginTiming, observeTiming] = useJobTiming(state.phase);
  const [verticals, setVerticals] = useState<VerticalInfo[]>([]);
  const [models, setModels] = useState<ModelChoice[]>([]);
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

  // モデル選択肢は3タブ共通（基本版でも選べる）。
  useEffect(() => {
    fetchModels()
      .then(setModels)
      .catch(() => setModels([]));
  }, []);

  const submit = useCallback(async (params: QueryParams) => {
    unsubscribeRef.current?.();
    // 起動 API を待たずにここで開始時刻を打つ。ユーザーが押した瞬間が「開始」。
    beginTiming();
    try {
      const { job_id } = await startQuery(params);
      dispatch({ type: 'started', jobId: job_id });
      unsubscribeRef.current = subscribeStream(
        job_id,
        (event) => {
          observeTiming(event);
          dispatch({ type: 'event', event });
        },
        (message) => dispatch({ type: 'failed', message }),
        'support',
      );
    } catch (error) {
      dispatch({
        type: 'failed',
        message: error instanceof Error ? error.message : String(error),
      });
    }
  }, [beginTiming]);

  const respond = useCallback(
    async (approve: boolean, selectedOption: string | null = null) => {
      if (!state.jobId || !state.intervention) return;
      setConfirming(true);
      try {
        await confirmIntervention(
          state.jobId, state.intervention.intervention_id, approve, selectedOption,
        );
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
        models={models}
        running={state.phase === 'running'}
        onSubmit={submit}
        showVertical={showVertical}
        multiline={variant === 'basic'}
      />

      {state.error && <div className="error-banner">{state.error}</div>}
      <JobStartLine timing={timing} />

      {state.phase === 'running' && !state.intervention && (
        <div className="running-banner">
          実行中… ステップ進捗は下のタイムラインに逐次表示されます
        </div>
      )}

      <StepTimeline state={state} />
      {/* 完了行は回答カードの末尾に出す。**失敗時はカード自体が無い**ので、
          そのときだけパネル直下へ出す（決着したのに時刻が消える、を防ぐ）。 */}
      {state.result ? (
        <AnswerCard result={state.result} timing={timing} />
      ) : (
        <JobFinishLine timing={timing} />
      )}

      {/* 承認待ちは 2 種類ある。どちらかは reason/options から純関数で判定する
          （state/interventionKind.ts）。既定は従来のアクション承認。 */}
      {state.intervention && interventionKind(state.intervention) === 'question' && (
        <QuestionSelectModal
          intervention={state.intervention}
          submitting={confirming}
          onRespond={respond}
        />
      )}
      {state.intervention && interventionKind(state.intervention) === 'action' && (
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

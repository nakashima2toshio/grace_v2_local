// GRACE-Review（文書 → 指摘）のタブ本体。
//
// 通信の形は Support と同じ（POST でジョブ起動 → SSE で進捗 → HITL 応答）。
// 違うのは結果の見せ方で、原文ハイライト（DocumentView）と指摘カード
// （FindingList）を左右に並べ、選択状態で相互にジャンプできるようにしている。
import { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import {
  confirmReviewIntervention,
  fetchRuleSets,
  startReview,
  subscribeStream,
} from '../api/client';
import { initialReviewState, reviewReducer } from '../state/reviewReducer';
import type { ReviewParams, RuleSetInfo } from '../types';
import { useJobTiming } from '../state/useJobTiming';
import { ConfirmModal } from './ConfirmModal';
import { JobFinishLine, JobStartLine } from './JobClock';
import { DocumentView } from './DocumentView';
import { FindingList, FindingSummaryBar } from './FindingList';
import { ReviewForm } from './ReviewForm';
import { ReviewTimeline } from './ReviewTimeline';

export function ReviewPanel() {
  const [state, dispatch] = useReducer(reviewReducer, initialReviewState);
  // 開始・完了時刻。完了の記録は phase の決着を見て自動で入る（useJobTiming）。
  const [timing, beginTiming] = useJobTiming(state.phase);
  const [rulesets, setRulesets] = useState<RuleSetInfo[]>([]);
  const [confirming, setConfirming] = useState(false);
  const unsubscribeRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    fetchRuleSets()
      .then(setRulesets)
      .catch(() => setRulesets([]));
    return () => unsubscribeRef.current?.();
  }, []);

  const submit = useCallback(async (params: ReviewParams) => {
    unsubscribeRef.current?.();
    // 起動 API を待たずにここで開始時刻を打つ。ユーザーが押した瞬間が「開始」。
    beginTiming();
    try {
      const { job_id } = await startReview(params);
      dispatch({
        type: 'started',
        jobId: job_id,
        document: params.document,
        documentTitle: params.document_title,
      });
      unsubscribeRef.current = subscribeStream(
        job_id,
        (event) => dispatch({ type: 'event', event }),
        (message) => dispatch({ type: 'failed', message }),
        'review',
      );
    } catch (error) {
      dispatch({
        type: 'failed',
        message: error instanceof Error ? error.message : String(error),
      });
    }
  }, [beginTiming]);

  const respond = useCallback(
    async (approve: boolean) => {
      if (!state.jobId || !state.intervention) return;
      setConfirming(true);
      try {
        await confirmReviewIntervention(
          state.jobId,
          state.intervention.intervention_id,
          approve,
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

  const select = useCallback(
    (findingId: string | null) => dispatch({ type: 'select_finding', findingId }),
    [],
  );

  const result = state.result;

  return (
    <>
      <p className="panel-lead">
        規程 RAG＋根拠検証（groundedness）で広告表示を点検し、条文つきの指摘を出します
      </p>

      <ReviewForm
        rulesets={rulesets}
        running={state.phase === 'running'}
        onSubmit={submit}
      />

      {state.error && <div className="error-banner">{state.error}</div>}
      <JobStartLine timing={timing} />

      {state.phase === 'running' && !state.intervention && (
        <div className="running-banner">
          点検中… ステップ進捗は下のタイムラインに逐次表示されます
        </div>
      )}

      <ReviewTimeline state={state} />

      {result && (
        <>
          <FindingSummaryBar summary={result.summary} />
          {result.truncated && (
            <div className="warn-banner">
              ⚠️ 文書が大きいため途中で打ち切りました（セグメントまたは判定回数の上限）。
              分割して再実行してください。
            </div>
          )}
          <div className="review-panes">
            <DocumentView
              document={state.document}
              findings={result.findings}
              selectedFindingId={state.selectedFindingId}
              onSelect={select}
            />
            <FindingList
              findings={result.findings}
              selectedFindingId={state.selectedFindingId}
              onSelect={select}
            />
          </div>
          {result.action_result && (
            <p className="review-action-result">
              <strong>アクション:</strong> {result.action_result}
            </p>
          )}
          <p className="review-kpi">
            {result.segments_total} セグメント / 判定 {result.rules_evaluated} 回 / 検出{' '}
            {result.detected_raw} 件 → 採用 {result.findings.length} 件（抑止{' '}
            {result.summary.suppressed} / 救済 {result.rescued} / 強制 high{' '}
            {result.forced_high}）
          </p>
          <JobFinishLine timing={timing} />
        </>
      )}

      {/* 失敗して結果が無いときも、決着した事実と所要時間は残す。 */}
      {!result && <JobFinishLine timing={timing} />}

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

// GRACE-Review のステップトレース（S1・①〜⑦）。
// 表示は Timeline（共通）に委譲し、ここは Review 固有のバッジだけを持つ。
import {
  REVIEW_STEP_IDS,
  REVIEW_STEP_LABELS,
  type ReviewJobState,
  type ReviewStepState,
} from '../state/reviewReducer';
import { Timeline } from './Timeline';

function stepBadges(step: ReviewStepState): string[] {
  const badges: string[] = [];
  const data = step.data;

  if (step.status === 'skipped' && typeof data.reason === 'string') {
    badges.push(`スキップ: ${data.reason}`);
  }
  if (step.id === 'ruleset' && step.status === 'done') {
    if (typeof data.name === 'string') badges.push(`${data.name}`);
    if (typeof data.rules === 'number') badges.push(`ルール ${data.rules} 件`);
  }
  if (step.id === 'segment' && step.status === 'done') {
    if (typeof data.segments === 'number') badges.push(`${data.segments} セグメント`);
    if (data.truncated === true) badges.push('⚠️ 上限で打ち切り');
  }
  if (step.id === 'detect' && step.status === 'done') {
    if (typeof data.llm_calls === 'number') badges.push(`判定 ${data.llm_calls} 回`);
    if (typeof data.detected_raw === 'number') badges.push(`検出 ${data.detected_raw} 件`);
    if (data.truncated === true) badges.push('⚠️ 呼び出し上限で打ち切り');
  }
  if (step.id === 'suppress' && step.status === 'done') {
    if (typeof data.suppressed === 'number') badges.push(`抑止 ${data.suppressed} 件`);
    if (typeof data.rescued === 'number' && data.rescued > 0) {
      badges.push(`救済 ${data.rescued} 件`);
    }
    if (typeof data.kept === 'number') badges.push(`採用 ${data.kept} 件`);
  }
  if (step.id === 'web' && step.status === 'done' && typeof data.checked === 'number') {
    badges.push(`裏取り ${data.checked} 件`);
  }
  if (step.id === 'severity' && step.status === 'done') {
    if (typeof data.forced_high === 'number' && data.forced_high > 0) {
      badges.push(`重大リスク語で high ${data.forced_high} 件`);
    }
  }
  if (step.id === 'action' && step.status === 'done') {
    badges.push(`${data.action_type}${data.dry_run ? '（dry-run）' : ''}`);
  }
  return badges;
}

export function ReviewTimeline({ state }: { state: ReviewJobState }) {
  if (state.phase === 'idle') return null;
  return (
    <Timeline
      title="ステップトレース"
      stepIds={REVIEW_STEP_IDS}
      labels={REVIEW_STEP_LABELS}
      steps={state.steps}
      logs={state.logs}
      badges={(step) => stepBadges(step as ReviewStepState)}
    />
  );
}

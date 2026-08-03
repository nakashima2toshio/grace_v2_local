// GRACE-Support のステップトレース（①〜⑥、④'・④救済含む）。
// 実行中／完了／スキップをステータス表示し、SSE で逐次更新される。
// 表示そのものは Timeline（共通）に委譲し、ここは Support 固有のバッジだけを持つ。
import { STEP_IDS, STEP_LABELS, type JobState, type StepState } from '../state/jobReducer';
import { Timeline } from './Timeline';

function stepBadges(step: StepState): string[] {
  const badges: string[] = [];
  const data = step.data;
  if (step.id === 'web' && step.status === 'done' && data.web_reused === true) {
    badges.push('Web再利用（重複推論を省略）');
  }
  if (step.id === 'web' && step.status === 'skipped' && typeof data.reason === 'string') {
    badges.push(`スキップ: ${data.reason}`);
  }
  if (step.id === 'gate' && step.status === 'done') {
    if (data.forced_escalate === true) badges.push(`強制エスカレ（'${data.matched_keyword}'）`);
    if (data.rescued === true) badges.push('④救済（出典付き・矛盾なし回答を維持）');
    if (typeof data.decision === 'string') badges.push(`判定: ${data.decision}`);
  }
  if (step.id === 'no_info' && step.status === 'done' && data.no_info === true) {
    badges.push('情報なし回答を検知 → escalate');
  }
  if (step.id === 'confidence' && step.status === 'done' && typeof data.support_rate === 'number') {
    badges.push(`支持率 ${(data.support_rate as number).toFixed(2)}`);
  }
  if (step.id === 'action' && step.status === 'done') {
    badges.push(`${data.action_type}${data.dry_run ? '（dry-run）' : ''}`);
  }
  return badges;
}

export function StepTimeline({ state }: { state: JobState }) {
  if (state.phase === 'idle') return null;
  return (
    <Timeline
      title="ステップトレース"
      stepIds={STEP_IDS}
      labels={STEP_LABELS}
      steps={state.steps}
      logs={state.logs}
      badges={(step) => stepBadges(step as StepState)}
    />
  );
}

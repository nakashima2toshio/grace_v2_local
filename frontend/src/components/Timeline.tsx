// ステップトレースの表示だけを担う共通コンポーネント（状態を持たない）。
//
// Support（`StepTimeline`）と Review（`ReviewTimeline`）で見た目が同じなので、
// マークアップだけをここへ寄せた。**ステップ ID の集合とバッジの出し方は
// エージェントごとに違う**ため、そこは呼び出し側から渡す。

export interface TimelineStep {
  id: string;
  status: 'pending' | 'running' | 'done' | 'skipped';
  logs: string[];
  data: Record<string, unknown>;
}

const STATUS_ICON: Record<TimelineStep['status'], string> = {
  pending: '○',
  running: '▶',
  done: '✓',
  skipped: '−',
};

interface Props {
  title: string;
  stepIds: readonly string[];
  labels: Record<string, string>;
  steps: Record<string, TimelineStep>;
  /** ステップに紐づかないログ。 */
  logs: string[];
  /** ステップごとの補足バッジ（判定結果・スキップ理由など）。 */
  badges: (step: TimelineStep) => string[];
}

export function Timeline({ title, stepIds, labels, steps, logs, badges }: Props) {
  return (
    <section className="timeline">
      <h2>{title}</h2>
      <ol>
        {stepIds.map((id) => {
          const step = steps[id];
          return (
            <li key={id} className={`step step-${step.status}`}>
              <span className="step-icon">{STATUS_ICON[step.status]}</span>
              <div className="step-body">
                <div className="step-title">
                  {labels[id]}
                  {badges(step).map((badge) => (
                    <span key={badge} className="badge">
                      {badge}
                    </span>
                  ))}
                </div>
                {step.logs.length > 0 && (
                  <details className="step-logs" open={step.status === 'running'}>
                    <summary>ログ（{step.logs.length}）</summary>
                    <pre>{step.logs.join('\n')}</pre>
                  </details>
                )}
              </div>
            </li>
          );
        })}
      </ol>
      {logs.length > 0 && (
        <details className="step-logs">
          <summary>その他のログ（{logs.length}）</summary>
          <pre>{logs.join('\n')}</pre>
        </details>
      )}
    </section>
  );
}

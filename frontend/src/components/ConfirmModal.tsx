// HITL CONFIRM モーダル: アクション内容（action_type / args / バックエンド / dry-run）と
// 本人確認ステップを表示し、承認 / 拒否を選択させる。承認なしにアクションは実行されない。
// タイムアウト時はバックエンドが安全側（実行せず有人対応へ）に倒す。
import type { InterventionInfo } from '../types';

/**
 * アクションステップの表示に必要な部分だけを構造的に受ける。
 * Support の `StepState` と Review の `ReviewStepState` の両方が当てはまるため、
 * 本モーダルは両エージェントで共用できる。
 */
export interface ActionStepView {
  data: Record<string, unknown>;
  logs: string[];
}

interface Props {
  intervention: InterventionInfo;
  actionStep: ActionStepView; // ⑥/⑦ の step started イベント（action_type/args/backend/dry_run）
  submitting: boolean;
  onRespond: (approve: boolean) => void;
}

export function ConfirmModal({ intervention, actionStep, submitting, onRespond }: Props) {
  const data = actionStep.data;
  const identityLog = actionStep.logs.find((line) => line.includes('本人確認'));
  return (
    <div className="modal-backdrop">
      <div className="modal" role="dialog" aria-modal="true" aria-label="アクション実行の承認">
        <h2>アクション実行の承認（HITL CONFIRM）</h2>
        <p className="modal-message">{intervention.message}</p>
        <table className="modal-table">
          <tbody>
            <tr>
              <th>アクション種別</th>
              <td>
                <code>{String(data.action_type ?? '不明')}</code>
              </td>
            </tr>
            <tr>
              <th>引数</th>
              <td>
                <pre>{JSON.stringify(data.args ?? {}, null, 2)}</pre>
              </td>
            </tr>
            <tr>
              <th>バックエンド</th>
              <td>
                {String(data.backend ?? '-')}
                {data.dry_run === true
                  ? '（dry-run: 実行せずログのみ）'
                  : '（実行モード）'}
              </td>
            </tr>
            {identityLog && (
              <tr>
                <th>本人確認</th>
                <td>{identityLog.trim()}</td>
              </tr>
            )}
            {intervention.reason && (
              <tr>
                <th>理由</th>
                <td>{intervention.reason}</td>
              </tr>
            )}
            {typeof intervention.timeout_seconds === 'number' && (
              <tr>
                <th>タイムアウト</th>
                <td>
                  {intervention.timeout_seconds} 秒（超過時は実行せず有人対応へエスカレーション）
                </td>
              </tr>
            )}
          </tbody>
        </table>
        <div className="modal-actions">
          <button
            className="approve"
            disabled={submitting}
            onClick={() => onRespond(true)}
          >
            承認して実行（PROCEED）
          </button>
          <button
            className="reject"
            disabled={submitting}
            onClick={() => onRespond(false)}
          >
            拒否（実行しない）
          </button>
        </div>
      </div>
    </div>
  );
}

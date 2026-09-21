// HITL CONFIRM モーダル: アクション内容（action_type / args / バックエンド / dry-run）と
// 本人確認ステップを表示し、承認 / 拒否を選択させる。承認なしにアクションは実行されない。
// タイムアウト時はバックエンドが安全側（実行せず有人対応へ）に倒す。
import { useEffect, useRef, type KeyboardEvent as ReactKeyboardEvent } from 'react';

import { isTabKey, nextFocusIndex } from '../state/focusTrap';
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

  // ── フォーカストラップ ─────────────────────────────────────
  // `aria-modal="true"` は「背後は不活性」と支援技術へ伝えるだけで、
  // ブラウザのフォーカス順序は変えない。Tab で背後のページへ抜けないよう、
  // ここで実際に閉じ込める。移動先の計算は `state/focusTrap.ts` の純関数。
  //
  // ⚠️ **Escape では閉じない。** HITL CONFIRM は承認／拒否を明示的に選ぶ関門で、
  // 「閉じる」に相当する既定の選択肢が無い（拒否に倒すと誤操作で実行機会を失い、
  // 承認に倒すのは論外）。タイムアウト時はバックエンドが安全側へ倒す。
  const modalRef = useRef<HTMLDivElement | null>(null);

  const focusables = (): HTMLElement[] => {
    const root = modalRef.current;
    if (!root) return [];
    // 承認／拒否ボタンが対象。`disabled` な要素は焦点を持てないので除く。
    return Array.from(
      root.querySelectorAll<HTMLElement>('button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'),
    );
  };

  // 開いたら最初の要素（承認ボタン）へ焦点を移す。
  useEffect(() => {
    focusables()[0]?.focus();
    // 開いた直後に 1 度だけ。以降の再描画で焦点を奪わない。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (!isTabKey(event)) return;
    const items = focusables();
    const current = items.indexOf(document.activeElement as HTMLElement);
    const next = nextFocusIndex(items.length, current, event.shiftKey);
    if (next < 0) return;
    event.preventDefault();
    items[next]?.focus();
  };

  return (
    <div className="modal-backdrop">
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label="アクション実行の承認"
        ref={modalRef}
        onKeyDown={handleKeyDown}
      >
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

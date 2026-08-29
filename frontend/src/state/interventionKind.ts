// 承認待ち（intervention）が「どちらの種類か」を判定する純関数。
//
// HITL は 2 種類ある。どちらも同じ `intervention` イベントで届くため、
// **受け取り側が見分ける必要がある**。
//
//   action   : ⑥ アクション実行の承認（承認 / 拒否の 2 択）
//   question : 0-(A) 入力・質問分析での主質問の選択（N 択）
//
// 見分けを ConfirmModal の中でやると、コンポーネントの分岐になりテストできない
// （vitest は `.test.tsx` を収集しない）。判断だけをここへ出す。
//
// 設計: docs/multi_question_handling.md §13.2

/** バックエンドが主質問の選択に付ける理由（support_agent.py と一致させること）。 */
export const MULTI_QUESTION_REASON = 'multi_question_selection';

/** 判定に必要な最小のフィールドだけを受ける（types.ts の InterventionInfo が当てはまる）。 */
export interface InterventionKindInput {
  reason?: string | null;
  options?: string[] | null;
}

export type InterventionKind = 'action' | 'question';

/**
 * 承認待ちの種類を返す。
 *
 * ⚠️ **`options` があるだけで「質問選択」と決めない。** `InterventionRequest`
 * の `options` は汎用フィールドで、将来アクション側が選択肢を持つこともある。
 * バックエンドが明示した `reason` を主、選択肢の実在を従として判定する。
 * 判断がつかないときは `action`（従来の承認モーダル）に倒す。
 */
export function interventionKind(info: InterventionKindInput): InterventionKind {
  const hasOptions = Array.isArray(info.options) && info.options.length > 0;
  return info.reason === MULTI_QUESTION_REASON && hasOptions ? 'question' : 'action';
}

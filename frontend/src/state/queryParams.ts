// 入力フォームの状態 → API へ送る `QueryParams` を組み立てる純関数。副作用ゼロ。
//
// `QueryForm` の中に書くと React を起動しないとテストできないため、判断の要る部分
// （基本版での vertical 固定・識別子を送るかどうか・trim）だけをここへ出す。
// 方針は `jobReducer` / `highlight` と同じ — **純関数だけを単体テストする**。
import type { QueryParams } from '../types';

/** support_actions.py の IDENTITY_FIELDS と一致させる。 */
export const IDENTITY_FIELDS = ['order_id', 'email'] as const;

export interface QueryFormState {
  query: string;
  /** 業界プロファイル ID。未選択は空文字。 */
  vertical: string;
  useWeb: boolean;
  doAction: boolean;
  dryRun: boolean;
  verbose: boolean;
  orderId: string;
  email: string;
  /** 基本版タブは false。false なら vertical を送らない。 */
  showVertical: boolean;
}

/**
 * フォーム状態から送信ペイロードを作る。
 *
 * - `query` は trim する（前後の空白だけの入力は呼び出し側が弾く）
 * - **基本版（`showVertical=false`）では `vertical` を常に `null`** にする
 * - 識別子は `order_id` / `email` のどちらかが入っていれば送り、
 *   **両方空なら `null`**（「提示なし」を空文字の辞書と区別するため）
 */
export function buildQueryParams(state: QueryFormState): QueryParams {
  const identity: Record<string, string> = {
    order_id: state.orderId.trim(),
    email: state.email.trim(),
  };
  const hasIdentity = IDENTITY_FIELDS.some((field) => identity[field]);

  return {
    query: state.query.trim(),
    vertical: state.showVertical ? state.vertical || null : null,
    dry_run: state.dryRun,
    use_web: state.useWeb,
    do_action: state.doAction,
    verbose: state.verbose,
    identity: hasIdentity ? identity : null,
  };
}

/**
 * 本人確認が実際に起動するか。
 *
 * `require_identity` のプロファイルを選んでいるときだけ true。基本版は
 * `vertical` を送らないので常に false（コア側で `identity_verifier` が
 * 作られず、識別子は参照されない）。
 */
export function isIdentityActive(
  showVertical: boolean,
  requireIdentity: boolean | undefined,
): boolean {
  return showVertical && requireIdentity === true;
}

/** 識別子欄の下に出す状態メッセージ（入力しても効かない設定を明示する）。 */
export function identityNote(active: boolean, dryRun: boolean): string {
  if (!active) {
    return '現在の設定では本人確認を行いません（このプロファイルは require_identity=false）';
  }
  return dryRun
    ? 'dry-run 中はデモ照合のため、入力値は照合に使われません'
    : 'SUPPORT_IDENTITY_FILE の顧客台帳と照合します（未設定の場合は常に未確認）';
}

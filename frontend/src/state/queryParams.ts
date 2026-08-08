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
  /**
   * 選択中プロファイルの `require_identity`。未選択・基本版は `undefined`。
   *
   * **識別子欄の有効／無効と、識別子を送るかどうかの両方がこの値で決まる。**
   * 画面が「無効」と表示している欄の値を送らないために必要（`buildQueryParams` は
   * DOM ではなく state からペイロードを組むので、`fieldset disabled` による
   * 「無効な欄は送信されない」という HTML の保護が効かない）。
   */
  requireIdentity?: boolean;
}

/**
 * フォーム状態から送信ペイロードを作る。
 *
 * - `query` は trim する（前後の空白だけの入力は呼び出し側が弾く）
 * - **基本版（`showVertical=false`）では `vertical` を常に `null`** にする
 * - 識別子は `order_id` / `email` のどちらかが入っていれば送り、
 *   **両方空なら `null`**（「提示なし」を空文字の辞書と区別するため）
 * - **本人確認が起動しない設定（`isIdentityActive` が false）でも `null`**。
 *   欄が無効表示のまま値が残るケース（ec で入力 → gov へ切替）で、
 *   画面に出ていない識別子が送られるのを防ぐ。
 */
export function buildQueryParams(state: QueryFormState): QueryParams {
  const identity: Record<string, string> = {
    order_id: state.orderId.trim(),
    email: state.email.trim(),
  };
  const active = isIdentityActive(state.showVertical, state.requireIdentity);
  const hasIdentity = IDENTITY_FIELDS.some((field) => identity[field]);

  return {
    query: state.query.trim(),
    vertical: state.showVertical ? state.vertical || null : null,
    dry_run: state.dryRun,
    use_web: state.useWeb,
    do_action: state.doAction,
    verbose: state.verbose,
    identity: active && hasIdentity ? identity : null,
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

export interface IdentityNoteInput {
  /** 基本版タブは false（業界プロファイル自体を使わない）。 */
  showVertical: boolean;
  /** 選択中プロファイルの ID。未選択は空文字。 */
  vertical: string;
  /** 選択中プロファイルの `require_identity`。未選択は `undefined`。 */
  requireIdentity?: boolean;
  dryRun: boolean;
  /**
   * `require_identity=true` のプロファイル ID 一覧（どれを選べば有効かを案内する）。
   *
   * **基本版タブは `/api/verticals` を引かないので空になる。** 空を
   * 「該当プロファイルが無い」と解釈してはいけない（単に未取得なだけ）。
   */
  identityVerticals: string[];
}

/**
 * 識別子欄の下に出す状態メッセージ。
 *
 * **「なぜ無効なのか」を設定ごとに言い分ける。** 以前は無効側が 1 文しか無く、
 * プロファイル未選択・基本版タブでも「**このプロファイルは** require_identity=false」と
 * 出していた。存在しないプロファイルのせいにしてしまうため、
 * 「どう操作しても有効にならない」と読めてしまうのが問題だった。
 * どのプロファイルなら有効になるかも併記する。
 */
export function identityNote(input: IdentityNoteInput): string {
  const { showVertical, vertical, requireIdentity, dryRun, identityVerticals } = input;
  // 一覧が空でも「該当プロファイルは無い」とは書かない（未取得と区別できないため）。
  const hint = identityVerticals.length
    ? `　本人確認を行うプロファイル: ${identityVerticals.join(' / ')}`
    : '';

  if (!showVertical) {
    return `基本版は業界プロファイルを使わないため本人確認を行いません（GRACE-Support タブで選択してください）。${hint}`;
  }
  if (!vertical) {
    return `業界プロファイルが未選択のため本人確認を行いません。${hint}`;
  }
  if (requireIdentity !== true) {
    return `${vertical} は本人確認を行いません（require_identity=false）。${hint}`;
  }
  return dryRun
    ? 'dry-run 中はデモ照合のため、入力値は照合に使われません（dry-run をオフにすると台帳と照合します）'
    : 'SUPPORT_IDENTITY_FILE の顧客台帳と照合します（未設定の場合は常に未確認）';
}

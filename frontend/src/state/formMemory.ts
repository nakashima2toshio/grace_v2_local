// フォームの入力内容を、コンポーネントの寿命より長く覚えておくストア。
//
// ## 何のためにあるか
//
// タブは**アンマウントで切り替える**（`App.tsx` の条件レンダリング）。
// これは離れた側の `EventSource` を `useEffect` のクリーンアップで確実に閉じるための
// 意図的な設計だが、副作用として `QueryForm` / `ReviewForm` の `useState` が
// すべて初期値へ戻ってしまう。
//
//   GRACE-Support でチェックを外す → GRACE-Review へ切替 → 戻る
//     → チェックが既定値に復帰し、入力した問い合わせ文まで消える
//
// 「設定したはずの値が勝手に戻る」のは、実行結果を左右する dry-run や
// Web フォールバックでは特に危険である（意図せず実行モードに戻る）。
//
// アンマウントは維持したまま、**入力内容だけをここに退避**して再マウント時に復元する。
// `activeJobs.ts`（実行中ジョブの job_id 保持）と同じ方針。
//
// ## なぜ React の状態でないのか
//
// 覚えておきたいのは「アンマウントされても消えない」情報なので、state では目的を果たせない。
// `App` まで持ち上げる案もあるが、フォームの項目をすべて `App` が知ることになり
// 「パネルが自分の状態を持つ」という設計を崩す。
//
// ローカル開発用のシングルページなので、ページを再読み込みすれば消えて構わない
// （`sessionStorage` にはしない — 起動直後は既定値から始まるほうが分かりやすい）。

// ⚠️ モデルの選択はここに入れない。ヘッダー（App）のセレクタが持ち、App は
//    アンマウントされないので退避が要らない（state/headerModel.ts）。

// ---------------------------------------------------------------------------
// QueryForm（基本版 / GRACE-Support）
// ---------------------------------------------------------------------------

export interface QueryFormMemory {
  query: string;
  vertical: string;
  dryRun: boolean;
  verbose: boolean;
  useWeb: boolean;
  doAction: boolean;
  orderId: string;
  email: string;
}

/** `QueryForm` の `useState` 初期値と 1:1。ここを変えたら実装側も揃える。 */
export const DEFAULT_QUERY_FORM: QueryFormMemory = {
  query: '',
  vertical: '',
  // dry-run の既定は OFF（アクションは ⑥ の HITL CONFIRM で承認してから実行する）
  dryRun: false,
  // 詳細ログの既定は ON（ステップトレースで各段の判断根拠を追えるように）
  verbose: true,
  useWeb: true,
  doAction: true,
  orderId: '',
  email: '',
};

/**
 * 基本版と GRACE-Support は**別のタブ**なので、記憶も分ける。
 * 片方で dry-run を外したらもう片方も外れる、という挙動は意図に反する。
 */
export type QueryFormKey = 'basic' | 'vertical';

const queryForms = new Map<QueryFormKey, QueryFormMemory>();

/** 再マウント時に引く。未記録なら既定値。 */
export function recallQueryForm(key: QueryFormKey): QueryFormMemory {
  return queryForms.get(key) ?? DEFAULT_QUERY_FORM;
}

/** 入力が変わるたびに退避する。 */
export function rememberQueryForm(key: QueryFormKey, state: QueryFormMemory): void {
  queryForms.set(key, state);
}

// ---------------------------------------------------------------------------
// ReviewForm（GRACE-Review）
// ---------------------------------------------------------------------------

export interface ReviewFormMemory {
  document: string;
  title: string;
  ruleset: string;
  useWeb: boolean;
  dryRun: boolean;
  verbose: boolean;
}

/** `ReviewForm` の `useState` 初期値と 1:1。 */
export const DEFAULT_REVIEW_FORM: ReviewFormMemory = {
  document: '',
  title: '',
  ruleset: 'ec_ad',
  // Web 裏取りの既定は ON（法改正の見落としを防ぐ。信頼度を下げる方向にのみ使う）
  useWeb: true,
  // dry-run の既定は OFF（起票は ⑦ の HITL CONFIRM で承認してから実行する）
  dryRun: false,
  // 詳細ログの既定は ON（ステップトレースで各段の判断根拠を追えるように）
  verbose: true,
};

let reviewForm: ReviewFormMemory | null = null;

export function recallReviewForm(): ReviewFormMemory {
  return reviewForm ?? DEFAULT_REVIEW_FORM;
}

export function rememberReviewForm(state: ReviewFormMemory): void {
  reviewForm = state;
}

// ---------------------------------------------------------------------------

/** テスト用。全消去。 */
export function clearFormMemory(): void {
  queryForms.clear();
  reviewForm = null;
}

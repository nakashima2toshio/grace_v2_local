// モーダル内にフォーカスを閉じ込める（フォーカストラップ）ための純関数。
//
// ## なぜ必要か
//
// `ConfirmModal` は `role="dialog"` と `aria-modal="true"` を持つが、
// **Tab で背後のページへフォーカスが抜けていた**（`frontend/docs/README.md` §7-2）。
// `aria-modal="true"` は「背後は不活性」と支援技術へ伝えるだけで、
// ブラウザのフォーカス順序は変えない。実際に閉じ込めるのは実装側の仕事である。
//
// HITL CONFIRM は**承認なしにアクションが実行されない**関門なので、
// キーボード利用者が承認／拒否のボタンへ確実に到達できる必要がある。
//
// ## なぜ純関数に切り出すのか
//
// `vite.config.ts` の vitest は `environment: 'node'`・`include: ['src/**/*.test.ts']` で、
// **`.test.tsx` は収集されない**。DOM 操作（`focus()` の呼び出し）はコンポーネント側に残し、
// 「次にどれへ移るか」の**計算だけ**をここへ出す。

// React の KeyboardEvent に依存せず、必要なフィールドだけを受ける。
export interface TabKeyEvent {
  key: string;
  shiftKey?: boolean;
  ctrlKey?: boolean;
  metaKey?: boolean;
  altKey?: boolean;
}

/**
 * フォーカス移動として扱う Tab 押下か。
 *
 * Shift 以外の修飾キーが付いていれば false（Ctrl+Tab のようなブラウザ操作を奪わない）。
 */
export function isTabKey(event: TabKeyEvent): boolean {
  if (event.ctrlKey || event.metaKey || event.altKey) return false;
  return event.key === 'Tab';
}

/**
 * Tab / Shift+Tab を押したあとに焦点を当てるべき要素の添字。
 *
 * 端で**巻き戻す**（最後 → 最初、最初 → 最後）ことでモーダル内に閉じ込める。
 *
 * @param count トラップ対象の要素数。0 以下なら -1（移動先なし）
 * @param currentIndex いま焦点のある要素の添字。範囲外・不明なら -1 を渡す
 * @param shiftKey Shift を押しているか（逆方向）
 * @returns 移動先の添字。`count <= 0` のときだけ -1
 *
 * ⚠️ `currentIndex` が -1（モーダル外・不明）のときは、**前進なら先頭・
 * 後退なら末尾**へ入れる。フォーカスが外へ出ていても次の Tab で戻ってこられる。
 */
export function nextFocusIndex(
  count: number,
  currentIndex: number,
  shiftKey = false,
): number {
  if (count <= 0) return -1;
  if (currentIndex < 0 || currentIndex >= count) {
    return shiftKey ? count - 1 : 0;
  }
  const delta = shiftKey ? -1 : 1;
  return (currentIndex + delta + count) % count;
}

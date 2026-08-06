// タブの矢印キー移動を計算する純関数。
//
// WAI-ARIA の tablist パターンでは、Tab キーではなく**左右（縦なら上下）矢印**で
// タブ間を移動し、Home / End で端へ飛ぶことが期待される。
// `App.tsx` のタブと `DataPanel.tsx` のサブタブの両方で使うため、
// 計算だけを切り出してテスト可能にしてある。

/** 矢印キーで移動した先のインデックス。移動しないキーなら null。 */
export function nextTabIndex(
  currentIndex: number,
  count: number,
  key: string,
): number | null {
  if (count <= 0) return null;
  switch (key) {
    case 'ArrowRight':
    case 'ArrowDown':
      // 端まで行ったら先頭へ回り込む（WAI-ARIA の推奨挙動）
      return (currentIndex + 1) % count;
    case 'ArrowLeft':
    case 'ArrowUp':
      return (currentIndex - 1 + count) % count;
    case 'Home':
      return 0;
    case 'End':
      return count - 1;
    default:
      return null;
  }
}

/**
 * タブのキーボード操作を処理する。
 *
 * 移動した場合は `preventDefault()` を呼ぶ（`ArrowDown` でのページスクロールや
 * `Home` / `End` でのページ端ジャンプを止める）。
 *
 * @returns 移動先のインデックス。移動しなかったら null。
 */
export function handleTabKeyDown(
  event: { key: string; preventDefault: () => void },
  currentIndex: number,
  count: number,
): number | null {
  const next = nextTabIndex(currentIndex, count, event.key);
  if (next === null || next === currentIndex) return null;
  event.preventDefault();
  return next;
}

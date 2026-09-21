// 指摘の選択（原文ハイライト ⇄ 指摘カード）をキーボードでも操作するための純関数。
//
// ## なぜ必要か
//
// `DocumentView` の `<mark>` と `FindingList` の `<li>` は、どちらも
// `onClick` で指摘を選択する。**どちらも本来はインタラクティブでない要素**なので、
// クリック以外の手段が無い。キーボードだけで使う人と支援技術の利用者にとって、
// 2 ペインの相互ジャンプが使えない状態だった（`frontend/docs/README.md` §7-3）。
//
// `role="button"` と `tabIndex={0}` を付けたうえで、**Enter と Space で
// クリックと同じ動作**をさせる。これはネイティブの `<button>` の挙動に揃えたもので、
// WAI-ARIA Authoring Practices の button パターンに従う。
//
// ## なぜ純関数に切り出すのか
//
// `vite.config.ts` の vitest 設定は `environment: 'node'` かつ
// `include: ['src/**/*.test.ts']` で、**`.test.tsx` は収集されない**。
// 判断（どのキーで発火するか・押した結果どちらが選択状態になるか）をここへ寄せれば
// `.test.ts` で検証できる。`state/submitKey.ts` と同じ方針。

// React の KeyboardEvent に依存せず、必要なフィールドだけを受ける。
export interface ActivationKeyEvent {
  key: string;
  /** 修飾キー。いずれか押されていれば発火しない（ブラウザのショートカットを奪わない）。 */
  ctrlKey?: boolean;
  metaKey?: boolean;
  altKey?: boolean;
  shiftKey?: boolean;
  /** IME の変換中か（DOM の `KeyboardEvent.isComposing`）。省略時は false 扱い。 */
  isComposing?: boolean;
}

/**
 * `role="button"` な要素で「クリック相当」とみなすキー操作か。
 *
 * - **Enter / Space（`' '`）→ true**
 * - それ以外のキー → false
 * - **修飾キー付き → false**（Ctrl+Enter などはブラウザ／アプリ側の操作）
 * - **IME 変換中 → false**（日本語入力の確定 Enter を選択操作と取り違えない）
 *
 * ⚠️ 旧仕様の `Spacebar`（IE）は受けない。対象ブラウザは Vite のビルドターゲットに従う。
 */
export function isActivationKey(event: ActivationKeyEvent): boolean {
  if (event.isComposing) return false;
  if (event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return false;
  return event.key === 'Enter' || event.key === ' ';
}

/**
 * 指摘を選択／選択解除したあとの `selectedFindingId`。
 *
 * **同じ指摘をもう一度選ぶと解除**（トグル）。原文のハイライトと指摘カードで
 * 同じ規則にするため、判断をここ 1 箇所に置く。
 *
 * @param selectedFindingId いま選択されている指摘（未選択なら null）
 * @param findingId 操作された指摘
 */
export function toggleSelection(
  selectedFindingId: string | null,
  findingId: string,
): string | null {
  return selectedFindingId === findingId ? null : findingId;
}

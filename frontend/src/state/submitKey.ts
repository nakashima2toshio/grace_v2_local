// 複数行入力（textarea）での「送信」キー操作を判定する純関数。
//
// ## なぜ必要か
//
// 単一行の `<input type="text">` は Enter でフォームが暗黙送信される
// （HTML の implicit submission）。`<textarea>` へ替えるとこれが効かなくなり、
// Enter は改行になる。キーボードだけで送信する手段が無くなるため、
// Ctrl+Enter / ⌘+Enter を送信に割り当てる。
//
// ## なぜ純関数に切り出すのか
//
// `vite.config.ts` の vitest 設定は `environment: 'node'` かつ
// `include: ['src/**/*.test.ts']` で、**`.test.tsx` は収集されない**。
// つまりコンポーネントのレンダリングテストは書けない。判断（どのキーで送信するか）
// をここへ寄せておけば `.test.ts` で検証できる。`state/tabKeys.ts` と同じ方針。

// React の KeyboardEvent に依存せず、必要なフィールドだけを受ける
// （node 環境のテストから素のオブジェクトを渡せるようにするため）。
export interface SubmitKeyEvent {
  key: string;
  ctrlKey: boolean;
  metaKey: boolean;
  /**
   * Shift の押下。**送信条件には使わない**（Shift+Enter は改行のまま）。
   * 「Shift+Enter で送信されない」ことを明示・テストするために受け取る。
   */
  shiftKey?: boolean;
  /**
   * IME の変換中か（DOM の `KeyboardEvent.isComposing`）。省略時は false 扱い。
   */
  isComposing?: boolean;
}

/**
 * textarea で「送信」とみなすキー操作か。
 *
 * - **Ctrl+Enter / ⌘+Enter → true**（送信）
 * - Enter 単独・Shift+Enter → false（改行のまま）
 *
 * ⚠️ **IME 変換中（`isComposing`）は常に false。**
 * 日本語入力では変換の確定に Enter を使う。確定操作を送信と取り違えると、
 * 変換途中の文章がそのまま実行されてしまう。修飾キー付きの Enter を
 * 変換確定に割り当てる IME もあるため、`isComposing` を最優先で見る。
 */
export function isSubmitKey(event: SubmitKeyEvent): boolean {
  if (event.isComposing) return false;
  if (event.key !== 'Enter') return false;
  return event.ctrlKey || event.metaKey;
}

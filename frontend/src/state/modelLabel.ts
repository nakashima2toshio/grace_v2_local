// ヘッダーのモデルセレクタに出す文字列を組み立てる純関数（`state/headerModel.ts` が使う）。
//
// ## なぜ純関数に切り出すのか
//
// `vite.config.ts` の vitest 設定は `environment: 'node'` かつ
// `include: ['src/**/*.test.ts']` で、**`.test.tsx` は収集されない**。
// つまりコンポーネントのレンダリングテストは書けない。表示の判断（何を出し、
// 何を出さないか）をここへ寄せておけば、`.test.ts` で検証できる。
//
// ⚠️ 既定のモデル名をこのファイルに書かないこと。値は必ず API
// （GET /api/model → `config.py::get_default_ollama_model()` の解決結果）
// から来る。フロントに既定値を持つと、設定を変えたときに画面と実挙動がずれる。
import type { ModelChoice } from '../types';

/** ヘッダーのラベル見出し（エージェントの 3 タブ）。 */
export const MODEL_LABEL_PREFIX = '利用モデル名：';

/**
 * ヘッダーのモデルセレクタの各選択肢に出す文字列を返す。
 *
 * `GET /api/models` は `supports_tool_calls` と `notes`（モデルの容量・
 * 得手不得手・制約）を返しているのに、以前のセレクタは `id` しか出していなかった。
 * **選ぶ前に分かるべき情報**なので、ラベルへ畳み込む。
 *
 * - tool calling 非対応なら、その旨を先頭に出す（ReAct 経路で使えない）
 * - `notes` があれば続けて出す
 * - **`notes` が既に tool calling に触れているなら重ねて出さない**
 *   （`config.py::OllamaConfig.MODEL_CONSTRAINTS` の文言と二重になる）
 *
 * @example
 * modelOptionLabel({ id: 'gemma4:12b-mlx', supports_tool_calls: true, notes: 'デフォルト' })
 * // → 'gemma4:12b-mlx — デフォルト'
 */
export function modelOptionLabel(choice: ModelChoice): string {
  const id = choice.id.trim();
  const notes = choice.notes.trim();

  const flags: string[] = [];
  if (!choice.supports_tool_calls && !/tool\s*call/i.test(notes)) {
    flags.push('tool calling 非対応');
  }

  const detail = [...flags, notes].filter(Boolean).join(' / ');
  return detail ? `${id} — ${detail}` : id;
}

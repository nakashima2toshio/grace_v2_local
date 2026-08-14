// ヘッダーに出す「利用モデル名」の文字列を組み立てる純関数。
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
import type { ModelInfo } from '../types';

/** ヘッダーのラベル見出し。 */
export const MODEL_LABEL_PREFIX = '利用モデル名：';

/**
 * 表示するモデル名を返す。**出せない情報は出さない**（null を返す）。
 *
 * - 取得前・取得失敗（`info === null`）→ null（ヘッダーに何も出さない）
 * - `model` が空文字 → null（「利用モデル名：」だけが出るのを防ぐ）
 * - `heavy_model` が設定されていて `model` と異なる → 併記する。
 *   論理層（計画生成・推論・根拠検証）だけ別モデルへ寄せている状態を
 *   隠すと、ヘッダーが実際の挙動について嘘をつくことになるため。
 */
export function formatModelLabel(info: ModelInfo | null): string | null {
  if (info === null) return null;

  const model = info.model.trim();
  if (!model) return null;

  const heavy = info.heavy_model.trim();
  if (heavy && heavy !== model) {
    return `${model}（論理層: ${heavy}）`;
  }
  return model;
}

// ヘッダーのモデルセレクタ（基本版 / GRACE-Support / GRACE-Review）の判断を
// まとめた純関数。
//
// ## なぜヘッダーにあるのか
//
// モデルは「そのタブで何を使って走るか」という画面全体の設定なので、
// タイトル横（`利用モデル名：`）で選ぶ。以前は各フォームの中にセレクタがあり、
// ヘッダーは既定値を表示するだけだったため、同じ情報が 2 箇所に出ていた。
//
// ## 未選択（空文字）の意味
//
// 空文字 = 「サーバーの既定値を使う」。送信時は `buildQueryParams` / ReviewForm が
// null へ倒し、サーバーが既定モデル（`config.py::get_default_ollama_model()` に
// `config/grace_config.yml` / 環境変数を適用した値）で走る。
// ⚠️ 既定のモデル名をフロントに持たないこと（値は GET /api/model から来る）。
//
// ## 選択は「スロット」ごと
//
// 基本版と GRACE-Support は同じパイプラインだが**別のタブ**なので、片方で
// 選んだモデルがもう片方へ漏れないよう記憶を分ける（formMemory と同じ方針）。
// データ管理タブは工程（チャンキング / Q/A 作成）ごとに**別のモデルを選べる**よう
// 2 つのセレクタを並べ、それぞれ別のスロットに持つ（既定はどちらも `ModelInfo.model`）。
// 値は `App` の state に持つ。`App` はアンマウントされないので、
// タブを切り替えても選択は残る。
import type { ModelChoice, ModelInfo } from '../types';
import { MODEL_LABEL_PREFIX, modelOptionLabel } from './modelLabel';

/** 画面のタブ（`App.tsx` の `Tab` と同じ値）。 */
export type AppTab = 'basic' | 'support' | 'review' | 'data';

/** 選択を持つ単位。エージェントの 3 タブは 1 つずつ、データ管理タブは工程ごとに 2 つ。 */
export type ModelSlot = 'basic' | 'support' | 'review' | 'chunking' | 'qa';

/** スロットごとの選択。空文字 = 未選択（サーバーの既定値）。 */
export type HeaderModels = Record<ModelSlot, string>;

export const INITIAL_HEADER_MODELS: HeaderModels = {
  basic: '',
  support: '',
  review: '',
  chunking: '',
  qa: '',
};

/** ヘッダーに並べるセレクタ 1 つ分。 */
export interface HeaderSlot {
  slot: ModelSlot;
  /** セレクタの見出し（例: `利用モデル名：` / `① チャンキング：`）。 */
  label: string;
  /** 未選択時に表示するサーバーの既定モデル名。未取得なら空文字。 */
  defaultModel: string;
  /** 論理層（`llm.heavy_model`）の注記を出すか。エージェントのタブだけ。 */
  showHeavy: boolean;
}

/**
 * そのタブのヘッダーに並べるセレクタを返す。
 *
 * - エージェントの 3 タブ → 1 つ（既定は `ModelInfo.model`）
 * - データ管理タブ → 2 つ（① チャンキング / ② Q/A 作成）。どちらも既定は
 *   `ModelInfo.model`（`ChunkingRequest.model` / `QaGenerationRequest.model` を
 *   省略するとサーバーが同じ値で解決する。`core/data_jobs.py::_resolve_model()`）。
 */
export function headerSlots(tab: AppTab, info: ModelInfo | null): HeaderSlot[] {
  if (tab === 'data') {
    return [
      {
        slot: 'chunking',
        label: '① チャンキング：',
        defaultModel: info?.model ?? '',
        showHeavy: false,
      },
      {
        slot: 'qa',
        label: '② Q/A 作成：',
        defaultModel: info?.model ?? '',
        showHeavy: false,
      },
    ];
  }
  return [
    { slot: tab, label: MODEL_LABEL_PREFIX, defaultModel: info?.model ?? '', showHeavy: true },
  ];
}

/**
 * セレクタに表示する値。未選択ならサーバーの既定モデル名を出す。
 *
 * ⚠️ 未選択のまま空欄を表示しない。「何で走るか」が画面から消えるため。
 */
export function headerSelectValue(selected: string, defaultModel: string): string {
  return selected.trim() || defaultModel.trim();
}

export interface HeaderModelOption {
  id: string;
  label: string;
}

/**
 * セレクタの選択肢。GET /api/models の一覧に `supports_tool_calls` / `notes` を
 * 畳み込んだラベルを付けたもの（`modelOptionLabel`）。
 *
 * 既定モデルが一覧に無い（設定ファイルで選択肢外のモデルを指している）場合は
 * 先頭に足す。足さないと `<select>` の表示が別の選択肢へずれ、
 * 実際に走るモデルと画面が食い違う。
 */
export function headerModelOptions(
  models: ModelChoice[],
  defaultModel: string,
): HeaderModelOption[] {
  const options = models.map((m) => ({ id: m.id, label: modelOptionLabel(m) }));
  const name = defaultModel.trim();
  if (name && !models.some((m) => m.id === name)) {
    options.unshift({ id: name, label: name });
  }
  return options;
}

/**
 * 論理層だけ別モデルへ寄せている（`llm.heavy_model`）ときの注記。
 * 無ければ null。ヘッダーがセレクタになっても、この事実は隠さない。
 */
export function heavyModelNote(model: string, heavyModel: string): string | null {
  const heavy = heavyModel.trim();
  if (!heavy || heavy === model.trim()) return null;
  return `（論理層: ${heavy}）`;
}

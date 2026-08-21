// 3タブ共通の LLM モデルセレクタ。QueryForm（基本版・GRACE-Support）と
// ReviewForm（GRACE-Review）の両方から使う（重複防止）。
//
// 選択肢は GET /api/models（config.py::get_selectable_ollama_models()）から
// 取得したものだけ（Anthropic 系・tool calling 非対応モデルは含まれない）。
// 未選択（空文字）は「サーバーの既定値を使う」を意味し、buildQueryParams /
// ReviewForm の送信処理でそれぞれ null に変換される。
import type { ModelChoice } from '../types';

interface Props {
  models: ModelChoice[];
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}

export function ModelSelect({ models, value, onChange, disabled }: Props) {
  return (
    <label>
      モデル:
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
      >
        <option value="">（既定値）</option>
        {models.map((m) => (
          <option key={m.id} value={m.id}>
            {m.id}
          </option>
        ))}
      </select>
    </label>
  );
}

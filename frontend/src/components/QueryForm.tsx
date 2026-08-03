// 問い合わせ入力フォーム。CLI（agent_support_example.py）の引数と 1:1 に対応する。
//
//   query        → 問い合わせ入力
//   --vertical   → 業界プロファイル セレクタ（基本版タブでは出さない）
//   --no-web     → Web フォールバック トグル
//   --no-action  → アクション実行 トグル
//   --dry-run    → dry-run トグル
//   -v           → 詳細ログ トグル
//   --identity   → 本人確認の識別子（order_id / email）
//
// ⚠️ 判断の要るロジック（基本版での vertical 固定・識別子を送るかどうか・状態表示）は
//    `state/queryParams.ts` の純関数へ出してある（vitest で単体テスト済み）。
//    ここへ戻すとテストできなくなるので注意。
import { FormEvent, useState } from 'react';
import {
  buildQueryParams,
  identityNote,
  isIdentityActive,
} from '../state/queryParams';
import type { QueryParams, VerticalInfo } from '../types';

const BASIC_EXAMPLES: Array<{ label: string; query: string; vertical: string | null }> = [
  { label: 'パスワードを忘れました', query: 'パスワードを忘れました', vertical: null },
  { label: '領収書は発行できますか？', query: '領収書は発行できますか？', vertical: null },
];

const VERTICAL_EXAMPLES: Array<{ label: string; query: string; vertical: string | null }> = [
  { label: 'パスワードを忘れました', query: 'パスワードを忘れました', vertical: null },
  { label: 'gov: 住民票の写しの取り方は？', query: '住民票の写しの取り方は？', vertical: 'gov' },
  { label: 'ec: 返品したい', query: '返品したい', vertical: 'ec' },
  { label: 'saas: サービスが落ちています', query: 'サービスが落ちています', vertical: 'saas' },
];

interface Props {
  verticals: VerticalInfo[];
  running: boolean;
  onSubmit: (params: QueryParams) => void;
  /** 業界プロファイル セレクタを出すか。基本版タブでは false（vertical は常に null）。 */
  showVertical?: boolean;
}

export function QueryForm({ verticals, running, onSubmit, showVertical = true }: Props) {
  const [query, setQuery] = useState('');
  const [vertical, setVertical] = useState<string>('');
  const [dryRun, setDryRun] = useState(true);
  const [verbose, setVerbose] = useState(false);
  const [useWeb, setUseWeb] = useState(true);
  const [doAction, setDoAction] = useState(true);
  const [orderId, setOrderId] = useState('');
  const [email, setEmail] = useState('');

  // 本人確認が実際に起動するのは require_identity のプロファイルのときだけ。
  // 基本版（showVertical=false）は vertical を送らないので、常に起動しない。
  const selected = showVertical ? verticals.find((v) => v.id === vertical) : undefined;
  const requireIdentity = isIdentityActive(showVertical, selected?.require_identity);

  // 識別子欄は常に出すが、実際に照合されるかは設定次第なので状態を明示する。
  const note = identityNote(requireIdentity, dryRun);

  const examples = showVertical ? VERTICAL_EXAMPLES : BASIC_EXAMPLES;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!query.trim() || running) return;
    onSubmit(buildQueryParams({
      query, vertical, useWeb, doAction, dryRun, verbose,
      orderId, email, showVertical,
    }));
  };

  return (
    <form className="query-form" onSubmit={submit}>
      <div className="query-row">
        <input
          type="text"
          value={query}
          placeholder="問い合わせ内容を入力（例: パスワードを忘れました）"
          onChange={(e) => setQuery(e.target.value)}
          disabled={running}
        />
        <button type="submit" disabled={running || !query.trim()}>
          {running ? '実行中…' : '送信'}
        </button>
      </div>

      <div className="query-options">
        {showVertical && (
          <label>
            業界プロファイル:
            <select
              value={vertical}
              onChange={(e) => setVertical(e.target.value)}
              disabled={running}
            >
              <option value="">（なし）</option>
              {verticals.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.id}（{v.name}
                  {v.require_identity ? '・本人確認必須' : ''}）
                </option>
              ))}
            </select>
          </label>
        )}
        <label>
          <input
            type="checkbox"
            checked={useWeb}
            onChange={(e) => setUseWeb(e.target.checked)}
            disabled={running}
          />
          Web フォールバック（オフで内部RAGのみ・<code>--no-web</code> 相当）
        </label>
        <label>
          <input
            type="checkbox"
            checked={doAction}
            onChange={(e) => setDoAction(e.target.checked)}
            disabled={running}
          />
          アクション実行（オフで判定のみ・<code>--no-action</code> 相当）
        </label>
        <label>
          <input
            type="checkbox"
            checked={dryRun}
            onChange={(e) => setDryRun(e.target.checked)}
            disabled={running}
          />
          dry-run（アクションを実行せずログのみ・既定 ON）
        </label>
        <label>
          <input
            type="checkbox"
            checked={verbose}
            onChange={(e) => setVerbose(e.target.checked)}
            disabled={running}
          />
          詳細ログ（<code>-v</code> 相当）
        </label>
      </div>

      <fieldset className="identity-fields" disabled={running || !requireIdentity}>
        <legend>本人確認の識別子（<code>--identity</code> 相当）</legend>
        <label>
          order_id:
          <input
            type="text"
            value={orderId}
            placeholder="1001"
            onChange={(e) => setOrderId(e.target.value)}
          />
        </label>
        <label>
          email:
          <input
            type="text"
            value={email}
            placeholder="a@example.com"
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
      </fieldset>
      <p className={`identity-note${requireIdentity ? '' : ' muted'}`}>{note}</p>

      <div className="query-examples">
        {examples.map((example) => (
          <button
            key={example.label}
            type="button"
            className="example-chip"
            disabled={running}
            onClick={() => {
              setQuery(example.query);
              if (showVertical) setVertical(example.vertical ?? '');
            }}
          >
            {example.label}
          </button>
        ))}
      </div>
    </form>
  );
}

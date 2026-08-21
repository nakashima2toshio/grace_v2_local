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
// ⚠️ 判断の要るロジック（基本版での vertical 固定・識別子を送るかどうか・状態表示・
//    複数行入力の送信キー）は `state/queryParams.ts` / `state/submitKey.ts` の
//    純関数へ出してある（vitest で単体テスト済み）。
//    ここへ戻すとテストできなくなるので注意。
import { FormEvent, KeyboardEvent, useState } from 'react';
import {
  buildQueryParams,
  identityNote,
  isIdentityActive,
} from '../state/queryParams';
import { isSubmitKey } from '../state/submitKey';
import type { ModelChoice, QueryParams, VerticalInfo } from '../types';
import { ModelSelect } from './ModelSelect';

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
  models: ModelChoice[];
  running: boolean;
  onSubmit: (params: QueryParams) => void;
  /** 業界プロファイル セレクタを出すか。基本版タブでは false（vertical は常に null）。 */
  showVertical?: boolean;
  /**
   * 問い合わせ欄を複数行（textarea）にするか。基本版タブでは true。
   *
   * ⚠️ `showVertical` から導出しないこと。両者は別の関心事であり、
   * 「Support も複数行にしたい」となったときに解けなくなる。
   */
  multiline?: boolean;
}

export function QueryForm({
  verticals,
  models,
  running,
  onSubmit,
  showVertical = true,
  multiline = false,
}: Props) {
  const [query, setQuery] = useState('');
  const [vertical, setVertical] = useState<string>('');
  const [model, setModel] = useState<string>('');
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

  // 「どのプロファイルなら有効になるか」を案内文に出す（既定では ec だけ）。
  const identityVerticals = verticals.filter((v) => v.require_identity).map((v) => v.id);

  // 識別子欄は常に出すが、実際に照合されるかは設定次第なので状態を明示する。
  const note = identityNote({
    showVertical,
    vertical,
    requireIdentity: selected?.require_identity,
    dryRun,
    identityVerticals,
  });

  const examples = showVertical ? VERTICAL_EXAMPLES : BASIC_EXAMPLES;

  // 送信の経路は 3 つ（ボタン / 単一行の Enter による暗黙送信 / 複数行の
  // Ctrl+Enter）あるが、すべてここを通す。送信可否の条件を 1 箇所に保つため。
  const submitIfReady = () => {
    if (!query.trim() || running) return;
    onSubmit(buildQueryParams({
      query, vertical, model, useWeb, doAction, dryRun, verbose,
      orderId, email, showVertical,
      requireIdentity: selected?.require_identity,
    }));
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    submitIfReady();
  };

  // textarea では Enter が改行になり、フォームの暗黙送信が効かない。
  // Ctrl+Enter / ⌘+Enter を送信に割り当てる（判定は state/submitKey.ts）。
  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (!isSubmitKey(e)) return;
    e.preventDefault();
    submitIfReady();
  };

  return (
    <form className="query-form" onSubmit={submit}>
      <div className={`query-row${multiline ? ' multiline' : ''}`}>
        {multiline ? (
          <textarea
            value={query}
            placeholder={'問い合わせ内容を入力（複数行可）\n例: パスワードを忘れました'}
            rows={4}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={running}
          />
        ) : (
          <input
            type="text"
            value={query}
            placeholder="問い合わせ内容を入力（例: パスワードを忘れました）"
            onChange={(e) => setQuery(e.target.value)}
            disabled={running}
          />
        )}
        <button type="submit" disabled={running || !query.trim()}>
          {running ? '実行中…' : '送信'}
        </button>
      </div>
      {/*
        送信ショートカットは画面に出ていないと気付けない（placeholder は入力すると
        消える）。textarea のときだけ添える。
      */}
      {multiline && (
        <p className="query-hint">
          改行は Enter / 送信は <kbd>Ctrl</kbd>+<kbd>Enter</kbd>（macOS は{' '}
          <kbd>⌘</kbd>+<kbd>Enter</kbd>）
        </p>
      )}

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
        <ModelSelect models={models} value={model} onChange={setModel} disabled={running} />
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
        <legend>本人確認の識別子（<code>--identity</code> 相当）業界プロファイル＝ecの場合・有効</legend>
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

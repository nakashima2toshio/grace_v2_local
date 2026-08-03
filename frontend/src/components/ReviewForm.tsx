// 文書レビューの入力フォーム: 文書 textarea・RuleSet セレクタ・実行オプション。
import { FormEvent, useState } from 'react';
import type { ReviewParams, RuleSetInfo } from '../types';

// backend/app/schemas.py の MAX_DOCUMENT_CHARS と一致させる（超過は API が 422）。
const MAX_DOCUMENT_CHARS = 50000;

const EXAMPLES: Array<{ label: string; title: string; document: string }> = [
  {
    label: 'NG 例（優良誤認・薬機法）',
    title: '化粧品LP案',
    document:
      '当社の美容液は業界No.1の実力です。\n' +
      '\n' +
      '使い続ければシミが治ると評判で、副作用がないので誰でも安心してお使いいただけます。\n' +
      '\n' +
      '・今だけ期間限定 通常価格 12,000円 → 4,980円\n' +
      '・送料無料',
  },
  {
    label: 'OK 例（特商法表記あり）',
    title: '適正LP案',
    document:
      '当社の美容液は、うるおいを与えて肌をなめらかに整えます。\n' +
      '\n' +
      '■ 特定商取引法に基づく表記\n' +
      '販売業者: 株式会社サンプル\n' +
      '運営責任者: 山田太郎\n' +
      '所在地: 東京都千代田区1-1-1\n' +
      '電話番号: 03-0000-0000\n' +
      '販売価格: 4,980円（税込）\n' +
      '返品: 商品到着後8日以内、未開封に限り返品可能（送料はお客様負担）',
  },
];

interface Props {
  rulesets: RuleSetInfo[];
  running: boolean;
  onSubmit: (params: ReviewParams) => void;
}

export function ReviewForm({ rulesets, running, onSubmit }: Props) {
  const [document, setDocument] = useState('');
  const [title, setTitle] = useState('');
  const [ruleset, setRuleset] = useState<string>('ec_ad');
  const [useWeb, setUseWeb] = useState(false);
  const [dryRun, setDryRun] = useState(true);
  const [verbose, setVerbose] = useState(false);

  const tooLong = document.length > MAX_DOCUMENT_CHARS;
  const canSubmit = !!document.trim() && !tooLong && !running;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    onSubmit({
      document,
      document_title: title.trim() || '無題',
      ruleset: ruleset || null,
      use_web: useWeb,
      do_action: true,
      dry_run: dryRun,
      verbose,
    });
  };

  const selected = rulesets.find((r) => r.id === ruleset);

  return (
    <form className="review-form" onSubmit={submit}>
      <div className="review-row">
        <input
          type="text"
          value={title}
          placeholder="文書タイトル（例: 春キャンペーンLP案）"
          onChange={(e) => setTitle(e.target.value)}
          disabled={running}
        />
        <button type="submit" disabled={!canSubmit}>
          {running ? '点検中…' : '表示チェックを実行'}
        </button>
      </div>

      <textarea
        className="review-document"
        value={document}
        placeholder="点検したい広告文・LP・バナー原稿を貼り付けてください"
        rows={12}
        onChange={(e) => setDocument(e.target.value)}
        disabled={running}
      />
      <div className={`review-counter${tooLong ? ' over' : ''}`}>
        {document.length.toLocaleString()} / {MAX_DOCUMENT_CHARS.toLocaleString()} 文字
        {tooLong && '（上限を超えています。分割して実行してください）'}
      </div>

      <div className="query-options">
        <label>
          ルールセット:
          <select
            value={ruleset}
            onChange={(e) => setRuleset(e.target.value)}
            disabled={running}
          >
            {rulesets.map((r) => (
              <option key={r.id} value={r.id}>
                {r.id}（{r.name}・{r.rule_count} ルール）
              </option>
            ))}
          </select>
        </label>
        <label>
          <input
            type="checkbox"
            checked={useWeb}
            onChange={(e) => setUseWeb(e.target.checked)}
            disabled={running}
          />
          Web で法改正を裏取り（既定 OFF・条文が一次情報のため）
        </label>
        <label>
          <input
            type="checkbox"
            checked={dryRun}
            onChange={(e) => setDryRun(e.target.checked)}
            disabled={running}
          />
          dry-run（起票せずログのみ・既定 ON）
        </label>
        <label>
          <input
            type="checkbox"
            checked={verbose}
            onChange={(e) => setVerbose(e.target.checked)}
            disabled={running}
          />
          詳細ログ（-v 相当）
        </label>
      </div>

      {selected && (
        <p className="review-ruleset-note">
          対象法令: {selected.laws.join(' / ')} — 常時チェック {selected.always_check_count} 件
          （表記漏れの検出）。指摘の自動確定は支持率 {selected.notify_th} 以上。
        </p>
      )}

      <div className="query-examples">
        {EXAMPLES.map((example) => (
          <button
            key={example.label}
            type="button"
            className="example-chip"
            disabled={running}
            onClick={() => {
              setDocument(example.document);
              setTitle(example.title);
            }}
          >
            {example.label}
          </button>
        ))}
      </div>
    </form>
  );
}

// GRACE のローカル開発用 UI。4 つのメニューをタブで切り替える。
//
//   基本版         — 問い合わせ → 回答（業界特化なし・/api/support/*）
//   GRACE-Support  — 問い合わせ → 回答（業界プロファイル適用・/api/support/*）
//   GRACE-Review   — 文書 → 指摘（ルールセット適用・/api/review/*）
//   データ管理     — チャンキング → Qdrant 登録 → コレクション管理（/api/data/* ほか）
//
// 前 3 つは「エージェントを使う」側、最後の 1 つは「データを準備する」側で、
// モードが異なる。前者の並びは「業界特化を足していく順」で、基本版が素の
// パイプライン、Support は VerticalProfile、Review は RuleSet を差し替えたもの。
// この 2 つの業界定義はほぼ同型（collections / *_keywords / action_map /
// notify_th / confirm_th / prompt_addendum を共有）である。
//
// ⚠️ タブは**アンマウントで切り替える**（条件レンダリング）。各パネルが自分の
// reducer・SSE 購読・承認状態を持つため、離れた側の EventSource が
// useEffect のクリーンアップで確実に閉じる。
//
// モデルの選択はヘッダー（タイトル横）で行う。エージェントの 3 タブは 1 つ、
// データ管理タブは工程（チャンキング / Q/A 作成）ごとに 2 つ並べる。
// 選択は App の state にスロットごとに持つので、タブを切り替えても残る
// （判断は state/headerModel.ts の純関数）。
import { useEffect, useRef, useState } from 'react';
import { fetchModelInfo, fetchModels } from './api/client';
import {
  INITIAL_HEADER_MODELS,
  headerModelOptions,
  headerSelectValue,
  headerSlots,
  heavyModelNote,
  type HeaderModels,
} from './state/headerModel';
import { handleTabKeyDown } from './state/tabKeys';
import { DataPanel } from './components/DataPanel';
import { ReviewPanel } from './components/ReviewPanel';
import { SupportPanel } from './components/SupportPanel';
import type { ModelChoice, ModelInfo } from './types';

type Tab = 'basic' | 'support' | 'review' | 'data';

const TABS: Array<{ id: Tab; label: string; description: string }> = [
  { id: 'basic', label: '基本版', description: '問い合わせ → 回答（業界特化なし）' },
  { id: 'support', label: 'GRACE-Support', description: '問い合わせ → 回答（業界特化）' },
  { id: 'review', label: 'GRACE-Review', description: '文書 → 指摘（業界特化）' },
  { id: 'data', label: 'データ管理', description: 'チャンク化 → 登録 → コレクション管理' },
];

export default function App() {
  const [tab, setTab] = useState<Tab>('basic');
  const active = TABS.find((t) => t.id === tab) ?? TABS[0];
  // 矢印キーで移動したときにフォーカスも運ぶ（WAI-ARIA の tablist パターン）
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  // サーバーの既定モデル名。起動時に 1 回だけ取る（サーバ側の設定値）。
  // ⚠️ 取得に失敗しても画面は壊さない（バックエンド未起動でもタブ操作はできるべき）。
  const [modelInfo, setModelInfo] = useState<ModelInfo | null>(null);
  useEffect(() => {
    let alive = true;
    fetchModelInfo()
      .then((info) => {
        if (alive) setModelInfo(info);
      })
      .catch(() => {
        /* ヘッダーの装飾なので握りつぶす */
      });
    return () => {
      alive = false;
    };
  }, []);

  // モデルの選択肢（GET /api/models）と、スロットごとの選択（空文字 = サーバーの既定値）。
  // ⚠️ 選択肢の取得失敗は握りつぶしてよい。既定モデルだけの選択肢に縮退し、
  //    サーバーは設定どおりのモデルで走るので機能は失われない（選べないだけ）。
  const [models, setModels] = useState<ModelChoice[]>([]);
  const [headerModels, setHeaderModels] = useState<HeaderModels>(INITIAL_HEADER_MODELS);
  useEffect(() => {
    let alive = true;
    fetchModels()
      .then((list) => {
        if (alive) setModels(list);
      })
      .catch(() => {
        /* 上記のとおり縮退して動くので出さない */
      });
    return () => {
      alive = false;
    };
  }, []);
  const slots = headerSlots(tab, modelInfo);

  const onKeyDown = (event: React.KeyboardEvent, index: number) => {
    const next = handleTabKeyDown(event, index, TABS.length);
    if (next === null) return;
    setTab(TABS[next].id);
    tabRefs.current[next]?.focus();
  };

  return (
    <div className="app">
      <header>
        {/* タイトルの右へモデルのセレクタを並べる。ここで選んだ値が送信に使われる。
            エージェントの 3 タブは 1 つ、データ管理タブは工程ごとに 2 つ。 */}
        <div className="header-title">
          <h1>{active.label}</h1>
          {slots.map(({ slot, label, defaultModel, showHeavy }) => {
            const value = headerSelectValue(headerModels[slot], defaultModel);
            return (
              <label key={slot} className="model-badge">
                <span className="model-badge-label">{label}</span>
                <select
                  className="model-badge-select"
                  value={value}
                  onChange={(e) =>
                    setHeaderModels((prev) => ({ ...prev, [slot]: e.target.value }))
                  }
                >
                  {headerModelOptions(models, defaultModel).map((option) => (
                    <option key={option.id} value={option.id}>
                      {option.label}
                    </option>
                  ))}
                </select>
                {showHeavy && modelInfo !== null && heavyModelNote(value, modelInfo.heavy_model)}
              </label>
            );
          })}
        </div>
        <nav className="tabs" role="tablist" aria-label="エージェントとデータ準備">
          {TABS.map((t, index) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              id={`tab-${t.id}`}
              aria-selected={t.id === tab}
              aria-controls={`tabpanel-${t.id}`}
              // 選択中のタブだけを Tab キーの到達点にする（roving tabindex）
              tabIndex={t.id === tab ? 0 : -1}
              ref={(el) => {
                tabRefs.current[index] = el;
              }}
              className={`tab${t.id === tab ? ' active' : ''}`}
              onClick={() => setTab(t.id)}
              onKeyDown={(event) => onKeyDown(event, index)}
            >
              {t.label}
              <span className="tab-sub">{t.description}</span>
            </button>
          ))}
        </nav>
      </header>

      <div role="tabpanel" id={`tabpanel-${tab}`} aria-labelledby={`tab-${tab}`}>
        {tab === 'data' ? (
          <DataPanel chunkingModel={headerModels.chunking} qaModel={headerModels.qa} />
        ) : tab === 'review' ? (
          <ReviewPanel model={headerModels.review} />
        ) : (
          // 基本版と Support は同一パイプライン。variant で業界特化の有無だけを切り替える。
          <SupportPanel
            key={tab}
            variant={tab === 'basic' ? 'basic' : 'vertical'}
            model={headerModels[tab === 'basic' ? 'basic' : 'support']}
          />
        )}
      </div>
    </div>
  );
}

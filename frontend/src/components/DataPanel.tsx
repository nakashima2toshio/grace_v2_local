// データ管理タブのルート。パイプラインの流れ順にサブタブを並べる。
//
//   ① チャンキング → ② Qdrant 登録 → ③ コレクション管理
//
// エージェント 3 タブ（基本版 / Support / Review）が「エージェントを使う」側なのに対し、
// こちらは「データを準備する」側で、モードが違うので入れ子のタブにしてある。
//
// ⚠️ `key={sub}` は必須。無いと React が同じ位置のコンポーネントを再利用し、
// 前のサブタブの reducer 状態と SSE 購読が残る（App.tsx のタブ切替と同じ理由）。
import { useRef, useState } from 'react';

import { handleTabKeyDown } from '../state/tabKeys';
import { CollectionPanel } from './CollectionPanel';
import { DataJobPanel } from './DataJobPanel';

type SubTab = 'chunking' | 'register' | 'collections';

const SUB_TABS: Array<{ id: SubTab; label: string; description: string }> = [
  {
    id: 'chunking',
    label: '① チャンキング',
    description: 'CSV / テキスト → セマンティックチャンク CSV',
  },
  {
    id: 'register',
    label: '② Qdrant 登録',
    description: 'Q/A CSV → Qdrant コレクション（Embedding 生成つき）',
  },
  {
    id: 'collections',
    label: '③ コレクション管理',
    description: '一覧・プレビュー・削除（削除は承認が必要）',
  },
];

export function DataPanel() {
  const [sub, setSub] = useState<SubTab>('chunking');
  const active = SUB_TABS.find((t) => t.id === sub) ?? SUB_TABS[0];
  // 矢印キーで移動したときにフォーカスも運ぶ（WAI-ARIA の tablist パターン）
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const onKeyDown = (event: React.KeyboardEvent, index: number) => {
    const next = handleTabKeyDown(event, index, SUB_TABS.length);
    if (next === null) return;
    setSub(SUB_TABS[next].id);
    tabRefs.current[next]?.focus();
  };

  return (
    <>
      <nav className="sub-tabs" role="tablist" aria-label="データ準備の工程">
        {SUB_TABS.map((tab, index) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            id={`subtab-${tab.id}`}
            aria-selected={sub === tab.id}
            aria-controls={`subpanel-${tab.id}`}
            // 選択中のタブだけを Tab キーの到達点にする（roving tabindex）。
            // 未選択タブへは矢印キーで移動する
            tabIndex={sub === tab.id ? 0 : -1}
            ref={(el) => {
              tabRefs.current[index] = el;
            }}
            className={sub === tab.id ? 'active' : ''}
            onClick={() => setSub(tab.id)}
            onKeyDown={(event) => onKeyDown(event, index)}
          >
            {tab.label}
          </button>
        ))}
      </nav>
      <p className="tab-description">{active.description}</p>

      <div role="tabpanel" id={`subpanel-${sub}`} aria-labelledby={`subtab-${sub}`}>
        {sub === 'collections' ? (
          <CollectionPanel key={sub} />
        ) : (
          <DataJobPanel key={sub} variant={sub} />
        )}
      </div>
    </>
  );
}

# DataPanel.tsx - データ管理タブのルート ドキュメント

**Version 1.4** | 最終更新: 2026-09-24

---

## 目次

- [概要](#概要)
- [1. アーキテクチャ構成図](#1-アーキテクチャ構成図)
- [2. Props インターフェース](#2-props-インターフェース)
- [3. 状態管理](#3-状態管理)
- [4. データフロー・副作用](#4-データフロー副作用)
- [5. API 通信・SSE イベント](#5-api-通信sse-イベント)
- [6. ユーザー操作フロー](#6-ユーザー操作フロー)
- [7. 型定義とバックエンド対応](#7-型定義とバックエンド対応)
- [8. スタイル・アクセシビリティ](#8-スタイルアクセシビリティ)
- [9. テスト](#9-テスト)
- [10. 変更履歴](#10-変更履歴)

---

## 概要

| 項目 | 内容 |
|---|---|
| ファイル | `frontend/src/components/DataPanel.tsx` |
| 種別 | 状態保持コンポーネント（`useState` 1 個） |
| 親 | `App.tsx`（4 タブ目「データ管理」） |
| 子 | `DataJobPanel.tsx`（チャンキング / Q/A 作成 / 登録）、`CollectionPanel.tsx`（コレクション管理） |
| 主な依存 | `./CollectionPanel`, `./DataJobPanel` |
| 対応バックエンド | `backend/app/api/data.py`, `backend/app/api/qdrant.py` |

### 主な責務

- データ準備の 3 工程＋コレクション管理を**パイプラインの流れ順**にサブタブとして並べる。
- サブタブの切り替えでコンポーネントを**アンマウント**し、前工程の状態と SSE 購読を残さない。
- 各工程の説明文を出し、何をする画面かを明示する。

### 各責務対応のモジュール

| # | 責務 | 対応モジュール | 説明 |
|---|---|---|---|
| 1 | 工程順のサブタブ | `DataPanel.tsx` | ① チャンキング → ② Q/A 作成 → ③ Qdrant 登録 → ④ コレクション管理 |
| 2 | 切替時のアンマウント | `DataPanel.tsx` | 条件レンダリングで前工程の state と SSE 購読を残さない |
| 3 | 工程の説明文 | `DataPanel.tsx` | サブタブごとの説明を出す |

### なぜ入れ子のタブなのか

`App.tsx` の 4 タブのうち、前 3 つ（基本版 / Support / Review）は
**「エージェントを使う」**側で、このタブだけが**「データを準備する」**側である。
モードが違うため、同列に 6 タブ並べるのではなく入れ子にしてある。

```
App.tsx
 ├─ 基本版          ┐
 ├─ GRACE-Support   ├ エージェントを使う
 ├─ GRACE-Review    ┘
 └─ データ管理       ← データを準備する
     ├─ ① チャンキング
     ├─ ② Q/A 作成
     ├─ ③ Qdrant 登録
     └─ ④ コレクション管理
```

### ② Q/A 作成が後から入った経緯

v1.1 までサブタブは 3 つで、**Q/A 生成だけが CLI 専用**だった
（`qa_qdrant/make_qa_register_qdrant.py` の Phase 1）。
③ Qdrant 登録の入力は「既に作られた Q/A CSV」なので、
① と ③ の間が画面上で途切れていた。

v1.2 で `DataJobPanel` に `variant='qa'` を足し、同じ `QAPipeline` を
呼ぶジョブとして間を埋めた（CLI と結果は変わらない）。

### 主要機能一覧

| 機能 | 実装 | 説明 |
|---|---|---|
| サブタブ切替 | `useState<SubTab>` | 既定は `chunking`（パイプラインの先頭） |
| 説明文 | `active.description` | 選択中の工程が何をするか |
| アンマウント切替 | `key={sub}` | **必須**。無いと前工程の状態が残る |

---

## 1. アーキテクチャ構成図

### 1.1 システム全体での位置づけ

```mermaid
flowchart TB
    subgraph CALLER["呼び出し側"]
        APP["App.tsx<br>データ管理タブ"]
    end
    subgraph TARGET["対象コンポーネント"]
        DP["DataPanel.tsx<br>useState(sub)"]
    end
    subgraph EXTERNAL["外部（API・バックエンド）"]
        DJ["DataJobPanel.tsx<br>①〜③"]
        CP["CollectionPanel.tsx<br>④"]
        BE["backend api/data.py / api/qdrant.py"]
    end
    APP -->|"chunkingModel / qaModel"| DP
    DP -->|"variant"| DJ
    DP --> CP
    DJ --> BE
    CP --> BE
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class APP,DP,DJ,CP,BE default
style CALLER fill:#1a1a1a,stroke:#fff,color:#fff
style TARGET fill:#1a1a1a,stroke:#fff,color:#fff
style EXTERNAL fill:#1a1a1a,stroke:#fff,color:#fff
```

**データフロー**:

1. `App` のデータ管理タブで描画され、サブタブの選択だけを `useState` で持つ
2. 選択中の工程に応じて `DataJobPanel`（`variant` 指定）または `CollectionPanel` を描画する
3. API 呼び出しと SSE 購読は子パネルが行い、本コンポーネントは通信しない

### 1.2 コンポーネントツリー図

```mermaid
flowchart TB
    subgraph Root["ルート"]
        direction TB
        App["App.tsx<br>useState(tab)"]
    end
    subgraph Container["データ管理（本ドキュメント対象）"]
        direction TB
        DP["DataPanel.tsx<br>useState(sub)"]
    end
    subgraph Panels["工程別パネル"]
        direction TB
        DJ1["DataJobPanel<br>variant=chunking"]
        DJ2["DataJobPanel<br>variant=qa"]
        DJ3["DataJobPanel<br>variant=register"]
        CP["CollectionPanel<br>useReducer(dataReducer)"]
    end
    App -->|"tab === data"| DP
    DP -->|"variant / key=sub"| DJ1
    DP -->|"variant / key=sub"| DJ2
    DP -->|"variant / key=sub"| DJ3
    DP -->|"key=sub"| CP
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class App,DP,DJ1,DJ2,DJ3,CP default
style Root fill:#1a1a1a,stroke:#fff,color:#fff
style Container fill:#1a1a1a,stroke:#fff,color:#fff
style Panels fill:#1a1a1a,stroke:#fff,color:#fff
```

---

## 2. Props インターフェース

```typescript
export function DataPanel({
  chunkingModel = '',
  qaModel = '',
}: {
  /** ヘッダー（App）の「① チャンキング」で選んだモデル。空文字 = サーバーの既定値。 */
  chunkingModel?: string;
  /** ヘッダー（App）の「② Q/A 作成」で選んだモデル。空文字 = サーバーの既定値。 */
  qaModel?: string;
} = {})
```

| Prop | 型 | 必須 | 既定値 | 説明 |
|---|---|:---:|---|---|
| `chunkingModel` | `string` | | `''` | ヘッダーで選んだチャンキングのモデル。`DataJobPanel` へ素通し |
| `qaModel` | `string` | | `''` | ヘッダーで選んだ Q/A 作成のモデル。`DataJobPanel` へ素通し |

タブの選択状態は自分の `useState` が持ち、親（`App.tsx`）へは通知しない。
モデルは**ヘッダー（`App`）で選ぶ**ので、ここでは受け取った値を渡すだけである。

---

## 3. 状態管理

### 3.1 ローカル state（`useState`）

| 変数 | 型 | 初期値 | 更新契機 | 説明 |
|---|---|---|---|---|
| `sub` | `SubTab`（`'chunking' \| 'qa' \| 'register' \| 'collections'`） | `'chunking'` | サブタブのクリック | 表示中の工程 |

初期値が `'chunking'` なのは**パイプラインの先頭**だから。
初めて開いたユーザーが工程順に進めるようにしている。

### 3.2 reducer state（`useReducer`）

**なし。** ジョブの状態は子（`DataJobPanel` / `CollectionPanel`）がそれぞれ持つ。

### 3.3 親から渡る状態（props 由来）

| props | 出所 | 使い方 |
|---|---|---|
| `chunkingModel` / `qaModel` | `App.tsx` の `headerModels.chunking` / `.qa` | 読み取りのみ。`DataJobPanel` へ渡す |

---

## 4. データフロー・副作用

### 4.1 副作用一覧（`useEffect`）

**なし。** 購読・タイマー・API 呼び出しのいずれも行わない。

### 4.2 `key={sub}` が必須である理由

```tsx
{sub === 'collections' ? (
  <CollectionPanel key={sub} />
) : (
  <DataJobPanel
    key={sub}
    variant={sub}
    chunkingModel={chunkingModel}
    qaModel={qaModel}
  />
)}
```

`DataJobPanel` はチャンキング・Q/A 作成・登録の 3 工程で**同じコンポーネント**なので、
`key` が無いと React は同じ位置の要素として**インスタンスを再利用**する。
`variant` prop だけが変わり、以下がそのまま残る:

| 残るもの | 症状 |
|---|---|
| `dataReducer` の state | チャンキングの進捗が Q/A 作成タブに表示される |
| SSE 購読（`unsubscribeRef`） | 前工程の `EventSource` が開いたまま |
| フォームの入力値 | 入力ファイルやワーカー数が引き継がれる |

⚠️ **この不具合は型検査でも vitest でも捕まらない。**
`App.tsx` のタブ切替（基本版 ⇄ Support）とまったく同じ理由であり、
そちらでも `key={tab}` が必須になっている。

### 4.3 データフロー図

```mermaid
flowchart LR
    U["サブタブをクリック"] --> S["setSub(id)"]
    S --> K["key が変わる"]
    K --> UM["前のパネルをアンマウント<br>useEffect クリーンアップで SSE 解除"]
    UM --> MT["新しいパネルをマウント<br>初期状態から開始"]
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class U,S,K,UM,MT default
```

---

## 5. API 通信・SSE イベント

**該当なし。** `fetch` / `EventSource` を一切呼ばない。通信は子が行う。

---

## 6. ユーザー操作フロー

### 6.1 イベントハンドラ一覧

| 要素 | イベント | ハンドラ | 効果 | 無効化条件 |
|---|---|---|---|---|
| サブタブボタン | `click` | `setSub(tab.id)` | 表示中の工程を切り替える | **なし** |
| サブタブボタン | `keydown` | `onKeyDown(event, index)` | 左右/上下矢印で移動、Home/End で端へ。フォーカスも運ぶ | **なし** |

> **実行中でもタブを切り替えられる。** 切り替えるとパネルがアンマウントされ、
> `useEffect` のクリーンアップで SSE 購読が解除される。ジョブ自体はバックエンドで
> 走り続け、**戻ると `state/activeJobs.ts` に残した `job_id` で購読し直す**ので
> タイムラインごと復元される（[`DataJobPanel.md`](./DataJobPanel.md) §4.1.1）。

### 6.2 操作フロー図

```mermaid
flowchart TB
    Open["データ管理タブを開く"] --> C1["① チャンキング（既定）"]
    C1 -->|"チャンク CSV ができた"| C2["② Q/A 作成"]
    C2 -->|"Q/A CSV ができた"| C3["③ Qdrant 登録"]
    C3 -->|"コレクションができた"| C4["④ コレクション管理"]
    C4 -->|"確認・削除"| C4
    C1 -.->|"いつでも切替可"| C4
    C2 -.->|"いつでも切替可"| C1
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class Open,C1,C2,C3,C4 default
```

---

## 7. 型定義とバックエンド対応

| TS 型 | 定義元 | 対応 |
|---|---|---|
| `SubTab` | 本ファイル（module private） | UI 固有。バックエンドに対応物なし |

工程 ID（`chunking` / `qa` / `register`）は `DataJobKind` の一部と一致するが、
`collections` は**ジョブではない**ため型としては別物である
（`DataJobKind` にはこの 3 つに加えて `delete` がある）。

---

## 8. スタイル・アクセシビリティ

| 項目 | 内容 |
|---|---|
| スタイル方式 | プレーン CSS（`src/styles.css`） |
| 主要クラス | `.sub-tabs`, `.sub-tabs button.active`, `.tab-description` |
| ダークモード | 未対応 |

### アクセシビリティ・チェック

| 観点 | 状態 |
|---|---|
| フォーム要素に `label` が対応しているか | 該当なし（フォーム要素を持たない） |
| モーダルにフォーカストラップがあるか | 該当なし |
| 状態表示が色のみに依存していないか（記号併用） | ✅ 選択中のタブは `aria-selected` ＋ 太字 ＋ 背景色。ラベルに ①〜④ の番号も入る |
| キーボードのみで操作できるか | ✅ ネイティブ `<button>` なので Tab + Enter で切替可 |
| タブに `role` が付いているか | ✅ `role="tablist"` / `role="tab"` / `aria-selected` |
| タブが矢印キーで移動できるか | ✅ 左右/上下矢印で移動（端で回り込む）、Home/End で端へ。移動時は `preventDefault()` でページスクロールを止め、フォーカスも運ぶ |
| `tabpanel` が関連付けられているか | ✅ `aria-controls` / `role="tabpanel"` / `aria-labelledby` |
| 選択中タブだけが Tab キーの到達点か | ✅ roving tabindex（選択中 `0` / それ以外 `-1`）。タブ群を素通りして本文へ行ける |

矢印キーの移動先計算は `state/tabKeys.ts` の純関数（テスト済み）。`App.tsx` のタブと共用している。

---

## 9. テスト

| テストファイル | 対象 | 実行 |
|---|---|---|
| `src/state/dataReducer.test.ts` | 子が使う reducer（27 ケース） | `npm test` |
| `src/state/dataParams.test.ts` | 子が使うフォーム純関数（37 ケース） | `npm test` |
| `src/state/tabKeys.test.ts` | 矢印キーの移動先計算（12 ケース） | `npm test` |
| （本コンポーネントの専用テストなし） | — | — |

**専用テストは未整備。** `@testing-library/react` を導入していないため
JSX のレンダリングテストが書けず、`tsc --noEmit` でガードしている。

⚠️ **`key={sub}` の欠落は型検査でもテストでも検出できない。**
レンダリングテストを導入するなら、まずここを対象にすべきである。

---

## 10. 変更履歴

| 版 | 日付 | 変更内容 |
|---|---|---|
| 1.0 | 2026-08-05 | 初版作成 |
| 1.1 | 2026-08-05 | サブタブの矢印キー移動・roving tabindex・`role="tabpanel"` を追加。タブ離脱で進捗を失う記述を、再購読するよう修正 |
| 1.2 | 2026-09-05 | サブタブに **② Q/A 作成**（`DataJobPanel variant='qa'`）を追加し 4 つに。以降の番号を繰り下げ（Qdrant 登録 → ③ / コレクション管理 → ④） |
| 1.3 | 2026-09-23 | **モデルの選択をヘッダーへ移した**（grace_v2 と同じ変更）。`chunkingModel` / `qaModel` prop を受け取り `DataJobPanel` へ渡すようにした（Props なし → 2 つ） |
| 1.4 | 2026-09-24 | `a_react_page_md_format.md` v1.1 に追随（2026-09-24）。概要に「各責務対応のモジュール」（主な責務と 1:1）を追加し、`## 1.` を「アーキテクチャ構成図」として **1.1 システム全体での位置づけ（3 層）** と 1.2 コンポーネントツリー図の 2 枚構成にした。Mermaid の `classDef subgraphStyle` の欠落を補った |

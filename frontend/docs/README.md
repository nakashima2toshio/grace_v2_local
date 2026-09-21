# frontend/docs 棚卸し

**Version 1.2** | 最終更新: 2026-09-21

`frontend/`（Vite + React 18 + TypeScript）のドキュメント一覧と、実装への追随状況・
欠落・残タスクをまとめる。

> **なぜ索引が要るか**: `grace/docs/` と `backend/docs/` には棚卸し README があり、
> 実ファイルとの突き合わせができていた。**frontend/docs だけ索引が無かった**ため、
> コンポーネント 5 件の文書欠落（§3）が検知されずに残っていた。
> **新しいコンポーネントを足したら、この表にも行を足すこと。**

> **関連**: 形式仕様は [`.claude/skills/grace-agent-docs/a_react_page_md_format.md`](../../.claude/skills/grace-agent-docs/a_react_page_md_format.md) ／
> 純関数規約は CLAUDE.md §6 ／ 姉妹リポジトリとの乖離は CLAUDE.md §5

---

## 目次

- [1. 現在わかっている問題](#1-現在わかっている問題)
- [2. 文書一覧](#2-文書一覧)
- [3. 実装カバレッジ](#3-実装カバレッジ)
- [4. state/ 純関数の一覧](#4-state-純関数の一覧)
- [5. テスト件数（実測）](#5-テスト件数実測)
- [6. 検証手順](#6-検証手順)
- [7. 残タスク](#7-残タスク)
- [8. 変更履歴](#8-変更履歴)

---

## 1. 現在わかっている問題

| # | 問題 | 状態 |
|---|---|---|
| 1 | `ReviewPanel` / `ReviewForm` / `ModelSelect` / `JobClock` / `MetaErrorBanner` に対応する文書が無い | ✅ 解消（5 件を新規作成。§3） |
| 2 | `frontend/docs` に索引が無く、欠落を検知できなかった | ✅ 解消（本書） |
| 3 | `review_ui.md` が対応する `.tsx` を持たず、命名規則（`<Component>.md`）から外れている | ⚠️ **横断文書**として意図的に置いている（§2.4） |
| 4 | `ReviewPanel` の打ち切り警告・エラーバナーに `role` が無い | ❌ 未対応（§7-1） |

---

## 2. 文書一覧

### 2.1 コンテナコンポーネント（状態・副作用・API を束ねる）

| 文書 | 対象 | 実装行数 | 版 | 重要度 |
|---|---|---:|---|:--:|
| `SupportPanel.md` | `components/SupportPanel.tsx` — 基本版 / GRACE-Support 共用 | 199 | 1.4 | ★★★ |
| `DataJobPanel.md` | `components/DataJobPanel.tsx` — データ準備ジョブ | 758 | 1.3 | ★★★ |
| `DataPanel.md` | `components/DataPanel.tsx` — データ管理タブの枠 | 94 | 1.2 | ★★ |
| `CollectionPanel.md` | `components/CollectionPanel.tsx` — コレクション管理 | 416 | 1.1 | ★★ |
| `App.md` | `App.tsx` — タブ切替とパネルの振り分け | 117 | 1.0 | ★★ |
| `ReviewPanel.md` | `components/ReviewPanel.tsx` — GRACE-Review 本体 | 217 | 1.1 | ★★★ |

### 2.2 入力・モーダル

| 文書 | 対象 | 実装行数 | 版 | 重要度 |
|---|---|---:|---|:--:|
| `QueryForm.md` | `components/QueryForm.tsx` | 281 | 1.2 | ★★★ |
| `ConfirmModal.md` | `components/ConfirmModal.tsx` — HITL アクション承認 | 142 | 1.1 | ★★ |
| `QuestionSelectModal.md` | `components/QuestionSelectModal.tsx` — 0-(A) 主質問の選択 | 76 | 1.0 | ★★ |
| `ReviewForm.md` | `components/ReviewForm.tsx` | 254 | 1.1 | ★★ |
| `ModelSelect.md` | `components/ModelSelect.tsx` — 3 タブ共通のモデルセレクタ | 52 | 1.1 | ★★ |

### 2.3 表示コンポーネント

| 文書 | 対象 | 実装行数 | 版 | 重要度 |
|---|---|---:|---|:--:|
| `AnswerCard.md` | `components/AnswerCard.tsx` | 250 | 1.2 | ★★★ |
| `FindingList.md` | `components/FindingList.tsx` | 141 | 1.1 | ★★ |
| `Markdown.md` | `components/Markdown.tsx`（`markdown/parseMarkdown.ts`） | 114 | 1.1 | ★★ |
| `Timeline.md` | `components/Timeline.tsx` | 81 | 1.0 | ★★ |
| `StepTimeline.md` | `components/StepTimeline.tsx` | 45 | 1.0 | ★★ |
| `ReviewTimeline.md` | `components/ReviewTimeline.tsx` | 64 | 1.0 | ★ |
| `DocumentView.md` | `components/DocumentView.tsx` | 62 | 1.1 | ★ |
| `JobClock.md` | `components/JobClock.tsx` — 開始行 / 完了行 | 47 | 1.0 | ★ |
| `MetaErrorBanner.md` | `components/MetaErrorBanner.tsx` — メタ取得失敗の表示 | 24 | 1.0 | ★ |

### 2.4 横断文書

| 文書 | 内容 | 版 | 備考 |
|---|---|---|---|
| `review_ui.md` | GRACE-Review 画面全体の設計を俯瞰する**横断文書** | 1.0 | 対応する `.tsx` は無い。個別仕様は各 `<Component>.md` が正 |

---

## 3. 実装カバレッジ

`frontend/src/components/*.tsx` は **19 件**、`App.tsx` を加えて **20 件**。
対応する `<Component>.md` も **20 件**で、**欠落は無い**（2026-09-20 に 5 件を新規作成）。

| コンポーネント | 文書 | 作成日 |
|---|---|---|
| `ReviewPanel.tsx` | `ReviewPanel.md` | 2026-09-20 |
| `ReviewForm.tsx` | `ReviewForm.md` | 2026-09-20 |
| `ModelSelect.tsx` | `ModelSelect.md` | 2026-09-20 |
| `JobClock.tsx` | `JobClock.md` | 2026-09-20 |
| `MetaErrorBanner.tsx` | `MetaErrorBanner.md` | 2026-09-20 |

> 📌 `main.tsx`（10 行）・`types.ts`（419 行）・`api/client.ts`（302 行）には個別文書が無い。
> `types.ts` はバックエンドのスキーマと 1:1 で `backend/docs/` 側が正、
> `api/client.ts` は各パネル文書の「API 通信」節が実質の記述である。**意図的に持たない。**

> ⚠️ **`MetaErrorBanner.tsx` は 2026-09-20 に grace_v2 から移植したが、
> そのとき文書を作っていなかった。** コンポーネントを足したら同じコミットで
> `<Component>.md` と本書の表も更新すること（CLAUDE.md §6）。

---

## 4. state/ 純関数の一覧

CLAUDE.md §6 のとおり、**判断ロジックはコンポーネントに残さず `state/` の純関数へ出す**
（vitest は `.test.tsx` を収集しないため、コンポーネント内の分岐はテストできない）。

| モジュール | 行数 | 切り出した判断 |
|---|---:|---|
| `dataReducer.ts` | 225 | データ準備ジョブの状態遷移 |
| `dataParams.ts` | 202 | データ準備フォーム → API パラメータ（未選択モデルのキー省略を含む） |
| `reviewReducer.ts` | 191 | Review ジョブの状態遷移 |
| `elapsed.ts` | 180 | 所要時間の整形・サーバ権威タイムスタンプの採否 |
| `jobReducer.ts` | 173 | Support ジョブの状態遷移 |
| `queryParams.ts` | 126 | 送信ペイロードの組み立て・基本版の vertical 固定・モデル未選択の null 化 |
| `formMemory.ts` | 119 | タブ切替時の入力退避と復元（選んだモデルを含む） |
| `citations.ts` | 105 | 出典の派生値 |
| `highlight.ts` | 89 | 引用箇所のハイライト |
| `useJobTiming.ts` | 56 | **例外的にフック**。判断は持たず `elapsed.ts` に委ねる |
| `modelLabel.ts` | 85 | モデル名の表示文字列（ヘッダー・「（既定値: …）」・**選択肢のラベル**） |
| `metaFetch.ts` | 53 | メタ取得失敗 → 対処可能な文言 |
| `documentLimit.ts` | 52 | 文字数上限の判定・表示文言・アナウンス文言 |
| `selectionKeys.ts` | 63 | 指摘の選択キー（Enter / Space・IME 変換中は発火しない）と選択トグル |
| `focusTrap.ts` | 62 | モーダル内の Tab 移動先（端で巻き戻す） |
| `tabKeys.ts` | 49 | タブの矢印キー移動 |
| `submitKey.ts` | 49 | 送信キー（IME 変換中は送信しない） |
| `activeJobs.ts` | 45 | 実行中ジョブの派生値 |
| `timelineAnnounce.ts` | 43 | 支援技術へ読み上げる 1 行 |
| `interventionKind.ts` | 36 | 承認待ちが action か question か |

> `markdown/parseMarkdown.ts`（250 行）も同じ方針の純関数（`Markdown.md` が担当）。

---

## 5. テスト件数（実測）

**2026-09-21 に `cd frontend && npm test` を実行した実測値。記憶で書かないこと。**

```
Test Files  22 passed (22)
     Tests  333 passed (333)
```

| テストファイル | 件数 |
|---|---:|
| `state/dataParams.test.ts` | 38 |
| `state/dataReducer.test.ts` | 27 |
| `state/queryParams.test.ts` | 27 |
| `state/elapsed.test.ts` | 22 |
| `state/citations.test.ts` | 20 |
| `components/ReviewForm.examples.test.ts` | 17 |
| `state/serverTiming.test.ts` | 16 |
| `markdown/parseMarkdown.test.ts` | 16 |
| `state/formMemory.test.ts` | 13 |
| `state/focusTrap.test.ts` | 12 |
| `state/selectionKeys.test.ts` | 9 |
| `state/highlight.test.ts` | 13 |
| `state/modelLabel.test.ts` | 20 |
| `state/reviewReducer.test.ts` | 13 |
| `state/tabKeys.test.ts` | 12 |
| `state/documentLimit.test.ts` | 10 |
| `state/metaFetch.test.ts` | 10 |
| `state/submitKey.test.ts` | 10 |
| `state/timelineAnnounce.test.ts` | 9 |
| `state/activeJobs.test.ts` | 8 |
| `state/jobReducer.test.ts` | 7 |
| `state/interventionKind.test.ts` | 4 |

> ⚠️ `serverTiming.test.ts` が検証するのは `elapsed.ts`（`state/serverTiming.ts` は**存在しない**）。
> ファイル名から実装を推測しないこと。

---

## 6. 検証手順

```bash
cd frontend
npm run lint     # tsc --noEmit
npm test         # vitest run
npm run build    # 本番ビルド
```

3 つとも CI の blocking ゲート（`frontend (tsc + vitest + build)`）に含まれる。
**バックエンドの API スキーマを変えたら `src/types.ts` も必ず追随させる**
（Python 側が全部緑でも型エラー 1 個でマージは止まる。CLAUDE.md §4）。

---

## 7. 残タスク

| # | 内容 | 優先 |
|---|---|:--:|
| 1 | ~~`ReviewPanel` の打ち切り警告とエラーバナーに `role` が無い。`SupportPanel` の `.error-banner` も同様~~ | ✅ **完了**（2026-09-21）。エラー 2 箇所に `role="alert"`、打ち切り警告に `role="status"`（結果と同時に描画されるため割り込ませない） |
| 2 | ~~`ConfirmModal` にフォーカストラップが無い~~ | ✅ **完了**（2026-09-21）。Tab / Shift+Tab が端で巻き戻り、マウント時に承認ボタンへ焦点が移る。移動先の計算は `state/focusTrap.ts`（12 ケース） |
| 3 | ~~`DocumentView` / `FindingList` の 2 ペイン相互ジャンプがキーボードで操作できない~~ | ✅ **完了**（2026-09-21）。両方を `role="button"` ＋ `tabIndex={0}` ＋ `aria-pressed` にし、Enter / Space で発火。判定は `state/selectionKeys.ts`（9 ケース）。焦点は**破線**、選択中は実線で区別する |
| 4 | ~~`ModelSelect` が `ModelChoice.supports_tool_calls` / `notes` を表示していない~~ | ✅ **完了**（2026-09-21）。選択肢のラベルへ畳み込んだ（`state/modelLabel.ts::modelOptionLabel`）。`notes` が既に tool calling に触れていれば重ねない |
| 5 | ~~`ReviewForm` の textarea に送信ショートカットが無い~~ | ✅ **完了**（2026-09-21）。`QueryForm` と同じ `state/submitKey.ts` を共用し、Ctrl+Enter / ⌘+Enter で実行（**IME 変換中は送信しない**挙動も同じ） |

**残タスクは 0 件になった**（2026-09-21）。新しく見つけたら、実装との突き合わせのうえでここへ足すこと。

> 📌 banner 系の `role` は `CollectionPanel`（4 箇所）・`DataJobPanel`（1 箇所）・
> `MetaErrorBanner`（1 箇所）に加え、**`ReviewPanel`（2 箇所）・`SupportPanel`（1 箇所）**
> にも付いた（2026-09-21）。
>
> 📌 `ConfirmModal` に残る a11y の ❌ 2 件（`Escape` で閉じない・閉じたあとのフォーカス復帰）は
> **判断のうえで未対応**であり、実装漏れではない（`ConfirmModal.md` §8）。
>
> 📌 `.running-banner` に `role` を足さないのは**意図的**である。実行中であることは
> `Timeline` の `aria-live` が読み上げており、バナーにも付けると二重読み上げになる。

---

## 8. 変更履歴

| 版 | 日付 | 変更内容 |
|---|---|---|
| 1.2 | 2026-09-21 | **残タスク 4・5（低優先）を完了し、§7 は 0 件になった**。`ModelSelect` が `supports_tool_calls` / `notes` を選択肢へ出すようにし（`modelOptionLabel`・7 ケース追加）、`ReviewForm` の textarea に Ctrl+Enter / ⌘+Enter を付けた（`submitKey.ts` を `QueryForm` と共用）。テストは 326 → **333 件**（実測） |
| 1.1 | 2026-09-21 | **a11y の残タスク 3 件（中優先）を完了**。`state/` 純関数に `selectionKeys.ts` / `focusTrap.ts` を追加（計 20 件）し、テストは 20 ファイル / 305 件 → **22 ファイル / 326 件**（実測）。§2 の Ver 列を 5 文書ぶん更新（`DocumentView` 1.1 / `FindingList` 1.1 / `ConfirmModal` 1.1 / `ReviewPanel` 1.1 / `SupportPanel` **1.4** — 最後の 1 件はヘッダーが 1.0 のまま変更履歴だけ 1.3 まで進んでいたので実態へ揃えた） |
| 1.0 | 2026-09-20 | 初版作成。欠落していた 5 件（`ReviewPanel` / `ReviewForm` / `ModelSelect` / `JobClock` / `MetaErrorBanner`）を新規作成して解消。文書一覧・実装カバレッジ・`state/` 純関数 18 件・テスト件数（`npm test` の実測 20 ファイル / 305 件）・残タスク 5 件を記載 |

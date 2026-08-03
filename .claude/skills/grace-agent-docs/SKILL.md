---
name: grace-agent-docs
description: >-
  Author or update Japanese module/component documentation for grace_v2 and the
  sibling *_grace_agent repos. Use when writing or modernizing docs under
  <package>/docs/*.md, backend/docs/, frontend React component docs, or the
  top-level readme_*.md / docs/*.md, when asked to follow
  `a_class_method_md_format.md` (Python modules/classes),
  `a_react_page_md_format.md` (React components) or `a_pages_md_format.md`
  (Streamlit UI pages), or when adding Mermaid diagrams. Encodes the IPO doc
  format, the React/Streamlit page formats, the mandatory black-background
  Mermaid style, and the unified tech-stack terminology.
---

# grace_agent ドキュメント作成スキル

日本語RAG/GRACEプロジェクト群（grace_v2 ほか）のモジュール／画面ドキュメントを、
プロジェクト規約どおりに作成・最新化するための知見。

## 0. どのフォーマット仕様を使うか（必ず先に判定）

対象によって**使う仕様書が異なる**。いずれもスキル同梱（`.claude/skills/...`）で、
書く前に該当仕様を**実際に読むこと**。

| 対象 | 使う仕様書 | 中心構造 | ドキュメント所在 |
|------|-----------|---------|------------------|
| Python モジュール（クラス/関数） | `a_class_method_md_format.md` | IPO（Input-Process-Output） | `<package>/docs/<module>.md` |
| **React コンポーネント**（`frontend/src/**`） | **`a_react_page_md_format.md`** | コンポーネントツリー＋props＋3層状態＋SSE | `frontend/docs/<Component>.md` |
| Streamlit 画面（`ui/pages/*.py`） | `a_pages_md_format.md` | 画面レイアウト＋セッション状態＋操作フロー | `ui/pages/docs/<page>.md` |
| 単体テスト | `.claude/skills/grace-agent-tests/a_test_md_format.md` | SAE（Setup-Action-Expected） | grace-agent-tests スキル参照 |

> ⚠️ **grace_v2 に Streamlit は存在しない。** フロントエンドは `frontend/`（Vite + React + TS）。
> `a_pages_md_format.md` は他リポジトリ（`*_grace_agent` の `ui/pages/`）用に残してあるだけで、
> **grace_v2 で UI ドキュメントを書くときは必ず `a_react_page_md_format.md` を使う。**
>
> テスト仕様（SAE）は **grace-agent-tests** スキルが担当。
> 開発メモ・サンプルQ&A等の参考資料は `.claude/skills/grace-agent-docs/a_memo_dev.txt`。

## 1. モジュール仕様（`a_class_method_md_format.md`・IPO形式）— 必読
- 仕様書はスキル同梱 `.claude/skills/grace-agent-docs/a_class_method_md_format.md`（IPO形式）。**先に読むこと**。
- タイトル: `# <module>.py - <説明> ドキュメント` → 次行 `**Version X.X** | 最終更新: YYYY-MM-DD`。
- 必須セクション順:
  1. 目次
  2. 概要（`### 主な責務` 箇条書き → `### 各責務対応のモジュール` 表 → `### 主要機能一覧` 表）
  3. アーキテクチャ構成図（Mermaid・3層）
  4. モジュール構成図（Mermaid）
  5. クラス・関数一覧表
  6. クラス・関数 IPO詳細：各要素に **概要 / シグネチャ / パラメータ表 / IPOテーブル(Input・Process・Output) / 戻り値例 / 使用例** を必ず付ける
  7. 設定・定数（あれば）
  8. 使用例（ワークフロー）
  9. エクスポート（`__all__`）
  10. 変更履歴（表。版を上げたら必ず追記）
  11. 付録: 依存関係図（Mermaid）
- 横断的な「まとめ」ドキュメントは IPO を各モジュール doc に委ね、本文はアーキテクチャ＋データフロー＋リンク集に徹してよい。

## 1B. React コンポーネント仕様（`a_react_page_md_format.md`）— grace_v2 の UI はこちら

`frontend/src/**` の `.tsx` / `.ts` は IPO ではなく**コンポーネント特化フォーマット**を使う。
タイトルは `# <Component>.tsx - <説明> ドキュメント` → `**Version X.X** | 最終更新: YYYY-MM-DD`。

- 必須セクション順:
  1. 目次
  2. 概要（メタ表＝ファイル/種別/親/子/主な依存/対応バックエンド ＋ `### 主な責務` ＋ `### 主要機能一覧` 表）
  3. コンポーネントツリー図（Mermaid。**各ノードに保持 state を併記**、矢印ラベルは `"props / コールバック"`）
  4. Props インターフェース（`interface Props` を実コードから転記 → 表に展開 ＋ **コールバックの契約表**）
  5. 状態管理（**3層を分けて記述**: ローカル `useState` ／ reducer state ＋アクション一覧＋状態遷移図 ／ props 由来）
  6. データフロー・副作用（`useEffect` の**依存配列とクリーンアップを必ず表に**）
  7. API 通信・SSE イベント（API 一覧表 ＋ **`SupportEvent.type` の網羅表** ＋ sequenceDiagram）
  8. ユーザー操作フロー（イベントハンドラ一覧＝**`disabled` の無効化条件を必須列に** ＋ 操作フロー図）
  9. 型定義とバックエンド対応（`src/types.ts` ↔ `backend/app/schemas.py` の対応表）
  10. スタイル・アクセシビリティ（**未対応項目も ❌ で残す**）
  11. テスト
  12. 変更履歴
- **Streamlit 版との最大の違いは状態の持ち方**。`st.session_state` の単一辞書に対し、
  React は props / `useState` / reducer の 3 層。混ぜて 1 表にしない。
- 状態遷移図は `stateDiagram-v2`（**`classDef` 非対応なのでスタイル指定を付けない**）。
- 実装整合: `interface Props`・`useState` 初期値・`useEffect` 依存配列・クリーンアップ関数の
  有無を**実コードと突合**する。特に **SSE の購読解除漏れ**はこのプロジェクトで最も
  起きやすいバグなので、`subscribeStream` の戻り値を `useEffect` が返しているか必ず確認する。

## 2. Mermaid 黒背景・白文字（CLAUDE.md §5 / 各仕様書の Mermaid 節）— 必須
- flowchart/graph はブロック末尾に必ず:
  - `classDef default fill:#000,stroke:#fff,color:#fff`
  - `classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff`
  - 全ノード `class <id,...> default`
  - 各サブグラフ `style <Subgraph> fill:#1a1a1a,stroke:#fff,color:#fff`
- sequenceDiagram は先頭に `%%{ init: { "theme":"base", "themeVariables": { ...黒テーマ... } } }%%` を付け、`classDef`/`class` は使わない。
  - **すべての要素を黒背景・白文字に統一する**: `background`/`mainBkg`/`actorBkg`/**`noteBkgColor` を `#000000`**、`textColor`/`actorTextColor`/`noteTextColor` を `#ffffff`、`noteBorderColor` を `#ffffff`。Note（`Note over` 等）の背景も `#000000` とし、`#1a1a1a` は使わない。
  - ⚠️ **Note 背景の変数名は `noteBkgColor`（`noteBkg` ではない）**。`noteBkg` は Mermaid に認識されず既定の黄色（`#fff5ad`）になるため、Note ボックスが黄色背景で描画される不具合の原因になる。
- `stateDiagram-v2` は `classDef`/`class` に非対応 → **スタイル指定を付けない**。
- ノードラベルの特殊文字はダブルクォートで囲む。バッククォート禁止。`<br>` は可。
  **TS の総称型（`Record<StepId, StepState>` 等）は `<` `>` がタグ解釈されうる** ため、
  ダブルクォートで囲んだうえで可能なら `Record[StepId, StepState]` へ置換する。
- 検証（grep）: 各ファイルで `flowchart|graph` の数 == `classDef default fill:#000` の数、`sequenceDiagram` の数 == `%%{ init` の数。

## 3. 技術スタック表記の統一（CLAUDE.md §3）
- LLM = **Anthropic Claude**、既定 `claude-sonnet-4-6`（軽量 `claude-haiku-4-5-20251001`）。鍵 `ANTHROPIC_API_KEY`。
- Embedding = **Gemini** `gemini-embedding-001`（3072次元）。鍵 `GOOGLE_API_KEY`。
- LLM設定クラスは `ModelConfig`（`config.py`）。`text-embedding-3-*` を LLM/本番Embedding用途で書かない。
- モデル名マッピングを作らない（CRITICAL RULES）。`responses.parse()`/`create()` は両方正。
- フロントは **Vite + React 18 + TypeScript**（`npm run dev` / Vite dev サーバ :5173）。
  Streamlit・Next.js とは書かない。

## 4. 実装との整合（重要）
- 書く前に**対応ソースを実際に読む**。シグネチャ・既定値・`__all__`・`interface Props` を突合。
- **廃止ファイルを参照しない**（grace_v2 に**存在しない**）: `setup.py` / `server.py` /
  a-prefixed scripts（`a30_qdrant_registration.py` 等） / `agent_rag.py` / `ui/` / `start_celery.sh`。
- 現行のエントリポイント:

  | 用途 | コマンド |
  |---|---|
  | 開発サーバ一括起動（backend + frontend） | `./run_dev.sh` → UI `:5173` / API `:8000` |
  | バックエンド単体 | `uvicorn backend.app.main:app --reload --port 8000` |
  | CLI 実行 | `uv run python agent_support_example.py --vertical gov -v "<質問>"` |
  | チャンク化 | `python -m chunking.csv_text_to_chunks_text_csv` |
  | Q/A生成＋登録 | `python qa_qdrant/make_qa_register_qdrant.py`（登録のみ `register_to_qdrant.py`） |
  | Qdrant 起動 | `docker-compose -f docker-compose/docker-compose.yml up -d` |

- データ準備パイプラインは3段階（チャンキング→Q/A生成→Qdrant登録）。チャンキングは
  文書境界保証（`load_documents_from_csv`/`doc_id`）・`continuity_mode="rule"`・
  `max_chunk_tokens=512`・manifest出力。
- **Web API と CLI は同じ `run_support_agent_core`（`backend/app/core/support_agent.py`）を通る。**
  「CLI だけ / Web だけ」の分岐は無いので、片方で検証した挙動は他方にも当てはまる。

## 5. ドキュメントの所在（**`docs`（複数形）に統一**）

| 領域 | 所在 |
|---|---|
| Python モジュール（IPO） | `<package>/docs/<module>.md` — `chunking/docs/`, `qa_generation/docs/`, `qa_qdrant/docs/`, `services/docs/`, `grace/docs/`, `grace/step_trace/docs/` |
| backend | `backend/docs/` |
| React コンポーネント | `frontend/docs/<Component>.md`（未作成なら新規に切る） |
| 横断/利用ガイド・設計メモ | リポジトリ直下 `docs/`（`performance_levers.md`, `reasoning_flow.md` 等） |

> **単数形 `doc/` は使わない。** 過去に `<package>/doc/` と `<package>/docs/` が混在していたが
> `docs/` へ統一済み。新規ディレクトリも必ず `docs/` で切る。

## 6. 進め方のコツ
- 複数ファイルを最新化するときも、**既定は本体で 1 ファイルずつ順に処理する**。サブエージェントは
  呼び出し元の文脈を引き継がず毎回ゼロから調べ直すため割高であり、**ユーザーが並列実行を
  明示的に指示した場合のみ**使う。その場合はファイル単位で起動し、各エージェントに
  「**使うフォーマット仕様のパス**（Python=`a_class_method_md_format.md` /
  React=`a_react_page_md_format.md` / Streamlit=`a_pages_md_format.md`、いずれも
  `.claude/skills/grace-agent-docs/` 配下）＋対象ソース＋黒背景Mermaid規約＋スタック表記」を漏れなく渡す。
- 仕上げに mermaid 準拠を grep 検証（`flowchart|graph` 数 == `classDef default fill:#000` 数、`sequenceDiagram` 数 == `%%{ init` 数）し、版・最終更新日・変更履歴を更新。

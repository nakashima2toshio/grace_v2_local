# CLAUDE.md

このファイルは Claude Code（claude.ai/code）が本リポジトリで作業するときの指針です。

---

## ⚠️ ファイル書き込みポリシー

### GitHub ブランチ操作：全許可
- ブランチへのコミット・プッシュ・PR作成・master へのマージを確認なしで実行してよい。
- **指定ブランチ以外への push や force push は事前に確認すること。**

### ローカルファイル操作：作業範囲内許可
- タスクに関連するファイルの新規作成・編集は確認なしで実行してよい。
- タスクと無関係なファイルへの書き込みは事前に確認すること。
- **ファイルの削除など不可逆的な操作は事前に確認すること。**

---

## ⚠️ 作業原則（最重要）

- **必ずコードをよく読んでから判断する。** 「現状コード＋慎重さ」を優先して**読まずに**進めると、
  バグにバグを重ねることになる（実際にそれで 1 日溶かした事例あり）。
- 修正・調査の前に、関連する実コード（クライアント生成・既定モデル・呼び出し経路・
  プロバイダ解決）を実際に追って確認すること。「たぶん意図的」で確認を打ち切らない。
- **やっていない検証を「やった」と書かない。** テストが落ちたら落ちたと出力付きで報告する。
- 回帰修正を入れるときは、**修正前のコードに当ててテストが fail することを確認**する。
  fail しないテストは回帰を捕まえていない。

---

## 1. プロジェクト概要

**GRACE-Support** — 業界特化・自律型サポートエージェント。日本語 RAG（Retrieval-Augmented
Generation）に、根拠検証（groundedness）・Web 裏取り・HITL（Human-In-The-Loop）アクションを
組み合わせたシステム。

| 層 | 実体 |
|---|---|
| フロントエンド | `frontend/` — Vite + React 18 + TypeScript（dev: `:5173`） |
| Web API | `backend/app/` — FastAPI（dev: `:8000`）。SSE でステップ進捗を配信 |
| パイプライン中核 | `backend/app/core/support_agent.py::run_support_agent_core` |
| 自律エージェント基盤 | `grace/` — planner / executor / confidence / intervention / replan / tools |
| ツール・検索 | `agent_tools.py`, `agent_parallel_search.py`, `agent_cache.py`, `qdrant_client_wrapper.py` |
| データ準備 | `chunking/`, `qa_generation/`, `qa_qdrant/` |
| ベクトルDB | Qdrant（`docker-compose/docker-compose.yml`） |

### 業界プロファイル（vertical）
`backend/app/core/verticals.py` に `gov` / `saas` / `ec` を定義。各プロファイルが
許可コレクション・エスカレーションキーワード・アクションマップ・閾値・プロンプト追記を持つ。

### パイプライン 1 周
```
S1 業界プロファイル適用
 → ① Plan（planner）
 → ② Execute（内部RAG → reasoning）
 → ③ Confidence（GroundednessVerifier で根拠検証）
 → ④ 回答ゲート（＋強制エスカレ＋救済）
 → ⑤ Web フォールバック
 → ④' 情報なし回答の検知
 → ⑥ Action（本人確認 → HITL CONFIRM → 実行）
```
`support_rate = supported / (supported + contradicted)` — neutral は分母から除外する
（＝答えていない内容を減点しない）。

> **⚠️ Web API と CLI は同じ `run_support_agent_core` を通る。**
> `uvicorn backend.app.main:app` も `agent_support_example.py` も、この 1 関数を呼ぶ。
> 「Web だけ / CLI だけ」の分岐は存在しないので、片方で検証した挙動は他方にも当てはまる。

---

## 2. 開発コマンド

### 起動
```bash
# 前提: .env に ANTHROPIC_API_KEY / GOOGLE_API_KEY、Qdrant 起動済み
docker-compose -f docker-compose/docker-compose.yml up -d

# 開発サーバ一括起動（backend :8000 + frontend :5173）
./run_dev.sh

# バックエンド単体
uvicorn backend.app.main:app --reload --port 8000

# CLI（同じコアを通る。挙動確認に便利）
uv run python agent_support_example.py --vertical gov -v "住民票の写しの取り方は？"
```

### データ準備（3段階）
```bash
# 1. チャンク化
python -m chunking.csv_text_to_chunks_text_csv

# 2-3. Q/A 生成 + Qdrant 登録
python qa_qdrant/make_qa_register_qdrant.py
#   登録のみ: python qa_qdrant/register_to_qdrant.py
```

### 検証（CI と同じゲート）
```bash
uv run ruff check . --no-cache          # lint
uv run pytest backend/tests -q          # backend テスト
python -m compileall -q -x '\.venv|/\.git/|/logs/' .   # 構文ゲート
cd frontend && npm run lint && npm test && npm run build   # frontend
```

> `pyproject.toml` に `pythonpath` 指定は無い。CI は `PYTHONPATH=.` を env で与えている。
> `python backend/tests/x.py` を直接叩くと `ModuleNotFoundError: No module named 'backend'`
> になる → `uv run python -m backend.tests.x` を使う。

---

## 3. プロバイダ方針（恒久ルール）

| 用途 | プロバイダ | 既定 | APIキー |
|---|---|---|---|
| **Embedding（検索）のみ** | **Gemini** | `gemini-embedding-001`（3072次元） | `GOOGLE_API_KEY` |
| **それ以外の全 LLM 用途**（Q&A生成・Plan/Execute/Reasoning/Confidence/Replan/ReAct 等） | **Anthropic** | `claude-sonnet-4-6`（軽量 `claude-haiku-4-5-20251001`） | `ANTHROPIC_API_KEY` |

- LLM クライアントは `helper.helper_llm.create_llm_client("anthropic")` /
  `grace.llm_compat.create_chat_client`。LLM モデル既定は `config.ModelConfig.DEFAULT_MODEL`。
- `config.GeminiConfig` は **Embedding 用途（`EMBEDDING_MODEL` / `EMBEDDING_DIMS`）に限って**参照可。
- **Embedding 文脈の `provider="gemini"` / `GOOGLE_API_KEY` は正しい**ので変更しない。
- 本リポジトリは Gemini 由来コードから Anthropic へ移植した経緯があり、コードに残る
  Gemini 系の **LLM** 既定（`gemini-2.5-flash` 等）は「設計上の意図」ではなく
  **移植漏れ（負債）**とみなす。発見次第 Anthropic へ是正する。
  「現存コード＝意図」と推論しないこと。

---

## 4. CI と ブランチ運用

### 必須ゲートは 4 つ（すべて blocking）

| ジョブ | 内容 |
|---|---|
| `compile (syntax gate)` | `python -m compileall` |
| `ruff` | `ruff check .`（`ruff==0.12.11` 固定） |
| `pytest (backend)` | `pytest backend/tests -q -rs`（実 API キー・Qdrant 不要） |
| `frontend (tsc + vitest + build)` | `npm run lint` → `npm test` → `npm run build` |

`auto-merge` は `needs: [build, lint, backend-tests, frontend]`。4 つ緑になれば
`claude/*` ブランチの PR を Ready 化して master へマージする（`hold` ラベルで抑止）。

> ⚠️ **frontend ゲートを忘れない。** Python 側が全部緑でも `frontend/src/types.ts` の
> 型エラー 1 個でマージは止まる。バックエンドの API スキーマを変えたら
> `frontend/src/types.ts` も必ず追随させる。

### ruff 設定の要点
`[tool.ruff.lint.isort] known-first-party` を**明示必須**。未設定だと
「CI（未インストール）＝first-party」「ローカル（導入済）＝third-party」で isort 分類が割れ、
**I001 がローカル緑／CI 赤**になる。**新規トップレベルモジュールを足したらここにも追記する。**

### ブランチ
- 開発は `claude/<topic>` ブランチ。**ドラフト PR** で作成（auto-merge が Ready 化する）。
- **指定ブランチの PR が既にマージ済みなら、そのブランチは使い回さない。**
  `git fetch origin master && git checkout -B <branch> origin/master` で作り直す。
- **リモート Git プロキシは ref 削除を 403 で拒否する。** `git push origin --delete` は
  この環境から実行できない → ブランチ削除は GitHub UI かユーザのローカルで行う。
  できない旨を正直に報告し、コマンドを提示すること。
- GitHub 操作は `mcp__github__*` MCP ツール（`gh` CLI はこの環境に無い）。
- **モデル識別子（`claude-opus-5` 等）をコミットメッセージ・PR 本文・コードコメントに
  書かない**（チャット返信のみ）。

---

## 5. Mermaidダイアグラム スタイル規約

### 5.1 構文バージョン
**PyCharm Pro v9 互換構文**を使用する。

- ノードラベルにバッククォートや markdown文字列（`` `text` ``）を使用しない
- 特殊文字を含むノードラベルは必ずダブルクォート（`"..."`）で囲む
- TS の総称型（`Record<StepId, StepState>` 等）は `<` `>` がタグ解釈されうるため、
  ダブルクォートで囲んだうえで可能なら `Record[StepId, StepState]` へ置換する

### 5.2 カラーテーマ（黒背景・白文字）— **必須**

| 要素 | 設定値 |
|---|---|
| ノード背景色 | `fill:#000` |
| ノードテキスト色 | `color:#fff` |
| ノード枠線色 | `stroke:#fff` |
| サブグラフ背景色 | `fill:#1a1a1a` |
| サブグラフテキスト色 | `color:#fff` |
| サブグラフ枠線色 | `stroke:#fff` |

### 5.3 flowchart / graph の実装パターン
```
flowchart TB
    subgraph Layer["レイヤー名"]
        NodeA["ノードA"]
        NodeB["ノードB"]
    end
    NodeA --> NodeB
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class NodeA,NodeB default
style Layer fill:#1a1a1a,stroke:#fff,color:#fff
```

**必須ルール:**
1. `classDef default fill:#000,stroke:#fff,color:#fff` を必ずブロック末尾に追加する
2. `classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff` を追加する
3. 全ノードに `class <node_ids> default` を付与する
4. 全サブグラフに `style <subgraph_name> fill:#1a1a1a,stroke:#fff,color:#fff` を付与する
5. 既存の `style`/`classDef`/`class` 行は重複しないよう整理する

### 5.4 sequenceDiagram の実装パターン
```
%%{ init: { "theme": "base", "themeVariables": {
  "background": "#000000", "mainBkg": "#000000",
  "textColor": "#ffffff", "lineColor": "#ffffff",
  "actorBkg": "#000000", "actorTextColor": "#ffffff",
  "actorLineColor": "#ffffff", "noteBkgColor": "#000000",
  "noteTextColor": "#ffffff", "noteBorderColor": "#ffffff" } } }%%
sequenceDiagram
    participant A as "参加者A"
    A->>B: メッセージ
```

**必須ルール:**
- `sequenceDiagram` の前に必ず `%%{ init: ... }%%` ヘッダーを挿入する
- `classDef` / `class` 行は `sequenceDiagram` では使用しない（非対応）
- ⚠️ **Note 背景の変数名は `noteBkgColor`（`noteBkg` ではない）。**
  `noteBkg` は Mermaid に認識されず既定の黄色（`#fff5ad`）になる

### 5.5 stateDiagram-v2
`classDef` / `class` に非対応 → **スタイル指定を付けない。**

### 5.6 検証（grep）
各ファイルで `flowchart|graph` の数 == `classDef default fill:#000` の数、
`sequenceDiagram` の数 == `%%{ init` の数。

---

## 6. コーディング規約

### 6.1 型ヒント
```python
# ❌ 誤り
def func(callback: Optional[callable] = None): ...

# ✅ 正しい
from typing import Optional, Callable
def func(callback: Optional[Callable] = None): ...
```

### 6.2 出力ファイル命名（チャンク分割）
```bash
# ✅ デフォルト: 固定ファイル名（後続バッチとの連携のため）
cc_news_1per.csv  →  output_chunked/cc_news_1per_chunks.csv

# タイムスタンプが必要な場合は --timestamp オプションで明示指定
python -m chunking.csv_text_to_chunks_text_csv \
  --input-file OUTPUT/cc_news_1per.csv \
  --output output_chunked \
  --timestamp   # ← これがある場合のみ日時サフィックスを付与
```

---

## 7. ドキュメント規約

### 7.1 所在は `docs`（複数形）に統一

| 領域 | 所在 |
|---|---|
| Python モジュール（IPO） | `<package>/docs/<module>.md` — `chunking/docs/`, `qa_generation/docs/`, `qa_qdrant/docs/`, `services/docs/`, `grace/docs/`, `grace/step_trace/docs/` |
| backend | `backend/docs/` |
| React コンポーネント | `frontend/docs/<Component>.md` |
| 横断/設計メモ | リポジトリ直下 `docs/` |

**単数形 `doc/` は使わない。** 新規ディレクトリも必ず `docs/` で切る。

### 7.2 フォーマット仕様（書く前に該当仕様を実際に読むこと）

| 対象 | 仕様書（`.claude/skills/` 配下） |
|---|---|
| Python モジュール | `grace-agent-docs/a_class_method_md_format.md`（IPO 形式） |
| React コンポーネント | `grace-agent-docs/a_react_page_md_format.md` |
| 単体テスト | `grace-agent-tests/a_test_md_format.md`（SAE 形式） |

> `grace-agent-docs/a_pages_md_format.md` は **Streamlit 用**。
> **本リポジトリに Streamlit は存在しない**（他リポジトリ用に同梱しているだけ）。

### 7.3 技術スタック表記の統一

| 用途 | ✅ 正しい表記 | ❌ 禁止表記 |
|---|---|---|
| LLM全般 | `Anthropic Claude` | `OpenAI GPT`, `Gemini`（LLM 用途） |
| デフォルトモデル | `claude-sonnet-4-6`（軽量 `claude-haiku-4-5-20251001`） | `gpt-4o-mini`, `gemini-2.5-flash` |
| Embedding | `Gemini` `gemini-embedding-001`（3072次元） | `text-embedding-3-*`（本番 Embedding 用途） |
| LLMクライアント | `create_llm_client("anthropic")` | `"openai"` / `"gemini"`（LLM 用途） |
| LLM用APIキー | `ANTHROPIC_API_KEY` | `OPENAI_API_KEY` |
| フロントエンド | `Vite + React 18 + TypeScript` | `Streamlit`, `Next.js` |

### 7.4 参照してはいけない廃止ファイル
grace_v2 に**存在しない**: `setup.py` / `server.py` / a-prefixed scripts
（`a30_qdrant_registration.py` 等）/ `agent_rag.py` / `ui/` / `start_celery.sh` /
リポジトリ直下の `tests/`。

---

# ⚠️ CRITICAL RULES - MUST READ BEFORE ANY MODIFICATION ⚠️

## R1. モデル名のマッピングを絶対に作らない

**以下はすべて実在する有効なモデル名:**
- `claude-sonnet-4-6`, `claude-haiku-4-5-20251001`
- `gpt-5-nano`, `gpt-5-mini`, `gpt-5` ← 実在する GPT-5 系
- `gpt-4.1`, `gpt-4.1-mini` ← 実在する GPT-4.1 系
- `o3`, `o3-mini`, `o4`, `o4-mini` ← 実在する O 系

**❌ こういうマッピングを作ってはならない:**
```python
MODEL_MAPPING = {"gpt-5-nano": "gpt-4o-mini"}  # ← WRONG! DO NOT DO THIS
```

モデル名は定義済みのものをそのまま使う。

## R2. OpenAI API のメソッドは 2 つとも正しい

```python
# Structured Outputs API（Q/A 生成向け・型安全）
response = client.responses.parse(
    input=combined_input, model=model,
    text_format=QAPairsResponse, max_output_tokens=1000,
)

# Responses API（標準のテキスト生成）
response = client.responses.create(
    input=input_messages, model=model, max_output_tokens=1000,
)
```

**⚠️ `.parse()` と `.create()` は両方 CORRECT。用途に応じて使い分ける。**
片方をもう片方へ「修正」しない。

## R3. よくある間違い

| ❌ 誤り | ✅ 事実 |
|---|---|
| 「`gpt-5-nano` はエラーになるから存在しないモデルだ」 | 実在する。**本当のエラー原因**を調べること |
| 「`responses.parse()` は存在しないから `create()` に直そう」 | 両方存在する |
| 「旧モデル名を新モデル名に翻訳するマッピングを作ってあげよう」 | モデル名は既に正しい。マッピングを作らない |

## R4. エラーが出たときの手順

「model not found」「API error」を見たら:

1. ❌ モデル名が間違っているせいでは**ない**
2. ❌ API メソッド名が間違っているせいでは**ない**
3. ✅ 確認する: API キー、ネットワーク、Qdrant 起動状態、Celery/Redis 接続
4. ✅ 確認する: 実際のエラーメッセージとスタックトレース
5. ✅ **モデル名や API メソッド名を「直す」ことを最初の対応にしない**

## R5. コミット前チェックリスト

- [ ] MODEL_MAPPING を作っていないか？（作っていたら → 削除）
- [ ] `responses.parse()` を `responses.create()` に変えていないか？（変えていたら → 戻す）
- [ ] 4 つの CI ゲート（ruff / pytest backend / compileall / frontend）をローカルで通したか？
- [ ] API スキーマを変えたなら `frontend/src/types.ts` を追随させたか？
- [ ] 確信が持てない → **ユーザーに聞く**

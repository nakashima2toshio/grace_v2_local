# データ管理タブ 移植 TODO（grace_v2 → grace_v2_local）

**Version 1.3** | 最終更新: 2026-09-24

---

**対象**: `grace_v2_local` のアプリに「データ管理」タブ（チャンク化 → Qdrant 登録 → コレクション管理）を追加する
**移植元**: `grace_v2` の master（`ab5a497`）
**作成日**: 2026-08-03

> 本書は TODO の作成と説明のみ。実装は含まない。

> **⚠️ 本書は 2026-08-03 時点の記録である。以降の実装で状況が変わった箇所がある。**
> §0-2 の「Q/A 作成は UI が無い」は**解消済み**: 2026-09-05 に
> `POST /api/qa/generate`（`QaGenerationParams` / `_qa_runner`）と
> サブタブ「② Q/A 作成」を追加し、runner は 4 種になった。
> 現在の設計は `backend/docs/data_pipeline.md`（v1.2 以降）を参照。

---

## 目次

- [0. 調査でわかったこと（前提の訂正を含む）](#0-調査でわかったこと前提の訂正を含む)
- [1. 移植対象ファイル一覧](#1-移植対象ファイル一覧)
- [2. 移植しないもの（意図的な除外）](#2-移植しないもの意図的な除外)
- [3. Ollama 化に伴う要注意点](#3-ollama-化に伴う要注意点)
- [4. 実装順序（推奨）](#4-実装順序推奨)
- [5. 着手前に決めたいこと](#5-着手前に決めたいこと)
- [6. 完了の定義](#6-完了の定義)
- [7. 実機確認の手順・期待結果](#7-実機確認の手順期待結果)
- [8. 実装の記録（2026-08-03）](#8-実装の記録2026-08-03)
- [変更履歴](#変更履歴)

---

## 0. 調査でわかったこと（前提の訂正を含む）

### 0-1. なぜ grace_v2_local に無いのか

`grace_v2_local` は `grace_v2` の **PR #54 時点のスナップショット**（`c987a69` 相当）から
コピーされている。データ管理タブは **その後の PR #55〜#59 で grace_v2 に追加**された。
つまり「Ollama 移植で消えた」のではなく、**分岐時点より後の機能**である。

```
grace_v2      … c81233b(PR#54) ─→ … ─→ ab5a497(PR#59)  ← データ管理タブはここ
                     │
                     └─ copy ─→ grace_v2_local  c987a69 ─→ Ollama 移植（PR#1,#2）
```

frontend の既存 12 コンポーネントは **grace_v2 の PR#54 時点とバイト単位で同一**であり、
Ollama 移植でも frontend は 1 行も触っていない。したがって移植は**追加が主体**で、
既存 UI との衝突はほぼ無い。

### 0-2. ⚠️ 「Q/A 作成」は grace_v2 にも UI が無い

ご要望の 3 機能のうち、UI として実装済みなのは **2 つだけ**である。

| ご要望 | grace_v2 の UI | 実体 |
|---|---|---|
| **チャンク** | ✅ あり | `POST /api/chunking/run`（`chunking/csv_text_to_chunks_text_csv`） |
| **Q/A 作成** | ❌ **無い** | `qa_generation/` は **CLI のみ**（`qa_qdrant/make_qa_register_qdrant.py`） |
| **Qdrant への CRUD** | ✅ あり（C/R/D） | 登録・一覧・詳細・プレビュー・削除 |

`POST /api/qdrant/register` の入力は「**既に作られた Q/A CSV**」であり、Q/A 生成そのものは
UI から起動できない。`data_jobs.py` に登録されている runner も 3 種だけである。

```python
register_runner(ChunkingParams, _chunking_runner, "chunking")
register_runner(RegisterParams,  _register_runner, "register")
register_runner(DeleteParams,    _delete_runner,   "delete")
```

→ **Q/A 生成タブが必要なら「移植」ではなく「新規実装」になる。** 判断が必要（§5）。

### 0-3. Qdrant の CRUD は正確には C/R/D

| 操作 | 状況 |
|---|---|
| **C**reate | ✅ `POST /api/qdrant/register`（コレクション作成＋ポイント登録） |
| **R**ead | ✅ 一覧 / 詳細 / ポイントプレビュー / ヘルスチェック |
| **U**pdate | ⚠️ 個別ポイントの更新 UI は**無い**。`recreate=True` での**作り直し**が実質の更新 |
| **D**elete | ✅ `POST /api/qdrant/delete`（**必ず HITL CONFIRM を通る**） |

### 0-4. 移植が軽く済む理由

ジョブ基盤 `backend/app/core/jobs.py` は **両リポジトリで完全に同一（269行）** で、
既に「params の型から runner を解決する」汎用設計になっている
（`backend/tests/test_jobs_generic.py` が grace_v2_local に既に存在する）。
SSE・HITL ブリッジ・`ConfirmModal`・`Timeline` もそのまま流用できる。

---

## 1. 移植対象ファイル一覧

凡例: 🆕 新規追加（ほぼそのままコピー） / ✏️ 既存ファイルへ追記 / ⚠️ Ollama 対応が必要

### Phase 1 — バックエンド: サービス層

| # | ファイル | 種別 | 行数 | 内容 |
|---|---|---|---|---|
| 1 | `services/data_pipeline_service.py` | 🆕 | 286 | 許可ディレクトリ解決・入力ファイル列挙・チャンキング実行・コレクション削除/存在確認・DataFrame→レコード変換 |
| 2 | `pyproject.toml` | ✏️ | — | `[tool.ruff.lint.isort] known-first-party` は既に `services` を含むため**変更不要**（確認のみ） |

### Phase 2 — バックエンド: ジョブ層

| # | ファイル | 種別 | 行数 | 内容 |
|---|---|---|---|---|
| 3 | `backend/app/core/data_jobs.py` | 🆕⚠️ | 547 | `ChunkingParams` / `RegisterParams` / `DeleteParams` と 3 つの runner。`register_runner()` を import 時に実行 |
| 4 | `backend/app/core/job_logs.py` | 🆕 | 193 | 既存モジュールの `logging` 出力を横取りして SSE の log イベントへ流す |

**⚠️ Ollama 対応が必要な箇所（#3）**

| 行 | 現状 | 変更後 |
|---|---|---|
| `ChunkingParams.model` | `"claude-haiku-4-5"` | **`None`**（当時は `"gemma4:e4b"`。その後、import 時に既定を畳み込まないよう `None` にして runner の `_resolve_model()` で解決する形へ変わった） |
| `RegisterParams.provider` | `"gemini"` | **変更しない**（Embedding は Gemini 継続） |

### Phase 3 — バックエンド: API 層

| # | ファイル | 種別 | 行数 | 内容 |
|---|---|---|---|---|
| 5 | `backend/app/api/data.py` | 🆕 | 160 | `POST /api/chunking/run` / `/api/qdrant/register` / `/api/qdrant/delete`、SSE `/api/data/stream/{job_id}`、`/api/data/confirm/{job_id}`、`/api/data/result/{job_id}` |
| 6 | `backend/app/api/qdrant.py` | 🆕 | 200 | `GET /api/qdrant/health` / `/collections` / `/collections/{name}` / `/collections/{name}/points` / `/api/files` |
| 7 | `backend/app/schemas.py` | ✏️ | +142 | `ChunkingRequest` / `RegisterRequest` / `DeleteCollectionsRequest` / `DataJobStatusResponse` / `CollectionInfo` / `CollectionDetail` / `CollectionPoints` / `QdrantHealth` / `InputFileListResponse` 等 |
| 8 | `backend/app/main.py` | ✏️ | +5 | `data` / `qdrant` ルーターの登録 |

### Phase 4 — フロントエンド: 状態管理

| # | ファイル | 種別 | 行数 | 内容 |
|---|---|---|---|---|
| 9 | `frontend/src/state/dataReducer.ts` | 🆕 | — | データジョブのステップ状態（`stepIdsFor` / `stepLabelsFor`） |
| 10 | `frontend/src/state/dataParams.ts` | 🆕 | — | フォーム入力のバリデーション・正規化 |
| 11 | `frontend/src/state/activeJobs.ts` | 🆕 | — | 実行中ジョブの記憶（`rememberJob` / `recallJob` / `forgetJob`）。タブ離脱→復帰で再購読するため |
| 12 | `frontend/src/state/tabKeys.ts` | 🆕 | — | WAI-ARIA tablist のキーボード操作（`handleTabKeyDown`）。`App.tsx` と `DataPanel.tsx` が使う |

### Phase 5 — フロントエンド: コンポーネント

| # | ファイル | 種別 | 行数 | 内容 |
|---|---|---|---|---|
| 13 | `frontend/src/components/DataPanel.tsx` | 🆕 | 85 | データ管理タブの親。内部で 3 つのサブタブを切り替える |
| 14 | `frontend/src/components/DataJobPanel.tsx` | 🆕 | 536 | チャンク化 / 登録のフォーム＋進捗タイムライン＋CONFIRM |
| 15 | `frontend/src/components/CollectionPanel.tsx` | 🆕 | 396 | コレクション一覧・詳細・ポイントプレビュー・削除 |
| 16 | `frontend/src/types.ts` | ✏️ | +152 | `DataJobKind` / `InputFileInfo` / `CollectionInfo` / `CollectionDetail` 等。**backend の schemas.py と 1:1 で追随必須** |
| 17 | `frontend/src/api/client.ts` | ✏️ | +133 | 新エンドポイントの呼び出し関数と SSE 購読 |
| 18 | `frontend/src/App.tsx` | ✏️ | +27 | タブを 3 → 4 へ（`data` を追加）。`handleTabKeyDown` の配線 |
| 19 | `frontend/src/styles.css` | ✏️ | +162 | データ管理タブのスタイル |

### Phase 6 — テスト

| # | ファイル | 種別 | 行数 |
|---|---|---|---|
| 20 | `backend/tests/test_data_jobs.py` | 🆕⚠️ | 617 |
| 21 | `backend/tests/test_data_pipeline.py` | 🆕 | 297 |
| 22 | `backend/tests/test_job_logs.py` | 🆕 | 210 |
| 23 | `frontend/src/state/dataReducer.test.ts` | 🆕 | 220 |
| 24 | `frontend/src/state/dataParams.test.ts` | 🆕 | 205 |
| 25 | `frontend/src/state/activeJobs.test.ts` | 🆕 | 60 |
| 26 | `frontend/src/state/tabKeys.test.ts` | 🆕 | — |

**⚠️ #20 は既定モデル名（`claude-haiku-4-5`）をアサートしている箇所があるはずなので、
Ollama 側の既定へ追随させる。**

### Phase 7 — ドキュメント

| # | ファイル | 種別 |
|---|---|---|
| 27 | `backend/docs/data_pipeline.md` | 🆕 |
| 28 | `frontend/docs/DataPanel.md` / `DataJobPanel.md` / `CollectionPanel.md` | 🆕 |
| 29 | `services/docs/data_pipeline_service.md` | 🆕（grace_v2 にあれば流用） |
| 30 | `CLAUDE.md` | ✏️ 「データ管理タブ」の説明を §1 の構成表へ追記 |

**合計: 新規 20 ファイル前後（約 3,300 行）＋ 既存 7 ファイルへの追記**

---

## 2. 移植しないもの（意図的な除外）

grace_v2 の PR#55〜#59 にはデータ管理タブ以外の変更も含まれる。**依存していないものは
今回のスコープから外す**（差分を小さく保ち、レビューを容易にするため）。

| ファイル | 除外理由 |
|---|---|
| `frontend/src/state/formMemory.ts` | Support/Review フォームの入力記憶（PR#59）。データ管理タブとは独立 |
| `frontend/src/state/metaFetch.ts` / `components/MetaErrorBanner.tsx` | メタ情報取得のエラー表示改善。独立した改善 |
| `frontend/src/state/timelineAnnounce.ts` | タイムラインのスクリーンリーダー通知。独立した改善 |

> ただし `tabKeys.ts` だけは `DataPanel.tsx` / `App.tsx` が直接 import しているため**必須**。

これらを別途取り込みたい場合は、**データ管理タブとは別 PR** にする。

---

## 3. Ollama 化に伴う要注意点

`grace_v2` は Anthropic 前提、`grace_v2_local` は Ollama 前提。移植時に**そのままコピーすると
プロバイダ方針に反する**箇所を挙げる。

| 論点 | grace_v2（移植元） | grace_v2_local（あるべき姿） |
|---|---|---|
| チャンク化の LLM モデル既定 | `claude-haiku-4-5` | **`config.py::get_default_ollama_model()`**（現在の実値 `gemma4:12b-mlx`。移植当時は `gemma4:e4b` 固定だった） |
| 登録時の Embedding provider | `"gemini"` | **`"gemini"` のまま**（変更禁止） |
| コレクション名 | `*_anthropic` | **そのまま**（Embedding 3072 次元が不変のため既存データを使い続ける） |
| API キー前提 | `ANTHROPIC_API_KEY` チェック | **不要**（PR#1 で起動ガード削除済み。同じ轍を踏まない） |
| コスト表示 | トークン課金 | **ローカル LLM は 0**（表示するなら Embedding のみ） |

### チャンク化が Ollama で動くかは未検証

`chunking/csv_text_to_chunks_text_csv.py` は PR#2 で `create_llm_client("ollama")` へ
切り替え済みだが、**実 Ollama での動作は未検証**である。データ管理タブから起動する前に、
まず CLI で 1 本通すのが安全。

**⚠️ 構造化出力の失敗が最も出やすい箇所**。`_resolve_schema_refs()` は実装済みだが、
モデルが実際にスキーマへ従うかは実機で確認するまで分からない。

**手順・期待結果・失敗時の切り分けは §7 にある**（2026-09-22 追記）。

---

## 4. 実装順序（推奨）

依存関係の下から積む。各 Phase の終わりで CI 4 ゲートを通す。

```
Phase 1  services/data_pipeline_service.py          ← 依存なし。単体テストも書ける
   ↓
Phase 2  core/job_logs.py → core/data_jobs.py       ← jobs.py（既存・無改造）に乗る
   ↓
Phase 3  schemas.py → api/data.py, api/qdrant.py → main.py
   ↓         ⚠️ ここで backend のテストが全部通ることを確認してから frontend へ
Phase 4  state/（dataReducer, dataParams, activeJobs, tabKeys）
   ↓
Phase 5  types.ts → client.ts → components/ → App.tsx → styles.css
   ↓         ⚠️ types.ts が schemas.py とズレると frontend ゲートで落ちる
Phase 6  テスト
   ↓
Phase 7  ドキュメント
```

**PR の分割案**: Phase 1〜3（バックエンド）と Phase 4〜5（フロントエンド）で 2 本に分けると
レビューしやすい。ただし Phase 3 だけをマージすると「API はあるが UI が無い」中間状態に
なる（動作上の実害はない）。1 本にまとめても構わない。

---

## 5. 着手前に決めたいこと

| # | 論点 | 選択肢 |
|---|---|---|
| 1 | **Q/A 生成タブ** | (a) 今回は対象外（チャンク＋Qdrant CRUD のみ移植）/ (b) 新規実装する（`qa_generation/` を包む 4 つ目の runner ＋ UI を書き起こす。移植元が無いため工数は別途） |
| 2 | **PR の分割** | (a) バックエンド／フロントエンドで 2 本 / (b) 1 本にまとめる |
| 3 | **除外した 3 機能**（formMemory / metaFetch＋MetaErrorBanner / timelineAnnounce） | (a) 今回は入れない / (b) 別 PR で後追い / (c) ついでに入れる |
| 4 | **チャンク化の実機確認** | 先に CLI で動作を確認してから UI を載せるか、UI ごと作ってから確認するか → **前者で決着**（手順は §7） |

---

## 6. 完了の定義

### 実装（完了）

- [x] アプリに「データ管理」タブが表示され、3 つのサブタブが機能する
- [x] チャンク化ジョブが SSE で進捗を返す配線（runner・API・UI）
- [x] Q/A CSV を Qdrant へ登録する経路（`recreate=True` では CONFIRM）
- [x] コレクション一覧・詳細・ポイントプレビューの API と画面
- [x] コレクション削除が **必ず CONFIRM を経由**する（テストで固定）
- [x] 既定モデルが `config.py::get_default_ollama_model()` 由来（当時 `gemma4:e4b`・現在 `gemma4:12b-mlx`）、Embedding provider が `gemini`（3072次元）
- [x] CI 4 ゲートが緑（compileall / ruff / pytest backend / frontend）

### 実機確認（未実施 — ローカル環境で要確認）

⚠️ **この開発環境には Ollama も Qdrant も無いため、全テストはスタブ経由である。**
UI から実際にジョブが走ることは検証できていない。

手順・期待結果・失敗時の切り分けは次節（§7）にまとめた。チェックリストだけ再掲する。

- [ ] **A.** CLI でチャンク化を 1 本通す（**先にこれ**。UI と同じ経路を通る）
- [ ] **B.** `./run_dev.sh` → :5173 の「データ管理」タブでチャンク化を実行
- [ ] **C.** 登録（`recreate=True`）で CONFIRM ダイアログが出ることを確認
- [ ] **D.** 削除で CONFIRM ダイアログが出ることを確認
- [ ] **E.** タブを離れて戻ったときに進捗が復元されること（`activeJobs` の再購読）

---

## 7. 実機確認の手順・期待結果

**2026-09-22 追記。** 実行者が手順で迷わないよう、前提・コマンド・**何をもって合格とするか**・
落ちたときの切り分けまで書き下す。所要は A が 5〜15 分、B〜E が 15 分程度。

### 7.0 前提の準備（共通）

```bash
# 1. ローカル LLM（別ターミナルで常駐）
ollama serve
ollama pull gemma4:12b-mlx        # 既定モデル。config.py::get_default_ollama_model()

# 2. Embedding 用のキー（Qdrant 登録に要る。チャンク化だけなら不要）
echo 'GOOGLE_API_KEY=...' >> .env

# 3. Qdrant
docker-compose -f docker-compose/docker-compose.yml up -d
curl -s localhost:6333/healthz     # → 期待: healthz check passed
```

> ⚠️ **既定モデルは `gemma4:12b-mlx`**（本書 v1.1 までは `gemma4:e4b` と書いていた）。
> `ollama pull` を忘れると実行時に **404** で落ちる。これは**モデル名の誤りではない**
> ので、名前を「直そう」としないこと（CLAUDE.md R4）。
>
> 別モデルで試すなら `--model` か環境変数 `OLLAMA_DEFAULT_MODEL` を使う。ただし
> `phi3` / `gemma2` は **tool calling 非対応**（`config.OllamaConfig.MODEL_CONSTRAINTS`）。

### 7.1 A — CLI でチャンク化を 1 本通す（最優先）

**なぜ先に CLI か**: UI（データ管理タブ）は同じ `chunking/` の実装を runner 経由で
呼ぶだけなので、**壊れているとすればチャンク化そのもの**である。CLI なら SSE・
ジョブ管理・React を挟まずに切り分けられる。

```bash
# 小さい入力で試す（--max-rows で行数を絞れる）
uv run python -m chunking.csv_text_to_chunks_text_csv \
  --input-file OUTPUT/<入力>.csv \
  --output output_chunked \
  --max-rows 20 \
  --verbose
```

| 期待結果 | 確認方法 |
|---|---|
| 例外で落ちない | 終了コード 0 |
| 出力 CSV ができる | `output_chunked/<入力名>_chunks.csv`（**固定ファイル名**。日時サフィックスは `--timestamp` を付けたときだけ・CLAUDE.md §8.2） |
| チャンクが 1 件以上ある | `wc -l output_chunked/<入力名>_chunks.csv` が 2 行以上（ヘッダ＋データ） |
| 分割が機械的でない | 出力を数行眺めて、文が途中でぶつ切りになっていないこと |

> ⚠️ **最も失敗しやすいのは構造化出力**。`_resolve_schema_refs()` で `$defs`/`$ref` を
> 展開したスキーマに、そのモデルが実際に従うかは実機でしか分からない。
> スキーマ定義そのものをオウム返しされた場合は `SchemaEchoError` が名指しで出る
> （CLAUDE.md §3「Ollama 固有の落とし穴」）。
>
> ⚠️ **`--workers` を上げない。** Ollama は既定で 1 本ずつしか処理せず、
> 上げても速くならないうえ**待ち行列がタイムアウトを食う**
> （`--workers` のヘルプに明記。既定は `OLLAMA_NUM_PARALLEL` があればその値、無ければ 1）。
> 本書 v1.1 までの `--workers 2` という例は取り下げる。

**落ちたときの切り分け**

| 症状 | 見るところ |
|---|---|
| `404` / model not found | `ollama list` に既定モデルがあるか。**モデル名を書き換えない**（CLAUDE.md R4） |
| `SchemaEchoError` | その Ollama が `json_schema` 未対応で `json_object` へ落ちている。モデルを替える |
| MAX_TOKENS 系 | `--block-size` を 1000 より小さくする |
| 応答が返らない | `curl localhost:11434/api/tags` で `ollama serve` の生死を見る |

### 7.2 B — UI（データ管理タブ）でチャンク化

```bash
./run_dev.sh          # backend :8000 + frontend :5173
```

:5173 → 「データ管理」→ ①チャンキング。A と同じ入力を選んで実行する。

| 期待結果 | 確認方法 |
|---|---|
| 入力ファイルが選べる | セレクタに候補が出る（`GET /api/files`） |
| ジョブが 202 で受理される | `POST /api/chunking/run` → `job_id` が返る |
| **進捗が逐次流れる** | 画面のタイムラインが伸びる（SSE `GET /api/data/stream/{job_id}`）。**最後まで無反応なら SSE の配線が切れている** |
| 完了する | 完了行と出力パスが出る（`GET /api/data/result/{job_id}`） |
| 出力が A と一致する | 同じ入力なら同じ出力パスへ書かれる |

### 7.3 C — 登録（recreate=True）で CONFIRM が出る

②Q/A 作成 →（または既存の Q/A CSV を使って）③Qdrant 登録。
**「既存コレクションを作り直す（recreate）」を ON** にして実行する。

| 期待結果 | 確認方法 |
|---|---|
| **承認ダイアログが出る** | `ConfirmModal` が前面に出て、承認するまで実行されない |
| 拒否すると登録されない | コレクションの点数が変わらない（④コレクション管理で確認） |
| 承認すると登録される | 点数が増える。`GET /api/qdrant/collections/{name}` の `points_count` |
| **Tab が背面へ抜けない** | モーダル内で Tab を回し続けられる（2026-09-21 に実装したフォーカストラップ） |

> ⚠️ **`recreate=True` は既存コレクションを作り直す**（＝中身が消える）。
> 確認は**捨ててよいコレクション名**で行うこと。

### 7.4 D — 削除で CONFIRM が出る

④コレクション管理 → 削除。

| 期待結果 | 確認方法 |
|---|---|
| **必ず承認ダイアログを経由する** | `ConfirmModal` が出る（テストでも固定済み） |
| 拒否すると消えない | 一覧に残る |
| 承認すると消える | 一覧から消える（`GET /api/qdrant/collections`） |

### 7.5 E — タブを離れて戻ると進捗が復元される

チャンク化を**実行中のまま**別タブ（例: 基本版）へ切り替え、数秒後に戻る。

| 期待結果 | 確認方法 |
|---|---|
| 進捗が続きから見える | タイムラインが最初から再生され直さない（`state/activeJobs.ts` の再購読） |
| 入力が消えていない | 選んだファイル・モデルが残っている（`state/formMemory.ts`） |
| 二重購読になっていない | 同じステップ行が 2 度出ない |

> 📌 タブ切替は**アンマウント方式**なので、退避が効いていないと入力が既定値へ戻る。
> ここが壊れていると「dry-run を外したのに戻っている」類の事故になる。

### 7.6 記録の残し方

実行したら、このチェックリストを埋めて `docs/README.md` §6 の残タスク 3 を閉じること。
**落ちた場合は落ちたと書く**（CLAUDE.md 作業原則）。合否だけでなく、使ったモデル名・
入力ファイル・所要時間も残すと、次に誰かが再現するときに役立つ。

---

## 8. 実装の記録（2026-08-03）

| Phase | 内容 | コミット |
|---|---|---|
| 1 | `services/data_pipeline_service.py` ＋ テスト 23 件 | `4e1ad34` |
| 2 | `core/job_logs.py` / `core/data_jobs.py` ＋ テスト 35 件 | `bd6d3b0` |
| 3 | `api/data.py` / `api/qdrant.py` / `schemas.py` / `main.py` ＋ API テスト 17 件 | `f79bc3d` |
| 4-5 | `state/` 4 本・コンポーネント 3 本・`types.ts` / `client.ts` / `App.tsx` / `styles.css` ＋ テスト 67 件 | `65849d4` |
| 7 | ドキュメント 4 本 ＋ `CLAUDE.md` | 本コミット |

**Phase 6（テスト）は各 Phase に合流させたため独立したコミットは無い。**

### 移植元から変更した点（すべてプロバイダ方針への追随）

| 箇所 | 変更 |
|---|---|
| `data_jobs.ChunkingParams.model` | `claude-haiku-4-5` → `gemma4:e4b` |
| `schemas.ChunkingRequest.model` | 同上 |
| `_chunking_runner` | `ANTHROPIC_API_KEY` の起動ガードを**削除** |
| `RegisterParams.provider` | `"gemini"` のまま（変更禁止の旨をコメントで明示） |
| `frontend/docs/DataJobPanel.md` | 既定モデル表記と Embedding の説明 |
| `backend/docs/data_pipeline.md` | 「実行の前提（プロバイダ）」の節を追記 |

frontend の実装（`.tsx` / `.ts`）は**プロバイダ非依存のため無変更**で移植した。

### 併せて修正した移植漏れ

`chunking/csv_text_to_chunks_text_csv.py` に既定モデル `claude-haiku-4-5` が
5 箇所残っていた（Ollama 移植 PR#2 は `claude-haiku-4-5-20251001` を置換対象に
しており、ハイフン付きの別文字列を取りこぼしていた）。Phase 1 で是正済み。

---

## 変更履歴

| バージョン | 変更内容 |
|---|---|
| 1.3 | `a_cross_doc_md_format.md`（TODO＝種別 C）に準拠（2026-09-24）。H2 が 10 個あるため目次を追加 |
| 1.2 | **実機確認の手順を §7 として書き下した**（2026-09-22）。前提の準備・CLI での確認（A）・UI での確認（B〜E）・期待結果・失敗時の切り分けを、実行者が迷わない粒度で記載。あわせて本文の既定モデル表記を実態へ是正（`gemma4:e4b` → `get_default_ollama_model()`・実値 `gemma4:12b-mlx`）し、**`--workers 2` という誤った例を取り下げた**（Ollama は既定で 1 本ずつしか処理せず、上げると待ち行列がタイムアウトする） |
| 1.1 | 「② Q/A 作成」の追加に追随（`POST /api/qa/generate` / `QaGenerationParams` / `_qa_runner`）。§0-2 の「Q/A 作成は UI が無い」が解消済みである旨を冒頭へ追記した（2026-09-05） |
| 1.0 | 初版。データ管理タブの移植 TODO（2026-08-03 時点の記録・2026-09-03 にコミット） |

# API 契約（エンドポイント・SSE・ステータス） ドキュメント

**Version 1.1** | 最終更新: 2026-09-23

> **本書の位置づけ**: backend が外へ約束している**契約**をまとめる。
> エンドポイント一覧・SSE のワイヤ形式・HTTP ステータスの使い分け・
> `frontend/src/types.ts` との対応。フィールド単位の定義は
> [`reference/schemas.md`](./reference/schemas.md) を参照する。

> ⚠️ **これは内部仕様ではなく契約である。** CI の frontend ゲートは
> `frontend/src/types.ts` の型エラー 1 個でマージを止める。
> **API スキーマを変えたら `types.ts` を同じ PR で追随させること。**

> **関連ドキュメント**
> - [`architecture.md`](./architecture.md) / [`job_runtime.md`](./job_runtime.md)
> - [`config_and_providers.md`](./config_and_providers.md) — モデル一覧・既定の解決

---

## 目次

- [1. エンドポイント一覧](#1-エンドポイント一覧)
- [2. 非同期ジョブの 3 点セット](#2-非同期ジョブの-3-点セット)
- [3. SSE のワイヤ形式](#3-sse-のワイヤ形式)
- [4. HITL CONFIRM の往復](#4-hitl-confirm-の往復)
- [5. HTTP ステータスの使い分け](#5-http-ステータスの使い分け)
- [6. スキーマ ↔ types.ts 対応表](#6-スキーマ--typests-対応表)
- [7. 変更履歴](#7-変更履歴)

---

## 1. エンドポイント一覧

全 25 エンドポイント。ベース URL は `http://localhost:8000`。**認証は無い。**

### 1.1 GRACE-Support（`api/support.py`）

| Method | パス | 状態 | 説明 |
|---|---|---:|---|
| POST | `/api/support/query` | 202 | 問い合わせジョブ起動 → `{job_id, stream_url}`。`model` は任意 |
| GET | `/api/support/stream/{job_id}` | 200 | SSE でステップ進捗（0-(A)〜⑥） |
| POST | `/api/support/confirm/{job_id}` | 200 | HITL 応答（承認 / 拒否 / 選択肢） |
| GET | `/api/support/result/{job_id}` | 200 | 状態と `SupportResult`（ポーリング用） |

### 1.2 GRACE-Review（`api/review.py`）

| Method | パス | 状態 | 説明 |
|---|---|---:|---|
| POST | `/api/review/submit` | 202 | レビュージョブ起動（文書は最大 `MAX_DOCUMENT_CHARS` = 50,000 字）。`model` は任意 |
| GET | `/api/review/stream/{job_id}` | 200 | SSE でステップ進捗（S1・①〜⑦） |
| POST | `/api/review/confirm/{job_id}` | 200 | HITL 応答 |
| GET | `/api/review/result/{job_id}` | 200 | 状態と `ReviewResult` |

### 1.3 データ準備ジョブ（`api/data.py`）

起動は 4 本に分かれるが、**SSE / CONFIRM / 結果取得は 1 組を共用**する。

| Method | パス | 状態 | CONFIRM | 説明 |
|---|---|---:|---|---|
| POST | `/api/chunking/run` | 202 | なし | チャンク化（非破壊） |
| POST | `/api/qa/generate` | 202 | なし | Q/A 生成（非破壊・出力は新規ファイル） |
| POST | `/api/qdrant/register` | 202 | `recreate=true` のときだけ | Q/A CSV を Qdrant へ登録 |
| POST | `/api/qdrant/delete` | 202 | **常に** | コレクション削除 |
| GET | `/api/data/stream/{job_id}` | 200 | — | 4 種共通の SSE |
| POST | `/api/data/confirm/{job_id}` | 200 | — | 4 種共通の HITL 応答 |
| GET | `/api/data/result/{job_id}` | 200 | — | 4 種共通の結果取得 |

### 1.4 Qdrant 参照系（`api/qdrant.py`）— 読み取り専用

| Method | パス | 状態 | 説明 |
|---|---|---:|---|
| GET | `/api/qdrant/health` | **200 固定** | 稼働確認（落ちていても 200・本文の `available` で判定） |
| GET | `/api/qdrant/collections` | 200 / 503 | コレクション一覧 |
| GET | `/api/qdrant/collections/{name}` | 200 / 404 / 503 | コレクション詳細 |
| GET | `/api/qdrant/collections/{name}/points` | 200 / 404 / 503 | ポイントのプレビュー（`limit` 1〜500・既定 50） |
| GET | `/api/files` | 200 / 400 | 入力ファイル候補（許可ディレクトリのみ・絶対パスは返さない） |

### 1.5 メタ情報（`api/meta.py`）— **本リポジトリ固有の 2 本を含む**

| Method | パス | 状態 | 説明 |
|---|---|---:|---|
| GET | `/api/models` | 200 | **モデルセレクタの選択肢**（`id` / `supports_tool_calls` / `notes`）。`get_selectable_ollama_models()` で絞り込み済み |
| GET | `/api/model` | 200 | **現在の利用モデル**（`provider` / `model` / `light_model` / `heavy_model`）。`get_config().llm` の**解決済みの値**を返す |
| GET | `/api/verticals` | 200 | 業界プロファイル一覧（gov / saas / ec） |
| GET | `/api/rulesets` | 200 | ルールセット一覧（**ルール本文は返さない**） |
| GET | `/api/health` | 200 | 稼働確認と `GOOGLE_API_KEY` の有無（**LLM 用のキーは無い**） |

> ⚠️ **`GET /api/model` に表示用の固定文字列を返さないこと。**
> `config/grace_config.yml` や環境変数（`OLLAMA_DEFAULT_MODEL` / `GRACE_LLM_MODEL`）で
> 上書きされうるため、解決済みの値を返さないと**画面の表示と実挙動がずれる**。

---

## 2. 非同期ジョブの 3 点セット

Support / Review / データ準備は**同じ 3 点セット**で動く。

```
POST   …/query|submit|run     → 202 {job_id, stream_url}
GET    {stream_url}           → SSE（進捗・介入・結果・done 番兵）
POST   …/confirm/{job_id}     → HITL 応答（必要なときだけ）
GET    …/result/{job_id}      → ポーリング用フォールバック
```

`stream_url` は**サーバが返す**（クライアントで組み立てない）。

---

## 3. SSE のワイヤ形式

3 系統で**完全に同一**。フロントは同じパーサを使える。

```
data: {"seq":0,"ts":1758000000.0,"type":"step","step":"plan","status":"started", ...}

: keepalive

data: {"type":"done","status":"completed","ts":...,"started_at":...}
```

| 約束 | 内容 |
|---|---|
| イベント名 | **付けない**。種別は JSON の `type` で判定する |
| メッセージ | `data: ` + `SupportEventModel` の JSON 1 行（`ensure_ascii=False`） |
| keepalive | `: keepalive` のコメント行（15 秒新イベントが無いとき）。**ローカル LLM の長い 1 ステップで接続が切れないために要る** |
| 終端 | `type:"done"` の番兵 1 通 |
| リプレイ | **常に seq=0 から**配信する |
| ヘッダ | `Cache-Control: no-cache` / `X-Accel-Buffering: no` |

### ステップ ID

| 系統 | 定数 | 値 |
|---|---|---|
| Support | `STEP_IDS` | `analyze` `profile` `plan` `execute` `confidence` `gate` `web` `no_info` `action` |
| Review | `REVIEW_STEP_IDS` | `ruleset` `segment` `retrieve` `detect` `ground` `suppress` `web` `severity` `action` |
| チャンク化 | `CHUNKING_STEP_IDS` | `load` `chunk` `save` |
| Q/A 生成 | `QA_STEP_IDS` | `load` `generate` `coverage` `save` |
| 登録 | `REGISTER_STEP_IDS` | `prepare` `confirm` `embed` `upsert` |
| 削除 | `DELETE_STEP_IDS` | `inspect` `confirm` `delete` |

> ⚠️ **ステップ ID の並びは実行順であって、番号（①〜⑦）とは一致しない。**
> Support の `no_info`（④'）は `web`（⑤）の後、Review の `severity`（⑤）は `web`（⑥）の後。

---

## 4. HITL CONFIRM の往復

```
SSE  → {"type":"intervention","status":"waiting","data":{"intervention_id":"...","timeout_seconds":300, ...}}
POST → /api/{support|review|data}/confirm/{job_id}
       {"intervention_id":"...","approve":true,"selected_option":null}
SSE  → {"type":"intervention","status":"resolved","data":{"action":"proceed"}}
```

`ConfirmResponse.status` の値は 3 つ。

| 値 | 意味 | HTTP |
|---|---|---|
| `resolved` | 応答を注入できた | 200 |
| `not_waiting` | その `intervention_id` は待機中でない（タイムアウト済み・ID 違い） | **200**（エラーにしない） |
| `not_found` | ジョブが存在しない | 404 |

無応答のまま `timeout_seconds` を過ぎると `status:"timeout"` が流れ、**アクションは実行されない**。

---

## 5. HTTP ステータスの使い分け

| ステータス | 使う場面 |
|---|---|
| **200** | 参照系・SSE・CONFIRM 応答（`not_waiting` を含む） |
| **202** | ジョブ起動 |
| **400** | 許可ディレクトリ外の指定（`/api/files`） |
| **404** | ジョブ ID が無い / コレクションが無い |
| **422** | Pydantic のバリデーション違反。**未対応のモデル名**（`GET /api/models` に無い値）と文書長超過がここ |
| **503** | Qdrant へ接続できない（**参照系のみ**） |

### ⚠️ モデル名は受付時に弾く

`QueryRequest.model` / `ReviewRequest.model` は `_validate_model_choice` で検証され、
`get_selectable_ollama_models()` に無い値は **422** になる。

> 未知のモデル名を Ollama へそのまま投げると、**ジョブが起動してから実行時に失敗する**
> （原因が分かりにくい）。リクエスト受付の時点で弾くのが設計意図である。

### ⚠️ `/api/qdrant/health` だけは 200 固定

Qdrant が落ちていても 200 を返し、本文の `available: false` と理由で伝える。
503 にすると画面側で「通信エラー」と「Qdrant を起動してください」を出し分けられない。

---

## 6. スキーマ ↔ types.ts 対応表

| backend（`schemas.py`） | frontend（`types.ts`） | 使う画面 |
|---|---|---|
| `QueryRequest` | `QueryParams` | SupportPanel |
| `SupportResultModel`（`model_used` を含む） | `SupportResult` | SupportPanel |
| `SupportEventModel` | `SupportEvent` | 全パネル（SSE 共通） |
| `VerticalInfo` | `VerticalInfo` | SupportPanel |
| **`ModelChoice` / `ModelInfo`** | **`ModelChoice` / `ModelInfo`** | **App（ヘッダーのモデルセレクタ・全タブ）** |
| `ReviewRequest` / `ReviewResultModel` ほか Review 系 | 同名 | ReviewPanel |
| `RuleSetInfo` | `RuleSetInfo` | ReviewPanel |
| `QdrantHealth` / `CollectionInfo` / `CollectionDetail` / `CollectionPoints` | 同名 | DataPanel |
| `InputFileInfo` / `InputFileListResponse` | 同名 | DataPanel |
| `ChunkingRequest` / `QaGenerationRequest` / `RegisterRequest` | `ChunkingParams` / `QaParams` / `RegisterParams` | DataPanel |
| `DataJobStatusResponse` | `DataJobStatusResponse` | DataPanel |
| `ConfirmRequest` / `ConfirmResponse` | `InterventionInfo` 経由 | ConfirmModal / QuestionSelectModal |

---

## 7. 変更履歴

| Version | 日付 | 変更内容 |
|---|---|---|
| 1.1 | 2026-09-23 | §6 の型対応表で `ModelChoice` / `ModelInfo` の利用元を、削除済みの `ModelSelect` から `App`（ヘッダーのモデルセレクタ）へ訂正 |
| 1.0 | 2026-09-16 | 新規作成。全 25 エンドポイント（**`/api/models` `/api/model` を含む**）・SSE ワイヤ形式・ステータス方針・types.ts 対応を実装から書き起こした |

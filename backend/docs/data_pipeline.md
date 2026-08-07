# データ準備パイプライン（チャンキング / 登録 / 削除） ドキュメント

**Version 1.1** | 最終更新: 2026-08-05

---

## 目次

1. [概要](#概要)
2. [アーキテクチャ構成図](#1-アーキテクチャ構成図)
3. [モジュール構成図](#2-モジュール構成図)
4. [クラス・関数一覧表](#3-クラス関数一覧表)
5. [クラス・関数 IPO詳細](#4-クラス関数-ipo詳細)
6. [使用例](#5-使用例)
7. [エクスポート](#6-エクスポート)
8. [変更履歴](#7-変更履歴)
9. [付録: 依存関係図](#付録-依存関係図)

---

## 概要

CLI でしか実行できなかった**データ準備の 3 工程**（チャンク化 → Q/A 生成 → Qdrant 登録）と
コレクション管理を、Web API と React 画面から実行できるようにした一連のモジュール群。

GRACE-Support・GRACE-Review と**同じジョブ基盤**（`core/jobs.py`）に乗せているため、
SSE による進捗配信・HITL CONFIRM・ジョブ管理を新規に実装していない。

| 層 | 実体 | 役割 |
|---|---|---|
| API（参照） | `backend/app/api/qdrant.py` | コレクション一覧・詳細・ポイント・ヘルス・ファイル一覧 |
| API（ジョブ） | `backend/app/api/data.py` | チャンク化・登録・削除の起動、SSE、HITL 応答 |
| runner | `backend/app/core/data_jobs.py` | 3 種のジョブ本体（`register_runner` で登録） |
| 進捗転送 | `backend/app/core/job_logs.py` | 既存パッケージの `logging` 出力を SSE イベントへ |
| ラッパ | `services/data_pipeline_service.py` | CLI に埋まっていた処理の関数化・JSON 化・パス検証 |

### 設計の前提：既存パッケージは 1 行も変更していない

`chunking/` `qa_generation/` `qa_qdrant/` `services/qdrant_service.py` の中身は
**無改修**である。Web 化にあたって加えたのは以下だけ:

1. CLI の `main()` に埋まっていた処理を関数として取り出す層（`data_pipeline_service.py`）
2. `logging` 出力を SSE へ転送する仕組み（`job_logs.py`）
3. ジョブ基盤に載せる runner（`data_jobs.py`）

### 主な責務

- **チャンク化**: CSV / テキスト → セマンティックチャンク CSV（LLM・3 段階）
- **Qdrant 登録**: Q/A CSV → コレクション（Embedding 生成つき）
- **コレクション管理**: 一覧・詳細・ポイントのプレビュー・削除
- **破壊的操作の承認**: 削除は常に、登録は `recreate=True` のときだけ HITL CONFIRM を通す
- **入力ファイルの安全な選択**: 許可ディレクトリのホワイトリスト内に限定する
- **進捗の可視化**: 既存モジュールを無改修のまま SSE で進捗を流す

### 実行の前提（プロバイダ）

| 用途 | プロバイダ | 既定 | 必要なもの |
|---|---|---|---|
| チャンク化の LLM | **ローカル LLM（Ollama）** | `gemma4:e4b` | `ollama serve` ＋ `ollama pull gemma4:e4b` |
| 登録時の Embedding | **Gemini** | `gemini-embedding-001`（3072次元） | `GOOGLE_API_KEY` |

⚠️ **LLM 用の API キーは不要。** ローカル実行のためキーが存在しないので、
`_chunking_runner` にキーの起動ガードは置いていない（置くと常に失敗する）。
Ollama への疎通不良はチャンク化の例外として捕捉し、error イベントで返す。

⚠️ **Embedding を Ollama にしてはいけない。** 既存 Qdrant コレクションの
次元（3072）を維持するための決定であり、変えると全件再登録が必要になる。

### 各責務対応のモジュール

| # | 責務 | 対応モジュール | 説明 |
|---|------|--------------|------|
| 1 | チャンク化 | `core/data_jobs.py::_chunking_runner` → `chunking/csv_text_to_chunks_text_csv.py` | `chunks_all_async` を同期ラップして呼ぶ |
| 2 | Qdrant 登録 | `core/data_jobs.py::_register_runner` → `qa_qdrant/register_to_qdrant.py` | `register_to_qdrant()` は元から純関数 |
| 3 | 削除 | `core/data_jobs.py::_delete_runner` → `services/data_pipeline_service.py::delete_collection` | CLI に直書きだった処理を関数化 |
| 4 | 参照 | `api/qdrant.py` → `services/qdrant_service.py` | `QdrantDataFetcher` の DataFrame を JSON 化 |
| 5 | 承認 | `core/intervention_bridge.py`（既存） | Support / Review と同一の仕組み |
| 6 | 進捗 | `core/job_logs.py` | `logging.Handler` で横取り |
| 7 | パス検証 | `services/data_pipeline_service.py` | ホワイトリスト ＋ `resolve()` の二段 |

### 主要機能一覧

| 機能 | エンドポイント | 承認 |
|---|---|:---:|
| Qdrant 稼働確認 | `GET /api/qdrant/health` | — |
| コレクション一覧 | `GET /api/qdrant/collections` | — |
| コレクション詳細 | `GET /api/qdrant/collections/{name}` | — |
| ポイントのプレビュー | `GET /api/qdrant/collections/{name}/points` | — |
| 入力ファイル一覧 | `GET /api/files` | — |
| チャンク化の実行 | `POST /api/chunking/run` | なし |
| Qdrant 登録 | `POST /api/qdrant/register` | `recreate=True` のときだけ |
| コレクション削除 | `POST /api/qdrant/delete` | **常に** |
| 進捗の購読 | `GET /api/data/stream/{job_id}` | — |
| HITL 応答 | `POST /api/data/confirm/{job_id}` | — |
| 結果の取得 | `GET /api/data/result/{job_id}` | — |

---

## 1. アーキテクチャ構成図

### 1.1 システム全体構成

```mermaid
flowchart TB
    subgraph CLIENT["クライアント層"]
        direction TB
        FE["React（データ管理タブ）<br>DataPanel / DataJobPanel / CollectionPanel"]
        CLI["CLI（従来どおり利用可）<br>python -m chunking...ほか"]
    end

    subgraph API["API 層"]
        direction TB
        QR["api/qdrant.py<br>参照系（GET）"]
        DA["api/data.py<br>ジョブ系（POST + SSE）"]
    end

    subgraph CORE["ジョブ層（既存基盤を共用）"]
        direction TB
        JOBS["core/jobs.py<br>JobManager / register_runner"]
        DJ["core/data_jobs.py<br>3 種の runner"]
        JL["core/job_logs.py<br>logging 横取り"]
        IB["core/intervention_bridge.py<br>HITL CONFIRM"]
    end

    subgraph EXIST["既存パッケージ（無改修）"]
        direction TB
        CH["chunking/"]
        QQ["qa_qdrant/"]
        QS["services/qdrant_service.py"]
    end

    subgraph WRAP["ラッパ層"]
        direction TB
        DPS["services/data_pipeline_service.py"]
    end

    FE --> QR
    FE --> DA
    CLI --> EXIST
    DA --> JOBS
    JOBS --> DJ
    DJ --> JL
    DJ --> IB
    DJ --> DPS
    QR --> DPS
    DPS --> EXIST
    JL -.->|"logging を横取り"| EXIST
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class FE,CLI,QR,DA,JOBS,DJ,JL,IB,CH,QQ,QS,DPS default
style CLIENT fill:#1a1a1a,stroke:#fff,color:#fff
style API fill:#1a1a1a,stroke:#fff,color:#fff
style CORE fill:#1a1a1a,stroke:#fff,color:#fff
style EXIST fill:#1a1a1a,stroke:#fff,color:#fff
style WRAP fill:#1a1a1a,stroke:#fff,color:#fff
```

> **CLI は残す。** 大規模バッチや `--resume` は CLI の方が適しており、画面化の目的は
> 「小〜中規模の試行と可視化」である。両者は同じ関数を呼ぶので挙動は一致する。

### 1.2 承認フロー（削除の場合）

```mermaid
%%{ init: { "theme": "base", "themeVariables": {
  "background": "#000000", "mainBkg": "#000000",
  "textColor": "#ffffff", "lineColor": "#ffffff",
  "actorBkg": "#000000", "actorTextColor": "#ffffff",
  "actorLineColor": "#ffffff", "noteBkgColor": "#000000",
  "noteTextColor": "#ffffff", "noteBorderColor": "#ffffff" } } }%%
sequenceDiagram
    participant U as "ユーザー"
    participant FE as "CollectionPanel"
    participant API as "api/data.py"
    participant JM as "JobManager"
    participant R as "_delete_runner"
    participant Q as "Qdrant"
    U->>FE: "削除ボタン（2 件選択）"
    FE->>API: "POST /api/qdrant/delete"
    API->>JM: "start(DeleteParams)"
    JM->>R: "ワーカースレッドで実行"
    R->>Q: "対象の存在と件数を確認"
    R-->>FE: "step: inspect finished（対象と件数）"
    R-->>FE: "intervention: waiting"
    FE->>U: "ConfirmModal を表示"
    Note over R: "resolver がブロックして待つ"
    U->>FE: "承認"
    FE->>API: "POST /api/data/confirm/{job_id}"
    API->>JM: "confirm(intervention_id, approve=True)"
    JM->>R: "ブロック解除"
    R->>Q: "delete_collection × N"
    R-->>FE: "step: delete finished / result / done"
```

> ⚠️ **拒否・タイムアウトのいずれでも `delete_collection` は呼ばれない。**
> `_ask_confirmation()` が `(承認されたか, タイムアウトしたか)` を返し、
> 承認されていなければ実行前に return する。

---

## 2. モジュール構成図

```mermaid
flowchart TB
    subgraph L1["API"]
        direction TB
        A1["api/qdrant.py"]
        A2["api/data.py"]
    end
    subgraph L2["ジョブ"]
        direction TB
        B1["core/data_jobs.py"]
        B2["core/job_logs.py"]
        B3["core/jobs.py（既存）"]
    end
    subgraph L3["ラッパ"]
        direction TB
        C1["services/data_pipeline_service.py"]
    end
    subgraph L4["既存実装"]
        direction TB
        D1["chunking/"]
        D2["qa_qdrant/"]
        D3["services/qdrant_service.py"]
    end
    A1 --> C1
    A2 --> B1
    B1 --> B2
    B1 --> B3
    B1 --> C1
    C1 --> D1
    C1 --> D3
    B1 --> D2
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class A1,A2,B1,B2,B3,C1,D1,D2,D3 default
style L1 fill:#1a1a1a,stroke:#fff,color:#fff
style L2 fill:#1a1a1a,stroke:#fff,color:#fff
style L3 fill:#1a1a1a,stroke:#fff,color:#fff
style L4 fill:#1a1a1a,stroke:#fff,color:#fff
```

---

## 3. クラス・関数一覧表

### 3.1 `backend/app/core/job_logs.py`

| 種別 | 名前 | 説明 |
|---|---|---|
| クラス | `JobLogHandler` | 自スレッドのログレコードだけを `emit` へ転送する Handler |
| 関数 | `capture_logs()` | 指定ロガーの出力を転送するコンテキストマネージャ |
| 定数 | `DEFAULT_LOGGER_NAMES` | `chunking` / `qa_generation` / `qa_qdrant` / `services` |

### 3.2 `services/data_pipeline_service.py`

| 種別 | 名前 | 説明 |
|---|---|---|
| 定数 | `ALLOWED_INPUT_DIRS` | 参照を許可するディレクトリ（4 件） |
| 例外 | `PathNotAllowedError` | 許可外パスを指した |
| 関数 | `resolve_allowed_dir()` | ディレクトリ名 → 絶対パス（検証つき） |
| 関数 | `list_input_files()` | 入力ファイル候補の列挙（更新日時降順） |
| 関数 | `resolve_input_file()` | `dir/name` → 実パス |
| 関数 | `delete_collection()` | コレクションを 1 つ削除（例外を投げず bool） |
| 関数 | `collection_exists()` | 存在確認 |
| 関数 | `dataframe_to_records()` | DataFrame → `list[dict]`（NaN → None） |
| 関数 | `collection_columns()` | レコード列から出現順に列名を抽出 |
| 関数 | `run_chunking_sync()` | `chunks_all_async` の同期ラッパ |
| 関数 | `load_input_text()` | CSV / テキストの読み込み |

### 3.3 `backend/app/core/data_jobs.py`

| 種別 | 名前 | 説明 |
|---|---|---|
| dataclass | `ChunkingParams` | チャンク化のパラメータ（CLI 引数と 1:1） |
| dataclass | `RegisterParams` | 登録のパラメータ |
| dataclass | `DeleteParams` | 削除のパラメータ |
| 関数 | `_chunking_runner()` | 読み込み → チャンク化 → 出力 |
| 関数 | `_register_runner()` | 検証 → 承認（条件付き）→ Embedding → 登録 |
| 関数 | `_delete_runner()` | 対象確認 → 承認 → 削除 |
| 関数 | `_ask_confirmation()` | HITL CONFIRM を要求し `(承認, タイムアウト)` を返す |
| 定数 | `*_STEP_IDS` / `*_STEP_LABELS` | フロントの Timeline と 1:1 |

---

## 4. クラス・関数 IPO詳細

### 4.1 `capture_logs(emit_fn, logger_names, step, level)`

| 項目 | 内容 |
|---|---|
| **Input** | `emit_fn`（進捗の送信先）、`logger_names`（横取りするロガー）、`step`（イベントの step ID）、`level`（既定 INFO） |
| **Process** | ① `JobLogHandler` を生成（**生成時のスレッド ident を記録**）<br>② 対象ロガーに `addHandler`、level を参照カウント付きで引き上げ<br>③ `yield`<br>④ `finally` で `removeHandler` と level 復元 |
| **Output** | `JobLogHandler`（`set_step()` で転送先を切り替えられる） |

#### なぜスレッドで絞るのか

ハンドラは**ロガー（プロセス全体）**に付く。絞らないと、同時に走る別ジョブの
ログが自分の進捗として流れる。`record.thread` が生成時のスレッドと一致する
レコードだけを転送することで防ぐ。

#### なぜ level の復元に参照カウントが要るのか

素朴に「入るとき控えて出るとき書き戻す」と実装すると、**同時に 2 本走ったときに
復元されない**。

```
ジョブA: NOTSET(0) を控える → INFO(20) へ引き上げ
ジョブB: すでに 20 になっているものを「元の値」として控える
ジョブA: 終了 → 0 に戻す
ジョブB: 終了 → 20 に書き戻す   ← 元は 0 だった
```

結果、全ジョブ終了後もロガーが INFO のまま残り、コンソール出力が増え続ける。
最初に入った 1 本だけが元の値を持ち、最後に出る 1 本がそれを戻す方式にしてある。

> 回帰テスト: `backend/tests/test_job_logs.py::test_sequenced_jobs_restore_level`。
> Event で「A が入る → B が入る → A が出る → B が出る」の順序を固定している。
> **入れ子（後入れ先出し）では素朴実装でも通ってしまう**ため、そちらは回帰テストではない。

### 4.2 `resolve_input_file(rel_path, base)`

| 項目 | 内容 |
|---|---|
| **Input** | `rel_path`（`ディレクトリ名/ファイル名`）、`base`（基点。既定はカレント） |
| **Process** | ① `/` で 2 分割できるか検証<br>② ファイル名側に区切りが混ざっていないか検証<br>③ ディレクトリをホワイトリスト照合<br>④ `resolve()` 後に基点配下か検証<br>⑤ 実ファイルの存在確認 |
| **Output** | `Path`（絶対パス） |
| **例外** | `PathNotAllowedError` / `FileNotFoundError` |

**ホワイトリスト照合だけでは足りない。** `OUTPUT/../..` のような値に備えて
`resolve()` した結果が基点配下にあることも確認する（二段の検証）。

| 入力 | 結果 |
|---|---|
| `OUTPUT/a.csv` | ✅ 解決 |
| `a.csv` | ❌ 形式不正（ディレクトリ指定なし） |
| `OUTPUT/../../etc/passwd` | ❌ 形式不正（区切りが多い） |
| `logs/app.log` | ❌ 許可外ディレクトリ |
| `OUTPUT/..` | ❌ 親を指す |

### 4.3 `dataframe_to_records(df)`

| 項目 | 内容 |
|---|---|
| **Input** | pandas DataFrame（`QdrantDataFetcher.fetch_collection_points()` の戻り） |
| **Process** | `astype(object).where(pd.notnull(df), None)` で NaN を None へ寄せて `to_dict(orient="records")` |
| **Output** | `list[dict]` |

⚠️ **NaN を残すと不正な JSON になる。** JSON に NaN というリテラルは無く、
`json.dumps` が `NaN` という**パースできないトークン**を出力する。
列が揃わないコレクション（payload のキーがレコードごとに違う）では
必ず NaN が発生するため、この変換は必須である。

### 4.4 `_delete_runner(params, emit, confirm)`

| 項目 | 内容 |
|---|---|
| **Input** | `DeleteParams(collections)`、`emit`、`confirm` |
| **Process** | ① 対象の存在と件数を確認（`inspect`）<br>② **HITL CONFIRM**（`confirm`）<br>③ 承認されたら削除（`delete`） |
| **Output** | `{"kind": "delete", "deleted": [...], "failed": [...], "missing": [...], "cancelled": bool}` |

| 状況 | 挙動 |
|---|---|
| 承認 | 削除する |
| **拒否** | **削除しない**。`cancelled: true` で完了 |
| **タイムアウト** | **削除しない**（安全側）。`reason` に「タイムアウト」 |
| 一部が存在しない | 存在する分だけ削除し、`missing` に載せる |
| 全部存在しない | 承認を求めず error |

承認画面には**対象名と合計件数**を出す（何が消えるか分からないまま押させない）。

### 4.5 `_register_runner(params, emit, confirm)`

承認を求める条件は **`recreate=True` かつ既存コレクションがある**ときだけ。

| `recreate` | コレクション | 承認 | 理由 |
|:---:|---|:---:|---|
| `False` | 任意 | 不要 | 既存を壊さない（追記） |
| `True` | 存在する | **必要** | 削除して作り直す＝破壊的 |
| `True` | 存在しない | 不要 | 壊すものが無い |

毎回ダイアログを出すと煩わしいため、**破壊を伴う場合に限定**している。

---

## 5. 使用例

### 5.1 画面から（推奨）

```bash
./run_dev.sh          # backend :8000 + frontend :5173
# → ブラウザで「データ管理」タブ
#   ① チャンキング → ② Qdrant 登録 → ③ コレクション管理
```

### 5.2 API を直接叩く

```bash
# コレクション一覧
curl -s localhost:8000/api/qdrant/collections | jq

# 入力ファイル候補
curl -s 'localhost:8000/api/files?dir=OUTPUT' | jq '.files[].path'

# チャンク化を起動して進捗を眺める
JOB=$(curl -s -X POST localhost:8000/api/chunking/run \
  -H 'Content-Type: application/json' \
  -d '{"input_file":"OUTPUT/cc_news_1per.csv","workers":8}' | jq -r .job_id)
curl -N localhost:8000/api/data/stream/$JOB
```

### 5.3 進捗を見失ったとき（再購読）

フロントはタブ切替でパネルをアンマウントするため SSE 購読が切れるが、
`job_id` を覚えておけば購読し直せる。**`Job.stream_events()` は常にイベントを
先頭からリプレイする**ので、再購読するだけでタイムラインも承認待ちも復元される。

```bash
# ジョブがまだ存在するかを先に確かめる（消えていれば 404）
curl -s localhost:8000/api/data/result/$JOB | jq '{status, kind}'

# 生きていれば購読し直す。先頭から全イベントが流れてくる
curl -N localhost:8000/api/data/stream/$JOB
```

> ⚠️ **存在確認を挟むのが重要。** 完了ジョブは 50 件で GC される
> （`MAX_FINISHED_JOBS`）。消えた `job_id` に SSE で直接つなぐと、
> フロント側では `onerror` が発火して「切断されました」という**誤ったエラー**になる。

### 5.4 削除（承認が要る）

```bash
JOB=$(curl -s -X POST localhost:8000/api/qdrant/delete \
  -H 'Content-Type: application/json' \
  -d '{"collections":["old_collection"]}' | jq -r .job_id)

# SSE に intervention が流れるので intervention_id を取り、承認する
curl -s -X POST localhost:8000/api/data/confirm/$JOB \
  -H 'Content-Type: application/json' \
  -d '{"intervention_id":"<SSE で受け取った ID>","approve":true}'
```

> 承認しなければ削除されない。**承認を経ずに消す API は用意していない**
> （HTTP `DELETE` メソッドを使っていないのはこのため）。

### 5.4 CLI（従来どおり）

```bash
# 大規模バッチ・--resume は CLI の方が適している
python -m chunking.csv_text_to_chunks_text_csv \
  --input-file OUTPUT/cc_news_1per.csv --output output_chunked
python qa_qdrant/register_to_qdrant.py --input-file qa_output/x.csv --collection x
python qdrant_delete_collection.py x --yes
```

---

## 6. エクスポート

### `backend/app/core/job_logs.py`

```python
__all__ = ["JobLogHandler", "capture_logs", "DEFAULT_LOGGER_NAMES", "EmitFn"]
```

### `services/data_pipeline_service.py`

```python
ALLOWED_INPUT_DIRS, PathNotAllowedError,
resolve_allowed_dir, list_input_files, resolve_input_file,
delete_collection, collection_exists,
dataframe_to_records, collection_columns,
run_chunking_sync, load_input_text
```

### `backend/app/core/data_jobs.py`

```python
ChunkingParams, RegisterParams, DeleteParams
CHUNKING_STEP_IDS, REGISTER_STEP_IDS, DELETE_STEP_IDS
CHUNKING_STEP_LABELS, REGISTER_STEP_LABELS, DELETE_STEP_LABELS
```

> runner（`_chunking_runner` 等）は private。`register_runner()` により
> **params の型から解決される**ので、外から直接呼ぶ必要はない。

---

## 7. 変更履歴

| 版 | 日付 | 変更内容 |
|---|---|---|
| 1.0 | 2026-08-05 | 初版作成（D0〜D10） |
| 1.1 | 2026-08-05 | 再購読（タブ離脱後の進捗復元）の節を追加。`stream_events()` が先頭からリプレイする性質に依存することを明記 |

---

## 付録: 依存関係図

```mermaid
flowchart TB
    subgraph NEW["新規追加"]
        direction TB
        N1["api/qdrant.py"]
        N2["api/data.py"]
        N3["core/data_jobs.py"]
        N4["core/job_logs.py"]
        N5["services/data_pipeline_service.py"]
    end
    subgraph REUSE["再利用（無改修）"]
        direction TB
        R1["core/jobs.py"]
        R2["core/intervention_bridge.py"]
        R3["chunking/"]
        R4["qa_qdrant/register_to_qdrant.py"]
        R5["services/qdrant_service.py"]
        R6["qdrant_client_wrapper.py"]
    end
    N2 --> N3
    N3 --> N4
    N3 --> R1
    N3 --> R2
    N3 --> N5
    N3 --> R4
    N1 --> N5
    N5 --> R3
    N5 --> R5
    N5 --> R6
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class N1,N2,N3,N4,N5,R1,R2,R3,R4,R5,R6 default
style NEW fill:#1a1a1a,stroke:#fff,color:#fff
style REUSE fill:#1a1a1a,stroke:#fff,color:#fff
```

**新規 5 ファイルに対し、再利用 6 ファイルは無改修。** `jobs.py` の
`register_runner` 機構と `InterventionBridge` が、そのまま 3 種類目の
ジョブ系統を受け入れられる設計だったことによる。

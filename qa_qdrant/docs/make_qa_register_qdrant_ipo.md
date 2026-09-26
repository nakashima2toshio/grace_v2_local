# make_qa_register_qdrant.py - Q/A 生成 → Qdrant 登録 統合 CLI ドキュメント

**Version 1.2** | 最終更新: 2026-09-26

---

## 目次

1. [概要](#概要)
2. [アーキテクチャ構成図](#1-アーキテクチャ構成図)
3. [モジュール構成図](#2-モジュール構成図)
4. [入力の振り分けと既知の問題（重点解説）](#3-入力の振り分けと既知の問題重点解説)
5. [クラス・関数一覧表](#4-クラス関数一覧表)
6. [クラス・関数 IPO詳細](#5-クラス関数-ipo詳細)
7. [設定・定数](#6-設定定数)
8. [エクスポート](#7-エクスポート)
9. [変更履歴](#8-変更履歴)
10. [付録: 依存関係図](#付録-依存関係図)

---

## 概要

`qa_qdrant/make_qa_register_qdrant.py` は、チャンク済み CSV・テキストファイル（`.txt`・先にチャンク化する）・
事前定義データセットのいずれかから Q/A ペアを生成し（Phase 1）、その Q/A を Embedding して Qdrant コレクションへ登録する（Phase 2）
**統合 CLI** です。Q/A 生成は `qa_generation.pipeline.QAPipeline`（ローカル LLM・Ollama。既定は `config.py::get_default_ollama_model()` = `gemma4:12b-mlx`）に、
Embedding と Qdrant 操作は `services/qdrant_service.py`（Gemini `gemini-embedding-001`・3072 次元）に委譲し、
本モジュールは**入力の振り分け・Ollama の事前確認・`.txt` のチャンク化の呼び出し・2 フェーズの順序制御・登録ループ・UI 用 CSV の出力**を受け持ちます。

> 📎 **使い方（運用手順）は [`make_qa_register_qdrant.md`](make_qa_register_qdrant.md)（種別 B・手順書）**、
> 本書はモジュール仕様（IPO）です。同名の手順書が先にあったため、本書はファイル名に `_ipo` を付けています
> （姉妹リポジトリ grace_v2 と同じ命名）。
>
> 📌 **データ管理タブ（Web）はこの CLI を呼ばない。** Q/A 生成は `services/data_pipeline_service.py::run_qa_generation_sync()`、
> 登録は `qa_qdrant/register_to_qdrant.py` を通る。Phase 1 は同じ `QAPipeline` なので生成結果は変わらない。
>
> 📌 [§3.3 既知の問題](#33-既知の問題2026-09-26-実測) の 6 件は **2026-09-26 にすべて修正済み**（修正前の挙動の記録として残している）。

### 主な責務

- 入力ソース（`--dataset` / `--input-file`）の排他検証と、ファイル種別・カラムによる処理の振り分け
- LLM を呼ぶ前の Ollama の事前確認（接続できるか・モデルが pull 済みか）
- `.txt` 入力の事前チャンク化（チャンク化 CLI・データ管理タブと同じ経路）
- Q/A 生成（Phase 1）の `QAPipeline` への委譲と、生成された Q/A CSV の特定
- Q/A の `question` 列のバッチ Embedding と Qdrant へのアップサート（Phase 2）
- UI 用に `question` / `answer` だけを持つ CSV を、日時サフィックスを外した固定名で出力する
- ファイル名から日時サフィックスを除いた正規化名の生成（Qdrant の `source` と UI 用 CSV 名に使う）

### 各責務対応のモジュール

| # | 責務 | 対応モジュール | 説明 |
|---|------|--------------|------|
| 1 | 入力の排他検証と振り分け | `make_qa_register_qdrant.py` | `main()` が引数を解析し、`.txt` / `.csv`（Q/A 列あり・本文列あり）/ データセットで分岐（§3） |
| 2 | Ollama の事前確認 | `services/data_pipeline_service.py` | `require_ollama_ready()` が `ollama_unreachable_message()` / `model_not_pulled_message()`（データ管理タブ・チャンク化 CLI と同じ判定）を呼ぶ |
| 3 | `.txt` の事前チャンク化 | `services/data_pipeline_service.py` | `chunk_text_file()` が `run_chunking_sync()`（`chunking.csv_text_to_chunks_text_csv.chunks_all_async` の同期版）で `<入力名>_chunks.csv` を作る |
| 4 | Q/A 生成の委譲 | `qa_generation/pipeline.py` | `QAPipeline.run()` が `SmartQAGenerator` で生成し、`saved_files["qa_csv"]` を返す |
| 5 | Embedding と Qdrant 登録 | `services/qdrant_service.py` | `run_registration()` が `embed_texts_for_qdrant()` / `build_points_for_qdrant()` / `upsert_points_to_qdrant()` を呼ぶ |
| 6 | UI 用 CSV の出力 | `make_qa_register_qdrant.py` | `run_registration()` の末尾で `question` / `answer` 列だけを `--ui-output` へ書く |
| 7 | ファイル名の正規化 | `make_qa_register_qdrant.py` | `normalize_source_filename()` が `_YYYYMMDD_HHMMSS` を除去 |

### 主要機能一覧

| 機能 | 説明 |
|------|------|
| `main()` | CLI エントリーポイント。引数解析 → 入力検証 → Phase 1 → Phase 2 |
| `require_ollama_ready()` | Ollama へ接続できるか・モデルが pull 済みかを確かめ、駄目なら終了コード 1 |
| `chunk_text_file()` | `.txt` をセマンティックチャンク化し、チャンク CSV のパスを返す |
| `run_registration()` | Q/A CSV を Embedding して Qdrant へ登録し、UI 用 CSV を書く（Phase 2） |
| `normalize_source_filename()` | ファイル名から日時サフィックス（`_YYYYMMDD_HHMMSS`）を除去 |

---

## 1. アーキテクチャ構成図

### 1.1 システム全体構成

```mermaid
flowchart TB
    subgraph CLIENT["呼び出し側"]
        USER["利用者の端末（python qa_qdrant/make_qa_register_qdrant.py）"]
        WORKER["Celery ワーカー（start_celery.sh・--use-celery 時）"]
    end

    subgraph MODULE["make_qa_register_qdrant.py"]
        MAIN["main()"]
        READY["require_ollama_ready()"]
        CHUNK["chunk_text_file()（.txt のみ）"]
        REG["run_registration()"]
        NORM["normalize_source_filename()"]
    end

    subgraph EXTERNAL["外部サービス層"]
        DPS["services.data_pipeline_service（Ollama 確認・run_chunking_sync）"]
        PIPE["QAPipeline（qa_generation）"]
        OLLAMA["Ollama（ローカル LLM・チャンク化と Q/A 生成）"]
        QSVC["services.qdrant_service"]
        GEMINI["Gemini gemini-embedding-001（3072 次元）"]
        QDRANT["Qdrant"]
        FS["ファイルシステム（output_chunked/・qa_output/）"]
    end

    USER --> MAIN
    MAIN --> READY
    READY --> DPS
    MAIN --> CHUNK
    CHUNK --> DPS
    DPS --> OLLAMA
    DPS --> FS
    MAIN --> PIPE
    PIPE --> OLLAMA
    PIPE --> WORKER
    MAIN --> REG
    REG --> NORM
    REG --> QSVC
    QSVC --> GEMINI
    QSVC --> QDRANT
    PIPE --> FS
    REG --> FS
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class USER,WORKER,MAIN,READY,CHUNK,REG,NORM,DPS,PIPE,OLLAMA,QSVC,GEMINI,QDRANT,FS default
style CLIENT fill:#1a1a1a,stroke:#fff,color:#fff
style MODULE fill:#1a1a1a,stroke:#fff,color:#fff
style EXTERNAL fill:#1a1a1a,stroke:#fff,color:#fff
```

### 1.2 データフロー

1. `main()` が引数を解析し、`--dataset` / `--input-file` のどちらか 1 つだけが指定されていること、`GOOGLE_API_KEY`（Embedding 用）があることを確かめる
2. Q/A を生成する経路（§3.1 の 1・2・4）では、LLM を呼ぶ前に `require_ollama_ready()` で Ollama へ接続できるか・使うモデルが pull 済みかを確かめる（駄目なら終了コード 1）
3. `.txt` 入力なら、まず `chunk_text_file()` が `<--chunk-output>/<入力名>_chunks.csv` を作る（チャンク化にも Ollama を使う）
4. **Phase 1**: 入力に応じて `QAPipeline.run()` で Q/A を生成し、`<--output>/qa_pairs_<種別>_<日時>.csv` を得る
   （入力 CSV が既に `question` / `answer` 列を持つ場合は生成を飛ばし、その CSV をそのまま使う）。**Q/A が 0 件なら終了コード 1**
5. **Phase 2**: `run_registration()` が Q/A CSV を読み、`question` 列だけを `--batch-size` 件ずつ Embedding する
6. 各バッチを Qdrant のポイントに組み立て、`source` をファイル名の正規化名に揃えてアップサートする（失敗なら終了コード 1）
7. 最後に `question` / `answer` 列だけの CSV を `<--ui-output>/<正規化名>` へ書き出す

---

## 2. モジュール構成図

### 2.1 内部モジュール構成

```mermaid
flowchart TB
    subgraph ENTRY["エントリーポイント"]
        MAIN["main()"]
        ARGS["argparse（入力 / CSV / チャンク化 / Q/A 生成 / Qdrant 登録 / 出力）"]
    end

    subgraph PHASE1["Phase 1: Q/A 生成"]
        ROUTE["入力の振り分け（§3）"]
        READY["require_ollama_ready()"]
        CHUNK["chunk_text_file()（.txt のみ）"]
        PIPE["QAPipeline(...).run(...)"]
        ZERO["Q/A 0 件なら終了コード 1"]
    end

    subgraph PHASE2["Phase 2: Qdrant 登録"]
        REG["run_registration()"]
        NORM["normalize_source_filename()"]
        UICSV["UI 用 CSV の書き出し"]
    end

    MAIN --> ARGS
    ARGS --> ROUTE
    ROUTE --> READY
    READY --> CHUNK
    CHUNK --> PIPE
    READY --> PIPE
    ROUTE --> REG
    PIPE --> ZERO
    ZERO --> REG
    REG --> NORM
    REG --> UICSV
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class MAIN,ARGS,ROUTE,READY,CHUNK,PIPE,ZERO,REG,NORM,UICSV default
style ENTRY fill:#1a1a1a,stroke:#fff,color:#fff
style PHASE1 fill:#1a1a1a,stroke:#fff,color:#fff
style PHASE2 fill:#1a1a1a,stroke:#fff,color:#fff
```

### 2.2 外部依存関係

| ライブラリ | 用途 |
|-----------|------|
| `pandas` | 入力 CSV の判定・Q/A CSV の読み込み・UI 用 CSV の書き出し |
| `argparse` / `logging` / `re` / `pathlib`（標準） | 引数解析・ログ・日時サフィックスの除去・拡張子判定 |

### 2.3 内部依存モジュール

| モジュール | 用途 |
|-----------|------|
| `config.DATASET_CONFIGS` | `--dataset` の選択肢（`wikipedia_ja` / `wikipedia_ja_5per` / `japanese_text` / `fineweb_edu_ja` / `cc_news` / `livedoor`） |
| `config.get_default_ollama_model` | `--model` / `--chunk-model` の既定値（環境変数 `OLLAMA_DEFAULT_MODEL` があればそちら） |
| `config.get_default_chunking_workers` | `.txt` のチャンク化の並列数（`OLLAMA_NUM_PARALLEL` または 1。データ管理タブと同じ） |
| `services.data_pipeline_service.ollama_unreachable_message` | Ollama へ接続できるか（接続拒否だけを「確実に落ちている」と判定） |
| `services.data_pipeline_service.model_not_pulled_message` | モデルが Ollama に pull 済みか |
| `services.data_pipeline_service.run_chunking_sync` | `.txt` のチャンク化（`chunks_all_async()` を同期実行。データ管理タブのチャンク化ジョブと同じ関数） |
| `chunking.csv_text_to_chunks_text_csv.generate_output_filename` | チャンク CSV のパス（`<出力先>/<入力名>_chunks.csv`）を決める |
| `qa_generation.pipeline.QAPipeline` | Phase 1 の Q/A 生成（`SmartQAGenerator`・Celery 並列対応） |
| `qdrant_client_wrapper.create_qdrant_client` | Qdrant クライアントの生成 |
| `services.qdrant_service.create_or_recreate_collection_for_qdrant` | コレクションの作成・再作成（既定 3072 次元） |
| `services.qdrant_service.embed_texts_for_qdrant` | Gemini Embedding（`gemini-embedding-001`。プロバイダは固定） |
| `services.qdrant_service.build_points_for_qdrant` | DataFrame → `PointStruct`（payload: `domain` / `question` / `answer` / `source` / `created_at` / `schema`=`qa:v1` ほか） |
| `services.qdrant_service.upsert_points_to_qdrant` | ポイントのアップサート |

---

## 3. 入力の振り分けと既知の問題（重点解説）

### 3.1 入力ごとの処理

`main()` は入力の種類で Phase 1 を切り替えます。**判定はこの順に行われる**（上から最初に当てはまった行）。

| # | 入力 | 判定条件 | Phase 1 | Phase 2 に渡す CSV |
|---|------|---------|---------|-------------------|
| 1 | `--dataset <名前>` | — | `require_ollama_ready([--model])` のあと `QAPipeline(dataset_name=...)` で生成 | 生成した Q/A CSV |
| 2 | `--input-file *.txt` | 拡張子 `.txt` | `require_ollama_ready([--chunk-model, --model])` → `chunk_text_file()` で `<--chunk-output>/<入力名>_chunks.csv` を作る → `QAPipeline(input_file=<チャンク CSV>)` で生成。本文が空なら終了コード 1 | 生成した Q/A CSV |
| 3 | `--input-file *.csv` | `question` 列と `answer` 列が両方ある | **生成しない**（Q/A 済みとみなす）。Ollama も確かめない | 入力 CSV そのもの |
| 4 | `--input-file *.csv` | `--text-column`（既定 `text`）列、または `Combined_Text` 列がある | `require_ollama_ready([--model])` のあと `QAPipeline(input_file=..., text_column=<判定に使った列>)` で生成（`--text-column` の列があればそれ、無ければ `Combined_Text`） | 生成した Q/A CSV |
| 5 | `--input-file *.csv` | 上のどれにも当てはまらない | エラー（必要なカラムを表示）・終了コード 1 | — |
| 6 | `--input-file` のその他の拡張子 | — | エラー（「未対応のファイル形式」）・終了コード 1 | — |

生成した場合（1・2・4）、**Q/A が 0 件なら Phase 2 へ進まずに終了コード 1** で止まる。

```mermaid
flowchart TB
    START["main()"]
    DS{"--dataset ?"}
    EXT{"拡張子"}
    COLS{"カラム"}
    READY["require_ollama_ready()"]
    CHUNK["chunk_text_file() でチャンク化"]
    GEN["QAPipeline で生成（Phase 1）"]
    ZERO{"Q/A 0 件 ?"}
    SKIP["生成しない（入力をそのまま使う）"]
    FAIL["エラー終了（終了コード 1）"]
    REG["run_registration()（Phase 2）"]

    START --> DS
    DS -->|"はい"| READY
    DS -->|"いいえ（--input-file）"| EXT
    EXT -->|".txt"| READY
    EXT -->|".csv"| COLS
    EXT -->|"その他"| FAIL
    COLS -->|"question + answer"| SKIP
    COLS -->|"text / Combined_Text"| READY
    COLS -->|"どれも無い"| FAIL
    READY -->|"Ollama 不通・未 pull"| FAIL
    READY -->|".txt"| CHUNK
    READY -->|"それ以外"| GEN
    CHUNK --> GEN
    GEN --> ZERO
    ZERO -->|"はい"| FAIL
    ZERO -->|"いいえ"| REG
    SKIP --> REG
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class START,DS,EXT,COLS,READY,CHUNK,GEN,ZERO,SKIP,FAIL,REG default
```

> 📌 `.txt` のチャンク化はチャンク化 CLI（`python -m chunking.csv_text_to_chunks_text_csv`）と同じ処理で、
> 出力も同じ `<入力名>_chunks.csv`（CLAUDE.md §8.2・同名があれば上書き）。並列数は `get_default_chunking_workers()`
> （`OLLAMA_NUM_PARALLEL` または 1）、ブロックサイズは 1000 字で、CLI からは変えられない。変えたいときや
> `--resume` で再開したいときは、チャンク化 CLI で先に作ってから CSV を渡す。

### 3.2 出力ファイル

| 出力 | 場所・名前 | 中身 |
|------|-----------|------|
| チャンク CSV（`.txt` 入力のみ） | `<--chunk-output>/<入力名>_chunks.csv` と `<入力名>_chunks_simple.csv`（既定 `output_chunked/`） | セマンティックチャンク。**同じ入力なら上書き** |
| Q/A CSV（Phase 1） | `<--output>/qa_pairs_<種別>_<YYYYMMDD_HHMMSS>.csv`（`qa_generation/data_io.py::save_results`） | 生成した Q/A と付随列。**実行ごとに新しいファイル** |
| Q/A JSON（Phase 1） | `<--output>/qa_pairs_<種別>_<YYYYMMDD_HHMMSS>.json` | 同上の JSON 版 |
| カバレッジ（Phase 1） | `<--output>/coverage_<種別>_<YYYYMMDD_HHMMSS>.json` | カバレッジ分析の結果（常に実行・§6.1） |
| UI 用 CSV（Phase 2） | `<--ui-output>/<Q/A CSV 名から日時を除いた名前>`（例 `qa_output/qa_pairs_cc_news_1per_chunks.csv`） | `question` / `answer` の 2 列だけ。**同じ入力なら上書き** |
| Qdrant | コレクション `--collection` | Q/A 1 件 = 1 ポイント。`source` payload は UI 用 CSV と同じ正規化名 |

`<種別>` は `QAPipeline` の設定の `type` です。`--input-file` のときは入力ファイル名の拡張子を除いた部分
（例 `cc_news_1per_chunks`。`.txt` 入力ではチャンク CSV の名前なので `<入力名>_chunks`）。`--dataset` のときは**データセット名**（例 `cc_news`）です。
`config.DATASET_CONFIGS` の各エントリには `type` キーが無いため、`QAPipeline._load_config()` がデータセット名で補います。
2026-09-26 までは補っておらず、`self.config.get("type", "unknown")` の既定値へ倒れて**どのデータセットでも `unknown`** になっていました。
その結果、UI 用 CSV（`qa_pairs_unknown.csv`）が上書きされるだけでなく、チャンク ID（`unknown_chunk_<n>`）と途中経過ファイル
（`qa_progress_unknown.jsonl`）もデータセット間で共有され、途中で落ちたデータセットの途中経過を別のデータセットの再開が読んでいました
（`backend/tests/test_qa_pipeline_dataset_type.py`。修正前の実装で fail することを確認）。

### 3.3 既知の問題（2026-09-26 実測）

v1.0 を書く際に実装を読み、ダミーの `GOOGLE_API_KEY`（外部 API へは届かない）・Qdrant 停止・Ollama 停止の状態で
CLI を実行して確かめたものです。v1.0 では挙動を記録するだけでコードは変えなかった。**6 件とも 2026-09-26 に修正済み**。
1〜4 は姉妹リポジトリ grace_v2 の修正（2026-09-25）を移植し、grace_v2 の `ANTHROPIC_API_KEY` の事前確認は
本リポジトリでは Ollama の事前確認（5）に置き換えた。

各修正にはテストがあり、**修正前の実装に当てて fail することを確認**した（17 件中 14 件が fail。残る 3 件は
「Q/A 済み CSV は Ollama なしで通る」「登録成功で正常終了」「`text_column` 未指定なら従来の自動検出」という、
変えてはいけない挙動を見張るテスト）。

| # | 問題 | 修正前の実測 | 修正 |
|---|------|-----------|------|
| 1 | ~~**`.txt` 入力は必ず失敗する。**~~ ✅ | `.txt` をそのまま `QAPipeline` へ渡し、`QAPipeline.load_data()` が `ValueError: 未対応のファイル形式: .txt` → 終了コード 1 | `chunk_text_file()` で先にチャンク化する（§3.1 の 2。`backend/tests/test_make_qa_register_qdrant_txt_input.py`） |
| 2 | ~~**Qdrant 登録が失敗しても終了コードは 0。**~~ ✅ | Q/A 列ありの CSV を Qdrant 停止中に渡すと `Qdrant接続エラー: [Errno 111] Connection refused` のあと終了コード 0 | `sys.exit(1)` で止める（`backend/tests/test_make_qa_register_qdrant_exit_code.py`） |
| 3 | ~~**`--provider` は効かない。**~~ ✅ | `--provider openai` を渡してもエラーにならず、黙って Gemini で Embedding | Embedding は Gemini だけという方針（CLAUDE.md §3）に合わせ `choices=["gemini"]`。他の値は argparse が終了コード 2 で拒否。`provider` は登録開始ログに出す（`backend/tests/test_make_qa_register_qdrant_startup_checks.py`） |
| 4 | ~~**`--text-column` は Q/A 生成に渡らない。**~~ ✅ | `QAPipeline` が `text` → `Combined_Text` → `content` → `chunk_text` の順で自分で列を探し、`text` 列もある CSV では指定を無視した | `QAPipeline` に `text_column` 引数を足し、判定に使った列を渡す。既定 `None` は従来の自動検出なので、データ管理タブ・`make_qa.py` は変わらない（`backend/tests/test_qa_pipeline_text_column.py`） |
| 5 | ~~**起動時に LLM（Ollama）への接続を確かめない。**~~ ✅ | Ollama 停止中にチャンク済み CSV を渡すと、各チャンクで `Connection error.` を出したまま `Q/A生成完了: 0 ペア` まで進んだ | Q/A を生成する経路（§3.1 の 1・2・4）で `require_ollama_ready()` が生成（`.txt` はチャンク化）の前に確かめ、接続できない・モデルが未 pull なら終了コード 1。Q/A 済み CSV の登録（§3.1 の 3）では確かめない（`backend/tests/test_make_qa_register_qdrant_startup_checks.py`・`test_make_qa_register_qdrant_txt_input.py`） |
| 6 | ~~**Q/A が 0 件でも異常終了しない。**~~ ✅ | 空の Q/A CSV で `run_registration()` が `No columns to parse from file` になり、2 と重なって終了コード 0 | Phase 1 のあと `qa_count == 0` なら登録へ進まず終了コード 1（`backend/tests/test_make_qa_register_qdrant_exit_code.py`） |

> 📝 `normalize_source_filename()` の docstring は「UI（agent_rag.py）での参照を安定させるため」と書くが、
> `agent_rag.py` は本リポジトリに存在しない（CLAUDE.md §9.4）。現在は、データ管理タブの「③ Qdrant 登録」の
> 選択肢（`services/data_pipeline_service.py::list_input_files()`）が `qa_output/` **直下**のファイルを列挙するので、
> UI 用 CSV（既定 `qa_output/`）はそこに現れる。一方、本 CLI の Q/A CSV の既定出力先 `qa_output/pipeline/` は
> サブディレクトリなので列挙されない（同関数は `iterdir()` で入れ子を見ない）。

---

## 4. クラス・関数一覧表

### 4.1 クラス一覧

クラスはありません。

### 4.2 関数一覧（カテゴリ別）

#### エントリーポイント

| 関数 | 説明 |
|------|------|
| `main()` | CLI 引数を解析し、Phase 1（Q/A 生成）→ Phase 2（Qdrant 登録）を実行する |

#### Qdrant 登録

| 関数 | 説明 |
|------|------|
| `run_registration(csv_path, collection_name, recreate, batch_size, provider, ui_output_dir="qa_output")` | Q/A CSV を Embedding して Qdrant へ登録し、UI 用 CSV を書く。成功で `True` |

#### 事前確認・チャンク化

| 関数 | 説明 |
|------|------|
| `require_ollama_ready(models, purpose)` | Ollama へ接続できるか・`models` がすべて pull 済みかを確かめ、駄目なら終了コード 1 |
| `chunk_text_file(txt_path, output_dir, model)` | `.txt` をチャンク化して `<output_dir>/<入力名>_chunks.csv` のパスを返す |

#### ユーティリティ

| 関数 | 説明 |
|------|------|
| `normalize_source_filename(filename)` | `_YYYYMMDD_HHMMSS` を除いたファイル名を返す |

---

## 5. クラス・関数 IPO詳細

### 5.1 使用例

#### 5.1.1 基本的なワークフロー（チャンク済み CSV → Q/A 生成 → 登録）

```bash
# 前提: ollama serve 起動済み（既定モデル gemma4:12b-mlx を pull 済み）、
#       .env に GOOGLE_API_KEY（Embedding）、Qdrant 起動済み
#       Ollama が落ちている・モデルが未 pull なら、生成の前に終了コード 1 で止まる

# 1. チャンク化（生 CSV から始める場合。.txt なら 5.1.2 のとおり本 CLI がチャンク化まで行う）
python -m chunking.csv_text_to_chunks_text_csv \
  --input-file OUTPUT/cc_news_1per.csv --output output_chunked

# 2. Q/A 生成 → Qdrant 登録（Celery 不使用・同期）
python qa_qdrant/make_qa_register_qdrant.py \
  --input-file output_chunked/cc_news_1per_chunks.csv \
  --collection cc_news_1per \
  --recreate

# 出力例（ログの末尾）:
# 🎉 統合処理が正常に完了しました！
#    コレクション: cc_news_1per
#    データ件数  : 1234 件
#    Q/A CSV     : qa_output/pipeline/qa_pairs_cc_news_1per_chunks_20260926_101500.csv
#    UI用CSV     : qa_output/qa_pairs_cc_news_1per_chunks.csv
```

#### 5.1.2 テキストファイル（.txt）からチャンク化も含めて一気に回す（Celery 並列）

```bash
# 1. ワーカーを起動（別ターミナル）。-c はこの CLI の --concurrency と同じ値にそろえる
./start_celery.sh restart -c 8 --flower

# 2. .txt を渡すと、先に output_chunked/document_chunks.csv を作ってから Q/A 生成・登録する
python qa_qdrant/make_qa_register_qdrant.py \
  --input-file data/document.txt \
  --collection my_collection \
  --use-celery --concurrency 8 \
  --recreate

# 出力例（抜粋）:
# 📝 テキストファイル検出 - チャンク作成 + Q/A生成を実行します
# ✂️ チャンク化: data/document.txt → output_chunked/document_chunks.csv（model=gemma4:12b-mlx）
# ✅ チャンク作成完了: 42 チャンク
```

#### 5.1.3 生成済みの Q/A CSV を登録だけする

```bash
# question / answer 列を持つ CSV を渡すと Phase 1 を飛ばす（§3.1 の 3）。Ollama は不要
python qa_qdrant/make_qa_register_qdrant.py \
  --input-file qa_output/qa_pairs_cc_news_1per_chunks.csv \
  --collection cc_news_1per

# 出力例:
# ✅ Q/Aカラムが存在します - Q/A生成をスキップして登録へ
```

> 📌 登録に失敗すると終了コード 1 で止まる（`❌ Qdrant登録フェーズで失敗しました。`）。

### 5.2 エントリーポイント

#### `main`

**概要**: CLI エントリーポイント。引数を解析・検証し、Phase 1（Q/A 生成）と Phase 2（Qdrant 登録）を順に実行する。

```python
def main() -> None
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| （なし） | — | — | `sys.argv` から CLI 引数を読む（§6.1） |

| 項目 | 内容 |
|------|------|
| **Input** | CLI 引数（§6.1）、環境変数 `GOOGLE_API_KEY` |
| **Process** | 1. `argparse` で引数を解析（`--collection` は必須。`--provider` に `gemini` 以外を渡すと argparse が終了コード 2）<br>2. `--dataset` と `--input-file` がちょうど 1 つであることを確かめる（0 個・2 個ならエラー終了）<br>3. `GOOGLE_API_KEY` が無ければエラー終了<br>4. 入力の種類で Phase 1 を振り分け（§3.1）。Q/A を生成する経路では生成の前に `require_ollama_ready()` を呼ぶ（`.txt` は `[--chunk-model, --model]` を確かめてから `chunk_text_file()` でチャンク CSV を作る）。生成は `QAPipeline(...).run(use_celery, celery_workers, concurrency, batch_chunks, analyze_coverage=True)` で、`result["saved_files"]["qa_csv"]` を Q/A CSV とする<br>5. Q/A CSV が作られていなければエラー終了<br>6. `qa_count == 0` ならエラー終了（Phase 2 へ進まない）<br>7. `run_registration()` で Phase 2 を実行<br>8. 成功なら件数・Q/A CSV・UI 用 CSV のパスをログに出す。失敗ならエラーログを出して終了コード 1<br>9. 途中の例外は「致命的なエラー」としてトレースバックを出して終了コード 1 |
| **Output** | `None`。副作用としてチャンク CSV（`.txt` のみ）・Q/A CSV / JSON・UI 用 CSV・Qdrant のポイントを作る。**終了コード**: 成功で `0`、入力・カラム・キー不備・Ollama 不通・モデル未 pull・Phase 1 の例外・Q/A 0 件・Phase 2（Qdrant 登録）の失敗で `1`、`--provider` の不正値で `2` |

**戻り値例**:
```python
None  # 戻り値は使わない。結果はログ・ファイル・Qdrant・終了コードで確かめる
```

```python
# 使用例（Python から呼ぶ場合。通常は CLI として実行する）
import sys
from qa_qdrant.make_qa_register_qdrant import main

sys.argv = [
    "make_qa_register_qdrant.py",
    "--input-file", "output_chunked/cc_news_1per_chunks.csv",
    "--collection", "cc_news_1per",
]
main()
```

> ⚠️ **注意**: import した時点で `logging.basicConfig()`（`%(asctime)s - %(levelname)s - %(message)s`）と
> `sys.path.insert(0, <プロジェクトルート>)` が走る（§6.3）。

### 5.3 Qdrant 登録関数

#### `run_registration`

**概要**: Q/A CSV の `question` 列を Embedding して Qdrant コレクションへアップサートし、UI 用 CSV を書き出す（Phase 2）。

```python
def run_registration(
        csv_path: str,
        collection_name: str,
        recreate: bool,
        batch_size: int,
        provider: str,
        ui_output_dir: str = "qa_output"
) -> bool
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `csv_path` | str | - | Q/A CSV のパス（`question` / `answer` 列が必要） |
| `collection_name` | str | - | 登録先コレクション名。payload の `domain` にも入る |
| `recreate` | bool | - | `True` ならコレクションを作り直す（既存ポイントは消える） |
| `batch_size` | int | - | 1 回の Embedding・アップサートで扱う行数 |
| `provider` | str | - | Embedding プロバイダ名。**ログ表示にだけ使う**（Embedding は常に Gemini。CLI は `gemini` しか受け付けない・§3.3 の 3） |
| `ui_output_dir` | str | `"qa_output"` | UI 用 CSV の出力先ディレクトリ |

| 項目 | 内容 |
|------|------|
| **Input** | `csv_path`, `collection_name`, `recreate`, `batch_size`, `provider`, `ui_output_dir = "qa_output"` |
| **Process** | 1. `csv_path` が無ければ `False`<br>2. CSV を読む（失敗なら `False`）<br>3. `question` と `answer` の両列が無ければ `False`。ベクトル化の対象は **`question` 列だけ**（検索クエリとの対称性を保つため。`question + answer` を結合すると類似度が下がる）<br>4. `create_qdrant_client()` → `create_or_recreate_collection_for_qdrant(recreate=...)`（接続失敗なら `False`）<br>5. `batch_size` 行ずつ `embed_texts_for_qdrant()` → `build_points_for_qdrant(domain=collection_name, source_file=正規化名, start_index=i)` → payload の `source` を正規化名で上書き → `upsert_points_to_qdrant()`。ベクトルが空のバッチは警告を出して**飛ばす**<br>6. ループ中の例外は `False`<br>7. `question` / `answer` 列だけを `<ui_output_dir>/<正規化名>` へ書く（失敗しても警告のみで結果は変えない） |
| **Output** | `bool`: 登録まで終われば `True`。入力・接続・登録のいずれかで失敗すれば `False`（例外は投げない） |

**戻り値例**:
```python
True
```

```python
# 使用例
from qa_qdrant.make_qa_register_qdrant import run_registration

ok = run_registration(
    csv_path="qa_output/pipeline/qa_pairs_cc_news_1per_chunks_20260926_101500.csv",
    collection_name="cc_news_1per",
    recreate=False,
    batch_size=100,
    provider="gemini",
    ui_output_dir="qa_output",
)
print(ok)
# True
# （qa_output/qa_pairs_cc_news_1per_chunks.csv が作られる）
```

> ⚠️ **注意**: ベクトル生成に失敗したバッチはスキップされ、それでも `True` が返る。登録件数はログの
> `✅ 進捗: N / M 件完了` で確かめること。

### 5.4 事前確認・チャンク化関数

#### `require_ollama_ready`

**概要**: LLM を呼ぶ工程（`.txt` のチャンク化・Q/A 生成）の前に、Ollama へ接続できるか・モデルが pull 済みかを確かめる。Q/A 済み CSV を登録するだけの経路では呼ばない（Embedding は Gemini なので Ollama は要らない）。

```python
def require_ollama_ready(models: list[str], purpose: str) -> None
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `models` | list[str] | - | pull 済みか確かめるモデル名（`.txt` は `[--chunk-model, --model]`、それ以外は `[--model]`） |
| `purpose` | str | - | エラーログに出す用途（例 `"Q/A 生成"`） |

| 項目 | 内容 |
|------|------|
| **Input** | `models: list[str]`, `purpose: str` |
| **Process** | 1. `services.data_pipeline_service.ollama_unreachable_message()` を呼ぶ。メッセージが返れば（接続拒否・接続タイムアウト）ログに出して `sys.exit(1)`<br>2. `models` の各モデルについて `model_not_pulled_message(model)` を呼び、メッセージが返ればログに出して `sys.exit(1)`<br>いずれもデータ管理タブ（`backend/app/core/data_jobs.py`）・チャンク化 CLI と同じ判定。モジュール属性経由で呼ぶので、テストで差し替えられる |
| **Output** | `None`（問題が無ければ何もしない） |

#### `chunk_text_file`

**概要**: テキストファイルをセマンティックチャンク化し、チャンク CSV のパスを返す。`QAPipeline` はチャンク済み CSV しか受け付けないため、`.txt` 入力はここで先にチャンク化する。

```python
def chunk_text_file(txt_path: Path, output_dir: str, model: str) -> str
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `txt_path` | Path | - | 入力 `.txt`（UTF-8） |
| `output_dir` | str | - | チャンク CSV の出力先（`--chunk-output`） |
| `model` | str | - | チャンク化に使う LLM（`--chunk-model`） |

| 項目 | 内容 |
|------|------|
| **Input** | `txt_path`, `output_dir`, `model` |
| **Process** | 1. 本文を読み、空白だけなら `sys.exit(1)`<br>2. `generate_output_filename()` で `<output_dir>/<入力名>_chunks.csv` を決める<br>3. `run_chunking_sync(text, model, max_workers=get_default_chunking_workers(), block_size=1000, output_file, dataset_type=<入力名>, source_file=<ファイル名>)` でチャンク化<br>4. チャンクが空・CSV ができていなければ `sys.exit(1)`<br>Ollama の確認は呼び出し側（`main()`）が `require_ollama_ready()` で先に済ませる |
| **Output** | `str`: チャンク CSV のパス。チャンク化中の例外（連続失敗による中断など）はそのまま呼び出し側へ伝わる |

**戻り値例**:
```python
"output_chunked/document_chunks.csv"
```

### 5.5 ユーティリティ関数

#### `normalize_source_filename`

**概要**: ファイル名に含まれる日時サフィックス `_YYYYMMDD_HHMMSS` を取り除き、実行のたびに変わらない名前にする。

```python
def normalize_source_filename(filename: str) -> str
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `filename` | str | - | ファイル名（パスではなく basename を想定） |

| 項目 | 内容 |
|------|------|
| **Input** | `filename: str` |
| **Process** | 正規表現 `_\d{8}_\d{6}` に一致する部分を**すべて**空文字に置き換える |
| **Output** | `str`: 正規化したファイル名（一致が無ければ入力のまま） |

**戻り値例**:
```python
"qa_pairs_cc_news_1per_chunks.csv"
```

```python
# 使用例
from qa_qdrant.make_qa_register_qdrant import normalize_source_filename

print(normalize_source_filename("qa_pairs_cc_news_1per_chunks_20260926_101500.csv"))
# qa_pairs_cc_news_1per_chunks.csv
print(normalize_source_filename("qa_pairs_livedoor.csv"))
# qa_pairs_livedoor.csv
```

---

## 6. 設定・定数

### 6.1 CLI 引数

`python qa_qdrant/make_qa_register_qdrant.py --help` が正。以下は 2026-09-26 時点の実装値。

| グループ | 引数 | 既定値 | 説明 |
|---------|------|-------|------|
| 入力（どちらか 1 つ） | `--dataset` | — | 事前定義データセット名（`config.DATASET_CONFIGS` のキー） |
| | `--input-file` | — | 入力ファイル（`.csv` / `.txt`。§3.1） |
| CSV 処理 | `--text-column` | `text` | 本文列の列名。判定に使い、`QAPipeline(text_column=...)` へ渡す（この列が無く `Combined_Text` があればそちら・§3.3 の 4） |
| チャンク化（`.txt` のみ） | `--chunk-output` | `output_chunked` | チャンク CSV の出力先 |
| | `--chunk-model` | `gemma4:12b-mlx` | チャンク化に使うローカル LLM。既定値は `config.py::get_default_ollama_model()`（チャンク化 CLI と同じ） |
| Q/A 生成 | `--model` | `gemma4:12b-mlx` | `QAPipeline` に渡すローカル LLM（Ollama）。既定値は `config.py::get_default_ollama_model()`（環境変数 `OLLAMA_DEFAULT_MODEL` で上書き可） |
| | `--max-docs` | `None` | 処理する最大チャンク数 |
| | `--use-celery` | off | Celery 並列で生成する |
| | `-c`, `--concurrency` | `8` | 並列タスク数。`start_celery.sh -c` と同じ値を推奨 |
| | `--celery-workers` | `1` | **非推奨**。ワーカー数チェック用（後方互換のため残っている） |
| | `--batch-chunks` | `3` | 1 回の LLM 呼び出しで処理するチャンク数 |
| Qdrant 登録 | `--collection` | —（**必須**） | 登録先コレクション名 |
| | `--recreate` | off | コレクションを作り直す |
| | `--batch-size` | `100` | Embedding・アップサートのバッチサイズ |
| | `--provider` | `gemini` | Embedding プロバイダ。**`gemini` のみ**（`choices`。他の値は終了コード 2・§3.3 の 3） |
| 出力 | `--output` | `qa_output/pipeline` | Q/A CSV / JSON の出力先 |
| | `--ui-output` | `qa_output` | UI 用 CSV の出力先 |

> カバレッジ分析（`analyze_coverage`）は常に `True` で `QAPipeline.run()` に渡され、CLI からは切り替えられない。

### 6.2 環境変数

| 変数 | 起動時の検査 | 用途 |
|------|:-----------:|------|
| `GOOGLE_API_KEY` | ✅（無ければ終了コード 1） | Embedding（Gemini `gemini-embedding-001`） |
| `OLLAMA_BASE_URL` | Q/A を生成するときだけ ✅（接続できなければ終了コード 1） | Ollama の接続先（既定 `http://localhost:11434/v1`） |
| `OLLAMA_DEFAULT_MODEL` | — | `--model` / `--chunk-model` の既定値を変える（`config.py::get_default_ollama_model()`） |
| `OLLAMA_NUM_PARALLEL` | — | `.txt` のチャンク化の並列数（`config.py::get_default_chunking_workers()`。無ければ 1） |

> API キーは LLM には不要（ローカル実行）。`ANTHROPIC_API_KEY` は使わない。

### 6.3 モジュール定数・副作用

| 項目 | 値 |
|------|----|
| ログ設定 | import 時に `logging.basicConfig(level=INFO, format='%(asctime)s - %(levelname)s - %(message)s')` |
| `sys.path` | import 時に先頭へプロジェクトルートを挿入 |
| 日時サフィックスの正規表現 | `_\d{8}_\d{6}`（`normalize_source_filename()`） |
| `CHUNK_DEFAULT_OUTPUT_DIR` | `output_chunked`（`--chunk-output` の既定） |
| `CHUNK_BLOCK_SIZE` | `1000`（`.txt` のチャンク化のブロック文字数。CLI からは変えられない） |

---

## 7. エクスポート

`__all__` の定義はありません。外部から使える要素は次の 5 つです（`__main__` 実行時は `main()` を呼ぶ）。

```python
main                        # CLI エントリーポイント
require_ollama_ready        # LLM を呼ぶ工程の前の Ollama 確認
chunk_text_file             # .txt のチャンク化
run_registration            # Phase 2（Embedding → Qdrant 登録 → UI 用 CSV）
normalize_source_filename   # 日時サフィックスの除去
```

> 📌 本モジュールを直接 import しているのはテストだけ（2026-09-26 grep。ほかはコメント・docstring での言及）。

| テスト | 件数 | 対象 |
|---|---:|---|
| `backend/tests/test_make_qa_register_qdrant_csv.py` | 2 | `run_registration()` の CSV 入力 |
| `backend/tests/test_make_qa_register_qdrant_csv_fixed.py` | 1 | 同上 |
| `backend/tests/test_make_qa_register_qdrant_exit_code.py` | 3 | 登録の成否・Q/A 0 件と終了コード（§3.3 の 2・6） |
| `backend/tests/test_make_qa_register_qdrant_txt_input.py` | 3 | `.txt` のチャンク化とその失敗系（§3.3 の 1・5） |
| `backend/tests/test_make_qa_register_qdrant_startup_checks.py` | 5 | Ollama の事前確認と `--provider` の拒否（§3.3 の 3・5） |
| `backend/tests/test_qa_pipeline_text_column.py` | 6 | `--text-column` が `QAPipeline` へ渡ること（§3.3 の 4） |

---

## 8. 変更履歴

| バージョン | 変更内容 |
|-----------|---------|
| 1.0 | 初版作成（2026-09-26）。`qa_qdrant/docs/README.md` の残タスク 5（本モジュールの IPO 文書が無い）を解消。姉妹リポジトリ grace_v2 の同名文書を写さず、本リポジトリの実装（609 行）を読んで書き起こした。ダミーの `GOOGLE_API_KEY`・Qdrant 停止・Ollama 停止の状態で CLI を実行し、`.txt` 入力が必ず失敗すること・Qdrant 登録失敗でも終了コード 0・`--provider openai` が通ること・`--text-column` が生成に渡らないこと・Ollama 停止で Q/A 0 件でも終了コード 0 になることを確かめ、§3.3 に 6 件として記録した（コードは未変更） |
| 1.1 | §3.3 の 6 件の修正に追随（2026-09-26）。1〜4 は grace_v2 の修正を移植し、grace_v2 の `ANTHROPIC_API_KEY` の事前確認の代わりに Ollama の事前確認（`require_ollama_ready()`・5）と Q/A 0 件での停止（6）を入れた。`.txt` のチャンク化の並列数は grace_v2 の固定 8 ではなくデータ管理タブと同じ `get_default_chunking_workers()`。概要・責務表・構成図 3 枚・§3.1 の判定表と図・§3.2・§3.3・§4.2・§5.1・§5.2・§5.3・§5.4（`require_ollama_ready` / `chunk_text_file` の IPO を新設。旧 §5.4 は §5.5 へ）・§6・§7・付録を更新 |
| 1.2 | §3.2 の「`--dataset` のとき種別が `unknown`」の修正に追随（2026-09-26）。`QAPipeline._load_config()` がデータセット名で補うようになった。出力名だけでなく、チャンク ID と途中経過ファイルがデータセット間で共有されていたことも記録 |

---

## 付録: 依存関係図

```mermaid
flowchart LR
    MQR["make_qa_register_qdrant.py"]

    subgraph STD["標準ライブラリ・pandas"]
        ARGP["argparse"]
        PD["pandas"]
        RE["re"]
    end

    subgraph QAGEN["qa_generation"]
        PIPE["QAPipeline"]
        SMART["SmartQAGenerator"]
    end

    subgraph SVC["services.qdrant_service"]
        COLL["create_or_recreate_collection_for_qdrant"]
        EMB["embed_texts_for_qdrant"]
        PTS["build_points_for_qdrant"]
        UPS["upsert_points_to_qdrant"]
    end

    subgraph DPS["services.data_pipeline_service"]
        UNR["ollama_unreachable_message"]
        NPL["model_not_pulled_message"]
        RCS["run_chunking_sync"]
    end

    subgraph OTHER["その他"]
        CONF["config.DATASET_CONFIGS / get_default_ollama_model / get_default_chunking_workers"]
        QCW["qdrant_client_wrapper.create_qdrant_client"]
        GOF["chunking.generate_output_filename"]
    end

    MQR --> ARGP
    MQR --> PD
    MQR --> RE
    MQR --> PIPE
    PIPE --> SMART
    MQR --> COLL
    MQR --> EMB
    MQR --> PTS
    MQR --> UPS
    MQR --> UNR
    MQR --> NPL
    MQR --> RCS
    MQR --> CONF
    MQR --> QCW
    MQR --> GOF
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class MQR,ARGP,PD,RE,PIPE,SMART,COLL,EMB,PTS,UPS,UNR,NPL,RCS,CONF,QCW,GOF default
style STD fill:#1a1a1a,stroke:#fff,color:#fff
style QAGEN fill:#1a1a1a,stroke:#fff,color:#fff
style SVC fill:#1a1a1a,stroke:#fff,color:#fff
style DPS fill:#1a1a1a,stroke:#fff,color:#fff
style OTHER fill:#1a1a1a,stroke:#fff,color:#fff
```

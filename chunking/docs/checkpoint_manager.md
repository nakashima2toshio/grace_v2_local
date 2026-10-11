# checkpoint_manager.py - チャンク化の途中結果（チェックポイント）管理 ドキュメント

**Version 1.1** | 最終更新: 2026-10-11

---

## 目次

1. [概要](#概要)
2. [アーキテクチャ構成図](#1-アーキテクチャ構成図)
3. [モジュール構成図](#2-モジュール構成図)
4. [クラス・関数一覧表](#3-クラス関数一覧表)
5. [クラス・関数 IPO詳細](#4-クラス関数-ipo詳細)
6. [設定・定数](#5-設定定数)
7. [エクスポート](#6-エクスポート)
8. [変更履歴](#7-変更履歴)
9. [付録: 依存関係図](#付録-依存関係図)

---

## 概要

`chunking/checkpoint_manager.py` は、チャンク化の 3 段階（Step1 段落分割・Step2 意味的分割・Step3 連続性チェック）の
**各段の結果を JSON に保存し、途中で落ちたときに続きから再開する**ための `CheckpointManager` クラスを持つ。
保存先は `<checkpoint_dir>/<job_id>/step1.json`〜`step3.json`（既定 `./checkpoints/<YYYYMMDD_HHMMSS>/`）。
同じディレクトリの `job.json` にはジョブの入力と設定を残し、**別の入力・設定で再開しようとしたら `ResumeMismatchError` で止める**（2026-10-11〜）。

> 📌 呼び出し元は 2 つ: チャンク化 CLI（`csv_text_to_chunks_text_csv.py::main()` の `--resume <JOB_ID>`）と、
> データ管理タブ・`.txt` 入力の Q/A 登録（`services/data_pipeline_service.py::run_chunking_sync()`。`job_id` を渡せば再開）。
> どちらも **`./checkpoints` はカレントディレクトリ基準**で、実行のたびに新しいジョブのディレクトリが増える（自動では消えない）。

### 主な責務

- ジョブごとのチェックポイントディレクトリの用意
- 段の結果（文字列のリスト）とメタデータの原子的な保存と読み込み
- 最後に終わった段から、再開すべき段とその入力を決める
- 保存済みジョブの一覧・情報の取得と、古いジョブ・このジョブの削除
- 再開するジョブの入力・設定の照合（`job.json`）

### 各責務対応のモジュール

| # | 責務 | 対応モジュール | 説明 |
|---|------|--------------|------|
| 1 | ディレクトリの用意 | `checkpoint_manager.py` | `CheckpointManager.__init__()` が `<checkpoint_dir>/<job_id>/` を作る（`job_id` 省略時は `_new_job_dir()` が現在時刻で作る。同じ秒の 2 つ目以降は `_1`, `_2` …） |
| 2 | 保存と読み込み | `checkpoint_manager.py` | `save()`（一時ファイル → `os.replace`）/ `load()` / `load_with_metadata()` / `exists()` |
| 3 | 再開点の決定 | `checkpoint_manager.py` | `get_latest_completed_step()` / `get_resume_point()` |
| 4 | 一覧と削除 | `checkpoint_manager.py` | `list_jobs()` / `get_job_info()` / `cleanup_old_jobs()` / `clear()` |
| 5 | 入力・設定の照合 | `checkpoint_manager.py` | `verify_or_record_job()` / `load_job_meta()` / `save_job_meta()` / `ResumeMismatchError`（照合する値は `csv_text_to_chunks_text_csv.py::_job_meta()` が作る） |

### 主要機能一覧

| 機能 | 説明 |
|------|------|
| `CheckpointManager(checkpoint_dir="./checkpoints", job_id=None)` | ジョブのディレクトリを作る |
| `save(step_name, data, metadata=None)` | `<step_name>.json` に `{"step", "timestamp", "count", "data", "metadata"}` を書く |
| `load(step_name)` | `data` を返す（無い・壊れていれば `None`） |
| `get_resume_point()` | `(再開する段, 前段のデータ)`。全段済みなら `(None, step3 のデータ)` |
| `list_jobs(checkpoint_dir)` / `cleanup_old_jobs(checkpoint_dir, keep_count)` | ジョブの一覧と、古いジョブの削除（クラスメソッド） |
| `verify_or_record_job(meta)` | `job.json` と照合し、違えば `ResumeMismatchError`。無ければ記録する |

---

## 1. アーキテクチャ構成図

### 1.1 システム全体構成

```mermaid
flowchart TB
    subgraph CALLER["呼び出し側"]
        CLI["csv_text_to_chunks_text_csv.py<br>main()（--resume）"]
        PIPE["chunks_all_async() と _step1〜_step3"]
        DPS["services/data_pipeline_service.py<br>run_chunking_sync(job_id)"]
    end
    subgraph MODULE["checkpoint_manager.py"]
        CM["CheckpointManager"]
    end
    subgraph EXT["外部"]
        FS["ファイルシステム<br>./checkpoints/JOB_ID/step1〜3.json"]
    end
    CLI --> CM
    DPS --> CM
    PIPE -->|"exists / load / save"| CM
    CM --> FS
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class CLI,PIPE,DPS,CM,FS default
style CALLER fill:#1a1a1a,stroke:#fff,color:#fff
style MODULE fill:#1a1a1a,stroke:#fff,color:#fff
style EXT fill:#1a1a1a,stroke:#fff,color:#fff
```

### 1.2 データフロー

1. 呼び出し側が `CheckpointManager()`（新規）か `CheckpointManager(job_id=...)`（再開）を作る。ディレクトリはこの時点で作られる
2. 各段の関数は、最初に `exists("stepN")` を見て、あれば `load("stepN")` を返して LLM を呼ばない
3. 無ければ LLM で処理し、終わったら `save("stepN", 結果)` する（`*.tmp` に書いてから `os.replace` で置き換える）
4. 3 段とも済んだジョブを同じ `job_id` で流し直すと、LLM を 1 回も呼ばずに `step3.json` の内容が出力になる

---

## 2. モジュール構成図

### 2.1 内部モジュール構成

```mermaid
flowchart LR
    subgraph INST["インスタンスメソッド"]
        INIT["__init__"]
        SAVE["save"]
        LOAD["load / load_with_metadata / exists"]
        RES["get_latest_completed_step / get_resume_point"]
        INFO["get_job_info / clear"]
        META["verify_or_record_job<br>load_job_meta / save_job_meta"]
    end
    subgraph CLS["クラスメソッド"]
        LIST["list_jobs"]
        CLEAN["cleanup_old_jobs"]
    end
    RES --> LOAD
    INFO --> LOAD
    LIST --> INIT
    LIST --> INFO
    META --> RES
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class INIT,SAVE,LOAD,RES,INFO,LIST,CLEAN,META default
style INST fill:#1a1a1a,stroke:#fff,color:#fff
style CLS fill:#1a1a1a,stroke:#fff,color:#fff
```

### 2.2 外部依存関係

| ライブラリ | 用途 |
|-----------|------|
| `json` / `os` / `shutil` / `datetime` / `logging`（標準） | JSON の読み書き・原子的な置き換え・ディレクトリ削除・ジョブ ID の生成 |

---

## 3. クラス・関数一覧表

### 3.1 クラス一覧

| クラス | 概要 |
|--------|------|
| `CheckpointManager` | 1 ジョブのチェックポイントを扱う。クラスメソッドで全ジョブの一覧・掃除もする |
| `ResumeMismatchError(ValueError)` | 再開しようとしたジョブと、今回の入力・設定が食い違う |

### 3.2 メソッド一覧

| メソッド | 種別 | 概要 |
|---------|------|------|
| `__init__(checkpoint_dir="./checkpoints", job_id=None)` | インスタンス | `<checkpoint_dir>/<job_id>/` を作る |
| `_new_job_dir(checkpoint_dir)` | 静的 | 新規ジョブの ID とディレクトリを作る（重なれば `_1`, `_2` …） |
| `save(step_name, data, metadata=None)` | インスタンス | 段の結果を原子的に保存し、パスを返す |
| `load(step_name)` | インスタンス | `data` を返す。無い・壊れていれば `None` |
| `load_with_metadata(step_name)` | インスタンス | JSON 全体を返す。無い・壊れていれば `None` |
| `exists(step_name)` | インスタンス | `<step_name>.json` があるか |
| `get_latest_completed_step()` | インスタンス | `step3` → `step2` → `step1` の順に探し、最初に見つかった段 |
| `get_resume_point()` | インスタンス | 再開する段と、その入力（前段のデータ） |
| `clear()` | インスタンス | このジョブのディレクトリを消す |
| `get_job_info()` | インスタンス | `job_id` / `job_dir` / 段ごとの `timestamp`・`count`・`metadata` |
| `list_jobs(checkpoint_dir="./checkpoints")` | クラス | 全ジョブの `get_job_info()`（ディレクトリ名の降順） |
| `cleanup_old_jobs(checkpoint_dir="./checkpoints", keep_count=10)` | クラス | ディレクトリ名の降順で `keep_count` 個を残し、ほかを消す |
| `load_job_meta()` | インスタンス | `job.json` を読む。無ければ `None` |
| `save_job_meta(meta)` | インスタンス | `job.json` に書き、パスを返す |
| `verify_or_record_job(meta)` | インスタンス | `job.json` と照合する（無ければ記録する） |

モジュール関数 `_short(value)` は、エラー文に出す値を短くする（`repr` が 40 文字を超えたら先頭 12 文字だけ）。

---

## 4. クラス・関数 IPO詳細

### 4.1 使用例

| # | パターン | いつ使うか | 例 |
|---|---------|-----------|----|
| 1 | **基本**: 段を保存し、再開点を求める | 3 段階パイプラインと同じ使い方を確かめる | 4.1.1 |
| 2 | CLI で中断したジョブを再開する（`--resume`） | 長いチャンク化が途中で落ちた | 4.1.2 |
| 3 | ジョブの一覧と掃除 | `./checkpoints/` が溜まった | 4.1.3 |
| 4 | 再開する前に入力・設定を照合する | 自作のパイプラインで再開を扱う（`chunks_all_async()` は自分で呼ぶ） | 4.1.4 |

> 📝 **確かめ方（2026-10-11）**: 4.1.1・4.1.3・4.1.4 は空の一時ディレクトリで実行した。4.1.2 は Ollama の代わりに偽サーバ
> （段落分割は「。」で文に割るだけ・連続性はつながらない）を立てて CLI を 2 回実行した。

#### 4.1.1 基本: 段を保存し、再開点を求める

```python
from chunking.checkpoint_manager import CheckpointManager

m = CheckpointManager(checkpoint_dir="ckpt", job_id="demo_job")   # ckpt/demo_job/ ができる
print(m.get_resume_point())                       # まだ何も無い

m.save("step1", ["段落1", "段落2"], metadata={"block_size": 1000})
print(m.get_resume_point())                       # Step2 から、Step1 の結果を入力に

m.save("step2", ["チャンク1", "チャンク2", "チャンク3"])
print(m.get_latest_completed_step(), m.load("step2"))
print(m.load_with_metadata("step1")["metadata"])

# 実行結果:
# ('step1', None)
# ('step2', ['段落1', '段落2'])
# step2 ['チャンク1', 'チャンク2', 'チャンク3']
# {'block_size': 1000}
```

> 📌 3 段階パイプライン（`_step1_hierarchical_split` ほか）は `get_resume_point()` を使わず、各段の頭で `exists()` / `load()` を見る。
> どちらでも結果は同じ（済んだ段は LLM を呼ばずに読み戻す）。

#### 4.1.2 CLI で中断したジョブを再開する

```bash
# 1 回目（途中で落ちたとする）。JOB_ID は ./checkpoints/ 配下のディレクトリ名（実行時刻）
python -m chunking.csv_text_to_chunks_text_csv --input-file data/document.txt --output output_chunked
JOB_ID=$(ls -t checkpoints | head -1)

# 2 回目: 同じ入力で --resume を付けると、済んだ段は LLM を呼ばずに読み戻す
python -m chunking.csv_text_to_chunks_text_csv --input-file data/document.txt --output output_chunked --resume "$JOB_ID" 2>&1 | grep "チェックポイントから再開"

# 出力例:
# 2026-10-11 07:45:22 [INFO] __main__: Step1: チェックポイントから再開
# 2026-10-11 07:45:22 [INFO] __main__: Step2: チェックポイントから再開
# 2026-10-11 07:45:22 [INFO] __main__: Step3: チェックポイントから再開
```

> ⚠️ **別の入力・設定では再開できない。** `--resume <前のジョブ>` に別の入力ファイル（または別の `--block-size` / `--model` /
> `--text-column` / `--max-rows`）を渡すと、LLM を呼ぶ前に終了コード 1 で止まる（4.1.4・[`csv_text_to_chunks_text_csv.md`](csv_text_to_chunks_text_csv.md) §4.1.5）。
> 2026-10-11 の修正前は、前のジョブのチャンクが新しい入力の名前の CSV に書かれていた（同 §5.3 の 2）。

#### 4.1.3 ジョブの一覧と掃除

```python
import os
from chunking.checkpoint_manager import CheckpointManager

for jid in ["20261009_090000", "20261010_090000", "20261011_090000"]:
    CheckpointManager("ckpt2", jid).save("step1", ["x"])

print([j["job_id"] for j in CheckpointManager.list_jobs("ckpt2")])
CheckpointManager.cleanup_old_jobs("ckpt2", keep_count=2)
print(sorted(os.listdir("ckpt2")))

# 実行結果:
# ['20261011_090000', '20261010_090000', '20261009_090000']
# ['20261010_090000', '20261011_090000']
```

> ⚠️ `list_jobs()` / `cleanup_old_jobs()` の「新しい順」は**ディレクトリ名の降順**で、更新日時ではない。
> 既定の `job_id`（`YYYYMMDD_HHMMSS`）なら時刻順になるが、`job_id` に任意の名前を付けると順番が崩れ、新しいジョブが消えることがある。
> 本リポジトリに `cleanup_old_jobs()` を呼ぶコードは無い（2026-10-11 grep）ので、`./checkpoints/` は溜まり続ける。

#### 4.1.4 再開する前に入力・設定を照合する

```python
from chunking.checkpoint_manager import CheckpointManager, ResumeMismatchError

meta = {"source_file": "a.txt", "text_chars": 12, "text_sha256": "726e2a23b922da44f532c27789f7dc333cd2c195fe4d1660c0142018fef561a9",
        "block_size": 1000, "model": "gemma4:26b-a4b-it-qat"}

m = CheckpointManager("ckpt5", "job")
m.verify_or_record_job(meta)            # 新規 → job.json に記録
print(m.load_job_meta() == meta)
m.verify_or_record_job(meta)            # 同じ値 → 何も起きない

try:
    m.verify_or_record_job({**meta, "source_file": "b.txt", "block_size": 500})
except ResumeMismatchError as e:
    print(type(e).__name__, isinstance(e, ValueError))
    print(e)

# 実行結果:
# True
# ResumeMismatchError True
# ジョブ job は別の入力・設定で作られています（source_file: 記録='a.txt' 今回='b.txt'; block_size: 記録=1000 今回=500）。--resume を外して新規に実行するか、同じ入力・設定で再開してください（チェックポイント: ckpt5/job）。
```

> 📌 `chunks_all_async()` はこれを先頭で呼ぶ（値は `_job_meta()` が作る）ので、CLI とデータ管理タブの再開は自分で呼ばなくてよい。
> `job.json` の無い古いジョブ（2026-10-11 より前に作ったもの）は照合できないので、警告を出して今回の値を記録する:
>
> ```python
> m = CheckpointManager("ckpt6", "old_job")
> m.save("step1", ["段落1"])                      # job.json の無い古いジョブ
> m.verify_or_record_job({"source_file": "a.txt", "block_size": 1000})
> # WARNING ジョブ old_job には入力・設定の記録（job.json）が無いため、別の入力での再開かどうかを確かめられません。今回の値を記録して続けます。
> ```

### 4.2 `CheckpointManager.__init__`

```python
def __init__(self, checkpoint_dir: str = "./checkpoints", job_id: Optional[str] = None)
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `checkpoint_dir` | str | `"./checkpoints"` | 保存先の親ディレクトリ（カレントディレクトリ基準） |
| `job_id` | Optional[str] | `None` | ジョブ ID。`None` なら `_new_job_dir()` が現在時刻（`%Y%m%d_%H%M%S`）で新しく作る。同じ ID のディレクトリが既にあれば `_1`, `_2` … を付ける |

| 項目 | 内容 |
|------|------|
| **Input** | `checkpoint_dir`, `job_id` |
| **Process** | `job_id` あり: `job_dir = <checkpoint_dir>/<job_id>` を `os.makedirs(exist_ok=True)` で作る（再開。既存のディレクトリをそのまま使う）。<br>`job_id` なし: `_new_job_dir()` が `os.makedirs()`（`exist_ok=False`）で、まだ無い ID を取るまで `_1`, `_2` … と試す。<br>どちらも INFO ログを出す |
| **Output** | インスタンス（属性 `checkpoint_dir` / `job_id` / `job_dir`）。**作った時点でディレクトリができる** |

> 📌 2026-10-11 までは同じ秒に作った新規ジョブが同じディレクトリを共有し、あとの実行が前の実行の途中結果（別の入力のチャンク）を
> 読み戻していた。入力の照合（§4.7）を入れたことでこれが `ResumeMismatchError` として表に出たため、新規の ID が重ならないようにした
> （`tests/test_chunking_fallback_and_resume.py`）。

### 4.3 `save` / `load` / `load_with_metadata` / `exists`

```python
def save(self, step_name: str, data: List[str], metadata: Optional[dict] = None) -> str
def load(self, step_name: str) -> Optional[List[str]]
def load_with_metadata(self, step_name: str) -> Optional[dict]
def exists(self, step_name: str) -> bool
```

| 項目 | 内容 |
|------|------|
| **Input** | `step_name`（`"step1"` / `"step2"` / `"step3"`。ほかの名前も保存はできる）、`data`（文字列のリスト）、`metadata` |
| **Process** | `save`: `{"step", "timestamp"(ISO 8601), "count", "data", "metadata"}` を `<step>.json.tmp` に書き、`os.replace` で `<step>.json` へ置き換える（書き込み失敗時は `.tmp` を消して例外を上げ直す）。`load`: JSON を読み `data` を返す。`load_with_metadata`: JSON 全体を返す。`exists`: ファイルの有無 |
| **Output** | `save`: 保存したパス。`load` / `load_with_metadata`: 無い・JSON が壊れていれば `None`（エラーログを出す）。`exists`: bool |

```python
# 使用例
from chunking.checkpoint_manager import CheckpointManager

m = CheckpointManager("ckpt3", "job")
path = m.save("step3", ["最終チャンク"], metadata={"model": "gemma4:26b-a4b-it-qat"})
print(path, m.exists("step3"), m.load("step3"), m.load("step9"))
# ckpt3/job/step3.json True ['最終チャンク'] None
```

### 4.4 `get_latest_completed_step` / `get_resume_point`

| 項目 | 内容 |
|------|------|
| **Input** | なし |
| **Process** | `step3` → `step2` → `step1` の順に `exists()` を見る |
| **Output** | `get_latest_completed_step`: 最初に見つかった段名か `None`。`get_resume_point`: `step3` 済み → `(None, step3 のデータ)`、`step2` 済み → `("step3", step2 のデータ)`、`step1` 済み → `("step2", step1 のデータ)`、何も無い → `("step1", None)` |

使用例は 4.1.1。

### 4.5 `clear` / `get_job_info`

| 項目 | 内容 |
|------|------|
| **Input** | なし |
| **Process** | `clear`: `job_dir` を `shutil.rmtree` で消す。`get_job_info`: 3 段それぞれの `load_with_metadata()` から `timestamp` / `count` / `metadata` を集める |
| **Output** | `clear`: `None`。`get_job_info`: `{"job_id", "job_dir", "steps": {"step1": {...}, ...}}`（無い段は含まない） |

```python
# 使用例
from chunking.checkpoint_manager import CheckpointManager

m = CheckpointManager("ckpt4", "job")
m.save("step1", ["a", "b"])
print(m.get_job_info()["steps"]["step1"]["count"])
m.clear()
print(m.load("step1"))
# 2
# None
```

### 4.6 `list_jobs` / `cleanup_old_jobs`（クラスメソッド）

| 項目 | 内容 |
|------|------|
| **Input** | `checkpoint_dir`（既定 `"./checkpoints"`）、`keep_count`（既定 10） |
| **Process** | `list_jobs`: ディレクトリ名の降順に、各ジョブの `CheckpointManager` を作って `get_job_info()` を集める。`cleanup_old_jobs`: ディレクトリ名の降順で `keep_count` 番目以降を `shutil.rmtree` |
| **Output** | `list_jobs`: ジョブ情報のリスト（親ディレクトリが無ければ `[]`）。`cleanup_old_jobs`: `None` |

使用例は 4.1.3。

### 4.7 `verify_or_record_job` / `load_job_meta` / `save_job_meta`

```python
def verify_or_record_job(self, meta: dict) -> None
def load_job_meta(self) -> Optional[dict]
def save_job_meta(self, meta: dict) -> str
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `meta` | dict | - | 今回の入力と設定。チャンク化では `{"source_file", "text_chars", "text_sha256", "block_size", "model"}` |

| 項目 | 内容 |
|------|------|
| **Input** | `meta` |
| **Process** | `verify_or_record_job`: 1. `load_job_meta()` で `job.json` を読む<br>2. 無い: ステップのチェックポイントがあれば（古いジョブ）照合できない旨を警告する。どちらでも `save_job_meta(meta)` で記録して戻る<br>3. ある: `meta` のキーごとに記録と比べ、違うキーがあれば `ResumeMismatchError`（メッセージに `キー: 記録=… 今回=…` を並べる） |
| **Output** | `verify_or_record_job`: `None`（食い違えば `ResumeMismatchError`）。`load_job_meta`: `dict` か `None`。`save_job_meta`: 書いたパス |

使用例は 4.1.4。

> 📌 比べるのは `meta` にあるキーだけ。記録にしかないキーは無視するので、あとから照合する項目を減らしても古いジョブを再開できる。

---

## 5. 設定・定数

| 項目 | 値 |
|------|----|
| 既定の保存先 | `./checkpoints`（カレントディレクトリ基準。CLI・データ管理タブとも既定のまま使う） |
| ジョブ ID の既定 | 現在時刻 `YYYYMMDD_HHMMSS`。同じ秒の 2 つ目以降は `YYYYMMDD_HHMMSS_1`, `_2` …（名前の降順でも新しい方が前に来る） |
| ファイル名 | `<step_name>.json`（書き込み中は `<step_name>.json.tmp`） |
| `JOB_META_FILE` | `"job.json"`（ジョブの入力と設定） |

---

## 6. エクスポート

`__all__` は無い。`chunking/__init__.py` が `CheckpointManager` を再エクスポートする（`from chunking import CheckpointManager`）。
`ResumeMismatchError` は再エクスポートしないので、`chunking.checkpoint_manager` から import する。

```python
CheckpointManager
ResumeMismatchError
JOB_META_FILE
```

> 📌 照合は `tests/test_chunking_fallback_and_resume.py`（`chunks_all_async()` 経由・CLI の終了コード）と
> `tests/test_data_jobs.py::test_chunking_resume_with_other_input_is_error`（データ管理タブ）が検査する。
> 保存・読み込み・掃除そのものの直接のテストは無い（本書 4.1 で実行して確かめている）。

---

## 7. 変更履歴

| バージョン | 日付 | 変更内容 |
|---|---|---|
| 1.0 | 2026-10-11 | 新規作成（`chunking/docs/` の構成整理。それまで専用の文書が無かった）。§4.1 の使用例 3 本と §4.3・§4.5 の例を実行して確かめ、`--resume` が入力ファイルとの対応を確かめないこと・掃除の順番がディレクトリ名順であることを記録した |
| 1.1 | 2026-10-11 | 別の入力・設定での再開を止める照合（`job.json`・`verify_or_record_job()` / `load_job_meta()` / `save_job_meta()`・`ResumeMismatchError`）を足したのに合わせて改訂。主な責務に 5 行目を足し、§4.1.4（実行して確かめた）と §4.7 を新設、§4.1.2 の「対応を確かめない」の注記を差し替えた。照合で表に出た「同じ秒に作った新規ジョブが同じディレクトリを共有する」問題も直し（`_new_job_dir()`。重なれば `_1`, `_2` …）、§4.2・§5 を改めた |

---

## 付録: 依存関係図

```mermaid
flowchart LR
    CM["checkpoint_manager.py"]
    subgraph STD["標準ライブラリ"]
        JSON["json"]
        OS["os / shutil"]
        DT["datetime"]
    end
    CM --> JSON
    CM --> OS
    CM --> DT
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class CM,JSON,OS,DT default
style STD fill:#1a1a1a,stroke:#fff,color:#fff
```

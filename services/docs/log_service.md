# log_service.py - ログ管理サービス ドキュメント

**Version 1.0** | 最終更新: 2026-06-17

---

## 目次

1. [概要](#概要)
2. [アーキテクチャ構成図](#1-アーキテクチャ構成図)
3. [モジュール構成図](#2-モジュール構成図)
4. [クラス・関数一覧表](#3-クラス関数一覧表)
5. [クラス・関数 IPO詳細](#4-クラス関数-ipo詳細)
6. [設定・定数](#5-設定定数)
7. [使用例](#6-使用例)
8. [エクスポート](#7-エクスポート)
9. [変更履歴](#8-変更履歴)
10. [付録: 依存関係図](#付録-依存関係図)

---

## 概要

`log_service.py`は、Agent（GRACE自律エージェント）が回答できなかった質問（未回答質問）をCSVファイルに記録・読み込み・クリアするためのログ管理サービスです。RAG検索でヒットがない場合やスコアが低い場合などの「未回答」イベントを蓄積し、後からの分析や改善に活用します。

### 主な責務

- ログディレクトリ・ログファイルの初期化（存在しない場合の自動生成）
- 未回答質問のCSVへの追記記録
- 未回答質問ログの読み込み（pandas DataFrameとして提供）
- 未回答質問ログのクリア（ファイル再作成）
- 例外発生時のロギングによる安全な失敗（エラーを伝播させない）

### 各責務対応のモジュール

| # | 責務 | 対応モジュール | 説明 |
|---|------|--------------|------|
| 1 | ログディレクトリ・ファイルの初期化 | `log_service.py` | `_ensure_log_dir()`がディレクトリ作成とヘッダー書き込みを担当 |
| 2 | 未回答質問のCSV追記記録 | `log_service.py` | `log_unanswered_question()`が1行を追記 |
| 3 | 未回答質問ログの読み込み | `log_service.py` | `load_unanswered_logs()`がDataFrameとして返却 |
| 4 | 未回答質問ログのクリア | `log_service.py` | `clear_unanswered_logs()`がファイルを再作成 |
| 5 | 例外発生時のロギング | `log_service.py` | 各関数が`try/except`で`logger.error()`記録 |

### 主要機能一覧

| 機能 | 説明 |
|------|------|
| `log_unanswered_question()` | 回答できなかった質問をCSVに追記記録する |
| `load_unanswered_logs()` | 未回答質問ログを読み込み、新しい順にソートしたDataFrameを返す |
| `clear_unanswered_logs()` | 未回答ログをクリア（ヘッダーのみのファイルに再作成） |
| `_ensure_log_dir()` | ログディレクトリとCSVファイルを初期化する（内部関数） |
| `LOG_DIR` | ログ保存先ディレクトリ定数 |
| `UNANSWERED_LOG_FILE` | 未回答質問ログCSVファイルパス定数 |

---

## 1. アーキテクチャ構成図

### 1.1 システム全体構成

```mermaid
flowchart TB
    subgraph CLIENT["クライアント層"]
        AGENT["GRACE Executor Agent"]
        UI["Streamlit UI"]
    end

    subgraph MODULE["log_service.py"]
        LOG["log_unanswered_question()"]
        LOAD["load_unanswered_logs()"]
        CLEAR["clear_unanswered_logs()"]
    end

    subgraph EXTERNAL["外部サービス層"]
        FS["ファイルシステム (logs/)"]
        PANDAS["pandas DataFrame"]
    end

    AGENT --> LOG
    UI --> LOAD
    UI --> CLEAR
    LOG --> FS
    CLEAR --> FS
    LOAD --> FS
    LOAD --> PANDAS
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class AGENT,UI,LOG,LOAD,CLEAR,FS,PANDAS default
style CLIENT fill:#1a1a1a,stroke:#fff,color:#fff
style MODULE fill:#1a1a1a,stroke:#fff,color:#fff
style EXTERNAL fill:#1a1a1a,stroke:#fff,color:#fff
```

### 1.2 データフロー

1. Agentが回答に失敗した際、`log_unanswered_question()`を呼び出す
2. `_ensure_log_dir()`でログディレクトリ・ファイルを初期化（未作成時）
3. タイムスタンプ・質問・コレクション・理由・応答をCSVに1行追記
4. UI層は`load_unanswered_logs()`でログをDataFrameとして取得し表示
5. 必要に応じて`clear_unanswered_logs()`でログをリセット

---

## 2. モジュール構成図

### 2.1 内部モジュール構成

```mermaid
flowchart TB
    subgraph CONST["定数・設定"]
        LOGDIR["LOG_DIR"]
        LOGFILE["UNANSWERED_LOG_FILE"]
        LOGGER["logger"]
    end

    subgraph INTERNAL["内部関数"]
        ENSURE["_ensure_log_dir()"]
    end

    subgraph PUBLIC["公開関数"]
        LOG["log_unanswered_question()"]
        LOAD["load_unanswered_logs()"]
        CLEAR["clear_unanswered_logs()"]
    end

    CONST --> ENSURE
    CONST --> LOG
    CONST --> LOAD
    CONST --> CLEAR
    LOG --> ENSURE
    LOAD --> ENSURE
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class LOGDIR,LOGFILE,LOGGER,ENSURE,LOG,LOAD,CLEAR default
style CONST fill:#1a1a1a,stroke:#fff,color:#fff
style INTERNAL fill:#1a1a1a,stroke:#fff,color:#fff
style PUBLIC fill:#1a1a1a,stroke:#fff,color:#fff
```

### 2.2 外部依存関係

| ライブラリ | バージョン | 用途 |
|-----------|-----------|------|
| `pandas` | - | ログCSVの読み込みとDataFrame化・ソート |
| `csv` | 標準 | CSVの書き込み（ヘッダー・追記） |
| `logging` | 標準 | 記録・エラーのロギング |
| `datetime` | 標準 | タイムスタンプ生成 |
| `pathlib` | 標準 | ファイルパス操作・ディレクトリ作成 |

### 2.3 内部依存モジュール

| モジュール | 用途 |
|-----------|------|
| なし | 本モジュールは内部モジュールに依存しない（自己完結） |

---

## 3. クラス・関数一覧表

### 3.1 クラス一覧

本モジュールにクラスは定義されていません（関数ベースのサービスモジュール）。

### 3.2 関数一覧（カテゴリ別）

#### 内部関数

| 関数名 | 概要 |
|-------|------|
| `_ensure_log_dir()` | ログディレクトリとCSVファイルを初期化する |

#### 公開関数

| 関数名 | 概要 |
|-------|------|
| `log_unanswered_question(query, collections, reason, agent_response="")` | 回答できなかった質問をCSVに追記記録する |
| `load_unanswered_logs()` | 未回答質問ログを読み込み、新しい順にソートしたDataFrameを返す |
| `clear_unanswered_logs()` | 未回答ログをクリア（ヘッダーのみのファイルに再作成） |

---

## 4. クラス・関数 IPO詳細

### 4.1 内部関数

#### `_ensure_log_dir`

**概要**: ログディレクトリが存在しなければ作成し、CSVファイルが存在しなければヘッダー行を書き込んで初期化する。

```python
def _ensure_log_dir() -> None
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| なし | - | - | 引数なし |

| 項目 | 内容 |
|------|------|
| **Input** | なし |
| **Process** | 1. `LOG_DIR`が存在しなければ`mkdir(parents=True)`で作成<br>2. `UNANSWERED_LOG_FILE`が存在しなければヘッダー行（timestamp, query, collections, reason, agent_response）を書き込む |
| **Output** | `None`（副作用としてディレクトリ・ファイルを作成） |

**戻り値例**:
```python
None
```

```python
# 使用例（内部利用）
_ensure_log_dir()
# logs/ ディレクトリと logs/unanswered_questions.csv が存在する状態になる
```

### 4.2 公開関数

#### `log_unanswered_question`

**概要**: 回答できなかった質問の情報（質問・対象コレクション・理由・応答）をタイムスタンプ付きでCSVに追記記録する。例外時はログ出力のみで失敗を握りつぶす。

```python
def log_unanswered_question(
    query: str,
    collections: List[str],
    reason: str,
    agent_response: str = ""
) -> None
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `query` | str | - | ユーザーの質問 |
| `collections` | List[str] | - | 検索対象としたコレクションのリスト |
| `reason` | str | - | 未回答の理由（例: "No RAG results", "Low score"） |
| `agent_response` | str | "" | エージェントの最終応答（あれば） |

| 項目 | 内容 |
|------|------|
| **Input** | `query: str`, `collections: List[str]`, `reason: str`, `agent_response: str = ""` |
| **Process** | 1. `_ensure_log_dir()`でファイルを初期化<br>2. 現在時刻を`"%Y-%m-%d %H:%M:%S"`形式でタイムスタンプ化<br>3. `collections`をカンマ区切り文字列に結合<br>4. CSVへ1行追記<br>5. `logger.info()`で記録、例外時は`logger.error()` |
| **Output** | `None`（副作用としてCSVに1行追記） |

**戻り値例**:
```python
None
```

```python
# 使用例
from services.log_service import log_unanswered_question

log_unanswered_question(
    query="返品の手続きを教えて",
    collections=["faq_anthropic", "manual_anthropic"],
    reason="No RAG results",
    agent_response=""
)
# logs/unanswered_questions.csv に1行追記される
```

#### `load_unanswered_logs`

**概要**: 未回答質問ログCSVを読み込み、タイムスタンプの新しい順にソートしたpandas DataFrameを返す。ファイルが無い・空・読込失敗時は空のDataFrameを返す。

```python
def load_unanswered_logs() -> pd.DataFrame
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| なし | - | - | 引数なし |

| 項目 | 内容 |
|------|------|
| **Input** | なし |
| **Process** | 1. `_ensure_log_dir()`でファイルを初期化<br>2. ファイル未存在または空サイズなら空DataFrameを返す<br>3. `pd.read_csv()`で読み込み<br>4. `timestamp`列があれば降順ソート<br>5. 例外時は`logger.error()`し空DataFrameを返す |
| **Output** | `pd.DataFrame`: 列 `timestamp, query, collections, reason, agent_response` を持つログデータ |

**戻り値例**:
```python
#              timestamp                 query                collections           reason    agent_response
# 0  2026-06-17 10:30:00  返品の手続きを教えて   faq_anthropic, manual...   No RAG results
# 1  2026-06-17 09:15:00  営業時間は？           faq_anthropic              Low score
```

```python
# 使用例
from services.log_service import load_unanswered_logs

df = load_unanswered_logs()
print(f"未回答件数: {len(df)}")
# 未回答件数: 2
```

#### `clear_unanswered_logs`

**概要**: 未回答質問ログをクリアする。CSVファイルをヘッダー行のみの状態に再作成する。例外時はログ出力のみで失敗を握りつぶす。

```python
def clear_unanswered_logs() -> None
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| なし | - | - | 引数なし |

| 項目 | 内容 |
|------|------|
| **Input** | なし |
| **Process** | 1. `UNANSWERED_LOG_FILE`を`'w'`モードで開きヘッダー行のみ書き込む<br>2. `logger.info()`で記録、例外時は`logger.error()` |
| **Output** | `None`（副作用としてCSVを再作成しデータを消去） |

**戻り値例**:
```python
None
```

```python
# 使用例
from services.log_service import clear_unanswered_logs

clear_unanswered_logs()
# logs/unanswered_questions.csv がヘッダーのみの状態にリセットされる
```

---

## 5. 設定・定数

### 5.1 ログファイルパス定数

ログの保存先を定義する定数群です。

```python
LOG_DIR = Path("logs")
UNANSWERED_LOG_FILE = LOG_DIR / "unanswered_questions.csv"
```

| 定数名 | 値 | 説明 |
|-------|-----|------|
| `LOG_DIR` | `Path("logs")` | ログ保存先ディレクトリ（カレントディレクトリ基準の相対パス） |
| `UNANSWERED_LOG_FILE` | `logs/unanswered_questions.csv` | 未回答質問ログCSVファイルのパス |

### 5.2 CSVカラム定義

未回答質問ログCSVのヘッダー（列）構成です。

| 列名 | 説明 |
|------|------|
| `timestamp` | 記録時刻（`%Y-%m-%d %H:%M:%S`形式） |
| `query` | ユーザーの質問 |
| `collections` | 検索対象コレクション（カンマ区切り文字列） |
| `reason` | 未回答の理由 |
| `agent_response` | エージェントの最終応答 |

---

## 6. 使用例

### 6.1 基本的なワークフロー

```python
from services.log_service import (
    log_unanswered_question,
    load_unanswered_logs,
    clear_unanswered_logs,
)

# 1. 未回答質問を記録
log_unanswered_question(
    query="返品の手続きを教えて",
    collections=["faq_anthropic"],
    reason="No RAG results",
    agent_response="",
)

# 2. ログを読み込んで確認
df = load_unanswered_logs()
print(df.head())

# 3. 必要に応じてログをクリア
clear_unanswered_logs()
```

### 6.2 応用的なワークフロー（Streamlit UI連携）

```python
import streamlit as st
from services.log_service import load_unanswered_logs, clear_unanswered_logs

# 未回答質問ログを表形式で表示
df = load_unanswered_logs()
st.dataframe(df, use_container_width=True)

# クリアボタン
if st.button("ログをクリア"):
    clear_unanswered_logs()
    st.success("未回答ログをクリアしました")
```

---

## 7. エクスポート

本モジュールには`__all__`定義はありません。以下の公開要素が外部から利用可能です。

```python
# 公開関数
log_unanswered_question
load_unanswered_logs
clear_unanswered_logs

# 公開定数
LOG_DIR
UNANSWERED_LOG_FILE

# 内部関数（先頭アンダースコア・非公開）
# _ensure_log_dir
```

---

## 8. 変更履歴

| バージョン | 変更内容 |
|-----------|---------|
| 1.0 | 初版作成（2026-06-17） |

---

## 付録: 依存関係図

```mermaid
flowchart LR
    MODULE["log_service.py"]

    subgraph EXT["外部ライブラリ"]
        PANDAS["pandas"]
        CSV["csv"]
        LOGGING["logging"]
        DATETIME["datetime"]
        PATHLIB["pathlib"]
    end

    MODULE --> PANDAS
    MODULE --> CSV
    MODULE --> LOGGING
    MODULE --> DATETIME
    MODULE --> PATHLIB

    PANDAS --> P1["pandas.DataFrame"]
    PANDAS --> P2["pandas.read_csv"]
    CSV --> C1["csv.writer"]
    DATETIME --> D1["datetime.now"]
    PATHLIB --> PL1["pathlib.Path"]
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class MODULE,PANDAS,CSV,LOGGING,DATETIME,PATHLIB,P1,P2,C1,D1,PL1 default
style EXT fill:#1a1a1a,stroke:#fff,color:#fff
```

# qa_service.py - Q/A生成サービス ドキュメント

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

`qa_service.py` は、Q/Aペアの生成と保存に関するビジネスロジックを提供するサービスモジュールです。LLM には **Anthropic Claude**（既定モデル `claude-sonnet-4-6`）を使用し、`create_llm_client(provider="anthropic")` 経由でクライアントを生成します。構造化出力 API でテキストからQ/Aペアを生成し、CSV/JSON 形式でファイルに保存します。

### 主な責務

- 外部ランナー（`qa_generator_runner`）を直接インポートしてQ/A生成パイプラインを実行する
- Anthropic Claude を用いたテキストからのQ/Aペア自動生成
- 生成されたQ/Aペアへのメタデータ（チャンクID・データセットタイプ等）の付与
- Q/AペアのCSV・JSON形式でのファイル保存
- ログコールバックによる進捗・エラー通知

### 各責務対応のモジュール

| # | 責務 | 対応モジュール | 説明 |
|---|------|--------------|------|
| 1 | Q/A生成パイプラインの実行 | `qa_service.py` | `run_advanced_qa_generation()` が `qa_generator_runner` を直接実行 |
| 2 | Anthropic Claude によるQ/A生成 | `qa_service.py` | `generate_qa_pairs()` が `create_llm_client("anthropic")` を利用 |
| 3 | メタデータの付与 | `models.py` | `QAPair` モデルにチャンクID等を格納 |
| 4 | CSV・JSON保存 | `qa_service.py` | `save_qa_pairs_to_file()` が `pandas`/`json` で出力 |
| 5 | 進捗・エラー通知 | `qa_service.py` | 各関数の `log_callback` 引数で通知 |

### 主要機能一覧

| 機能 | 説明 |
|------|------|
| `QAPair` | Q/Aペアのデータモデル（Pydantic、`models.py` 定義） |
| `QAPairsResponse` | Q/Aペア生成レスポンスモデル（構造化出力用、`models.py` 定義） |
| `run_advanced_qa_generation()` | Q/A生成パイプラインを直接インポートモードで実行 |
| `generate_qa_pairs()` | テキストから Anthropic Claude でQ/Aペアを生成 |
| `save_qa_pairs_to_file()` | Q/AペアをCSVとJSONで保存 |

---

## 1. アーキテクチャ構成図

### 1.1 システム全体構成

```mermaid
flowchart TB
    subgraph CLIENT["クライアント層"]
        UI["Streamlit UI"]
        RUNNER["qa_generator_runner"]
        CELERY["Celery タスク"]
    end

    subgraph MODULE["qa_service.py"]
        RUN["run_advanced_qa_generation()"]
        GEN["generate_qa_pairs()"]
        SAVE["save_qa_pairs_to_file()"]
    end

    subgraph EXTERNAL["外部サービス層"]
        CLAUDE["Anthropic Claude (claude-sonnet-4-6)"]
        FS["ファイルシステム (qa_output/)"]
        MODELS["models.py (QAPair / QAPairsResponse)"]
    end

    UI --> RUN
    CELERY --> GEN
    RUNNER --> GEN
    RUN --> GEN
    GEN --> CLAUDE
    GEN --> MODELS
    SAVE --> FS
    GEN --> SAVE
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class UI,RUNNER,CELERY,RUN,GEN,SAVE,CLAUDE,FS,MODELS default
style CLIENT fill:#1a1a1a,stroke:#fff,color:#fff
style MODULE fill:#1a1a1a,stroke:#fff,color:#fff
style EXTERNAL fill:#1a1a1a,stroke:#fff,color:#fff
```

### 1.2 データフロー

1. クライアント層（UI・ランナー・Celery）からQ/A生成リクエストを受信
2. `run_advanced_qa_generation()` が `qa_generator_runner` をインポートして実行
3. `generate_qa_pairs()` が Anthropic Claude の構造化出力APIを呼び出しQ/Aを生成
4. 生成結果に `QAPair` メタデータを付与
5. `save_qa_pairs_to_file()` がCSV・JSONとして `qa_output/` に保存

---

## 2. モジュール構成図

### 2.1 内部モジュール構成

```mermaid
flowchart TB
    subgraph IMPORTS["インポート"]
        LLM["create_llm_client"]
        QAMODELS["QAPair / QAPairsResponse"]
        LOGGER["logger"]
    end

    subgraph PIPELINE["パイプライン実行"]
        RUN["run_advanced_qa_generation()"]
    end

    subgraph GENERATION["Q/A生成"]
        GEN["generate_qa_pairs()"]
    end

    subgraph PERSIST["保存"]
        SAVE["save_qa_pairs_to_file()"]
    end

    LLM --> GEN
    QAMODELS --> GEN
    LOGGER --> GEN
    RUN --> GEN
    GEN --> SAVE
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class LLM,QAMODELS,LOGGER,RUN,GEN,SAVE default
style IMPORTS fill:#1a1a1a,stroke:#fff,color:#fff
style PIPELINE fill:#1a1a1a,stroke:#fff,color:#fff
style GENERATION fill:#1a1a1a,stroke:#fff,color:#fff
style PERSIST fill:#1a1a1a,stroke:#fff,color:#fff
```

### 2.2 外部依存関係

| ライブラリ | バージョン | 用途 |
|-----------|-----------|------|
| `pandas` | - | Q/AペアのDataFrame変換・CSV出力 |
| `anthropic`（`create_llm_client` 経由） | - | Anthropic Claude API クライアント |

### 2.3 内部依存モジュール

| モジュール | 用途 |
|-----------|------|
| `helper.helper_llm.create_llm_client` | LLM クライアント生成（provider="anthropic"） |
| `models.QAPair` | Q/Aペアのデータモデル |
| `models.QAPairsResponse` | 構造化出力レスポンスモデル |
| `qa_generator_runner`（実行時インポート） | Q/A生成パイプライン本体 |

---

## 3. クラス・関数一覧表

### 3.1 クラス一覧

#### QAPair

> 📝 **注意**: `QAPair` は `models.py` で定義され、本モジュールがインポートして使用します。

| フィールド | 概要 |
|---------|------|
| `question` | 質問文 |
| `answer` | 回答文 |
| `question_type` | 質問タイプ |
| `source_chunk_id` | ソースチャンクID |
| `dataset_type` | データセットタイプ |
| `auto_generated` | 自動生成フラグ |

#### QAPairsResponse

> 📝 **注意**: `QAPairsResponse` は `models.py` で定義され、構造化出力のレスポンス型として使用します。

| フィールド | 概要 |
|---------|------|
| `qa_pairs` | 生成されたQ/Aペア（`QAPair`）のリスト |

### 3.2 関数一覧（カテゴリ別）

#### パイプライン実行

| 関数名 | 概要 |
|-------|------|
| `run_advanced_qa_generation(...)` | Q/A生成を直接インポートモードで実行 |

#### Q/A生成

| 関数名 | 概要 |
|-------|------|
| `generate_qa_pairs(...)` | テキストから Anthropic Claude でQ/Aペアを生成 |

#### 保存

| 関数名 | 概要 |
|-------|------|
| `save_qa_pairs_to_file(...)` | Q/AペアをCSVとJSONで保存 |

---

## 4. クラス・関数 IPO詳細

### 4.1 QAPair クラス

Q/Aペアのデータモデル。基本的なQ/Aペア情報に加え、品質・難易度のメタデータを含みます（`models.py` 定義）。

**概要**: 質問・回答とそのメタデータを保持する Pydantic モデル。

```python
class QAPair(BaseModel):
    question: str
    answer: str
    question_type: str = "fact"
    source_chunk_id: Optional[str] = None
    dataset_type: Optional[str] = None
    auto_generated: bool = False
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `question` | str | - | 質問文（必須） |
| `answer` | str | - | 回答文（必須） |
| `question_type` | str | "fact" | 質問タイプ（fact/reason/comparison 等） |
| `source_chunk_id` | Optional[str] | None | ソースチャンクID |
| `dataset_type` | Optional[str] | None | データセットタイプ |
| `auto_generated` | bool | False | 自動生成フラグ |

| 項目 | 内容 |
|------|------|
| **Input** | `question: str`, `answer: str`, `question_type: str = "fact"`, `source_chunk_id: Optional[str] = None`, `dataset_type: Optional[str] = None`, `auto_generated: bool = False` |
| **Process** | Pydantic によるフィールド検証とインスタンス生成 |
| **Output** | `QAPair` インスタンス |

**戻り値例**:
```python
QAPair(
    question="RAGとは何ですか？",
    answer="検索拡張生成の略です。",
    question_type="definition",
    source_chunk_id="chunk_001",
    dataset_type="faq",
    auto_generated=True
)
```

```python
# 使用例
from models import QAPair

qa = QAPair(question="RAGとは？", answer="検索拡張生成です。")
print(qa.question_type)
# fact
```

---

### 4.2 QAPairsResponse クラス

Q/Aペア生成レスポンス。構造化出力（structured output）で使用します（`models.py` 定義）。

**概要**: 生成された複数の `QAPair` をまとめて保持するレスポンスモデル。

```python
class QAPairsResponse(BaseModel):
    qa_pairs: List[QAPair] = []
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `qa_pairs` | List[QAPair] | [] | 生成されたQ/Aペアのリスト |

| 項目 | 内容 |
|------|------|
| **Input** | `qa_pairs: List[QAPair] = []` |
| **Process** | 構造化出力APIのレスポンスを `QAPair` リストとして格納 |
| **Output** | `QAPairsResponse` インスタンス |

**戻り値例**:
```python
QAPairsResponse(
    qa_pairs=[
        QAPair(question="質問1", answer="回答1"),
        QAPair(question="質問2", answer="回答2")
    ]
)
```

```python
# 使用例
from models import QAPairsResponse, QAPair

resp = QAPairsResponse(qa_pairs=[QAPair(question="Q", answer="A")])
print(len(resp.qa_pairs))
# 1
```

---

### 4.3 パイプライン実行関数

#### `run_advanced_qa_generation`

**概要**: Q/A生成を直接インポートモードで実行する。プロセス間通信の問題を回避するため、`qa_generator_runner` をモジュールとしてインポートして直接実行します。

```python
def run_advanced_qa_generation(
    dataset: Optional[str],
    input_file: Optional[str],
    use_celery: bool,
    celery_workers: int,
    batch_chunks: int,
    max_docs: int,
    merge_chunks: bool,
    min_tokens: int,
    max_tokens: int,
    coverage_threshold: float,
    model: str,
    analyze_coverage: bool,
    log_callback,
    progress_callback=None,
) -> Dict[str, Any]
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `dataset` | Optional[str] | - | データセット名 |
| `input_file` | Optional[str] | - | 入力ファイルパス |
| `use_celery` | bool | - | Celery を使用するか |
| `celery_workers` | int | - | Celery ワーカー数 |
| `batch_chunks` | int | - | バッチあたりのチャンク数 |
| `max_docs` | int | - | 最大ドキュメント数 |
| `merge_chunks` | bool | - | チャンクをマージするか |
| `min_tokens` | int | - | 最小トークン数 |
| `max_tokens` | int | - | 最大トークン数 |
| `coverage_threshold` | float | - | カバレッジ閾値 |
| `model` | str | - | 使用するLLMモデル |
| `analyze_coverage` | bool | - | カバレッジ分析を行うか |
| `log_callback` | Callable | - | ログコールバック関数 |
| `progress_callback` | Optional[Callable] | None | 進捗コールバック関数 |

| 項目 | 内容 |
|------|------|
| **Input** | 上記パラメータ一式 |
| **Process** | 1. カレントディレクトリを `sys.path` に追加<br>2. `qa_generator_runner` をインポート<br>3. `run_qa_generator()` を各引数で呼び出し<br>4. 例外時はトレースバックをログ出力 |
| **Output** | `Dict[str, Any]`: 実行結果（失敗時 `{"success": False, "error": ...}`） |

**戻り値例**:
```python
{
    "success": False,
    "error": "qa_generator_runner module not found"
}
```

```python
# 使用例
result = run_advanced_qa_generation(
    dataset="faq",
    input_file=None,
    use_celery=False,
    celery_workers=1,
    batch_chunks=10,
    max_docs=100,
    merge_chunks=True,
    min_tokens=50,
    max_tokens=200,
    coverage_threshold=0.8,
    model="claude-sonnet-4-6",
    analyze_coverage=True,
    log_callback=print,
)
print(result["success"])
```

---

### 4.4 Q/A生成関数

#### `generate_qa_pairs`

**概要**: テキストから Anthropic Claude を用いてQ/Aペアを生成する。`create_llm_client(provider="anthropic")` でクライアントを生成し、構造化出力APIで `QAPairsResponse` を取得します。

```python
def generate_qa_pairs(
    text: str,
    dataset_type: str,
    chunk_id: str,
    model: str = "claude-sonnet-4-6",
    qa_per_chunk: int = 3,
    log_callback=None,
) -> List[QAPair]
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `text` | str | - | 対象テキスト |
| `dataset_type` | str | - | データセットタイプ |
| `chunk_id` | str | - | チャンクID |
| `model` | str | "claude-sonnet-4-6" | 使用するモデル（Anthropic Claude） |
| `qa_per_chunk` | int | 3 | チャンクあたりのQ/A数 |
| `log_callback` | Optional[Callable] | None | ログコールバック関数 |

| 項目 | 内容 |
|------|------|
| **Input** | `text: str`, `dataset_type: str`, `chunk_id: str`, `model: str = "claude-sonnet-4-6"`, `qa_per_chunk: int = 3`, `log_callback=None` |
| **Process** | 1. `create_llm_client(provider="anthropic")` でクライアント生成<br>2. Q/A生成プロンプトを構築<br>3. `client.generate_structured()` で構造化出力（`QAPairsResponse`）を取得<br>4. 各Q/Aに `chunk_id`・`dataset_type`・`auto_generated=True` を付与<br>5. 例外時は空リストを返却 |
| **Output** | `List[QAPair]`: 生成されたQ/Aペアのリスト（エラー時は `[]`） |

**戻り値例**:
```python
[
    QAPair(
        question="RAGの目的は何ですか？",
        answer="外部知識を検索して生成精度を高めることです。",
        question_type="conceptual",
        source_chunk_id="chunk_001",
        dataset_type="faq",
        auto_generated=True
    )
]
```

```python
# 使用例
pairs = generate_qa_pairs(
    text="RAGは検索拡張生成の略で...",
    dataset_type="faq",
    chunk_id="chunk_001",
    model="claude-sonnet-4-6",
    qa_per_chunk=3,
    log_callback=print,
)
print(f"生成数: {len(pairs)}")
# 生成数: 3
```

---

### 4.5 保存関数

#### `save_qa_pairs_to_file`

**概要**: Q/AペアをCSVとJSONの両形式で `qa_output/` ディレクトリに保存する。ファイル名にはタイムスタンプを付与します。

```python
def save_qa_pairs_to_file(
    qa_pairs: List[QAPair],
    dataset_type: str,
    log_callback=None,
) -> Dict[str, str]
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `qa_pairs` | List[QAPair] | - | Q/Aペアのリスト |
| `dataset_type` | str | - | データセットタイプ（ファイル名に使用） |
| `log_callback` | Optional[Callable] | None | ログコールバック関数 |

| 項目 | 内容 |
|------|------|
| **Input** | `qa_pairs: List[QAPair]`, `dataset_type: str`, `log_callback=None` |
| **Process** | 1. `qa_output/` ディレクトリを作成<br>2. タイムスタンプを生成<br>3. Q/Aペアを `pandas.DataFrame` に変換<br>4. CSV（utf-8-sig）として保存<br>5. メタ情報付きJSON（utf-8）として保存 |
| **Output** | `Dict[str, str]`: 保存ファイルパスの辞書（`csv` / `json` キー） |

**戻り値例**:
```python
{
    "csv": "qa_output/qa_pairs_faq_20260617_143000.csv",
    "json": "qa_output/qa_pairs_faq_20260617_143000.json"
}
```

```python
# 使用例
saved = save_qa_pairs_to_file(
    qa_pairs=pairs,
    dataset_type="faq",
    log_callback=print,
)
print(saved["csv"])
# qa_output/qa_pairs_faq_20260617_143000.csv
```

---

## 5. 設定・定数

本モジュールに設定辞書・定数の定義はありません。主要なデフォルト値は関数引数として定義されています。

| 項目 | 値 | 説明 |
|------|------|------|
| 既定モデル | `claude-sonnet-4-6` | `generate_qa_pairs()` の `model` デフォルト（Anthropic Claude） |
| LLM プロバイダ | `anthropic` | `create_llm_client(provider="anthropic")` |
| 出力ディレクトリ | `qa_output/` | CSV・JSON保存先 |
| 既定Q/A数 | `3` | `qa_per_chunk` のデフォルト |

> 📝 **注意**: LLM 用APIキーは環境変数 `ANTHROPIC_API_KEY` で設定します。

---

## 6. 使用例

### 6.1 基本的なワークフロー

```python
from services.qa_service import (
    generate_qa_pairs,
    save_qa_pairs_to_file,
)

# 1. テキストからQ/Aペアを生成（Anthropic Claude）
pairs = generate_qa_pairs(
    text="RAGは検索拡張生成の略で、外部知識を検索して生成します。",
    dataset_type="faq",
    chunk_id="chunk_001",
    model="claude-sonnet-4-6",
    qa_per_chunk=3,
    log_callback=print,
)

# 2. ファイルに保存
saved = save_qa_pairs_to_file(
    qa_pairs=pairs,
    dataset_type="faq",
    log_callback=print,
)

print(f"CSV: {saved['csv']}")
print(f"JSON: {saved['json']}")
```

### 6.2 応用ワークフロー（パイプライン一括実行）

```python
from services.qa_service import run_advanced_qa_generation

result = run_advanced_qa_generation(
    dataset="faq",
    input_file=None,
    use_celery=False,
    celery_workers=1,
    batch_chunks=10,
    max_docs=100,
    merge_chunks=True,
    min_tokens=50,
    max_tokens=200,
    coverage_threshold=0.8,
    model="claude-sonnet-4-6",
    analyze_coverage=True,
    log_callback=print,
)

if result.get("success"):
    print("Q/A生成完了")
else:
    print(f"エラー: {result.get('error')}")
```

---

## 7. エクスポート

`qa_service.py` には `__all__` の定義はありません。公開要素は以下のとおりです。

```python
# 関数
run_advanced_qa_generation   # Q/A生成パイプライン実行
generate_qa_pairs            # Anthropic Claude によるQ/A生成
save_qa_pairs_to_file        # CSV・JSON保存

# 再エクスポート（models.py からインポート）
QAPair                       # Q/Aペアのデータモデル
QAPairsResponse              # Q/Aペア生成レスポンスモデル
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
    QASERVICE["qa_service.py"]

    subgraph PANDAS["pandas"]
        DF["DataFrame"]
    end

    subgraph HELPER["helper.helper_llm"]
        LLMCLIENT["create_llm_client"]
    end

    subgraph MODELSPKG["models"]
        QAPAIR["QAPair"]
        QARESP["QAPairsResponse"]
    end

    subgraph RUNTIME["実行時インポート"]
        RUNNER["qa_generator_runner"]
    end

    QASERVICE --> DF
    QASERVICE --> LLMCLIENT
    QASERVICE --> QAPAIR
    QASERVICE --> QARESP
    QASERVICE --> RUNNER
    LLMCLIENT --> CLAUDE["Anthropic Claude"]
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class QASERVICE,DF,LLMCLIENT,QAPAIR,QARESP,RUNNER,CLAUDE default
style PANDAS fill:#1a1a1a,stroke:#fff,color:#fff
style HELPER fill:#1a1a1a,stroke:#fff,color:#fff
style MODELSPKG fill:#1a1a1a,stroke:#fff,color:#fff
style RUNTIME fill:#1a1a1a,stroke:#fff,color:#fff
```

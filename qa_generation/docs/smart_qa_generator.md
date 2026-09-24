# smart_qa_generator.py - コンテンツ適応型 Q/A 生成 ドキュメント

**Version 1.2** | 最終更新: 2026-09-24

---

## 目次

1. [概要](#概要)
2. [アーキテクチャ構成図](#1-アーキテクチャ構成図)
3. [モジュール構成図](#2-モジュール構成図)
4. [SmartQAGenerator の優位性](#3-smartqagenerator-の優位性)
5. [判断基準と Q/A 数決定ロジック](#4-判断基準と-qa-数決定ロジック)
6. [クラス・関数一覧表](#5-クラス関数一覧表)
7. [クラス・関数 IPO詳細](#6-クラス関数-ipo詳細)
8. [エラーハンドリング](#7-エラーハンドリング)
9. [設定・定数](#8-設定定数)
10. [関連モジュール](#9-関連モジュール)
11. [変更履歴](#10-変更履歴)

---

## 概要

`qa_generation/smart_qa_generator.py` は、**コンテンツを考慮したインテリジェントQ/A生成システム**です。従来の固定数Q/A生成方式と異なり、ローカル LLM（Ollama）によるチャンク分析を行い、各チャンクの情報密度・重要度・複雑さに応じて最適なQ/A数を動的に決定します。

v3.0 では、旧来の「分析（`analyze_chunk`）＋生成（`generate_qa_pairs`）」の2段階方式を廃止し、`analyze_and_generate()` による**構造化出力（`response_schema=SmartQAResult`）1回呼び出し**に統合しました。これにより LLM 呼び出しコストを半減し、Markdownフェンス手剥がし＋`json.loads` の脆弱なパースを排除しています。

### 主な責務

- Q/A 生成の出力スキーマを定義する
- LLM クライアントを用意する
- チャンクを分析し、適切な数の Q/A を生成する
- チャンク単位の処理結果を辞書で返す（失敗時も落とさない）
- 生成結果を集計する

### 各責務対応のモジュール

| # | 責務 | 対応モジュール | 説明 |
|---|---|---|---|
| 1 | Q/A 生成の出力スキーマを定義する | `SmartQAPair` / `SmartQAResult` | チャンク分析結果と Q/A リストを 1 つの構造化出力にまとめる Pydantic モデル |
| 2 | LLM クライアントを用意する | `SmartQAGenerator.__init__()` | `create_llm_client("ollama")` でクライアントを生成し、既定モデルを保持する |
| 3 | チャンクを分析し、適切な数の Q/A を生成する | `SmartQAGenerator.analyze_and_generate()` | 情報密度・重要度・複雑さから Q/A 数を決め、1 回の構造化出力で生成 |
| 4 | チャンク単位の処理結果を辞書で返す（失敗時も落とさない） | `SmartQAGenerator.process_chunk()` | パイプライン / Celery タスクが使う入口。例外時はエラー情報付きの空結果 |
| 5 | 生成結果を集計する | `analyze_qa_statistics()` | Q/A 数の分布・平均などの統計 |

### 主要機能一覧

| 機能 | 説明 |
|---|---|
| `SmartQAGenerator(model=..., api_key=None)` | LLM クライアントを生成する |
| `process_chunk(chunk_text)` | チャンク 1 件を処理し、Q/A と分析結果の辞書を返す（主な入口） |
| `analyze_and_generate(chunk_text)` | 分析＋生成を構造化出力 1 回で行い `SmartQAResult` を返す |
| `analyze_qa_statistics(results)` | 複数チャンクの結果を集計する |

---

## 1. アーキテクチャ構成図

### 1.1 システム全体構成

```mermaid
flowchart TB
    subgraph CALLER["呼び出し側"]
        PIPE["QAPipeline._generate_sync()（pipeline.py）"]
        CEL["celery_tasks（Celery ワーカー）"]
    end
    subgraph TARGET["smart_qa_generator.py"]
        GEN["SmartQAGenerator（analyze_and_generate / process_chunk）"]
        STAT["analyze_qa_statistics()"]
    end
    subgraph EXTERNAL["外部（LLM・Embedding・ファイル・基盤）"]
        LLMC["helper.helper_llm.create_llm_client('ollama')"]
        LLM["Ollama（ローカル LLM）"]
    end
    PIPE -->|"チャンク"| GEN
    CEL -->|"チャンク"| GEN
    PIPE -->|"集計"| STAT
    GEN --> LLMC
    LLMC -->|"構造化出力"| LLM
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class PIPE,CEL,GEN,STAT,LLMC,LLM default
style CALLER fill:#1a1a1a,stroke:#fff,color:#fff
style TARGET fill:#1a1a1a,stroke:#fff,color:#fff
style EXTERNAL fill:#1a1a1a,stroke:#fff,color:#fff
```

### 1.2 データフロー

1. `QAPipeline._generate_sync()` または Celery ワーカーがチャンク本文を `process_chunk()` へ渡す
2. `analyze_and_generate()` が Ollama（ローカル LLM） へ分析と生成を 1 回の構造化出力（`SmartQAResult`）で依頼する
3. LLM がチャンクの情報密度・重要度・複雑さを判断し、Q/A 数を決めて生成する
4. `process_chunk()` が結果を辞書へ整形して返す（失敗時はエラー情報付きの空結果）
5. 呼び出し側が全チャンク分を集め、必要なら `analyze_qa_statistics()` で集計する

---

## 2. モジュール構成図

### 2.1 全体構成

```
┌─────────────────────────────────────────────────────────────┐
│                   smart_qa_generator.py                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              SmartQAGenerator クラス                 │    │
│  ├─────────────────────────────────────────────────────┤    │
│  │  __init__()             # 初期化・統一クライアント設定  │    │
│  │  analyze_and_generate() # 分析＋生成（構造化出力1回）   │    │
│  │  process_chunk()        # 一括処理（メイン）           │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                            │
│  ┌─────────────────────────────────────────────────────┐   │
│  │           ユーティリティ関数                           │   │
│  ├─────────────────────────────────────────────────────┤   │
│  │  analyze_qa_statistics()  # 統計分析                  │   │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│            統一 LLM クライアント（helper_llm）                │
│  └─ create_llm_client("ollama") → OllamaClient              │
│     └─ generate_structured()  # OpenAI 互換 API              │
│                               # response_schema=SmartQAResult │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 処理フロー

```mermaid
graph TB
    subgraph Input
        A[チャンクテキスト]
    end

    subgraph PROC["SmartQAGenerator.process_chunk()"]
        B[analyze_and_generate]
        C[SmartQAResult]
        D{qa_count}
        E[空リスト]
    end

    subgraph Output
        F[Q/Aペアリスト]
        G[分析結果]
    end

    A --> B
    B --> C
    C --> D
    D -->|0| E
    D -->|1-5| F
    C --> G
    E --> G
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class A,B,C,D,E,F,G default
style Input fill:#1a1a1a,stroke:#fff,color:#fff
style PROC fill:#1a1a1a,stroke:#fff,color:#fff
style Output fill:#1a1a1a,stroke:#fff,color:#fff
```

---

## 3. SmartQAGenerator の優位性

### 3.1 従来方式との比較

| 観点 | 従来方式 | SmartQAGenerator |
|-----|---------|------------------|
| **Q/A数決定** | 固定（例: 3個/チャンク） | 動的（0〜5個/チャンク） |
| **コンテンツ考慮** | なし | 情報密度・重要度・複雑さを分析 |
| **メタ情報処理** | 無駄なQ/Aを生成 | 0個（スキップ） |
| **高密度情報** | 情報損失の可能性 | 4〜5個で網羅的にカバー |
| **品質** | 均一（低〜中） | コンテンツに最適化（高） |

### 3.2 主な優位性

#### 1. コンテンツ適応型Q/A数決定

```
従来: すべてのチャンク → 固定3個のQ/A
Smart: チャンク分析 → 0〜5個の最適なQ/A数
```

- **メタ情報チャンク**（「詳細は付録参照」など）→ **0個**（無駄を排除）
- **単純な事実**（「製品は赤色です」）→ **1個**
- **標準的な説明**（複数の関連情報）→ **2〜3個**
- **高密度技術情報**（API仕様、暗号化詳細）→ **4〜5個**

#### 2. 重要トピックの明示化

分析フェーズで抽出された `key_topics` を生成フェーズに渡すことで、重要な情報を優先的にQ/A化します。

```python
# 分析結果例
{
    'qa_count': 4,
    'key_topics': ['暗号化方式', '鍵長', 'ブロックサイズ', '利用モード'],
    'importance_score': 0.9,
    'complexity': 'high'
}
```

#### 3. 構造化出力1回による品質向上

```mermaid
graph LR
    A[チャンク] --> B[analyze_and_generate]
    B --> C[SmartQAResult]
    C --> D{qa_count}
    D -->|0個| E[空リスト]
    D -->|1-5個| F[Q/Aペア]
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class A,B,C,D,E,F default
```

- **1回呼び出し**: 分析（qa_count 決定）とQ/A生成を `response_schema=SmartQAResult` で同時取得
- **温度**: 0.2（安定した判断と自然な生成を両立）
- 旧 v2.x の「分析フェーズ＋生成フェーズ」2段階呼び出しは廃止（コスト半減）

#### 4. エラーハンドリング

構造化出力に失敗した場合は例外を捕捉し、`process_chunk()` が `success=False`
（`qa_pairs=[]`）を返します。呼び出し側はそのチャンクをスキップできます。
旧 v2.x にあった文字数ベースのフォールバック数推定は廃止されています。

#### 5. 統計分析機能

処理結果の品質を数値で把握できます。

```python
{
    'total_chunks': 100,
    'total_qa_pairs': 245,
    'avg_qa_per_chunk': 2.45,
    'avg_importance_score': 0.72,
    'qa_distribution': {0: 5, 1: 15, 2: 30, 3: 35, 4: 12, 5: 3}
}
```

---

## 4. 判断基準と Q/A 数決定ロジック

### 4.1 Q/A数の判断基準

| Q/A数 | 判断基準 | 例 |
|:-----:|---------|---|
| **0** | 補足情報のみ、メタ情報、意味のない繰り返し | 「詳細は付録参照」「ページ番号: 42」 |
| **1** | 単純な事実の記述（1つの情報のみ） | 「この製品は赤色です。」 |
| **2** | 関連する2つの事実 | 「製品は赤色で、サイズはMです。」 |
| **3** | 複数の関連情報、標準的な説明パラグラフ | 一般的な製品説明、概要説明 |
| **4-5** | 高密度な技術情報、複数の独立したポイント、警告・注意事項 | API仕様、暗号化詳細、安全上の注意 |

### 4.2 分析プロンプトの観点

1. **情報密度**: チャンクに含まれる独立した情報・事実の数
2. **重要度**: 情報の重要性（critical/high/medium/low）
3. **複雑さ**: 説明に必要な詳細度（low/medium/high）
4. **独立性**: 各情報が他の文脈なしで理解可能か

---

## 5. クラス・関数一覧表

### 5.1 クラス一覧

| クラス名 | 機能概要 |
|---------|---------|
| `SmartQAGenerator` | コンテンツを考慮したインテリジェントQ/A生成を行うメインクラス。チャンク分析とQ/A生成の両機能を提供。 |

### 5.2 メソッド一覧（SmartQAGenerator）

| メソッド名 | 可視性 | 機能概要 |
|-----------|:-----:|---------|
| `__init__` | public | インスタンス初期化。統一 LLM クライアント（ローカル LLM / Ollama）の設定。 |
| `analyze_and_generate` | public | チャンク分析とQ/A生成を構造化出力1回（`response_schema=SmartQAResult`）で実行。 |
| `process_chunk` | public | `analyze_and_generate` をラップし、dict 形式（analysis/qa_pairs/usage/success）で返すメインメソッド。 |

### 5.3 ユーティリティ関数一覧

| 関数名 | 機能概要 |
|-------|---------|
| `analyze_qa_statistics` | 複数チャンクの処理結果を統計分析し、Q/A数分布・平均重要度などを算出。 |

---

## 6. クラス・関数 IPO詳細

### 6.1 使用例

#### 6.1.1 基本的な使用例

```python
from qa_generation.smart_qa_generator import SmartQAGenerator

# 初期化（既定でローカル LLM / Ollama を使用）
generator = SmartQAGenerator(model="gemma4:12b-mlx")

# 単一チャンク処理
result = generator.process_chunk(chunk_text)

if result['success']:
    print(f"分析結果: {result['analysis']}")
    print(f"生成Q/A数: {len(result['qa_pairs'])}")
    for qa in result['qa_pairs']:
        print(f"Q: {qa['question']}")
        print(f"A: {qa['answer']}")
```

#### 6.1.2 複数チャンクの一括処理

```python
from qa_generation.smart_qa_generator import SmartQAGenerator, analyze_qa_statistics

generator = SmartQAGenerator()

# 複数チャンク処理
results = []
for chunk in chunks:
    result = generator.process_chunk(chunk['text'])
    results.append(result)

# 統計分析
stats = analyze_qa_statistics(results)
print(f"総Q/A数: {stats['total_qa_pairs']}")
print(f"平均Q/A数/チャンク: {stats['avg_qa_per_chunk']:.2f}")
```

#### 6.1.3 分析結果（SmartQAResult）を直接取得する場合

```python
# analyze_and_generate は SmartQAResult を直接返す（分析＋生成を1回で実行）
result = generator.analyze_and_generate(chunk_text)
print(f"推奨Q/A数: {result.qa_count}")
print(f"主要トピック: {result.key_topics}")
for qa in result.qa_pairs:
    print(f"Q: {qa.question} / A: {qa.answer}")
```

> v3.0 では分析専用メソッド（`analyze_chunk`）・生成専用メソッド（`generate_qa_pairs`）は
> 廃止され、`analyze_and_generate()` の1回呼び出しに統合されています。

### 6.2 SmartQAGenerator.\_\_init\_\_()

#### IPO

| 区分 | 内容 |
|-----|------|
| **Input** | `model`: str（使用するローカル LLM モデル、既定: `get_default_ollama_model()` → `gemma4:12b-mlx`）<br>`api_key`: Optional[str]（**未使用**。ローカル実行なので API キーは要らない） |
| **Process** | 1. `create_llm_client(provider="ollama", default_model=model)` で統一クライアント生成<br>2. モデル名・`last_usage` の初期化 |
| **Output** | SmartQAGeneratorインスタンス |

#### プロセスフロー

```mermaid
flowchart TD
    A[開始] --> B["create_llm_client(provider='ollama')"]
    B --> C[OllamaClient 生成]
    C --> D[self.model 保存]
    D --> E["last_usage 初期化"]
    E --> F[完了]
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class A,B,C,D,E,F default
```

### 6.3 SmartQAGenerator.analyze_and_generate()

#### IPO

| 区分 | 内容 |
|-----|------|
| **Input** | `chunk_text`: str（分析・生成対象のチャンクテキスト） |
| **Process** | 1. `COMBINED_PROMPT` 構築（分析基準＋生成ガイドライン）<br>2. `client.generate_structured(response_schema=SmartQAResult, temperature=0.2, max_output_tokens=4096)` を1回呼び出し<br>3. `None` の場合は `ValueError`<br>4. クライアントの `last_usage`（input/output tokens）を取り込み |
| **Output** | `SmartQAResult`（qa_count, key_topics, importance_score, complexity, reasoning, qa_pairs） |

#### プロセスフロー

```mermaid
flowchart TD
    A[chunk_text受信] --> B[COMBINED_PROMPT構築]
    B --> C["client.generate_structured(SmartQAResult)"]
    C --> D{result is None?}
    D -->|Yes| E[ValueError]
    D -->|No| F[last_usage取り込み]
    F --> G[SmartQAResult返却]
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class A,B,C,D,E,F,G default
```

#### 出力構造（SmartQAResult）

```python
{
    'qa_count': int,           # 生成すべきQ/A数（0-5）
    'key_topics': List[str],   # 主要トピック
    'importance_score': float, # 重要度（0.0-1.0）
    'complexity': str,         # 複雑さ（low/medium/high）
    'reasoning': str,          # 判断理由
    'qa_pairs': List[SmartQAPair]  # {question, answer, topic} のリスト（qa_count 件）
}
```

### 6.4 SmartQAGenerator.process_chunk()

#### IPO

| 区分 | 内容 |
|-----|------|
| **Input** | `chunk_text`: str（チャンクテキスト） |
| **Process** | 1. `analyze_and_generate()` 実行（構造化出力1回）<br>2. `SmartQAResult` を analysis dict と qa_pairs list に分解<br>3. `last_usage`（トークン使用量）を付与<br>4. 例外時は `success=False` で失敗結果返却 |
| **Output** | `Dict`: {analysis, qa_pairs, usage, success} |

#### プロセスフロー

```mermaid
flowchart TD
    A[chunk_text受信] --> B[try開始]
    B --> C[analyze_and_generate実行]
    C --> D[analysis分解]
    D --> E[qa_pairs分解]
    E --> F[usage付与]
    F --> G[成功結果構築]
    G --> H[結果返却]
    B --> I[except発生]
    I --> J[エラーログ出力]
    J --> K["失敗結果構築 (success=False)"]
    K --> H
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class A,B,C,D,E,F,G,H,I,J,K default
```

#### 出力構造

```python
{
    'analysis': Dict,        # SmartQAResult の分析部（qa_count等）
    'qa_pairs': List[Dict],  # 生成されたQ/A [{question, answer, topic}, ...]
    'usage': Dict[str, int], # トークン使用量 {input_tokens, output_tokens}
    'success': bool          # 処理成功フラグ
}
```

### 6.5 analyze_qa_statistics()

#### IPO

| 区分 | 内容 |
|-----|------|
| **Input** | `results`: List[Dict]（process_chunk()の結果リスト） |
| **Process** | 1. 総チャンク数カウント<br>2. 総Q/A数カウント<br>3. Q/A数分布計算<br>4. 平均Q/A数計算<br>5. 平均重要度計算 |
| **Output** | `Dict`: {total_chunks, total_qa_pairs, avg_qa_per_chunk, avg_importance_score, qa_distribution} |

#### プロセスフロー

```mermaid
flowchart TD
    A[results受信] --> B[total_chunks = len]
    B --> C[total_qa = sum of qa_pairs]
    C --> D[qa_distribution計算]
    D --> E[avg_qa_per_chunk計算]
    E --> F[importance_scores抽出]
    F --> G[avg_importance計算]
    G --> H[統計結果構築]
    H --> I[結果返却]
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class A,B,C,D,E,F,G,H,I default
```

#### 出力構造

```python
{
    'total_chunks': int,           # 総チャンク数
    'total_qa_pairs': int,         # 総Q/A数
    'avg_qa_per_chunk': float,     # 平均Q/A数/チャンク
    'avg_importance_score': float, # 平均重要度
    'qa_distribution': Dict[int, int]  # Q/A数分布 {0: 5, 1: 15, ...}
}
```

---

## 7. エラーハンドリング

### 7.1 失敗時の挙動

構造化出力（`generate_structured`）が失敗、または結果が `None` の場合、
`analyze_and_generate()` は `ValueError` を送出します。`process_chunk()` は
これを捕捉してエラーログを出力し、`success=False` の結果を返します。
旧 v2.x にあった文字数ベースのフォールバック数推定は廃止されています。

### 7.2 エラー時の戻り値（process_chunk）

```python
{
    'analysis': {},
    'qa_pairs': [],
    'usage'   : {"input_tokens": 0, "output_tokens": 0},
    'success' : False
}
```

---

## 8. 設定・定数

### 8.1 初期化パラメータ

| パラメータ | 型 | デフォルト | 説明 |
|----------|---|----------|------|
| `model` | str | `get_default_ollama_model()` | 使用するローカル LLM モデル（既定 `gemma4:12b-mlx`） |
| `api_key` | Optional[str] | None | **未使用**。ローカル実行なので API キーは要らない |

### 8.2 内部設定値

| 項目 | 値 | 用途 |
|-----|---|------|
| temperature | 0.2 | 分析・生成の両立（構造化出力1回） |
| max_output_tokens | 4096 | 構造化出力の最大トークン |
| Q/A数上限 | 5 | 最大Q/A数 |
| Q/A数下限 | 0 | 最小Q/A数（スキップ） |
| importance_score上限 | 1.0 | 最大重要度 |
| importance_score下限 | 0.0 | 最小重要度 |

### 8.3 LLM クライアント

| 項目 | 値 |
|-----|---|
| プロバイダー | `ollama`（`create_llm_client("ollama")`） |
| クライアント | `OllamaClient`（helper/helper_llm.py） |
| API | OpenAI 互換 API（`generate_structured` 経由の構造化出力） |
| APIキー | **不要**（ローカル実行）。前提は `ollama serve` と既定モデルの pull |

---

## 9. 関連モジュール

| モジュール | 関係 |
|-----------|------|
| `qa_generation/pipeline.py` | SmartQAGeneratorを使用してQ/A生成を実行 |
| `qa_generation/evaluation.py` | 生成されたQ/Aのカバレッジを分析 |
| `qa_generation/models.py` | Q/Aペアのデータモデル定義 |
| `celery_tasks.py` | 並列処理時にSmartQAGeneratorを呼び出し |

---

**作成日**: 2025-01-27
**最終更新**: 2026-09-21（LLM 表記を Ollama へ是正。下の変更履歴を参照）
**対象ファイル**: `qa_generation/smart_qa_generator.py`
**バージョン**: v3.0

---

## 10. 変更履歴

| バージョン | 変更内容 |
|---|---|
| 1.2 | 基本フォーマット `a_class_method_md_format.md` の章構成へ組み替え（2026-09-24）。概要に「主な責務」と「各責務対応のモジュール」（1:1）を置き、`## 1. アーキテクチャ構成図`（3 層＋データフロー）を新設。既存の構成図は `## 2. モジュール構成図` へ、使用方法は IPO 詳細の冒頭（`### 6.1 使用例`）へ移した。固有の解説章（「SmartQAGenerator の優位性」・「判断基準と Q/A 数決定ロジック」）は §1.3 に従い一覧表の前に置き、章・小節に番号を振った。本文の内容は変えていない |
| 1.1 | **LLM 表記を Ollama へ是正**（2026-09-21）。実装は `create_llm_client(provider="ollama")`（`smart_qa_generator.py:69`）・既定モデルは `get_default_ollama_model()` だが、文書は Anthropic Claude / `claude-sonnet-4-6` / `ANTHROPIC_API_KEY` のままだった。あわせて `**Version X.X**` ヘッダーを追加 |
| 1.0 | 初版（2026-06-21 時点。当時は LLM を Anthropic Claude へ統一していた） |

# models.py 完全ガイド

**Version 1.0** | 最終更新: 2026-09-21

## 概要

`qa_generation/models.py` は、**Q/A 生成で使う Pydantic データモデルの定義だけを持つモジュール**です。ロジックは一切持たず、LLM も I/O も呼びません。`helper/helper_rag_qa.py` に散在していた 8 クラスを 1 箇所へ集約する目的で作られました（モジュール docstring の「統合元」）。

### 主な責務

- Q/A ペアとそのリストのスキーマを定義する
- Chain-of-Thought 方式の分析結果・Q/A・レスポンスのスキーマを定義する
- LLM 品質向上用の簡易 Q/A スキーマを定義する
- Q/A 生成前のチェックリスト（文書特性・抽出要件・品質基準・Q/A 特性）を定義する

### 主要機能一覧

| クラス | 説明 |
|---|---|
| `QAPair` | Q/A ペアの基本モデル（質問・回答＋メタ 3 項目） |
| `QAPairsList` | `QAPair` のリスト |
| `ChainOfThoughtAnalysis` | CoT の文書分析結果 |
| `ChainOfThoughtQAPair` | 推論過程と信頼度を持つ Q/A ペア |
| `ChainOfThoughtResponse` | 分析＋Q/A リストの複合 |
| `EnhancedQAPair` | 質問・回答だけの最小モデル |
| `EnhancedQAPairsList` | `EnhancedQAPair` のリスト |
| `QAGenerationConsiderations` | Q/A 生成前のチェックリスト（4 つの設定辞書） |

---

## 目次

1. [⚠️ 同名クラスが 3 箇所にある](#️-同名クラスが-3-箇所にある)
2. [クラス一覧](#クラス一覧)
3. [クラス構成図](#クラス構成図)
4. [フィールド詳細](#フィールド詳細)
5. [QAGenerationConsiderations の既定値](#qagenerationconsiderations-の既定値)
6. [使用方法](#使用方法)
7. [エクスポート](#エクスポート)
8. [関連モジュール](#関連モジュール)
9. [変更履歴](#変更履歴)

---

## ⚠️ 同名クラスが 3 箇所にある

**`QAPair` という名前のクラスは、このリポジトリに 3 つ存在する。互いに別物である。**

| 定義場所 | 使われ方 | フィールド |
|---|---|---|
| **`models.py`（リポジトリ直下）** | **現役**。`services/qa_service.py` が `from models import QAPair` で使う | `question` / `answer` / `question_type` / `difficulty_level` ほか |
| **`qa_generation/models.py`（本モジュール）** | `qa_generation/__init__.py` が再エクスポートするだけで、**ほかに import 元が無い**（2026-09-21 実測） | `question` / `answer` / `question_type` / `difficulty` / `source_span` |
| `helper/helper_rag_qa.py` | 統合元として残っている旧定義 | 同系だが独立 |

> ⚠️ **`from models import QAPair` と `from qa_generation.models import QAPair` は別のクラスを指す。**
> 前者は `difficulty_level`、後者は `difficulty` と `source_span` を持つ。
> import 文を「短く」書き換えると、フィールド名が合わずに壊れる。
>
> 📌 本モジュールに**本番の利用者はいない**（`qa_generation/__init__.py` 経由の再エクスポートのみ）。
> `services/qa_service.py` が使うのは直下の `models.py` である。
> 整理するかどうかは未判断で、索引の残タスクとして扱う。

---

## クラス一覧

| クラス | 基底 | 行 | 必須フィールド |
|---|---|---:|---|
| `QAPair` | `BaseModel` | 27 | `question` / `answer` |
| `QAPairsList` | `BaseModel` | 36 | なし（既定は空リスト） |
| `ChainOfThoughtAnalysis` | `BaseModel` | 45 | なし |
| `ChainOfThoughtQAPair` | `BaseModel` | 52 | `question` / `answer` |
| `ChainOfThoughtResponse` | `BaseModel` | 60 | なし |
| `EnhancedQAPair` | `BaseModel` | 70 | `question` / `answer` |
| `EnhancedQAPairsList` | `BaseModel` | 76 | なし |
| `QAGenerationConsiderations` | `BaseModel` | 85 | なし（4 辞書すべて既定値あり） |

---

## クラス構成図

```mermaid
flowchart TB
    subgraph Basic["基本Q/Aペアモデル"]
        QAPair["QAPair"]
        QAPairsList["QAPairsList"]
    end
    subgraph CoT["Chain-of-Thought関連モデル"]
        Analysis["ChainOfThoughtAnalysis"]
        CoTPair["ChainOfThoughtQAPair"]
        CoTResp["ChainOfThoughtResponse"]
    end
    subgraph Enh["拡張Q/Aペアモデル"]
        EPair["EnhancedQAPair"]
        EList["EnhancedQAPairsList"]
    end
    subgraph Cfg["Q/A生成要件・設定モデル"]
        Consider["QAGenerationConsiderations"]
    end
    QAPairsList -->|"List[QAPair]"| QAPair
    CoTResp -->|"analysis"| Analysis
    CoTResp -->|"List[ChainOfThoughtQAPair]"| CoTPair
    EList -->|"List[EnhancedQAPair]"| EPair
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class QAPair,QAPairsList,Analysis,CoTPair,CoTResp,EPair,EList,Consider default
style Basic fill:#1a1a1a,stroke:#fff,color:#fff
style CoT fill:#1a1a1a,stroke:#fff,color:#fff
style Enh fill:#1a1a1a,stroke:#fff,color:#fff
style Cfg fill:#1a1a1a,stroke:#fff,color:#fff
```

`QAGenerationConsiderations` はどのモデルも参照せず、独立している。

---

## フィールド詳細

### QAPair

Q/A ペアの基本モデル。

| フィールド | 型 | 既定 | 説明 |
|---|---|---|---|
| `question` | `str` | **必須** | 質問文 |
| `answer` | `str` | **必須** | 回答文 |
| `question_type` | `str` | `"fact"` | 質問タイプ（`fact` / `reason` / `comparison` / `application`） |
| `difficulty` | `str` | `"medium"` | 難易度（`easy` / `medium` / `hard`） |
| `source_span` | `str` | `""` | 回答の根拠となる元テキストの一部 |

> 📌 `question_type` / `difficulty` は**自由文字列**である。`Enum` でも `Literal` でもないため、
> 説明にない値を入れてもバリデーションは通る。

### QAPairsList

| フィールド | 型 | 既定 | 説明 |
|---|---|---|---|
| `qa_pairs` | `List[QAPair]` | 空リスト | Q/A ペアのリスト |

### ChainOfThoughtAnalysis

| フィールド | 型 | 既定 | 説明 |
|---|---|---|---|
| `main_topics` | `List[str]` | 空リスト | 主要トピック |
| `key_concepts` | `List[str]` | 空リスト | 重要概念 |
| `information_density` | `str` | `"medium"` | 情報密度（`low` / `medium` / `high`） |

### ChainOfThoughtQAPair

| フィールド | 型 | 既定 | 制約 | 説明 |
|---|---|---|---|---|
| `question` | `str` | **必須** | — | 質問文 |
| `answer` | `str` | **必須** | — | 回答文 |
| `reasoning` | `str` | `""` | — | 推論過程 |
| `confidence` | `float` | `0.8` | `0.0 <= x <= 1.0` | 信頼度スコア |

`confidence` は**このモジュールで唯一の範囲制約付きフィールド**で、範囲外は
`ValidationError` になる。

### ChainOfThoughtResponse

| フィールド | 型 | 既定 | 説明 |
|---|---|---|---|
| `analysis` | `ChainOfThoughtAnalysis` | 既定インスタンス | 分析結果 |
| `qa_pairs` | `List[ChainOfThoughtQAPair]` | 空リスト | Q/A ペア |

### EnhancedQAPair / EnhancedQAPairsList

| クラス | フィールド | 型 | 既定 |
|---|---|---|---|
| `EnhancedQAPair` | `question` / `answer` | `str` | **必須** |
| `EnhancedQAPairsList` | `qa_pairs` | `List[EnhancedQAPair]` | 空リスト |

「LLM 品質向上用」の最小スキーマで、メタ情報を持たない。

---

## QAGenerationConsiderations の既定値

4 つの辞書フィールドをまとめて持つ設定モデル。いずれも `default_factory` で生成されるため、
インスタンスごとに独立した辞書になる（クラス間で共有されない）。

### document_characteristics（文書特性）

| キー | 既定 | 意味 |
|---|---|---|
| `domain` | `"general"` | 専門分野 |
| `text_type` | `"informative"` | 説明文・対話 など |
| `complexity` | `"medium"` | 複雑度 |
| `length` | `"medium"` | 文書長 |

### extraction_requirements（抽出要件）

| キー | 既定 | 意味 |
|---|---|---|
| `focus_areas` | `[]` | 重点領域 |
| `key_entities` | `[]` | 重要エンティティ |
| `ignore_sections` | `[]` | 無視するセクション |

### quality_standards（品質基準）

| キー | 既定 | 意味 |
|---|---|---|
| `min_answer_length` | `10` | 最小回答文字数 |
| `max_answer_length` | `200` | 最大回答文字数 |
| `require_source_span` | `True` | 出典必須 |
| `diversity_threshold` | `0.7` | 多様性閾値 |

### qa_characteristics（Q/A 特性）

| キー | 既定 |
|---|---|
| `question_types` | `["fact", "reason", "comparison", "application"]` |
| `difficulty_distribution` | `{"easy": 0.3, "medium": 0.5, "hard": 0.2}` |
| `answer_formats` | `["短答", "説明", "リスト", "段落"]` |
| `coverage_targets` | `{"minimum": 0.3, "optimal": 0.6, "comprehensive": 0.8}` |

> ⚠️ **これらの既定値を読んで動くコードは現在存在しない。** `QAGenerationConsiderations` は
> 定義されているだけで、生成パイプライン（`SmartQAGenerator` / `QAPipeline`）は
> 参照していない。実際の Q/A 数の決定は `SmartQAGenerator.COMBINED_PROMPT` の
> 基準（0〜5 個）が行う。

---

## 使用方法

### 基本

```python
from qa_generation.models import QAPair, QAPairsList

pair = QAPair(
    question="AES-256の鍵長は？",
    answer="256ビットです。",
    question_type="fact",
    difficulty="easy",
    source_span="256ビットの鍵長を持ちます",
)
lst = QAPairsList(qa_pairs=[pair])
print(lst.model_dump())
```

### 構造化出力のスキーマとして使う

```python
from helper.helper_llm import create_llm_client
from qa_generation.models import QAPairsList

client = create_llm_client(provider="ollama")
result = client.generate_structured(
    prompt="次のテキストからQ/Aを作ってください: ...",
    response_schema=QAPairsList,   # オブジェクト型（配列トップレベルは不可）
)
```

> 📌 Ollama の構造化出力は**オブジェクトのみ**を返せる。`List[QAPair]` を直接
> スキーマにはできないので、`QAPairsList` のように `qa_pairs` キーで包んだ形を使う
> （CLAUDE.md §3「Ollama 固有の落とし穴」）。
> `SmartQAGenerator` が使うのは本モジュールではなく、自前の `SmartQAResult` である。

### 範囲制約の確認

```python
from pydantic import ValidationError
from qa_generation.models import ChainOfThoughtQAPair

try:
    ChainOfThoughtQAPair(question="Q", answer="A", confidence=1.5)
except ValidationError as e:
    print(e)   # confidence は 0.0〜1.0
```

---

## エクスポート

```python
__all__ = [
    # 基本モデル
    "QAPair",
    "QAPairsList",
    # Chain-of-Thoughtモデル
    "ChainOfThoughtAnalysis",
    "ChainOfThoughtQAPair",
    "ChainOfThoughtResponse",
    # 拡張モデル
    "EnhancedQAPair",
    "EnhancedQAPairsList",
    # 設定モデル
    "QAGenerationConsiderations",
]
```

8 クラスすべてを公開しており、`qa_generation/__init__.py` も同じ 8 件を再エクスポートする。

---

## 関連モジュール

| モジュール | 関係 |
|---|---|
| `qa_generation/__init__.py` | 8 クラスすべてを再エクスポートする**唯一の import 元** |
| `models.py`（リポジトリ直下） | **別物の同名クラス群**。`services/qa_service.py` が使う現役の定義 |
| `helper/helper_rag_qa.py` | 統合元。同系のクラスが今も定義されている |
| `qa_generation/smart_qa_generator.py` | Q/A 生成の実装。**本モジュールを使わず** `SmartQAPair` / `SmartQAResult` を自前で持つ |

---

## 変更履歴

| Version | 日付 | 内容 |
|---|---|---|
| 1.0 | 2026-09-21 | 初版作成。実装（155 行・8 クラス）を読み起こしてフィールドと既定値を記述。あわせて**同名 `QAPair` が 3 箇所にある**こと、本モジュールの本番利用者が `qa_generation/__init__.py` 以外に無いことを実測して明記した。索引 `qa_generation/docs/README.md` §6 の残タスク 1（文書欠落）に対応 |

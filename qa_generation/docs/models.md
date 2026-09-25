# models.py - Q/A データモデル ドキュメント

**Version 1.5** | 最終更新: 2026-09-25

---

## 目次

1. [概要](#概要)
2. [アーキテクチャ構成図](#1-アーキテクチャ構成図)
3. [モジュール構成図](#2-モジュール構成図)
4. [QAPair の定義場所（直下 models.py へ一本化）](#3-qapair-の定義場所直下-modelspy-へ一本化)
5. [クラス・関数一覧表](#4-クラス関数一覧表)
6. [クラス・関数 IPO詳細](#5-クラス関数-ipo詳細)
7. [設定・定数（QAGenerationConsiderations の既定値）](#6-設定定数qagenerationconsiderations-の既定値)
8. [エクスポート](#7-エクスポート)
9. [関連モジュール](#8-関連モジュール)
10. [変更履歴](#9-変更履歴)

---

## 概要

`qa_generation/models.py` は、**Q/A 生成で使う Pydantic データモデルの定義だけを持つモジュール**です（`QAPair` だけは自前で定義せず、リポジトリ直下 `models.py` の定義を import して再エクスポートします。§3）。ロジックは一切持たず、LLM も I/O も呼びません。`helper/helper_rag_qa.py` に散在していた 8 クラスを 1 箇所へ集約する目的で作られました（モジュール docstring の「統合元」）。

### 主な責務

- Q/A ペアとそのリストのスキーマを定義する
- Chain-of-Thought 方式の分析結果・Q/A・レスポンスのスキーマを定義する
- LLM 品質向上用の簡易 Q/A スキーマを定義する
- Q/A 生成前のチェックリスト（文書特性・抽出要件・品質基準・Q/A 特性）を定義する

### 各責務対応のモジュール

| # | 責務 | 対応モジュール | 説明 |
|---|---|---|---|
| 1 | Q/A ペアとそのリストのスキーマを定義する | `QAPair` / `QAPairsList`（どちらも直下 `models.py` から再エクスポート） | 質問・回答＋メタ 8 項目 |
| 2 | Chain-of-Thought 方式の分析結果・Q/A・レスポンスのスキーマを定義する | `ChainOfThoughtAnalysis` / `ChainOfThoughtQAPair` / `ChainOfThoughtResponse` | 推論過程と信頼度を持つ |
| 3 | LLM 品質向上用の簡易 Q/A スキーマを定義する | `EnhancedQAPair` / `EnhancedQAPairsList` | 質問・回答だけの最小モデル |
| 4 | Q/A 生成前のチェックリスト（文書特性・抽出要件・品質基準・Q/A 特性）を定義する | `QAGenerationConsiderations` | 4 つの設定辞書（§6） |

### 主要機能一覧

| クラス | 説明 |
|---|---|
| `QAPair` | Q/A ペアの基本モデル（質問・回答＋メタ 8 項目）。**直下 `models.py` の定義を再エクスポート** |
| `QAPairsList` | `QAPair` のリスト。**直下 `models.py` の `QAPairsResponse`（別名 `QAPairsList`）を再エクスポート** |
| `ChainOfThoughtAnalysis` | CoT の文書分析結果 |
| `ChainOfThoughtQAPair` | 推論過程と信頼度を持つ Q/A ペア |
| `ChainOfThoughtResponse` | 分析＋Q/A リストの複合 |
| `EnhancedQAPair` | 質問・回答だけの最小モデル |
| `EnhancedQAPairsList` | `EnhancedQAPair` のリスト |
| `QAGenerationConsiderations` | Q/A 生成前のチェックリスト（4 つの設定辞書） |

---

## 1. アーキテクチャ構成図

### 1.1 システム全体構成

```mermaid
flowchart TB
    subgraph CALLER["呼び出し側"]
        INIT["qa_generation/__init__.py（再エクスポート）"]
        TEST["backend/tests（定義場所の固定）"]
    end
    subgraph TARGET["models.py"]
        M["Pydantic モデル 8 クラス"]
    end
    subgraph EXTERNAL["外部（LLM・Embedding・ファイル・基盤）"]
        PYD["pydantic.BaseModel"]
        ROOT["models.py（直下）の QAPair"]
    end
    INIT --> M
    TEST --> M
    M -->|"継承"| PYD
    M -->|"QAPair を import"| ROOT
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class INIT,TEST,M,PYD,ROOT default
style CALLER fill:#1a1a1a,stroke:#fff,color:#fff
style TARGET fill:#1a1a1a,stroke:#fff,color:#fff
style EXTERNAL fill:#1a1a1a,stroke:#fff,color:#fff
```

### 1.2 データフロー

1. 利用側が `qa_generation`（または `qa_generation.models`）からモデルを import する
2. Pydantic がフィールドの型・範囲制約を検証してインスタンスを作る
3. 構造化出力のスキーマとして使う場合は `model_json_schema()` で JSON Schema を得る
4. ※ ロジック・I/O・LLM 呼び出しは持たない

---

## 2. モジュール構成図

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
    Root["models.py（直下）の QAPair"]
    QAPair -.->|"import して再エクスポート"| Root
    QAPairsList -->|"List[QAPair]"| QAPair
    QAPairsList -.->|"import して再エクスポート（= QAPairsResponse）"| Root
    CoTResp -->|"analysis"| Analysis
    CoTResp -->|"List[ChainOfThoughtQAPair]"| CoTPair
    EList -->|"List[EnhancedQAPair]"| EPair
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class QAPair,QAPairsList,Analysis,CoTPair,CoTResp,EPair,EList,Consider,Root default
style Basic fill:#1a1a1a,stroke:#fff,color:#fff
style CoT fill:#1a1a1a,stroke:#fff,color:#fff
style Enh fill:#1a1a1a,stroke:#fff,color:#fff
style Cfg fill:#1a1a1a,stroke:#fff,color:#fff
```

`QAGenerationConsiderations` はどのモデルも参照せず、独立している。

---

## 3. QAPair の定義場所（直下 models.py へ一本化）

**2026-09-25 に、`QAPair` をリポジトリ直下 `models.py` の定義へ一本化した**（grace_v2 と同じ変更）。
本モジュールは自前の `class QAPair` を持たず、`from models import QAPair` して再エクスポートする。

| 定義場所 | 状態 | フィールド |
|---|---|---|
| **`models.py`（リポジトリ直下）** | **正本**。`services/qa_service.py` が使う | `question` / `answer` / `question_type` / `difficulty_level` ほか計 10 項目（§5.2） |
| `qa_generation/models.py`（本モジュール） | 正本を import して再エクスポートするだけ。`qa_generation.QAPair is models.QAPair` | （正本と同じ） |
| `helper/helper_rag_qa.py` | 旧定義（`difficulty` / `source_span`）を**削除**し、正本を import して使う（2026-09-25） | （正本と同じ） |

### 3.1 一本化した理由

以前は本モジュールと `helper/helper_rag_qa.py` にも別定義（`difficulty` / `source_span` を持つ）があり、
`from models import QAPair` と `from qa_generation import QAPair` が**別のクラス**を指していた。
Pydantic は知らない項目名を黙って無視するため、取り違えると値がエラーも出ずに消えていた（実測）。

```python
from models import QAPair as A              # 正本
A(question="Q", answer="A", difficulty="hard").difficulty_level   # → "medium"（"hard" は消える）
```

2026-09-21 には「統合しない」（公開 API なので破壊的変更になる）と決めていたが、本モジュール側の定義には
本番の利用者がいなかった（`qa_generation/__init__.py` の再エクスポートのみ・grep 実測）ため、削除して正本へ寄せた。
**`from qa_generation import QAPair` という import 文はそのまま使える**が、
指すクラスのフィールドは正本のもの（`difficulty` → `difficulty_level`、`source_span` は無い）に変わった。

同日、`helper/helper_rag_qa.py` の旧定義も削除した。旧定義を使っていたのは同ファイルの
`LLMBasedQAGenerator`（構造化出力のスキーマ `QAPairsList` の要素）だけで、その出力を読む
`HybridQAGenerator` は `question` / `answer` しか見ていない（grep 実測）。プロンプトが指示する項目名も
`difficulty` / `source_span` から正本の `difficulty_level` へ揃えた（スキーマとプロンプトの食い違いを防ぐため）。

### 3.2 QAPairsList も一本化した

同じ 2026-09-25 に、`QAPairsList` も直下 `models.py` の定義へ一本化した。
`QAPairsList` は直下 `models.py` の `QAPairsResponse` の**別名**（`QAPairsList = QAPairsResponse`）で、
本モジュールと `helper/helper_rag_qa.py` はそれぞれ同名のクラスを自前で持っていた。

| 場所 | 以前 | 現在 |
|---|---|---|
| `models.py`（直下） | `QAPairsResponse` ＋別名 `QAPairsList`（`qa_pairs` の既定は空リスト） | **正本**（`services/qa_service.py` の構造化出力スキーマ） |
| `qa_generation/models.py`（本モジュール） | 自前の `class QAPairsList`（同じ形） | 正本を import して再エクスポート |
| `helper/helper_rag_qa.py` | 自前の `class QAPairsList`（`qa_pairs` が**必須**） | 正本を import（`LLMBasedQAGenerator` の構造化出力スキーマ） |

`helper_rag_qa.py` 側だけ `qa_pairs` が必須だったが、`qa_pairs` の無い応答は以前は `ValidationError` →
呼び出し側の `except` で `[]`、現在は既定の空リスト → `[]` となり、**結果は変わらない**。

### 3.3 固定しているテスト

`backend/tests/qa_generation/test_qa_pair_definitions.py`（6 件）が次を確かめる。

| テスト | 内容 |
|---|---|
| `test_package_qa_pair_is_the_top_level_definition` | `qa_generation.QAPair` / `qa_generation.models.QAPair` が正本そのものであること |
| `test_qa_generation_models_does_not_define_its_own_qa_pair` | 本モジュールに `class QAPair` を書き戻していないこと（`ast` で静的検査） |
| `test_package_qa_pairs_list_holds_the_top_level_qa_pair` | `QAPairsList` の要素も正本の `QAPair` になること |
| `test_helper_rag_qa_uses_the_top_level_definition` | `helper_rag_qa.py` に `class QAPair` が無く、`from models import QAPair` していること（`spacy` 依存を避けて `ast` で読む） |
| `test_qa_pairs_list_is_a_single_definition` | `models.QAPairsList` / `qa_generation.QAPairsList` / `qa_generation.models.QAPairsList` がすべて `models.QAPairsResponse` そのものであること |
| `test_no_module_defines_its_own_qa_pairs_list` | 本モジュールと `helper_rag_qa.py` に `class QAPairsList` が無く、`helper_rag_qa.py` が `models` から import していること（`ast`） |

---

## 4. クラス・関数一覧表

| クラス | 基底 | 行 | 必須フィールド |
|---|---|---:|---|
| `QAPair` | `BaseModel` | 33（`from models import QAPair, QAPairsList`。定義は直下 `models.py` 27 行目） | `question` / `answer` |
| `QAPairsList` | `BaseModel` | 33（同上。定義は直下 `models.py` 70 行目の `QAPairsResponse`、別名は 234 行目） | なし（既定は空リスト） |
| `ChainOfThoughtAnalysis` | `BaseModel` | 39 | なし |
| `ChainOfThoughtQAPair` | `BaseModel` | 46 | `question` / `answer` |
| `ChainOfThoughtResponse` | `BaseModel` | 54 | なし |
| `EnhancedQAPair` | `BaseModel` | 64 | `question` / `answer` |
| `EnhancedQAPairsList` | `BaseModel` | 70 | なし |
| `QAGenerationConsiderations` | `BaseModel` | 79 | なし（4 辞書すべて既定値あり） |

---

## 5. クラス・関数 IPO詳細

> データモデルのみのモジュールのため、IPO の代わりに各クラスのフィールド定義を示す。

### 5.1 使用例

#### 5.1.1 基本

```python
from qa_generation.models import QAPair, QAPairsList

pair = QAPair(
    question="AES-256の鍵長は？",
    answer="256ビットです。",
    question_type="fact",
    difficulty_level="easy",        # 難易度は difficulty_level（difficulty ではない）
)
lst = QAPairsList(qa_pairs=[pair])
print(lst.model_dump())
```

#### 5.1.2 構造化出力のスキーマとして使う

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

#### 5.1.3 範囲制約の確認

```python
from pydantic import ValidationError
from qa_generation.models import ChainOfThoughtQAPair

try:
    ChainOfThoughtQAPair(question="Q", answer="A", confidence=1.5)
except ValidationError as e:
    print(e)   # confidence は 0.0〜1.0
```

### 5.2 QAPair

Q/A ペアの基本モデル。**定義はリポジトリ直下 `models.py`**（本モジュールは再エクスポートのみ・§3）。

| フィールド | 型 | 既定 | 説明 |
|---|---|---|---|
| `question` | `str` | **必須** | 質問文 |
| `answer` | `str` | **必須** | 回答文 |
| `question_type` | `str` | `"fact"` | 質問タイプ（`fact` / `reason` / `comparison` / `application` / `definition` / `process` / `evaluation`） |
| `difficulty_level` | `Optional[str]` | `"medium"` | 難易度（`easy` / `medium` / `hard`） |
| `question_category` | `Optional[str]` | `"understanding"` | 質問カテゴリ（`basic` / `understanding` / `application`） |
| `source_chunk_id` | `Optional[str]` | `None` | ソースチャンク ID |
| `dataset_type` | `Optional[str]` | `None` | データセットタイプ |
| `auto_generated` | `bool` | `False` | 自動生成フラグ |
| `confidence_score` | `Optional[float]` | `None` | 生成の確信度（0.0〜1.0） |
| `quality_score` | `Optional[float]` | `None` | 品質スコア（0.0〜1.0） |

> 📌 `question_type` / `difficulty_level` / `question_category` は**自由文字列**である。`Enum` でも `Literal` でもないため、
> 説明にない値を入れてもバリデーションは通る。`confidence_score` / `quality_score` にも範囲制約は無い（説明上の目安）。
>
> ⚠️ **知らない項目名は黙って無視される**（Pydantic の既定）。`difficulty="hard"` や `source_span=...` を
> 渡してもエラーにならず、値は保存されない（§3.1）。

### 5.3 QAPairsList

**定義は直下 `models.py` の `QAPairsResponse`**（`QAPairsList` はその別名。本モジュールは再エクスポートのみ・§3.2）。

| フィールド | 型 | 既定 | 説明 |
|---|---|---|---|
| `qa_pairs` | `List[QAPair]` | 空リスト | Q/A ペアのリスト |

### 5.4 ChainOfThoughtAnalysis

| フィールド | 型 | 既定 | 説明 |
|---|---|---|---|
| `main_topics` | `List[str]` | 空リスト | 主要トピック |
| `key_concepts` | `List[str]` | 空リスト | 重要概念 |
| `information_density` | `str` | `"medium"` | 情報密度（`low` / `medium` / `high`） |

### 5.5 ChainOfThoughtQAPair

| フィールド | 型 | 既定 | 制約 | 説明 |
|---|---|---|---|---|
| `question` | `str` | **必須** | — | 質問文 |
| `answer` | `str` | **必須** | — | 回答文 |
| `reasoning` | `str` | `""` | — | 推論過程 |
| `confidence` | `float` | `0.8` | `0.0 <= x <= 1.0` | 信頼度スコア |

`confidence` は**このモジュールで唯一の範囲制約付きフィールド**で、範囲外は
`ValidationError` になる。

### 5.6 ChainOfThoughtResponse

| フィールド | 型 | 既定 | 説明 |
|---|---|---|---|
| `analysis` | `ChainOfThoughtAnalysis` | 既定インスタンス | 分析結果 |
| `qa_pairs` | `List[ChainOfThoughtQAPair]` | 空リスト | Q/A ペア |

### 5.7 EnhancedQAPair / EnhancedQAPairsList

| クラス | フィールド | 型 | 既定 |
|---|---|---|---|
| `EnhancedQAPair` | `question` / `answer` | `str` | **必須** |
| `EnhancedQAPairsList` | `qa_pairs` | `List[EnhancedQAPair]` | 空リスト |

「LLM 品質向上用」の最小スキーマで、メタ情報を持たない。

---

## 6. 設定・定数（QAGenerationConsiderations の既定値）

4 つの辞書フィールドをまとめて持つ設定モデル。いずれも `default_factory` で生成されるため、
インスタンスごとに独立した辞書になる（クラス間で共有されない）。

### 6.1 document_characteristics（文書特性）

| キー | 既定 | 意味 |
|---|---|---|
| `domain` | `"general"` | 専門分野 |
| `text_type` | `"informative"` | 説明文・対話 など |
| `complexity` | `"medium"` | 複雑度 |
| `length` | `"medium"` | 文書長 |

### 6.2 extraction_requirements（抽出要件）

| キー | 既定 | 意味 |
|---|---|---|
| `focus_areas` | `[]` | 重点領域 |
| `key_entities` | `[]` | 重要エンティティ |
| `ignore_sections` | `[]` | 無視するセクション |

### 6.3 quality_standards（品質基準）

| キー | 既定 | 意味 |
|---|---|---|
| `min_answer_length` | `10` | 最小回答文字数 |
| `max_answer_length` | `200` | 最大回答文字数 |
| `require_source_span` | `True` | 出典必須 |
| `diversity_threshold` | `0.7` | 多様性閾値 |

### 6.4 qa_characteristics（Q/A 特性）

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

## 7. エクスポート

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
`QAPair` と `QAPairsList` は直下 `models.py` から import したものを公開している（§3）。

---

## 8. 関連モジュール

| モジュール | 関係 |
|---|---|
| `qa_generation/__init__.py` | 8 クラスすべてを再エクスポートする |
| `models.py`（リポジトリ直下） | **`QAPair` の正本**。本モジュールはここから import する。`services/qa_service.py` も使う |
| `helper/helper_rag_qa.py` | 統合元。`QAPair` / `QAPairsList` は正本を import して使う（旧定義は削除済み）。`EnhancedQAPairsList` ほか同系のクラスは今も定義されている |
| `qa_generation/smart_qa_generator.py` | Q/A 生成の実装。**本モジュールを使わず** `SmartQAPair` / `SmartQAResult` を自前で持つ |

---

## 9. 変更履歴

| Version | 日付 | 内容 |
|---|---|---|
| 1.5 | 2026-09-25 | **`QAPairsList` も直下 `models.py` の定義（`QAPairsResponse` の別名）へ一本化**（grace_v2 と同じ変更）。本モジュールと `helper/helper_rag_qa.py` の同名クラスを削除したのに追随し、§3.2（新設）・§3.3 のテスト一覧（6 件）・§1・§2 の図・§4 の行番号・§5.3・§7・§8 を更新 |
| 1.4 | 2026-09-25 | `helper/helper_rag_qa.py` の旧 `QAPair` も削除し、定義は直下 `models.py` の 1 つだけになった（grace_v2 と同じ変更）。§3 の表・§3.1・§3.2 のテスト一覧・§8 を更新 |
| 1.3 | 2026-09-25 | **`QAPair` を直下 `models.py` の定義へ一本化**（v1.1 の「統合しない」判断を変更。grace_v2 と同じ変更）。本モジュールの `class QAPair`（`difficulty` / `source_span`）を削除し、正本を import して再エクスポートする形にしたのに追随。§3 を「定義場所（一本化）」に書き直し（理由・テスト）、§5.1.1 の使用例と §5.2 のフィールド表を正本（10 項目）へ、§1・§2 の図、§4 の行番号、§7・§8 を更新 |
| 1.2 | 2026-09-24 | 基本フォーマット `a_class_method_md_format.md` の章構成へ組み替え。概要に「主な責務」と「各責務対応のモジュール」（1:1）を置き、`## 1. アーキテクチャ構成図`（3 層＋データフロー）を新設。既存の構成図は `## 2. モジュール構成図` へ、使用方法は IPO 詳細の冒頭（`### 5.1 使用例`）へ移した。固有の解説章（「⚠️ 同名クラスが 3 箇所にある」）は §1.3 に従い一覧表の前に置き、章・小節に番号を振った。本文の内容は変えていない |
| 1.1 | 2026-09-21 | 3 重定義の扱いを**「統合しない」で決着**。3 箇所の docstring に相互参照の警告を入れ、差分を固定する `test_qa_pair_definitions.py`（4 件）を追加した |
| 1.0 | 2026-09-21 | 初版作成。実装（155 行・8 クラス）を読み起こしてフィールドと既定値を記述。あわせて**同名 `QAPair` が 3 箇所にある**こと、本モジュールの本番利用者が `qa_generation/__init__.py` 以外に無いことを実測して明記した。索引 `qa_generation/docs/README.md` §6 の残タスク 1（文書欠落）に対応 |

# json_service.py - JSON処理サービス ドキュメント

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

`json_service.py`は、安全なJSONシリアライズ・デシリアライズおよびJSONファイルの読み書きを提供するユーティリティモジュールです。Pydanticモデルや`datetime`、`bytes`、`set`など標準の`json`モジュールでは直接処理できないオブジェクトを安全に変換し、例外発生時にもフォールバック値を返すことで、Anthropic Claude応答ログやQ&A生成結果などの永続化を堅牢に行います。

本モジュールは元々`helper_api.py`に分散していたJSON関連処理（`safe_json_serializer` / `safe_json_dumps` / `load_json_file` / `save_json_file`）を集約・統合したサービスです。

### 主な責務

- 標準で処理できないオブジェクトのJSON互換変換（カスタムシリアライザー）
- 例外安全なJSON文字列化・パース
- JSONファイルの読み込み・保存（ディレクトリ自動生成）
- 複数JSONファイルのマージ
- JSON妥当性検証および整形・コンパクト出力

### 各責務対応のモジュール

| # | 責務 | 対応モジュール | 説明 |
|---|------|--------------|------|
| 1 | 標準で処理できないオブジェクトのJSON互換変換 | `json_service.py` | `safe_json_serializer()`がPydantic/datetime/bytes/set等を変換 |
| 2 | 例外安全なJSON文字列化・パース | `json_service.py` | `safe_json_dumps()` / `safe_json_loads()`がエラー時にフォールバック |
| 3 | JSONファイルの読み込み・保存 | `json_service.py` | `load_json_file()` / `save_json_file()`がI/Oを管理 |
| 4 | 複数JSONファイルのマージ | `json_service.py` | `merge_json_files()`が複数ファイルを統合 |
| 5 | JSON妥当性検証および整形・コンパクト出力 | `json_service.py` | `is_valid_json()` / `pretty_print_json()` / `compact_json()` |

### 主要機能一覧

| 機能 | 説明 |
|------|------|
| `safe_json_serializer()` | 標準で処理できないオブジェクトをJSON互換形式に変換 |
| `safe_json_dumps()` | 例外安全なJSON文字列化（フォールバック付き） |
| `safe_json_loads()` | 例外安全なJSONパース（デフォルト値付き） |
| `load_json_file()` | JSONファイルを読み込む（エラー時None） |
| `save_json_file()` | JSONファイルを保存する（ディレクトリ自動生成） |
| `load_json_file_or_default()` | JSONファイル読み込み（存在しない場合デフォルト値） |
| `merge_json_files()` | 複数JSONファイルをマージ |
| `is_valid_json()` | 文字列が有効なJSONか検証 |
| `pretty_print_json()` | JSONを整形して文字列化（indent=4） |
| `compact_json()` | JSONをコンパクトに文字列化（改行・空白なし） |

---

## 1. アーキテクチャ構成図

### 1.1 システム全体構成

```mermaid
flowchart TB
    subgraph CLIENT["クライアント層"]
        HELPER["helper_api.py"]
        TASKS["celery_tasks.py"]
        SERVICES["各種サービスモジュール"]
    end

    subgraph MODULE["json_service.py"]
        SER["シリアライザー群"]
        FILE["ファイル操作群"]
        UTIL["ユーティリティ群"]
    end

    subgraph EXTERNAL["外部サービス層"]
        STDJSON["json 標準ライブラリ"]
        FS["ファイルシステム"]
        LOG["logging"]
    end

    HELPER --> MODULE
    TASKS --> MODULE
    SERVICES --> MODULE
    SER --> STDJSON
    FILE --> FS
    FILE --> STDJSON
    UTIL --> STDJSON
    MODULE --> LOG
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class HELPER,TASKS,SERVICES,SER,FILE,UTIL,STDJSON,FS,LOG default
style CLIENT fill:#1a1a1a,stroke:#fff,color:#fff
style MODULE fill:#1a1a1a,stroke:#fff,color:#fff
style EXTERNAL fill:#1a1a1a,stroke:#fff,color:#fff
```

### 1.2 データフロー

1. クライアント層（`helper_api.py`等）がJSON処理を要求
2. シリアライザー群が標準で処理できないオブジェクトを変換
3. ファイル操作群がファイルシステムへ読み書き
4. 例外発生時は`logging`へ記録し、フォールバック値または`None`を返却

---

## 2. モジュール構成図

### 2.1 内部モジュール構成

```mermaid
flowchart TB
    subgraph SERIALIZER["シリアライザー"]
        S1["safe_json_serializer()"]
        S2["safe_json_dumps()"]
        S3["safe_json_loads()"]
    end

    subgraph FILEOPS["ファイル操作"]
        F1["load_json_file()"]
        F2["save_json_file()"]
        F3["load_json_file_or_default()"]
        F4["merge_json_files()"]
    end

    subgraph UTILITY["ユーティリティ"]
        U1["is_valid_json()"]
        U2["pretty_print_json()"]
        U3["compact_json()"]
    end

    S1 --> S2
    S2 --> F2
    F1 --> F3
    F1 --> F4
    F2 --> F4
    S2 --> U2
    S2 --> U3
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class S1,S2,S3,F1,F2,F3,F4,U1,U2,U3 default
style SERIALIZER fill:#1a1a1a,stroke:#fff,color:#fff
style FILEOPS fill:#1a1a1a,stroke:#fff,color:#fff
style UTILITY fill:#1a1a1a,stroke:#fff,color:#fff
```

### 2.2 外部依存関係

| ライブラリ | バージョン | 用途 |
|-----------|-----------|------|
| `json` | 標準 | JSONシリアライズ・パース |
| `logging` | 標準 | エラー・警告ログ出力 |
| `datetime` | 標準 | `datetime`オブジェクトのISO形式変換 |
| `pathlib` | 標準 | 出力ディレクトリの自動生成 |
| `typing` | 標準 | 型ヒント |

### 2.3 内部依存モジュール

なし（標準ライブラリのみで完結）

---

## 3. クラス・関数一覧表

> 本モジュールにクラスは定義されていません。関数のみで構成されます。

### 3.1 関数一覧（カテゴリ別）

#### シリアライザー

| 関数名 | 概要 |
|-------|------|
| `safe_json_serializer(obj)` | 標準で処理できないオブジェクトをJSON互換形式に変換 |
| `safe_json_dumps(data, **kwargs)` | 例外安全なJSON文字列化 |
| `safe_json_loads(data, default)` | 例外安全なJSONパース |

#### ファイル操作

| 関数名 | 概要 |
|-------|------|
| `load_json_file(filepath)` | JSONファイルを読み込む |
| `save_json_file(data, filepath)` | JSONファイルを保存する |
| `load_json_file_or_default(filepath, default)` | 読み込み（存在しない場合デフォルト値） |
| `merge_json_files(filepaths, output_path)` | 複数JSONファイルをマージ |

#### ユーティリティ

| 関数名 | 概要 |
|-------|------|
| `is_valid_json(data)` | 文字列が有効なJSONか検証 |
| `pretty_print_json(data)` | JSONを整形して文字列化 |
| `compact_json(data)` | JSONをコンパクトに文字列化 |

---

## 4. クラス・関数 IPO詳細

### 4.1 シリアライザー関数

#### `safe_json_serializer`

**概要**: 標準の`json`モジュールでは処理できないオブジェクト（Pydanticモデル・`datetime`・`bytes`・`set`・Anthropic Claude/OpenAI互換のUsageオブジェクト等）をJSON互換形式へ変換するカスタムシリアライザー。`json.dumps`の`default`引数として使用される。

```python
def safe_json_serializer(obj: Any) -> Any
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `obj` | Any | - | シリアライズ対象オブジェクト |

| 項目 | 内容 |
|------|------|
| **Input** | `obj: Any` |
| **Process** | 1. `model_dump()`を持つ場合は呼び出し（Pydantic）<br>2. `dict()`を持つ場合は呼び出し<br>3. `datetime`は`isoformat()`へ変換<br>4. `prompt_tokens`/`completion_tokens`を持つ場合はトークン辞書へ変換<br>5. `bytes`はUTF-8デコード（失敗時hex）<br>6. `set`はリスト化<br>7. それ以外は`str()`で文字列化 |
| **Output** | `Any`: JSON互換形式に変換されたオブジェクト |

**戻り値例**:
```python
{
    "prompt_tokens": 120,
    "completion_tokens": 45,
    "total_tokens": 165
}
```

```python
# 使用例
from datetime import datetime
from services.json_service import safe_json_serializer

result = safe_json_serializer(datetime(2026, 6, 17, 10, 0, 0))
print(result)
# 出力: 2026-06-17T10:00:00
```

#### `safe_json_dumps`

**概要**: 例外安全なJSON文字列化関数。`safe_json_serializer`を`default`に指定し、`ensure_ascii=False`・`indent=2`をデフォルトとする。失敗時は文字列化フォールバックを行う。

```python
def safe_json_dumps(data: Any, **kwargs) -> str
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `data` | Any | - | シリアライズ対象データ |
| `**kwargs` | Any | - | `json.dumps`へ渡す追加引数 |

| 項目 | 内容 |
|------|------|
| **Input** | `data: Any`, `**kwargs` |
| **Process** | 1. デフォルト引数（`ensure_ascii=False`, `indent=2`, `default=safe_json_serializer`）を構築<br>2. `kwargs`で上書き<br>3. `json.dumps`を実行<br>4. 例外時はエラーログ出力後、文字列化してフォールバック |
| **Output** | `str`: JSON文字列 |

**戻り値例**:
```python
'{\n  "name": "テスト",\n  "count": 3\n}'
```

```python
# 使用例
from services.json_service import safe_json_dumps

text = safe_json_dumps({"name": "テスト", "count": 3})
print(text)
# 出力:
# {
#   "name": "テスト",
#   "count": 3
# }
```

#### `safe_json_loads`

**概要**: 例外安全なJSONパース関数。パース失敗時はエラーログを出力し、指定されたデフォルト値を返す。

```python
def safe_json_loads(data: str, default: Any = None) -> Any
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `data` | str | - | JSON文字列 |
| `default` | Any | None | パースエラー時のデフォルト値 |

| 項目 | 内容 |
|------|------|
| **Input** | `data: str`, `default: Any = None` |
| **Process** | 1. `json.loads`を実行<br>2. `JSONDecodeError`時はエラーログ出力し`default`を返却<br>3. その他例外時もエラーログ出力し`default`を返却 |
| **Output** | `Any`: パース結果（エラー時は`default`） |

**戻り値例**:
```python
{"status": "ok", "value": 42}
```

```python
# 使用例
from services.json_service import safe_json_loads

data = safe_json_loads('{"status": "ok", "value": 42}')
print(data["value"])
# 出力: 42

fallback = safe_json_loads("invalid json", default={})
print(fallback)
# 出力: {}
```

### 4.2 ファイル操作関数

#### `load_json_file`

**概要**: JSONファイルを読み込み、辞書として返す。ファイル未存在・デコードエラー・その他例外時はログを出力し`None`を返す。

```python
def load_json_file(filepath: str) -> Optional[Dict[str, Any]]
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `filepath` | str | - | 読み込むファイルパス |

| 項目 | 内容 |
|------|------|
| **Input** | `filepath: str` |
| **Process** | 1. UTF-8でファイルを開く<br>2. `json.load`で読み込み<br>3. `FileNotFoundError`時は警告ログ出力し`None`<br>4. `JSONDecodeError`/その他例外時はエラーログ出力し`None` |
| **Output** | `Optional[Dict[str, Any]]`: 読み込んだデータ（エラー時`None`） |

**戻り値例**:
```python
{
    "version": "1.0",
    "items": [1, 2, 3]
}
```

```python
# 使用例
from services.json_service import load_json_file

config = load_json_file("config.json")
if config is not None:
    print(config["version"])
# 出力: 1.0
```

#### `save_json_file`

**概要**: データをJSONファイルへ保存する。親ディレクトリが存在しない場合は自動生成し、`safe_json_dumps`を用いて安全に書き込む。

```python
def save_json_file(data: Dict[str, Any], filepath: str) -> bool
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `data` | Dict[str, Any] | - | 保存するデータ |
| `filepath` | str | - | 保存先ファイルパス |

| 項目 | 内容 |
|------|------|
| **Input** | `data: Dict[str, Any]`, `filepath: str` |
| **Process** | 1. 親ディレクトリを`mkdir(parents=True, exist_ok=True)`で作成<br>2. `safe_json_dumps`でJSON文字列化<br>3. UTF-8でファイルへ書き込み<br>4. 例外時はエラーログ出力し`False` |
| **Output** | `bool`: 成功時`True`、失敗時`False` |

**戻り値例**:
```python
True
```

```python
# 使用例
from services.json_service import save_json_file

ok = save_json_file({"result": "success"}, "output/result.json")
print(ok)
# 出力: True
```

#### `load_json_file_or_default`

**概要**: JSONファイルを読み込み、存在しない・読み込み失敗時はデフォルト値を返すラッパー関数。

```python
def load_json_file_or_default(filepath: str, default: Any = None) -> Any
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `filepath` | str | - | 読み込むファイルパス |
| `default` | Any | None | ファイル未存在時のデフォルト値 |

| 項目 | 内容 |
|------|------|
| **Input** | `filepath: str`, `default: Any = None` |
| **Process** | 1. `load_json_file`を呼び出し<br>2. 結果が`None`でなければそれを返却<br>3. `None`の場合は`default`を返却 |
| **Output** | `Any`: 読み込んだデータまたは`default` |

**戻り値例**:
```python
{"items": []}
```

```python
# 使用例
from services.json_service import load_json_file_or_default

data = load_json_file_or_default("missing.json", default={"items": []})
print(data)
# 出力: {'items': []}
```

#### `merge_json_files`

**概要**: 複数のJSONファイルを読み込み、辞書を順次`update`してマージする。`output_path`指定時はマージ結果を保存する。

```python
def merge_json_files(filepaths: list, output_path: str = None) -> Dict[str, Any]
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `filepaths` | list | - | マージするファイルパスのリスト |
| `output_path` | str | None | 出力先パス（省略時は保存しない） |

| 項目 | 内容 |
|------|------|
| **Input** | `filepaths: list`, `output_path: str = None` |
| **Process** | 1. 空辞書を初期化<br>2. 各ファイルを`load_json_file`で読み込み<br>3. データが存在すれば`update`でマージ<br>4. `output_path`指定時は`save_json_file`で保存 |
| **Output** | `Dict[str, Any]`: マージされたデータ |

**戻り値例**:
```python
{
    "a": 1,
    "b": 2,
    "c": 3
}
```

```python
# 使用例
from services.json_service import merge_json_files

merged = merge_json_files(["part1.json", "part2.json"], output_path="all.json")
print(merged)
# 出力: {'a': 1, 'b': 2, 'c': 3}
```

### 4.3 ユーティリティ関数

#### `is_valid_json`

**概要**: 文字列が有効なJSONかどうかを検証する。パース可能なら`True`、`JSONDecodeError`または`TypeError`時は`False`を返す。

```python
def is_valid_json(data: str) -> bool
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `data` | str | - | チェック対象文字列 |

| 項目 | 内容 |
|------|------|
| **Input** | `data: str` |
| **Process** | 1. `json.loads`を実行<br>2. 成功時`True`<br>3. `JSONDecodeError`/`TypeError`時`False` |
| **Output** | `bool`: 有効なJSONの場合`True` |

**戻り値例**:
```python
True
```

```python
# 使用例
from services.json_service import is_valid_json

print(is_valid_json('{"x": 1}'))
# 出力: True
print(is_valid_json("not json"))
# 出力: False
```

#### `pretty_print_json`

**概要**: データをインデント幅4で整形したJSON文字列にする。内部で`safe_json_dumps`を使用するため安全に処理される。

```python
def pretty_print_json(data: Any) -> str
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `data` | Any | - | 整形対象データ |

| 項目 | 内容 |
|------|------|
| **Input** | `data: Any` |
| **Process** | `safe_json_dumps(data, indent=4)`を呼び出し |
| **Output** | `str`: 整形されたJSON文字列 |

**戻り値例**:
```python
'{\n    "key": "value"\n}'
```

```python
# 使用例
from services.json_service import pretty_print_json

print(pretty_print_json({"key": "value"}))
# 出力:
# {
#     "key": "value"
# }
```

#### `compact_json`

**概要**: データを改行・空白なしのコンパクトなJSON文字列にする。区切り文字に`(',', ':')`を使用し最小サイズで出力する。

```python
def compact_json(data: Any) -> str
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `data` | Any | - | 対象データ |

| 項目 | 内容 |
|------|------|
| **Input** | `data: Any` |
| **Process** | `safe_json_dumps(data, indent=None, separators=(',', ':'))`を呼び出し |
| **Output** | `str`: コンパクトなJSON文字列 |

**戻り値例**:
```python
'{"a":1,"b":2}'
```

```python
# 使用例
from services.json_service import compact_json

print(compact_json({"a": 1, "b": 2}))
# 出力: {"a":1,"b":2}
```

---

## 5. 設定・定数

本モジュールには公開された設定辞書・定数は定義されていません。

`safe_json_dumps`の内部デフォルト引数として、以下が暗黙的に使用されます。

| キー | デフォルト値 | 説明 |
|-----|-------------|------|
| `ensure_ascii` | `False` | 日本語等の非ASCII文字をエスケープしない |
| `indent` | `2` | インデント幅 |
| `default` | `safe_json_serializer` | カスタムシリアライザー |

---

## 6. 使用例

### 6.1 基本的なワークフロー

```python
from services.json_service import (
    safe_json_dumps,
    safe_json_loads,
    save_json_file,
    load_json_file,
)

# 1. データを安全にJSON文字列化
data = {"query": "RAGとは", "score": 0.92}
json_str = safe_json_dumps(data)

# 2. ファイルへ保存（親ディレクトリ自動生成）
save_json_file(data, "output/qa_result.json")

# 3. ファイルから読み込み
loaded = load_json_file("output/qa_result.json")

# 4. 文字列をパース
parsed = safe_json_loads(json_str, default={})
print(f"処理完了: {parsed['query']}")
```

### 6.2 応用的なワークフロー

```python
from services.json_service import merge_json_files, pretty_print_json, is_valid_json

# 複数の中間結果をマージして1ファイルに統合
merged = merge_json_files(
    ["chunk_part1.json", "chunk_part2.json", "chunk_part3.json"],
    output_path="chunks_merged.json",
)

# 整形して確認
if is_valid_json(pretty_print_json(merged)):
    print(pretty_print_json(merged))
```

---

## 7. エクスポート

`__all__`で公開される要素：

```python
__all__ = [
    # シリアライザー
    "safe_json_serializer",
    "safe_json_dumps",
    "safe_json_loads",
    # ファイル操作
    "load_json_file",
    "save_json_file",
    "load_json_file_or_default",
    "merge_json_files",
    # ユーティリティ
    "is_valid_json",
    "pretty_print_json",
    "compact_json",
]
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
    MODULE["json_service.py"]

    subgraph STDLIB["Python標準ライブラリ"]
        JSON["json"]
        LOGGING["logging"]
        DATETIME["datetime.datetime"]
        PATHLIB["pathlib.Path"]
        TYPING["typing"]
    end

    MODULE --> JSON
    MODULE --> LOGGING
    MODULE --> DATETIME
    MODULE --> PATHLIB
    MODULE --> TYPING

    JSON --> JDUMP["json.dumps"]
    JSON --> JLOAD["json.loads / json.load"]
    PATHLIB --> MKDIR["Path.mkdir"]
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class MODULE,JSON,LOGGING,DATETIME,PATHLIB,TYPING,JDUMP,JLOAD,MKDIR default
style STDLIB fill:#1a1a1a,stroke:#fff,color:#fff
```

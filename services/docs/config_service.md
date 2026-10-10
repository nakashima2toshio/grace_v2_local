# config_service.py - 設定管理サービス ドキュメント

**Version 1.6** | 最終更新: 2026-10-10

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

`config_service.py`は、YAMLベースのアプリケーション設定を一元管理するサービスモジュールです。設定ファイルの読み込み、環境変数によるオーバーライド、ドット区切りキーによるキャッシュ付き設定値取得、およびロガーの初期化を担います。`ConfigManager`はシングルトンとして実装され、アプリケーション全体で単一の設定状態を共有します。

統合元: `helper_api.py::ConfigManager`

### 主な責務

- YAML設定ファイル（`config.yml`）の読み込みとフォールバック（デフォルト設定）の提供
- 環境変数（`GOOGLE_API_KEY` / `LOG_LEVEL` / `DEBUG_MODE` / `LLM_PROVIDER`）による設定オーバーライド
- ドット区切りキーによるキャッシュ付き設定値の取得・更新
- ロガー（`Gemini_helper`）の初期化とコンソール／ファイルハンドラーの設定
- 設定のファイル保存・再読み込み・全件取得などの管理操作

### 各責務対応のモジュール

| # | 責務 | 対応モジュール | 説明 |
|---|------|--------------|------|
| 1 | YAML設定の読み込みとフォールバック | `config_service.py` | `_load_config()` / `_get_default_config()` |
| 2 | 環境変数によるオーバーライド | `config_service.py` | `_apply_env_overrides()` |
| 3 | 設定値の取得・更新 | `config_service.py` | `ConfigManager.get()` / `ConfigManager.set()` |
| 4 | ロガーの初期化 | `config_service.py` | `_setup_logger()` |
| 5 | 設定の保存・再読み込み・全件取得 | `config_service.py` | `save()` / `reload()` / `get_all()` / `has()` |

### 主要機能一覧

| 機能 | 説明 |
|------|------|
| `ConfigManager` | 設定ファイルを管理するシングルトンクラス |
| `ConfigManager.__init__()` | コンストラクタ（設定ファイルパス指定・初回のみ初期化） |
| `ConfigManager.__new__()` | シングルトンインスタンス生成 |
| `ConfigManager.get()` | ドット区切りキーで設定値を取得（キャッシュ付き） |
| `ConfigManager.set()` | 設定値を更新 |
| `ConfigManager.reload()` | 設定を再読み込み |
| `ConfigManager.save()` | 設定をYAMLファイルへ保存 |
| `ConfigManager.get_all()` | 全設定をコピーで取得 |
| `ConfigManager.has()` | キーの存在確認 |
| `ConfigManager._setup_logger()` | ロガーの初期化（プライベート） |
| `ConfigManager._load_config()` | 設定ファイルの読み込み（プライベート） |
| `ConfigManager._apply_env_overrides()` | 環境変数オーバーライドの適用（プライベート） |
| `ConfigManager._get_default_config()` | デフォルト設定辞書の生成（プライベート） |
| `get_config()` | 設定値取得のショートカット関数 |
| `set_config()` | 設定値更新のショートカット関数 |
| `reload_config()` | 設定再読み込みのショートカット関数 |

---

## 1. アーキテクチャ構成図

### 1.1 システム全体構成

```mermaid
flowchart TB
    subgraph CLIENT["クライアント層"]
        AGENT["GRACE Agent"]
        SERVICES["他サービスモジュール"]
        UI["Streamlit UI"]
    end

    subgraph MODULE["config_service.py"]
        MANAGER["ConfigManager (シングルトン)"]
        SHORTCUT["ショートカット関数群"]
        GLOBAL["config / logger グローバル"]
    end

    subgraph EXTERNAL["外部リソース層"]
        YAML["config.yml"]
        ENV["環境変数"]
        LOGOUT["ログ出力 (コンソール/ファイル)"]
    end

    AGENT --> SHORTCUT
    SERVICES --> SHORTCUT
    UI --> GLOBAL
    SHORTCUT --> MANAGER
    GLOBAL --> MANAGER
    MANAGER --> YAML
    MANAGER --> ENV
    MANAGER --> LOGOUT
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class AGENT,SERVICES,UI,MANAGER,SHORTCUT,GLOBAL,YAML,ENV,LOGOUT default
style CLIENT fill:#1a1a1a,stroke:#fff,color:#fff
style MODULE fill:#1a1a1a,stroke:#fff,color:#fff
style EXTERNAL fill:#1a1a1a,stroke:#fff,color:#fff
```

### 1.2 データフロー

1. クライアント層が `get_config()` / `config.get()` などで設定値を要求する
2. `ConfigManager`はキャッシュを確認し、なければ内部設定辞書をドット区切りキーで探索する
3. 初回生成時、`config.yml`を読み込み（存在しなければデフォルト設定を採用）、環境変数で上書きする
4. ロガーはコンソール／ファイルハンドラーへログを出力する
5. 取得した設定値をキャッシュに保存し、クライアントへ返却する

---

## 2. モジュール構成図

### 2.1 内部モジュール構成

```mermaid
flowchart TB
    subgraph CONST["定数・グローバル"]
        DEFAULT["_get_default_config 既定値"]
        GCONF["config インスタンス"]
        GLOG["logger"]
    end

    subgraph MANAGER["ConfigManager クラス"]
        NEW["__new__()"]
        INIT["__init__()"]
        GET["get()"]
        SET["set()"]
        RELOAD["reload()"]
        SAVE["save()"]
        GETALL["get_all()"]
        HAS["has()"]
        SETUPLOG["_setup_logger()"]
        LOADCONF["_load_config()"]
        ENVOVR["_apply_env_overrides()"]
        DEFCONF["_get_default_config()"]
    end

    subgraph FUNC["ショートカット関数"]
        GETC["get_config()"]
        SETC["set_config()"]
        RELC["reload_config()"]
    end

    NEW --> INIT
    INIT --> LOADCONF
    INIT --> SETUPLOG
    LOADCONF --> ENVOVR
    LOADCONF --> DEFCONF
    DEFCONF --> DEFAULT
    INIT --> GCONF
    GCONF --> GLOG
    GETC --> GET
    SETC --> SET
    RELC --> RELOAD
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class DEFAULT,GCONF,GLOG,NEW,INIT,GET,SET,RELOAD,SAVE,GETALL,HAS,SETUPLOG,LOADCONF,ENVOVR,DEFCONF,GETC,SETC,RELC default
style CONST fill:#1a1a1a,stroke:#fff,color:#fff
style MANAGER fill:#1a1a1a,stroke:#fff,color:#fff
style FUNC fill:#1a1a1a,stroke:#fff,color:#fff
```

### 2.2 外部依存関係

| ライブラリ | バージョン | 用途 |
|-----------|-----------|------|
| `PyYAML` | 6.x | YAML設定ファイルの読み書き（`yaml.safe_load` / `yaml.safe_dump`） |

### 2.3 標準ライブラリ依存

| モジュール | 用途 |
|-----------|------|
| `logging` / `logging.handlers` | ロガー設定、ローテーションファイルハンドラー |
| `os` | 環境変数の取得 |
| `pathlib.Path` | 設定ファイルパスの操作 |
| `typing` | 型ヒント（`Any` / `Dict`） |

---

## 3. クラス・関数一覧表

### 3.1 クラス一覧

#### ConfigManager

| メソッド | 概要 |
|---------|------|
| `__new__(config_path="config.yml")` | シングルトンインスタンスを生成 |
| `__init__(config_path="config.yml")` | 初回のみ設定読み込みとロガー初期化を実行 |
| `get(key, default=None)` | ドット区切りキーで設定値を取得（キャッシュ付き） |
| `set(key, value)` | 設定値を更新しキャッシュを破棄 |
| `reload()` | 設定を再読み込みしキャッシュをクリア |
| `save(filepath=None)` | 設定をYAMLファイルへ保存 |
| `get_all()` | 全設定辞書のコピーを取得 |
| `has(key)` | キーの存在を確認 |
| `_setup_logger()` | ロガー（`Gemini_helper`）を初期化 |
| `_load_config()` | 設定ファイルを読み込み |
| `_apply_env_overrides(config)` | 環境変数で設定をオーバーライド |
| `_get_default_config()` | デフォルト設定辞書を生成 |

### 3.2 関数一覧（カテゴリ別）

#### ショートカット関数

| 関数名 | 概要 |
|-------|------|
| `get_config(key, default=None)` | グローバル`config`から設定値を取得 |
| `set_config(key, value)` | グローバル`config`の設定値を更新 |
| `reload_config()` | グローバル`config`を再読み込み |

---

## 4. クラス・関数 IPO詳細

### 4.1 使用例

`config_service` は**直下 `config.yml`**（`config/grace_config.yml` ではない）を読む設定マネージャ。import した時点で
シングルトン `config` が作られる。使い方は次の 4 通り。

| 処理パターン | 呼び方 | 向いている場面 | 例 |
|---|---|---|---|
| 読む | `get_config("a.b")` / `config.get(...)` / `config.has(...)` | ドット区切りのキーで値を引く | 4.1.1 |
| 環境変数で上書きする | `GOOGLE_API_KEY` / `LOG_LEVEL` / `DEBUG_MODE` / `LLM_PROVIDER` を**import より前に**設定 | 鍵やログレベルを外から与える | 4.1.2 |
| 書き換えて保存する | `set_config` → `config.save(path)` → `reload_config()` | 実行中に値を変える・別ファイルへ書き出す | 4.1.3 |
| テストで作り直す | `ConfigManager._instance = None` → `ConfigManager(path)` | 別の設定ファイルを読ませる | 4.1.4 |

> 📝 4 本とも**別プロセスでそのまま実行し**、出力を確かめてある（2026-10-10。カレントはリポジトリ直下。ファイルは一時ディレクトリへ書いた）。
>
> ⚠️ **grace のパイプライン（`grace/config.py::get_config`）とは別物**。名前が同じ `get_config` だが、こちらは直下 `config.yml` の辞書を引く。
> 本番で読んでいるのは `helper/helper_api.py` 経由の `config` / `logger` くらい。LLM のモデル名は `config.py::get_default_ollama_model()` の 1 箇所で決まり、
> 直下 `config.yml` には `models.default` を置かない（CLAUDE.md §3）。

#### 4.1.1 基本的なワークフロー（読む）

```python
from services.config_service import config, get_config, logger

print(get_config("models.default"))                       # 実ファイルに置かない（None）
print(get_config("models.available")[:2])                 # ドット区切り
print(get_config("qdrant.vector_dims.gemini"))
# ⚠️ 無いキーは「最初に読んだときの結果」がキャッシュされる（下の 2 つは実ファイルに無いキー）
print(get_config("api.timeout", 30), get_config("api.timeout", 60), config.has("api.timeout"))
print(config.has("api.max_retries"), get_config("api.max_retries", 3))
print(config.config_path, type(config.get_all()).__name__)
logger.info("ロガーは 'Gemini_helper' という名前で 1 回だけ設定される")
```

```
# 出力例:
# None
# ['gemma4:12b-mlx', 'gemma4:e4b']
# 3072
# 30 30 True
# False None
# config.yml dict
```

> ⚠️ **`config.yml` は呼び出し側のカレントディレクトリからの相対パス**で読む（`ConfigManager("config.yml")`）。
> リポジトリ直下以外で起動すると「設定ファイルが見つかりません」と出し、内蔵の既定値（`_get_default_config`）で動く。
> 既定値と実ファイルではキーが違う（例: 既定値にある `api.timeout` / `llm.provider` / `models.default`〔`get_default_ollama_model()` の値〕は、実ファイルには無い）。
>
> ⚠️ **`get` は無いキーの結果（既定値）もキャッシュする。** 既定値 30 で 1 回読むと、以後は既定値 60 を渡しても 30 が返り、`has` も True になる（3 行目）。
> 逆に `has` を先に呼ぶと `None` がキャッシュされ、続く `get(key, 3)` は既定値ではなく `None` を返す（4 行目）。
> 無いかもしれないキーは、どこでも同じ既定値で `get` だけを使い、`has` で事前に確かめない。

#### 4.1.2 環境変数で上書きする

```python
import os

# import より前に設定する（import の時点でシングルトンが作られ、環境変数はそのときだけ読む）
os.environ["LOG_LEVEL"] = "DEBUG"
os.environ["LLM_PROVIDER"] = "ollama"

from services.config_service import get_config  # noqa: E402

print(get_config("logging.level"), get_config("llm.provider"))
print(get_config("api.google_api_key") is not None)       # GOOGLE_API_KEY があれば api.google_api_key に入る
```

```
# 出力例:
# DEBUG ollama
# True
```

> 📝 上書きできるのは `GOOGLE_API_KEY`（→ `api.google_api_key`）・`LOG_LEVEL`（→ `logging.level`）・`DEBUG_MODE`（→ `experimental.debug_mode`）・
> `LLM_PROVIDER`（→ `llm.provider`。読んで LLM を選ぶコードは無い。LLM の選択は `grace/config.py`）の 4 つだけ。import した後に変えたときは `reload_config()` で読み直す。

#### 4.1.3 書き換えて保存する

```python
import tempfile
from pathlib import Path

from services.config_service import config, get_config, reload_config, set_config

# 1. 実行中に書き換える（ファイルは変わらない）
print(get_config("qdrant.collection.default_name"))
set_config("qdrant.collection.default_name", "my_docs")
print(get_config("qdrant.collection.default_name"))

# 2. 別のファイルへ書き出す（元の config.yml へは書かない）
out = Path(tempfile.mkdtemp()) / "config_copy.yml"
print(config.save(str(out)), out.exists())

# 3. 読み直すと元のファイルの値に戻る
reload_config()
print(get_config("qdrant.collection.default_name"))
```

```
# 出力例:
# rag_documents
# my_docs
# True True
# rag_documents
```

> ⚠️ **`config.save()` を引数なしで呼ぶと元の `config.yml` を上書きし、コメントがすべて消える**（`yaml.safe_dump` で書き直すため）。
> 書き出すときは必ず別のパスを渡す。
>
> ⚠️ `get` は**キーごとに結果をキャッシュ**し、`set` は**同じキーのキャッシュだけ**を消す。`set("qdrant.collection", {...})` のように親を書き換えると、
> 先に読んだ子のキー（`qdrant.collection.default_name`）は古い値のまま返る。親を書き換えたら `reload_config()` するか、子のキーで書く。

#### 4.1.4 テストで作り直す（シングルトン）

```python
import tempfile
from pathlib import Path

from services.config_service import ConfigManager, config

# 1. 2 回目以降の ConfigManager(...) は、パスを変えても同じインスタンス（最初のパスのまま）
other = Path(tempfile.mkdtemp()) / "other.yml"
other.write_text("api:\n  timeout: 99\n", encoding="utf-8")
print(ConfigManager(str(other)) is config, ConfigManager(str(other)).config_path)

# 2. 別のファイルを読ませるには、シングルトンを捨ててから作る（tests/services/test_config_service.py と同じ）
ConfigManager._instance = None
fresh = ConfigManager(str(other))
print(fresh is config, fresh.get("api.timeout"))
```

```
# 出力例:
# True config.yml
# False 99
```

> ⚠️ 作り直しても、モジュール変数の `config` / `logger` と、それを import 済みのモジュールは**古いインスタンスを指したまま**。
> テスト以外では作り直さない。

---

### 4.2 ConfigManager クラス

設定ファイルを管理するシングルトンクラス。YAML読み込み、環境変数オーバーライド、キャッシュ付き設定取得、ロガー設定を提供する。

#### メソッド: `__new__`

**概要**: シングルトンパターンでインスタンスを1つだけ生成する。

```python
def __new__(cls, config_path: str = "config.yml")
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `config_path` | str | "config.yml" | 設定ファイルパス（生成時は未使用） |

| 項目 | 内容 |
|------|------|
| **Input** | `config_path: str = "config.yml"` |
| **Process** | 1. クラス変数 `_instance` が None か確認<br>2. None なら新規インスタンスを生成して保持<br>3. 既存インスタンスを返却 |
| **Output** | `ConfigManager`: 単一のシングルトンインスタンス |

**戻り値例**:
```python
<config_service.ConfigManager object at 0x10a2b3c40>
```

```python
# 使用例
m1 = ConfigManager("config.yml")
m2 = ConfigManager("config.yml")
print(m1 is m2)
# True（同一インスタンス）
```

#### コンストラクタ: `__init__`

**概要**: 初回呼び出し時のみ設定ファイル読み込み・キャッシュ初期化・ロガー設定を行う。

```python
def __init__(self, config_path: str = "config.yml")
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `config_path` | str | "config.yml" | 設定ファイルのパス |

| 項目 | 内容 |
|------|------|
| **Input** | `config_path: str = "config.yml"` |
| **Process** | 1. `_initialized` 属性があれば即return（再初期化防止）<br>2. `config_path` を `Path` に変換<br>3. `_load_config()` で設定を読み込み<br>4. キャッシュ辞書を初期化<br>5. `_setup_logger()` でロガーを設定 |
| **Output** | `None`（インスタンスを初期化） |

**戻り値例**:
```python
None
```

```python
# 使用例
config = ConfigManager("config.yml")
print(config.config_path)
# config.yml
```

#### メソッド: `get`

**概要**: ドット区切りキーで設定値を取得する。取得結果はキャッシュされる。

```python
def get(self, key: str, default: Any = None) -> Any
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `key` | str | - | ドット区切りキー（例: `"api.timeout"`） |
| `default` | Any | None | 値が無い場合に返すデフォルト値 |

| 項目 | 内容 |
|------|------|
| **Input** | `key: str`, `default: Any = None` |
| **Process** | 1. キャッシュに `key` があれば返却<br>2. `key` を `.` で分割し設定辞書を順に探索<br>3. 途中で辞書でなくなれば `default` を採用<br>4. 結果をキャッシュへ保存して返却 |
| **Output** | `Any`: 設定値（無ければ `default`） |

**戻り値例**:
```python
30
```

```python
# 使用例
timeout = config.get("api.timeout", 10)
print(timeout)
# 30
```

#### メソッド: `set`

**概要**: ドット区切りキーで設定値を更新し、該当キーのキャッシュを破棄する。

```python
def set(self, key: str, value: Any) -> None
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `key` | str | - | ドット区切りキー |
| `value` | Any | - | 設定する値 |

| 項目 | 内容 |
|------|------|
| **Input** | `key: str`, `value: Any` |
| **Process** | 1. `key` を `.` で分割<br>2. 末尾以外のキーで辞書を `setdefault` しながら降りる<br>3. 末尾キーに `value` を代入<br>4. 該当キーのキャッシュを削除 |
| **Output** | `None`（設定を更新） |

**戻り値例**:
```python
None
```

```python
# 使用例
config.set("api.timeout", 60)
print(config.get("api.timeout"))
# 60
```

#### メソッド: `reload`

**概要**: 設定ファイルを再読み込みし、キャッシュを全クリアする。

```python
def reload(self) -> None
```

| 項目 | 内容 |
|------|------|
| **Input** | なし（selfのみ） |
| **Process** | 1. `_load_config()` で設定辞書を再構築<br>2. キャッシュ辞書をクリア |
| **Output** | `None`（設定を再読み込み） |

**戻り値例**:
```python
None
```

```python
# 使用例
config.reload()
print(config.get("models.default"))
# None（config.yml は models.default を持たない。既定は get_default_ollama_model()）
```

#### メソッド: `save`

**概要**: 現在の設定辞書をYAMLファイルへ保存する。

```python
def save(self, filepath: str = None) -> bool
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `filepath` | str | None | 保存先パス（省略時は元の `config_path`） |

| 項目 | 内容 |
|------|------|
| **Input** | `filepath: str = None` |
| **Process** | 1. `filepath` があれば `Path` 化、無ければ `config_path` を使用<br>2. UTF-8 で開き `yaml.safe_dump` で書き出し<br>3. 例外時はロガーにエラーを記録し `False` を返す |
| **Output** | `bool`: 成功時 `True`、失敗時 `False` |

**戻り値例**:
```python
True
```

```python
# 使用例
ok = config.save("config_backup.yml")
print(ok)
# True
```

#### メソッド: `get_all`

**概要**: 全設定辞書のシャローコピーを返す。

```python
def get_all(self) -> Dict[str, Any]
```

| 項目 | 内容 |
|------|------|
| **Input** | なし（selfのみ） |
| **Process** | 内部設定辞書 `_config` の `.copy()` を返却 |
| **Output** | `Dict[str, Any]`: 設定辞書のコピー |

**戻り値例**:
```python
{
    "models": {"available": [...], "categories": {...}},
    "api": {"timeout": 30, "max_retries": 3},
    "llm": {"provider": "ollama"}
}
```

```python
# 使用例
all_conf = config.get_all()
print(all_conf["llm"]["provider"])
# ollama
```

#### メソッド: `has`

**概要**: 指定キーが存在するか（値が None でないか）を判定する。

```python
def has(self, key: str) -> bool
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `key` | str | - | ドット区切りキー |

| 項目 | 内容 |
|------|------|
| **Input** | `key: str` |
| **Process** | `get(key)` の結果が `None` でないかを判定 |
| **Output** | `bool`: 存在すれば `True` |

**戻り値例**:
```python
True
```

```python
# 使用例
print(config.has("api.timeout"))
# True
print(config.has("api.unknown"))
# False
```

#### メソッド: `_setup_logger`

**概要**: ロガー `Gemini_helper` をログ設定に従って初期化する（プライベート）。

```python
def _setup_logger(self) -> logging.Logger
```

| 項目 | 内容 |
|------|------|
| **Input** | なし（selfのみ） |
| **Process** | 1. `Gemini_helper` ロガーを取得<br>2. 既にハンドラーがあればそのまま返却<br>3. `logging` 設定からレベル・フォーマットを取得<br>4. コンソールハンドラーを追加<br>5. `file` 指定があればローテーションファイルハンドラーを追加 |
| **Output** | `logging.Logger`: 設定済みロガー |

**戻り値例**:
```python
<Logger Gemini_helper (INFO)>
```

```python
# 使用例
logger = config._setup_logger()
logger.info("初期化完了")
# 2026-06-17 ... - Gemini_helper - INFO - 初期化完了
```

#### メソッド: `_load_config`

**概要**: 設定ファイルを読み込み、環境変数オーバーライドを適用する（プライベート）。

```python
def _load_config(self) -> Dict[str, Any]
```

| 項目 | 内容 |
|------|------|
| **Input** | なし（selfのみ） |
| **Process** | 1. `config_path` が存在すれば `yaml.safe_load` で読み込み<br>2. `_apply_env_overrides()` で環境変数を適用<br>3. 読み込み失敗時はデフォルト設定を返す<br>4. ファイルが無ければデフォルト設定に環境変数を適用して返す |
| **Output** | `Dict[str, Any]`: 設定辞書 |

**戻り値例**:
```python
{
    "models": {"available": [...], ...},  # default キーは置かない
    "llm": {"provider": "ollama"}
}
```

```python
# 使用例
conf = config._load_config()
print(conf["models"].get("default"))
# None
```

#### メソッド: `_apply_env_overrides`

**概要**: 環境変数の存在に応じて設定辞書を上書きする（プライベート）。

```python
def _apply_env_overrides(self, config: Dict[str, Any]) -> None
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `config` | Dict[str, Any] | - | 上書き対象の設定辞書 |

| 項目 | 内容 |
|------|------|
| **Input** | `config: Dict[str, Any]` |
| **Process** | 1. `GOOGLE_API_KEY` があれば `api.google_api_key` を設定<br>2. `LOG_LEVEL` があれば `logging.level` を設定<br>3. `DEBUG_MODE` があれば `experimental.debug_mode` を真偽値化して設定<br>4. `LLM_PROVIDER` があれば `llm.provider` を設定 |
| **Output** | `None`（`config` を破壊的に更新） |

**戻り値例**:
```python
None
```

```python
# 使用例
import os
os.environ["LLM_PROVIDER"] = "ollama"   # 有効値は ollama / openai / gemini
conf = {}
config._apply_env_overrides(conf)
print(conf["llm"]["provider"])
# ollama
```

#### メソッド: `_get_default_config`

**概要**: 設定ファイルが無い／読み込み失敗時に使うデフォルト設定辞書を返す（プライベート）。

```python
def _get_default_config(self) -> Dict[str, Any]
```

| 項目 | 内容 |
|------|------|
| **Input** | なし（selfのみ） |
| **Process** | `models` / `api` / `ui` / `cache` / `logging` / `error_messages` / `experimental` / `llm` の各セクションを持つ辞書を生成 |
| **Output** | `Dict[str, Any]`: デフォルト設定辞書 |

**戻り値例**:
```python
{
    "models": {"default": get_default_ollama_model(), "available": get_selectable_ollama_models()},
    "llm": {"provider": "ollama"}
}
```

```python
# 使用例
defaults = config._get_default_config()
print(defaults["models"]["default"])
# gemma4:26b-a4b-it-qat（環境変数 OLLAMA_DEFAULT_MODEL があればその値）
```

### 4.3 ショートカット関数

#### `get_config`

**概要**: グローバル`config`インスタンスから設定値を取得するショートカット関数。

```python
def get_config(key: str, default: Any = None) -> Any
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `key` | str | - | ドット区切りキー |
| `default` | Any | None | デフォルト値 |

| 項目 | 内容 |
|------|------|
| **Input** | `key: str`, `default: Any = None` |
| **Process** | グローバル `config.get(key, default)` を呼び出し |
| **Output** | `Any`: 設定値 |

**戻り値例**:
```python
"ollama"
```

```python
# 使用例
from services.config_service import get_config
provider = get_config("llm.provider", "ollama")
print(provider)
# ollama（環境変数 LLM_PROVIDER を設定していれば、その値）
```

#### `set_config`

**概要**: グローバル`config`インスタンスの設定値を更新するショートカット関数。

```python
def set_config(key: str, value: Any) -> None
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `key` | str | - | ドット区切りキー |
| `value` | Any | - | 設定する値 |

| 項目 | 内容 |
|------|------|
| **Input** | `key: str`, `value: Any` |
| **Process** | グローバル `config.set(key, value)` を呼び出し |
| **Output** | `None`（設定を更新） |

**戻り値例**:
```python
None
```

```python
# 使用例
from services.config_service import set_config, get_config
set_config("api.timeout", 90)
print(get_config("api.timeout"))
# 90
```

#### `reload_config`

**概要**: グローバル`config`インスタンスを再読み込みするショートカット関数。

```python
def reload_config() -> None
```

| 項目 | 内容 |
|------|------|
| **Input** | なし |
| **Process** | グローバル `config.reload()` を呼び出し |
| **Output** | `None`（設定を再読み込み） |

**戻り値例**:
```python
None
```

```python
# 使用例
from services.config_service import reload_config
reload_config()
```

---

## 5. 設定・定数

### 5.1 デフォルト設定辞書

`_get_default_config()`が返す設定辞書。`config.yml`が存在しない、または読み込みに失敗した場合に使用される。

```python
{
    "models": {
        "default": get_default_ollama_model(),
        "available": get_selectable_ollama_models()
    },
    "api": {
        "timeout": 30,
        "max_retries": 3,
        "openai_api_key": None,
        "google_api_key": None
    },
    "ui": {
        "page_title": "RAG Q/A Generator",
        "page_icon": "🤖",
        "layout": "wide"
    },
    "cache": {
        "enabled": True,
        "ttl": 3600,
        "max_size": 100
    },
    "logging": {
        "level": "INFO",
        "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        "file": None,
        "max_bytes": 10485760,
        "backup_count": 5
    },
    "error_messages": {
        "general_error": "エラーが発生しました",
        "api_key_missing": "APIキーが設定されていません",
        "network_error": "ネットワークエラーが発生しました"
    },
    "experimental": {
        "debug_mode": False,
        "performance_monitoring": True
    },
    "llm": {
        "provider": "ollama"
    }
}
```

| キー | デフォルト値 | 説明 |
|-----|-------------|------|
| `models.default` | `get_default_ollama_model()`（既定 `gemma4:26b-a4b-it-qat`） | 既定のローカル LLM（Ollama） |
| `models.available` | `get_selectable_ollama_models()` | UI に出せるモデル一覧 |
| `api.timeout` | 30 | APIタイムアウト（秒） |
| `api.max_retries` | 3 | 最大リトライ回数 |
| `api.openai_api_key` | None | OpenAI APIキー（既定では未設定） |
| `api.google_api_key` | None | Gemini Embedding 用APIキー（`GOOGLE_API_KEY` で上書き） |
| `ui.page_title` | "RAG Q/A Generator" | UIページタイトル |
| `ui.page_icon` | "🤖" | UIページアイコン |
| `ui.layout` | "wide" | UIレイアウト |
| `cache.enabled` | True | キャッシュ有効化フラグ |
| `cache.ttl` | 3600 | キャッシュ有効期間（秒） |
| `cache.max_size` | 100 | キャッシュ最大件数 |
| `logging.level` | "INFO" | ログレベル（`LOG_LEVEL` で上書き） |
| `logging.format` | "%(asctime)s - %(name)s - %(levelname)s - %(message)s" | ログフォーマット |
| `logging.file` | None | ログファイルパス（None でファイル出力なし） |
| `logging.max_bytes` | 10485760 | ログローテーションのサイズ上限（10MB） |
| `logging.backup_count` | 5 | ログバックアップ世代数 |
| `experimental.debug_mode` | False | デバッグモード（`DEBUG_MODE` で上書き） |
| `experimental.performance_monitoring` | True | パフォーマンス監視フラグ |
| `llm.provider` | "ollama" | LLMプロバイダー（`LLM_PROVIDER` で上書き） |

### 5.2 環境変数オーバーライド

| 環境変数 | オーバーライド先キー | 説明 |
|---------|-------------------|------|
| `GOOGLE_API_KEY` | `api.google_api_key` | Gemini Embedding 用APIキー |
| `LOG_LEVEL` | `logging.level` | ログレベル |
| `DEBUG_MODE` | `experimental.debug_mode` | デバッグモード（`"true"` で有効） |
| `LLM_PROVIDER` | `llm.provider` | LLMプロバイダー |

### 5.3 グローバルインスタンス

| 名前 | 型 | 説明 |
|------|------|------|
| `config` | ConfigManager | `ConfigManager("config.yml")` のシングルトン |
| `logger` | logging.Logger | `config.logger`（`Gemini_helper` ロガー） |

> 📝 **注意**: LLM はローカル LLM（Ollama。既定は `config.py::get_default_ollama_model()` → `gemma4:26b-a4b-it-qat`、API キー不要）、Embedding は Gemini（`gemini-embedding-001`、鍵 `GOOGLE_API_KEY`）を用います。`_get_default_config()` の `llm.provider` は `"ollama"`（2026-09-25 に `"anthropic"` から是正）。ただし本モジュールの設定を読んで LLM を選ぶ箇所は無い（LLM の選択は `grace/config.py` 側）。


---

## 6. エクスポート

`__all__`で公開される要素：

```python
__all__ = [
    # クラス
    "ConfigManager",
    # グローバルインスタンス
    "config",
    "logger",
    # ユーティリティ関数
    "get_config",
    "set_config",
    "reload_config",
]
```

---

## 7. 変更履歴

| バージョン | 日付 | 変更内容 |
|---|---|---|
| 1.0 | 2026-06-17 | 初版作成（2026-06-17） |
| 1.1 | 2026-09-24 | 使用例を IPO 詳細の冒頭（`### 4.1 使用例`）へ移し、末尾の「## 6. 使用例」章を削除（基本フォーマット `a_class_method_md_format.md` v1.6〜 §6.1 に準拠。2026-09-24）。IPO の小節を 4.2 以降へ繰り下げ、後続の章番号を 1 つ繰り上げた。文書内の `§4.x` 参照も追随 |
| 1.2 | 2026-09-24 | `models` の記述を実装に合わせた（2026-09-24）。`_get_default_config()` は `get_default_ollama_model()` / `get_selectable_ollama_models()` を返すのに、文書は Anthropic のモデル名のままだった。末尾の注意書きの LLM 表記も Ollama へ是正。あわせて直下 `config.yml` から `models.default` を外した（既定を `get_default_ollama_model()` に一元化）のに追随し、出力例を更新 |
| 1.3 | — | `_get_default_config()` の `llm.provider` の既定値を `"anthropic"` から `"ollama"` へ是正したのに追随（使用例・出力例・設定表・注記） |
| 1.4 | 2026-10-08 | Anthropic 予備経路の削除に追随（2026-10-08）。使用例に残っていた `LLM_PROVIDER="anthropic"` と出力例 `# anthropic` を `ollama` へ是正（`create_llm_client("anthropic")` は `ValueError` になったため） |
| 1.5 | 2026-10-03 | 現在の既定モデルの記載 `gemma4:12b-mlx` を、2026-10-03 の変更後の値 `gemma4:26b-a4b-it-qat`（`config.py::get_default_ollama_model()` の戻り値）へ是正（§4.1.1・§4.2 の出力例・§5.1 の設定表・注意書き。2026-10-08） |
| 1.6 | 2026-10-10 | §4.1 使用例を処理パターン別（読む／環境変数で上書き／書き換えて保存／テストで作り直す）の 4 本に書き直した（2026-10-10。`grace/docs/executor.md` §4.1 を手本に、処理パターンの表 → パターンごとの例 → 落とし穴の注記の形にし、別プロセスで全例を実行して出力を確かめた）。直下 `config.yml` に `models.default` を置かないこと（`None`）を例で示した。`get` が無いキーの既定値・`None` もキャッシュする落とし穴を実行で見つけて注記 |

---

## 付録: 依存関係図

```mermaid
flowchart LR
    MODULE["config_service.py"]

    subgraph EXT["外部ライブラリ"]
        YAML["PyYAML"]
    end

    subgraph STD["標準ライブラリ"]
        LOGGING["logging / logging.handlers"]
        OS["os"]
        PATH["pathlib.Path"]
        TYPING["typing"]
    end

    MODULE --> YAML
    MODULE --> LOGGING
    MODULE --> OS
    MODULE --> PATH
    MODULE --> TYPING

    YAML --> YL1["yaml.safe_load"]
    YAML --> YD1["yaml.safe_dump"]
    LOGGING --> LH1["RotatingFileHandler"]
    LOGGING --> LH2["StreamHandler"]
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class MODULE,YAML,LOGGING,OS,PATH,TYPING,YL1,YD1,LH1,LH2 default
style EXT fill:#1a1a1a,stroke:#fff,color:#fff
style STD fill:#1a1a1a,stroke:#fff,color:#fff
```

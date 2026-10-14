# core/job_logs.py - ジョブ進捗へのログ転送 ドキュメント

**Version 1.4** | 最終更新: 2026-10-14

> **本書の位置づけ**: `backend/app/core/job_logs.py`（既存パッケージの `logging` を進捗イベントへ転送）の **IPO リファレンス**。
> 引くための文書であり、**設計の「なぜ」は上位の文書が正本**である。
>
> | 知りたいこと | 参照先 |
> |---|---|
> | **機構の正本**（スレッド絞り込み・level の参照カウント） | [`core_process_flow.md` §3.2](core_process_flow.md) |
> | 唯一の利用者 | [`core/docs/data_jobs.md`](./data_jobs.md) |
> | SSE のワイヤ形式 | [`README_backend_api.md` §4.1](../../api/docs/README_backend_api.md) |
> | 文書全体の地図 | [`README_app.md`](../../docs/README_app.md) |

---

## 目次

1. [概要](#概要)
2. [アーキテクチャ構成図](#1-アーキテクチャ構成図)
3. [モジュール構成図](#2-モジュール構成図)
4. [クラス・関数一覧表](#3-クラス関数一覧表)
5. [クラス・関数 IPO詳細](#4-クラス関数-ipo詳細)
6. [設定・定数](#5-設定定数)
7. [落とし穴](#6-落とし穴)
8. [変更履歴](#7-変更履歴)

---

## 概要

`chunking/` `qa_generation/` `qa_qdrant/` `services/` の各パイプラインは、進捗を
`logger.info(...)` と `tqdm` にしか出しておらず、**進捗コールバックを持たない**。
GRACE-Support / GRACE-Review が `emit(SupportEvent(...))` で SSE へ流しているのに対し、
データ準備側にはその経路が無い。

そこで各モジュールへ `emit` 引数を足す（＝3 パッケージを改修する）代わりに、
**ジョブ実行スレッドに紐づく `logging.Handler` を一時的に取り付けてログレコードを横取りする**。
既存コードは 1 行も変えずに進捗が SSE へ流れる。

### 主な責務

- 指定したロガー（既定は データ準備 4 パッケージ）の出力を `log` イベントへ転送する
- **自スレッドのレコードだけ**を転送する（同時実行する他ジョブとの混線防止）
- ロガーの `level` を一時的に下げ、**参照カウントで正しく復元**する

### 各責務対応のモジュール

| # | 責務 | 対応モジュール | 説明 |
|---|------|--------------|------|
| 1 | 指定ロガーの出力を `log` イベントへ転送 | `backend/app/core/job_logs.py` | `capture_logs()` が `JobLogHandler` を取り付け、終了時に必ず外す。転送は `JobLogHandler.emit()`、ステップの切り替えは `set_step()` |
| 2 | 自スレッドのレコードだけを転送 | `backend/app/core/job_logs.py` | `JobLogHandler.emit()` が取り付けたスレッドの ID と照合する |
| 3 | level の一時変更と参照カウントでの復元 | `backend/app/core/job_logs.py` | `_acquire_level()` / `_release_level()` / `_level_refs` / `_level_lock` |

### 主要機能一覧

| 機能 | 説明 |
|---|---|
| `JobLogHandler` | 自スレッドのレコードだけを `emit` へ転送する `logging.Handler` |
| `capture_logs()` | ハンドラの取り付け・取り外しを行うコンテキストマネージャ |
| `DEFAULT_LOGGER_NAMES` | 既定で横取りするロガー名 |

---

## 1. アーキテクチャ構成図

### 1.1 システム全体構成

```mermaid
flowchart TB
    subgraph CLIENT["クライアント層"]
        RUNNER["core/data_jobs.py の各 runner"]
    end

    subgraph MODULE["core/job_logs.py"]
        CAP["capture_logs()（contextmanager）"]
        HANDLER["JobLogHandler（自スレッドのみ転送）"]
        REFS["_level_refs（level の参照カウント）"]
    end

    subgraph EXTERNAL["転送元・転送先"]
        PKG["chunking / qa_generation / qa_qdrant / services のロガー"]
        JOB["core/jobs.py Job.emit → SSE"]
    end

    RUNNER --> CAP
    CAP --> HANDLER
    CAP --> REFS
    PKG --> HANDLER
    HANDLER --> JOB
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class RUNNER,CAP,HANDLER,REFS,PKG,JOB default
style CLIENT fill:#1a1a1a,stroke:#fff,color:#fff
style MODULE fill:#1a1a1a,stroke:#fff,color:#fff
style EXTERNAL fill:#1a1a1a,stroke:#fff,color:#fff
```

### 1.2 データフロー

1. runner が `with capture_logs(emit, ["chunking"], step="chunk"):` に入る
2. 対象ロガーへ `JobLogHandler` を取り付け、`level` を `_acquire_level()` で引き下げる
3. 既存パッケージが `logger.info(...)` を呼ぶ
4. ハンドラが `record.thread` を見て**自スレッド分だけ** `emit(SupportEvent(type="log", ...))` する
5. `with` を抜けるとハンドラを外し、`_release_level()` が参照カウント 0 で元の level へ戻す

---

## 2. モジュール構成図

```mermaid
flowchart TB
    CAPT["capture_logs()"]
    HND["JobLogHandler.emit()"]
    SETSTEP["JobLogHandler.set_step()"]
    ACQ["_acquire_level()"]
    REL["_release_level()"]
    LOCK["_level_lock / _level_refs"]
    CAPT --> HND
    CAPT --> ACQ
    CAPT --> REL
    ACQ --> LOCK
    REL --> LOCK
    CAPT --> SETSTEP
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class CAPT,HND,SETSTEP,ACQ,REL,LOCK default
```

### 2.2 外部依存関係

| 依存 | 用途 |
|---|---|
| `backend.app.core.support_agent.SupportEvent` / `EmitFn` | 転送するイベントの型 |
| 標準ライブラリ `logging` / `threading` / `contextlib` | ハンドラ・スレッド識別・コンテキストマネージャ |

---

## 3. クラス・関数一覧表

| 要素 | 概要 |
|---|---|
| `DEFAULT_LOGGER_NAMES` | 既定で横取りするロガー名（`chunking` / `qa_generation` / `qa_qdrant` / `services`） |
| `JobLogHandler(emit_fn, step=None, thread_ident=None)` | 自スレッドのレコードだけを転送する Handler |
| `JobLogHandler.emit(record)` | レコードを `log` イベントへ転送する（失敗は握りつぶす） |
| `JobLogHandler.set_step(step)` | 転送先のステップ ID を切り替える |
| `capture_logs(emit_fn, logger_names=DEFAULT_LOGGER_NAMES, step=None, level=INFO)` | 取り付け／取り外しを行うコンテキストマネージャ |
| `_acquire_level(logger, level)` / `_release_level(logger)` | level の引き下げと復元（参照カウント方式・非公開） |

---

## 4. クラス・関数 IPO詳細

### 4.1 使用例

| # | パターン | いつ使うか | 例 |
|---|---------|-----------|----|
| 1 | **基本**: `with capture_logs(...)` の間だけ転送する | runner が既存パッケージを呼ぶ区間 | 4.1.1 |
| 2 | 段の切り替え（`set_step`）と、転送しないもの（別スレッド・低い level） | 1 つの runner が複数の段を持つとき・同時実行の確認 | 4.1.2 |

> 📝 **確かめ方（2026-10-11）**: どちらも `PYTHONPATH=. python3 <例>.py` で実行した（標準出力だけを載せている。ルートの logging が同じ行を標準エラーにも出す）。

#### 4.1.1 基本的なワークフロー（既存パッケージのログを SSE へ流す）

`chunking/` などの既存パッケージは**無改修**のまま、`logging` の出力だけを
ジョブの進捗イベントへ転送する。これが本モジュールの唯一の役割。

```python
import logging
from backend.app.core.job_logs import capture_logs

events = []
logger = logging.getLogger("chunking.demo")

# 1. with の中だけ、指定した logger 配下の出力が emit へ転送される
with capture_logs(events.append, logger_names=["chunking"], step="chunk"):
    logger.info("チャンク化を開始します")
    logger.warning("入力が空の行をスキップしました")

# 2. with を抜ければ元の logging に戻る（ハンドラは必ず外れる）
logger.info("この行は捕捉されない")

for e in events:
    print(f"{e.type} step={e.step} {e.message}")

# 出力例:
# log step=chunk チャンク化を開始します
# log step=chunk 入力が空の行をスキップしました
```

> ⚠️ **`step` を渡すと、その間のログが UI のどの段に属するかが決まる。**
> runner はステップの切り替わりで `set_step()` を呼び、ログ行を正しい段へ寄せる。

#### 4.1.2 段の切り替えと、転送しないもの

```python
import logging
import threading

from backend.app.core.job_logs import capture_logs

events = []
logger = logging.getLogger("qa_generation.demo")

with capture_logs(events.append, logger_names=["qa_generation"], step="generate") as handler:
    logger.info("Q/A を生成中")
    handler.set_step("save")                      # 以降のログは ⑵ 保存の段へ
    logger.info("CSV を保存しました")
    # 別スレッド（＝同時に走る別のジョブ）のログは転送しない
    t = threading.Thread(target=lambda: logger.info("別ジョブのログ"))
    t.start(); t.join()
    logger.debug("DEBUG は既定の level=INFO より低いので転送しない")

for e in events:
    print(f"{e.type} step={e.step} {e.message}")

# with を抜けると logger の level は元に戻る（未設定 = 0 = 親を継承）
print(logger.getEffectiveLevel() == logging.getLogger().getEffectiveLevel(),
      logging.getLogger("qa_generation").level)

# 実行結果:
# log step=generate Q/A を生成中
# log step=save CSV を保存しました
# True 0
```

> 📌 「別ジョブのログ」は転送されない。ハンドラは作ったスレッド（＝ジョブのワーカースレッド）のレコードだけを通す（§6）。
> logger の level は with の間だけ下げ、抜けると元（ここでは未設定の `0`）へ戻す。同時に走る複数のジョブは参照カウントで level を共有する（§6）。


### 4.2 `JobLogHandler.emit(record)`

| 項目 | 内容 |
|---|---|
| **Input** | `logging.LogRecord` |
| **Process** | 1. `record.thread != self._thread_ident` なら**無視**（他ジョブとの混線防止）<br>2. `self.format(record)` で整形（失敗は `handleError` に委ねる）<br>3. 空文字なら捨てる<br>4. `emit(SupportEvent(type="log", step=self._step, message=…, data={"level": record.levelname}))` |
| **Output** | なし（副作用としてイベント送信）。**転送中の例外は握りつぶす**（ログ出力の失敗で本処理を落とさない） |

### 4.3 `capture_logs(...)`

| 項目 | 内容 |
|---|---|
| **Input** | `emit_fn`（`Job.emit`）、`logger_names`、`step`、`level`（既定 `INFO`） |
| **Process** | 1. `JobLogHandler` を生成（生成時のスレッド ＝ ジョブのワーカースレッド）<br>2. 対象ロガーごとに `_acquire_level()` → `addHandler()`<br>3. `yield handler`<br>4. `finally` で `removeHandler()` → `_release_level()` |
| **Output** | `JobLogHandler`（`set_step()` で段階を切り替えられる） |

> **level を下げる理由**: 多くのロガーは未設定（＝ root の `WARNING` を継承）なので、
> 下げないと `INFO` が届かない。

---

## 5. 設定・定数

| 定数 | 値 | 意味 |
|---|---|---|
| `DEFAULT_LOGGER_NAMES` | `("chunking", "qa_generation", "qa_qdrant", "services")` | パッケージロガーに付ければ、`logging.getLogger(__name__)` で作られた子ロガーの出力も propagate で拾える |
| `_level_refs` | `{ロガー名: (元の level, 参照数)}` | 復元用（§6） |

---

## 6. 落とし穴

| 罠 | 実際に起きること |
|---|---|
| **スレッド絞り込みを外す** | ハンドラはロガー（プロセス全体）に付くため、同時に 2 本走らせると**片方の進捗にもう片方のログが混ざる** |
| **参照カウントを素朴な退避・復元に変える** | ジョブA が `NOTSET(0)` を控えて `INFO(20)` に上げた後、ジョブB が `20` を「元の値」として控えるため、**全ジョブ終了後もロガーが INFO のまま残る** |
| **`with` を使わず手で取り付ける** | 外し忘れるとハンドラが積み上がり、1 行のログが N 回転送される |
| **同時実行で違う `level` を要求する** | 先に入った方が勝つ。全ジョブが既定の `INFO` を使う限り問題にならない |

---

## 7. 変更履歴

| バージョン | 日付 | 変更内容 |
|---|---|---|
| 1.0 | 2026-09-16 | 新規作成（文書再編 Phase 3）。実装（193 行）から IPO を書き起こした |
| 1.1 | 2026-09-24 | 概要に「各責務対応のモジュール」（主な責務と 1:1）を追加（基本フォーマット §2.4。2026-09-24） |
| 1.2 | 2026-10-11 | `backend/docs/reference/core_job_logs.md` から移した（`a_cross_doc_md_format.md` §1.1.2。リンクを付け替えた）。§4.1 を、実行できなかった断片・出力の無い例から、本リポジトリで実行して確かめた例に作り直した（with の間だけ転送・段の切り替え・別スレッドと低い level は転送しない） |
| 1.3 | 2026-10-12 | backend/docs/ の廃止（2026-10-12）に追随し、参照先を backend/app/docs/README_app.md・backend/app/api/docs/README_api.md・backend/app/core/docs/core_process_flow.md などへ付け替えた（テストの地図は docs/testing.md、実行例の txt は docs/examples/） |
| 1.4 | 2026-10-14 | 索引の改名（frontend/docs/README.md → README_frontend.md、README_api.md → README_backend_api.md、README_core.md → README_backend_core.md）に合わせて参照を付け替えた |

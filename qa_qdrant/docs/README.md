# qa_qdrant/docs/ 棚卸し

**Version 1.7** | 最終更新: 2026-09-26

> 📎 **姉妹版**: [`chunking/docs/README.md`](../../chunking/docs/README.md) /
> [`qa_generation/docs/README.md`](../../qa_generation/docs/README.md) /
> [`services/docs/README.md`](../../services/docs/README.md) /
> [`docs/README.md`](../../docs/README.md)（直下・配置の境界）

`qa_qdrant/docs/` 配下のドキュメントを一覧化する。**目的から入口を引ける**ようにするのが狙い。

本ディレクトリは **12 文書と最も数が多く**、IPO・手順書・比較検討メモが混在している。
§2 で形式を明示するので、目的に合わない形式を開かないこと。

> 📌 **データ準備 3 工程のうち ③ にあたるパッケージ。**
> ① チャンク化は [`chunking/`](../../chunking/docs/README.md)、
> ② Q/A 生成は [`qa_generation/`](../../qa_generation/docs/README.md)。
> 運用手順の入口は [`README_DATA.md`](../../README_DATA.md)。

---

## 目次

- [1. 目的別の入口](#1-目的別の入口)
- [2. 一覧](#2-一覧)
- [3. 実装カバレッジ](#3-実装カバレッジ)
- [4. ⚠️ `qa_qdrant/__init__.py` は `make_qa.py` の古い写し](#4-️-qa_qdrant__init__py-は-make_qapy-の古い写し)
- [5. 書き分けの約束](#5-書き分けの約束)
- [6. 残タスク](#6-残タスク)
- [7. テスト件数（実測）](#7-テスト件数実測)
- [8. 変更履歴](#8-変更履歴)

---

## 1. 目的別の入口

| やりたいこと | 読む文書 |
|---|---|
| **環境を作る**（Ollama / MeCab / Docker / Celery） | [`01_install.md`](01_install.md) — **唯一の入口** |
| **Celery 並列を動かす** | [`celery_quick_start.md`](celery_quick_start.md) |
| **生成 → 登録を 1 本で回す** | [`make_qa_register_qdrant.md`](make_qa_register_qdrant.md) |
| **既存 CSV を登録するだけ** | [`register_to_qdrant.md`](register_to_qdrant.md) |
| **コレクションを消す** | [`qdrant_delete_collection.md`](qdrant_delete_collection.md) |
| **システム全体の設計を知る** | [`qa_qdrant_architecture.md`](qa_qdrant_architecture.md) |
| **なぜ Celery なのか / なぜスマート生成なのか** | [`asyncio_vs_celery.md`](asyncio_vs_celery.md) / [`generation_vs_SmartGeneration.md`](generation_vs_SmartGeneration.md) |

---

## 2. 一覧

> 行数・Ver は **2026-09-24 の実測値**（`wc -l` と各文書の Version ヘッダー）。

### 2.1 手順書

**種別 B**（`a_cross_doc_md_format.md` §6。概要に状態・結論・対象モジュール）。

| 文書 | 内容 | 行数 | Ver | 重要度 |
|---|---|---:|---|:--:|
| [`01_install.md`](01_install.md) | 環境構築。Ollama・MeCab・Docker（Qdrant / Redis）・Celery。**運用の唯一の入口** | 910 | 2.1 | ★★★ |
| [`celery_quick_start.md`](celery_quick_start.md) | Celery ワーカーの起動手順 | 619 | 2.3 | ★★☆ |
| [`make_qa_register_qdrant.md`](make_qa_register_qdrant.md) | `make_qa_register_qdrant.py`（Q/A 生成 → Qdrant 登録の統合 CLI）の使い方。**IPO ではない**（2026-09-24 に §2.2 から移した） | 943 | 1.2 | ★★★ |

### 2.2 IPO（モジュール仕様）

**種別 E**（`a_class_method_md_format.md`。使用例は IPO 詳細の冒頭 `### 4.1`）。

| 文書 | 対象実装 | 実装行数 | 文書行数 | Ver | 重要度 |
|---|---|---:|---:|---|:--:|
| [`register_to_qdrant.md`](register_to_qdrant.md) | `register_to_qdrant.py` — 既存 CSV → Qdrant | 593 | 586 | 2.1 | ★★★ |
| [`make_qa.md`](make_qa.md) | `make_qa.py` — Q/A 生成のみの CLI | 265 | 448 | 3.3 | ★★☆ |
| [`qdrant_delete_collection.md`](qdrant_delete_collection.md) | **`qdrant_delete_collection.py`（リポジトリ直下）** — コレクション削除 CLI | 73 | 304 | 1.1 | ★★☆ |
| [`make_qa_qapipeline.md`](make_qa_qapipeline.md) | `QAPipeline` ＋ `SmartQAGenerator` の連携（**実体は `qa_generation/`**） | — | 943 | 1.1 | ★★☆ |

> ⚠️ **`qdrant_delete_collection.md` の対象はこのパッケージの外にある**（リポジトリ直下の
> `qdrant_delete_collection.py`）。関連が深いためここに置いているが、探すときは注意。
>
> ⚠️ **`make_qa_qapipeline.md` は `qa_generation/` の実装を説明している。**
> モジュール単位の IPO は [`qa_generation/docs/pipeline.md`](../../qa_generation/docs/pipeline.md) /
> [`smart_qa_generator.md`](../../qa_generation/docs/smart_qa_generator.md) が正本。
> 本書は**両者の連携**を扱う横断文書として読むこと。

### 2.3 設計・比較検討

`qa_qdrant_architecture.md` は**種別 A**（横断文書）、ほかの 4 件は**種別 B**（比較検討・改修の記録。本文は当時のまま残し、概要で現在の状態を示す）。

| 文書 | 内容 | 行数 | Ver | 重要度 |
|---|---|---:|---|:--:|
| [`qa_qdrant_architecture.md`](qa_qdrant_architecture.md) | Q/A 生成 & Qdrant 登録システムの設計書（v3.0） | 794 | 3.1 | ★★☆ |
| [`asyncio_vs_celery.md`](asyncio_vs_celery.md) | 並列方式の比較分析（なぜ Celery か） | 693 | 1.1 | ★☆☆ |
| [`generation_vs_SmartGeneration.md`](generation_vs_SmartGeneration.md) | Q/A 生成方式の比較（なぜ SmartGeneration 一本化か） | 691 | 1.1 | ★☆☆ |
| [`smart_generation_upgrade.md`](smart_generation_upgrade.md) | スマート生成デフォルト化の改修サマリー | 503 | 1.1 | ★☆☆ |
| [`00_learning.md`](00_learning.md) | 学習順の構成比較メモ ＋ カテゴリー別一覧（**H1 を先頭へ移動済み**） | 376 | 1.1 | ★☆☆ |

---

## 3. 実装カバレッジ

`qa_qdrant/*.py` は **4 件**（`__init__.py` を含む）。

| 実装 | 行数 | 文書 | 備考 |
|---|---:|---|---|
| `make_qa_register_qdrant.py` | 609 | ⚠️ `make_qa_register_qdrant.md`（**手順書のみ・IPO 無し**） | §6 残タスク 5 |
| `register_to_qdrant.py` | 593 | ✅ `register_to_qdrant.md` | ログ format を `celery_config.py` と統一（§4.6） |
| `make_qa.py` | 265 | ✅ `make_qa.md` |  |
| `__init__.py` | 24 | — | **docstring のみ**（2026-09-21 に整理・§4.6）。処理を書かないこと |

---

## 4. ⚠️ `qa_qdrant/__init__.py` は `make_qa.py` の古い写し

**2026-09-20 に実測で調査し、2026-09-21 に対処した。** 以下はすべて計測・grep・diff で確認した事実である。

### 4.1 何者か

対処前の `__init__.py`（236 行）の docstring は
`make_qa.py - Q/Aペア生成 CLIエントリーポイント（改修版）` で始まり、
**`make_qa.py` の v3.0 以前の版**である
（`diff -u qa_qdrant/__init__.py qa_qdrant/make_qa.py` → **差分 197 行**）。

現行の `make_qa.py` は v3.0（`--input-chunks` 廃止・`-c/--concurrency` 追加・
`SmartQAGenerator` 一本化）だが、`__init__.py` はその前の状態で止まっている。
git 履歴上のコミットは **1 件だけ**（2026-09-03）で、以降更新されていない。

**公開シンボル（`main` / `PROJECT_ROOT` / `logger`）に依存しているコードは 1 件も無い**
（`qa_qdrant.main` / `from qa_qdrant import` / `qa_qdrant.PROJECT_ROOT` を grep して 0 件）。

### 4.2 本番経路から読み込まれる

`backend/app/core/data_jobs.py:683`（**Qdrant 登録ジョブの runner**）が
`from qa_qdrant.register_to_qdrant import register_to_qdrant` を実行するため、
**パッケージ import の副作用として `__init__.py` の 236 行が走る。**

### 4.3 実測した影響

`__init__.py` を空にした複製と比較した（3 回ずつ計測）。

| 項目 | 現状 | 空の `__init__.py` | 差 |
|---|---|---|---|
| import 所要時間 | 1.63 / 1.77 / 1.82 s | 1.45 / 1.58 / 1.57 s | **約 +0.2 s（12〜15%）** |
| 読み込まれるモジュール数 | 1799 | 1683 | **+116** |
| `celery_config` が載るか | **Yes** | No | — |
| `qa_generation.pipeline` が載るか | **Yes** | No | — |

**Qdrant 登録ジョブは Celery も Q/A 生成パイプラインも使わない。**
`__init__.py` の `from qa_generation.pipeline import QAPipeline`（L20）が
不要な依存ツリーを丸ごと引き込んでいる。

### 4.4 ⚠️ ログ設定は `__init__.py` のせいではない（v1.0 の見立てを訂正）

v1.0 では「import 副作用でログ設定が変わる」と読める書き方をしていたが、**誤り**だった。

`import qa_qdrant.register_to_qdrant` すると root logger に StreamHandler が付き、
レベルが `WARNING` → `INFO` になる。だが**これは `__init__.py` を空にしても同じ**である
（上表のとおり `root level=INFO / handlers=1` は両方で一致した）。

理由: `logging.basicConfig()` を**モジュールレベルで呼ぶファイルが 7 件ある**。

| ファイル | 行 |
|---|---:|
| `qa_qdrant/register_to_qdrant.py` | 63 |
| `qa_qdrant/__init__.py` | 23 |
| `qa_qdrant/make_qa.py` | 49 |
| `qa_qdrant/make_qa_register_qdrant.py` | 120 |
| `celery_config.py` | 25 |
| `helper/helper_rag.py` | 18 |
| `qa_generation/smart_qa_generator.py` | 25 |

（`grace/config.py:31` も呼ぶが関数内なので影響が小さい。`backend/tests/` の 2 件は対象外。）

`basicConfig()` は **root に既にハンドラがあると何もしない**ので、**最初に走った 1 つが勝つ**。
`__init__.py` を消しても `register_to_qdrant.py:63` が同じことをする。

> 📌 **ただし `__init__.py` は「どの format が勝つか」を変える。**
> 現状は L20 の import 連鎖で `celery_config` が先に走り、その format
> `[%(asctime)s] %(levelname)s [%(name)s] %(message)s` が採用される。
> 空にすると `register_to_qdrant.py` の format
> `%(asctime)s - %(levelname)s - %(message)s` になる。**実測で確認済み。**

### 4.5 空にしてもテストは全件通る

一時的に空にして CI と同じ `pytest backend/tests -q` を実行した
（検証後にファイルは復元し、`git status` が 0 件であることを確認済み）。

```
1906 passed, 22 skipped
```

### 4.6 結論と対処（2026-09-21 に実施済み）

| 論点 | 判定 |
|---|---|
| 死にコードか | **Yes**。参照ゼロ・git 履歴 1 件・現行 `make_qa.py` の劣化コピー |
| import 副作用があるか | **Yes**。登録ジョブの経路で不要な 116 モジュール（+0.2 s） |
| ログ設定を壊しているか | **No**（§4.4）。ただし format の優先順位は変える |
| 空にするとテストが壊れるか | **No**（§4.5・1906 passed） |

**対処は 2 手に分けた。**観測可能な挙動変化をゼロにするためである。

1. **`register_to_qdrant.py:63` の format を `celery_config.py` と揃えた**
   （`%(asctime)s - %(levelname)s - %(message)s` → `[%(asctime)s] %(levelname)s [%(name)s] %(message)s`）。
   §4.4 のとおり `basicConfig()` は先に走った 1 つが勝つので、揃えておけば
   どちらが先でもログの見た目が変わらない。
2. **`__init__.py` を docstring だけ（24 行）にした。** パッケージとして必要なので
   ファイル自体は残す。処理を書かない理由を docstring に記録した。

### 4.7 対処後の実測

```
モジュール数    1799 → 1683（−116）
所要時間        2.09 / 1.75 / 1.77 s
celery_config   載る → 載らない
qa_generation.pipeline  載る → 載らない
root level      INFO（変更なし）
format          '[%(asctime)s] %(levelname)s [%(name)s] %(message)s'（変更なし）
```

**format と root level は対処前と完全に一致する。** 消えたのは不要な依存だけである。

あわせて次も確認した。

| 確認 | 結果 |
|---|---|
| `import qa_qdrant` と 3 モジュールの import | OK |
| 3 つの CLI（`--help` まで到達するか） | 3 本とも OK |
| `pytest backend/tests -q` | 1906 passed, 22 skipped |
| 旧シンボル（`main` / `PROJECT_ROOT` / `logger`）への参照 | 0 件（再確認） |


---

## 5. 書き分けの約束

**運用手順は `01_install.md` にだけ書く。** 同じ手順を複数箇所に書くと、
片方だけ直したときに食い違う（`chunking/docs/README.md` §3 と同じ方針）。

| 置き場所 | 書くもの | 書かないもの |
|---|---|---|
| `01_install.md` | 環境構築・前提・起動手順 | モジュールの内部実装 |
| `celery_quick_start.md` | Celery 固有の起動・監視 | 環境構築全般（`01_install.md` へリンク） |
| `<module>.md` | IPO（入出力・副作用・CLI 引数） | 環境構築手順 |
| 比較検討メモ | **なぜその方式を選んだか**（意思決定の記録） | 現在の仕様（`<module>.md` が正本） |

> IPO 形式の仕様は `.claude/skills/grace-agent-docs/a_class_method_md_format.md`。
> **重複禁止ルールと正本の一覧は [`docs/README.md`](../../docs/README.md) §4 が持つ。**

---

## 6. 残タスク

| # | 内容 | 優先 |
|---|---|:--:|
| 1 | ~~`qa_qdrant/__init__.py` を空にする~~ | **完了**（§4.6・2026-09-21）。`register_to_qdrant.py` の format を `celery_config.py` と揃えてから docstring のみにしたので、**ログの見た目は変わらない** | ✅ |
| 2 | ~~`make_qa.md` の技術スタック表と Mermaid ノードが Anthropic 表記のまま~~ | ✅ **完了**（2026-09-21・v3.2）。あわせて `--model` 既定値の `gemini-2.5-flash` も実装（`make_qa.py:108`）に合わせて是正した |
| 3 | ~~7 文書に `**Version X.X**` ヘッダーが無い~~ | ✅ **完了**（2026-09-21）。7 件すべてにヘッダーと変更履歴を追加。版と日付は **git 履歴からの実測値**（`celery_quick_start` は既存の「最終更新 2025-01-20 / v2.1」を規約形式へ揃えて **2.2**、`qa_qdrant_architecture` は既存の更新履歴に合わせて **3.0**） |
| 4 | ~~`00_learning.md` の H1 が 20 行目にある~~ | ✅ **完了**（2026-09-21）。**タイトルを先頭へ出す**方を採り、冒頭に 2 つの主題（構成の比較 / カテゴリー別一覧）の関係を 1 文で示した。分割はしていない（片方だけでは読めない分量ではないため） |
| 5 | `make_qa_register_qdrant.py` の IPO 文書が無い。`make_qa_register_qdrant.md` は 2025-01 時点の使い方ガイドで、`*_modified.py` など現存しないファイルにも触れている | 中 |
| 6 | ~~`QAPipeline` の引数の記述が実装から遅れている~~ | ✅ **完了**（2026-09-24）。`make_qa_qapipeline.md` と `qa_generation/docs/pipeline.md` から削除済みの `use_smart_generation` を外した |

> 📌 **`qdrant_delete_collection.md` の `cc_news_2per_anthropic` 等は誤りではない。**
> これは**実際のコレクション名**である（`backend/app/core/verticals.py` などで使用）。
> Embedding が Gemini のまま既存コレクションを使い続ける設計のため、名前に `_anthropic` が
> 残っている（CLAUDE.md §3）。表記統一の対象外。

---

## 7. テスト件数（実測）

**2026-09-20 に各ファイルを個別実行した実測値。記憶で書かないこと。**

| テストファイル | 件数 | 対象 |
|---|---:|---|
| `backend/tests/test_make_qa_register_qdrant_csv.py` | 2 | `make_qa_register_qdrant.py` の CSV 入力 |
| `backend/tests/test_register_qdrant_metadata.py` | 2 | 登録時のメタデータ |

```bash
uv run --no-sync pytest backend/tests/test_make_qa_register_qdrant_csv.py backend/tests/test_register_qdrant_metadata.py -q
```

> ⚠️ **テストは薄い。** 実装 1,467 行（`__init__.py` を除く 3 ファイル）に対して 4 件で、
> `register_to_qdrant.py`（587 行）と `make_qa.py`（265 行）には専用テストが無い。
> 実 Qdrant を要する処理が多いためだが、カバレッジの空白として認識しておくこと。

---

## 8. 変更履歴

| Version | 日付 | 変更 |
|---|---|---|
| 1.7 | 2026-09-26 | `make_qa_register_qdrant.md` v1.2（§7 の `--model` 既定を Ollama の既定へ是正）に追随して §2 の行数・Ver を更新 |
| 1.6 | 2026-09-24 | 残タスク 6（`QAPipeline` の引数の記述遅れ）を完了 |
| 1.5 | 2026-09-24 | 12 文書を仕様へ追随させたのにあわせ、目次と文書種別（A / B / E）を追加。`make_qa_register_qdrant.md` は IPO ではないため §2.2 から §2.1（手順書）へ移し、§3 に IPO 欠落を明記。§2・§3 の Ver・行数を再実測。残タスク 5・6 を追加 |
| 1.4 | 2026-09-21 | 残タスク 3・4 を完了（Version ヘッダー 7 件、`00_learning.md` の H1 位置）。**残タスク 0 件**。§2 の行数・Ver 列を再実測 |
| 1.3 | 2026-09-21 | 残タスク 2 を完了（`make_qa.md` v3.2 の Anthropic 表記と `--model` 既定値を是正） |
| 1.2 | 2026-09-21 | 残タスク 1 を実施。`register_to_qdrant.py` のログ format を `celery_config.py` と統一したうえで `__init__.py` を docstring のみ（24 行）にした。**モジュール数 1799 → 1683、format と root level は変化なし**（§4.7） |
| 1.1 | 2026-09-21 | §4 を実測ベースへ全面書き換え（import 所要時間・モジュール数を 3 回計測、テスト全件実行、`basicConfig` 7 箇所を grep）。**§4.4 で v1.0 の見立てを訂正** — ログ設定の変化は `__init__.py` のせいではなく、空にしても同じだった |
| 1.0 | 2026-09-20 | 新規作成。`qa_qdrant/docs/` だけ棚卸し索引が無かった。12 文書を形式別（手順書 / IPO / 設計・比較）に整理し、実装カバレッジ・テスト件数（実測）・残タスク 4 件を記載。あわせて **`qa_qdrant/__init__.py` が `make_qa.py` の古い写しで import 副作用を持つ**ことを `diff` で確認し §4 に記録した |

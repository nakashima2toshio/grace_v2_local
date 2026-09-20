# qa_qdrant/docs/ 棚卸し

**Version 1.0** | 最終更新: 2026-09-20

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

> 行数・Ver は **2026-09-20 の実測値**（`wc -l` と各文書の Version ヘッダー）。

### 2.1 手順書

| 文書 | 内容 | 行数 | Ver | 重要度 |
|---|---|---:|---|:--:|
| [`01_install.md`](01_install.md) | 環境構築。Ollama・MeCab・Docker（Qdrant / Redis）・Celery。**運用の唯一の入口** | 922 | 2.0 | ★★★ |
| [`celery_quick_start.md`](celery_quick_start.md) | Celery ワーカーの起動手順 | 581 | — | ★★☆ |

### 2.2 IPO（モジュール仕様）

| 文書 | 対象実装 | 実装行数 | 文書行数 | Ver | 重要度 |
|---|---|---:|---:|---|:--:|
| [`make_qa_register_qdrant.md`](make_qa_register_qdrant.md) | `make_qa_register_qdrant.py` — Q/A 生成 → Qdrant 登録の統合 CLI | 609 | 910 | — | ★★★ |
| [`register_to_qdrant.md`](register_to_qdrant.md) | `register_to_qdrant.py` — 既存 CSV → Qdrant | 587 | 587 | 2.0 | ★★★ |
| [`make_qa.md`](make_qa.md) | `make_qa.py` — Q/A 生成のみの CLI | 265 | 448 | 3.1 | ★★☆ |
| [`qdrant_delete_collection.md`](qdrant_delete_collection.md) | **`qdrant_delete_collection.py`（リポジトリ直下）** — コレクション削除 CLI | 73 | 305 | 1.0 | ★★☆ |
| [`make_qa_qapipeline.md`](make_qa_qapipeline.md) | `QAPipeline` ＋ `SmartQAGenerator` の連携（**実体は `qa_generation/`**） | — | 964 | 1.0 | ★★☆ |

> ⚠️ **`qdrant_delete_collection.md` の対象はこのパッケージの外にある**（リポジトリ直下の
> `qdrant_delete_collection.py`）。関連が深いためここに置いているが、探すときは注意。
>
> ⚠️ **`make_qa_qapipeline.md` は `qa_generation/` の実装を説明している。**
> モジュール単位の IPO は [`qa_generation/docs/pipeline.md`](../../qa_generation/docs/pipeline.md) /
> [`smart_qa_generator.md`](../../qa_generation/docs/smart_qa_generator.md) が正本。
> 本書は**両者の連携**を扱う横断文書として読むこと。

### 2.3 設計・比較検討

| 文書 | 内容 | 行数 | Ver | 重要度 |
|---|---|---:|---|:--:|
| [`qa_qdrant_architecture.md`](qa_qdrant_architecture.md) | Q/A 生成 & Qdrant 登録システムの設計書（v3.0） | 704 | — | ★★☆ |
| [`asyncio_vs_celery.md`](asyncio_vs_celery.md) | 並列方式の比較分析（なぜ Celery か） | 660 | — | ★☆☆ |
| [`generation_vs_SmartGeneration.md`](generation_vs_SmartGeneration.md) | Q/A 生成方式の比較（なぜ SmartGeneration 一本化か） | 654 | — | ★☆☆ |
| [`smart_generation_upgrade.md`](smart_generation_upgrade.md) | スマート生成デフォルト化の改修サマリー | 455 | — | ★☆☆ |
| [`00_learning.md`](00_learning.md) | 学習順の構成比較メモ ＋ カテゴリー別一覧。**H1 が 20 行目にあり、冒頭は `## 構成の比較` から始まる** | 320 | — | ★☆☆ |

---

## 3. 実装カバレッジ

`qa_qdrant/*.py` は **4 件**（`__init__.py` を含む）。

| 実装 | 行数 | 文書 | 備考 |
|---|---:|---|---|
| `make_qa_register_qdrant.py` | 609 | ✅ `make_qa_register_qdrant.md` | |
| `register_to_qdrant.py` | 587 | ✅ `register_to_qdrant.md` | |
| `make_qa.py` | 265 | ✅ `make_qa.md` | |
| `__init__.py` | 236 | ❌ **無い** | **§4 を参照。文書を書く前に中身を確認すること** |

---

## 4. ⚠️ `qa_qdrant/__init__.py` は `make_qa.py` の古い写し

`__init__.py`（236 行）の docstring は次のとおりで、**`make_qa.py` の v3.0 以前の版**である
（2026-09-20 に `diff -u qa_qdrant/__init__.py qa_qdrant/make_qa.py` で確認）。

```python
"""
make_qa.py - Q/Aペア生成 CLIエントリーポイント（改修版）
チャンクCSV読み込み機能を追加

🔧 qa_qdrant/ ディレクトリ配下に移動後の修正版
"""
```

現行の `make_qa.py` は v3.0（`--input-chunks` 廃止・`-c/--concurrency` 追加・
`SmartQAGenerator` 一本化）だが、`__init__.py` はその前の状態で止まっている。

**単なる死にコードではなく、副作用がある。** モジュールレベルで
`sys.path.insert(...)` を実行し、`config` と `qa_generation.pipeline.QAPipeline` を
import するため、**`import qa_qdrant` するだけでこれらが走る**。

> 📌 現状 `from qa_qdrant.make_qa_register_qdrant import ...`（`backend/tests/`）から
> 間接的に読み込まれている。**実害の有無は未検証**なので、削除や整理は
> 影響範囲を確認してから行うこと（**§6 の残タスク 1**）。本書では事実の記録に留める。

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
| 1 | `qa_qdrant/__init__.py` が `make_qa.py` の古い写し（§4）。import 副作用があるため、影響範囲を確認したうえで整理する | 中 |
| 2 | `make_qa.md` の技術スタック表（L63）と Mermaid ノード（L89）が `Anthropic Claude（claude-sonnet-4-6）` / `ANTHROPIC_API_KEY` のまま。本リポジトリの LLM 既定は **Ollama**（CLAUDE.md §9.3） | 中 |
| 3 | 7 文書に `**Version X.X**` ヘッダーが無い（`00_learning` / `asyncio_vs_celery` / `celery_quick_start` / `generation_vs_SmartGeneration` / `make_qa_register_qdrant` / `qa_qdrant_architecture` / `smart_generation_upgrade`） | 低 |
| 4 | `00_learning.md` は H1（`# Q/A生成 & Qdrant登録システム - カテゴリー別一覧`）が **20 行目**にあり、冒頭が `## 構成の比較` から始まる。タイトルを先頭へ出すか、2 つの主題（構成比較 / カテゴリー別一覧）を分けるか要判断 | 低 |

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

> ⚠️ **テストは薄い。** 実装 1,461 行（`__init__.py` を除く 3 ファイル）に対して 4 件で、
> `register_to_qdrant.py`（587 行）と `make_qa.py`（265 行）には専用テストが無い。
> 実 Qdrant を要する処理が多いためだが、カバレッジの空白として認識しておくこと。

---

## 8. 変更履歴

| Version | 日付 | 変更 |
|---|---|---|
| 1.0 | 2026-09-20 | 新規作成。`qa_qdrant/docs/` だけ棚卸し索引が無かった。12 文書を形式別（手順書 / IPO / 設計・比較）に整理し、実装カバレッジ・テスト件数（実測）・残タスク 4 件を記載。あわせて **`qa_qdrant/__init__.py` が `make_qa.py` の古い写しで import 副作用を持つ**ことを `diff` で確認し §4 に記録した |

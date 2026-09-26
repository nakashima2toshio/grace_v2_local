# qa_generation/docs/ 棚卸し

**Version 1.11** | 最終更新: 2026-09-26

> 📎 **姉妹版**: [`chunking/docs/README.md`](../../chunking/docs/README.md) /
> [`qa_qdrant/docs/README.md`](../../qa_qdrant/docs/README.md) /
> [`services/docs/README.md`](../../services/docs/README.md) /
> [`docs/README.md`](../../docs/README.md)（直下・配置の境界）

`qa_generation/docs/` 配下のドキュメントを一覧化する。**目的から入口を引ける**ようにするのが狙い。

> 📌 **データ準備 3 工程のうち ② にあたるパッケージ。**
> ① チャンク化は [`chunking/`](../../chunking/docs/README.md)、
> ③ Qdrant 登録は [`qa_qdrant/`](../../qa_qdrant/docs/README.md)。
> 運用手順の入口は [`README_DATA.md`](../../README_DATA.md)。

---

## 目次

- [1. 目的別の入口](#1-目的別の入口)
- [2. 一覧](#2-一覧)
- [3. 実装カバレッジ](#3-実装カバレッジ)
- [4. ⚠️ 文書に Anthropic 前提の記述が残っている](#4-️-文書に-anthropic-前提の記述が残っている)
- [5. 書き分けの約束](#5-書き分けの約束)
- [6. 残タスク](#6-残タスク)
- [7. テスト件数（実測）](#7-テスト件数実測)
- [8. 変更履歴](#8-変更履歴)

---

## 1. 目的別の入口

| やりたいこと | 読む文書 |
|---|---|
| **パイプライン全体の流れを知る** | [`pipeline.md`](pipeline.md) |
| **Q/A をどう生成しているか** | [`smart_qa_generator.md`](smart_qa_generator.md) |
| **カバレージをどう測るか** | [`semantic.md`](semantic.md) / [`evaluation.md`](evaluation.md) |
| **CLI から動かす** | [`../../qa_qdrant/docs/01_install.md`](../../qa_qdrant/docs/01_install.md) §6 |
| **入力 CSV の読み方・出力ファイルの仕様を知る** | [`data_io.md`](data_io.md) |
| **Q/A のスキーマ（Pydantic）を知る** | [`models.md`](models.md) |
| **パッケージの公開 API・import 副作用を知る** | [`__init__.md`](__init__.md) |

---

## 2. 一覧

> 行数は **2026-09-24 の実測値**（`wc -l`）。
>
> 7 文書はすべて**種別 E**（IPO 形式・`a_class_method_md_format.md` 準拠。使用例は IPO 詳細の冒頭）。本索引自体は種別 C（`a_cross_doc_md_format.md`）。

| 文書 | 対象実装 | 実装行数 | 文書行数 | Ver | 重要度 |
|---|---|---:|---:|---|:--:|
| [`pipeline.md`](pipeline.md) | `pipeline.py` — `QAPipeline`（Web / CLI 共通の実体） | 565 | 807 | 1.5 | ★★★ |
| [`smart_qa_generator.md`](smart_qa_generator.md) | `smart_qa_generator.py` — `SmartQAGenerator`（構造化出力 1 回） | 296 | 572 | 1.2 | ★★★ |
| [`semantic.md`](semantic.md) | `semantic.py` — `SemanticCoverage`（Embedding によるカバレージ） | 542 | 779 | 1.2 | ★★☆ |
| [`evaluation.md`](evaluation.md) | `evaluation.py` — `analyze_coverage()` ほか | 316 | 821 | 1.2 | ★★☆ |
| [`data_io.md`](data_io.md) | `data_io.py` — 入力 CSV の読み込みと結果 4 ファイルの保存 | 168 | 481 | 1.2 | ★★☆ |
| [`models.md`](models.md) | `models.py` — Pydantic モデル 8 クラス（`QAPair` / `QAPairsList` は直下 `models.py` から再エクスポート） | 149 | 423 | 1.5 | ★☆☆ |
| [`__init__.md`](__init__.md) | `__init__.py` — 公開 API（再エクスポート 11 件） | 65 | 301 | 1.5 | ★☆☆ |

> 📌 `smart_qa_generator.py` は 2026-09-21 に 301 → **296 行**（`__main__` の
> `ANTHROPIC_API_KEY` ゲート削除）。

---

## 3. 実装カバレッジ

`qa_generation/*.py` は **7 件**（`__init__.py` を含む）、対応する `<module>.md` も **7 件**。
**2026-09-21 に欠落 3 件を解消し、1:1 対応が揃った。**

| 実装 | 行数 | 文書 | 備考 |
|---|---:|---|---|
| `data_io.py` | 168 | ✅ [`data_io.md`](data_io.md) | `services/dataset_service.py` の削除後、**データセット読み込みの現役経路はこちら**（`docs/port_from_grace_v2_todo.md` §11） |
| `models.py` | 149 | ✅ [`models.md`](models.md) | Pydantic モデル 8 クラス。**`QAPair` / `QAPairsList` は直下 `models.py` の定義を再エクスポート**（2026-09-25 に一本化） |
| `__init__.py` | 65 | ✅ [`__init__.md`](__init__.md) | 公開 API（再エクスポート 11 件）。**import 副作用で Celery が読み込まれる**（実測 +117 モジュール） |

### 3.1 文書化して分かったこと（2026-09-21）

| # | 内容 | 扱い |
|---|---|---|
| 1 | ~~`QAPair` が 3 箇所に別定義で存在する~~ — 直下 `models.py`（`services/qa_service.py` が使う現役）／`qa_generation/models.py`（本パッケージ）／`helper/helper_rag_qa.py`（統合元）。フィールドが違う（`difficulty_level` と `difficulty` + `source_span`） | ✅ 2026-09-25 に直下 `models.py` へ一本化（§6 の残タスク 5）。`qa_generation/models.py` と `helper_rag_qa.py` の別定義を削除し、**定義は 1 つだけ** |
| 2 | **`qa_generation/models.py` に本番の利用者がいない** — `qa_generation/__init__.py` の再エクスポート以外に import 元が無い（grep 実測） | 同上 |
| 3 | ~~**`import qa_generation.<任意>` が Celery を連れてくる**~~ → **解消済み**（2026-09-21）。`pipeline.py` の `celery_tasks` import を `_generate_with_celery()` 内へ移し、1,799 → **1,689 モジュール**（9.58 → **1.82 秒**） | 残タスク 6 を完了 |
| 4 | `QAGenerationConsiderations` の既定値（品質基準・難易度分布など）を**読むコードが無い**。Q/A 数の決定は `SmartQAGenerator.COMBINED_PROMPT` が行う | [`models.md`](models.md) に記録 |
| 5 | **`helper/helper_rag_qa.py` の裸 import が `celery_tasks` の `sys.path` 挿入に依存していた** — 3 の修正で `celery_tasks` が自動で読まれなくなり、`test_keyword_extraction.py` が収集エラーで露見した。**元から単体実行では通らないテスト**だった（全体実行で偶然 `celery_tasks` が先に読まれていた） | `helper.helper_embedding` / `helper.helper_llm` へ是正済み |

---

## 4. ⚠️ 文書に Anthropic 前提の記述が残っている

本リポジトリの LLM 既定は **Ollama**（CLAUDE.md §3・§9.3）だが、
`qa_generation/docs/` の 3 文書に Anthropic 表記が残っている（2026-09-20 に grep で実測）。

| 文書 | `anthropic`/`claude` の出現 | 内容 |
|---|---:|---|
| `smart_qa_generator.md` | 18 | 冒頭に「LLM を Anthropic Claude へ統一」と明記 |
| `pipeline.md` | 8 | — |
| `semantic.md` | 2 | 冒頭に「LLM 文脈の表記を Anthropic Claude に統一」と明記 |

**実装はすでに Ollama である**ことを確認済み:

```python
# qa_generation/semantic.py:32
self.unified_client = create_llm_client(provider="ollama")
# qa_generation/smart_qa_generator.py:69
self.client = create_llm_client(provider="ollama", default_model=model)
```

→ **文書が実装に追いついていない。§6 の残タスク 2。**

### 4.1 死んだ `provider="anthropic"` 引数 → **削除済み**（2026-09-21）

かつて `QAPipeline._generate_with_celery()` は次のように呼んでいた。

```python
tasks = submit_unified_qa_generation(
    chunks, self.config, self.model, provider="anthropic"
)
```

一見 CLAUDE.md §3 違反だが、**受け取り側が使っていなかった**。

```python
def submit_unified_qa_generation(
        chunks, config, model,
        provider: str = "anthropic",  # 互換性のために残すが使用しない
) -> List:
```

`generate_qa_for_chunk_task.apply_async(args=(chunk, config, model))` に `provider` は
渡っておらず、実際のプロバイダはワーカー側（`SmartQAGenerator`）で解決される。
**機能上の不具合ではないが誤解を招く**ため、呼び出し元がこの 1 箇所だけであることを
確認したうえで、**受け側の引数ごと削除**した。

```python
tasks = submit_unified_qa_generation(chunks, self.config, self.model)
```

`grep -i anthropic celery_tasks.py` は **0 件**になった。

---

## 5. 書き分けの約束

| 置き場所 | 書くもの | 書かないもの |
|---|---|---|
| `<module>.md` | IPO（入出力・副作用・使用例） | 運用手順（`qa_qdrant/docs/01_install.md` へリンク） |
| `qa_qdrant/docs/` | CLI の実行手順・環境構築 | `qa_generation/` 内部の実装 |
| 直下 `docs/` | 2 領域以上にまたがる横断文書 | 1 モジュールの IPO |

> IPO 形式の仕様は `.claude/skills/grace-agent-docs/a_class_method_md_format.md`。
> **重複禁止ルールと正本の一覧は [`docs/README.md`](../../docs/README.md) §4 が持つ。**

---

## 6. 残タスク

| # | 内容 | 優先 |
|---|---|:--:|
| 1 | ~~`data_io.md` / `models.md` / `__init__.md` が無い（§3）~~ | ✅ **完了**（2026-09-21）。3 文書を新規作成し、実装との 1:1 対応が揃った |
| 2 | ~~3 文書に Anthropic 前提の記述が残る~~ | ✅ **完了**（2026-09-21・§4）。`smart_qa_generator.md` / `pipeline.md` / `semantic.md` を Ollama 表記へ是正し、Version ヘッダーと変更履歴も追加した |
| 3 | ~~`pipeline.py` の `provider="anthropic"`（死んだ引数・§4.1）~~ | ✅ **完了**（2026-09-21）。呼び出し元が `QAPipeline._generate_with_celery` の 1 箇所だけだったので、**受け側（`celery_tasks.submit_unified_qa_generation`）の引数ごと削除**した |
| 4 | ~~4 文書とも `**Version X.X**` ヘッダーが無い~~ | ✅ **完了**（2026-09-21）。`evaluation.md` に v1.1 のヘッダーと変更履歴を追加し、4 件すべてが揃った |
| 5 | ~~**`QAPair` の 3 重定義**~~ | ✅ **決着**（2026-09-25 に見直し）。2026-09-21 には「統合しない」としたが、**直下 `models.py` へ一本化**した（grace_v2 と同じ変更）。`qa_generation/models.py` と `helper/helper_rag_qa.py` の別定義を削除し、どちらも正本を import する（`helper_rag_qa.py` の LLM プロンプトの項目名も正本へ揃えた）。`from qa_generation import QAPair` は引き続き使えるが、フィールドは正本のもの（`difficulty_level` ほか）になる。関係を `test_qa_pair_definitions.py`（4 件）で固定した |
| 6 | ~~**`qa_generation` の import で Celery が読み込まれる**~~ | ✅ **完了**（2026-09-21）。`pipeline.py` の遅延 import 化で 1,799 → 1,689 モジュール。回帰は `test_import_side_effects.py`（2 件）で固定 |

---

## 7. テスト件数（実測）

**2026-09-20 に各ファイルを個別実行した実測値。記憶で書かないこと。**

| テストファイル | 件数 | 対象 |
|---|---:|---|
| `backend/tests/test_semantic.py` | 10 | `semantic.py` |
| `backend/tests/test_smart_qa_usage.py` | 4 | `smart_qa_generator.py` |
| `backend/tests/test_evaluation.py` | 1 | `evaluation.py` |
| `backend/tests/qa_generation/test_import_side_effects.py` | 2 | パッケージの import 副作用（Celery が載らないこと） |
| `backend/tests/qa_generation/test_qa_pair_definitions.py` | 6 | `QAPair` / `QAPairsList` の定義場所の固定（`qa_generation` 側が直下 `models.py` の正本そのものであること・`qa_generation/models.py` と `helper_rag_qa.py` に別定義を書き戻していないこと） |
| `backend/tests/qa_generation/test_data_io_missing_text.py` | 3 | `data_io.py` — 欠損セルを `"nan"` にしないこと（2026-09-24 追加） |

`backend/tests/qa_generation/` ディレクトリ全体では **42 件**（2026-09-25 実測）。
backend 全体は **1923 passed, 22 skipped**（2026-09-25 実測）。

```bash
uv run --no-sync pytest backend/tests/test_semantic.py backend/tests/test_smart_qa_usage.py -q
```

> ⚠️ **`pipeline.py`（549 行）を直接対象にしたテストは無い。** `QAPipeline.run()` は
> Web / CLI 共通の実体なので、カバレッジの空白として認識しておくこと。

---

## 8. 変更履歴

| Version | 日付 | 変更 |
|---|---|---|
| 1.11 | 2026-09-26 | `pipeline.md` v1.5（`QAPipeline(text_column=...)` の追加）に追随して §2 の行数・版を更新 |
| 1.10 | 2026-09-25 | `QAPairsList` も直下 `models.py` の定義（`QAPairsResponse` の別名）へ一本化（grace_v2 と同じ変更）。`qa_generation/models.py` と `helper/helper_rag_qa.py` の同名クラスを削除し、`test_qa_pair_definitions.py` に 2 件を追加（計 6 件）。§2 の行数・版を更新 |
| 1.9 | 2026-09-25 | `helper/helper_rag_qa.py` の旧 `QAPair` も削除し、`QAPair` の定義は直下 `models.py` の 1 つだけになった（grace_v2 と同じ変更）。§2・§3.1・§6・§7 を更新 |
| 1.8 | 2026-09-25 | 残タスク 5（`QAPair` の 3 重定義）の決着を「統合しない」から**「直下 `models.py` へ一本化」**へ変更（grace_v2 と同じ変更）。§2・§3・§3.1・§6・§7 を更新 |
| 1.7 | 2026-09-24 | `data_io.py` の `"nan"` 混入を修正したのに追随（grace_v2 と同じ修正）。§2・§3 の実装行数（162 → 168）と文書の版・行数、§7 のテスト一覧（`test_data_io_missing_text.py` 3 件を追加）と件数（`qa_generation/` 40 件・全体 1921 passed）を再実測で更新 |
| 1.6 | 2026-09-24 | `pipeline.md` の `QAPipeline` 引数の記述を実装に合わせたのに追随し、§2 の版・行数を更新（v1.4・804 行） |
| 1.5 | 2026-09-24 | 7 文書を基本フォーマットの章構成（概要＋責務 1:1＋3 層構成図＋番号付き章＋使用例は IPO 冒頭）へ組み替えたのにあわせ、§2 の Ver・行数を再実測し、文書種別（E／本索引は C）を明記。H2 が 8 個のため目次を追加。実装行数も再実測（`pipeline.py` 549 → 553、`models.py` 155 → 166） |
| 1.4 | 2026-09-21 | 残タスク 3・4 を完了（死んだ `provider` 引数の削除、`evaluation.md` の Version ヘッダー）。**残タスク 0 件** |
| 1.3 | 2026-09-21 | 残タスク 5・6 を決着（6 は `pipeline.py` の遅延 import 化で解消、5 は「統合しない」判断＋テストで固定）。§3.1 に 5 件目（`helper_rag_qa.py` の裸 import が `celery_tasks` の `sys.path` 挿入に依存していた件）を追記。§7 のテスト件数を再実測 |
| 1.2 | 2026-09-21 | 残タスク 1 を完了（`data_io.md` / `models.md` / `__init__.md` を新規作成し、実装 7 件との 1:1 対応が揃った）。文書化の過程で判明した 4 点を §3.1 に記録し、うち 2 点を残タスク 5・6 として新規登録した。§2 の行数を再実測（`smart_qa_generator.py` 301 → 296） |
| 1.1 | 2026-09-21 | 残タスク 2 を完了（3 文書の Anthropic 表記を Ollama へ是正）。残タスク 4 も 3/4 完了（`evaluation.md` のみ残る） |
| 1.0 | 2026-09-20 | 新規作成。`qa_generation/docs/` だけ棚卸し索引が無かった。文書一覧・実装カバレッジ（**欠落 3 件**）・テスト件数（実測）・残タスク 4 件を記載。あわせて **文書に Anthropic 表記が残る一方で実装は Ollama 済み**であることを grep で確認し §4 に記録した |

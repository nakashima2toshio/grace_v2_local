# qa_generation/docs/ 棚卸し

**Version 1.1** | 最終更新: 2026-09-21

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

## 1. 目的別の入口

| やりたいこと | 読む文書 |
|---|---|
| **パイプライン全体の流れを知る** | [`pipeline.md`](pipeline.md) |
| **Q/A をどう生成しているか** | [`smart_qa_generator.md`](smart_qa_generator.md) |
| **カバレージをどう測るか** | [`semantic.md`](semantic.md) / [`evaluation.md`](evaluation.md) |
| **CLI から動かす** | [`../../qa_qdrant/docs/01_install.md`](../../qa_qdrant/docs/01_install.md) §6 |

---

## 2. 一覧

> 行数は **2026-09-20 の実測値**（`wc -l`）。

| 文書 | 対象実装 | 実装行数 | 文書行数 | Ver | 重要度 |
|---|---|---:|---:|---|:--:|
| [`pipeline.md`](pipeline.md) | `pipeline.py` — `QAPipeline`（Web / CLI 共通の実体） | 549 | 733 | 1.1 | ★★★ |
| [`smart_qa_generator.md`](smart_qa_generator.md) | `smart_qa_generator.py` — `SmartQAGenerator`（構造化出力 1 回） | 301 | 506 | 1.1 | ★★★ |
| [`semantic.md`](semantic.md) | `semantic.py` — `SemanticCoverage`（Embedding によるカバレージ） | 542 | 719 | 1.1 | ★★☆ |
| [`evaluation.md`](evaluation.md) | `evaluation.py` — `analyze_coverage()` ほか | 316 | 741 | — | ★★☆ |

---

## 3. 実装カバレッジ

`qa_generation/*.py` は **7 件**（`__init__.py` を含む）、対応する `<module>.md` は **4 件**。
**3 件が欠落している。**

| 実装 | 行数 | 文書 | 備考 |
|---|---:|---|---|
| `data_io.py` | 162 | ❌ **無い** | `load_uploaded_file()` ほか。`services/dataset_service.py` の削除後、**データセット読み込みの現役経路はこちら**（`docs/port_from_grace_v2_todo.md` §11） |
| `models.py` | 155 | ❌ **無い** | `QAPair` / `QAPairsList` ほか Pydantic モデル。構造化出力のスキーマそのもの |
| `__init__.py` | 65 | ❌ **無い** | パッケージ docstring にモジュール構成の要約がある |

→ **§6 の残タスク 1**。

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

### 4.1 `pipeline.py:341` の `provider="anthropic"` は死んだ引数

```python
tasks = submit_unified_qa_generation(
    chunks, self.config, self.model, provider="anthropic"
)
```

一見 CLAUDE.md §3 違反に見えるが、**受け取り側が使っていない**:

```python
# celery_tasks.py:63-67
def submit_unified_qa_generation(
        chunks, config, model,
        provider: str = "anthropic",  # 互換性のために残すが使用しない
) -> List:
```

`generate_qa_for_chunk_task.apply_async(args=(chunk, config, model))` に `provider` は
渡っておらず、実際のプロバイダはタスク側で解決される。**機能上の不具合ではないが
誤解を招く**ため、残タスク 3 として記録する（今回は実装に手を入れていない）。

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
| 1 | `data_io.md` / `models.md` / `__init__.md` が無い（§3）。とくに `data_io.py` は `dataset_service.py` 削除後の現役経路なので優先度が高い | 中 |
| 2 | ~~3 文書に Anthropic 前提の記述が残る~~ | ✅ **完了**（2026-09-21・§4）。`smart_qa_generator.md` / `pipeline.md` / `semantic.md` を Ollama 表記へ是正し、Version ヘッダーと変更履歴も追加した |
| 3 | `pipeline.py:341` の `provider="anthropic"`（死んだ引数・§4.1）。受け側（`celery_tasks.py:67`）ごと消せるか要確認。**機能上の不具合ではない**ので優先度は低い | 低 |
| 4 | ~~4 文書とも `**Version X.X**` ヘッダーが無い~~ | 🔶 **3 件完了**（2026-09-21）。残るは `evaluation.md` のみ | 低 |

---

## 7. テスト件数（実測）

**2026-09-20 に各ファイルを個別実行した実測値。記憶で書かないこと。**

| テストファイル | 件数 | 対象 |
|---|---:|---|
| `backend/tests/test_semantic.py` | 10 | `semantic.py` |
| `backend/tests/test_smart_qa_usage.py` | 4 | `smart_qa_generator.py` |
| `backend/tests/test_evaluation.py` | 1 | `evaluation.py` |

```bash
uv run --no-sync pytest backend/tests/test_semantic.py backend/tests/test_smart_qa_usage.py -q
```

> ⚠️ **`pipeline.py`（549 行）を直接対象にしたテストは無い。** `QAPipeline.run()` は
> Web / CLI 共通の実体なので、カバレッジの空白として認識しておくこと。

---

## 8. 変更履歴

| Version | 日付 | 変更 |
|---|---|---|
| 1.1 | 2026-09-21 | 残タスク 2 を完了（3 文書の Anthropic 表記を Ollama へ是正）。残タスク 4 も 3/4 完了（`evaluation.md` のみ残る） |
| 1.0 | 2026-09-20 | 新規作成。`qa_generation/docs/` だけ棚卸し索引が無かった。文書一覧・実装カバレッジ（**欠落 3 件**）・テスト件数（実測）・残タスク 4 件を記載。あわせて **文書に Anthropic 表記が残る一方で実装は Ollama 済み**であることを grep で確認し §4 に記録した |

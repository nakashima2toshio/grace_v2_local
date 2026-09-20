# services/docs/ 棚卸し

**Version 1.0** | 最終更新: 2026-09-20

> 📎 **姉妹版**: [`grace/docs/README.md`](../../grace/docs/README.md) /
> [`backend/docs/README.md`](../../backend/docs/README.md) /
> [`frontend/docs/README.md`](../../frontend/docs/README.md) /
> [`chunking/docs/README.md`](../../chunking/docs/README.md) /
> [`docs/README.md`](../../docs/README.md)（直下・配置の境界）

`services/docs/` 配下のドキュメントを一覧化する。**目的から入口を引ける**ようにするのが狙い。

---

## 1. 目的別の入口

| やりたいこと | 読む文書 |
|---|---|
| **パッケージ全体の構造を知る** | [`__init__.md`](__init__.md) — 再エクスポート対応表つき |
| **Qdrant を操作する** | [`qdrant_service.md`](qdrant_service.md) |
| **データ管理タブの裏側を知る** | [`data_pipeline_service.md`](data_pipeline_service.md) |
| **設定・ロガーを取り回す** | [`config_service.md`](config_service.md) |
| **トークン数・コストを数える** | [`token_service.md`](token_service.md) |

---

## 2. 一覧

> 行数・Ver は **2026-09-20 の実測値**（`wc -l` と各文書の Version ヘッダー）。

| 文書 | 対象実装 | 実装行数 | 文書行数 | Ver | 重要度 |
|---|---|---:|---:|---|:--:|
| [`__init__.md`](__init__.md) | `__init__.py` — 再エクスポート（`__all__` **51 件**） | 147 | 455 | 1.0 | ★★★ |
| [`qdrant_service.md`](qdrant_service.md) | `qdrant_service.py` — Qdrant CRUD・ヘルスチェック・Embedding 登録 | 1103 | 1541 | 2.0 | ★★★ |
| [`data_pipeline_service.md`](data_pipeline_service.md) | `data_pipeline_service.py` — データ準備の Web 向けラッパ層 | 475 | 859 | 1.0 | ★★★ |
| [`config_service.md`](config_service.md) | `config_service.py` — YAML / 環境変数・ロガー | 291 | 883 | 1.0 | ★★☆ |
| [`token_service.md`](token_service.md) | `token_service.py` — トークンカウント・コスト推定 | 353 | 829 | 1.0 | ★★☆ |
| [`json_service.md`](json_service.md) | `json_service.py` — 安全な JSON 入出力 | 283 | 677 | 1.0 | ★★☆ |
| [`cache_service.md`](cache_service.md) | `cache_service.py` — TTL 付きメモリキャッシュ | 258 | 889 | 1.0 | ★★☆ |
| [`qa_service.md`](qa_service.md) | `qa_service.py` — Q/A 生成（サブプロセス実行） | 225 | 630 | 1.0 | ★★☆ |
| [`log_service.md`](log_service.md) | `log_service.py` — 未回答質問ログ | 89 | 462 | 1.1 | ★☆☆ |
| [`prompts.md`](prompts.md) | `prompts.py` — 共通プロンプト定義 | 32 | 319 | 1.0 | ★☆☆ |
| [`agent_service.md`](agent_service.md) | `agent_service.py` — **Legacy ReAct**（§4 の注記を先に読むこと） | 539 | 616 | 2.0 | ★☆☆ |

---

## 3. 実装カバレッジ

`services/*.py` は **11 件**（`__init__.py` を含む）、対応する `<module>.md` も **11 件**。
**欠落は無い**（2026-09-20 に `data_pipeline_service.md` を作成して解消）。

> 📌 **2026-09-20 に `dataset_service.py` / `file_service.py` を削除した。**
> Streamlit 版アプリ（`ui/`）の名残で、`services/__init__.py` の再エクスポート以外に
> 呼び出し元が 1 件も無かった。対応する IPO 文書 2 件も同時に削除している。
> 経緯は `services/__init__.py` の docstring と `docs/port_from_grace_v2_todo.md` §11。

---

## 4. ⚠️ `agent_service.py` は Legacy ReAct 経路である

`ReActAgent` を**本番コードから呼んでいる箇所は 1 件も無い**
（2026-09-20 に `grep -rn "ReActAgent"` で確認。ヒットしたのは
`backend/tests/services/test_agent_service.py` と
`backend/tests/agents/test_agent_service_paris_income.py` のみ）。

Web 経路（`run_support_agent_core`）は `grace/executor.py` を通るため、
このモジュールは通らない。`agent_parallel_search.py` / `agent_cache.py` と同じ位置づけである
（CLAUDE.md §1）。

> ⚠️ **`agent_service.py` の docstring は「Anthropic Claude の Tool Use」と書いている。**
> 本リポジトリの LLM 既定は Ollama なので、CLAUDE.md §9.3 の表記統一に反する。
> Legacy 経路であるため今回は据え置いたが、**§6 の残タスク**として記録する。

---

## 5. 書き分けの約束

| 置き場所 | 書くもの | 書かないもの |
|---|---|---|
| `<module>.md` | IPO（入出力・副作用・使用例） | 運用手順・他モジュールの仕様 |
| `__init__.md` | 再エクスポート対応表・パッケージ全体の構造 | 各モジュールの IPO（リンクで参照） |
| `backend/docs/` | `backend/app/**` 側からの呼び出し方 | `services/` 内部の実装 |
| 直下 `docs/` | 2 領域以上にまたがる横断文書 | 1 モジュールの IPO |

> IPO 形式の仕様は `.claude/skills/grace-agent-docs/a_class_method_md_format.md`。
> **重複禁止ルールと正本の一覧は [`docs/README.md`](../../docs/README.md) §4 が持つ。**

---

## 6. 残タスク

| # | 内容 | 優先 |
|---|---|:--:|
| 1 | `agent_service.py` の docstring が `Anthropic Claude` 表記（CLAUDE.md §9.3 違反）。Legacy 経路のため実害は無いが、表記は揃えたい | 低 |
| 2 | `agent_service.md` も同様に Anthropic 前提の記述を含む可能性がある（未精査） | 低 |

---

## 7. テスト件数（実測）

**2026-09-20 に各ファイルを個別実行した実測値。記憶で書かないこと。**

| テストファイル | 件数 | 対象 |
|---|---:|---|
| `backend/tests/test_data_pipeline.py` | 30 | `data_pipeline_service` |
| `backend/tests/services/test_qdrant_service_legacy.py` | 21 | `qdrant_service`（旧 API） |
| `backend/tests/services/test_qdrant_service.py` | 19 | `qdrant_service` |
| `backend/tests/services/test_json_service.py` | 6 | `json_service` |
| `backend/tests/services/test_config_service.py` | 6 | `config_service` |
| `backend/tests/services/test_token_service.py` | 6 | `token_service` |
| `backend/tests/services/test_cache_service.py` | 4 | `cache_service` |
| `backend/tests/services/test_agent_service.py` | 4 | `agent_service`（Legacy） |
| `backend/tests/services/test_qa_service.py` | 3 | `qa_service` |
| `backend/tests/services/test_log_service.py` | 3 | `log_service` |

```bash
uv run --no-sync pytest backend/tests/services backend/tests/test_data_pipeline.py -q
```

---

## 8. 変更履歴

| Version | 日付 | 変更 |
|---|---|---|
| 1.0 | 2026-09-20 | 新規作成。`services/docs/` だけ棚卸し索引が無かった（`backend` / `grace` / `frontend` / `chunking` にはある）。文書一覧・実装カバレッジ・テスト件数（実測）・残タスクを記載。あわせて **`ReActAgent` に本番の呼び出し元が 1 件も無い**ことを grep で確認し §4 に記録した |

# データ管理（チャンキング / Q&A 生成 / Qdrant CRUD） — 文書索引

**Version 1.0** | 最終更新: 2026-09-20

データ準備まわり（チャンク化 → Q/A 生成 → Qdrant 登録 → コレクション管理）の
**文書がどこにあるかだけ**を示す索引である。

> ⚠️ **本書は IPO を持たない。** 実装の仕様は各領域の `docs/` が正本であり、
> ここへ内容を書き写さないこと。**1 つの内容を 2 箇所で保守すると、必ず片方が腐る**
> （[`docs/README.md`](docs/README.md) §4 の重複禁止ルール）。

> 📌 姉妹リポジトリ `grace_v2` の同名ファイルは「移転のお知らせ」である
> （2,313 行の IPO 文書を `backend/docs/` へ移し、索引に役割を変えた記録）。
> **本リポジトリにはその歴史が無い。** 最初から索引として作成している。

---

## 目次

- [1. 全体像から入る](#1-全体像から入る)
- [2. モジュール単位で読む](#2-モジュール単位で読む)
- [3. React 側](#3-react-側)
- [4. CLI から動かす](#4-cli-から動かす)
- [5. 変更履歴](#5-変更履歴)

---

## 1. 全体像から入る

| 知りたいこと | 文書 |
|---|---|
| **データ準備 3 工程の設計全体**（チャンク化 → Q/A 生成 → Qdrant 登録） | [`backend/docs/data_pipeline.md`](./backend/docs/data_pipeline.md) |
| 画面から何ができるか・操作と実装の対応 | [`README.md`](./README.md) §4.5「データ管理画面」 |
| 環境構築（Ollama / MeCab / Docker / Celery） | [`qa_qdrant/docs/01_install.md`](./qa_qdrant/docs/01_install.md) |
| Celery 並列の起動手順 | [`qa_qdrant/docs/celery_quick_start.md`](./qa_qdrant/docs/celery_quick_start.md) |
| 直下 `docs/` に何があるか | [`docs/README.md`](./docs/README.md) |

---

## 2. モジュール単位で読む

| 実装 | 文書 |
|---|---|
| `backend/app/api/data.py` — ジョブ起動・SSE・HITL | [`backend/docs/reference/api_data.md`](./backend/docs/reference/api_data.md) |
| `backend/app/api/qdrant.py` — Qdrant 参照 API（読み取り専用） | [`backend/docs/reference/api_qdrant.md`](./backend/docs/reference/api_qdrant.md) |
| `backend/app/core/data_jobs.py` — 4 種の runner・ステップ定義・CONFIRM の要否 | [`backend/docs/reference/core_data_jobs.md`](./backend/docs/reference/core_data_jobs.md) |
| `backend/app/core/job_logs.py` — 既存パッケージの `logging` を進捗イベントへ転送 | [`backend/docs/reference/core_job_logs.md`](./backend/docs/reference/core_job_logs.md) |
| `services/data_pipeline_service.py` — パス検証・Qdrant 操作・データ変換・**Ollama の事前確認** | [`services/docs/data_pipeline_service.md`](./services/docs/data_pipeline_service.md) |
| `services/qdrant_service.py` — Qdrant クライアント・ヘルスチェック | [`services/docs/qdrant_service.md`](./services/docs/qdrant_service.md) |
| `qa_generation/pipeline.py` — `QAPipeline` | [`qa_generation/docs/pipeline.md`](./qa_generation/docs/pipeline.md) |
| `chunking/csv_text_to_chunks_text_csv.py` — セマンティックチャンキング | [`chunking/docs/csv_text_to_chunks_text_csv.md`](./chunking/docs/csv_text_to_chunks_text_csv.md) |
| `chunking/` 全体の索引 | [`chunking/docs/README.md`](./chunking/docs/README.md) |

---

## 3. React 側

| コンポーネント | 文書 |
|---|---|
| `DataPanel.tsx` — データ管理タブの枠（サブタブ 4 つ） | [`frontend/docs/DataPanel.md`](./frontend/docs/DataPanel.md) |
| `DataJobPanel.tsx` — 3 種のジョブ（チャンキング / Q/A 作成 / Qdrant 登録） | [`frontend/docs/DataJobPanel.md`](./frontend/docs/DataJobPanel.md) |
| `CollectionPanel.tsx` — コレクション管理 | [`frontend/docs/CollectionPanel.md`](./frontend/docs/CollectionPanel.md) |
| フロント全体の索引 | [`frontend/docs/README.md`](./frontend/docs/README.md) |

---

## 4. CLI から動かす

画面（データ管理タブ）と CLI は**同じ関数**（`QAPipeline` など）を呼ぶので結果は変わらない。
`--resume` つきの大規模バッチは CLI の方が適している。

```bash
# 前提: 別ターミナルで ollama serve が動いていること
ollama serve

# 1. チャンク化
python -m chunking.csv_text_to_chunks_text_csv

# 2-3. Q/A 生成 + Qdrant 登録
python qa_qdrant/make_qa_register_qdrant.py
#   登録のみ: python qa_qdrant/register_to_qdrant.py
#   Celery 並列: --use-celery（先にワーカー起動が必要）
```

> ⚠️ **LLM はローカル（Ollama）で動く。API キーは不要**だが、`ollama serve` が
> 動いていないと全ジョブが落ちる。必要な API キーは Embedding 用の
> `GOOGLE_API_KEY` だけである（CLAUDE.md §3）。
>
> 📌 ジョブ開始前の事前確認（疎通・モデル pull 済みか）は
> `services/data_pipeline_service.py` の `ollama_unreachable_message()` /
> `model_not_pulled_message()` が行う。詳細は
> [`services/docs/data_pipeline_service.md`](./services/docs/data_pipeline_service.md) §4.4。

---

## 5. 変更履歴

| 版 | 日付 | 変更内容 |
|---|---|---|
| 1.0 | 2026-09-20 | 初版作成。データ準備まわりの文書が 13 ファイルに分散しており、入口が無かったため索引として作成した。**IPO は持たず、各領域の `docs/` へのリンクに徹する**（`docs/README.md` §4 の重複禁止ルール） |

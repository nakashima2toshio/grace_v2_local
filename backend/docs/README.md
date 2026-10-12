# backend/docs — 文書の地図

**Version 2.6** | 最終更新: 2026-10-11

---

`backend/`（FastAPI + パイプライン中核）の**入口**。どの文書に何が書いてあるか、
どの順に読むかだけを示す。

> ⚠️ **本リポジトリは Ollama（ローカル LLM）版。** LLM 用の API キーは**不要**で、
> 必要なのは Embedding 用の `GOOGLE_API_KEY`（Gemini `gemini-embedding-001`・3072 次元）だけ。
> 姉妹リポジトリ `grace_v2` は Anthropic 版で**表記が逆**なので、あちらの文書を持ち込まない
> （`CLAUDE.md` §5）。モデルセレクタ（`GET /api/models`）は grace_v2 にもあるが、
> 選択肢の中身（こちらは Ollama のモデルと tool calling 対応の注記、あちらは Anthropic の単価つき）が違う。

> **関連**: `grace/` 側は [`grace/docs/README_grace.md`](../../grace/docs/README_grace.md)、
> フロントは [`frontend/docs/`](../../frontend/docs/)、
> 横断の設計メモは [`docs/`](../../docs/)。

---

## 目次

- [1. 読む順路](#1-読む順路)
- [2. 文書一覧](#2-文書一覧)
- [3. 目的別の早見表](#3-目的別の早見表)
- [4. 文書を書くときの規約](#4-文書を書くときの規約)
- [5. 変更履歴](#5-変更履歴)

---

## 1. 読む順路

```
architecture.md            層構造・モジュール責務・外部境界（まずここ）
  └ job_runtime.md         ジョブ・SSE・HITL の共有基盤（3 系統に共通・必読）
      ├ support_flow.md    担当する系統だけ読む（Support: フロー＋設計）
      ├ review_flow.md     Review: フロー＋設計
      └ data_pipeline.md   データ準備
api_contract.md            フロント / API 利用者はここから
config_and_providers.md    モデル・プロバイダを触る前に
pitfalls.md                コードを触る前に（Ollama 固有の罠を含む）
../app/docs/・../app/api/docs/・../app/core/docs/   モジュール文書（引く。通読しない）
```

---

## 2. 文書一覧

### 2.1 横断（backend 全体を理解する）

| 文書 | 種別 | 何が書いてあるか |
|---|:--:|---|
| [`architecture.md`](./architecture.md) | A | 層構造、18 モジュールの責務と行数、**backend が持たないもの（外部境界）**、依存の向き、リクエストが通る経路 |
| [`job_runtime.md`](./job_runtime.md) | A | **3 系統が共有する実行基盤の正本。** ジョブのライフサイクル、イベントのリプレイ、runner 注入、HITL の橋渡し、ログ転送、ローカル LLM 前提の待ち時間 |
| [`api_contract.md`](./api_contract.md) | A | 全 25 エンドポイント（**`/api/models` `/api/model` を含む**）、SSE のワイヤ形式、ステータスの使い分け、`types.ts` 対応 |
| [`config_and_providers.md`](./config_and_providers.md) | A | **モデル名の解決経路**、`judge_model` / `detect_model` / `_resolve_model`、モデルセレクタ、Ollama の前提チェック |
| [`pitfalls.md`](./pitfalls.md) | B | 非自明な設計判断・過去に壊れた箇所・**Ollama 固有の罠**・直してはいけないもの |

### 2.2 系統別（何をどう判断しているか）

| 文書 | 種別 | 対象 |
|---|:--:|---|
| [`support_flow.md`](./support_flow.md) | A | GRACE-Support の処理フロー（0-(A)〜⑥）と設計判断。**WHY と HOW が 1 本**（v3.0 で設計 3 文書を統合） |
| [`review_flow.md`](./review_flow.md) | A | GRACE-Review の処理フロー（S1・①〜⑦）と設計判断（v2.0 で `review_agent_spec.md` を統合） |
| [`verticals_and_rulesets.md`](./verticals_and_rulesets.md) | A | 業界プロファイル（gov / saas / ec）とルールセット（ec_ad・23 ルール）の**カタログ**・増やし方 |
| [`data_pipeline.md`](./data_pipeline.md) | A | チャンク化 → Q/A 生成 → Qdrant 登録（**付録A: 規程コレクションの準備**） |
| [`webapp_flow.md`](./webapp_flow.md) | A | `run_dev.sh` 起点の end-to-end（旧 `react_processing_flow.md`） |

### 2.3 モジュール文書（`backend/app/**/docs/`）— 引く用

モジュール文書は 2026-10-11 に `backend/docs/reference/` から**各ディレクトリの `docs/`** へ移した（`a_cross_doc_md_format.md` §1.1.2。
`reference/api_meta.md` → `backend/app/api/docs/meta.md` のように接頭辞を外した）。種別はすべて E（`a_class_method_md_format.md` の IPO 形式。
使用例は IPO 詳細の冒頭 `### 4.1 使用例`（`api/docs/data.md`・`qdrant.md`・`core/docs/job_logs.md` は `### 3.1`））。

| ディレクトリ | 索引 | 文書 |
|---|---|---|
| `backend/app/`（`main.py` / `schemas.py`） | [`README_app.md`](../app/docs/README_app.md) | [`main.md`](../app/docs/main.md)・[`schemas.md`](../app/docs/schemas.md) |
| `backend/app/api/`（ルーター 5 本・25 エンドポイント） | [`README_api.md`](../app/api/docs/README_api.md) | [`support.md`](../app/api/docs/support.md)・[`review.md`](../app/api/docs/review.md)・[`data.md`](../app/api/docs/data.md)・[`qdrant.md`](../app/api/docs/qdrant.md)・[`meta.md`](../app/api/docs/meta.md) |
| `backend/app/core/`（Support の 1 周と判定） | [`README_core.md`](../app/core/docs/README_core.md) | [`support_agent.md`](../app/core/docs/support_agent.md)・[`gates.md`](../app/core/docs/gates.md)・[`verticals.md`](../app/core/docs/verticals.md) |
| 〃（Review の 1 周と判定） | 〃 | [`review_agent.md`](../app/core/docs/review_agent.md)・[`review_gates.md`](../app/core/docs/review_gates.md)・[`rulesets.md`](../app/core/docs/rulesets.md)・[`review_facts.md`](../app/core/docs/review_facts.md) |
| 〃（ジョブ基盤とデータ準備） | 〃 | [`jobs.md`](../app/core/docs/jobs.md)・[`intervention_bridge.md`](../app/core/docs/intervention_bridge.md)・[`job_logs.md`](../app/core/docs/job_logs.md)・[`data_jobs.md`](../app/core/docs/data_jobs.md) |

### 2.4 運用・記録

| 文書 | 種別 | 内容 |
|---|:--:|---|
| [`install_and_setup.md`](./install_and_setup.md) | B | 環境構築（`ollama serve` / `ollama pull` / `.env` / Qdrant）・起動手順 |
| [`tests.md`](./tests.md) | B | テストスイート索引（**§6 に GRACE-Review 系 18 ファイルの地図**） |
| [`migration_plan.md`](./migration_plan.md) | C | 文書再編の計画（Phase 1 完了 / Phase 2・3 の予定） |
| [`docs_audit.md`](./docs_audit.md) | C | 棚卸し・実装追随の照合結果・残タスク |

---

## 3. 目的別の早見表

| やりたいこと | 読む文書 |
|---|---|
| backend の全体像を掴む | `architecture.md` |
| SSE / ジョブ / HITL の仕組みを知る | `job_runtime.md` |
| 使うモデルを変える・セレクタを直す | `config_and_providers.md` |
| Ollama が繋がらない・404 になる | `config_and_providers.md` §5 → `pitfalls.md` §6 |
| 新しいエンドポイントを足す | `api_contract.md` → `job_runtime.md` §3 → `backend/app/api/docs/README_api.md` |
| 新しいジョブ種別を足す | `job_runtime.md` §3・§8 |
| 回答が escalate に倒れる理由を追う | `support_flow.md` → `backend/app/core/docs/gates.md` |
| 指摘が出ない / 誤検知する理由を追う | `review_flow.md` → `backend/app/core/docs/review_gates.md` |
| 触る前に地雷を確認する | `pitfalls.md` |

---

## 4. 文書を書くときの規約

| 対象 | 仕様書 |
|---|---|
| Python モジュール（IPO 形式） | `.claude/skills/grace-agent-docs/a_class_method_md_format.md` |
| React コンポーネント | `.claude/skills/grace-agent-docs/a_react_page_md_format.md` |
| 設計・フロー・API 契約・手順・索引（IPO 以外。上表の種別 A / B / C） | `.claude/skills/grace-agent-docs/a_cross_doc_md_format.md`（A: 概要に主な責務・各責務対応のモジュール・3 層の構成図／B: 概要に結論・対象モジュール／C: 目次と変更履歴） |
| 単体テスト（SAE 形式） | `.claude/skills/grace-agent-tests/a_test_md_format.md` |
| Mermaid のスタイル | `CLAUDE.md` §7（黒背景・白文字が**必須**） |

- 全文書に `**Version X.Y** | 最終更新: YYYY-MM-DD` のヘッダーを付ける
- **実装の表・定数を文書へ複製しない**（複製は必ず腐る。リンクで正本を指す）
- **テスト件数は実行して実測値を書く**（記憶で書かない）
- **技術スタック表記は `CLAUDE.md` §9.3 に従う**（`Ollama` / `gemma4:26b-a4b-it-qat` / Embedding は Gemini）

---

## 5. 変更履歴

| バージョン | 日付 | 変更内容 |
|---|---|---|
| 1.4 以前 | 〜2026-09-10 | [`docs_audit.md`](./docs_audit.md) を参照 |
| 2.0 | 2026-09-16 | 棚卸し内容を `docs_audit.md` へ分離し、README を**地図**に作り替えた。モジュール文書 13 本を `reference/` へ移動し、横断文書 5 本を新設した（再編 Phase 1） |
| 2.1 | 2026-09-16 | **Phase 2・3 を反映**。系統別を `support_flow.md` / `review_flow.md` の 2 本へ統合し、`verticals_and_rulesets.md` を新設、`react_processing_flow.md` を `webapp_flow.md` へ改称。`reference/` に欠けていた 4 本を追加した |
| 2.2 | 2026-09-23 | 冒頭の注記で「モデルセレクタはこちらにしかない」としていた記述を訂正（grace_v2 にも 2026-09-23 に入った。違うのは選択肢の中身） |
| 2.3 | 2026-09-24 | `a_cross_doc_md_format.md` v1.1（種別 C）に準拠（2026-09-24）。目次を追加し、§2 の各表へ「種別」列（A / B / C、`reference/` は E）を足し、§4 の規約表に横断文書フォーマットを追加した |
| 2.4 | 2026-10-08 | §4 の技術スタック表記の既定モデルを、CLAUDE.md §9.3 の現在の値 `gemma4:26b-a4b-it-qat` へ是正（旧既定 `gemma4:12b-mlx` のままだった） |
| 2.5 | 2026-10-10 | `grace/docs/` の構成整理（`README.md` → `README_grace.md`、`grace.md` / `grace_core.md` / `grace_core_flow.md` / `confidence_calibration.md` を `README_grace.md` / `grace_process_flow.md` / `grace_data_flow.md` へ統合）に合わせてリンクを直した |
| 2.6 | 2026-10-11 | モジュール文書 17 本を `reference/` から `backend/app/docs/`・`backend/app/api/docs/`・`backend/app/core/docs/` へ移したのに追随（§1・§2.3・§3）。§2.3 を 3 つの `README_<dir>.md` への索引に作り替え、文書の無かった `review_facts.md` を足した。`architecture.md` の説明のモジュール数を 18 に是正 |

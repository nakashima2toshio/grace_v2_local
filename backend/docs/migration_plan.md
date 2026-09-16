# backend/docs 再編計画 ドキュメント

**Version 3.0** | 最終更新: 2026-09-16

> **本書の位置づけ**: `backend/docs` を「横断 / 系統別 / 参照」の 3 階建てへ作り替える
> 計画と進捗。現在の構成は [`README.md`](./README.md)、棚卸しは [`docs_audit.md`](./docs_audit.md)。

> ⚠️ **姉妹リポジトリ `grace_v2`（Anthropic 版）は同じ再編を完了済みだが、文書はコピーしない。**
> 両リポジトリは双方向に乖離しており（`CLAUDE.md` §5）、こちらには
> **モデルセレクタ（`GET /api/models` / `/api/model`・`params.model`）と Ollama 固有の前提**が、
> 向こうには無い。横断文書はすべて**本リポジトリの実装から書き起こしている**。

---

## 目次

- [1. 再編の狙い](#1-再編の狙い)
- [2. Phase 1（完了・2026-09-16）](#2-phase-1完了2026-09-16)
- [3. Phase 2（完了・2026-09-16）系統別文書の統合](#3-phase-2完了2026-09-16系統別文書の統合)
- [4. Phase 3（完了・2026-09-16）欠落文書の作成と導線の追加](#4-phase-3完了2026-09-16欠落文書の作成と導線の追加)
- [5. 進め方の原則](#5-進め方の原則)
- [6. 変更履歴](#6-変更履歴)

---

## 1. 再編の狙い

再編前の `backend/docs` には 3 つの問題があった。

| # | 問題 | 根拠 |
|---|---|---|
| 1 | **共有基盤の説明が分散していた。** ジョブ・SSE・HITL は Support / Review / データ準備で同一実装なのに、説明が各系統の文書に散っていた | `api/review.py` と `api/data.py` の docstring が自ら「`api/support.py` と構造は同一」と書いている |
| 2 | **モジュール文書と読み物（設計・フロー）が同じ階層に平置きされ、読む順路が無かった** | `README.md` が棚卸し表で、入口として機能していなかった |
| 3 | **4 モジュールに文書が無い**（`api/data.py` / `api/qdrant.py` / `core/data_jobs.py` / `core/job_logs.py`） | `backend/app` の 17 モジュールに対し、文書は 13 本 |

狙いは **「横断＝ job_runtime / 系統＝ flow / 索引＝ reference」の 3 階建て**にして、
**同じ内容を 2 か所に持たない**こと。

---

## 2. Phase 1（完了・2026-09-16）

### 2.1 新設した横断文書（すべて本リポジトリの実装から書き起こし）

| 文書 | 何を集約したか | 本リポジトリ固有の内容 |
|---|---|---|
| `architecture.md` | 層構造・モジュール責務・外部境界・依存の向き | Ollama を外部境界に明示。`config` の 4 ヘルパー（`get_default_ollama_model` 等）への依存 |
| `job_runtime.md` | `jobs.py` / `intervention_bridge.py` / `job_logs.py` の共有基盤 | `params.model`、ローカル LLM の待ち時間と keepalive、並列は速くならない |
| `api_contract.md` | 全 25 エンドポイント・SSE・ステータス | `GET /api/models` / `GET /api/model`、**未対応モデル名は 422** |
| `config_and_providers.md` | モデル解決の 2 経路・3 つの解決関数 | モデルセレクタ、`ollama_unreachable_message` / `model_not_pulled_message` |
| `pitfalls.md` | 非自明な設計判断・過去の事故 | **Ollama 固有の罠**（`max_tokens` のみ・`json_schema` と `SchemaEchoError`・tool calling・未 pull 404・既定値を dataclass で評価しない） |

### 2.2 構成の変更

- モジュール文書 13 本（`api_*` 3 / `core_*` 8 / `main` / `schemas`）を **`reference/` へ移動**
- `README.md`（棚卸し）を **`docs_audit.md`** へ改称し、README を**地図**に作り替えた
- `grace/step_trace/docs/README.md` の**壊れていたリンク 3 件**を是正（`docs/` を指していたが実体は `backend/docs/`。再編前からの不具合）

### 2.3 Phase 1 で意図的にやらなかったこと

**内容の統合・削除は 1 件も行っていない。** `support_flow.md`（1,092 行）等の
大きな文書はそのまま残っている（Phase 2 で扱う）。

---

## 3. Phase 2（完了・2026-09-16）系統別文書の統合

**目的**: 系統ごとに 1 本へ寄せ、共有基盤の重複を `job_runtime.md` へ移す。
姉妹リポジトリ `grace_v2` と**結果の構造は揃えた**が、**中身はこちらの実装から書いた**。

### 3.0 実施結果

| 統合前 | 行数 | 統合後 |
|---|---:|---|
| `backend_flow.md` | 921 | **`support_flow.md` v3.0（1,851 行）へ改称・骨格** |
| `agent_support_example.md` | 1,092 | 同 §5 設計判断（回答ポリシー / データ契約 / ActionTool 案 / HITL / 処理シーケンス）・§6 KPI・§10 ロードマップ・付録A CLI。**§7 の関数 IPO は破棄**（`reference/core_*.md` が正本） |
| `agent_support_example_flow.md` | 491 | 同 **付録B**（1 コマンド実行トレース） |
| `confidence_flow_grace_vs_backend.md` | 258 | 同 **§3.2**（信頼度フローの比較） |
| `agent_support_verticals.md` | 417 | **`verticals_and_rulesets.md` §1**（新設） |
| `review_flow.md` | 663 | **`review_flow.md` v2.0（1,174 行）** の骨格 |
| `review_agent_spec.md` | 1,005 | 同 §1 設計方針・§4 各段の `設計仕様`・§8 データモデル・§9 未決事項・付録B。RuleSet 定義 → `verticals_and_rulesets.md` §2、ジョブ基盤 → `job_runtime.md` §3、API → `api_contract.md`、テスト方針 → `tests.md` §6 |
| `review_rules_collection.md` | — | **`data_pipeline.md` 付録A** |
| `react_processing_flow.md` | 657 | **`webapp_flow.md` へ改称**（`React` と `ReAct` の取り違えを避ける） |

### 3.1 あわせて是正したこと（実装と照合して判明）

| # | 内容 |
|---|---|
| 1 | **`STEP_IDS` の 0-(A) `analyze`（入力・質問分析）が文書に無かった。** 旧 `backend_flow.md` は 8 ステップしか書いておらず、複数質問の検知 → 選択 → 再構成が丸ごと欠けていた。実装（`support_agent.py` / `gates.py`）から **§4.0 として新規に書き起こした** |
| 2 | **ステップ番号の体系が旧番号 `(0)〜(8)` のままだった。** `CLAUDE.md` §1 の体系（`0-(A)` `0-(B)` `①`〜`⑥` `④'`）へ統一した |
| 3 | **ルール件数が 21 のまま取り残されていた**（実装は 23）。`reference/core_rulesets.md` の一覧に `yakki-04`（安全性の保証表現）と `policy-01`（表示内容と社内規程の不一致）を追加し、件数・`always_check`（6 → 7）・モジュール構成図・`review_agent.py` のコメント（4,200 → 4,600 回）も実測値へ是正した |
| 4 | 旧 `review_agent_spec.md` §9 が挙げていた **`test_review_segment.py` は存在しない**（① Segment の検証は `test_review_agent_core.py`）。`tests.md` §6 に実測のファイル別件数で置き換えた |

### 3.2 移送せずリンクへ集約したもの

- 旧 `review_agent_spec.md` §6 ジョブ基盤の汎用化 → `job_runtime.md` §3 が既に同内容
- 旧 `review_agent_spec.md` §7.1 / §7.2 API 設計 → `api_contract.md`
- 旧 `review_agent_spec.md` §8 フロントエンド設計 → `frontend/docs/`
- 旧 `review_agent_spec.md` §10 実装計画 → 実装完了済みのため引き継がない（git 履歴に残る）
- 旧フロー 2 文書の「クラス・関数一覧表」→ `reference/core_*.md`（3 重管理だった）

## 4. Phase 3（完了・2026-09-16）欠落文書の作成と導線の追加

### 4.1 欠けていた 4 モジュールの IPO 文書を作成

`backend/app` の 17 モジュールに対し、文書は 13 本しかなかった。実装から書き起こして 17 本に揃えた。

| 作成した文書 | 対象 | 対象の行数 |
|---|---|---:|
| `reference/api_data.md` | `backend/app/api/data.py` | 197 |
| `reference/api_qdrant.md` | `backend/app/api/qdrant.py` | 200 |
| `reference/core_data_jobs.md` | `backend/app/core/data_jobs.py` | 857 |
| `reference/core_job_logs.md` | `backend/app/core/job_logs.py` | 193 |

### 4.2 全 17 文書へ位置づけと上位文書への導線を追加

3 階建てにしたのに `reference/` から上位へ戻る導線が無く、**リファレンスを入口にした人が
設計文書へ辿り着けなかった**。各文書の冒頭に「本書の位置づけ」ブロックを置き、
Version を 1 つ上げて変更履歴にも記録した。

### 4.3 公開シンボルの網羅を 100% にした

AST による照合（`docs_audit.md` の検証手順）で **20 件の未記載**が見つかったので、
実装を読んで追記した。

| 文書 | 追記したシンボル |
|---|---|
| `core_review_agent.md` | `DOCUMENT_SEGMENT_ID` / `DOCUMENT_EXCERPT_MAX_CHARS` / `DOCUMENT_EXCERPT_MAX_RATIO` / `_LIST_RE` / `_HEADING_RE` / `_SENTENCE_END_RE` / `_document_segment` / `_is_too_broad`（§5.3・§5.4 を新設） |
| `core_verticals.md` | `JUDGE_MAX_OUTPUT_TOKENS` / `MULTI_QUESTION_MAX_OUTPUT_TOKENS` / `build_closing_instruction` / `_links_instruction` |
| `core_rulesets.md` | `DEFAULT_EVIDENCE_MIN_SCORE` / `DEFAULT_EVIDENCE_TOP_RATIO` / `retrieval_query` |
| `core_review_gates.md` | `select_document_rules` / `_brief` |
| `schemas.md` | `QuestionClusterModel` / `_validate_model_choice` |
| `api_meta.md` | `list_models` |

**結果（実測 2026-09-16）**: 17 文書 / **237 シンボル中 未記載 0**。

> 📝 姉妹リポジトリ `grace_v2` の Phase 3 は「参照文書の圧縮」を計画して実測の結果**見送った**が、
> 本リポジトリでは事情が違った。こちらは**文書そのものが 4 本欠けており、シンボル網羅も
> 100% ではなかった**ため、圧縮ではなく**欠落の補完**が Phase 3 の中身になった。

## 5. 進め方の原則

1. **1 Phase = 1 PR。** 移動と内容変更を同じコミットに混ぜない
2. **統合は「削除」ではなく「移送」。** 行き先を本書の表に明記してから動かす
3. **姉妹リポジトリの文書をコピーしない。** 構造だけ揃え、中身は実装から書く
4. **実装の表・定数を文書へ複製しない**
5. **リンクは機械的に検証する**（全 Markdown の相対リンクが解決すること）
6. **数値（行数・件数・テスト数）は実測値を書く**

---

## 6. 変更履歴

| Version | 日付 | 変更内容 |
|---|---|---|
| 3.0 | 2026-09-16 | **Phase 3 を実施し、結果（§4）を記録**。欠けていた 4 モジュールの IPO 文書を作成し、17 文書へ位置づけを追加、AST で見つかった未記載 20 件を追記して**網羅 237/237** にした |
| 2.0 | 2026-09-16 | **Phase 2 を実施し、結果（§3.0〜§3.2）を記録**。あわせて実装との食い違い 4 件（0-(A) の欠落・旧番号体系・ルール件数 21→23・存在しないテストファイル）を是正した |
| 1.0 | 2026-09-16 | 新規作成。Phase 1 の完了内容と、Phase 2・3 の移送計画を記載した |

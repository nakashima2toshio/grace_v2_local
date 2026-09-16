# backend/docs 再編計画 ドキュメント

**Version 1.0** | 最終更新: 2026-09-16

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
- [3. Phase 2（未着手）系統別文書の統合](#3-phase-2未着手系統別文書の統合)
- [4. Phase 3（未着手）欠落しているモジュール文書の作成](#4-phase-3未着手欠落しているモジュール文書の作成)
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

**内容の統合・削除は 1 件も行っていない。** `agent_support_example.md`（1,092 行）等の
大きな文書はそのまま残っている（Phase 2 で扱う）。

---

## 3. Phase 2（未着手）系統別文書の統合

**目的**: 系統ごとに 1 本へ寄せ、共有基盤の重複を `job_runtime.md` へ移す。
姉妹リポジトリ `grace_v2` が同じ統合を済ませているので、**結果の構造は揃える**が、
**中身はこちらの実装から書く**（モデルセレクタ・Ollama 前提が向こうには無い）。

### 3.1 Support（5 文書 → 1 本 `support_flow.md`）

| 統合前 | 行数 | 行き先 |
|---|---:|---|
| `backend_flow.md` | 921 | **`support_flow.md` へ改称**（処理ステップ IPO が骨格） |
| `agent_support_example.md` | 1,092 | 設計判断（回答ポリシー・HITL・データ契約）→ 統合先の「設計判断」節。§7 の関数 IPO は**破棄**（`reference/core_gates.md` 等が正本） |
| `agent_support_verticals.md` | 417 | **新設 `verticals_and_rulesets.md` §1** へ |
| `agent_support_example_flow.md` | 491 | 統合先の付録（1 コマンド実行トレース） |
| `confidence_flow_grace_vs_backend.md` | 258 | 統合先の「信頼度フローの比較」節 |

### 3.2 Review（2 文書 → 1 本 `review_flow.md`）

| 統合前 | 行数 | 行き先 |
|---|---:|---|
| `review_flow.md` | 663 | 骨格（IPO） |
| `review_agent_spec.md` | 1,005 | 各ステップの設計仕様を IPO の直下へ。RuleSet 定義 → `verticals_and_rulesets.md` §2、ジョブ基盤の汎用化 → `job_runtime.md` §3、API 設計 → `api_contract.md`、テスト方針 → `tests.md` |
| `review_rules_collection.md` | — | `data_pipeline.md` の付録へ |

### 3.3 新設 `verticals_and_rulesets.md`

`VerticalProfile`（gov / saas / ec）と `RuleSet` の**カタログ**＋増やし方。
**ルール本文とキーワードは複製しない**（`rulesets.py` を正本として指す）。

### 3.4 `react_processing_flow.md` の扱い

`run_dev.sh` 起点の end-to-end。`grace_v2` では `webapp_flow.md` へ改称している
（`React`（フロントエンド）と `ReAct`（推論パターン）の取り違えを避けるため）。
本リポジトリでも同じ改称を検討する。

### 3.5 ステップ番号の体系

`CLAUDE.md` §1 の体系（`0-(A)` `0-(B)` `①`〜`⑥` `④'`）へ統一し、
`STEP_IDS` の 9 段すべてが記述されているか照合する。

---

## 4. Phase 3（未着手）欠落しているモジュール文書の作成

`reference/` に無い 4 本を、実装から IPO 形式（`a_class_method_md_format.md`）で書く。

| 作成する文書 | 対象 | 行数 |
|---|---|---:|
| `reference/api_data.md` | `backend/app/api/data.py` | 197 |
| `reference/api_qdrant.md` | `backend/app/api/qdrant.py` | 200 |
| `reference/core_data_jobs.md` | `backend/app/core/data_jobs.py` | 857 |
| `reference/core_job_logs.md` | `backend/app/core/job_logs.py` | 193 |

あわせて、`reference/` の各文書へ**位置づけと上位文書への導線**を追加し、
AST による公開シンボルの網羅（`docs_audit.md` の検証手順）を実測する。

---

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
| 1.0 | 2026-09-16 | 新規作成。Phase 1 の完了内容と、Phase 2・3 の移送計画を記載した |

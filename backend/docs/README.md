# backend/docs — 文書の地図

**Version 2.0** | 最終更新: 2026-09-16

`backend/`（FastAPI + パイプライン中核）の**入口**。どの文書に何が書いてあるか、
どの順に読むかだけを示す。

> ⚠️ **本リポジトリは Ollama（ローカル LLM）版。** LLM 用の API キーは**不要**で、
> 必要なのは Embedding 用の `GOOGLE_API_KEY`（Gemini `gemini-embedding-001`・3072 次元）だけ。
> 姉妹リポジトリ `grace_v2` は Anthropic 版で**表記が逆**なので、あちらの文書を持ち込まない
> （`CLAUDE.md` §5）。モデルセレクタ（`GET /api/models`）は**こちらにしかない**機能である。

> **関連**: `grace/` 側は [`grace/docs/README.md`](../../grace/docs/README.md)、
> フロントは [`frontend/docs/`](../../frontend/docs/)、
> 横断の設計メモは [`docs/`](../../docs/)。

---

## 1. 読む順路

```
architecture.md            層構造・モジュール責務・外部境界（まずここ）
  └ job_runtime.md         ジョブ・SSE・HITL の共有基盤（3 系統に共通・必読）
      ├ backend_flow.md    担当する系統だけ読む（Support のフロー）
      ├ review_flow.md     Review のフロー
      └ data_pipeline.md   データ準備
api_contract.md            フロント / API 利用者はここから
config_and_providers.md    モデル・プロバイダを触る前に
pitfalls.md                コードを触る前に（Ollama 固有の罠を含む）
reference/*.md             引く（通読しない）
```

---

## 2. 文書一覧

### 2.1 横断（backend 全体を理解する）

| 文書 | 何が書いてあるか |
|---|---|
| [`architecture.md`](./architecture.md) | 層構造、17 モジュールの責務と行数、**backend が持たないもの（外部境界）**、依存の向き、リクエストが通る経路 |
| [`job_runtime.md`](./job_runtime.md) | **3 系統が共有する実行基盤の正本。** ジョブのライフサイクル、イベントのリプレイ、runner 注入、HITL の橋渡し、ログ転送、ローカル LLM 前提の待ち時間 |
| [`api_contract.md`](./api_contract.md) | 全 25 エンドポイント（**`/api/models` `/api/model` を含む**）、SSE のワイヤ形式、ステータスの使い分け、`types.ts` 対応 |
| [`config_and_providers.md`](./config_and_providers.md) | **モデル名の解決経路**、`judge_model` / `detect_model` / `_resolve_model`、モデルセレクタ、Ollama の前提チェック |
| [`pitfalls.md`](./pitfalls.md) | 非自明な設計判断・過去に壊れた箇所・**Ollama 固有の罠**・直してはいけないもの |

### 2.2 系統別（何をどう判断しているか）

| 文書 | 対象 |
|---|---|
| [`backend_flow.md`](./backend_flow.md) | GRACE-Support の処理フロー（ステップ詳細） |
| [`agent_support_example.md`](./agent_support_example.md) | GRACE-Support の設計判断（回答ポリシー・HITL・データ契約） |
| [`agent_support_verticals.md`](./agent_support_verticals.md) | 業界特化（gov / saas / ec）の設計 |
| [`agent_support_example_flow.md`](./agent_support_example_flow.md) | 1 コマンド実行トレース |
| [`confidence_flow_grace_vs_backend.md`](./confidence_flow_grace_vs_backend.md) | `grace/` と `backend/` の信頼度フロー比較 |
| [`review_flow.md`](./review_flow.md) | GRACE-Review の処理フロー（S1・①〜⑦） |
| [`review_agent_spec.md`](./review_agent_spec.md) | GRACE-Review の設計判断 |
| [`review_rules_collection.md`](./review_rules_collection.md) | 規程コレクションの準備 |
| [`data_pipeline.md`](./data_pipeline.md) | チャンク化 → Q/A 生成 → Qdrant 登録 |
| [`react_processing_flow.md`](./react_processing_flow.md) | `run_dev.sh` 起点の end-to-end（React 画面 → FastAPI → コア） |

> ⚠️ **系統別は 2 本立て（spec / flow）のままで、統合は Phase 2 の予定**である
> （[`migration_plan.md`](./migration_plan.md)）。姉妹リポジトリ `grace_v2` は統合済みだが、
> **両リポジトリは双方向に乖離しているので文書をコピーしない**。

### 2.3 モジュール参照（`reference/`）— 引く用

| 対象 | 文書 |
|---|---|
| `backend/app/main.py` / `schemas.py` | [`reference/main.md`](./reference/main.md) / [`reference/schemas.md`](./reference/schemas.md) |
| `api/support.py` / `api/review.py` / `api/meta.py` | [`reference/api_support.md`](./reference/api_support.md)・[`api_review.md`](./reference/api_review.md)・[`api_meta.md`](./reference/api_meta.md) |
| `core/support_agent.py` / `gates.py` / `verticals.py` | [`reference/core_support_agent.md`](./reference/core_support_agent.md)・[`core_gates.md`](./reference/core_gates.md)・[`core_verticals.md`](./reference/core_verticals.md) |
| `core/review_agent.py` / `review_gates.py` / `rulesets.py` | [`reference/core_review_agent.md`](./reference/core_review_agent.md)・[`core_review_gates.md`](./reference/core_review_gates.md)・[`core_rulesets.md`](./reference/core_rulesets.md) |
| `core/jobs.py` / `intervention_bridge.py` | [`reference/core_jobs.md`](./reference/core_jobs.md)・[`core_intervention_bridge.md`](./reference/core_intervention_bridge.md) |

> ⚠️ **4 モジュールに対応する文書がまだ無い**: `api/data.py` / `api/qdrant.py` /
> `core/data_jobs.py` / `core/job_logs.py`。Phase 3 で作成する（[`migration_plan.md` §4](./migration_plan.md)）。

### 2.4 運用・記録

| 文書 | 内容 |
|---|---|
| [`install_and_setup.md`](./install_and_setup.md) | 環境構築（`ollama serve` / `ollama pull` / `.env` / Qdrant）・起動手順 |
| [`tests.md`](./tests.md) | テスト方針 |
| [`migration_plan.md`](./migration_plan.md) | 文書再編の計画（Phase 1 完了 / Phase 2・3 の予定） |
| [`docs_audit.md`](./docs_audit.md) | 棚卸し・実装追随の照合結果・残タスク |

---

## 3. 目的別の早見表

| やりたいこと | 読む文書 |
|---|---|
| backend の全体像を掴む | `architecture.md` |
| SSE / ジョブ / HITL の仕組みを知る | `job_runtime.md` |
| 使うモデルを変える・セレクタを直す | `config_and_providers.md` |
| Ollama が繋がらない・404 になる | `config_and_providers.md` §5 → `pitfalls.md` §6 |
| 新しいエンドポイントを足す | `api_contract.md` → `job_runtime.md` §3 |
| 新しいジョブ種別を足す | `job_runtime.md` §3・§8 |
| 回答が escalate に倒れる理由を追う | `backend_flow.md` → `reference/core_gates.md` |
| 指摘が出ない / 誤検知する理由を追う | `review_flow.md` → `reference/core_review_gates.md` |
| 触る前に地雷を確認する | `pitfalls.md` |

---

## 4. 文書を書くときの規約

| 対象 | 仕様書 |
|---|---|
| Python モジュール（IPO 形式） | `.claude/skills/grace-agent-docs/a_class_method_md_format.md` |
| React コンポーネント | `.claude/skills/grace-agent-docs/a_react_page_md_format.md` |
| 単体テスト（SAE 形式） | `.claude/skills/grace-agent-tests/a_test_md_format.md` |
| Mermaid のスタイル | `CLAUDE.md` §7（黒背景・白文字が**必須**） |

- 全文書に `**Version X.Y** | 最終更新: YYYY-MM-DD` のヘッダーを付ける
- **実装の表・定数を文書へ複製しない**（複製は必ず腐る。リンクで正本を指す）
- **テスト件数は実行して実測値を書く**（記憶で書かない）
- **技術スタック表記は `CLAUDE.md` §9.3 に従う**（`Ollama` / `gemma4:12b-mlx` / Embedding は Gemini）

---

## 5. 変更履歴

| Version | 日付 | 変更内容 |
|---|---|---|
| 2.0 | 2026-09-16 | 棚卸し内容を `docs_audit.md` へ分離し、README を**地図**に作り替えた。モジュール文書 13 本を `reference/` へ移動し、横断文書 5 本を新設した（再編 Phase 1） |
| 1.4 以前 | 〜2026-09-10 | [`docs_audit.md`](./docs_audit.md) を参照 |

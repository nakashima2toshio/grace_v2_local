# エージェント階層（L0〜L4）— 一般用語と grace_v2_local 実装の対応

**Version 1.0** | 最終更新: 2026-09-20

一般的な LLM エージェント用語（ReAct / Planner-Executor / LLM-as-a-Judge / HITL 等）が
**grace_v2_local のどのモジュールに対応するか**を、粒度 L0〜L4 で対応づける。
「Agent が何本走っているのか」を実装から把握するための索引である。

> ⚠️ **本書は対応表であり、仕様書ではない。** ステップの実行順は
> [`pipelines.md`](pipelines.md)、判定機構の詳細は [`guardrails.md`](guardrails.md)、
> 関数の IPO は各領域の `docs/` が正本（[`README.md`](README.md) §4）。

> ⚠️ **本リポジトリは Ollama 版。** LLM は `gemma4:12b-mlx`
> （`config.py::get_default_ollama_model()`）で **API キーは不要**、
> Embedding のみ Gemini（`gemini-embedding-001`・3072 次元・`GOOGLE_API_KEY`）。
> 姉妹リポジトリ `grace_v2` は Anthropic 版（CLAUDE.md §3・§5）。

---

## 目次

- [1. 前提 — 「Agent が複数走る」の実態](#1-前提--agent-が複数走るの実態)
- [2. 階層の定義](#2-階層の定義)
- [3. L0 — 単発のモデル呼び出し](#3-l0--単発のモデル呼び出し)
- [4. L0.5 — 単発 LLM 判定器](#4-l05--単発-llm-判定器)
- [5. L1 — ツールループ（ReAct）](#5-l1--ツールループreact)
- [6. L2 — 役割分離（本リポジトリには弱い形のみ）](#6-l2--役割分離本リポジトリには弱い形のみ)
- [7. L3 — オーケストレーション](#7-l3--オーケストレーション)
- [8. L4 — 実行基盤と UI](#8-l4--実行基盤と-ui)
- [9. 階層に属さないもの](#9-階層に属さないもの)
- [10. ローカル LLM であることが効く箇所](#10-ローカル-llm-であることが効く箇所)
- [11. 逆引き表（一般用語 → 実装）](#11-逆引き表一般用語--実装)
- [12. 関連ドキュメント](#12-関連ドキュメント)
- [13. 変更履歴](#13-変更履歴)

---

## 1. 前提 — 「Agent が複数走る」の実態

モデル本体（1 回の推論）の中に Agent は存在しない。**Agent はモデルの外側で
アプリが組む構造**であり、同じモデルに違うプロンプト・違うツール・違う文脈を
与えて何度も呼ぶ、その「役割を持った呼び出し」の単位を指す。

本リポジトリの実態は次のとおり。

| 問い | 答え |
|---|---|
| 独立文脈のサブエージェントを起動するか | **しない**（§6） |
| 何本のループが走るか | **1 本**（`Executor` の静的パスまたは ReAct パス） |
| 役割分離はどこにあるか | クラス分離（Planner / Executor / Verifier …）。文脈は共有 |
| 厚みがある層はどこか | **L0.5 と L3** |
| LLM はどこで動くか | **すべてローカル**（Ollama・`http://localhost:11434/v1`） |

---

## 2. 階層の定義

| 層 | 単位 | 終了条件を決めるのは |
|---|---|---|
| **L0** | API 1 回 | なし（1 往復） |
| **L0.5** | 1 回の LLM 判定 | なし（1 往復・出力は判定値） |
| **L1** | ツールループ | **LLM** |
| **L2** | 独立文脈のサブエージェント | LLM |
| **L3** | 固定パイプライン | **アプリのコード** |
| **L4** | 常駐プロセス・UI | 外部イベント |

```mermaid
flowchart TB
    subgraph Base["L0 / L0.5 — 単発呼び出し"]
        L0["L0: helper_llm.OllamaClient<br>generate_content / generate_structured"]
        L05["L0.5: gates.py / review_gates.py / confidence.py<br>判定器（1 往復で 1 判定）"]
    end
    subgraph Loop["L1 — ツールループ"]
        L1["executor.execute_react_generator<br>終了条件を LLM が決める"]
    end
    subgraph Orch["L3 — オーケストレーション"]
        L3["support_agent / review_agent<br>順序はアプリのコードが決める"]
    end
    subgraph Infra["L4 — 実行基盤と UI"]
        L4["FastAPI + JobManager + SSE<br>Vite + React 18"]
    end
    L0 --> L05
    L05 --> L3
    L0 --> L1
    L1 --> L3
    L3 --> L4
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class L0,L05,L1,L3,L4 default
style Base fill:#1a1a1a,stroke:#fff,color:#fff
style Loop fill:#1a1a1a,stroke:#fff,color:#fff
style Orch fill:#1a1a1a,stroke:#fff,color:#fff
style Infra fill:#1a1a1a,stroke:#fff,color:#fff
```

> **L2 のサブグラフが無いのは誤記ではない。** §6 のとおり本リポジトリに L2 は無い。

---

## 3. L0 — 単発のモデル呼び出し

入力 → 出力の 1 往復。状態も終了判定も持たない。

| 実装 | 中身 | 一般用語 |
|---|---|---|
| `helper/helper_llm.py` — `LLMClient`（ABC）/ `OllamaClient` / `create_llm_client` | プロバイダ抽象。**既定は `"ollama"`** | Provider abstraction |
| 〃 `generate_content` / `generate_structured` | テキスト生成 / スキーマ付き生成 | Completion / Structured Output |
| 〃 `generate_with_tools` → `ToolUseResponse` | ツール定義を渡し「次に呼ぶツール」を返させる | Function calling / Tool use |
| 〃 `SchemaEchoError` | **スキーマ定義そのものをオウム返しされた**ことを名指しで検知 | Output validation |
| 〃 `_resolve_schema_refs` | `$defs` / `$ref` を展開してから Ollama へ渡す | Schema flattening |
| 〃 `_parse_text_tool_calls` | tool call をテキストで返すモデルの救済 | Output parsing / repair |
| `grace/llm_compat.py` — `OllamaGenaiClient` / `create_chat_client` | genai 形の呼び出しを Ollama へ差し替える互換層 | Compatibility shim |
| 〃 `AnthropicGenaiClient` | **後方互換**（`provider="anthropic"` を明示したときだけ動く。grace_v2 との A/B 用） | — |
| 〃 `parse_score` | 「数値だけ返せ」が守られない前提でスコアを取り出す | Output parsing |
| 〃 `_strip_think` / `_strip_to_json` / `_schema_hint` | 思考タグ除去・JSON 強制・スキーマヒント注入 | Output repair |
| `helper/helper_embedding.py` — `GeminiEmbedding` / `create_embedding_client` | **Embedding のみ Gemini**（3072 次元） | Embedding model |
| `grace/config.py` — `GraceConfig` / `ConfigLoader` | yml → 環境変数（`GRACE_`）→ pydantic の 3 段検証 | Config resolution |
| `grace/schemas.py` — `PlanStep` / `ExecutionPlan` / `StepResult` / `ExecutionResult` | 層間で受け渡す型 | Typed contracts |

> ⚠️ **`OllamaClient` は出力上限パラメータを `max_tokens` に正規化する。**
> `max_completion_tokens` / `max_output_tokens` は Ollama では通らない（CLAUDE.md §3）。

---

## 4. L0.5 — 単発 LLM 判定器

ループを持たず、1 回呼んで 1 つの判定を返す。**本リポジトリで最も厚い層のひとつ。**
実装形式が統一されており、ファクトリがクロージャを返す（`create_*(config) -> Callable`）ため
テストでスタブに差し替えられる。

| 実装 | 判定器 | 判定内容 | 一般用語 |
|---|---|---|---|
| `backend/app/core/gates.py` | `create_intent_classifier` | 問い合わせの意図分類 | Intent classifier |
| 〃 | `create_no_info_judge` | 「情報が無い」系の回答か（④'） | Refusal detector |
| 〃 | `create_question_analyzer` | 複数質問の検知・主従分離（0-(A)） | Query decomposition |
| 〃 | `create_cluster_analyzer` | 質問クラスタの抽出 | Clustering |
| 〃 | `create_scope_classifier` | 各質問が業界プロファイルの範囲内か | Scope guard |
| `backend/app/core/review_gates.py` | `create_violation_detector` | 規程違反か（二段判定） | LLM-as-a-Judge |
| 〃 | `create_mention_classifier` | 言及の性質分類 | Classifier |
| 〃 | `create_vacuous_judge` | 指摘が空虚（無内容）でないか | Self-critique filter |
| `grace/confidence.py` | `GroundednessVerifier.verify` | claim 単位の supported / contradicted / neutral | Faithfulness / Entailment |
| 〃 | `LLMSelfEvaluator.evaluate` | 出力の自己評価 | Self-evaluation |
| 〃 | `QueryCoverageCalculator` | 質問を回答がカバーしているか | Answer relevance |
| 〃 | `SourceAgreementCalculator` | 出典どうしの一致度 | Self-consistency |
| `grace/planner.py` | `Planner.estimate_complexity_with_llm` | クエリ複雑度 | Difficulty routing |

> ⚠️ **この層はモデル解決を `judge_model()` / `detect_model()` に集約している。**
> UI のモデルセレクタが上書きするのは `llm.model` だけで、判定系が使う
> `light_model` は上書きしない。
>
> 📌 **本リポジトリでは `model` と `light_model` が既定で同じ**（ともに
> `get_default_ollama_model()`）。Anthropic 版のように sonnet / haiku で
> 階層化されてはいない。

---

## 5. L1 — ツールループ（ReAct）

LLM が次の 1 手を決め、ツールを呼び、結果を見てまた決める。
**終了条件を LLM が決める**ため、ここからが Agent。

| 実装 | 役割 | 一般用語 |
|---|---|---|
| `grace/tools.py` — `BaseTool` / `ToolResult` / `ToolRegistry` | ツールの抽象と登録簿 | Tool interface / Registry |
| 〃 `RAGSearchTool` | Qdrant 検索（コレクションを直列に探索し閾値で早期 `break`） | Retrieval tool |
| 〃 `ReasoningTool` | 検索結果から回答を生成 | Synthesis tool |
| 〃 `WebSearchTool` | DDG / Google / SerpAPI を backend 切替 | Web search tool |
| 〃 `AskUserTool` | 人間に問う | HITL tool |
| 〃 `CodeExecuteTool` | 静的チェック ＋ CPU / メモリ制限 | Sandboxed execution |
| `grace/executor.py::execute_react_generator` | ReAct ループ本体 | ReAct loop |
| 〃 `_decide_next_action` → `AgentThought` | Reason（次の 1 手を LLM が決める） | Reasoning step |
| `grace/schemas.py::Scratchpad` / `ScratchpadEntry` | 行動と観測の履歴をプロンプトへ戻す | Scratchpad |
| `config.executor.react_max_iterations` | ループ上限 | Iteration budget |

ループ 1 周は `Reason → Act → Observe → Confidence → Controller`。

> **同じ `Executor` が L1 と L3 を兼ねる。** `_decide_next_action` は LLM が使えないとき
> 初期 Plan のステップ列を順に辿るフォールバックへ degrade する。`execute_plan_generator`
> （静的パス）と `execute_react_generator`（動的パス）は内部処理を共有しており、
> Ollama が動いていない CI 環境でも同じコードが通る。

> ⚠️ **tool calling 非対応のモデルでは ReAct が動かない。** `phi3` / `gemma2` が該当する
> （`config.OllamaConfig.MODEL_CONSTRAINTS` / `supports_tool_calls()`）。
> `GET /api/models` の選択肢はこれで絞り込み済み。
>
> 📌 `OllamaClient.generate_with_tools()` は Anthropic 版と同じ `ToolUseResponse` を返す
> （`finish_reason == "tool_calls"` → `stop_reason == "tool_use"` へ正規化済み）。

---

## 6. L2 — 役割分離（本リポジトリには弱い形のみ）

**独立した文脈を持つサブエージェントを起動する機構は無い。**

| 一般用語の L2 | 本リポジトリの実態 |
|---|---|
| Sub-agent spawning | なし |
| Multi-agent debate | なし |
| Role separation | `Planner` / `Executor` / `ConfidenceCalculator` / `ReplanManager` / `InterventionHandler` — **クラス分離であって文脈分離ではない**。同一プロセス・同一 config |
| Router によるエージェント選択 | Support と Review の 2 エージェント。ただし **API ルート（`/api/query` と `/api/review/submit`）が選ぶ静的ディスパッチ**であり、ルータ LLM は介在しない |
| Parallel fan-out | `executor._prefetch_parallel_searches` — エージェント並列ではなく**検索の先読み並列** |

これは欠落ではなく設計判断である。L2 を持たないことでパイプラインが決定的になり、
`backend/tests` でテストできる。代償として、共用部品
（`GroundednessVerifier` / `InterventionBridge` / `support_actions.py::ActionBackend`）は
L2 で隔離されず L3 で共有されるため、**Support の変更が Review を壊しうる**。

> 📌 `agent_parallel_search.py::ParallelSearchEngine` は L2 ではない。
> **Legacy ReAct 経路専用**で、Web 経路では未稼働である（CLAUDE.md §1・§9.4）。

---

## 7. L3 — オーケストレーション

固定順序の状態機械。順序を決めるのは LLM ではなくアプリのコード。
**本リポジトリの主戦場。**

### 7.1 中核の 2 エージェント

| 実装 | 関数 |
|---|---|
| `backend/app/core/support_agent.py` | `run_support_agent_core` |
| `backend/app/core/review_agent.py` | `run_review_agent_core` |

両者ともステップごとにイベントを emit しながら進む。ステップの一覧と実行順は
[`pipelines.md`](pipelines.md) を参照（本書では再掲しない）。

### 7.2 役割別コンポーネント

| 一般用語 | 実装 | 補足 |
|---|---|---|
| Planner | `grace/planner.py::Planner.create_plan` | LLM / ルールベース / clarification / fallback の系統を分岐 |
| Executor | `grace/executor.py::Executor` ＋ `ExecutionState` | 依存解決・タイムアウト・フォールバック |
| Critic / Verifier | `grace/confidence.py::GroundednessVerifier` | `support_rate = supported / (supported + contradicted)`。neutral は分母から除外 |
| Score aggregation | `ConfidenceCalculator` / `ConfidenceAggregator` / `SourceAgreementCalculator` | 重み付き合算＋ペナルティ |
| Calibration | `grace/calibration.py::Calibrator` | 温度スケーリング |
| Guardrail | `backend/app/core/gates.py` / `review_gates.py` の**非 LLM 部分** | 機構一覧は [`guardrails.md`](guardrails.md) が正本 |
| Replanning | `grace/replan.py::ReplanManager` / `ReplanOrchestrator` | `ReplanTrigger`（なぜ）× `ReplanStrategy`（どう）の 2 軸 |
| HITL | `grace/intervention.py::InterventionHandler` ＋ `InterventionRequest` / `InterventionResponse` | `InterventionLevel`（`confidence.py`）の 4 レベル |
| Adaptive thresholds | `grace/intervention.py::DynamicThresholdAdjuster` ＋ `FeedbackRecord` | フィードバックから閾値を調整 |
| Confirmation flow | 〃 `ConfirmationFlow` | ⑥ の承認フロー |
| Episodic memory | `grace/memory.py::ExecutionMemory` ＋ `MemoryRecord` / `CollectionStat` | 過去実行からコレクションの事前分布を学習。JSONL 永続。**除外リストを尊重する**（2026-09-20 に是正） |
| Policy injection | `backend/app/core/verticals.py::VerticalProfile` | 業界ごとの許可コレクション・閾値・プロンプト追記 |
| Ruleset | `backend/app/core/rulesets.py::RuleSet` / `RuleItem` | 常時チェックとキーワードの二層 |
| Action / Effector | `support_actions.py::ActionBackend` | **Support / Review 共用** |
| Identity verification | `support_actions.py::IdentityVerifier` | ⑥ の前段 |

> LangGraph の `StateGraph` に相当するものを、本リポジトリは**素の Python の逐次実行**で
> 書いている。動的なグラフ変更ができない代わりにテストしやすく、その制約を補うのが
> `ReplanManager` である。

---

## 8. L4 — 実行基盤と UI

LLM は登場しない。L3 を走らせ、人に見せる層。

**UI は `backend/` ＋ `frontend/` の 2 つだけ**である（Vite + React 18 + TypeScript、
dev: `:5173` / FastAPI dev: `:8000`）。

| 実装 | 役割 | 一般用語 |
|---|---|---|
| `frontend/` | 4 タブ（基本版 / GRACE-Support / GRACE-Review / データ管理） | SPA |
| `backend/app/main.py` ＋ `api/*.py` | HTTP 境界 | API layer |
| `api/support.py` / `api/review.py` | `POST` で 202 Accepted → `GET /stream/{job_id}` で SSE → `POST /confirm/{job_id}` | Async job + streaming + callback |
| `api/data.py` / `api/qdrant.py` / `api/meta.py` | データ準備ジョブ / コレクション管理 / モデル・業界・ルールセットの一覧 | — |
| `backend/app/core/jobs.py::JobManager` / `Job` | `emit` / `stream_events` / `finish`、`_gc_finished_locked` で古いジョブを破棄 | Job queue / Worker pool |
| `backend/app/core/intervention_bridge.py::InterventionBridge` ＋ `PendingIntervention` | L3 の同期的な `resolver(request)` を HTTP の往復へ橋渡し | HITL bridge |
| `backend/app/core/job_logs.py::JobLogHandler` / `capture_logs` | `logging.Handler` を継承しログを SSE イベント化 | Log forwarding |
| `backend/app/core/data_jobs.py` | チャンク化 / Q&A 生成 / Qdrant 登録 / 削除の 4 ランナー | ETL jobs |
| `services/data_pipeline_service.py` | CLI 実装への薄いラッパ（async→sync・パス検証・Ollama 事前確認） | Adapter layer |
| `celery_config.py` / `celery_tasks.py` | Q/A 生成をチャンク単位で fan-out | Distributed task queue |
| `qdrant_client_wrapper.py` ＋ `docker-compose/` | ベクトル DB | Vector store |

> **`InterventionBridge` が L3 と L4 の境界そのもの。** `grace/intervention.py` は
> 「`resolver` を呼べば人間の答えが返る」同期関数として書かれており、実際には
> SSE → ユーザ操作 → `POST /confirm` の往復で実現される。この分離により、
> L3 のコードは HTTP から独立したままとなり、テストから同じコアを直接呼べる。

### 8.1 Streamlit は存在しない

本リポジトリに Streamlit は**コード・依存ともに 1 件も無い**（2026-09-20 に依存を除去）。
`.claude/skills/grace-agent-docs/a_pages_md_format.md` に Streamlit の記述があるが、
これは**他リポジトリ用に同梱されたスキル資材**であり本リポジトリの実装ではない
（CLAUDE.md §9.2）。UI を論じるときの参照先は常に `backend/` と `frontend/` である。

---

## 9. 階層に属さないもの

**`grace/step_trace/benchmark.py` はコード確認用のプログラムであり、
プロジェクトの仕組みを構成しない。** クエリセットを流して KPI（所要時間・
ツール呼び出し数・信頼度・経路一致）を記録する計測ツールで、
L0〜L4 のいずれにも配置しない。

> 📝 同パッケージにあった `s0_arg.py`〜`s9_render.py`（S0〜S9 のステップ別トレース）と
> CLI（`agent_support_example.py`）は **2026-09-20 に削除した**（CLAUDE.md §1・§9.4）。

同様に `backend/tests/` も仕組みの構成要素ではなく検証手段である。

---

## 10. ローカル LLM であることが効く箇所

姉妹リポジトリ `grace_v2`（Anthropic 版）と**構造は同じだが前提が違う**箇所を、
層ごとにまとめる。移植のときはここを読んでから判断すること（CLAUDE.md §5）。

| 層 | 論点 | 本リポジトリ |
|---|---|---|
| L0 | API キー | **不要**。`ollama serve` が動いていることが前提 |
| L0 | 出力上限 | **`max_tokens` のみ**（`OllamaClient` が自動変換） |
| L0 | 構造化出力 | `response_format={"type":"json_schema"}` を使う。`json_object` は**スキーマをオウム返しされる**ことがあり `SchemaEchoError` が検知 |
| L0 | 拡張思考（thinking） | **存在しない**。`heavy_thinking_budget_tokens` は設定互換のため残るが無視される |
| L0 | Embedding | **Gemini のまま**（3072 次元）。`nomic-embed-text`（768 次元）へ変えると全コレクション再作成が要る |
| L0.5 | モデル階層 | `model` と `light_model` が**既定で同じ**。sonnet / haiku のような階層は無い |
| L1 | tool calling | **モデルによっては非対応**（`phi3` / `gemma2`）。`supports_tool_calls()` で判定 |
| L3 | コスト計算 | **ローカル実行は 0**。トークン課金を前提にしたコードは持たない |
| L4 | 事前確認 | `data_pipeline_service.ollama_unreachable_message()` / `model_not_pulled_message()` が**ジョブ開始前**に弾く |
| 全域 | レイテンシ | 1 周が長い。内訳と予算は [`local_llm_timeout_budget.md`](local_llm_timeout_budget.md) が正本 |

---

## 11. 逆引き表（一般用語 → 実装）

| 一般用語 | 実装 | 層 |
|---|---|:--:|
| Provider abstraction | `helper/helper_llm.py::create_llm_client`（既定 `"ollama"`） | L0 |
| Function calling / Tool use | `helper/helper_llm.py::OllamaClient.generate_with_tools` | L0 |
| Structured Output | 〃 `generate_structured` ＋ `_resolve_schema_refs` | L0 |
| Output repair | `grace/llm_compat.py::_strip_think` / `_strip_to_json` / `parse_score` | L0 |
| Embedding model | `helper/helper_embedding.py::GeminiEmbedding` | L0 |
| LLM-as-a-Judge | `core/gates.py::create_*` / `core/review_gates.py::create_violation_detector` | L0.5 |
| Faithfulness / Groundedness | `grace/confidence.py::GroundednessVerifier` | L0.5 |
| Answer relevance | 〃 `QueryCoverageCalculator` | L0.5 |
| Self-consistency | 〃 `SourceAgreementCalculator` | L0.5 |
| Self-evaluation | 〃 `LLMSelfEvaluator` | L0.5 |
| ReAct | `grace/executor.py::execute_react_generator` | L1 |
| Scratchpad | `grace/schemas.py::Scratchpad` | L1 |
| Tool registry | `grace/tools.py::ToolRegistry` | L1 |
| Planner-Executor | `grace/planner.py` ＋ `grace/executor.py` | L3 |
| Confidence calibration | `grace/calibration.py::Calibrator` | L3 |
| Guardrail | `core/gates.py` / `core/review_gates.py` の非 LLM 部分 | L3 |
| Replanning | `grace/replan.py::ReplanManager` | L3 |
| HITL | `grace/intervention.py::InterventionHandler` | L3 |
| Adaptive thresholds | 〃 `DynamicThresholdAdjuster` | L3 |
| Episodic memory | `grace/memory.py::ExecutionMemory` | L3 |
| System prompt injection | `core/verticals.py::VerticalProfile` | L3 |
| Streaming | `api/support.py` ＋ `core/jobs.py::Job.emit` / `stream_events` | L4 |
| Job orchestration | `core/jobs.py::JobManager` | L4 |
| HITL bridge | `core/intervention_bridge.py::InterventionBridge` | L4 |
| Log forwarding | `core/job_logs.py::JobLogHandler` | L4 |
| Adapter layer | `services/data_pipeline_service.py` | L4 |
| Distributed fan-out | `celery_tasks.py` | L4 |

---

## 12. 関連ドキュメント

| 文書 | 内容 |
|---|---|
| [`pipelines.md`](pipelines.md) | 3 モードのステップ対照表・実行順（**本書はステップ表を持たない**） |
| [`guardrails.md`](guardrails.md) | ガードレール GA〜G9 の機構・実装・失敗時の既定（**本書は機構表を持たない**） |
| [`reasoning_flow.md`](reasoning_flow.md) | 生成の 2 ステップ（reasoning / detect）のプロンプト構造 |
| [`api_flow.md`](api_flow.md) | API の段階別一覧（0 〜 ⑥） |
| [`local_llm_timeout_budget.md`](local_llm_timeout_budget.md) | **タイムアウト予算と遅さの内訳**（本書 §10 の詳細） |
| [`performance_levers.md`](performance_levers.md) | 品質・レイテンシ・コストを決める箇所 |
| [`../backend/docs/README.md`](../backend/docs/README.md) | `backend/app/**` の IPO 索引 |
| [`../grace/docs/README.md`](../grace/docs/README.md) | `grace/**` の IPO 索引 |
| [`../frontend/docs/README.md`](../frontend/docs/README.md) | React コンポーネント索引 |

---

## 13. 変更履歴

| バージョン | 変更内容 |
|-----------|---------|
| 1.0 | 初版作成（2026-09-20）。一般的なエージェント用語と実装の対応表が存在せず、実装を読む前の見取り図が無かったため作成。ステップ表・ガードレール表は `pipelines.md` / `guardrails.md` が正本のため本書では持たずリンクとした（`README.md` §4）。**§10 に「ローカル LLM であることが効く箇所」を置き、姉妹リポジトリ（Anthropic 版）との前提の違いを層ごとに整理した**（移植時の誤コピー防止・CLAUDE.md §5） |

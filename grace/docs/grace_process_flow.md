# grace_process_flow.md - grace/ 処理フロー

**Version 3.4** | 最終更新: 2026-10-30

GRACE を 1 クエリ動かしたときの**処理の流れ**（どの順で・どのモジュールが・どんな条件で分岐するか）をまとめる。
各関数の引数・戻り値は各モジュールの文書（IPO）に、外部 API に渡るデータとプロンプトの所在は
[`grace_data_flow.md`](./grace_data_flow.md) に置き、ここには書かない。ディレクトリ全体の入口は [`README_grace.md`](./README_grace.md)。

---

## 目次

- [概要](#概要)
- [1. 全体フロー](#1-全体フロー)
- [2. ステップ詳細](#2-ステップ詳細)
  - [2.1 1 クエリの処理](#21-1-クエリの処理)
  - [2.2 全体信頼度の算出](#22-全体信頼度の算出)
  - [2.3 GRACE-Support / GRACE-Review での使われ方](#23-grace-support--grace-review-での使われ方)
- [3. 分岐・例外・中断](#3-分岐例外中断)
- [4. シーケンス](#4-シーケンス)
- [5. 関連ドキュメント](#5-関連ドキュメント)
- [6. 変更履歴](#6-変更履歴)

---

## 概要

入口は計画の作成（① Plan。GRACE-Support は 0-(A) で整理した話題から `Planner.create_plan_for_topic(topic)`、リプランは `Planner.create_plan(query, context_hints=...)`）と `Executor.execute(plan)`（② 〜 ⑤）の 2 つ。
`execute()` の内側で、ステップの実行・動的フォールバック・ステップ信頼度・介入・リプラン・全体信頼度・較正・実行メモリへの記録が回る。

### 主な責務

- 質問（0-(A) で整理した話題）から実行計画を作る
- 計画の複雑度で、静的な Plan-Execute と ReAct ループを振り分ける
- 計画のステップを順に実行し、RAG の結果に応じて Web 検索・ask_user を動的に挿入する
- ステップごとに信頼度を測り、介入レベルに応じて一時停止・通知する
- 失敗・低信頼のステップを契機に計画を作り直す
- 最終回答の根拠を検証して全体信頼度を出し、較正する
- 実行の実績を記録して、次回の計画に還元する

### 各責務対応のモジュール

| # | 責務 | 対応モジュール | 説明 |
|---|------|--------------|------|
| 1 | 計画の生成 | `grace/planner.py` | `Planner.create_plan_for_topic()`（観点ごとに `rag_search`）/ `create_plan()`（リプラン）。`memory.py` の優先コレクションを反映する |
| 2 | 静的パス／ReAct の振り分け | `grace/executor.py` | `Executor._dispatch_generator()`（`execute_plan()` が使う） |
| 3 | ステップの実行と動的フォールバック | `grace/executor.py` / `grace/tools.py` | `execute_plan_generator()` → `_execute_step()` → `ToolRegistry.get(...).execute()` |
| 4 | ステップ信頼度と介入 | `grace/confidence.py` / `grace/intervention.py` | `ConfidenceCalculator.calculate()`（`judges.step_confidence_llm=true` なら `llm_calculate()`）→ `decide_action()` → `InterventionHandler.handle()` |
| 5 | リプラン | `grace/replan.py` | `ReplanOrchestrator.handle_step_failure()` → `ReplanManager`（要否・戦略・新計画） |
| 6 | 全体信頼度と較正 | `grace/executor.py` / `grace/confidence.py` / `grace/calibration.py` | `_calculate_overall_confidence()` → `_blend_groundedness_confidence()` → `Calibrator.transform()` |
| 7 | 実績の記録 | `grace/memory.py` | `Executor._record_memory()` → `ExecutionMemory.record_many()` |

### アーキテクチャ構成図

```mermaid
flowchart TB
    subgraph CALLER["呼び出し側"]
        SUP["backend/app/core/support_agent.py<br>run_support_agent_core"]
    end
    subgraph GRACE["grace/（本書が扱う流れ）"]
        PLN["planner.py<br>① Plan"]
        EXE["executor.py<br>② Execute（統括）"]
        TLS["tools.py"]
        CNF["confidence.py<br>③ Confidence"]
        CAL["calibration.py"]
        INT["intervention.py<br>④ Intervention"]
        RPL["replan.py<br>⑤ Replan"]
        MEM["memory.py"]
    end
    subgraph EXTERNAL["外部"]
        LLM["ローカル LLM（Ollama）"]
        QD["Qdrant / Gemini Embedding"]
        WEB["Web 検索"]
        FS["logs/grace_memory.jsonl<br>config/calibration.json"]
    end
    SUP --> PLN
    SUP --> EXE
    MEM --> PLN
    EXE --> TLS
    EXE --> CNF
    EXE --> CAL
    EXE --> INT
    EXE --> RPL
    EXE --> MEM
    RPL --> PLN
    TLS --> QD
    TLS --> WEB
    TLS --> LLM
    CNF --> LLM
    PLN --> LLM
    MEM --> FS
    CAL --> FS
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class SUP,PLN,EXE,TLS,CNF,CAL,INT,RPL,MEM,LLM,QD,WEB,FS default
style CALLER fill:#1a1a1a,stroke:#fff,color:#fff
style GRACE fill:#1a1a1a,stroke:#fff,color:#fff
style EXTERNAL fill:#1a1a1a,stroke:#fff,color:#fff
```

**データフロー**:

1. 呼び出し側が質問を渡し、`planner` が `ExecutionPlan`（通常は `rag_search → reasoning` の 2 ステップ）を返す
2. `executor` がステップを順に実行し、`StepResult` とステップの `ConfidenceScore` を積み上げる。必要なら Web 検索・ask_user を差し込み、計画を作り直す
3. 最後に最終回答の根拠を検証して全体信頼度を出し、較正したうえで `ExecutionResult` を返す。使った RAG コレクションと成否は実行メモリへ書く

---

## 1. 全体フロー

```mermaid
flowchart TB
    Q(["質問"])
    PLAN["① Planner.create_plan_for_topic<br>観点ごとに rag_search（最新情報を求めた観点は web_search）→ reasoning<br>リプランは create_plan（LLM 計画）"]
    DISP{"allow_react かつ react_enabled<br>かつ complexity ≥ 0.7"}
    REACT["ReAct ループ<br>execute_react_generator"]
    STATIC["静的 Plan-Execute<br>execute_plan_generator"]
    STEP["ステップを実行<br>_execute_step → ツール"]
    DYN{"rag_search の結果は<br>十分か・質問に合うか"}
    WEBS["Web 検索を動的に挿入<br>（失敗なら ask_user）"]
    CONF["ステップ信頼度<br>→ decide_action"]
    PAUSE{"一時停止が必要か"}
    STOP(["一時停止して返す"])
    REP{"リプランが必要か"}
    NEWPLAN["⑤ ReplanOrchestrator<br>新しい計画で続きを実行"]
    NEXT{"次のステップがあるか"}
    OVERALL["全体信頼度<br>根拠検証ブレンド → 較正"]
    MEMW["実行メモリへ記録"]
    RES(["ExecutionResult"])
    Q --> PLAN --> DISP
    DISP -- "はい" --> REACT
    DISP -- "いいえ" --> STATIC
    STATIC --> STEP --> DYN
    DYN -- "不十分" --> WEBS --> CONF
    DYN -- "十分・rag 以外" --> CONF
    CONF --> PAUSE
    PAUSE -- "ESCALATE など" --> STOP
    PAUSE -- "いいえ" --> REP
    REP -- "はい" --> NEWPLAN --> STEP
    REP -- "いいえ" --> NEXT
    NEXT -- "ある" --> STEP
    NEXT -- "ない" --> OVERALL
    REACT --> OVERALL
    OVERALL --> MEMW --> RES
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class Q,PLAN,DISP,REACT,STATIC,STEP,DYN,WEBS,CONF,PAUSE,STOP,REP,NEWPLAN,NEXT,OVERALL,MEMW,RES default
```

> 📝 ReAct ループも内側で同じツール・同じ `decide_action`・同じ全体信頼度の算出を使う（§3.1）。
> 図を簡単にするため、ReAct の反復は 1 ブロックにまとめた。

---

## 2. ステップ詳細

### 2.1 1 クエリの処理

静的パス（`execute_plan_generator`）の 1 周。ブロッキング版 `execute_plan()` も、この同じジェネレータを最後まで回している。

| # | ステップ | 実装（ファイル::シンボル） | 入力 | 出力 | 失敗時の既定 |
|:--:|---|---|---|---|---|
| 1 | 計画を作る | `planner.py::Planner.create_plan_for_topic`（GRACE-Support）/ `create_plan`（リプラン） | 0-(A) の話題 / 質問 | `ExecutionPlan` | LLM 計画が失敗したら、単純な 2 ステップ（`rag_search → reasoning`）のフォールバック計画にし、**その `Planner` では以降 LLM 計画を試さない**（`_llm_plan_disabled`） |
| 2 | 振り分け | `executor.py::Executor._dispatch_generator` | `plan.complexity`・`allow_react` | 静的パス or ReAct | — |
| 3 | 実行するステップを選ぶ | `execute_plan_generator` | `ExecutionState.step_statuses` | 未成功のステップ列 | 成功済みは飛ばす（再開時） |
| 4 | 実行の前提を確かめる | `_web_search_allowed` / `_check_dependencies` | ステップ | 実行する／`SKIPPED` | Web 検索が無効・依存未達なら `SKIPPED` |
| 5 | 後続の検索を先に走らせる | `_prefetch_parallel_searches` | 依存の無い後続の検索ステップ | プリフェッチ結果 | `executor.parallel_search`（既定 true）のときだけ。最大 `executor.max_parallel_steps`（既定 4）件 |
| 6 | ツールを実行する | `_execute_step` → `_run_tool_with_timeout` → `ToolRegistry.get(action).execute(**kwargs)` | ステップ・前ステップの出力（`reasoning` には関連度で絞って重複を除いた参照情報） | `ToolResult` | 例外なら `fallback` のツールを試し、だめなら `failed`。期限は `PlanStep.timeout_seconds`、未指定なら `planner.step_timeout_seconds`（240 秒）。期限切れのツールはデーモンスレッドで走り続けるが、プロセスの終了は止めない（`_start_with_deadline`） |
| 7 | ステップ信頼度を測る | `_llm_calculate_step_confidence` → `ConfidenceCalculator.calculate`（既定） | `ConfidenceFactors` | `ConfidenceScore`（`step_confidence_scores` に保存） | 既定（`judges.step_confidence_llm=false`）はヒューリスティックだけで LLM を呼ばない。true なら `llm_calculate` で採点し、失敗したらヒューリスティック、検索ステップで LLM の値が 0.6 未満なら高い方を採る |
| 8 | RAG の結果で分岐する | `execute_plan_generator`（`rag_search` の直後） | 最大スコア・関連性 | Web 検索を挿入（検索語は `web_query`・理由を `web_search_log` に記録）／計画済みの Web 検索を飛ばす（`required` は飛ばさない） | §3.2 |
| 9 | 介入を判定する | `ConfidenceCalculator.decide_action` → `_should_pause_for_intervention` / `_handle_intervention_if_needed` | ステップの `ConfidenceScore` | 続行／一時停止 | §3.3 |
| 10 | ask_user に答えを反映する | `_handle_ask_user_response` | `ask_user` の結果 | 後続ステップへ渡る応答 | コールバックが無ければ何もしない |
| 11 | リプランを判定する | `_should_trigger_replan` → `ReplanOrchestrator.handle_step_failure` | `StepResult` | 新しい計画（あれば続きを実行） | §3.4 |
| 12 | 全体信頼度を出す | `_calculate_overall_confidence` | 全ステップ | `overall_confidence`（較正済み） | §2.2 |
| 13 | 実績を記録する | `_record_memory` → `ExecutionMemory.record_many` | 使ったコレクション・成否・信頼度 | JSONL へ追記 | 失敗しても実行は止めない（警告のみ） |
| 14 | 結果をまとめる | `_create_execution_result` | `ExecutionState` | `ExecutionResult` | 例外なら `overall_status="failed"`・`final_answer="実行エラー: ..."` |

`overall_status` は、キャンセルなら `cancelled`、全ステップ成功なら `success`、1 つでも成功なら `partial`、それ以外は `failed`。
`final_answer` は**最後に成功した `reasoning` ステップの出力**（`_final_answer_of`）。

> ⚠️ **ESCALATE で止まっても `overall_status` は `success` になりうる。** 実行済みのステップだけで決まるので、
> 検索の後で止まると「全ステップ成功・回答なし」になる。回答が出たかは `final_answer is not None` で見る。

### 2.2 全体信頼度の算出

`Executor._calculate_overall_confidence(state)` が統括する。旧 `confidence_calibration.md` の内容を実装と突き合わせて移した。

| 順 | 処理 | 実装 | 内容 |
|:--:|---|---|---|
| 0 | 較正器を読む（起動時に 1 回） | `Calibrator.load(config.confidence.calibration_path)` | ファイルが無ければ恒等（T=1.0） |
| 1 | ステップ信頼度を集める | `step_confidence_scores` | 各ステップの `ConfidenceScore` |
| 2 | 最終回答を特定する | `_final_answer_of` | 最後に成功した `reasoning` の出力 |
| 3 | ask_user 計画の扱い | — | 最終回答が無く `ask_user` ステップがあれば、`confidence.clarification_confidence`（0.3）に固定して終わる |
| 4 | 自己評価と網羅度 | `LLMSelfEvaluator.evaluate_final` | 1 回の LLM 呼び出しで `self_eval_score` / `coverage_score`（ここで順番に呼ぶ。grace_v2 にある先行実行は本リポジトリには無い） |
| 5 | 補助スコアを集約する | `ConfidenceAggregator.aggregate(method="weighted")` | ステップ信頼度＋4 の 2 つ |
| 6 | 根拠検証でブレンドする | `_blend_groundedness_confidence` → `GroundednessVerifier.verify` / `damp_support_rate` | 下の式 |
| 7 | 較正する | `Calibrator.transform` | 温度スケーリング。0〜1 に丸め、小数 3 桁 |

**6 のブレンド**（`config.confidence` の既定値）:

```
support_rate = supported / (supported + contradicted)     # neutral は分母から外す
decided      = supported + contradicted

検証できた（verified かつ decided > 0）:
    rate        = damp_support_rate(gres)                  # 判定できた割合で割り引く
    answer_conf = 重み付き平均( rate×0.6, self_eval×0.25, coverage×0.15 )   # 無い項目は外して正規化
    矛盾があれば answer_conf = min(answer_conf, 0.3)
    final       = 0.8×answer_conf + 0.2×aggregated

検証できない（ソース無し・LLM 失敗・decided = 0）:
    answer_conf = 重み付き平均( self_eval×0.5, coverage×0.3, aggregated×0.2 )
    ソースが 1 つも無ければ answer_conf ×= 0.85
    final       = 0.8×answer_conf + 0.2×aggregated
```

> ⚠️ **support_rate=0 を罰点に使わない。** 判定できた主張が 0 件のときは「裏付け 0」ではなく「判定不能」とみなし、
> 従来のブレンドへ落とす。そうしないと全クエリが CONFIRM / ESCALATE に落ちる。
> `confidence.groundedness_enabled=false` か最終回答が無いときは、ブレンドせず 5 の値をそのまま使う。

> 📝 **全体信頼度には `decide_action` を掛けない。** 介入レベルの判定はステップごと（§2.1 の 9）だけで、
> 全体信頼度を回答に使うかの判断は呼び出し側（GRACE-Support の ④ 回答ゲート `backend/app/core/gates.py`）が行う。

### 2.3 GRACE-Support / GRACE-Review での使われ方

両エージェントのステップごとに、grace のどのモジュール（シンボル）が効くか。ステップの実行順の図は
[`core_process_flow.md` §4.1](../../backend/app/core/docs/core_process_flow.md)、ステップ内部の設計は
[`core_process_flow.md` §1](../../backend/app/core/docs/core_process_flow.md) / [`core_process_flow.md` §2](../../backend/app/core/docs/core_process_flow.md) が正本。

**GRACE-Support（基本版も同じ）** — `support_agent.py::STEP_IDS` の順。「—」は grace を使わず `backend/app/core/` の純関数だけで判定するステップ。

| 実行順 | ステップ | grace モジュール（シンボル） | 備考 |
|:--:|---|---|---|
| 1 | 0-(A) `analyze` 質問の整理 | `llm_compat.py`（`create_chat_client`）／`intervention.py`（`InterventionRequest`） | `backend/app/core/question_refiner.py::refine_question` が毎回ローカル LLM で話題 → 観点に整理する。範囲内の話題が複数なら、選択の `InterventionRequest` を Web の承認待ち（`backend/app/core/intervention_bridge.py`）へ渡す |
| 2 | 0-(B) `profile` 業界プロファイル適用 | `config.py`（`GraceConfig`） | `qdrant.allowed_collections` / `llm.prompt_addendum` へ注入。**基本版はスキップ** |
| 3 | ① `plan` | `planner.py`（`create_plan_for_topic`）← `memory.py`（`best_collection`） | 観点ごとに rag_search。実行メモリのコレクション事前分布を計画に反映 |
| 4 | ② `execute` | `executor.py`（`execute(plan, allow_react=False)`）→ `tools.py`（`rag_search` / `reasoning`）・`replan.py`・`confidence.py`・`calibration.py`・`memory.py` | 全体信頼度はここで決まる（§2.2）。**本リポジトリ固有**: reasoning が空応答なら参照情報を絞って 1 回だけ再試行（`ReasoningTool._minimal_sources`）、reasoning の失敗によるリプランでは検索を積み増さない（`ReplanManager._drop_redundant_search_steps`） |
| 5 | ③ `confidence` | `confidence.py`（`GroundednessVerifier.verify`） | executor の検証器を使い回す（同じ入力は再検証しない） |
| 6 | ④ `gate` 回答ゲート＋強制エスカレ＋救済 | — | `gates.py::_answer_gate` ほか（純関数） |
| 7 | ⑤ `web` Web フォールバック | `tools.py`（`web_search` / `reasoning`）・`confidence.py`（`GroundednessVerifier` / `SourceAgreementCalculator`） | 内部回答と Web 回答の一致度を相互検証 |
| 8 | ④' `no_info` 情報なし回答検知 | `llm_compat.py` | `gates.py::create_no_info_judge` の LLM 判定 |
| 9 | ⑥ `action` | `intervention.py`（`InterventionHandler.handle`） | 本人確認（`ec` のみ）→ CONFIRM → `support_actions.py` が実行 |

**GRACE-Review** — `review_agent.py::REVIEW_STEP_IDS` の順。番号は Support との対応を示す呼称なので、⑥ が ⑤ より先に来る。
② Retrieve 〜 ④' Suppress は**セグメントごとに並行して流れる**。

| 実行順 | ステップ | grace モジュール（シンボル） | 備考 |
|:--:|---|---|---|
| 1 | S1 `ruleset` ルールセット適用 | `config.py`（`GraceConfig`） | Support の 0-(B) と同じ手順 |
| 2 | ① `segment` 文書を検査単位へ分割 | — | 決定的な分割（原文オフセット保持） |
| 3 | ② `retrieve` 規程を RAG 検索 | `tools.py`（`rag_search` を**直接** `ToolRegistry.execute`） | `planner` / `executor` を通らない |
| 4 | ③ `detect` 二段判定 | `llm_compat.py` | 第 1 段はルールのキーワード、第 2 段は `review_gates.py::create_violation_detector` の LLM 判定 |
| 5 | ④ `ground` 指摘の根拠を検証 | `confidence.py`（`GroundednessVerifier.verify` / `damp_support_rate`） | Support の ③ と同じ検証器 |
| 6 | ④' `suppress` 誤検知抑止＋救済 | `llm_compat.py` | `review_gates.py::create_vacuous_judge` |
| 7 | ⑥ `web` 法改正の裏取り（既定 OFF） | `tools.py`（`web_search`） | 信頼度を下げる方向にだけ使う |
| 8 | ⑤ `severity` 重大度の確定＋強制 high | `llm_compat.py` | `review_gates.py::create_mention_classifier` |
| 9 | ⑦ `action` | `intervention.py`（`InterventionHandler.handle`） | high があれば `escalate_to_human`（承認不要）、なければ `create_ticket`（CONFIRM） |

**比較**:

| grace モジュール | 区分 | GRACE-Support | GRACE-Review |
|---|:--:|---|---|
| `planner.py` | A | ✅ ① Plan | — |
| `executor.py` | A | ✅ ② Execute（統括） | — |
| `tools.py` | A | ✅ ② の内側・⑤ Web | ✅ ② Retrieve・⑥ Web（直接呼ぶ） |
| `confidence.py` | A | ✅ ② 多軸信頼度・③ 根拠検証・⑤ 相互検証 | ✅ ④ 根拠検証（`damp_support_rate` も） |
| `calibration.py` | A | ✅ ② の内側（較正ファイルがあるとき） | — |
| `memory.py` | A | ✅ ① で読み・② で書く | — |
| `replan.py` | A | ✅ ② の内側（失敗・低信頼時） | — |
| `intervention.py` | A | ✅ 0-(A) 主質問の選択・⑥ CONFIRM | ✅ ⑦ CONFIRM |
| `config.py` | B | ✅ リクエスト単位のコピーへ注入 | ✅ 同左 |
| `llm_compat.py` | B | ✅ 判定系（質問分析・意図・情報なし・担当範囲）。思考タグは `_strip_think()` で剥がす | ✅ 判定系（違反検出・言及分類・実質性） |
| `schemas.py` | B | ✅ 計画・ステップ結果の型 | —（直接は使わない） |

> ⚠️ **共用部品を触るときは両方を壊さないこと。** `GroundednessVerifier` / `InterventionHandler` / `ToolRegistry` は
> 両エージェントの共用である（CLAUDE.md §1）。`tests/test_review_*.py` も通すこと。

---

## 3. 分岐・例外・中断

### 3.1 静的パスと ReAct ループ

`execute_plan()` は `_dispatch_generator()` で振り分ける。

| 条件 | 経路 | 中身 |
|---|---|---|
| `allow_react`（既定 true。GRACE-Support は false）かつ `executor.react_enabled`（既定 true）かつ `plan.complexity ≥ executor.react_complexity_threshold`（既定 0.7） | `execute_react_generator` | Reason（`_decide_next_action` が `AgentThought` を構造化出力で返す）→ Act（ツール実行）→ Observe（`Scratchpad` に追記）→ ステップ信頼度 → `decide_action`。`finish` か `is_final` かつ回答ありで終わる。最大 `executor.react_max_iterations`（既定 8）回 |
| それ以外 | `execute_plan_generator` | §2.1 の静的パス |

> ⚠️ `execute_plan_generator()` を**直接**呼ぶと、複雑度にかかわらず静的パスになる（振り分けは `execute_plan()` の中だけ）。
>
> 📝 **GRACE-Support は ReAct へ回さない**（`execute(plan, allow_react=False)`。2026-10-30〜）。0-(A) が観点ごとに検索文を決めた計画を、
> ReAct が検索語を決め直して無視しないため。`create_plan_for_topic` の複雑度は 0.5 固定、`create_plan` のルールベース計画も 0.5 で、
> 0.7 以上になるのは LLM 計画（リプラン・`planner.force_llm_plan`）だけ。

### 3.2 RAG の結果による動的フォールバック

`rag_search` が成功した直後に判定する（`qdrant.rag_sufficient_score`、既定 0.64）。

| パターン | 条件 | 動き |
|---|---|---|
| (1) 十分 | 最大スコア ≥ 0.64 **かつ** LLM が「質問に合う」と判定（`_evaluate_rag_relevance`） | 計画済みの後続 `web_search` を `SKIPPED` にする。ただし `required`（利用者が最新情報を求めた観点）は飛ばさない |
| (2) 不足 | 最大スコア < 0.64、または関連性の判定が「合わない」 | `web_search` を動的に挿入して実行（`_execute_dynamic_web_search`。検索語は `web_query`、理由は「社内検索のスコア不足（x < 0.64）」か「社内検索の結果が質問に合わないと LLM が判定」）。同じ検索語の `web_search` が計画のこの後にあれば挿入せず、そちらを走らせる |
| (3) Web も失敗 | (2) の Web 検索が失敗 | `ask_user` を動的に挿入して実行（`_execute_dynamic_ask_user`） |
| Web 検索が無効 | `use_web=False` 等で `tools.disabled` に `web_search` | 挿入しない。ask_user も挿入せず内部 RAG の結果のまま進む。計画済みの `web_search` も `SKIPPED` |

> 📝 動的に挿入したステップは実行メモリの成否判定から除外する（Web が落ちただけで RAG コレクションに「失敗」が刻まれないように）。
> 詳しくは [`grace_data_flow.md` §3.3](./grace_data_flow.md#33-実行メモリの蓄積と読み戻し)。

### 3.3 介入（④ Intervention）

ステップごとに `ConfidenceCalculator.decide_action(score)` が介入レベルを決める（`confidence.thresholds`）。

| 条件 | レベル | executor の動き |
|---|---|---|
| score ≥ 0.9 | `SILENT` | 何もせず続行 |
| score ≥ 0.7 | `NOTIFY` | `InterventionHandler.handle()` で通知して続行 |
| score ≥ 0.4 | `CONFIRM` | `intervention.interactive`（既定 true）かつ非ブロッキング実行なら**一時停止**。ブロッキング（`execute_plan`）では止まらず、`handle()` で確認を求めて続行（`cancel` が返れば中止） |
| それ未満 | `ESCALATE` | **一時停止**（ブロッキングでも止まる）。ただしブロッキングの**検索ステップ**では止まらない（1 観点の空振りで他の観点の社内の結果ごと回答を捨てないため。2026-10-30〜） |

一時停止すると、`state.is_paused = True` と `state.intervention_request` を設定した状態を 1 回 yield して**ジェネレータを終える**。
このとき返す `ExecutionResult` は、**最終回答があれば全体信頼度を計算してから返す**（`Executor._result_on_pause`。回答が無いときは 0.0＝回答未生成。2026-10-10 に grace_v2 から移植するまでは、回答があっても 0.0 のまま返していた）。
再開は `Executor.resume(state)` で `is_paused` を戻し、同じ `state` を渡して `execute_plan_generator(plan, state)` を作り直す
（成功済みのステップは飛ばされる）。中止は `Executor.cancel(state)`。

### 3.4 リプラン（⑤ Replan）

| 項目 | 内容 |
|---|---|
| 発火条件（`Executor._should_trigger_replan`） | ステップが `failed` なら常に。低信頼（`StepResult.confidence < replan.confidence_threshold`、既定 0.4）は**検索ステップ（`rag_search` / `web_search`）だけ**。`ExecutionState.can_replan()`（`replan.max_replans`、既定 3）を超えたら発火しない |
| 戦略（`ReplanManager.determine_strategy`） | 上限超過 → `ABORT`／失敗したステップに `fallback` がある → `FALLBACK`／タイムアウト → `FULL`／ユーザーの指摘 → 「最初から」を含めば `FULL`、それ以外 `PARTIAL`／失敗が計画の最初の 1/3 以内 → `FULL`／それ以外 → `PARTIAL` |
| 新しい計画 | `FULL` / `PARTIAL` は `Planner.create_plan(..., context_hints=...)` で作り直す（エラーの文脈はクエリへ連結せず `context_hints` で渡す）。`PARTIAL` で、失敗したのが検索ステップでなく完了済みに検索があるときは、作り直した側の検索ステップを落とす（`_drop_redundant_search_steps`。本リポジトリだけ）。作り直したステップには `planner.step_timeout_seconds` を引き継ぐ。`FALLBACK` は失敗したステップの `action` を `fallback` へ差し替える。`SKIP` は列挙にあり `create_new_plan` も扱うが、`determine_strategy` は選ばない |
| 実行 | 新しい計画を `state.plan` に差し替え、同じ `state` で `execute_plan_generator` を呼び直す（成功済みのステップは飛ぶ）。`replan_count` を 1 つ増やす |

### 3.5 例外

| 場所 | 起きること |
|---|---|
| ツールの例外（`_execute_step`） | ステップの `fallback` に別のツールがあれば試す（Web 検索が無効なら `web_search` には落ちない）。だめなら `status="failed"`・`confidence=0.0` |
| ステップの期限切れ | `TimeoutError` として上と同じ扱い。動的に挿入する `web_search` の期限は、Web 検索自身の予算（`timeout × max_retries` ＋ バックオフ ＋ 5 秒。既定 101 秒）から導く（`_web_search_budget_seconds`） |
| `reasoning` の空応答 | 参照情報を `_RETRY_SOURCE_LIMIT`（8）件へ絞って 1 回だけ再試行する（Web の結果を優先して残す）。それでも空なら失敗にする（空応答を成功にしない） |
| 実行全体の例外（`execute_plan_generator`） | `overall_status="failed"`・`overall_confidence=0.0`・`final_answer="実行エラー: ..."` の `ExecutionResult` を返す（例外を外へ投げない） |
| 実行メモリの書き込み失敗 | 警告ログだけで実行は止めない |
| 較正ファイルが無い・壊れている | 恒等変換（T=1.0）で続ける |

### 3.6 主な設定値

`grace.config.GraceConfig`（`config/grace_config.yml` → 環境変数 `GRACE_` → pydantic）。値は 2026-10-10 に `get_config()` で読んだ実効値。

| 設定キー | 値 | 効く場所 |
|---|---|---|
| `llm.provider` / `llm.model` / `llm.light_model` | `ollama` / `gemma4:26b-a4b-it-qat` / 同左 | 計画・推論・根拠検証／判定系（ローカルではモデルの載せ替えが遅いので同じモデル） |
| `llm.timeout` / `planner.step_timeout_seconds` | 180 秒 / 240 秒 | LLM 1 呼び出しの期限 ＜ ステップの期限（逆転させない） |
| `judges.enabled` / `judges.step_confidence_llm` | false / false | 補助の LLM 判定（§2.1 の 7） |
| `planner.force_llm_plan` | false | 真なら `create_plan` が常に LLM 計画（`create_plan_for_topic` には効かない） |
| `executor.react_enabled` / `react_complexity_threshold` / `react_max_iterations` | true / 0.7 / 8 | §3.1 |
| `executor.max_parallel_steps` | 4 | 後続の検索の並列プリフェッチ |
| `qdrant.rag_sufficient_score` / `executor.reasoning_min_rag_score` | 0.64 / 0.64 | §3.2（前者は後者以下にする）。後者は reasoning へ渡す RAG の結果の下限も兼ねる |
| `executor.reasoning_max_sources` | 20 | reasoning へ渡す参照情報の上限（重複除去の後） |
| `confidence.thresholds` | silent 0.9 / notify 0.7 / confirm 0.4 | §3.3 |
| `confidence.groundedness_weight` / `self_eval_weight` / `coverage_weight` / `search_aux_weight` | 0.6 / 0.25 / 0.15 / 0.2 | §2.2 |
| `confidence.clarification_confidence` | 0.3 | §2.2 の 3 |
| `confidence.calibration_path` | `config/calibration.json` | §2.2 の 0 |
| `intervention.interactive` / `auto_proceed_on_timeout` / `default_timeout` | true / false / 300 秒 | §3.3 |
| `replan.max_replans` / `confidence_threshold` / `partial_replan_threshold` | 3 / 0.4 / 0.6 | §3.4 |
| `memory.enabled` / `path` / `min_count` / `min_score` | true / `logs/grace_memory.jsonl` / 3 / 0.6 | 実行メモリ |

---

## 4. シーケンス

静的パスで、検索 → 推論の 2 ステップを実行する場合。

```mermaid
%%{ init: { "theme": "base", "themeVariables": {
  "background": "#000000", "mainBkg": "#000000",
  "textColor": "#ffffff", "lineColor": "#ffffff",
  "actorBkg": "#000000", "actorTextColor": "#ffffff",
  "actorLineColor": "#ffffff", "noteBkgColor": "#000000",
  "noteTextColor": "#ffffff", "noteBorderColor": "#ffffff" } } }%%
sequenceDiagram
    participant U as "呼び出し側"
    participant PL as "planner.py"
    participant ME as "memory.py"
    participant EX as "executor.py"
    participant TO as "tools.py"
    participant CO as "confidence.py"
    participant IN as "intervention.py"
    participant RE as "replan.py"
    participant CA as "calibration.py"

    U->>PL: create_plan_for_topic(topic)
    PL->>ME: best_collection(query)
    ME-->>PL: コレクション名 or None
    PL-->>U: ExecutionPlan
    U->>EX: execute(plan, allow_react=False)
    loop 各ステップ
        EX->>TO: get(action).execute(**kwargs)
        TO-->>EX: ToolResult
        EX->>CO: calculate(factors) → decide_action(score)
        CO-->>EX: ConfidenceScore / ActionDecision
        Note over EX,TO: rag_search の結果が不十分なら web_search（失敗なら ask_user）を挿入
        alt ESCALATE（対話モードの CONFIRM も）
            EX-->>U: 一時停止した状態を返す
        else NOTIFY / ブロッキングの CONFIRM
            EX->>IN: handle(decision, step, plan)
            IN-->>EX: 続行 / 中止
        end
        alt 失敗 or 検索ステップの低信頼
            EX->>RE: handle_step_failure(...)
            RE->>PL: 新しい計画を作る
            RE-->>EX: ReplanResult（new_plan）
        end
    end
    EX->>CO: evaluate_final / aggregate / GroundednessVerifier.verify
    CO-->>EX: 自己評価・網羅度・支持率
    EX->>CA: transform(final_conf)
    CA-->>EX: 較正済みの全体信頼度
    EX->>ME: record_many(query, collections, success, confidence)
    EX-->>U: ExecutionResult
```

---

## 5. 関連ドキュメント

| 文書 | 内容 |
|---|---|
| [`README_grace.md`](./README_grace.md) | grace/ の概要・設計の考え方・モジュール索引・使い方・公開 API |
| [`grace_data_flow.md`](./grace_data_flow.md) | 外部 API に渡るデータ・プロンプトの所在・実行メモリと較正ファイルの形式 |
| [`executor.md`](./executor.md) | `Executor` の IPO と使用例（処理パターン 4 通り） |
| [`confidence.md`](./confidence.md) / [`calibration.md`](./calibration.md) | 信頼度・根拠検証・較正の IPO |
| [`core_process_flow.md` §1](../../backend/app/core/docs/core_process_flow.md) | GRACE-Support の各ステップの設計（grace の外側の判定を含む） |
| [`core_process_flow.md` §2](../../backend/app/core/docs/core_process_flow.md) | GRACE-Review の各ステップの設計 |

---

## 6. 変更履歴

| バージョン | 日付 | 変更内容 |
|---|---|---|
| 1.0 | — | 初版作成（A グループ 8 モジュールの横断まとめ。先頭にモジュール・ブロック図、3 層構成図、モジュール構成図、処理シーケンス、横断設定表を整備） |
| 1.1 | — | 目次・本文の採番を整理（モジュール別サマリーのサブ番号 3.1–3.8 を本文番号と一致させ、目次を明示番号付き箇条書きに変更）。新章「4. 実行メモリが貯まるまで（planner → executor → memory）」を例データ・場合分け・黒背景シーケンス図つきで追加し、以降の章を 5〜9 に繰り下げ |
| 2.0 | 2026-08-29 | 実装との突き合わせによる全面訂正。(1) **行番号参照（`planner.py:232` 等 4 件）を全廃**し、ファイル名＋シンボル名で参照する形式へ（行番号はコミットのたびに嘘になる／4 件すべて実装とずれていた）。(2) プロバイダ表記を **Ollama（LLM）／Gemini（Embedding のみ）** へ是正（CLAUDE.md §3・§9.3）。(3) 構成図のクライアント層を実在の **React UI + FastAPI + `agent_support_example.py`** へ差し替え（`agent_rag.py` / Streamlit は本リポジトリに存在しない）。(4) `_record_memory` の成否判定を現行仕様（**動的挿入ステップを除外＋最終回答の有無**／2026-08-29 の修正）へ更新。(5) 設定表の実値ずれを訂正（`llm.provider` `anthropic`→`ollama`、`executor.max_parallel_steps` `3`→`4`）し、`llm.light_model` / `llm.heavy_model` を追記。(6) `memory.md` へのリンクと `record_many` を追加、`tools.py` に opt-in の `CodeExecuteTool` を追記 |
| 2.1 | 2026-10-04 | `rag_sufficient_score` の既定を 0.7 → 0.64 に追随（2026-10-04・grace_v2 から移植。`executor.md` v5.3）。設定表のセクション名を実体（`qdrant.`）に直した |
| 2.2 | 2026-10-06 | 2026-10-06: 現在の既定モデルの記載 `gemma4:12b-mlx` を、2026-10-03 の変更後の値 `gemma4:26b-a4b-it-qat`（`config.py::get_default_ollama_model()` の戻り値）へ是正（変更履歴の中の記述は当時の値として残す） |
| 2.3 | 2026-10-08 | 2026-10-08: Anthropic 予備経路（`AnthropicGenaiClient`）の削除に追随。技術スタック注記と設定表の `llm.provider` から「`"anthropic"` は後方互換」を外した |
| 2.4 | 2026-10-10 | `grace/step_trace/`（`benchmark.py` を含む）を 2026-10-10 にディレクトリごと削除したのに追随し、現状を述べる記述から外した（過去の経緯の記述は残す） |
| 3.0 | 2026-10-10 | **`grace_core.md` を `grace_process_flow.md` へ改称し、処理フローの文書に作り直した**（`a_cross_doc_md_format.md` §1.3）。(1) 概要・モジュール役割サマリー・エクスポート・使用例は `README_grace.md` へ、実行メモリの章（旧 §4）は `grace_data_flow.md` §3.3 へ移した。(2) 旧 `confidence_calibration.md` の処理順・ブレンドの式を §2.2 へ、旧 `README.md` の GRACE-Support / GRACE-Review の対応表を §2.3 へ移し、両文書を削除した。(3) 実装と突き合わせて次を明記・是正した: ステップ信頼度は既定でヒューリスティック（`judges.step_confidence_llm=false`）、全体信頼度には `decide_action` を掛けない（旧 `confidence_calibration.md` の手順 8 は誤り）、介入で止まるのは ESCALATE と対話モードの CONFIRM だけで、止まったときの `overall_confidence` は 0.0 のまま、リプランの低信頼の発火は検索ステップに限る、`determine_strategy` は `SKIP` を選ばない。(4) 分岐・例外・中断（§3。ステップの期限・reasoning の空応答の再試行・LLM 計画の無効化を含む）と静的パスの 14 段の表（§2.1）を新設した。設定表（§3.6）は `get_config()` の実効値で作り直した |
| 3.1 | 2026-10-10 | 一時停止（介入）時の結果を返す `Executor._result_on_pause` を grace_v2 から移植したのに追随（2026-10-10。回答があれば全体信頼度を計算してから返す。以前は回答済みでも 0.0 のまま返っていた）。§3.3 を書き換えた |
| 3.2 | 2026-10-12 | backend/docs/ の廃止（2026-10-12）に追随し、参照先を backend/app/docs/README_app.md・backend/app/api/docs/README_api.md・backend/app/core/docs/core_process_flow.md などへ付け替えた（テストの地図は docs/testing.md、実行例の txt は docs/examples/） |
| 3.3 | 2026-10-13 | 削除した docs/pipelines.md への参照を core_process_flow.md §4.1 へ付け替え |
| 3.4 | 2026-10-30 | ① を `create_plan_for_topic`（0-(A) の観点ごとの計画）、② を `allow_react=False`・Web 検索の理由の記録にしたのに追随。§1 の図・§2.1・§2.3 の対応表・§3.1・§3.2・§3.3・§3.6 の設定（削除した `llm_plan_complexity_threshold` を `force_llm_plan` に）・§4 のシーケンス図を直した（grace_v2 の grace_process_flow.md v4.3 と同じ） |

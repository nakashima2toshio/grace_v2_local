# GRACE-Support 処理フローと設計 ドキュメント

**Version 3.0** | 最終更新: 2026-09-16

> **本書の位置づけ**: GRACE-Support（問い合わせ → 回答）の**処理フロー（HOW）と
> 設計判断（WHY）を 1 本にまとめた正本**。v3.0 で `backend_flow.md` を改称し、
> 設計 3 文書（`agent_support_example.md` / `agent_support_example_flow.md` /
> `confidence_flow_grace_vs_backend.md`）を統合した。
>
> | 知りたいこと | 参照先 |
> |---|---|
> | 各段が何を入力し何を出すか | 本書 §4（処理ステップ IPO 詳細） |
> | なぜこの判定・しきい値・ポリシーなのか | 本書 §5（設計判断） |
> | 業界プロファイル（gov / saas / ec）の中身 | [`verticals_and_rulesets.md` §1](./verticals_and_rulesets.md) |
> | ジョブ・SSE・HITL の機構 | [`job_runtime.md`](./job_runtime.md) |
> | 使うモデルの決まり方 | [`config_and_providers.md`](./config_and_providers.md) |
> | 関数・クラスのシグネチャ | [`reference/core_support_agent.md`](./reference/core_support_agent.md) / [`reference/core_gates.md`](./reference/core_gates.md) |

> **関連ドキュメント**
> - [`architecture.md`](./architecture.md) — backend の層構造と外部境界
> - [`api_contract.md`](./api_contract.md) — エンドポイントと SSE の契約
> - [`review_flow.md`](./review_flow.md) — 対になる GRACE-Review

---

## 目次

- [概要](#概要)
- [1. アーキテクチャ構成図](#1-アーキテクチャ構成図)
- [2. モジュール構成図](#2-モジュール構成図)
- [3. ステップ ↔ 実装対応と信頼度フロー](#3-ステップ--実装対応と信頼度フロー)
- [4. 処理ステップ IPO詳細（0-(A)〜⑥）](#4-処理ステップ-ipo詳細0-a)
- [5. 設計判断](#5-設計判断)
- [6. 評価指標（KPI）](#6-評価指標kpi)
- [7. 設定・定数](#7-設定定数)
- [8. 使用例](#8-使用例)
- [9. エクスポート](#9-エクスポート)
- [10. 実装ロードマップ](#10-実装ロードマップ)
- [11. 変更履歴](#11-変更履歴)
- [付録A: 旧 CLI 仕様（削除済み・記録）](#付録a-旧-cli-仕様削除済み記録)
- [付録B: 1 コマンド実行トレース](#付録b-1-コマンド実行トレース)
- [付録C: 依存関係図](#付録c-依存関係図)

---

## 概要

本ドキュメントは、GRACE-Support パイプライン（`backend/app/core/support_agent.py` の
`run_support_agent_core()`）が実行する **処理フローの各ステップ (0)〜(8)** を、
実装関数・シグネチャ・IPO（Input-Process-Output）・戻り値例・使用例つきで記述する。
全体像（アーキテクチャ・データフロー）はリポジトリルートの [`README.md`](../../README.md) §1〜§2 を参照。

> 📝 **注意（実行順）**: 本ドキュメントの番号はルート `README.md` §2-1 の
> フロー図の並びに従う。パイプラインの**実際の実行順**は
> **(0) → (1) → (2) → (3) → (4) → (4-1) → (5) → (4-2) → (6) → (7) → (8)** であり、
> ④'（(4-2) 情報なし回答検知）は ⑤（(5) Web フォールバック）の**後**に、
> `decision == "answer"` の場合のみ実行される。

### 主な責務

- 業界プロファイル（gov / saas / ec）による検索スコープ・しきい値・エスカレ語・本人確認の切替
- クエリの実行計画への分解（Plan）と内部 RAG → reasoning による回答生成（Execute）
- 回答の主張ごとの裏付け検証（Groundedness）と支持率・出典数に基づく回答可否判定（回答ゲート）
- 誤エスカレ・誤回答の抑止（強制エスカレの二段判定・④-救済・④' 情報なし回答検知）
- 内部根拠不足時の Web フォールバック（回答再利用による重複推論の省略・内部×Web 相互検証）
- 副作用のあるアクションの安全な実行（本人確認 → HITL CONFIRM → バックエンド実行）

### 各責務対応のモジュール

| # | 責務 | 対応モジュール | 説明 |
|---|------|--------------|------|
| 1 | 業界プロファイルの切替 | `backend/app/core/verticals.py` | `PROFILES`（VerticalProfile 定義）。適用は `support_agent.py` 内 |
| 2 | Plan / Execute | `grace`（planner / executor + tools） | `support_agent.py` から呼び出し。出典整形は `gates.py` |
| 3 | Groundedness と回答ゲート | `grace.confidence` / `backend/app/core/gates.py` | 検証は `GroundednessVerifier`、判定は `_answer_gate` |
| 4 | 誤エスカレ・誤回答の抑止 | `backend/app/core/gates.py` | `_should_force_escalate` / `_should_rescue_unaffirmed` / `_detect_no_info_answer` |
| 5 | Web フォールバック | `backend/app/core/support_agent.py` | tools（web_search / reasoning）と相互検証の編成。補助関数は `gates.py` |
| 6 | アクションの安全な実行 | `support_actions.py` / `backend/app/core/intervention_bridge.py` | 本人確認・バックエンド実行・HITL 承認待ち |

### 主要機能一覧

| 機能 | 説明 |
|------|------|
| `run_support_agent_core()` | パイプライン全体の編成（(0)〜(8) の実行主体・イベント発行型） |
| `PROFILES` | (0) 業界プロファイル定義（gov / saas / ec） |
| `_collect_citations()` | (2) step_results から出典リストを作成（[社内]/[Web] ラベル付け） |
| `_answer_gate()` | (4) 支持率・出典数から answer / escalate を判定する純関数 |
| `_should_force_escalate()` | (4) エスカレ語の二段判定（キーワード → 意図分類） |
| `create_intent_classifier()` | (4)(6) 意図分類器（question / request / incident。軽量 LLM） |
| `_should_rescue_unaffirmed()` | (4-1) 出典付き・矛盾なしの内部回答を escalate から救済するか判定 |
| `_detect_no_info_answer()` | (4-2) 「情報なし回答」の二段判定（定型句候補 → 実質回答判定） |
| `create_no_info_judge()` | (4-2) 実質回答判定器（answered / no_info。軽量 LLM） |
| `_pick_groundedness()` / `_merge_citations()` | (5) 内部×Web の検証結果・出典の統合 |
| `_decide_action()` | (6) 回答判定と問い合わせ内容から実行アクションを決定（二段判定） |
| `_perform_action()` | (7)(8) 本人確認 → HITL CONFIRM → バックエンド実行の編成 |
| `InterventionBridge` | (8) HITL 承認の同期⇔非同期変換（Web のフロント承認待ち） |

---

---

## 1. アーキテクチャ構成図

### 1.1 システム全体構成

```mermaid
flowchart TB
    subgraph CLIENT["クライアント層"]
        WEB["Web: core/jobs.py ワーカースレッド<br>（emit=SSE / confirm=InterventionBridge）"]
    end

    subgraph FLOW["処理フロー (run_support_agent_core)"]
        S0["(0) S1 profile 適用"]
        S1["(1) ① Plan"]
        S2["(2) ② Execute（内部RAG）"]
        S3["(3) ③ Confidence"]
        S4["(4) ④ 回答ゲート＋強制エスカレ"]
        S41["(4-1) ④-救済"]
        S5["(5) ⑤ Web フォールバック"]
        S42["(4-2) ④' 情報なし検知"]
        S6["(6) ⑥ Action 決定"]
        S7["(7) 本人確認"]
        S8["(8) HITL CONFIRM → 実行"]
    end

    subgraph EXTERNAL["外部サービス・部品層"]
        GRACE["grace: planner / executor + tools<br>(rag_search / web_search / reasoning)"]
        CONF["grace.confidence:<br>GroundednessVerifier /<br>SourceAgreementCalculator"]
        JUDGE["軽量 LLM (judge_model → llm.light_model)<br>意図分類・実質回答判定"]
        ACT["support_actions.py:<br>ActionBackend / IdentityVerifier"]
        HITL["grace.intervention +<br>InterventionBridge（フロント承認）"]
    end

    WEB --> FLOW
    S0 --> S1 --> S2 --> S3 --> S4 --> S41 --> S5 --> S42 --> S6 --> S7 --> S8
    S1 --> GRACE
    S2 --> GRACE
    S3 --> CONF
    S4 --> JUDGE
    S41 --> JUDGE
    S42 --> JUDGE
    S5 --> GRACE
    S5 --> CONF
    S7 --> ACT
    S8 --> HITL
    S8 --> ACT
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class WEB,S0,S1,S2,S3,S4,S41,S5,S42,S6,S7,S8,GRACE,CONF,JUDGE,ACT,HITL default
style CLIENT fill:#1a1a1a,stroke:#fff,color:#fff
style FLOW fill:#1a1a1a,stroke:#fff,color:#fff
style EXTERNAL fill:#1a1a1a,stroke:#fff,color:#fff
```

### 1.2 データフロー

1. クライアント（Web ジョブ）が `run_support_agent_core(query, vertical, ..., emit, confirm)` を呼び出す
2. (0) プロファイルを解決し、検索スコープ（`config.qdrant.allowed_collections`）と方針（`config.llm.prompt_addendum`）を config へ注入する
3. (1)〜(3) Plan → Execute → Groundedness 検証で内部回答・出典・支持率を得る
4. (4)〜(4-1) 回答ゲート・強制エスカレ・救済で `decision`（answer / escalate）を確定する
5. (5) escalate かつ非強制エスカレなら Web で裏取りし、検証結果・出典を統合する
6. (4-2) answer の場合のみ「情報なし回答」を検知し、該当すれば escalate に倒す
7. (6)〜(8) アクションを決定し、本人確認 → HITL CONFIRM → バックエンド実行を経て結果メッセージを得る
8. 各ステップの進捗は `emit(SupportEvent)` で通知され、最終的に `SupportResult` が `result` イベントと戻り値で返る

---

## 2. モジュール構成図

### 2.1 内部モジュール構成

```mermaid
flowchart LR
    subgraph ORCH["support_agent.py（編成）"]
        CORE["run_support_agent_core()"]
        PERF["_perform_action()"]
    end

    subgraph GATES["gates.py（判定・整形）"]
        AG["_answer_gate()"]
        FE["_should_force_escalate()"]
        RES["_should_rescue_unaffirmed()"]
        NOI["_detect_no_info_answer()"]
        DA["_decide_action()"]
        IC["create_intent_classifier()"]
        NJ["create_no_info_judge()"]
        CC["_collect_citations() ほか出典系"]
    end

    subgraph VERTS["verticals.py（定義）"]
        PR["PROFILES"]
        AR["ActionRequest"]
    end

    subgraph BRIDGE["intervention_bridge.py"]
        BR["InterventionBridge"]
    end

    CORE --> AG
    CORE --> FE
    CORE --> RES
    CORE --> NOI
    CORE --> DA
    CORE --> IC
    CORE --> NJ
    CORE --> CC
    CORE --> PR
    CORE --> PERF
    DA --> AR
    PERF --> BR
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class CORE,PERF,AG,FE,RES,NOI,DA,IC,NJ,CC,PR,AR,BR default
style ORCH fill:#1a1a1a,stroke:#fff,color:#fff
style GATES fill:#1a1a1a,stroke:#fff,color:#fff
style VERTS fill:#1a1a1a,stroke:#fff,color:#fff
style BRIDGE fill:#1a1a1a,stroke:#fff,color:#fff
```

### 2.2 外部依存関係

| ライブラリ / パッケージ | バージョン | 用途 |
|-----------|-----------|------|
| `grace`（リポジトリ内） | - | planner / executor + tools / GroundednessVerifier / SourceAgreementCalculator / InterventionHandler |
| `support_actions`（リポジトリ内） | - | ActionBackend（dry-run / webhook / pseudo）・IdentityVerifier |
| ローカル LLM（Ollama） | `config.py::get_default_ollama_model()`（既定 `gemma4:12b-mlx`）。判定系は `judge_model()` が `llm.light_model` から解決（既定は同一モデル） | Plan / reasoning / 検証・分類・判定。**API キー不要** |
| Gemini Embedding API | `gemini-embedding-001`（3072次元） | RAG 検索の埋め込み |
| Qdrant | - | 内部ナレッジのベクトル検索（コレクション `*_anthropic`） |

### 2.3 内部依存モジュール

| モジュール | 用途 |
|-----------|------|
| `backend.app.core.support_agent` | パイプライン編成（(0)〜(8) の実行主体） |
| `backend.app.core.gates` | 回答ゲート・二段判定・救済・出典整形（純関数群） |
| `backend.app.core.verticals` | `PROFILES` / `ActionRequest` / `Intent` / `INTENT_MODEL` |
| `backend.app.core.intervention_bridge` | (8) HITL 承認のフロント連携（Web のみ） |

---

## 3. ステップ ↔ 実装対応と信頼度フロー

> 📎 **関数の一覧表は本書に持たない。** 公開シンボルの正本は
> [`reference/core_support_agent.md`](./reference/core_support_agent.md) /
> [`reference/core_gates.md`](./reference/core_gates.md) /
> [`reference/core_verticals.md`](./reference/core_verticals.md) である
> （旧 §3.2「関数一覧（カテゴリ別）」は 3 重管理だったため削除した）。

### 3.1 ステップ ↔ 実装対応表

| ステップ | 内容 | 主実装 | 定義元 |
|---|---|---|---|
| (0) | S1 profile: 業界プロファイル適用 | `run_support_agent_core` 内 + `PROFILES` | `support_agent.py` / `verticals.py` |
| (1) | ① Plan | `planner.create_plan()` | `grace`（呼び出しは `support_agent.py`） |
| (2) | ② Execute | `executor.execute()` + `_collect_citations()` | `grace` / `gates.py` |
| (3) | ③ Confidence | `verifier.verify()` + `_citation_text()` | `grace.confidence` / `gates.py` |
| (4) | ④ 回答ゲート＋強制エスカレ | `_answer_gate()` + `_should_force_escalate()` + `create_intent_classifier()` | `gates.py` |
| (4-1) | ④-救済 | `_should_rescue_unaffirmed()` | `gates.py` |
| (4-2) | ④' 情報なし回答検知 | `_detect_no_info_answer()` + `create_no_info_judge()` | `gates.py` |
| (5) | ⑤ Web フォールバック | `run_support_agent_core` 内 + `_web_citations()` / `_web_source_texts()` / `_merge_citations()` / `_pick_groundedness()` | `support_agent.py` / `gates.py` |
| (6) | ⑥ Action 決定 | `_decide_action()` | `gates.py` |
| (7) | 本人確認 | `create_identity_verifier()` + `_perform_action()` 内 | `support_actions.py` / `support_agent.py` |
| (8) | HITL CONFIRM | `_perform_action()` 内 + `InterventionBridge` | `support_agent.py` / `intervention_bridge.py` |

---

### 3.2 信頼度フローの比較（`grace/` と `backend/app/`）

#### 共有する測定プリミティブ

backend は独自に信頼度検証器を持たず、**grace の `GroundednessVerifier` をそのまま再利用**する
（`backend/app/core/support_agent.py` が `from grace.confidence import create_groundedness_verifier`）。

| 共有要素 | 内容 |
|---------|------|
| `GroundednessVerifier.verify(query, answer, sources)` | 各主張を supported/contradicted/neutral に LLM 判定 |
| `GroundednessResult.support_rate` | `supported / (supported+contradicted)`（neutral は分母外） |
| `has_contradiction` / `verified` | 矛盾検出／検証成立可否 |

→ **「回答の各主張が引用ソースに支持されるか」という測定は同一**。差が出るのはこの後段。

---

#### grace/ の信頼度フロー

`grace/executor.py::_calculate_overall_confidence()` が統括（詳細は
`grace/docs/confidence_calibration.md`）。

```
① 各ステップ ConfidenceScore（ConfidenceCalculator, 5軸）
② 最終回答の自己評価＋網羅度（LLMSelfEvaluator.evaluate_final）
③ 補助集約（ConfidenceAggregator.aggregate, weighted）
④ groundedness ブレンド（_blend_groundedness_confidence）
     answer_conf = 0.6*support_rate + 0.25*self_eval + 0.15*coverage
     contradiction → min(・, 0.3);  final = 0.8*answer_conf + 0.2*aggregated
⑤ 較正（Calibrator.transform, 温度スケーリング）
⑥ 介入判定（decide_action）
     ≥0.9 SILENT / ≥0.7 NOTIFY / ≥0.4 CONFIRM / else ESCALATE
```

- 出力は**単一の `overall_confidence`（0-1）**と 4 値の `InterventionLevel`。
- **温度スケーリング較正**を最後に適用する（`config/calibration.json`）。
- 曖昧クエリ（ask_user 計画・最終回答なし）は低信頼固定（0.3）で CONFIRM/ESCALATE 帯へ。

---

#### backend/app/ の判定フロー

`backend/app/core/support_agent.py` の ④〜⑤ で、grace の `overall_confidence` は**使わず**、
`GroundednessVerifier` の支持率を `gates.py` の純関数へ通して **answer/escalate** を決める。

```
④ 回答ゲート:
     gres = verifier.verify(query, internal_answer, citations)
     decision, warning = _answer_gate(gres.support_rate, gres.verified,
                                       len(citations), notify_th, confirm_th)
   ＋ 強制エスカレ: _should_force_escalate（エスカレ語＋意図分類の二段判定）
   ＋ ④救済:      _should_rescue_unaffirmed（出典付き・矛盾なし・実質回答を維持）
⑤ Web フォールバック（escalate かつ非強制時）:
     gres_web = verifier.verify(...);  再度 _answer_gate
     _pick_groundedness(gres, gres_web) で支持率と判定数を採用
④' 情報なし検知: _detect_no_info_answer（定型句＋軽量LLMの二段判定）→ 情報なしは escalate
⑥ Action: 本人確認 → 副作用アクション（requires_confirmation=True）のみ HITL CONFIRM → 実行
     （escalate_to_human は有人引き継ぎそのもので承認不要＝直接実行）
```

`_answer_gate` のロジック（純関数）:

| 条件 | 結果 |
|------|------|
| `not verified` または `citation_count == 0` | `("escalate", False)` |
| `support_rate ≥ notify_th` | `("answer", False)`（高信頼） |
| `support_rate ≥ confirm_th` | `("answer", True)`（中信頼・未確認注記） |
| それ未満 | `("escalate", False)` |

- しきい値 `notify_th`/`confirm_th` は**業界プロファイル**（gov=0.8/0.5 等）または config 既定
  （notify 0.7 / confirm 0.4）。
- 出力は **2 値（answer/escalate）＋ warning フラグ**。
- `SupportResult.overall_confidence` には executor の `overall_confidence`（＝grace 側の較正済み値）
  を**受領・表示**するが、**判定には使わない**（判定は groundedness 支持率ベース）。
- `groundedness`（support_rate）・`groundedness_decided` を結果に格納。

---

#### 比較表

| 観点 | grace/（executor + confidence + calibration） | backend/app/（support_agent + gates） |
|------|---|---|
| 測定の主プリミティブ | `GroundednessVerifier`（共有） | `GroundednessVerifier`（**同じものを再利用**） |
| 信頼度の主軸 | `overall_confidence`（5軸→groundedness ブレンド→較正） | groundedness `support_rate` を直結 |
| 較正（温度スケーリング） | **あり**（`Calibrator.transform`） | **なし**（支持率の生値でゲート） |
| 自己評価/網羅度 | `evaluate_final` で信頼度に混合 | 使わない（別途 no_info 判定を使用） |
| 最終判定の値域 | 4 値 `InterventionLevel`（SILENT/NOTIFY/CONFIRM/ESCALATE） | 2 値 `decision`（answer/escalate）＋ warning |
| 判定関数 | `decide_action`（しきい値: 0.9/0.7/0.4） | `_answer_gate`（notify_th/confirm_th・vertical 可変） |
| ビジネスルール | なし（汎用の介入判定のみ） | 強制エスカレ・④救済・④'情報なし検知・⑤Web再検証 |
| 意図分類の使用 | なし | あり（強制エスカレ／アクション判定の二段目） |
| `overall_confidence` の役割 | 介入判定に使用 | 受領・表示のみ（判定には未使用） |
| 想定ユースケース | 汎用の自律実行（Plan/Execute） | 業界特化サポート応答（HITL・回答/エスカレ） |

---

#### 並置フロー図

```mermaid
flowchart TB
    subgraph SHARED["共有: 測定プリミティブ"]
        GND["GroundednessVerifier.verify() → support_rate / contradiction / verified"]
    end

    subgraph GRACE["grace/（自律エージェント）"]
        G1["5軸合成 + 自己評価/網羅度"]
        G2["_blend_groundedness_confidence()"]
        G3["Calibrator.transform（較正）"]
        G4["decide_action → SILENT/NOTIFY/CONFIRM/ESCALATE"]
    end

    subgraph BACKEND["backend/app/（GRACE-Support）"]
        B1["_answer_gate → answer/escalate"]
        B2["_should_force_escalate（強制エスカレ）"]
        B3["_should_rescue_unaffirmed（④救済）"]
        B4["_detect_no_info_answer（④'情報なし）"]
        B5["⑤ Web 再検証 → _pick_groundedness"]
    end

    GND --> G2
    G1 --> G2
    G2 --> G3
    G3 --> G4

    GND --> B1
    B1 --> B2
    B2 --> B3
    B3 --> B4
    B4 --> B5
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class GND,G1,G2,G3,G4,B1,B2,B3,B4,B5 default
style SHARED fill:#1a1a1a,stroke:#fff,color:#fff
style GRACE fill:#1a1a1a,stroke:#fff,color:#fff
style BACKEND fill:#1a1a1a,stroke:#fff,color:#fff
```

---

#### 設計上の含意

- **測定は共有、判定は分離**。同じ支持率（groundedness）を、grace は「連続値の信頼度＋介入
  レベル」に、backend は「回答するか有人へ回すかの二択＋業界ルール」に変換する。責務が
  異なるため、判定ロジックを別モジュールに置くのは妥当。
- **較正の適用点の違い**。grace は較正済み `overall_confidence` を介入判定に使うが、backend の
  ゲートは支持率の生値を使う（較正は overall_confidence を通じて結果に**表示**されるのみ）。
  backend の回答/エスカレ境界を厳密に較正したい場合は、`notify_th`/`confirm_th` の
  プロファイル調整、または支持率への較正適用の是非が検討ポイントになる。
- **backend 固有の安全弁**。④救済（出典付き・矛盾なし・実質回答を escalate から救う）と
  ④'情報なし検知（誠実な「見つかりません」を有人へ倒す）は、groundedness 単独では拾えない
  誤判定を補正するための backend 独自ルールで、grace 側には無い。

---


---

## 4. 処理ステップ IPO詳細（0-(A)〜⑥）

### 4.0 0-(A) `analyze`: 入力・質問分析（複数質問の検知 → 選択 → 再構成）

**概要**: 1 つの入力に複数の質問が混ざっているかを**二段判定**で調べ、
主質問が複数あれば**利用者に選ばせて**から、選ばれた 1 問へクエリを再構成する。
担当範囲外の主質問は**選択肢に出さず**、断り＋窓口案内で返す。

```python
looks_multi = looks_like_multi_question(query)          # 第 1 段（LLM 不要）
analysis = analyze_questions(query, create_question_analyzer(config, profile)) \
           if looks_multi else QuestionAnalysis(None, None)   # 第 2 段（LLM 1 回）
in_scope_idx, out_scope_idx = split_by_scope(clusters, scope_classifier_for(...))
reconstructed_query = reconstruct_query(main, related, config)
```

| 項目 | 内容 |
|------|------|
| **Input** | `query`（原文）、`profile`（0-(B) の手前で解決済み）、`resolve_confirm`（HITL） |
| **Process** | 1. **第 1 段**: `looks_like_multi_question()` が接続表現・疑問符の数で候補判定（**LLM を呼ばない**）<br>2. **第 2 段**: 一致したときだけ `analyze_questions()` が主質問クラスタ（main + related）と**担当範囲（IN/OUT）**を **1 回の LLM 呼び出し**で返す<br>3. 範囲外の主質問は選択肢から外し `out_of_scope_questions` へ<br>4. 範囲内が 2 つ以上なら `InterventionRequest(options=…)` で**利用者に選ばせる**（自動選定しない）<br>5. 採用したクラスタで `reconstruct_query()` を実行し、以降のパイプラインは再構成後のクエリで走る<br>6. 範囲内で今回答えなかった主質問は `deferred_questions`（保留）として必ず提示する |
| **Output** | `query`（再構成後）、`question_clusters` / `adopted_cluster_index` / `reconstructed_query` / `deferred_questions` / `out_of_scope_questions`（`SupportResult` へ伝搬）。イベント: `step(analyze)` |

**安全側の倒し方**（いずれも「分析は前処理であってゲートではない」という方針）:

| 状況 | 倒す先 |
|---|---|
| 第 1 段で不一致 | `step_skipped("analyze", reason="第 1 段で不一致（単一質問）")` ＝現行フローそのもの |
| 第 2 段が単一と判断／判定不能 | 同上（`reason` で**どちらで倒れたかを残す**） |
| 選択が拒否・タイムアウト | **原文のまま単一質問として処理**（escalate に倒さない） |
| 選択肢に無い値が返った | 先頭の主質問を採用し、残りは**保留として必ず提示**（黙って落とさない） |
| 担当範囲の判定不能 | 全件を範囲内として扱う（答えられる質問を誤って断つ害の方が大きい） |

> ⚠️ **第 1 段が一致してから解析器を作る。** `create_question_analyzer()` は生成時点で
> LLM クライアントを組み立てるため、引数の位置で無条件に呼ぶと単一質問のリクエストでも
> 毎回クライアントを作ることになり、「第 1 段で LLM を呼ばずに弾く」という二段判定の狙いが半分崩れる。
>
> ⚠️ **分解と担当範囲判定は 1 回の LLM 呼び出しに畳んである。** 以前は 2 回呼んでおり、
> 実測 2026-08-30 で **16.3 秒 ＋ 2.2 秒**かかっていた（前者はモデルのウォームアップ込み）。
> ローカル LLM では往復 1 回の差が体感に直結する。
>
> 🔴 **保留した主質問は必ず出す。** 出さないと「片方が無言で落ちたのに、支持率が高いので
> 高信頼として提示される」事故と区別がつかない。
>
> 🔴 **範囲外は「保留」ではなく「担当範囲外」。** 選ばせても答えは変わらず
> （生成側の `SCOPE_POLICY` が断る）、利用者に無駄な 1 往復を強いるだけになる
> （実測 2026-08-29「住民票 ＋ 明日の天気」で顕在化）。

**関連する純関数**（IPO は [`reference/core_gates.md`](./reference/core_gates.md) が正本）:
`looks_like_multi_question` / `analyze_questions` / `create_question_analyzer` /
`split_by_scope` / `scope_classifier_for` / `create_scope_classifier` /
`reconstruct_query` / `fallback_reconstruct` / `deferred_main_questions`

---

### 4.1 0-(B) `profile`: 業界プロファイル適用（--vertical 指定時のみ）

**概要**: `vertical`（gov / saas / ec）に応じて検索スコープ・しきい値・エスカレ語・本人確認を
切り替える。`config` へ検索スコープと業界方針を注入することで、後続の tools（rag_search）と
reasoning に効かせる。未指定時は `step_skipped("profile")` としてスキップされる。

```python
# run_support_agent_core 内（support_agent.py）
profile = PROFILES.get(vertical) if vertical else None
notify_th = profile.notify_th if (profile and profile.notify_th is not None) else th.notify
confirm_th = profile.confirm_th if (profile and profile.confirm_th is not None) else th.confirm
config.qdrant.allowed_collections = list(profile.collections) if profile else []
config.llm.prompt_addendum = profile.prompt_addendum if profile else ""
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `vertical` | Optional[str] | None | 業界プロファイル ID（`gov` / `saas` / `ec`） |

| 項目 | 内容 |
|------|------|
| **Input** | `vertical: Optional[str]`, `PROFILES: Dict[str, VerticalProfile]`, `config`（grace 設定） |
| **Process** | 1. `PROFILES.get(vertical)` でプロファイル解決（None なら全設定を既定のまま）<br>2. `notify_th` / `confirm_th` をプロファイル値で上書き（None は config 既定を維持）<br>3. `config.qdrant.allowed_collections` に検索スコープを注入（未登録コレクションは自動無視）<br>4. `config.llm.prompt_addendum` に業界方針を注入（reasoning のプロンプトへ）<br>5. `step` イベント（started / finished、未指定時は skipped）を emit |
| **Output** | `profile: Optional[VerticalProfile]`, `notify_th: float`, `confirm_th: float`（後続ステップが参照） |

**戻り値例**:
```python
# step_finished("profile", ...) の data（SSE で配信される）
{
    "vertical": "ec",
    "name": "EC",
    "collections": ["ec_policy_anthropic", "ec_faq_anthropic"],
    "notify_th": 0.7,
    "confirm_th": 0.4,
    "require_identity": True,
    "prompt_addendum": "注文情報の照会・変更は本人確認必須。返品・交換は規定の版に基づいて回答。"
}
```

```python
# 使用例
from backend.app.core.verticals import PROFILES

profile = PROFILES.get("ec")
print(profile.name, profile.require_identity)
# 出力: EC True
```

### 4.2 ① `plan` Plan（planner）— クエリを実行計画に分解

**概要**: grace の planner がクエリを複雑度つきの実行計画（ステップ列）へ分解する。
計画の各ステップは (2) の executor が tools（rag_search 等）で実行する。

```python
plan = planner.create_plan(query)   # planner = create_planner(config)
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `query` | str | - | 問い合わせ内容（チャット入力） |

| 項目 | 内容 |
|------|------|
| **Input** | `query: str` |
| **Process** | 1. LLM（ローカル / Ollama）でクエリを分析し実行計画を生成<br>2. 複雑度（complexity）を推定<br>3. `step` イベントで進捗（ステップ数・複雑度）を emit |
| **Output** | `Plan`: `steps`（実行ステップ列）と `complexity: float` を持つ計画オブジェクト |

**戻り値例**:
```python
# step_finished("plan", ...) の data
{
    "steps": 2,
    "complexity": 0.35
}
```

```python
# 使用例
plan = planner.create_plan("返品したい")
print(f"{len(plan.steps)} ステップ (complexity={plan.complexity:.2f})")
# 出力: 2 ステップ (complexity=0.35)
```

### 4.3 ② `execute` Execute（executor + tools）— 内部RAG検索 → reasoning

**概要**: executor が計画を実行し、内部 RAG 検索（Qdrant）→ reasoning で回答を生成する。
RAG スコア不足時は executor が `web_search` を**動的挿入**するため、出典に Web 由来が混ざる
（`[Web]` プレフィックスで検知し `used_dynamic_web` として (5) の再利用判定に使う）。

```python
result = executor.execute(plan)
internal_answer = result.final_answer or ""
internal_citations = _collect_citations(result.step_results)
used_dynamic_web = any(c.startswith("[Web]") for c in internal_citations)
```

#### `_collect_citations`

```python
def _collect_citations(step_results) -> List[str]
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `step_results` | list | - | executor の各ステップ結果（`sources` を持つ） |

| 項目 | 内容 |
|------|------|
| **Input** | `plan: Plan`（executor へ）、`result.step_results`（_collect_citations へ） |
| **Process** | 1. executor が計画の各ステップを tools で実行（rag_search → reasoning）<br>2. RAG スコア不足時は web_search を動的挿入<br>3. 各ステップの sources を重複排除し、URL は `[Web]`・それ以外は `[社内]` とラベル付け<br>4. `[Web]` の有無で動的 Web 検索の使用を検知 |
| **Output** | `internal_answer: str`（内部回答）, `internal_citations: List[str]`, `used_dynamic_web: bool` |

**戻り値例**:
```python
# internal_citations
[
    "[社内] ec_policy_anthropic: 返品ポリシー.md",
    "[Web] https://example.com/returns-guide"
]
```

```python
# 使用例
citations = _collect_citations(result.step_results)
print(any(c.startswith("[Web]") for c in citations))
# 出力: True（RAG スコア不足で web_search が動的挿入された場合）
```

### 4.4 ③ `confidence` Confidence（GroundednessVerifier）— 支持率 support_rate

**概要**: 回答を主張（claim）単位に分解し、各主張が出典で裏付けられるかを検証する。
支持率 `support_rate = supported / (supported + contradicted)` と矛盾の有無が (4) の入力になる。

```python
gres = verifier.verify(query, internal_answer, [_citation_text(c) for c in internal_citations])
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `query` | str | - | 問い合わせ内容 |
| `answer` | str | - | 検証対象の回答（内部回答） |
| `sources` | List[str] | - | 出典テキスト（`_citation_text` でラベルを外した中身） |

| 項目 | 内容 |
|------|------|
| **Input** | `query: str`, `internal_answer: str`, `sources: List[str]` |
| **Process** | 1. 回答を主張単位に分解（LLM）<br>2. 各主張を出典と突き合わせ supported / contradicted / neutral に判定<br>3. 支持率・矛盾有無・検証成否を集計 |
| **Output** | `GroundednessResult`: `support_rate: float`, `supported: int`, `contradicted: int`, `total: int`, `verified: bool`, `has_contradiction: bool` |

**戻り値例**:
```python
# step_finished("confidence", ...) の data
{
    "support_rate": 0.75,
    "supported": 3, "contradicted": 1, "total": 5,
    "verified": True, "has_contradiction": True,
    "citations": 2
}
```

```python
# 使用例
gres = verifier.verify(query, answer, source_texts)
print(f"支持率={gres.support_rate:.2f}（判定可能 {gres.supported + gres.contradicted}/{gres.total} 主張）")
# 出力: 支持率=0.75（判定可能 4/5 主張）
```

### 4.5 ④ `gate` 回答ゲート（_answer_gate）＋ 強制エスカレ

**概要**: 支持率と出典数から回答可否を判定する。さらにプロファイルのエスカレ語に一致した場合は
二段判定（キーワード → 意図分類）で強制エスカレする（FAQ 質問は誤検知として抑止）。

#### `_answer_gate`

```python
def _answer_gate(
    support_rate: float,
    verified: bool,
    citation_count: int,
    notify_th: float,
    confirm_th: float,
) -> tuple[Decision, bool]
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `support_rate` | float | - | (3) の支持率 |
| `verified` | bool | - | 検証が成立したか（JSON 崩れ等は False） |
| `citation_count` | int | - | 出典数 |
| `notify_th` | float | - | 高信頼しきい値（プロファイルで上書き可） |
| `confirm_th` | float | - | 中信頼しきい値（同上） |

| 項目 | 内容 |
|------|------|
| **Input** | `support_rate: float`, `verified: bool`, `citation_count: int`, `notify_th: float`, `confirm_th: float` |
| **Process** | 1. 未検証 or 出典 0 → escalate<br>2. 支持率 ≥ notify_th → answer（高信頼）<br>3. confirm_th ≤ 支持率 < notify_th → answer ＋ 未確認注記（warning=True）<br>4. それ未満 → escalate |
| **Output** | `tuple[Decision, bool]`: (decision, warning)。decision は `"answer"` / `"escalate"` |

**戻り値例**:
```python
("answer", True)   # 中信頼: 回答するが「未確認」の注意書きを付ける
```

#### `_should_force_escalate`

```python
def _should_force_escalate(
    query: str,
    profile: Optional[VerticalProfile],
    classify: Optional[Callable[[str], Optional[Intent]]] = None,
) -> tuple[bool, Optional[str], Optional[Intent]]
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `query` | str | - | 問い合わせ内容 |
| `profile` | Optional[VerticalProfile] | - | (0) で解決したプロファイル（None なら常に不発動） |
| `classify` | Optional[Callable] | None | 意図分類器（`create_intent_classifier` の戻り値・メモ化済み） |

| 項目 | 内容 |
|------|------|
| **Input** | `query: str`, `profile: Optional[VerticalProfile]`, `classify: Optional[Callable]` |
| **Process** | 1. 第 1 段: `escalate_keywords` の部分一致（不一致なら不発動・LLM 呼び出しなし）<br>2. 第 2 段: 意図分類。`question`（FAQ 質問）なら誤検知として不発動<br>3. `request` / `incident` / 分類失敗（None）は安全側＝強制エスカレ |
| **Output** | `tuple[bool, Optional[str], Optional[Intent]]`: (forced, matched_keyword, intent) |

**戻り値例**:
```python
(False, "課金", "question")   # saas「課金プランの違いを教えて」→ FAQ 質問なので誤検知抑止
(True, "減免", "request")     # gov「減免を個別に判断してほしい」→ 設計どおり有人へ
```

```python
# 使用例
decision, warning = _answer_gate(0.75, True, 2, notify_th=0.8, confirm_th=0.5)
forced, kw, intent = _should_force_escalate("障害が発生しています", profile, classify)
if forced:
    decision, warning = "escalate", False
print(decision, forced, kw, intent)
# 出力: escalate True 障害 incident
```

### 4.6 ④-救済（_should_rescue_unaffirmed）

**概要**: 「肯定の裏付けが弱いだけで**矛盾は検出されていない**」出典付きの内部回答を
escalate から救済し、answer（未確認注記付き）として維持する。放置すると (5) の Web 二次生成で
「情報なし」回答に化けて (4-2) で誤エスカレする（ec「返金ポリシー」等で顕在化）ことへの対策。

```python
def _should_rescue_unaffirmed(
    decision: Decision,
    forced_escalate: bool,
    has_contradiction: bool,
    citation_count: int,
    answer: str,
    query: str,
    no_info_judge: Optional[Callable[[str, str], Optional[bool]]] = None,
) -> bool
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `decision` | Decision | - | (4) の判定結果 |
| `forced_escalate` | bool | - | 強制エスカレ発動有無（発動時は救済しない） |
| `has_contradiction` | bool | - | (3) の矛盾検出有無 |
| `citation_count` | int | - | 出典数 |
| `answer` | str | - | 内部回答本文 |
| `query` | str | - | 問い合わせ内容 |
| `no_info_judge` | Optional[Callable] | None | 実質回答判定器（(4-2) と共用） |

| 項目 | 内容 |
|------|------|
| **Input** | `decision`, `forced_escalate`, `has_contradiction`, `citation_count`, `answer`, `query`, `no_info_judge` |
| **Process** | 1. escalate 以外・強制エスカレ時は救済対象外<br>2. 矛盾あり・出典 0・回答空は救済対象外（安全側）<br>3. `_detect_no_info_answer` で実質回答かを確認（「情報なし」回答は救済せず従来どおり escalate） |
| **Output** | `bool`: True なら answer（未確認注記付き）へ救済 |

**戻り値例**:
```python
True   # 矛盾なし・出典 2 件・実質回答 → answer（warning=True）として維持
```

```python
# 使用例
if _should_rescue_unaffirmed(decision, forced, gres.has_contradiction,
                             len(citations), answer, query, no_info_judge):
    decision, warning = "answer", True   # ⑤ の無駄な Web 二次生成・誤エスカレを回避
```

### 4.7 ④' `no_info` 情報なし回答検知（_detect_no_info_answer）

**概要**: 誠実な「見つかりませんでした」型の回答は出典・支持率を伴ってゲートを answer で
通過してしまうため、二段判定（定型句候補 → 実質回答判定 Haiku）で検知し escalate に倒す。
出典が Web のみ（社内根拠ゼロ）の回答は候補句がなくても第 2 段判定を必須にする
（`force_judge=True`。out-of-scope × 動的 Web 検索対策）。**実行位置は (5) の後・
`decision == "answer"` の場合のみ**。

```python
def _detect_no_info_answer(
    query: str,
    answer: str,
    judge: Optional[Callable[[str, str], Optional[bool]]] = None,
    force_judge: bool = False,
) -> tuple[bool, Optional[str]]
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `query` | str | - | 問い合わせ内容 |
| `answer` | str | - | 判定対象の回答 |
| `judge` | Optional[Callable] | None | 実質回答判定器（`create_no_info_judge` の戻り値） |
| `force_judge` | bool | False | True なら候補句不一致でも第 2 段判定を実施（出典 Web のみの回答） |

| 項目 | 内容 |
|------|------|
| **Input** | `query: str`, `answer: str`, `judge: Optional[Callable]`, `force_judge: bool` |
| **Process** | 1. 第 1 段: `NO_INFO_MARKERS`（「見当たりません」等 6 句・語幹照合）の部分一致<br>2. 不一致かつ force_judge=False → (False, None)（LLM 呼び出しなし）<br>3. 第 2 段: 軽量 LLM が実質回答（answered）か情報なし（no_info）かを判定<br>4. 判定失敗（None）は安全側＝True（escalate）に倒す |
| **Output** | `tuple[bool, Optional[str]]`: (no_info, matched_marker) |

**戻り値例**:
```python
(True, "見当たりません")    # 情報なし回答 → escalate（no_info_detected=True）
(False, "見当たりません")   # 候補句はあるが実質回答（例: 一般ルール提示＋断り書き）→ answer 維持
(True, None)               # 出典 Web のみ・候補句なしだが実質情報ゼロ → escalate
```

```python
# 使用例
web_only = bool(citations) and all(c.startswith("[Web]") for c in citations)
no_info, marker = _detect_no_info_answer(query, answer, no_info_judge, force_judge=web_only)
if no_info:
    support.decision, support.no_info_detected = "escalate", True
```

### 4.8 ⑤ `web` Web フォールバック

**概要**: 内部判定が escalate（かつ強制エスカレでない・`use_web=True`）の場合のみ Web で
裏取りする。(2) で executor が**同一クエリの Web 検索を使用済み**なら、回答を作り直さず
内部回答を本文スニペットで**再検証だけ**行う（重複推論の省略。1 ケース十数秒〜の短縮）。
未使用なら `web_search → reasoning` で Web 回答を生成し、内部×Web の相互検証を行う。

```python
# run_support_agent_core 内（support_agent.py）。主な補助関数:
def _web_citations(web_output: list) -> List[str]
def _web_source_texts(web_output: list) -> List[str]
def _merge_citations(internal: List[str], web: List[str]) -> List[str]
def _pick_groundedness(*results) -> tuple[float, int]
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `decision` | Decision | - | (4)〜(4-1) 確定後の判定（escalate のときのみ実行） |
| `use_web` | bool | True | Web フォールバックの有効化（`--no-web` 相当の逆） |
| `used_dynamic_web` | bool | - | (2) の動的 Web 検索使用有無（再利用判定） |

| 項目 | 内容 |
|------|------|
| **Input** | `query`, `internal_answer`, `internal_citations`, `used_dynamic_web`, `notify_th`, `confirm_th` |
| **Process** | 1. `web_search` を実行（結果なしなら used_web=True のみ記録して終了）<br>2. 再利用時: `web_answer = internal_answer`（reasoning 省略）／ 非再利用時: `reasoning` で Web 回答を生成<br>3. Web 出典・本文スニペットで再検証（GroundednessVerifier）<br>4. 非再利用時のみ内部×Web の意味的一致度を算出（`agreement < confirm_th` なら矛盾扱い。再利用時は同一回答の比較になるためスキップ）<br>5. `_answer_gate` で Web 側判定 → `_pick_groundedness` / `_merge_citations` で結果を統合し `SupportResult` を再構築 |
| **Output** | `SupportResult`（更新）: `answer`（w_decision=answer なら Web 回答）, `citations`（統合済み）, `used_web=True`, `web_reused`, `source_agreement`, `contradiction` |

**戻り値例**:
```python
# step_finished("web", ...) の data
{
    "web_reused": True,          # 内部回答を再利用（重複推論を省略）
    "citations": 3,
    "decision": "answer", "warning": True,
    "support_rate": 0.67,
    "agreement": None,           # 再利用時は相互検証をスキップ
    "contradiction": False
}
```

```python
# 使用例（結果の統合）
g_rate, g_decided = _pick_groundedness(gres, gres_web)       # 支持率が最大の検証結果を採用
citations = _merge_citations(internal_citations, web_citations)  # URL 包含で重複排除
```

### 4.9 ⑥ `action` Action（_decide_action）— do_action 時

**概要**: 回答判定と問い合わせ内容から実行アクションを決める。escalate 時は常に
`escalate_to_human`（**承認不要**・直接実行。タイムアウトで引き継ぎが宙に浮くのを防ぐ）。
answer 時は action_map の二段判定（キーワード → 意図分類）で起票／返信を決め、
FAQ 質問（intent=question）ならアクションなし（回答のみ）とする。

```python
def _decide_action(
    query: str,
    decision: Decision,
    profile: Optional[VerticalProfile] = None,
    classify: Optional[Callable[[str], Optional[Intent]]] = None,
) -> Optional[ActionRequest]
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `query` | str | - | 問い合わせ内容 |
| `decision` | Decision | - | 確定済みの回答判定 |
| `profile` | Optional[VerticalProfile] | None | プロファイル（`action_map` を使用。None は既定マッピング） |
| `classify` | Optional[Callable] | None | 意図分類器（(4) とメモ化を共有） |

| 項目 | 内容 |
|------|------|
| **Input** | `query: str`, `decision: Decision`, `profile: Optional[VerticalProfile]`, `classify: Optional[Callable]` |
| **Process** | 1. escalate → `ActionRequest("escalate_to_human", requires_confirmation=False)` を即返す<br>2. 第 1 段: `profile.action_map`（未指定時は既定マッピング）のキーワード一致で候補検出<br>3. 第 2 段: 意図分類。`question` なら None（回答のみ）。分類失敗は従来どおり起票（副作用は (8) の CONFIRM でも守られる） |
| **Output** | `Optional[ActionRequest]`: `action_type`（create_ticket / send_reply / escalate_to_human）・`args`・`requires_confirmation` |

**戻り値例**:
```python
ActionRequest(
    action_type="create_ticket",
    args={"query": "返品したい", "matched": "返品"},
    requires_confirmation=True,
)
```

```python
# 使用例
action = _decide_action("解約方法を教えて", "answer", profile, classify)
print(action)
# 出力: None（intent=question → FAQ 回答のみ。起票しない）
```

### 4.10 ⑥ 本人確認（require_identity）

**概要**: プロファイルが `require_identity=True`（例: ec）の場合、アクション実行前に
`IdentityVerifier` で提示識別子を照合する。**未確認ならアクションを実行せず有人対応へ
引き継ぐ**（安全側）。`_perform_action` の最初の段で行われる。

```python
# support_actions.py
def create_identity_verifier(dry_run: bool = True) -> IdentityVerifier
class IdentityVerifier:
    def verify(self, provided: Optional[Dict[str, str]]) -> IdentityResult
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `provided` | Optional[Dict[str, str]] | None | 提示された識別子（注文番号・メール等。Web 版は現状 None） |

| 項目 | 内容 |
|------|------|
| **Input** | `identity: Optional[Dict[str, str]]`, `require_identity: bool`（プロファイル由来） |
| **Process** | 1. `require_identity=False` なら本ステップをスキップ<br>2. `identity_verifier.verify(identity)` で照合（方式・詳細つきの結果）<br>3. 未確認（verified=False）→ アクション中断・有人対応への引き継ぎメッセージを返す<br>4. 確認済み → (8) HITL CONFIRM へ進む |
| **Output** | `IdentityResult`: `verified: bool`, `method: str`, `detail: str`。未確認時は `_perform_action` が中断メッセージ str を返す |

**戻り値例**:
```python
# 未確認時に _perform_action が返すメッセージ
"本人確認が完了しないため 'create_ticket' は実行せず、有人対応へ引き継ぎます"
```

```python
# 使用例（_perform_action 内の流れ）
identity_verifier = create_identity_verifier(dry_run=True) if require_identity else None
result = identity_verifier.verify(identity)   # identity=None → 未確認（安全側）
print("確認済み" if result.verified else "未確認")
# 出力: 未確認
```

### 4.11 ⑥ HITL CONFIRM（フロント承認待ち／タイムアウト → 実行せず有人へ）

**概要**: 副作用のあるアクション（`requires_confirmation=True`。create_ticket / send_reply）は
実行前に必ず人間の承認を経由する。Web では `InterventionBridge` が `intervention` イベントを
SSE へ流してフロントの CONFIRM モーダル応答（`POST /api/support/confirm/{job_id}`）を待ち、
**タイムアウト時は安全側＝実行せずエスカレーション**する。テストでは無条件承認（`AUTO_PROCEED`）
だが、**Web 側に自動承認は持ち込まない**（受け入れ条件 §5-2）。

```python
# intervention_bridge.py
class InterventionBridge:
    def resolver(self, request: InterventionRequest) -> InterventionResponse  # ワーカー側（ブロック）
    def resolve(self, intervention_id: str, approve: bool) -> bool            # API 側（応答注入）
```

| パラメータ | 型 | デフォルト | 説明 |
|------------|------|-----------|------|
| `request` | InterventionRequest | - | CONFIRM/ESCALATE 要求（message / reason / timeout_seconds 等） |
| `intervention_id` | str | - | 承認対象 ID（`intervention` イベントで配信済み） |
| `approve` | bool | - | True=承認（PROCEED・実行） / False=拒否（CANCEL） |

| 項目 | 内容 |
|------|------|
| **Input** | `ActionRequest`（requires_confirmation=True）、フロントの承認応答（approve: bool） |
| **Process** | 1. `handler.handle(decision)` → `InterventionBridge.resolver` が呼ばれる<br>2. `intervention` イベント（status=waiting, intervention_id, timeout_seconds）を emit<br>3. `threading.Event` で応答を待機（タイムアウト既定 300 秒）<br>4. 応答あり → PROCEED なら `backend.execute(action_type, args)` を実行 / CANCEL なら中止<br>5. タイムアウト → `CANCEL + timeout_reached=True` を返し、実行せず有人対応へエスカレーション |
| **Output** | `str`: アクション結果メッセージ（`SupportResult.action_result`。実行成功／キャンセル／タイムアウト引き継ぎ） |

**戻り値例**:
```python
# タイムアウト時（安全側）
"承認待ちがタイムアウトしたため 'create_ticket' は実行せず、有人対応へエスカレーションします"
# 拒否時
"アクション 'create_ticket' はキャンセルされました"
# 承認時（dry-run バックエンド実行後の ActionOutcome.message 例）
"[dry-run] create_ticket は実行されませんでした（引数: {'query': '返品したい', 'matched': '返品'}）"
```

```python
# 使用例（API 側からの承認注入。core/jobs.py の confirm 経由）
status = job_manager.confirm(job_id, intervention_id, approve=True)
print(status)
# 出力: resolved（待機中でなければ not_waiting、ジョブ不在なら not_found）
```

---

## 5. 設計判断

> 処理の**順序と入出力**は §4、**なぜその判定・しきい値なのか**は本節にある。
> HITL の機構そのもの（承認待ちの橋渡し・タイムアウト）は
> [`job_runtime.md` §4](./job_runtime.md#4-hitl-承認の橋渡しinterventionbridge) が正本。

### 5.1 回答判定フロー（全体像）

RAG 回答を **groundedness（支持率）でゲート**し、状態に応じて「回答／Web 調査／確認／エスカレ／アクション」へ分岐する。

```mermaid
flowchart TB
    Q(["ユーザー問い合わせ"])
    CLS["① Plan: 質問分類<br>planner.py（FAQ/調査/要対応）"]
    RAG["② Execute: 内部RAG検索<br>tools.RAGSearchTool"]
    GND["③ Confidence: 支持率評価<br>confidence.GroundednessVerifier"]
    GATE{"回答ゲート<br>支持率 × 出典数"}
    ANS["出典つき回答<br>（SILENT/NOTIFY）"]
    WARN["回答＋未確認の注意<br>（CONFIRM 任意）"]
    WEB["⑤ Replan: Webフォールバック<br>tools.WebSearchTool＋相互検証"]
    ESC["④ Intervention: 有人エスカレ<br>intervention ESCALATE"]
    ACT{"要対応アクション？"}
    HITL["④ Intervention: 承認要求<br>CONFIRM（人間承認）"]
    DO["ActionTool 実行<br>（既定ドライラン=ログ）"]
    OUT(["SupportResult を返す"])
    Q --> CLS --> RAG --> GND --> GATE
    GATE -->|" 高: 支持率>=0.7 かつ 出典>=1 "| ANS
    GATE -->|" 中: 0.4-0.7 "| WARN
    GATE -->|" 低/0件 "| WEB
    WEB -->|" 裏取り成功 "| ANS
    WEB -->|" なお不足 "| ESC
    ANS --> ACT
    WARN --> ACT
    ACT -->|" 必要 "| HITL --> DO --> OUT
    ACT -->|" 不要 "| OUT
    ESC --> OUT
    classDef default fill:#000,stroke:#fff,color:#fff
    class Q,CLS,RAG,GND,GATE,ANS,WARN,WEB,ESC,ACT,HITL,DO,OUT default
```

---

---

### 5.2 回答ポリシー（groundedness ゲート）

`GroundednessVerifier` の **支持率 (support_rate)**と**出典数**で分岐する。しきい値は既存 `config.confidence.thresholds`（
`silent=0.9 / notify=0.7 / confirm=0.4`）を流用する。

| 状態           | 条件（例）                 | decision            | 振る舞い                                                             |
|----------------|----------------------------|---------------------|----------------------------------------------------------------------|
| **自信あり**   | 支持率 ≥ 0.7 かつ 出典 ≥ 1 | `answer`            | 出典つきで自動回答（SILENT/NOTIFY）                                  |
| **要注意**     | 0.4 ≤ 支持率 < 0.7         | `answer`（注意付）  | 回答＋「未確認の注意書き」、必要なら CONFIRM                         |
| **わからない** | 支持率 < 0.4 または 出典 0 | `escalate` 前に Web | 「社内ナレッジには見当たりません」→ Web 調査 → なお不足なら ESCALATE |

> **設計意図**: 「根拠のない断定を構造的に出さない」ことを最優先にする。既存の `GroundednessVerifier`（回答を主張に分解し
> supported/contradicted/neutral を判定）をそのまま利用し、支持率が低い＝出典で裏付けられない回答は
> **自動的に“わからない”へ倒す**。

---

---

### 5.3 データ契約（dataclass）

v1〜v3 ＋業界特化では `backend/app/core/verticals.py` / `core/support_agent.py` 内の **dataclass** として実装している（コア `schemas.py`
への追加は将来。出典は当面 `list[str]`）。

```python
@dataclass
class ActionRequest:
    """副作用のある操作の要求（v3・擬似）。"""
    action_type: Literal["create_ticket", "send_reply", "escalate_to_human"]
    args: dict = field(default_factory=dict)  # 起票内容・宛先など
    requires_confirmation: bool = True  # 副作用は原則 True


@dataclass
class SupportResult:
    """サポート回答の結果（回答ゲート／Web／アクション／業界特化を集約）。"""
    answer: Optional[str]  # 最終回答（出典つき）
    citations: List[str] = field(default_factory=list)  # "[社内] …" / "[Web] …"
    groundedness: float = 0.0  # 支持率 (0.0-1.0)
    groundedness_decided: int = 0  # 判定できた主張数(supported+contradicted)。0=判定不能
    decision: Literal["answer", "escalate"] = "escalate"
    warning: bool = False  # 中信頼（未確認）の注意書きを付けるか
    used_web: bool = False  # Web（動的検索 or ⑤ フォールバック）を使ったか
    source_agreement: Optional[float] = None  # 内部×Web の意味的一致度（相互検証）
    contradiction: bool = False  # 矛盾の可能性
    action: Optional[ActionRequest] = None  # 実施（予定）のアクション
    action_result: Optional[str] = None  # アクションの結果メッセージ
    vertical: Optional[str] = None  # 適用した業界プロファイル（gov/saas/ec）
    overall_confidence: float = 0.0  # executor 由来の較正済み全体信頼度
    intent: Optional[Literal["question", "request", "incident"]] = None  # 二段判定の意図分類結果
    forced_escalate: bool = False  # エスカレ語による強制エスカレか（KPI 用）
    identity_checked: bool = False  # 本人確認ステップが起動したか（KPI 用）
    no_info_detected: bool = False  # ④' 情報なし回答検知で escalate に倒したか
    web_reused: bool = False  # ⑤ で executor の Web 結果を再利用したか
```

> 📝 `decision` は `answer` / `escalate` の 2 値（設計当初の `ask`/`action` は `warning` フラグ・`action` フィールドに整理）。
> `groundedness_decided` / `intent` / `forced_escalate` / `identity_checked` / `no_info_detected` / `web_reused` /
> `vertical` は **業界特化・二段判定・④' ゲート・KPI 計測**のために追加したフィールド（`eval/vertical/` が参照）。
> `Citation` の構造化（kind/collection/score）はコア schemas 化時に導入予定。

---

---

### 5.4 アクション実行の設計（`ActionTool` 案）

副作用のある操作を担う。**既定はドライラン（実行せずログ出力）**で、学習・検証を安全に行う。

| 項目           | 内容                                                                                                                     |
|----------------|--------------------------------------------------------------------------------------------------------------------------|
| クラス         | `ActionTool(BaseTool)`（`../../grace/tools.py` へ追加、`ToolRegistry` に opt-in 登録）                                   |
| `name`         | `action`（`PlanStep.action` に `"action"` を追加、または `create_ticket` 等の細分）                                      |
| メソッド       | `execute(action_type: str, args: dict, dry_run: bool = True) -> ToolResult`                                              |
| 対応アクション | `create_ticket` / `send_reply` / `escalate_to_human`                                                                     |
| 安全策         | ① 実行前に **CONFIRM 必須**（intervention 経由）② `dry_run=True` ならログのみ ③ 対象・引数を `confidence_factors` に残す |
| 既定           | `config.tools.enabled` には**含めない**（`code_execute` と同様の opt-in）                                                |

> セキュリティ方針は既存 `CodeExecuteTool`（静的チェック＋資源制限＋opt-in）に倣う。実 API 連携（Zendesk / メール等）は将来拡張とし、MVP
> では擬似実装。

---

---

### 5.5 HITL ポリシー

| トリガー                         | 介入レベル    | 挙動                                      |
|----------------------------------|---------------|-------------------------------------------|
| 副作用のあるアクション実行前     | **CONFIRM**   | 人間承認を得るまで実行しない              |
| 出典不足・低信頼（支持率 < 0.4） | **ESCALATE**  | 有人対応へ引き継ぎ、AI は回答を断定しない |
| 中信頼（0.4–0.7）                | NOTIFY        | 回答するが「未確認」を明示                |
| 高信頼（≥ 0.7・出典あり）        | SILENT/NOTIFY | 自動回答                                  |

- 非対話の検証（テスト・スクリプト）では、CONFIRM/ESCALATE のコールバックを **無条件承認＋ログ**（dry-run 既定）にして安全に確かめる。
- UI 連携時は実際の確認ダイアログ（`intervention.ConfirmationFlow`）に差し替える。

---

---

### 5.6 設計レベルの処理シーケンス

```mermaid
%%{ init: { "theme": "base", "themeVariables": {
  "background": "#000000", "mainBkg": "#000000",
  "textColor": "#ffffff", "lineColor": "#ffffff",
  "actorBkg": "#000000", "actorTextColor": "#ffffff",
  "actorLineColor": "#ffffff", "noteBkgColor": "#000000",
  "noteTextColor": "#ffffff", "noteBorderColor": "#ffffff" } } }%%
sequenceDiagram
    participant U as "ユーザー"
    participant S as "run_support_agent()"
    participant PL as "planner.py"
    participant EX as "executor.py"
    participant CO as "confidence.py"
    participant IN as "intervention.py"
    participant AC as "ActionTool（新規）"
    U ->> S: 問い合わせ
    S ->> PL: 分類 + create_plan
    PL -->> S: ExecutionPlan
    S ->> EX: execute(plan)（内部RAG→必要ならWeb）
    EX -->> S: ExecutionResult + sources
    S ->> CO: GroundednessVerifier.verify(answer, sources)
    CO -->> S: 支持率 / 出典
    alt 支持率>=0.7 かつ 出典>=1
        Note over S: decision=answer（出典つき回答）
    else 支持率<0.4 または 出典0
        S ->> IN: ESCALATE（有人へ）
        Note over S: decision=escalate
    end
    opt 要対応アクション
        S ->> IN: CONFIRM（人間承認）
        IN -->> S: 承認
        S ->> AC: execute(action_type, args, dry_run=True)
        AC -->> S: ToolResult（ログ）
    end
    S -->> U: SupportResult
```

---


---

## 6. 評価指標（KPI）

需要（サポート業務）に直結する指標をそのまま評価に使う。

| 指標                     | 定義                        | 目標                 |
|--------------------------|-----------------------------|----------------------|
| 自己解決率（deflection） | 有人に回さず解決した割合    | 高いほど良い         |
| 出典付与率               | 回答に出典が付いた割合      | ≈ 100%               |
| 根拠なし回答率           | 出典/根拠なしで断定した割合 | **0 に近いほど良い** |
| エスカレーション適合率   | ESCALATE が妥当だった割合   | 高いほど良い         |
| 平均応答時間             | 問い合わせ→回答             | 低いほど良い         |

---

## 7. 設定・定数

### 7.1 パイプラインのステップ ID（STEP_IDS）

UI のタイムライン表示と 1:1 対応。各ステップは `step` イベント（started / finished / skipped）で配信される。

```python
STEP_IDS = (
    "analyze",     # (0-A) 入力・質問分析（複数質問の検知 → 選択 → 再構成）
    "profile",     # (0-B) 業界プロファイル適用
    "plan",        # (1) ① Plan
    "execute",     # (2) ② Execute（内部RAG → reasoning）
    "confidence",  # (3) ③ Groundedness
    "gate",        # (4)(4-1) ④ 回答ゲート＋強制エスカレ＋④-救済
    "web",         # (5) ⑤ Web フォールバック
    "no_info",     # (4-2) ④' 情報なし回答検知
    "action",      # (6)(7)(8) ⑥ Action（本人確認 → HITL CONFIRM → 実行）
)
```

### 7.2 しきい値・モデル・タイムアウト

| キー | デフォルト値 | 説明 |
|-----|-------------|------|
| `notify_th` / `confirm_th` | config 既定（gov のみ 0.8 / 0.5 に上書き） | (4) 回答ゲートのしきい値 |
| `INTENT_MODEL` | `get_default_ollama_model()` | 判定系のフォールバック用モデル名。**直接使わず `gates.judge_model(config)` 経由で `llm.light_model` を優先する**（経路が割れると 404 になる） |
| `NO_INFO_MARKERS` | 「見当たりません」等 6 句 | (4-2) 第 1 段の候補検出（語幹照合） |
| `DEFAULT_CONFIRM_TIMEOUT` | 300（秒） | (8) 承認待ちのフォールバックタイムアウト |
| `dry_run` | True | (8) アクションバックエンドの既定（実行せず記録のみ） |

---

## 8. 使用例

### 8.1 基本的なワークフロー（スクリプトから直接・自動承認）

```python
from backend.app.core.support_agent import run_support_agent_core

# emit/confirm を渡さない場合: 通知なし・自動承認（既定 dry_run のため安全）
result = run_support_agent_core(
    query="返品したい",
    vertical="ec",
)
print(result.decision, result.action.action_type if result.action else None)
# 出力: answer create_ticket
```

### 8.2 応用: イベント購読と HITL 承認（Web 相当）

```python
from backend.app.core.jobs import JobParams, job_manager

# (0)〜(8) をワーカースレッドで実行し、進捗イベントを蓄積する
job = job_manager.start(JobParams(query="返品したい", vertical="ec"))

for event in job.stream_events():
    if event is None:
        continue  # keepalive
    if event["type"] == "intervention" and event["status"] == "waiting":
        # (8) フロントの CONFIRM モーダル相当: 承認を注入
        job_manager.confirm(job.job_id, event["data"]["intervention_id"], approve=True)
    if event["type"] == "result":
        print(event["data"]["decision"], event["data"]["action_result"])
        # 出力例: answer [dry-run] create_ticket は実行されませんでした（...）
```

---

## 9. エクスポート

本ドキュメントが対象とする各モジュールに `__all__` 定義はない。外部から参照される
実質的な公開シンボルは以下のとおり。

```python
# backend.app.core.support_agent
run_support_agent_core   # パイプライン本体（(0)〜(8) の実行主体）
SupportEvent / SupportResult / result_to_dict / STEP_IDS

# backend.app.core.gates
_answer_gate / _should_force_escalate / _should_rescue_unaffirmed
_detect_no_info_answer / _decide_action / create_intent_classifier / create_no_info_judge

# backend.app.core.verticals
PROFILES / VerticalProfile / ActionRequest / Intent / INTENT_MODEL

# backend.app.core.intervention_bridge
InterventionBridge
```

---

## 10. 実装ロードマップ

| 版                      | 機能                                                                                           | 追加実装                                                                                                      | 状態                                                                                         |
|-------------------------|------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------|
| **v1 (MVP)**            | 内部 RAG → 出典つき回答／根拠不足なら「わかりません」                                          | 回答ゲート（`_answer_gate`）＋ `SupportResult`                                                                | ✅ 実装済み（PR #99）                                                                        |
| **v2**                  | 内部不足時に Web フォールバック＋相互検証（矛盾提示）                                          | web_search 起動条件・引用統合・`SourceAgreementCalculator`                                                    | ✅ 実装済み（PR #100）                                                                       |
| **v3**                  | アクション（起票/返信/エスカレ）＋ HITL（ドライラン）                                          | 擬似 ActionTool ＋ CONFIRM 配線（`_decide_action`/`_perform_action`）                                         | ✅ 実装済み（PR #101）                                                                       |
| **業界特化**            | `--vertical {gov\|saas\|ec}`（検索スコープ・エスカレ語・しきい値・アクション・本人確認・方針） | `VerticalProfile`／`PROFILES`／二段判定（`_should_force_escalate`）／`allowed_collections`／`prompt_addendum` | ✅ 実装済み（PR #106 ほか。[`verticals_and_rulesets.md` §1](./verticals_and_rulesets.md)）      |
| **④' 情報なし検知ほか** | 「見つかりません」型回答の escalate 化・Web 重複実行の排除・KPI 是正・本人確認フロー           | `_detect_no_info_answer`／`create_no_info_judge`／`_should_rescue_unaffirmed`／`../../support_actions.py`     | ✅ 実装済み（PR #116〜#129。[`verticals_and_rulesets.md` §1](./verticals_and_rulesets.md)） |

---

---

## 11. 変更履歴

| Version | 変更内容 |
|---|---|
| 3.0 | **`backend_flow.md` → `support_flow.md` へ改称し、設計 3 文書を統合**（2026-09-16）。① `agent_support_example.md`（1,092 行）の設計判断（回答ポリシー / データ契約 / ActionTool 案 / HITL ポリシー / 処理シーケンス）を **§5 設計判断**へ、CLI 仕様を**付録A**へ、KPI を **§6**、実装ロードマップを **§10** へ ② `agent_support_example_flow.md`（491 行）を**付録B**へ ③ `confidence_flow_grace_vs_backend.md`（258 行）を **§3.2** へ。**ステップ番号を `CLAUDE.md` §1 の体系（`0-(A)` `0-(B)` `①`〜`⑥` `④'`）へ統一**し、**旧版に無かった `0-(A)` `analyze`（入力・質問分析）を §4.0 として新規に書き起こした**（`STEP_IDS` には以前から存在するが、本書は 8 ステップしか書いていなかった）。旧 §3.2「関数一覧（カテゴリ別）」は `reference/core_*.md` と 3 重管理だったため削除してリンクに置換。業界特化（旧 `agent_support_verticals.md`）は [`verticals_and_rulesets.md` §1](./verticals_and_rulesets.md) へ分離した |
| 2.x 以前 | `backend_flow.md` としての履歴。git で追える（`git log --follow backend/docs/support_flow.md`） |

---

## 付録A: 旧 CLI 仕様（削除済み・記録）

> ⚠️ **CLI（`agent_support_example.py`）は 2026-09-20 に削除した。**
> `run_support_agent_core()` を呼ぶだけの薄いラッパで、機能確認用だったため。
> 実装は git 履歴に残る（`git log --follow -- agent_support_example.py`）。
> **現在の操作はすべて Web UI（:5173）か API から行う。**
> 旧 CLI 引数と画面操作の対応表は `README.md` §3.1 にある。

旧 CLI が受け取っていた引数（当時の仕様。リクエストの各フィールドがどこから来るかの
参考として残す）:

| 引数 | 既定 | 対応する API フィールド |
|------|------|------|
| `query`（位置・任意） | `"パスワードを忘れました"` | `query` |
| `-v`, `--verbose` | off | `verbose` |
| `--vertical {gov\|saas\|ec}` | なし（共通挙動） | `vertical` |
| `--no-web` | off（Web 有効） | `use_web: false` |
| `--no-action` | off（アクション有効） | `do_action: false` |
| `--dry-run / --no-dry-run` | `dry-run`（安全） | `dry_run` |
| `--identity KEY=VALUE`（複数可） | なし | `identity` |

> 📎 `--vertical gov "住民票の写しの取り方は？"` 相当の 1 実行が、§1 のフロー図をどう流れるか
> （各ステップの IN/OUT データ）は**付録B**を参照。
> 業界特化の全体設計・KPI・テストデータは [`verticals_and_rulesets.md` §1](./verticals_and_rulesets.md)。

---

## 付録B: 1 リクエスト実行トレース

> 📝 旧 `agent_support_example_flow.md`（491 行）を統合した付録。
>
> ⚠️ 本文は当時 CLI（`agent_support_example.py`・2026-09-20 削除）で採取したトレースだが、
> **CLI も Web も同じ `run_support_agent_core()` を通っていた**ため、S1 以降の
> モジュール・データの流れは現在の Web 経路と同一である。入口（S0）だけが
> argparse から FastAPI のリクエストへ変わっている。

### B.0 対象リクエストと前提

```python
run_support_agent_core("住民票の写しの取り方は？", vertical="gov")
```

| 項目               | 値                                                                      |
|--------------------|-------------------------------------------------------------------------|
| クエリ             | `"住民票の写しの取り方は？"`（自治体 in-scope の代表質問）              |
| プロファイル       | `gov`（`PROFILES["gov"]` = 自治体）                                     |
| しきい値           | `notify_th=0.8` / `confirm_th=0.5`（3 業種で最も厳格）                  |
| 検索スコープ       | `gov_faq_anthropic` / `gov_laws_anthropic` / `wikipedia_ja`（暫定代替） |
| Web フォールバック | 有効（`--no-web` 未指定）                                               |
| アクション         | 有効（`--no-action` 未指定）・既定ドライラン                            |
| 本人確認           | 不要（gov は `require_identity=False`）                                 |

**前提**: `ollama serve` 起動済み（LLM・**API キー不要**）／`.env` に `GOOGLE_API_KEY`（Embedding）／Qdrant 起動済み。 本トレースは gov の代表質問が
**内部 RAG で回答できた（answer）** 場合を主線とし、 別入力での分岐は §4 に整理する。

---

### B.1 全体フロー図（トレース経路）

設計書 §1 のフロー図のうち、本コマンドが **実際に通る経路を太線**で示す（`gov` in-scope → answer）。

```mermaid
flowchart TB
    Q(["uv run … --vertical gov<br>住民票の写しの取り方は？"])
    ANA["S0-(A): 入力・質問分析<br>looks_like_multi_question → analyze_questions<br>→ split_by_scope → reconstruct_query"]
    PROF["S1: 業界プロファイル適用<br>PROFILES[gov] → config へ配線"]
    CLS["S2: ① Plan 質問分類・計画<br>planner.create_plan()"]
    RAG["S3: ② Execute 内部RAG→reasoning<br>executor.execute()（allowed_collections 限定）"]
    GND["S4: ③ Confidence 支持率評価<br>GroundednessVerifier.verify()"]
    GATE{"S5: ④ 回答ゲート<br>_answer_gate() 0.8/0.5<br>＋強制エスカレ二段判定"}
    ANS["④ answer（出典つき）"]
    WEB["S6: ⑤ Web フォールバック<br>今回はスキップ"]
    NOINFO{"S7: ④' 情報なし検知<br>_detect_no_info_answer()"}
    ACT{"S8: ⑥ 要対応アクション？<br>_decide_action()"}
    OUT(["S9: ⑦ _render → SupportResult"])
    Q ==> ANA ==> PROF ==> CLS ==> RAG ==> GND ==> GATE
    GATE ==>|" 支持率>=0.8 かつ 出典>=1 "| ANS
    GATE -.->|" escalate なら "| WEB
    ANS ==> NOINFO
    NOINFO ==>|" answered=実質回答 "| ACT
    NOINFO -.->|" no_info なら escalate "| OUT
    ACT ==>|" action_map 不一致=不要 "| OUT
    ACT -.->|" 必要なら 本人確認→CONFIRM→実行 "| OUT
    WEB -.-> NOINFO
    classDef default fill:#000,stroke:#fff,color:#fff
    classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
    class Q,ANA,PROF,CLS,RAG,GND,GATE,ANS,WEB,NOINFO,ACT,OUT default
```

> 太線（`==>`）が本コマンドの実経路。点線（`-.->`）は今回は通らない分岐（§4 で読み替え）。
>
> 📝 **S0-(A) は今回のクエリでは第 1 段（`looks_like_multi_question`）に掛からず、LLM を呼ばずに素通りする。**
> ステップ自体は必ず通るので、フロー図には実線で描いている。

---

### B.2 ステップ別トレース（モジュール・コード・データ IN/OUT）

各ステップを **モジュール / コード（関数・行） / データ（IN・OUT）** の 3 点で示す。 各ステップはまず **使用例**（`# 使用例` の
Python ブロック＝そのステップの実際の呼び出し）を挙げ、 続く `text` ブロックを **IN（入力）→ Process（呼び出すクラス・関数と処理）→
OUT（出力＝Process の生成物）** の 3 段で読む。実装は `backend/app/core/support_agent.py` / `core/gates.py` にある。

### S0. 起動・引数解釈（API リクエスト → `run_support_agent_core`）

| 観点           | 内容                                                                                                                                                                                   |
|----------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **モジュール** | `backend/app/api/support.py` → `backend/app/core/jobs.py`                                                                                                                              |
| **コード**     | `submit_query(QueryRequest)` → `JobManager.start(JobParams)` → `run_support_agent_core(query, ..., vertical="gov", identity=None)`                                                     |
| **処理**       | 1. `QueryRequest` が `vertical="gov"` と `query` を受け取る<br>2. `identity` 未指定なので `identity=None`<br>3. **LLM 用の API キーのガードは無い**（ローカル LLM のため削除済み） |

> 📝 当時は CLI（`agent_support_example.py` の `main()` → argparse）が入口だった。
> CLI 削除後も S1 以降は同一である。

```python
# 使用例
run_support_agent_core("住民票の写しの取り方は？", vertical="gov")
```

```text
IN     : argv = ["--vertical", "gov", "住民票の写しの取り方は？"]
Process: main() … argparse.parse_args() で argv を解釈し、--identity 未指定→None、
         run_support_agent(...) を呼ぶ（LLM 用の鍵ガードは無い）
OUT    : run_support_agent(
             query="住民票の写しの取り方は？",
             verbose=False, use_web=True, do_action=True, dry_run=True,
             vertical="gov", identity=None)
```

### S0-(A). 入力・質問分析（複数質問の検知 → 選択 → 再構成）

> ⚠️ **旧版（v1.2）にはこの段が無かった。** 現行の `run_support_agent_core` は
> `SUPPORT_STEPS` の先頭に `"analyze"` を持ち、業界プロファイル適用より**前**に走る。

| 項目 | 内容 |
|------|------|
| **モジュール** | `backend/app/core/gates.py`（判定の実体）／`backend/app/core/support_agent.py`（配線） |
| **入力** | 生のユーザークエリ |
| **処理** | 1. `looks_like_multi_question()` が**第 1 段**（接続表現・疑問符の数）。不一致なら LLM を呼ばずに素通り<br>2. 一致したら `analyze_questions()` が**第 2 段**（軽量 LLM の 1 回呼び出しで「分解」と「担当範囲 IN/OUT」を同時に得る）<br>3. `split_by_scope()` が担当範囲内／外の添字へ分ける<br>4. 主質問を 1 つ選び、`reconstruct_query()` が「主質問 ＋ 関連質問」を**自然言語の 1 文へ再構成**<br>5. 範囲外や後回しにした質問は `ensure_out_of_scope_notice()` / `deferred_main_questions()` で応答に添える |
| **出力** | `reconstructed_query`（以降の ①〜⑥ はこれを入力に動く）＋ `QuestionAnalysis` / `QuestionCluster` |

**再構成する理由**（`reconstruct_query` の docstring より）:

1. **指示語を解決するため。** 「**その**手数料は？」は単体では何の手数料か不明で、ベクトル検索がまったく効かない。主質問の文脈を埋め込む必要がある
2. **別トピックのノイズを落とすため。** 原文をそのまま渡すと、採用しなかった主質問の文字列が残り、検索の意味の重心がボケる

> ⚠️ **安全側の倒し方**: `split_by_scope()` は判定器が無い・判定できない場合、そして
> **全件が範囲外と判定された場合も「全件範囲内」を返す。** 分類器が壊れている（すべて OUT を返す）
> のと本当に全部範囲外なのを区別できず、前者だと利用者の質問が丸ごと消えてしまうため。
> 本当に全部範囲外なら、生成側の `SCOPE_POLICY` が従来どおり断るので二重に守られている。

**設定**: `multi_question_enabled()` が有効判定。詳細設計は
[`docs/multi_question_handling.md`](../../docs/multi_question_handling.md)。
**テスト**: `backend/tests/test_multi_question.py` / `test_scope_and_models.py`。

**今回のトレース（`住民票の写しの取り方は？`）**: 疑問符 1 個・接続表現なしで第 1 段に掛からないため、
**LLM を呼ばずに素通り**し、`reconstructed_query` は原文のまま。

---

### S1. 業界プロファイル適用（gov）

| 観点           | 内容                                                                                                                                                                                                                                                                                                                                                                                                                                            |
|----------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **モジュール** | `backend/app/core/verticals.py`（`PROFILES`）＋ `grace.config`（`get_config`）                                                                                                                                                                                                                                                                                                                                                                 |
| **コード**     | `profile = PROFILES.get("gov")` → `config.qdrant.allowed_collections` / `config.llm.prompt_addendum` へ配線                                                                                                                                                                                                                                                                                                                                     |
| **処理**       | 1. `get_config()` で共通設定を取得し、planner/executor/verifier/tool_registry/intervention を生成<br>2. `create_intent_classifier(config)` / `create_no_info_judge(config)`（軽量モデルは `judge_model(config)` が解決）を用意（**この時点では呼ばない**。候補一致時のみ発火）<br>3. gov プロファイルで `notify_th=0.8 / confirm_th=0.5` に上書き<br>4. **検索スコープと方針をコア config へ書き込む**（tools は config 参照を保持するため実行時に効く） |

```python
# 使用例
config = get_config()
profile = PROFILES.get("gov")
config.qdrant.allowed_collections = list(profile.collections)
config.llm.prompt_addendum = profile.prompt_addendum
notify_th = profile.notify_th  # -> 0.8
```

```text
IN     : vertical="gov"
Process: run_support_agent() 内 … get_config() で config 取得、
         planner/executor/verifier/tool_registry/intervention を生成、
         PROFILES.get("gov") で profile を取得し、
         config.qdrant.allowed_collections / config.llm.prompt_addendum へ配線
OUT    : profile = VerticalProfile(name="自治体",
             collections=["gov_faq_anthropic","gov_laws_anthropic","wikipedia_ja"],
             escalate_keywords=["法的","訴訟","減免","個別","例外","不服"],
             action_map={"申請":"send_reply","手続":"send_reply","様式":"send_reply"},
             require_identity=False, notify_th=0.8, confirm_th=0.5,
             prompt_addendum="条例・公式案内に基づき、断定を避け、該当ページ・担当課を明示。個人情報は尋ねない。")
         config.qdrant.allowed_collections = [...gov 3 コレクション...]   # RAG 検索を限定
         config.llm.prompt_addendum        = "条例・公式案内に基づき…"      # reasoning へ注入
         notify_th=0.8 / confirm_th=0.5
```

**端末出力（抜粋）**:

```text
============================================================
業界プロファイル: 自治体（--vertical gov）
============================================================
  検索スコープ: gov_faq_anthropic, gov_laws_anthropic, wikipedia_ja（未登録コレクションは自動的に無視）
  しきい値: notify=0.8 / confirm=0.5 / 本人確認=False
  方針(reasoningへ注入): 条例・公式案内に基づき、断定を避け、該当ページ・担当課を明示。個人情報は尋ねない。
```

### S2. ① Plan（質問分類・計画）

| 観点           | 内容                                                                                                      |
|----------------|-----------------------------------------------------------------------------------------------------------|
| **モジュール** | `../../grace/planner.py`（`Planner.create_plan`）                                                         |
| **コード**     | `plan = planner.create_plan(query)`                                                                       |
| **処理**       | LLM がクエリの複雑度を推定し、`rag_search`（必要なら `reasoning`）ステップからなる `ExecutionPlan` を生成 |

```python
# 使用例
plan = planner.create_plan("住民票の写しの取り方は？")
print(len(plan.steps), plan.complexity)  # -> 2 0.35
```

```text
IN     : query="住民票の写しの取り方は？"
Process: Planner.create_plan(query) … LLM がクエリの複雑度を推定し、
         rag_search（必要なら reasoning）ステップからなる ExecutionPlan を生成
OUT    : plan = ExecutionPlan(
             steps=[ PlanStep(step_id=1, action="rag_search", ...),
                     PlanStep(step_id=2, action="reasoning", ...) ],
             complexity=<0.0-1.0>)
```

**端末出力**: `[plan] 2 ステップ (complexity=0.35)` のような 1 行。

### S3. ② Execute（内部 RAG → reasoning）

| 観点           | 内容                                                                                                                                                                                                                                                                                                                                                                                                                                   |
|----------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **モジュール** | `../../grace/executor.py`＋`../../grace/tools.py`（`RAGSearchTool` / `ReasoningTool`）                                                                                                                                                                                                                                                                                                                                                 |
| **コード**     | `result = executor.execute(plan)` → `internal_answer` / `internal_citations = _collect_citations(result.step_results)`                                                                                                                                                                                                                                                                                                                 |
| **処理**       | 1. `RAGSearchTool` が Qdrant を検索。**S1 で設定した `allowed_collections` により gov 3 コレクションへ限定**（`_apply_allowed_collections`。未登録は無視、1 つも無ければ制限なし）<br>2. スコア不足時は executor が `web_search` を**動的挿入**（その出典は `[Web]` ラベルになる）<br>3. `ReasoningTool._build_prompt()` が **S1 の `prompt_addendum` を「業務方針（遵守）」としてシステム指示直後に注入**し、根拠から日本語回答を生成 |

```python
# 使用例
result = executor.execute(plan)
internal_answer = result.final_answer or ""
internal_citations = _collect_citations(result.step_results)
```

```text
IN     : plan（②の計画）, config.qdrant.allowed_collections（gov 3 件）, config.llm.prompt_addendum（gov 方針）
Process: executor.execute(plan) … RAGSearchTool が Qdrant を allowed_collections で限定検索
         → （スコア不足なら web_search を動的挿入）→ ReasoningTool._build_prompt() が
         prompt_addendum を注入して回答生成。_collect_citations() で出典にラベル付与
OUT    : result = ExecutionResult(
             final_answer="住民票の写しは、お住まいの市区町村の窓口（市民課等）または"
                          "コンビニ交付・郵送で請求できます。本人確認書類が必要です。"
                          "詳しくは担当課の案内ページをご確認ください。",
             step_results=[StepResult(step_id=1, status="success", sources=["gov_faq_anthropic/住民票.md"]), ...],
             overall_confidence=<0.0-1.0>)
         internal_answer   = result.final_answer
         internal_citations = ["[社内] gov_faq_anthropic/住民票.md", ...]
         used_dynamic_web  = False   # [Web] ラベルが無い＝内部だけで回答
```

**端末出力（抜粋）**: `step1: success (sources=3)` / `step2: success (sources=0)`。

### S4. ③ Confidence（支持率評価）

| 観点           | 内容                                                                                                                                                                               |
|----------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **モジュール** | `../../grace/confidence.py`（`GroundednessVerifier.verify`）                                                                                                                       |
| **コード**     | `gres = verifier.verify(query, internal_answer, [_citation_text(c) for c in internal_citations])`                                                                                  |
| **処理**       | 回答を主張に分解し、各主張が出典に **supported / contradicted / neutral** のどれかを判定。支持率 = supported / (supported+contradicted)。出典が無い／LLM 失敗時は `verified=False` |

```python
# 使用例
gres = verifier.verify(query, internal_answer,
                       [_citation_text(c) for c in internal_citations])
print(gres.support_rate)  # -> 0.86
```

```text
IN     : query, internal_answer, sources=["gov_faq_anthropic/住民票.md", ...]（ラベル除去済み本文/識別子）
Process: GroundednessVerifier.verify(query, answer, sources) … 回答を主張に分解し、
         各主張を supported/contradicted/neutral に判定。支持率=supported/(supported+contradicted)
OUT    : gres = GroundednessResult(
             support_rate=0.86, supported=3, contradicted=0, total=4,
             has_contradiction=False, verified=True)
```

**端末出力**: `[groundedness] 支持率=0.86（判定可能 3/4 主張） / 出典数=3`。

### S5. ④ 回答ゲート＋強制エスカレ（二段判定）

| 観点           | 内容                                                                                                                                                                                                                                                                                                                                                                                                 |
|----------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **モジュール** | `backend/app/core/gates.py`（`_answer_gate` / `_should_force_escalate` / `_should_rescue_unaffirmed`）                                                                                                                                                                                                                                                                                          |
| **コード**     | `decision, warning = _answer_gate(support_rate, verified, citation_count, notify_th=0.8, confirm_th=0.5)` → `_should_force_escalate(query, profile, classify)`                                                                                                                                                                                                                                       |
| **処理**       | 1. **回答ゲート**: `verified=True` かつ 出典≥1 かつ 支持率0.86≥notify0.8 → `("answer", warning=False)`<br>2. **強制エスカレ（第 1 段）**: `_match_keyword(query, escalate_keywords)` — クエリに `法的/訴訟/減免/個別/例外/不服` は**含まれない** → 候補なし → **意図分類 LLM は呼ばれない（追加コスト 0）**<br>3. `_should_rescue_unaffirmed` は `decision != "escalate"` なので発火せず（救済不要） |

```python
# 使用例
decision, warning = _answer_gate(0.86, True, 3, notify_th=0.8, confirm_th=0.5)
forced, kw, intent = _should_force_escalate(query, profile, classify)
# -> decision="answer", warning=False, forced=False, kw=None, intent=None
```

```text
IN     : support_rate=0.86, verified=True, citation_count=3, notify_th=0.8, confirm_th=0.5
         query="住民票の写しの取り方は？", profile=gov
Process: _answer_gate(...) が 支持率0.86≥notify0.8 かつ 出典3≥1 → ("answer", False)。
         _should_force_escalate(query, gov, classify) が _match_keyword で候補なし→強制エスカレせず
         （classify=意図分類LLMは未実行）。_should_rescue_unaffirmed は escalate でないため不発。
         結果を SupportResult に集約
OUT    : (decision, warning) = ("answer", False)
         forced_escalate=False, matched_kw=None, intent=None   # エスカレ語なし → classify 未実行
         support = SupportResult(answer=..., citations=[3件], groundedness=0.86,
                                 groundedness_decided=3, decision="answer",
                                 warning=False, used_web=False, vertical="gov",
                                 overall_confidence=...)
```

> 別入力例: 「固定資産税の **減免**を **個別**に判断してほしい」なら第 1 段が `減免` に一致 →
> 第 2 段の意図分類が `request` → **強制エスカレ**（Web もスキップ）。詳細は §4。

### S6. ⑤ Web フォールバック（今回はスキップ）

| 観点           | 内容                                                                                                                    |
|----------------|-------------------------------------------------------------------------------------------------------------------------|
| **モジュール** | `../../grace/tools.py`（`web_search` / `reasoning`）＋ `SourceAgreementCalculator`                                      |
| **コード**     | `if decision == "escalate" and use_web and not forced_escalate:`                                                        |
| **処理**       | 条件は `decision == "escalate"`。今回は **`decision == "answer"` のため丸ごとスキップ**（Web 検索・相互検証は走らない） |

```python
# 使用例
if decision == "escalate" and use_web and not forced_escalate:
    web_res = tool_registry.execute("web_search", query=query)
    # …（今回は decision="answer" のため未実行）
```

```text
IN     : decision="answer", use_web=True, forced_escalate=False
Process: `if decision == "escalate" and use_web and not forced_escalate:` の条件評価。
         decision="answer" のため条件不成立 → ⑤ ブロック全体をスキップ
OUT    : （分岐に入らない。support は S5 のまま）
```

> `decision` が escalate だった場合のみ、内部が Web を使い済みなら **再検証のみ**（重複推論を省略、`web_reused=True`）、
> 未使用なら `web_search → reasoning → 相互検証` を実行する。

### S7. ④' 情報なし回答検知

| 観点           | 内容                                                                                                                                                                                                                                                                                        |
|----------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **モジュール** | `backend/app/core/gates.py`（`_detect_no_info_answer` / `create_no_info_judge`）                                                                                                                                                                                                       |
| **コード**     | `if support.decision == "answer" and support.answer:` → `_detect_no_info_answer(query, answer, no_info_judge, force_judge=web_only)`                                                                                                                                                        |
| **処理**       | 1. `web_only = 出典がすべて [Web]?` → 今回は `[社内]` 出典があるので **False**<br>2. 第 1 段: `NO_INFO_MARKERS`（「見当たりません」等）が回答に含まれるか → 含まれない → **候補なし**<br>3. `force_judge=False` かつ候補なし → **LLM 判定は呼ばれず** `no_info=False`（実質回答として維持） |

```python
# 使用例
web_only = bool(support.citations) and all(c.startswith("[Web]") for c in support.citations)
no_info, marker = _detect_no_info_answer(query, support.answer, no_info_judge, force_judge=web_only)
# -> no_info=False, marker=None（実質回答 → answer 維持）
```

```text
IN     : query, answer（住民票の取り方の実質回答）, force_judge=False, citations に [社内] を含む
Process: web_only = all(c.startswith("[Web]")) → False。
         _detect_no_info_answer() 第1段: _match_keyword(answer, NO_INFO_MARKERS) 不一致 →
         force_judge=False かつ候補なしのため no_info_judge（LLM）は未実行 → False
OUT    : (no_info, marker) = (False, None)   # 実質回答 → decision="answer" を維持
```

> 出典が Web のみ（社内根拠ゼロ）の回答は `force_judge=True` になり、候補句が無くても
> 軽量 LLM が「実質回答か／確認方法の案内だけか」を判定する（out-of-scope×動的 Web の answer 化対策）。

### S8. ⑥ Action（今回は起票なし）

| 観点           | 内容                                                                                                                                                                                                                                                                   |
|----------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **モジュール** | `backend/app/core/gates.py`（`_decide_action`）＋ `../../support_actions.py`（`_perform_action` 経由・今回未使用）                                                                                                                                                |
| **コード**     | `action = _decide_action(query, support.decision, profile, classify)`                                                                                                                                                                                                  |
| **処理**       | 1. `decision="answer"` なので有人エスカレは選ばれない<br>2. 第 1 段: `_match_keyword(query, profile.action_map=申請/手続/様式)` → 「住民票の写しの取り方」に**該当語なし** → 候補なし<br>3. `action = None` → **⑥ ブロックに入らない**（CONFIRM も本人確認も走らない） |

```python
# 使用例
action = _decide_action(query, support.decision, profile, classify)
# -> None（action_map に「取り方」の候補なし → 起票せず）
```

```text
IN     : query="住民票の写しの取り方は？", decision="answer", profile.action_map={申請,手続,様式→send_reply}
Process: _decide_action() … decision="answer" で有人エスカレは非選択、
         _match_keyword(query, action_map) が「取り方」に候補なし → classify 未実行 → None。
         action が None なので ⑥（本人確認→CONFIRM→backend.execute）には入らない
OUT    : action = None   # アクションなし
```

> 別入力例: 「保育園の **申請**様式がほしい」なら第 1 段が `申請` に一致 → 第 2 段 `request` →
> `send_reply` を **本人確認（gov は不要）→ CONFIRM 承認 → backend.execute (dry-run)** で擬似実行。§4 参照。

### S9. ⑦ 応答整形（SupportResult）

| 観点           | 内容                                                                                                                              |
|----------------|-----------------------------------------------------------------------------------------------------------------------------------|
| **モジュール** | `backend/app/core/support_agent.py`（`SupportResult` の確定）→ 表示は React 側（`frontend/`）。当時は CLI の `_render()` が print していた |
| **コード**     | `support.forced_escalate=False` / `support.intent=None` を確定 → `_render(support)` → `return support`                            |
| **処理**       | `decision="answer"` なので回答本文＋出典一覧＋根拠メタ行を表示。KPI 計測用メタ（vertical/intent/forced/no_info/web_reused）も付与 |

```python
# 使用例
support.forced_escalate = forced_escalate
support.intent = _intent_cache.get(query)
_render(support)
return support
```

```text
IN     : support（S5〜S7 で確定した SupportResult）
Process: support.forced_escalate / support.intent を確定した後、
         _render(support) が回答本文＋出典一覧＋根拠メタ行を整形表示し、
         run_support_agent() が support を return
OUT    : 端末表示 ＋ 呼び出し元へ SupportResult を返却
```

**端末出力（抜粋）**:

```text
============================================================
応答
============================================================
住民票の写しは、お住まいの市区町村の窓口（市民課等）または…（本文）

【出典】
  [1] [社内] gov_faq_anthropic/住民票.md
  [2] [社内] gov_faq_anthropic/窓口案内.md

[根拠] 支持率(groundedness)=0.86 / 全体信頼度=0.78 / decision=answer / web=不使用 / vertical=gov
```

---

### B.3 データの積み上がり（SupportResult 最終形）

同じ `SupportResult` インスタンスが各ステップで少しずつ埋まっていく。本コマンドの最終形:

| フィールド                 | 値                                  | 埋めたステップ             |
|----------------------------|-------------------------------------|----------------------------|
| `answer`                   | 住民票の取り方の回答本文            | S3（② Execute）            |
| `citations`                | `["[社内] gov_faq_anthropic/…", …]` | S3（`_collect_citations`） |
| `groundedness`             | `0.86`                              | S4（③ Confidence）         |
| `groundedness_decided`     | `3`                                 | S4                         |
| `decision`                 | `"answer"`                          | S5（④ ゲート）             |
| `warning`                  | `False`                             | S5                         |
| `used_web`                 | `False`                             | S3/S6                      |
| `web_reused`               | `False`                             | S6（未発火）               |
| `action` / `action_result` | `None` / `None`                     | S8（未発火）               |
| `vertical`                 | `"gov"`                             | S1                         |
| `intent`                   | `None`（分類器未発火）              | S5                         |
| `forced_escalate`          | `False`                             | S5                         |
| `identity_checked`         | `False`                             | S8                         |
| `no_info_detected`         | `False`                             | S7                         |
| `overall_confidence`       | executor 由来                       | S3                         |

> ポイント: gov の in-scope 質問では **軽量 LLM（意図分類・情報なし判定）が一度も呼ばれない**。
> 二段判定はいずれも「第 1 段の候補検出で不一致 → 第 2 段スキップ」で終わり、追加コストは 0。
> LLM 呼び出しは ① Plan・② reasoning・③ groundedness の主要 3 系統に限られる。

---

### B.4 分岐の読み替え（別入力ならどこが変わるか）

同じ gov プロファイルでも入力次第で経路が変わる。主な分岐を S 番号で対応づける。

| 入力例                                                 | 変わるステップ | 挙動                                                                                                                                                |
|--------------------------------------------------------|----------------|-----------------------------------------------------------------------------------------------------------------------------------------------------|
| 「固定資産税の**減免**を**個別**に判断してほしい」     | **S5**         | 第 1 段が `減免` に一致 → 第 2 段の意図分類が `request` → **強制エスカレ**（`decision="escalate"`・Web もスキップ・`forced_escalate=True`）         |
| 「住民税の**減免**制度の概要を教えて」（keyword-trap） | **S5**         | 第 1 段は `減免` に一致するが第 2 段が `question` → **誤検知抑止**して通常フロー継続 → answer                                                       |
| 「来年の税制改正の予測は？」（out-of-scope）           | **S6→S7**      | 内部根拠なし→ escalate→⑤ Web→ 実質回答風になっても **④' が将来予測×非確定情報を no_info と判定 → escalate**（`no_info_detected=True`）              |
| 「保育園の**申請**様式がほしい」                       | **S8**         | `_decide_action` 第 1 段が `申請` に一致 → 第 2 段 `request` → `send_reply` を **CONFIRM 承認 → backend(dry-run) で擬似実行**（gov は本人確認なし） |
| 内部支持率が 0.5〜0.8 のとき                           | **S5**         | `_answer_gate` が `("answer", warning=True)` → 「未確認の注意書き」つきで回答                                                                       |
| 内部が出典 0／verified=False                           | **S5→S6**      | ゲートが escalate → **⑤ Web フォールバック**で裏取り（成功なら answer、なお不足なら escalate）                                                      |

> EC（`--vertical ec`）の「返品したい」では S8 で `require_identity=True` により
> **本人確認 → CONFIRM → 実行** の順になる（`identity_checked=True`）。詳細は
> [`verticals_and_rulesets.md` §1.5](./verticals_and_rulesets.md)。

---


---

## 付録C: 依存関係図

```mermaid
flowchart LR
    FLOW["backend_flow（処理フロー (0)〜(8)）"]

    subgraph CORE["backend/app/core"]
        SA["support_agent.py<br>run_support_agent_core / _perform_action"]
        GA["gates.py<br>判定・出典整形の純関数群"]
        VE["verticals.py<br>PROFILES / ActionRequest"]
        IB["intervention_bridge.py<br>InterventionBridge"]
    end

    subgraph GRACEPKG["grace パッケージ"]
        PL["planner / executor + tools"]
        CF["confidence: GroundednessVerifier /<br>SourceAgreementCalculator"]
        IV["intervention: InterventionHandler"]
    end

    subgraph ROOT["リポジトリルート"]
        SUP["support_actions.py<br>ActionBackend / IdentityVerifier"]
    end

    FLOW --> SA
    SA --> GA
    SA --> VE
    SA --> PL
    SA --> CF
    SA --> IV
    SA --> SUP
    IV --> IB
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class FLOW,SA,GA,VE,IB,PL,CF,IV,SUP default
style CORE fill:#1a1a1a,stroke:#fff,color:#fff
style GRACEPKG fill:#1a1a1a,stroke:#fff,color:#fff
style ROOT fill:#1a1a1a,stroke:#fff,color:#fff
```

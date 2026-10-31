# core_process_flow.md - GRACE-Support / GRACE-Review とジョブ基盤の処理フロー

**Version 1.6** | 最終更新: 2026-10-31

> ⚠️ **本リポジトリは Ollama 版。** LLM はすべてローカルの Ollama（既定は `config.py::get_default_ollama_model()`）、Embedding だけ Gemini。
> 姉妹リポジトリ `grace_v2`（Anthropic 版）の同名ファイルとは中身が違うので、文書をコピーで持ち込まない（CLAUDE.md §5）。
>
> 📝 2026-10-12 に `backend/docs/` を廃止したとき、`support_flow.md`（1,843 行）・`review_flow.md`（1,202 行）・
> `job_runtime.md`（345 行）の**流れと設計判断**だけをここへまとめた。関数のシグネチャ・引数・戻り値は書かない
> （各モジュール文書が正本。§5）。消した文書の全文は git 履歴に残る。

---

## 目次

- [概要](#概要)
- [1. GRACE-Support（問い合わせ → 回答）](#1-grace-support問い合わせ--回答)
- [2. GRACE-Review（文書 → 指摘）](#2-grace-review文書--指摘)
- [3. ジョブ基盤（3 系統の共有）](#3-ジョブ基盤3-系統の共有)
- [4. 3 モードの対照とガードレール](#4-3-モードの対照とガードレール)
- [5. 関連ドキュメント](#5-関連ドキュメント)
- [6. 変更履歴](#6-変更履歴)

---

## 概要

`backend/app/core/` には 2 本のパイプラインと、それらとデータ準備を載せるジョブ基盤がある。
本書は `a_cross_doc_md_format.md` §1.3 の処理フローの章立て（全体フロー → ステップ詳細 → 分岐・例外）を、
**パイプラインごとに 1 章ずつ**繰り返す形にしている（1 ディレクトリに流れが 3 つあるため）。

| 項目 | 内容 |
|---|---|
| 種別 | A（横断・設計） |
| 対象 | `support_agent.py` / `gates.py` / `verticals.py`（Support）、`review_agent.py` / `review_gates.py` / `rulesets.py` / `review_facts.py`（Review）、`jobs.py` / `intervention_bridge.py` / `job_logs.py`（基盤） |
| 入口 | Web API だけ（CLI は無い。CLAUDE.md §1）。`POST /api/support/query` / `POST /api/review/submit` → `JobManager.start()` |

### 主な責務

- GRACE-Support の 1 周（0-(A)〜⑥）の順序・分岐と、なぜその判定にしたかを示す
- GRACE-Review の 1 周（S1・①〜⑦）の順序・分岐と、Support の機構をどう再利用したかを示す
- 3 系統（Support / Review / データ準備）が共有するジョブ・SSE・HITL の流れと、失敗時の扱いを示す
- 3 モード（基本版 / Support / Review）の対照と、ガードレールごとの失敗時の既定を示す
- 各ステップの実装の所在（モジュール文書）へ案内する

### 各責務対応のモジュール

| # | 責務 | 対応モジュール | 説明 |
|---|---|---|---|
| 1 | Support の流れ | `support_agent.py::run_support_agent_core` / `gates.py` / `verticals.py` | §1 |
| 2 | Review の流れ | `review_agent.py::run_review_agent_core` / `review_gates.py` / `rulesets.py` | §2 |
| 3 | ジョブ基盤 | `jobs.py::JobManager` / `intervention_bridge.py::InterventionBridge` / `job_logs.py::capture_logs` | §3 |
| 4 | 3 モードの対照・ガードレール | `gates.py` / `review_gates.py` / `grace/confidence.py` / `grace/tools.py::ReasoningTool` | §4 |
| 5 | 案内 | 各モジュール文書（`support_agent.md` ほか） | §5 |

### アーキテクチャ構成図

```mermaid
flowchart TB
    subgraph CALLER["呼び出し側"]
        API["api/support.py / api/review.py / api/data.py<br>POST → 202 で job_id を返す"]
        FE["frontend<br>SSE 購読・承認モーダル"]
    end
    subgraph CORE["backend/app/core（本書）"]
        JM["jobs.py<br>JobManager（runner 注入・イベント蓄積）"]
        SA["support_agent.py<br>0-(A)〜⑥"]
        RA["review_agent.py<br>S1・①〜⑦"]
        DJ["data_jobs.py<br>データ準備 4 種"]
        BR["intervention_bridge.py<br>HITL の承認待ち"]
    end
    subgraph EXT["外部"]
        GR["grace/<br>planner / executor / confidence"]
        ACT["support_actions.py<br>ActionBackend（Support / Review 共用）"]
        LLM["Ollama（ローカル LLM）"]
        QD["Qdrant + Gemini Embedding"]
    end
    API --> JM
    FE -->|"SSE / POST confirm"| API
    JM --> SA
    JM --> RA
    JM --> DJ
    SA --> BR
    RA --> BR
    SA --> GR
    RA --> GR
    SA --> ACT
    RA --> ACT
    GR --> LLM
    GR --> QD
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class API,FE,JM,SA,RA,DJ,BR,GR,ACT,LLM,QD default
style CALLER fill:#1a1a1a,stroke:#fff,color:#fff
style CORE fill:#1a1a1a,stroke:#fff,color:#fff
style EXT fill:#1a1a1a,stroke:#fff,color:#fff
```

---

## 1. GRACE-Support（問い合わせ → 回答）

### 1.1 全体フロー

`STEP_IDS`（`support_agent.py`）の並びが実行順で、画面のタイムラインと 1 対 1 に対応する。
番号は呼称で、**④' は ⑤ の後**に走る。

```mermaid
flowchart TB
    Q(["問い合わせ"]) --> A["0-(A) analyze<br>質問の整理（LLM・毎回）→ 話題の選択・担当範囲"]
    A --> B["0-(B) profile<br>業界プロファイル（基本版は skipped）"]
    B --> P["① plan<br>planner.create_plan_for_topic"]
    P --> E["② execute<br>観点ごとの内部 RAG → reasoning"]
    E --> C["③ confidence<br>GroundednessVerifier → 支持率"]
    C --> G{"④ gate<br>支持率 × 出典数<br>＋強制エスカレ＋救済"}
    G -->|"answer"| N
    G -->|"escalate（強制でない・use_web）"| W["⑤ web<br>Web で裏取り・相互検証"]
    W --> N{"④' no_info<br>情報なし回答？（answer のときだけ）"}
    N -->|"いいえ"| ACT["⑥ action<br>本人確認 → HITL CONFIRM → 実行"]
    N -->|"はい → escalate"| ACT
    ACT --> R(["SupportResult"])
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class Q,A,B,P,E,C,G,W,N,ACT,R default
```

### 1.2 ステップ詳細

| # | `STEP_IDS` | 実装 | 入力 → 出力 | 失敗・判定不能時の既定 |
|---|---|---|---|---|
| 0-(A) | `analyze` | `question_refiner.py::refine_question`（毎回 LLM 1 回）→ `gates.py::split_by_scope` / `deferred_main_questions` | 原文 → 話題（回答用の質問文・観点ごとの社内検索の文と Web 検索の語）・保留質問・範囲外質問 | **元の文のまま 1 話題・1 観点**（`original_as_topic`）で 1 周 |
| 0-(B) | `profile` | `PROFILES.get(vertical)`（`verticals.py`） | `vertical` → 検索スコープ・しきい値・業務方針 | 基本版（`vertical=None`）は skipped |
| ① | `plan` | `grace.planner.Planner.create_plan_for_topic` | 採用した話題 → `ExecutionPlan`（観点ごとに `rag_search`・最新情報を求めた観点は `web_search`（`required`）・最後に `reasoning`） | — |
| ② | `execute` | `grace.executor.execute(plan, allow_react=False)` ＋ `gates.py::_collect_citations` | 計画 → 回答・出典（`[社内] …` / `[Web] …`）・Web 検索とその理由（`web_searches`） | RAG スコアが `reasoning_min_rag_score`（0.64）未満の結果は根拠に採らない |
| ③ | `confidence` | `GroundednessVerifier.verify` | 回答 × 出典本文 → 支持率（supported / contradicted / neutral） | 全 neutral は `decided=0`（母数から除外） |
| ④ | `gate` | `gates.py::_answer_gate` ＋ `_should_force_escalate` ＋ `_should_rescue_unaffirmed` | 支持率・出典数 → `answer` / `escalate`・`warning` | 未検証・出典 0 は escalate |
| ⑤ | `web` | `support_agent.py` 内 ＋ `_web_citations` / `_merge_citations` / `_pick_groundedness` | escalate の回答 → Web 回答と統合出典 | 結果なしは `used_web=True` だけ記録 |
| ④' | `no_info` | `gates.py::_detect_no_info_answer` ＋ `create_no_info_judge` | answer の回答 → 情報なしなら escalate | 判定できないなら escalate（答えない方が安全） |
| ⑥ | `action` | `gates.py::_decide_action` → `_perform_action`（本人確認 → `InterventionBridge` → `ActionBackend`） | 判定・質問 → アクションの結果文 | 本人確認なし・承認タイムアウトは**実行せず有人へ** |

`support_rate = supported / (supported + contradicted)`。neutral を分母から除くのは、
Q&A 形式の出典では答えていない内容が neutral になり、母数に入れると減点されるため。

### 1.3 回答ゲート（④）と救済・強制エスカレ

| 状態 | 条件 | `decision` / `warning` | 振る舞い |
|---|---|---|---|
| 高信頼 | 支持率 ≥ `notify_th` かつ出典 ≥ 1 | `answer` / `False` | 出典つきで回答 |
| 中信頼 | `confirm_th` ≤ 支持率 < `notify_th` | `answer` / `True` | 回答＋「未確認」の注意書き（黙って精度を落とさない） |
| 低信頼 | それ未満・未検証・出典 0 | `escalate` | ⑤ Web へ。なお不足なら有人対応 |

- しきい値は `config/grace_config.yml` の `confidence.thresholds`（notify 0.7 / confirm 0.4）。プロファイルが上書きでき、
  `gov` は 0.8 / 0.5（正確性優先）。
- **強制エスカレ**（二段判定）: 第 1 段でプロファイルの `escalate_keywords` に部分一致 → 第 2 段で意図分類
  （`create_intent_classifier`）。`question`（FAQ）なら誤検知として発動しない。`request` / `incident` / 分類失敗は安全側で発動する。
  照合する文は、0-(A) の LLM が書き直した回答用の質問文ではなく、利用者が書いた**原文**（`run_support_agent_core` の `original_query`）。言い換えで原文の語が消える・原文に無い語が入ることがあるため（2026-10-31〜。`tests/test_keyword_gates_use_original_text.py`）。⑥ の `action_map` の照合も同じ。
- **④-救済**: escalate でも、強制でなく・矛盾なし・出典あり・実質的な回答なら `answer`（`warning=True`）に戻す。
  放置すると ⑤ の作り直しで「情報なし」回答に化け、④' で誤エスカレしていた（ec の返金ポリシーで実測）。
- `decision` は `answer` / `escalate` の **2 値**。中信頼は「答えない」ではなく `warning` で表す（判定軸を 1 本に保つ）。

### 1.4 Web フォールバック（⑤）と情報なし検知（④'）

- ⑤ は **escalate かつ強制エスカレでなく `use_web=True`** のときだけ走る。採用した話題の**観点ごとの `web_query`**（0-(A) が作った
  Web 検索の語。同じ語は 1 回だけ）で検索し、結果をつなぐ。② で executor が Web 検索を使っていれば、
  回答を作り直さず**内部回答の再検証だけ**行う（`web_reused`。重複推論の省略）。
- ② で executor が Web 検索したときは、その**理由**（社内検索のスコア不足・LLM が質問に合わないと判定・利用者が最新情報を求めた観点・
  社内検索の失敗）を `ExecutionResult.web_searches` から画面のログへそのまま出す（2026-10-30 まではスコア不足と決め打ちで出していた）。
- `use_web=False` は「**内部 RAG のみ**」。⑤ だけでなく executor の Web 検索（5 経路）も `Executor._web_search_allowed` で止める
  （`tests/test_web_search_toggle.py`）。
- ④' は ⑤ の後、`decision == "answer"` のときだけ。第 1 段は `NO_INFO_MARKERS`（「見当たりません」等）の照合、
  第 2 段は軽量モデルの実質回答判定。**出典が Web だけのとき**は候補句が無くても第 2 段を必ず通す（範囲外の質問 × 動的 Web 検索の対策）。

### 1.5 Action（⑥）

| 判定 | アクション | 承認 |
|---|---|---|
| escalate | `escalate_to_human` | **不要**（引き継ぎ自体が安全側。承認待ちで宙に浮かせない） |
| answer ＋ `action_map` の語に一致 ＋ 意図が `request` / `incident` | `create_ticket` / `send_reply` | **必要**（HITL CONFIRM） |
| answer ＋ 意図が `question` | なし | — |

- `require_identity=True` のプロファイル（`ec`）は、実行前に `IdentityVerifier` で識別子を照合する。未確認なら実行せず有人へ。
- 既定は **dry-run**（`DryRunActionBackend`。記録だけ）。実 API 連携（`WebhookActionBackend`）は `dry_run=False` で `SUPPORT_ACTION_WEBHOOK_URL` を置いたときだけで、
  URL が無ければ疑似実行（`PseudoActionBackend`。ログだけ）。
- **本リポジトリは副作用の無いバックエンド（dry-run）では承認を省く**（`has_side_effects` が偽なら CONFIRM を出さない。`_perform_action`）。
  承認待ちを見たいときは `dry_run=False` にする（Webhook を置かなければ実行は疑似のまま）。
- 副作用は ⑥ がバックエンドを**直接呼ぶ**。`grace/tools.py` のツールにしない（executor が計画の都合で任意の位置に差し込めてしまうため）。

### 1.6 質問の整理・複数質問と担当範囲外（0-(A)）

**前処理であってゲートではない。** 毎回 `question_refiner.refine_question()` が軽量 LLM を 1 回呼び、問い合わせを
**話題**（対象が同じ文のまとまり。話題ごとに回答 1 つ）→ **観点**（知りたい側面。観点ごとに社内検索の文と Web 検索の語）に整理する。
採用した話題を ① `create_plan_for_topic` に渡すだけで、③ 以降の判定は変えない。詳細は [`question_refiner.md`](question_refiner.md)。

> ⚠️ **2026-10-29 までは文字列一致で LLM を呼ぶかを決めていた**（接続語 10 個の部分一致か「？」2 個以上）。それ以外は問い合わせの文を
> そのまま社内検索・Web 検索・回答生成に渡したので、「Jed York CEO が…経緯は？ / その件の経緯を詳しく / 最新ニュースを検索して、
> 49ers の新 GM とヘッドコーチの人事を」の 3 行は分解されず、指示語も解決されず、社内の記事に届かなかった。

| 入力 | 選択 | 結果 |
|---|---|---|
| 話題 1 つ（観点 1〜3） | 出さない | 観点ごとに社内検索して 1 周（指示語は `answer_question` に解決済み） |
| 範囲内 1 ＋ 範囲外 N-1 | 出さない | 範囲内に回答＋範囲外は断りと窓口案内 |
| 範囲内が複数 | 出す（`multi_question_selection`） | 選んだ話題で 1 周＋**保留質問を必ず返す** |
| 選択がタイムアウト / 拒否 | 出す | 元の文のまま 1 周（escalate にしない） |
| 選択肢に無い値が返った | 出す | 先頭の話題を採用し、残りは保留 |
| 整理の失敗（LLM の例外・JSON でない・検査の不合格） | 出さない | 元の文のまま 1 話題・1 観点（`RefineResult.failed`。理由をログに出す） |

- 🔴 **保留質問は必ず利用者へ出す。** 出さないと「片方の質問が黙って落ちたのに、支持率が高いので高信頼として提示される」。
  複数質問対応で最も危険とした事故がこれで、`deferred_main_questions` の戻り値と `AnswerCard` の表示が防いでいる。
- **保留**（`deferred_questions`：範囲内だが今回は答えていない）と**範囲外**（`out_of_scope_questions`：この窓口では答えられない）を混ぜない。利用者が次にすることが違う。
- 範囲外の判定は整理と同じ LLM 呼び出しで行う（`Topic.in_scope`。プロファイルの `scope_description` を渡したときだけ）。
  **判定できなければ範囲内**（`null` は範囲内。全件 OUT も判定の誤りとみなして `split_by_scope` が範囲内へ倒す）。
- 範囲外の質問は**検索クエリから外し、生成側には渡す**（`VerticalProfile.build_closing_instruction()` を回答の構成ルールの**後ろ**に置く。前に置くと構成ルールに負けて断りが落ちた）。
  モデルが断らなかったときは `gates.ensure_out_of_scope_notice()` が**ゲートの後で**追記し、案内先の URL（`out_of_scope_links`。実在する公的機関だけ）が欠けていれば URL だけを補う。
- LLM の出力はそのまま信用しない。書き写した文が問い合わせに由来するか（`gates._derives_from_query`）を検査し、`web_query` からは問い合わせに無い語を外す（`restrict_web_query`）。

### 1.7 基本版（`vertical=None`）

画面の「基本版」は別実装ではなく、同じ `run_support_agent_core` を `vertical=None` で呼ぶ。
プロファイル由来の機構（検索スコープ・しきい値・業務方針・断りの指示・担当範囲・強制エスカレ・本人確認・Web 優先ドメイン）は
すべて「無し」に倒れ、0-(B) は skipped になる。**パイプラインの実力をプロファイル抜きで見る基準線**として残している。
ガードレールが薄い（範囲外の質問にも答えようとする）ので、業務では GRACE-Support を選ぶ。3 モードの対照は §4。

| 項目 | 基本版（`vertical=None`） | GRACE-Support（`vertical` 指定） |
|---|---|---|
| 0-(B) `profile` | **skipped** | 実行（検索スコープ・しきい値・方針をログへ） |
| 検索スコープ | `allowed_collections = []`（**全コレクション**） | `profile.collections` に限定 |
| しきい値 | 既定（`confidence.thresholds`） | プロファイルで上書き（`gov` は 0.8 / 0.5） |
| 業務方針・断りの指示 | `prompt_addendum` / `prompt_closing` とも `""` | `build_prompt_addendum()` / `build_closing_instruction(...)` |
| 担当範囲の判定（GA'） | **行わない**（全質問が範囲内） | `scope_description` で範囲外を切り分け、窓口案内で断る |
| 強制エスカレ（G3） | 発火しない（`escalate_keywords` が無い） | プロファイルのキーワードで発火 |
| 本人確認（G8） | 行わない | `require_identity=True` のプロファイル（`ec`）で実行 |
| Web 優先ドメイン | 無し | `profile.preferred_domains`（加点のみ） |

### 1.8 評価指標

| 指標 | 定義 | 目標 |
|---|---|---|
| 自己解決率 | 有人に回さず解決した割合 | 高いほど良い |
| 出典付与率 | 回答に出典が付いた割合 | ほぼ 100% |
| 根拠なし回答率 | 出典・根拠なしで断定した割合 | 0 に近いほど良い |
| エスカレーション適合率 | escalate が妥当だった割合 | 高いほど良い |
| 平均応答時間 | 問い合わせ → 回答 | 低いほど良い |

LLM はローカル実行なので課金されるのは Embedding（Gemini）だけで、効いてくるのは**時間**である。ローカルでは往復 1 回の差が体感に直結するため、
0-(A) の分解と担当範囲の判定は 1 回の LLM 呼び出しに畳んである（以前は 2 回で、実測 16.3 秒＋2.2 秒かかっていた。2026-08-30）。

---

## 2. GRACE-Review（文書 → 指摘）

### 2.1 設計方針

GRACE-Review は文書（EC の LP・商品説明文）を規程（景表法・特商法・薬機法・社内方針）に照らし、**根拠条文つきの指摘**を返す。
入出力の型は Support と違うが、中核の機構は再利用している。

| GRACE-Support | GRACE-Review | 再利用する実装 |
|---|---|---|
| ③ 回答の裏付け | ④ Ground（**指摘の根拠**が規程にあるか） | `GroundednessVerifier`（無改造） |
| ④ 回答ゲート | ④' 指摘ゲート（confirmed / review_required / suppressed） | `_answer_gate` と同型の `decide_finding_status` |
| ④-救済 | ④-救済（矛盾なしの指摘は棄却せず保留へ） | `_should_rescue_unaffirmed` と同型 |
| エスカレ語 → 強制エスカレ | **重大リスク語** → 強制 high | `_should_force_escalate` と同型（二段判定） |
| ⑤ Web フォールバック | ⑥ Web 裏取り（**信頼度を下げる方向にだけ**使う） | `tool_registry.execute("web_search")` |
| ⑥ Action | ⑦ Action（レポート → HITL → 起票 / 引き継ぎ） | `_perform_action` / `ActionBackend`（無改造） |
| `VerticalProfile` | `RuleSet`（1 プロファイル = N ルール） | 構造を踏襲（型は分ける） |

3 原則: (1) **既存機構を無改造で使う**（変更が要るなら設計を疑う）、(2) **過検知を抑えることを最優先**
（指摘が多すぎると読まれない）、(3) **Support を壊さない**（共用部品を触ったら `tests/test_review_*.py` も流す）。
新規に作ったのは Segment / Detect / Severity の 3 つだけである。

### 2.2 全体フロー

`REVIEW_STEP_IDS` の並びが実行順。**⑥ Web は ⑤ Severity より先**に走る（番号は Support との対応を示す呼称）。

```mermaid
flowchart TB
    D(["文書（50,000 字まで）"]) --> S1["S1 ruleset<br>RuleSet を解決・スコープとしきい値を config コピーへ"]
    S1 --> SEG["① segment<br>段落・箇条書き・見出しへ決定的に分割（原文オフセット保持）"]
    SEG --> RET["② retrieve<br>ルールごとに 1 回、規程を RAG 検索"]
    RET --> DET{"③ detect<br>第 1 段: 常時チェック＋キーワード一致"}
    DET -->|"候補なし"| SKIP["その単位は LLM を呼ばない"]
    DET -->|"候補あり"| DET2["③ 第 2 段: LLM が違反か判定＋指摘文・修正案"]
    DET2 --> GND["④ ground<br>指摘文 × 規程 → 支持率"]
    GND --> SUP{"④' suppress<br>指摘ゲート＋救済"}
    SUP -->|"suppressed"| DROP["一覧から外す（件数だけ数える）"]
    SUP -->|"confirmed / review_required"| WEB["⑥ web<br>web_check のルールだけ法改正を確認"]
    SKIP --> WEB
    WEB --> SEV["⑤ severity<br>既定の重大度を確信度で調整＋重大リスク語で強制 high"]
    SEV --> ACT["⑦ action<br>レポート → HITL CONFIRM → 実行"]
    ACT --> R(["ReviewResult"])
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class D,S1,SEG,RET,DET,SKIP,DET2,GND,SUP,DROP,WEB,SEV,ACT,R default
```

### 2.3 ステップ詳細

| # | `REVIEW_STEP_IDS` | 実装 | 入力 → 出力 | 失敗・判定不能時の既定 |
|---|---|---|---|---|
| （0） | — | `run_review_agent_core` の冒頭 | 設定を**ディープコピー**（LLM はローカルで API キーが無いので事前チェックはしない） | Ollama・Embedding に届かなくても例外にせず、全ルールを「自動判定に失敗したため要確認」で返す |
| S1 | `ruleset` | `rulesets.py::get_ruleset` | ID → スコープ・しきい値（既定 0.85 / 0.60）・重大リスク語 | 未知の ID は skipped |
| ① | `segment` | `review_agent.py` の分割関数 | 文書 → `Segment`（`start` / `end` は原文基準） | 400 字超は文末で再分割。200 個で打ち切り（`truncated`） |
| ② | `retrieve` | `_retrieve_evidence`（`rag_search`・5 件） | ルールの `retrieval_query()` → 規程の出典と本文 | 例外・空は根拠なしで続行（要旨 `public_description()` が根拠の代わり） |
| ③ | `detect` | `review_gates.py::select_candidate_rules` → `create_violation_detector` | 判定単位 × 候補ルール → 違反の判定・指摘文・修正案 | LLM 呼び出しが 300 回で打ち切り（`truncated`） |
| ④ | `ground` | `GroundednessVerifier`（Support と同じ）＋ `damp_support_rate` | 指摘文 × 規程 → 支持率 | 判定できない主張が多いほど減衰 |
| ④' | `suppress` | `decide_finding_status` ＋ `should_rescue_finding` ＋ `detect_vacuous_finding` | 支持率・出典数 → 3 状態 | 未検証・根拠ゼロは **`review_required`**（指摘を消さない） |
| ⑥ | `web` | ルール単位で 1 回（`"{law} {article} 改正 ガイドライン"`） | `web_check` のルール → `web_checked` | 既定 OFF。例外・待ちの打ち切りは握りつぶして続行 |
| ⑤ | `severity` | `adjust_severity` ＋ `should_force_high` ＋ `apply_forced_high` | 既定の重大度・確信度・重大リスク語 → 最終の重大度 | 言及種別の分類に失敗したら安全側（強制する） |
| ⑦ | `action` | `_decide_review_action` → `_perform_action`（Support と同じ関数） | 指摘 → レポート（Markdown）→ 実行 | 本人確認は不要（`identity_verifier=None`） |

### 2.4 分岐の要点

- **判定単位**: 常時チェックのルール（`always_check=True`。表記漏れを見る 7 件）は**文書全体**を 1 単位として判定する。
  それ以外はセグメントごとに、キーワード（除外語 `keyword_excludes` を引いて）が当たったルールだけを第 2 段へ送る。
- **② はルール自身の文で引く。** 広告の文をクエリにするとスコアが下限 0.70 を割り、登録した条文が根拠に届かなかった（2026-10-01 に是正）。
  クエリがルールだけで決まるので、同じルールが複数の単位に出ても検索は 1 回。採用は `evidence_min_score`（0.70）以上かつ
  最上位 × `evidence_top_ratio`（0.92）以上（他ルールの条文が混ざって指摘文が越境するのを防ぐ）。`policy-01` は
  `evidence_query` / `evidence_collections` で自社規程のコレクションだけを引く（詳細と登録手順は [`rulesets.md`](rulesets.md) §6.4）。
- **② は並列 4、③④ は Ollama の同時処理数**（`OLLAMA_NUM_PARALLEL`、未設定なら 1＝直列）。ローカルの Ollama は既定で 1 本ずつしか処理しないので、
  多く投げてもキューで待つだけで速くならず、待ち時間が各呼び出しのタイムアウトを食う。`GRACE_REVIEW_WORKERS` は両方を上書きする（0 以下は直列、数値でなければ既定）。
- **④' 指摘ゲート**: 支持率 ≥ `notify_th` → `confirmed`、≥ `confirm_th` → `review_required`、それ未満 → `suppressed`。
  `suppressed` でも矛盾なし・根拠あり・実質的なら `review_required` に救済する。`confirm_needs_human=True` のルール
  （広告文だけでは決まらないもの。現在 keihyo-07）は `confirmed` にしない。
- **文字列で決まる事実**（送料の表示・返品条件の比較）は `review_facts.py` が LLM の判定を補う。
- **⑥ Web は新しい指摘を作らない。** 信頼度を下げる方向にだけ使う。
- **⑤ 強制 high**: `critical_keywords` に一致 → 言及種別を分類（打消し・引用などの誤検知を除く）→ high と `review_required` へ引き上げる。
- **⑦**: high が 1 件でもあれば `escalate_to_human`（承認不要）、それ以外は `create_ticket`（要承認）、指摘ゼロならアクションなし。

### 2.5 ガード上限

| 定数 | 値 | 到達時 |
|---|---:|---|
| `schemas.MAX_DOCUMENT_CHARS` | 50,000 | 入力段で 422 |
| `MAX_SEGMENTS` | 200 | 以降を捨てて `truncated=True` |
| `MAX_SEGMENT_CHARS` | 400 | 文末で再分割（打ち切りではない） |
| `MAX_LLM_CALLS` | 300 | ③ を打ち切って `truncated=True` |
| `RETRIEVE_LIMIT` | 5 | ② の取得件数 |

> ⚠️ **上限は外さない。** 200 セグメント × 23 ルールを無条件に第 2 段へ送ると 4,600 回の LLM 呼び出しになる。

### 2.6 データモデル

| 型 | 主な項目 |
|---|---|
| `Segment` | `segment_id` / `text` / `start` / `end` / `kind`（`paragraph` / `list_item` / `heading` / `document`） |
| `ReviewFinding` | 原文の位置（`segment_id` / `excerpt` / `start` / `end`）、ルール（`rule_id` / `law` / `article`）、`message` / `suggestion`、`severity` / `confidence` / `citations`、`status` / `forced` / `web_checked` |
| `FindingSummary` | 重大度別・状態別の件数（`suppressed` は件数だけで、`findings` には入らない） |
| `ReviewResult` | 上記に加えて `used_web` / `action` / `action_result` と計測値（`rules_evaluated` / `detected_raw` / `rescued` / `forced_high` / `truncated`） |

項目の正本は [`review_agent.md`](review_agent.md)、API の形は [`../../docs/schemas.md`](../../docs/schemas.md)。

### 2.7 前提と未決事項

| 論点 | 現在の扱い |
|---|---|
| 規程データ | `ec_ad_rules_anthropic` は grace_v2 と**共用の Qdrant** にあり、条文の雛形（`qa_output/ec_ad_rules_statutes_template.csv`）は grace_v2 にだけ置く（CLAUDE.md §5・[`docs/port_from_grace_v2_todo.md`](../../../../docs/port_from_grace_v2_todo.md) §12） |
| 入力方式 | テキストの貼り付けだけ（ファイルの取り込みは無い） |
| Web 裏取りの既定 | OFF（条文が一次情報で、速度とコストに見合わない） |
| 本人確認 | 不要（`identity_verifier=None` 固定） |
| 法務監修 | **本ルールセットは技術検証用のサンプル**。実運用には監修が要る |

---

## 3. ジョブ基盤（3 系統の共有）

### 3.1 ジョブのライフサイクルとリプレイ

```mermaid
flowchart TB
    Post["POST /api/*（202 で job_id を返す）"] --> Start["JobManager.start(params)"]
    Start --> Res["params の型から runner と kind を解決"]
    Res --> Br["Job を作り InterventionBridge(emit=job.emit) を付ける"]
    Br --> Th["daemon スレッドで runner(params, emit, confirm)"]
    Th --> Ok["dict を返す → completed"]
    Th --> Ng["None を返す → failed（error は runner が出す）"]
    Th --> Ex["例外 → error を出して failed"]
    Ok --> Gc["次の start() で完了 50 件超を古い順に破棄"]
    Ng --> Gc
    Ex --> Gc
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class Post,Start,Res,Br,Th,Ok,Ng,Ex,Gc default
```

- `emit()` はイベントに `seq`（連番）と `ts` を付けて `Job.events` に**捨てずに**積む。SSE は先頭から流すので、
  POST と購読のあいだのイベントも、再接続でも取りこぼさない。
- 15 秒イベントが無ければ keepalive（`SSE_KEEPALIVE` = **名前付きイベント**）を送る。フロントは keepalive も含めて 60 秒無音なら
  張り直し、リプレイ分を `seq` で読み飛ばす（`frontend/src/state/streamWatch.ts`）。keepalive をコメント行に戻さないこと。
- イベントは `step` / `log` / `intervention` / `result` / `error` の 5 種。SSE の終端 `done` は別物で、`ts` と `started_at` を必ず載せる
  （画面の所要時間がこれを使う）。ワイヤ形式は [`../../api/docs/README_backend_api.md`](../../api/docs/README_backend_api.md) §4.1。

### 3.2 runner 注入と HITL の橋渡し

| params の型 | kind | 登録する場所 |
|---|---|---|
| `JobParams` | `support` | `jobs.py` の末尾 |
| `ReviewParams` | `review` | `review_agent.py` の import 時 |
| `ChunkingParams` / `QaGenerationParams` / `RegisterParams` / `DeleteParams` | `chunking` / `qa` / `register` / `delete` | `data_jobs.py` の import 時 |

- `register_runner()` は import の副作用だが漏れない（params を作るにはそのモジュールを import する必要がある）。
  これで `jobs.py` は Review もデータ準備も知らずに済み、循環 import も起きない。未登録の型は `TypeError`。
- `InterventionBridge` は、同期コールバック（`InterventionHandler.on_confirm`）と別の HTTP リクエストで届く承認を `threading.Event` でつなぐ。

```mermaid
%%{ init: { "theme": "base", "themeVariables": {
  "background": "#000000", "mainBkg": "#000000",
  "textColor": "#ffffff", "lineColor": "#ffffff",
  "actorBkg": "#000000", "actorTextColor": "#ffffff",
  "actorLineColor": "#ffffff", "noteBkgColor": "#000000",
  "noteTextColor": "#ffffff", "noteBorderColor": "#ffffff" } } }%%
sequenceDiagram
    participant W as "ワーカースレッド"
    participant B as "InterventionBridge"
    participant S as "SSE 購読者（画面）"
    participant A as "POST /confirm/{job_id}"
    W->>B: resolver(InterventionRequest)
    B->>S: intervention（waiting・intervention_id）
    B->>B: Event.wait(timeout)
    A->>B: resolve(intervention_id, approve)
    B->>S: intervention（resolved）
    B-->>W: PROCEED / CANCEL
    Note over B,W: 時間切れは CANCEL（timeout_reached）＝実行しない
```

- 待ち時間は Bridge 生成時の指定 → `request.timeout_seconds` → `DEFAULT_CONFIRM_TIMEOUT`（300 秒）の順。
  **「返事が無い＝承認」には絶対にしない。** Web 経路に自動承認は持ち込まない。
- 同時に待てるのは 1 ジョブ 1 件。`intervention_id` が違えば `not_waiting`（404 ではない）。`selected_option` は 0-(A) の主質問の選択にだけ使う。
- データ準備のパッケージ（`chunking/` ほか）は進捗を `logger` にしか出さないので、`job_logs.capture_logs` が実行中だけハンドラを付けてイベントへ流す
  （スレッド ID で絞る・level を参照カウントで戻す。どちらも削ると壊れる。[`job_logs.md`](job_logs.md)）。

### 3.3 失敗の扱いと制約

| 失敗 | 見え方 |
|---|---|
| runner が例外 | `error`（型・内容と Qdrant / API キーの手がかり）→ `failed` |
| runner が `None` | runner が出した `error` だけ → `failed` |
| HITL のタイムアウト | `intervention`（`timeout`）。**ジョブは失敗しない**。アクションを実行せず有人へ倒して続行 |
| ジョブ ID が無い | 404（`job not found`） |
| データ準備で Ollama が起動していない・モデルが pull されていない | LLM ループの前に `error`（起動方法・pull 済みの一覧を添える）→ `failed` |

SSE は失敗しても必ず `done`（`status=failed`）で閉じる。

| 制約 | 影響 |
|---|---|
| メモリ上に保持・永続化なし | 再起動で全ジョブが消え、`job_id` は 404 |
| シングルプロセス前提 | `--workers 2` 以上だと別プロセスのジョブが見えない |
| 完了ジョブは 50 件まで（`MAX_FINISHED_JOBS`） | 古い完了ジョブから消え、結果の取得が 404 |
| 認証なし | CORS は `localhost:5173` / `127.0.0.1:5173` だけ |
| キャンセル API なし | 走り出したジョブは外から止められない |

ジョブ種別を足すときの手順は [`README_backend_core.md`](README_backend_core.md) §5.2。

---

## 4. 3 モードの対照とガードレール

> 📝 2026-10-13 に直下 `docs/` の `pipelines.md`（3 モードの対照）・`guardrails.md`（ガードレール一覧）・
> `reasoning_flow.md`（生成ステップ）をここへ統合した。全文は git 履歴に残る。
> 生成ステップのプロンプトは各モジュール文書（[`grace/docs/tools.md`](../../../../grace/docs/tools.md) §5.4・
> [`grace/docs/executor.md`](../../../../grace/docs/executor.md) の `_prepare_tool_kwargs`・[`review_gates.md`](review_gates.md)）が正本。

### 4.1 モードとステップの対照

画面のタブ（`frontend/src/App.tsx` の `Tab`）とコア関数の対応は次のとおり。**基本版と GRACE-Support は同じ 1 関数**で、
`vertical` を渡すかどうかだけが違う（§1.7）。GRACE-Review は別のコアで、判定の骨格だけを Support と同型にしてある（§4.3）。

| タブ | `Tab` | コア関数 | API |
|---|---|---|---|
| 基本版 | `basic` | `run_support_agent_core`（`vertical=None`） | `POST /api/support/query` → `GET /api/support/stream/{job_id}` |
| GRACE-Support | `support` | 同上（`vertical` を指定） | 同上 |
| GRACE-Review | `review` | `run_review_agent_core` | `POST /api/review/submit` → `GET /api/review/stream/{job_id}` |
| データ管理 | `data` | `data_jobs.py` / `services/data_pipeline_service.py` | `POST /api/chunking/run` / `POST /api/qa/generate` / `POST /api/qdrant/register` ほか |

番号は Support との対応を示す呼称で、実行順とは一致しない（Support の ④' は ⑤ の後、Review の ⑤ は ⑥ の後。実行順の図は §1.1・§2.2）。

| # | 基本版 | GRACE-Support | GRACE-Review |
|---|---|---|---|
| 0-(A) | `analyze` 質問の整理（LLM） | `analyze` 質問の整理（LLM）・担当範囲 | — |
| S1 / 0-(B) | — | `profile` 業界プロファイル適用 | `ruleset` ルールセット適用 |
| ① | `plan` | `plan` | `segment` 文書を検査単位へ分割 |
| ② | `execute` 内部 RAG → reasoning | 同左 | `retrieve` 規程を RAG 検索 |
| ③ | `confidence` 根拠検証 | 同左 | `detect` 二段判定で違反候補を検出 |
| ④ | `gate` 回答ゲート＋強制エスカレ＋救済 | 同左 | `ground` 指摘の根拠を検証 |
| ④' | `no_info` 情報なし回答の検知 | 同左 | `suppress` 誤検知抑止＋救済 |
| ⑤ | `web` Web フォールバック | 同左 | `severity` 重大度の確定＋強制 high |
| ⑥ | `action` 本人確認 → HITL → 実行 | 同左 | `web` 法改正・ガイドラインの裏取り |
| ⑦ | — | — | `action` レポート → HITL → 実行 |

### 4.2 ガードレール一覧（機構 → 実装 → 失敗時の既定）

右の 3 列は、そのガードレールがモードごとに効くか（✅）を示す。

| ID | 機構 | 実装 | 判定不能・失敗時 | 基本版 | Support | Review |
|---|---|---|---|:--:|:--:|:--:|
| GA | 質問の整理・話題の選択（0-(A)） | `question_refiner.py::refine_question` / `parse_refine_output` / `restrict_web_query`、`gates.py::deferred_main_questions`（§1.6） | **元の文のまま 1 話題・1 観点**。選択がタイムアウト・拒否でも元の文のまま 1 周し、escalate にしない | ✅ | ✅ | — |
| GA' | 担当範囲の判定（0-(A)） | `Topic.in_scope`（整理と同じ LLM 呼び出し）/ `gates.py::split_by_scope` / `ensure_out_of_scope_notice`、`VerticalProfile.scope_description` / `out_of_scope_links` | **全件を範囲内とみなす**（判定不能・分類器なし・全件 OUT のいずれも） | — | ✅ | — |
| G0 | RAG 採用下限 | `executor.reasoning_min_rag_score`（0.64） | 全件が下限未満ならフィルタを外す（0 件化を避ける） | ✅ | ✅ | ✅（`retrieve` の `evidence_min_score`） |
| G1 | 根拠検証 | `grace/confidence.py::GroundednessVerifier.verify`（claim ごとに 3 値。同一入力は `_CACHE_SIZE=4` でキャッシュ） | `verification_failed=True` を立てて救済判断（G5B）へ回す | ✅ | ✅ | ✅（`ground`） |
| G1A | 支持率 | `support_rate = supported / (supported + contradicted)`（neutral は分母から除外） | `decided=0` なら 0.0 ＋ `verified=False` | ✅ | ✅ | ✅ |
| G1A' | 方針文の除外 | `confidence.py::is_unsupportable_policy_claim`（「担当範囲外です」等の neutral を母数から外す） | 主張がすべて方針文なら除外しない | ✅ | ✅ |✅ |
| G1B | 判定率による減衰 | `executor.py::_damp_support_rate`（`strength=0.3` / `target=0.8`） | `strength=0` で減衰なし | ✅ | ✅ | ✅ |
| G1C | 矛盾による上限 | `executor.py::_blend_groundedness_confidence`（矛盾 1 件以上で `answer_conf` を 0.30 に） | — | ✅ | ✅ | — |
| G2 | 回答ゲート | `gates.py::_answer_gate`（§1.3） | 未検証・出典 0 → escalate | ✅ | ✅ | ✅（`decide_finding_status`） |
| G3 | 強制エスカレ（二段判定） | `gates.py::_should_force_escalate`（エスカレ語 → 意図分類。`question` なら抑止） | 分類失敗 → 安全側で強制エスカレ | —（語が無い） | ✅ | ✅（`should_force_high`） |
| G4 | 未肯定回答の救済 | `gates.py::_should_rescue_unaffirmed` | 強制エスカレ時は救済しない | ✅ | ✅ | ✅（`should_rescue_finding`） |
| G5 | Web フォールバック | `support_agent.py` の ⑤（§1.4） | 検索 0 件 → escalate 継続 | ✅ | ✅ | ✅（⑥ 裏取り。信頼度を下げる方向だけ） |
| G5A | 相互検証 | `confidence.py::SourceAgreementCalculator`（一致度が `confirm_th` 未満なら矛盾扱い） | 回答を再利用したときはスキップ | ✅ | ✅ | — |
| G5B | 検証器障害の救済 | `gates.py::_should_rescue_unverified`（**検証器が落ちたときだけ**） | 「肯定できなかった」は対象外 | ✅ | ✅ | — |
| G6 | 情報なし／実質性なしの検知 | `gates.py::_detect_no_info_answer`（出典が Web だけなら第 2 段を必ず通す） | 候補句あり＋判定不能 → escalate ／ 候補句なし＋判定不能 → **維持** | ✅ | ✅ | ✅（`detect_vacuous_finding`） |
| G7 | アクション判定 | `gates.py::_decide_action`（§1.5） | 分類失敗 → 起票（副作用は G9 が守る） | ✅ | ✅ | ✅（`_decide_review_action`：high があれば `escalate_to_human`、なければ `create_ticket`） |
| G8 | 本人確認 | `support_actions.py::IdentityVerifier.verify` | 未確認 → 実行せず引き継ぎ | — | ✅（`ec`） | — |
| G9 | HITL 承認 | `grace/intervention.py::InterventionHandler` / `intervention_bridge.py::InterventionBridge`（副作用のあるバックエンド〔`has_side_effects=True`〕だけ） | タイムアウト → 実行せず escalate | ✅ | ✅ | ✅ |

**3 つの原則**（全ガードレールに共通）:

1. **二段判定** — 第 1 段でキーワード候補を絞り（LLM を呼ばない）、一致したものだけ第 2 段の LLM 判定へ回す。
2. **安全側フォールバック** — 判定の失敗（`None`）は常にキーワード判定へ倒す。Support の安全側は「回答せず escalate」、
   Review の安全側は「指摘を消さず `review_required`」。例外が GA / GA' で、こちらは「元の文のまま進める・断らない」が安全側（誤って分解・拒否する方が害が大きい）。
3. **救済** — 「矛盾がある」と「肯定できなかった」を区別し、後者では回答を捨てない（G4・G5B）。

> ⚠️ **本リポジトリの既定は `judges.enabled: false`**（ローカル LLM では 1 判定に 90〜250 秒かかるため）。G3・G6・G7 の第 2 段 LLM 判定は
> 常に `None` を返し、**実際にはキーワード判定だけで動く**（`create_intent_classifier` が `lambda _query: None` を返す）。上の表の「第 2 段」は
> 既定構成では効いていないと読むこと。判定の精度を優先するなら `judges.enabled` を `true` に戻す。
> **0-(A) の質問の整理（GA / GA'）は `judges` に関係なく毎回動く**（`question_refiner.refine_question`。2026-10-29 に専用フラグ `judges.multi_question` を削除した）。姉妹リポジトリ grace_v2 は `judges` ブロックが無く、すべて有効。
>
> ⚠️ **dry-run では G9 を経由しない。** 承認は取り消せない操作の前に人を挟む仕組みなので、起票も送信もしない実行に求めても意味が無く、
> 押されないまま `intervention.default_timeout`（300 秒）待って有人対応へ倒れていた（実測: GRACE-Review の 1 実行 8 分 22 秒のうち 5 分がこの空転）。
> `ActionBackend.has_side_effects`（既定 `True`）で判定し、`dry-run` だけが `False` を宣言する。

### 4.3 生成ステップ（Support の reasoning と Review の detect）

| | Support の `reasoning`（② の最終ステップ） | Review の `detect`（③） |
|---|---|---|
| 実装 | `grace/tools.py::ReasoningTool`（grace 側。`support_agent.py` は `executor.execute(plan, allow_react=False)` に丸ごと委譲する） | `review_gates.py::create_violation_detector`（backend 側） |
| 呼び出し回数 | 1 回 | セグメント × 候補ルール（上限 `MAX_LLM_CALLS`。§2.5） |
| 出力 | 自由文（Markdown） | 構造化（`DetectVerdict`） |
| temperature / 出力上限 | `llm.temperature`（0.7）/ `llm.max_tokens`（4096） | 0.0 / 512 |
| モデル | `llm.model` | `detect_model(config)`（同じく設定 `get_config().llm` を正） |
| 失敗時 | `ToolResult(success=False)` → ② が失敗 → リプラン対象 | `None` → 指摘を残し、status の上限を `review_required` にする |
| 後段の検証 | `GroundednessVerifier`（回答 × 出典） | `GroundednessVerifier`（**指摘文** × 規程＋**検査対象の本文**） |

- **プロンプトと判定ゲートは一体の設計である。** 回答の構成ルール 3（出典の明示）で ③ が支持率を検証できる形になり、
  ルール 1 の定型句「提供された情報源には見当たりませんでした」が ④' の検知対象そのものになる。
- **指示の位置が結果を変える。** 担当範囲外の断り（`llm.prompt_closing`）は【回答の構成ルール（最重要）】の**後ろ**に置く。
  業務方針（前方）に混ぜていたときは構成ルールに負けて断りが落ちた（実測 2 回連続）。
- **Review の `detect` が `None`（判定失敗）を返したときの扱いは二重に注意する。** 「違反なし」と解釈すると指摘が静かに消える。
  一方で定型文の指摘をそのまま根拠検証にかけると、ルール名の言い換えなので支持率 1.00 で「確定」になってしまう（実測 2026-08-31）。
- **`detect_model(config)` は設定 `get_config().llm` を正とする。** 設定を見ない定数 `ModelConfig.DEFAULT_MODEL` を直接使うと、クライアント本体と食い違って
  detect だけが存在しないモデル名で呼ばれ 404 になる（実測 2026-08-31 で 33 回の detect が全滅し、指摘が全件「要確認」になった）。
- Review の根拠検証には**検査対象の本文も渡す。** 表記漏れの指摘は「〜の記載がない」という対象文書についての主張で、
  条文だけでは原理的に検証できない（全 neutral になる）。

### 4.4 閾値・設定値

| 設定キー（`config/grace_config.yml`） | 既定 | 効果 |
|---|---|---|
| `confidence.thresholds.notify` / `confirm` | 0.7 / 0.4 | §1.3 の高・中信頼の境目（`gov` は 0.8 / 0.5 に上書き） |
| `confidence.groundedness_weight` / `self_eval_weight` / `coverage_weight` | 0.6 / 0.25 / 0.15 | 信頼度の合成比（支持率が主成分） |
| `confidence.groundedness_coverage_strength` / `target` | 0.3 / 0.8 | G1B の減衰の強さと、減衰しなくなる判定率 |
| `executor.reasoning_min_rag_score` | 0.64 | G0。Embedding を変えたら測り直す |
| `executor.relevance_check_model` | `""`（= `llm.light_model`） | RAG 適合性チェックのモデル |
| `llm.timeout` / `planner.step_timeout_seconds` | 180 / 240 | **`llm.timeout` < `step_timeout_seconds` が不変条件**（LLM の待ちがステップのタイムアウトを超えると、リトライできずにステップごと失敗する）。`tests/test_timeout_budget.py` が守る。背景は [`docs/local_llm_timeout_budget.md`](../../../../docs/local_llm_timeout_budget.md) |
| `judges.enabled` | **false** | 補助 LLM 判定（意図分類・情報なし判定）を止める。0-(A) の整理は止めない（毎回呼ぶ） |

### 4.5 対応するテスト

| テスト（`tests/`） | 守っている挙動 |
|---|---|
| `test_support_agent_core.py` | Support の 1 周（0-(A)〜⑥）の判定 |
| `test_question_refiner.py` / `test_refine_pipeline.py` / `test_question_scope_and_deferred.py` | GA / GA'（§1.6） |
| `test_plan_for_topic.py` / `test_web_search_reasons.py` | ① 観点ごとの計画・② Web 検索の理由の記録 |
| `test_policy_claims.py` | G1A' 方針文を母数から外す |
| `test_groundedness_sources.py` / `test_groundedness_cache.py` / `test_groundedness_claim_trace.py` | G1 出典本文を渡す・同一入力の再検証抑止・矛盾主張の本文保持 |
| `test_verification_failure.py` | G5B 検証器障害の救済 |
| `test_adoption_threshold.py` / `test_measure_rag_threshold.py` | G0 RAG 採用下限 |
| `test_web_only_needs_a_verdict.py` / `test_no_info_judge_failure_reason.py` | G6（出典が Web だけのとき・判定失敗理由の記録） |
| `test_judge_model_resolution.py` | 判定系モデルの解決（設定 `get_config().llm` を正とする） |
| `test_rag_adoption.py` | G0 RAG 採用下限 |
| `test_no_info_judge.py` / `test_no_info_prediction.py` | G6（出典が Web だけのときの第 2 段・判定モデルの解決） |
| `test_observability.py` | 矛盾した主張の本文をログへ残す |
| `test_intervention_bridge.py` / `test_dry_run_skips_confirmation.py` | G9 Web の承認待ち・dry-run では承認を求めない |
| `test_timeout_budget.py` | `llm.timeout < planner.step_timeout_seconds` |
| `test_executor_reasoning_and_memory.py` | `ask_user` を reasoning の参照情報に混ぜない |
| `test_reasoning_prompt_order.py` | `prompt_closing` が構成ルールの後ろに置かれる |
| `test_review_gates.py` / `test_review_detect_failure_status.py` / `test_review_undecided_groundedness.py` | Review のゲート・判定失敗を `confirmed` にしない・全 neutral を支持率 0 と混同しない |


---

## 5. 関連ドキュメント

| 知りたいこと | 文書 |
|---|---|
| Support の関数・`SupportResult` の項目 | [`support_agent.md`](support_agent.md)・[`gates.md`](gates.md)・[`verticals.md`](verticals.md) |
| Review の関数・データクラスの項目 | [`review_agent.md`](review_agent.md)・[`review_gates.md`](review_gates.md)・[`rulesets.md`](rulesets.md)・[`review_facts.md`](review_facts.md) |
| ジョブ・HITL・ログ取り込みの実装 | [`jobs.md`](jobs.md)・[`intervention_bridge.md`](intervention_bridge.md)・[`job_logs.md`](job_logs.md)・[`data_jobs.md`](data_jobs.md) |
| エンドポイント・SSE のワイヤ形式・HTTP ステータス | [`../../api/docs/README_backend_api.md`](../../api/docs/README_backend_api.md) §4 |
| backend 全体の構成・モデル名の解決・API キー | [`../../docs/README_backend_app.md`](../../docs/README_backend_app.md) §4 |
| 生成ステップのプロンプト | [`grace/docs/tools.md`](../../../../grace/docs/tools.md)・[`grace/docs/executor.md`](../../../../grace/docs/executor.md)（`_prepare_tool_kwargs`） |
| 画面側 | [`frontend/docs/SupportPanel.md`](../../../../frontend/docs/SupportPanel.md)・[`frontend/docs/ReviewPanel.md`](../../../../frontend/docs/ReviewPanel.md) |

---

## 6. 変更履歴

| バージョン | 日付 | 変更内容 |
|---|---|---|
| 1.0 | 2026-10-12 | 新規作成。`backend/docs/` の廃止にあわせ、`support_flow.md` / `review_flow.md` / `job_runtime.md` の流れと設計判断をまとめた（grace_v2 の同名文書と章立てをそろえ、Ollama 版の違い〔dry-run で承認を省く・③④ の並列数・事前チェックの有無・課金〕を実装で確かめて書いた）。④' 指摘ゲートで根拠ゼロは `review_required`、② Retrieve はルールごとに 1 回（いずれも実装で確認） |
| 1.1 | 2026-10-13 | §1.6 に旧 docs/multi_question_handling.md §0 の要点（二段判定・了解の返事への対策・安全側の向き・judges.multi_question・URL の扱い・返すもの・テスト）を統合 |
| 1.2 | 2026-10-13 | §4「3 モードの対照とガードレール」を新設し、直下 docs/ の pipelines.md・guardrails.md・reasoning_flow.md を統合（モードとステップの対照・ガードレール一覧と失敗時の既定・judges の既定 false と dry-run の扱い・生成ステップの比較・閾値・テスト）。§1.7 に基本版との差の表を移した。関連ドキュメントを §5、変更履歴を §6 へ繰り下げ |
| 1.3 | 2026-10-14 | 索引の改名（frontend/docs/README.md → README_frontend.md、README_api.md → README_backend_api.md、README_core.md → README_backend_core.md）に合わせて参照を付け替えた |
| 1.4 | 2026-10-14 | 索引の改名（backend/app/docs/README_app.md → README_backend_app.md）に合わせて参照を付け替えた |
| 1.5 | 2026-10-30 | 0-(A) を `question_refiner.refine_question()`（毎回 LLM で話題・観点に整理）へ置き換えたのに追随（§1.1 の図・§1.2・§1.6 を書き直し、§1.4 に観点ごとの Web 検索と Web 検索の理由、§4.2 の GA / GA'・§4.4 の `judges`・§4.5 のテストを差し替え。grace_v2 の同名文書 v1.6 と同じ変更）。§4.5 の表の後ろに残っていた見出しの無い表の断片を消し、そこにしか無かった 3 行（`test_rag_adoption.py`・`test_no_info_*`・`test_observability.py`）を表へ移した |
| 1.6 | 2026-10-31 | ④ の強制エスカレ・⑥ のアクションの語を、0-(A) が書き直した質問文ではなく原文で照合するようにしたのに追随（§1.3。grace_v2 と同じ） |

# GRACE-Support API フロー一覧（0 〜 ⑥ 8 段階）

本書は、`grace/*.py`（自律エージェント基盤）と `backend/app/api/*.py` / `backend/app/core/*.py`
（Web API・オーケストレーション）に散らばる**主要 API**を、パイプラインの 8 段階に分類し、
呼び出し順に並べたものである。「処理の流れを API の流れで掴みたい」という目的に合わせ、
各段階について **API（シンボル名）／API概要／入力：概要／処理概要／出力：概要** の表でまとめる。

技術スタック: LLM = ローカル LLM（Ollama・既定 `gemma4:12b-mlx`、`grace/llm_compat.py::create_chat_client`
経由）／ Embedding = Gemini（`gemini-embedding-001`・3072次元）。全 LLM 呼び出しは
`client.models.generate_content(model=..., contents=..., config={...})` という統一インターフェース
（genai 互換）を通る — 内部は Ollama 実装（`OllamaGenaiClient`）だが、呼び出しサイトのコードは
このシグネチャのまま変わらない。

> ⚠️ **行番号は書かない。** 実装への参照は「ファイル名 + シンボル名」で示す（`docs/pipelines.md` /
> `docs/reasoning_flow.md` と同じ方針。行番号はコミットのたびに嘘になる）。
>
> **関連ドキュメント**: モード全体の対照は [`docs/pipelines.md`](./pipelines.md)、生成（reasoning）
> の詳細は [`docs/reasoning_flow.md`](./reasoning_flow.md)、判定（ゲート）の全体像は
> [`docs/guardrails.md`](./guardrails.md)、各モジュールの IPO 詳細は `grace/docs/*.md` /
> `backend/docs/core_gates.md` を参照。

---

## 目次

- [0. 全体フロー（呼び出し順）](#0-全体フロー呼び出し順)
- [(0)-A 入力・質問分析](#0-a-入力質問分析複数質問の検知--選択--再構成)
- [(0)-B 業界プロファイル適用](#0-b-業界プロファイル適用)
- [(1) Plan（planner）](#1-planplanner)
- [(2) Execute（内部RAG → reasoning）](#2-execute内部rag--reasoning)
- [(3) Groundedness（根拠検証）](#3-groundedness根拠検証)
- [(4) 回答ゲート＋強制エスカレ＋救済判定](#4-回答ゲート強制エスカレ救済判定answer)
- [(5) Web フォールバック](#5-web-フォールバック内部回答で確定した場合はスキップ)
- [(4)' 情報なし回答検知](#4-情報なし回答検知)
- [(6) Action（本人確認 → HITL CONFIRM → 実行）](#6-action本人確認--hitl-confirm--実行)
- [変更履歴](#変更履歴)

---

## 0. 全体フロー（呼び出し順）

```
Web: POST /api/support/query（backend/app/api/support.py::start_query）
  └→ backend/app/core/support_agent.py::run_support_agent_core（パイプライン統括の親API）
       ├→ (0)-A 入力・質問分析
       ├→ (0)-B 業界プロファイル適用
       ├→ ① Plan   … grace/planner.py::Planner.create_plan
       ├→ ② Execute … grace/executor.py::Executor.execute
       ├→ ③ Confidence … grace/confidence.py::GroundednessVerifier.verify
       ├→ ④ 回答ゲート … backend/app/core/gates.py::_answer_gate ほか
       ├→ ⑤ Web フォールバック（④で escalate のときのみ）
       ├→ ④' 情報なし回答検知
       └→ ⑥ Action
```

CLI（`agent_support_example.py`）も同じ `run_support_agent_core` を通る（`CLAUDE.md` §1）。
Web/CLI で分岐する API は存在しない。

---

## (0)-A 入力・質問分析（複数質問の検知 → 選択 → 再構成）

| API | API概要 | 入力：概要 | 処理概要 | 出力：概要 |
|---|---|---|---|---|
| `backend/app/api/support.py::start_query` | ・問い合わせジョブを起動する（Web 入口）。<br>・上位のモジュール：フロントエンド（`POST /api/support/query`）<br>・API：<code>job = job_manager.start(JobParams(query=...))</code> | `QueryRequest`（query / vertical / model / dry_run / use_web / do_action / identity） | `JobParams` を組み立て `job_manager.start()` へ委譲。ジョブはバックグラウンドスレッドで `run_support_agent_core` を実行する | `QueryAccepted`（job_id, stream_url）。以降の進捗は SSE（`GET /api/support/stream/{job_id}`）で配信 |
| `backend/app/core/support_agent.py::run_support_agent_core` | ・8 段階パイプライン全体を統括する**親 API**。<br>・上位のモジュール：`backend/app/core/jobs.py`（ジョブワーカー）／ CLI では `agent_support_example.py`<br>・API：<code>support = run_support_agent_core(query, vertical=..., model=..., emit=..., confirm=...)</code> | `query`（利用者の問い合わせ文）、`vertical`、`model`、`identity`、`emit`（進捗コールバック）、`confirm`（HITL 応答コールバック） | config をリクエスト単位でディープコピーし、`tool_registry` / `planner` / `executor` / `verifier` を生成。以降 ①〜⑥ を順に呼び出し、各段階の結果を `SupportResult` へ積み上げる | `SupportResult`（answer / citations / groundedness / decision / action_result ほか） |
| `gates.py::looks_like_multi_question` | ・複数質問の**候補**かを判定する第 1 段（LLM 呼び出しゼロ）。<br>・上位のモジュール：`support_agent.py::run_support_agent_core`<br>・API：<code>looks_multi = looks_like_multi_question(query)</code> | `query`（原文） | `MULTI_QUESTION_MARKERS`（接続表現）の部分一致・疑問符の数などをルールで判定 | `bool`。False なら第 2 段（LLM）は一切呼ばれない |
| `gates.py::create_question_analyzer` → `analyze_questions` | ・複数質問の**分解**と**担当範囲判定（GA'）を 1 回の LLM 呼び出しで**行う第 2 段。<br>・上位のモジュール：`support_agent.py::run_support_agent_core`（`looks_multi=True` のときのみ生成・呼び出し）<br>・API：<code>response = client.models.generate_content(model=judge_model(config), contents=prompt, config={"temperature": 0.0, "max_output_tokens": MULTI_QUESTION_MAX_OUTPUT_TOKENS})</code> | `query`、`profile`（業界プロファイル。`scope_description` 等を分類に使う） | 軽量 LLM（`judge_model(config)`）へ 1 回問い合わせ、質問クラスタ（主質問＋関連質問）と IN/OUT 判定を同時に得る。判定できなければ「単一質問」に倒す（**安全側の向きが他ゲートと逆**） | `QuestionAnalysis(clusters, verdicts)` |
| `gates.py::split_by_scope`（+ `scope_classifier_for` / `create_scope_classifier`） | ・クラスタを担当範囲内／範囲外の添字へ分ける（フォールバック経路）。<br>・上位のモジュール：`support_agent.py::run_support_agent_core`（`analysis` が IN/OUT を返していない場合のみ `create_scope_classifier` を追加で LLM 呼び出し）<br>・API：<code>response = client.models.generate_content(model=model_name, contents=prompt, config={"temperature": 0.0, "max_output_tokens": MULTI_QUESTION_MAX_OUTPUT_TOKENS})</code> | `clusters`（主質問＋関連質問のリスト）、`classify`（分類関数） | 範囲外の主質問は選択肢から除外（黙って保留にはしない）。判定不能なら**全件を範囲内**として扱う | `(in_scope_indexes, out_of_scope_indexes)` |
| `gates.py::reconstruct_query` | ・採用クラスタ（主質問＋関連質問）を自然文の 1 文へ再構成する。<br>・上位のモジュール：`support_agent.py::run_support_agent_core`（主質問が確定した後）<br>・API：<code>response = client.models.generate_content(model=judge_model(config), contents=prompt, config={"temperature": 0.0, "max_output_tokens": MULTI_QUESTION_MAX_OUTPUT_TOKENS})</code> | `main`（主質問）、`related`（関連質問リスト）、`config` | 関連質問が無ければ LLM を呼ばず `main` をそのまま返す。ある場合は LLM で指示語（「その」等）を解決した 1 文へ統合。失敗時は `fallback_reconstruct`（単純連結）にフォールバック | `str`（再構成後クエリ）。以降の ①〜⑥ はこの文字列を「利用者の元の質問文」として扱う |

---

## (0)-B 業界プロファイル適用

| API | API概要 | 入力：概要 | 処理概要 | 出力：概要 |
|---|---|---|---|---|
| `backend/app/core/verticals.py::PROFILES.get(vertical)` | ・`--vertical {gov\|saas\|ec}` から `VerticalProfile` を解決する。<br>・上位のモジュール：`support_agent.py::run_support_agent_core`（0-(A) の前に解決だけ済ませ、注入はここで行う）<br>・API：<code>profile = PROFILES.get(vertical) if vertical else None</code> | `vertical`（`gov` / `saas` / `ec` / `None`） | 辞書引き（LLM 不使用）。`vertical` 未指定（基本版）なら `None` | `Optional[VerticalProfile]` |
| `VerticalProfile.build_prompt_addendum` / `build_closing_instruction` | ・業界固有の方針＋共通 `SCOPE_POLICY` を reasoning プロンプトへ注入する文字列を組み立てる。<br>・上位のモジュール：`support_agent.py::run_support_agent_core`（`config.llm.prompt_addendum` / `config.llm.prompt_closing` へ設定）<br>・API：<code>config.llm.prompt_addendum = profile.build_prompt_addendum()</code> | `profile`、`out_of_scope_questions`（0-(A) で範囲外と判定された主質問） | 文字列組み立てのみ（LLM 不使用）。`config.qdrant.allowed_collections` / `config.web_search.preferred_domains` も同時に上書き | `str`（プロンプト追記文）。② Execute の reasoning がこの文字列を読む |

---

## (1) Plan（planner）

| API | API概要 | 入力：概要 | 処理概要 | 出力：概要 |
|---|---|---|---|---|
| `grace/planner.py::Planner.create_plan` | ・質問から実行計画（`ExecutionPlan`）を生成する**親 API**。<br>・上位のモジュール：`support_agent.py::run_support_agent_core`<br>・API：<code>plan = planner.create_plan(query)</code> | `query`（再構成後の質問文）、`context_hints`（replan 由来の補助情報。既定空） | 複雑度を推定し、単純ならルールベース、複雑なら LLM でステップ列を生成。生成後は `repair_plan_dependencies()` で依存関係を補修 | `ExecutionPlan`（`steps: List[PlanStep]`、`complexity: float`） |
| `Planner.estimate_complexity_with_llm` | ・質問の複雑度（0.0〜1.0）を LLM で推定する。<br>・上位のモジュール：`Planner.create_plan`<br>・API：<code>response = self.client.models.generate_content(model=self.model_name, contents=prompt, config={"temperature": self.config.planner.complexity_temperature, "max_output_tokens": self.config.planner.complexity_max_output_tokens})</code> | `query` | LLM 応答から `llm_compat.parse_score()` でスコアを抽出（`float()` 直変換はしない）。失敗時はルールベース推定にフォールバック | `float`（複雑度スコア） |
| `Planner._generate_plan_with_retry` | ・複雑と判定された質問について LLM でステップ列（JSON）を生成する。<br>・上位のモジュール：`Planner.create_plan`（`_should_use_llm_plan()` が True のときのみ）<br>・API：<code>response = self.client.models.generate_content(model=self.model_name, contents=prompt, config=config)</code> | `query`、`prompt`（ステップ生成用プロンプト） | 空応答・不正 JSON は既定回数までリトライ。連続失敗で `_llm_plan_disabled` サーキットブレーカーが働き、以降はルールベース計画へ自動フォールバック | `List[PlanStep]`（JSON パース結果） |

---

## (2) Execute（内部RAG → reasoning）

| API | API概要 | 入力：概要 | 処理概要 | 出力：概要 |
|---|---|---|---|---|
| `grace/executor.py::Executor.execute` | ・計画を実行するオーケストレータの**親 API**（内部RAG検索→reasoning→信頼度算出まで一括）。<br>・上位のモジュール：`support_agent.py::run_support_agent_core`<br>・API：<code>result = executor.execute(plan)</code> | `plan`（`ExecutionPlan`） | 各 `PlanStep` を締切ベースで実行。RAG スコア不足時は動的に `web_search` ステップを挿入（S3 ハイブリッド ReAct ループ）。最後に `_blend_groundedness_confidence` で内部の groundedness 検証も済ませる | `ExecutionResult`（`final_answer` / `step_results` / `overall_confidence`） |
| `grace/tools.py::RAGSearchTool.execute` | ・Qdrant ベクトル検索で内部ナレッジを取得する（LLM 不使用）。<br>・上位のモジュール：`Executor.execute`（`tool_registry.execute("rag_search", ...)`）<br>・API：<code>result = self.client.search(collection_name=..., query_vector=..., limit=..., score_threshold=...)</code>（Qdrant API。generate_content は使わない） | `query`、`collection`、`allowed_collections`（業界プロファイルの検索スコープ） | Gemini Embedding でクエリをベクトル化 → Qdrant 検索 → 許可コレクション順にフォールバック探索 | `ToolResult`（出典テキスト・スコア付きの検索結果） |
| `grace/tools.py::ReasoningTool.execute` | ・収集した情報（RAG/Web 出典）から回答を生成する。<br>・上位のモジュール：`Executor.execute`（`tool_registry.execute("reasoning", ...)`）／ ⑤ Web フォールバックからも再利用<br>・API：<code>response = self.client.models.generate_content(model=self.model_name, contents=prompt, config={"temperature": self.config.llm.temperature, "max_output_tokens": self.config.llm.max_tokens, "thinking_budget_tokens": heavy_thinking_budget(self.config)})</code> | `query`、`context`、`sources`（RAG/Web 検索結果） | プロンプト構築（0-(B) の `prompt_addendum` / `prompt_closing` を含む）→ 生成。空応答時は参照情報を絞って 1 回だけ再試行 | `ToolResult`（`output`＝生成された回答文） |
| `Executor._dispatch_generator`（S3 ハイブリッド ReAct ループ） | ・次アクション（追加検索／Web検索／終了）を LLM に判断させる。<br>・上位のモジュール：`Executor.execute`（`executor.react_enabled=True` のとき、既定で有効）<br>・API：<code>response = self._react_client.models.generate_content(model=resolve_heavy_model(self.config), contents=prompt, config={"response_mime_type": "application/json", "response_schema": AgentThought, "temperature": 0.0, "max_output_tokens": 512})</code> | 実行中の `ExecutionState`（既収集の出典・これまでの思考） | 構造化出力（`AgentThought`）で次アクションを決定。RAG の関連度が低いと判断すれば `web_search` を動的に追加（`ExecutionState.dynamic_steps`） | `AgentThought`（次アクション種別・理由） |

---

## (3) Groundedness（根拠検証）支持率

| API | API概要 | 入力：概要 | 処理概要 | 出力：概要 |
|---|---|---|---|---|
| `grace/confidence.py::GroundednessVerifier.verify` | ・回答の各主張が出典に裏付けられているかを検証する**親 API**（支持率の算出元）。<br>・上位のモジュール：`support_agent.py::run_support_agent_core`（`executor` と検証器インスタンスを共有し、同一入力の二重 LLM 呼び出しを回避）<br>・API：<code>response = self.client.models.generate_content(model=self.model_name, contents=prompt, config={"response_mime_type": "application/json", "response_schema": GroundednessResponse, "temperature": 0.0, "max_output_tokens": 1024})</code> | `query`、`answer`（内部回答）、`sources`（出典本文。無ければ出典ラベルで代替） | LLM に主張ごとの支持／矛盾／中立（`ClaimVerdict`）を判定させる。`is_unsupportable_policy_claim()` に該当する政策的断り文は分母から除外 | `GroundednessResult`（`support_rate` = supported / (supported + contradicted)、`has_contradiction`、`verified`） |
| `LLMSelfEvaluator.evaluate` / `evaluate_final` | ・LLM 自身に回答の妥当性を自己申告させる（多軸信頼度の一因子）。<br>・上位のモジュール：`Executor.execute` 内の信頼度算出経路（`ConfidenceCalculator`）<br>・API：<code>response = self.client.models.generate_content(model=self.model_name, contents=prompt, config={"response_mime_type": "application/json", "response_schema": FinalEvaluationResult, "temperature": 0.0, "max_output_tokens": 1024})</code> | `query`、`answer`、`sources` | 自己評価スコアと理由を構造化出力で取得 | `FinalEvaluationResult`（スコア・理由） |
| `grace/calibration.py::Calibrator.transform` | ・自己申告 confidence を温度スケーリングで事後較正する（LLM 不使用）。<br>・上位のモジュール：`Executor`（`overall_confidence` の算出経路）<br>・API：<code>calibrated = calibrator.transform(raw_confidence)</code> | `p`（較正前の confidence、0.0〜1.0） | `sigmoid(logit(p) / T)`。`config/calibration.json` から読み込んだ温度 `T` を適用（無ければ恒等 T=1.0） | `float`（較正後 confidence） |

---

## (4) 回答ゲート＋強制エスカレ＋救済判定: answer

| API | API概要 | 入力：概要 | 処理概要 | 出力：概要 |
|---|---|---|---|---|
| `gates.py::_answer_gate` | ・支持率・出典数から回答可否（answer/notify/confirm/escalate）を判定する純関数（LLM 不使用）。<br>・上位のモジュール：`support_agent.py::run_support_agent_core`<br>・API：<code>decision, warning = _answer_gate(gres.support_rate, gres.verified, len(citations), notify_th, confirm_th)</code> | `support_rate`、`verified`、`citation_count`、`notify_th`、`confirm_th`（業界プロファイルのしきい値） | しきい値との比較のみ | `(Decision, bool)`（`decision` は `"answer"` / `"escalate"` 等、`warning` は未確認注記の要否） |
| `gates.py::_should_force_escalate`（→ `create_intent_classifier`） | ・業界プロファイルのエスカレ語に一致したら強制的に有人対応へ倒す二段判定。<br>・上位のモジュール：`support_agent.py::run_support_agent_core`<br>・API：<code>response = client.models.generate_content(model=model_name, contents=prompt, config={"temperature": 0.0, "max_output_tokens": JUDGE_MAX_OUTPUT_TOKENS})</code>（第 2 段：意図分類。第 1 段のキーワード一致時のみ呼ばれる） | `query`、`profile`（`escalate_keywords`）、`classify`（意図分類関数、メモ化済み） | 第 1 段：キーワード部分一致で候補検出。第 2 段：意図が `"question"`（FAQ質問）なら誤検知として通常フロー継続、それ以外なら強制エスカレ | `(forced_escalate: bool, matched_keyword, intent)` |
| `gates.py::_should_rescue_unaffirmed` / `_should_rescue_unverified` | ・groundedness が「肯定できなかった」だけで escalate に落ちた**出典付き・矛盾なし**の回答を救済する（LLM 不使用、内部で `no_info_judge` へ委譲する経路のみ LLM）。<br>・上位のモジュール：`support_agent.py::run_support_agent_core`（④救済＝内部回答／⑤救済＝Web 回答）<br>・API：<code>rescued = _should_rescue_unaffirmed(decision, forced_escalate, gres.has_contradiction, len(citations), answer, query, no_info_judge)</code> | `decision`、`has_contradiction`、`citation_count`、`answer`、`query`、`no_info_judge` | 矛盾なし・出典あり・「情報なし」回答でない場合のみ `decision="answer"`（未確認注記つき）へ引き上げる | `bool`（救済したか） |

---

## (5) Web フォールバック（内部回答で確定した場合はスキップ）

| API | API概要 | 入力：概要 | 処理概要 | 出力：概要 |
|---|---|---|---|---|
| `grace/tools.py::WebSearchTool.execute` | ・内部が escalate（かつ強制エスカレでない）ときに Web 検索で裏取りする（LLM 不使用）。<br>・上位のモジュール：`support_agent.py::run_support_agent_core`（`tool_registry.execute("web_search", query=query)`）<br>・API：<code>raw_results = self._search_with_backend(backend, query, num, lang)</code>（DuckDuckGo / Google CSE / SerpAPI。主バックエンド失敗時は `fallback_backend` へ再試行） | `query`、`num_results`、`language` | executor が既に動的 Web 検索を実施済みなら再検索せず内部回答を再利用（重複推論の省略） | `ToolResult`（rag_search 互換フォーマットの検索結果） |
| `grace/tools.py::ReasoningTool.execute`（Web 経由） | ・Web 検索結果から回答を再生成する（再利用時はスキップし内部回答をそのまま使う）。<br>・上位のモジュール：`support_agent.py::run_support_agent_core`<br>・API：（(2) と同一シグネチャ。`sources=web_output` を渡す） | `query`、`sources=web_output` | (2) の `ReasoningTool.execute` と同じ経路 | `ToolResult`（Web 版の回答文） |
| `grace/confidence.py::SourceAgreementCalculator.calculate` | ・内部回答と Web 回答の一致度を Embedding のコサイン類似度で算出する（相互検証。LLM 不使用）。<br>・上位のモジュール：`support_agent.py::run_support_agent_core`（再利用時はスキップ＝同一回答の比較は無意味なため）<br>・API：<code>agreement = agreement_calc.calculate([internal_answer, web_answer])</code> | `answers: List[str]`（内部回答・Web 回答） | Gemini Embedding でベクトル化 → コサイン類似度。しきい値未満なら「矛盾」扱い | `float`（一致度 0.0〜1.0） |

---

## (4)' 情報なし回答検知

| API | API概要 | 入力：概要 | 処理概要 | 出力：概要 |
|---|---|---|---|---|
| `gates.py::create_no_info_judge` → `_detect_no_info_answer` | ・「見つかりませんでした」型の誠実な情報なし回答が、出典・支持率を伴ってゲートを通過してしまうのを防ぐ二段判定。<br>・上位のモジュール：`support_agent.py::run_support_agent_core`（`decision=="answer"` かつ回答が空でないときのみ実行） <br>・API：<code>response = client.models.generate_content(model=model_name, contents=prompt, config={"temperature": 0.0, "max_output_tokens": JUDGE_MAX_OUTPUT_TOKENS})</code> | `query`、`answer`、`force_judge`（出典が Web のみなら候補句なしでも判定を必須化） | 第 1 段：`NO_INFO_MARKERS` の部分一致。第 2 段：軽量 LLM が「実質回答か／情報なしか」を判定。判定不能時は判定なし（Web のみを理由にはエスカレしない） | `(no_info: bool, marker: Optional[str])`。`no_info=True` なら `support.decision` を `"escalate"` へ書き換える |

---

## (6) Action（本人確認 → HITL CONFIRM → 実行）

| API | API概要 | 入力：概要 | 処理概要 | 出力：概要 |
|---|---|---|---|---|
| `gates.py::_decide_action`（→ `create_intent_classifier` 再利用） | ・問い合わせ内容と回答判定から必要なアクション種別を決める二段判定（意図分類器は ④ とメモ化共有）。<br>・上位のモジュール：`support_agent.py::run_support_agent_core`（`do_action=True` のとき）<br>・API：<code>action = _decide_action(query, support.decision, profile, classify)</code> | `query`、`decision`、`profile`（`action_map`）、`classify` | 第 1 段：キーワード一致で候補検出。第 2 段：意図分類で確定 | `Optional[ActionRequest]`（`action_type` / `args` / `requires_confirmation`） |
| `support_actions.py::IdentityVerifier.verify` | ・本人確認（`profile.require_identity=True` のときのみ）。LLM 不使用。<br>・上位のモジュール：`support_agent.py::_perform_action`<br>・API：<code>result = identity_verifier.verify(identity)</code> | `identity`（利用者が提示した識別子） | `CsvIdentityChecker` 等で照合。未確認ならアクションを実行せず有人対応へ引き継ぐ（安全側） | `IdentityResult`（`verified: bool`、`method`、`detail`） |
| `grace/intervention.py::InterventionHandler.handle` | ・副作用のあるアクション実行前に人間の承認（CONFIRM）を求める。LLM 不使用。<br>・上位のモジュール：`support_agent.py::_perform_action`（`action.requires_confirmation` かつ `backend.has_side_effects` のときのみ）<br>・API：<code>response = handler.handle(ActionDecision(level=InterventionLevel.CONFIRM, ...))</code> | `ActionDecision`（`level` / `confidence_score` / `reason`） | Web は `InterventionBridge` の承認待ち（`confirm` コールバック）、CLI は `AUTO_PROCEED` で自動承認。タイムアウトは escalate に倒す（安全側） | `InterventionResponse`（`action`＝PROCEED/CANCEL 等、`timeout_reached`） |
| `support_actions.py::ActionBackend.execute`（DryRun / Webhook / Pseudo） | ・アクションを実際に実行する（LLM 不使用）。<br>・上位のモジュール：`support_agent.py::_perform_action`<br>・API：<code>outcome = backend.execute(action.action_type, action.args)</code> | `action_type`、`args` | `dry_run=True`（既定）なら副作用なしで模擬実行。`WebhookActionBackend` は実際に HTTP 呼び出し | `ActionOutcome`（`message`） |

---

## 変更履歴

| Version | 日付 | 内容 |
|---|---|---|
| 1.0 | 2026-09-03 | 初版作成。0〜⑥ 8 段階の主要 API をシンボル名ベースで一覧化 |

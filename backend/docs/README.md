# backend/docs/ - ドキュメント一覧・棚卸し

**Version 1.0** | 最終更新: 2026-09-04

`backend/docs/` 配下の全ドキュメントを棚卸しし、ドキュメント名・概要・重要度・必要性（現状の課題）を一覧化する。
`grace/docs/README.md` の姉妹版。

各行の判定は、対応する実コード（`backend/app/**`）の公開シンボルとドキュメント記載を突き合わせ、
プロバイダ表記・廃止ファイル参照・リンク切れ・設定既定値を機械的に照合したうえで、
本文を実際に読んで確認した結果である（CLAUDE.md 冒頭「作業原則」に従い、確認していないものは
「未確認」と明記する）。

---

## 凡例

- **重要度**: 高＝現行パイプラインの中核（実行経路で常時使われる） / 中＝周辺機能・横断資料 / 低＝参考
- **必要性**: 現行＝内容・用語とも最新実装と一致 / 要更新＝内容は概ね正しいが実装から遅れている /
  要修正＝ **事実誤り**（プロバイダ名・存在しないパス等）を含み優先度高で直すべき

> ⚠️ **プロバイダ表記の grep には注意。** 「`Anthropic` を含む」だけでは、
> **「Anthropic 経路は後方互換として残してある」という正しい説明**まで拾ってしまう。
> 件数ではなく該当行を読むこと。
>
> ⚠️ **Mermaid 規約の grep にも注意。** CLAUDE.md §7.6 は `classDef default fill:#000` で数えるが、
> 本ディレクトリには `classDef default fill: #000`（**コロンの後にスペース**）で書かれたファイルが複数ある。
> Mermaid はどちらも解釈するので**違反ではない**。スペース有無の両方にマッチさせて数えること。

---

## 1. GRACE-Support（`core/support_agent.py` ＋ `core/gates.py` ＋ `core/verticals.py`）

| ドキュメント名 | 概要 | 重要度 | 必要性 |
|---|---|:---:|---|
| [`agent_support_example.md`](./agent_support_example.md) | GRACE-Support 本体の設計書。回答判定フロー・groundedness ゲート・データ契約・ActionTool 仕様・関数 IPO | 高 | **現行**（2026-09-04 v2.0。未記載だった **0-(A) 入力・質問分析**と**判定系のモデル解決**を追加し、`INTENT_MODEL` の値・`ANTHROPIC_API_KEY` ガードを是正。**公開シンボル 26/26**） |
| [`agent_support_example_flow.md`](./agent_support_example_flow.md) | `--vertical gov` 実行 1 本のステップ別トレース（モジュール・コード・IN/OUT） | 高（デバッグ時の実用性が高い） | **現行**（2026-09-04 v2.0。**`S0-(A)` の段を新設**し全体フロー図にも追加。前提を `ollama serve` ＋ `GOOGLE_API_KEY` へ是正） |
| [`agent_support_verticals.md`](./agent_support_verticals.md) | 業界特化（自治体/SaaS/EC）設計書。`VerticalProfile`・しきい値・エスカレ語・アクション対応 | 高 | **現行**（2026-09-04 v2.0。冒頭に「本書を読む前に」を新設し、**KPI 評価基盤 `eval/vertical/` が本リポジトリに存在しない**ことを明示。存在しないテストパス 6 件・リンク切れ 4 件を是正） |
| [`core_support_agent.md`](./core_support_agent.md) | `core/support_agent.py` のモジュール仕様 | 高 | 要修正（Anthropic 表記 3 件・未確認） |
| [`core_gates.md`](./core_gates.md) | 回答ゲート・強制エスカレ・④' 情報なし検知・0-(A) 質問分析の判定ロジック | 高 | **現行**（2026-09-03 v2.0 で全面刷新済み） |
| [`core_verticals.md`](./core_verticals.md) | `VerticalProfile` / `PROFILES` / `SCOPE_POLICY` の定義 | 高 | 要修正（Anthropic 表記 4 件・未確認） |

### この領域で見つかった主な問題（2026-09-04）

| # | 内容 | 状態 |
|---|---|:--:|
| 1 | **0-(A) 入力・質問分析が 3 文書のどこにも無かった。** 現行の `run_support_agent_core` は `SUPPORT_STEPS` の先頭に `analyze` を持ち、業界プロファイル適用より前に走る。棚卸し前の公開シンボル網羅は **18/51** だった | ✅ 是正（26/26） |
| 2 | **KPI 評価基盤 `eval/vertical/` が存在しない。** `run.py` / `metrics.py` / `cases/*.jsonl` / `register_test_collections.py` / `data/*.csv` のいずれも無く、`git log --all --full-history -- 'eval/*'` も空。`agent_support_verticals.md` は 18 箇所からこれを参照し、**ヘッダーで「gov 7/7・saas 8/8・ec 9/9＝decision_accuracy 1.000」と実測値を主張**していた | ✅ 明示（削除はせず、Anthropic 版での計測記録である旨を冒頭に明記） |
| 3 | `INTENT_MODEL = "claude-haiku-4-5-20251001"` と記載。実装は `get_default_ollama_model()` | ✅ 是正 |
| 4 | `ANTHROPIC_API_KEY` の起動ガードを前提にした記述（3 文書・計 5 箇所）。**実装ではガードごと削除済み** | ✅ 是正 |
| 5 | 存在しないテストパス（`tests/test_agent_support_vertical.py` 等 6 箇所）。リポジトリ直下に `tests/` は無い | ✅ 是正 |
| 6 | 円建てのコスト試算（1 ケース ≈ 9 円 等）。ローカル LLM ではトークン課金が発生しない | ✅ 是正（履歴として `<details>` に保存し、実行**時間**の観点へ差し替え） |
| 7 | リンク切れ 6 件（`docs/vertical_spec_review.md` / `vertical_test_data.md` / `migration_and_update.md` / `vertical_gov.md`） | ✅ 是正 |

---

## 2. GRACE-Review（`core/review_agent.py` ＋ `core/review_gates.py` ＋ `core/rulesets.py`）

| ドキュメント名 | 概要 | 重要度 | 必要性 |
|---|---|:---:|---|
| [`review_agent_spec.md`](./review_agent_spec.md) | GRACE-Review の設計書（文書 → 指摘）。S1・①〜⑦ の全体設計 | 高 | **現行**（2026-09-04 訂正。LLM をローカル（Ollama）へ、`detect_model` / `judge_model` の解決経路を明記） |
| [`core_review_agent.md`](./core_review_agent.md) | `run_review_agent_core` のモジュール仕様 | 高 | **現行**（2026-09-04 訂正。削除済みの `ANTHROPIC_API_KEY` ガードを Mermaid・IPO・使用例から除去） |
| [`core_review_gates.md`](./core_review_gates.md) | ③ Detect 二段判定・重大度確定・強制 high の判定ロジック | 高 | **現行**（2026-09-04 訂正。`create_violation_detector` が使うのは **`detect_model`（本モデル）**であって軽量モデルではない点を是正） |
| [`core_rulesets.md`](./core_rulesets.md) | `RuleSet` / `RuleItem` の定義とルールセット（`ec_ad` 等） | 高 | **現行**（2026-09-04 訂正。Mermaid ノードのプロバイダ名） |
| [`review_flow.md`](./review_flow.md) | GRACE-Review の処理ステップ IPO（S1・①〜⑦） | 高 | **現行**（2026-09-04 訂正。§4.0 の鍵ガードを削除し、必要な外部キーは Embedding 用のみである旨を明記） |
| [`api_review.md`](./api_review.md) | `/api/review/*` の API 仕様 | 高 | **現行**（プロバイダ誤記なし） |
| [`review_rules_collection.md`](./review_rules_collection.md) | 規程コレクションの登録手順 | 中 | 要更新（Version ヘッダーが無く更新管理外） |
| [`../../frontend/docs/review_ui.md`](../../frontend/docs/review_ui.md) | `ReviewPanel` ほか GRACE-Review UI | 中 | **現行**（プロバイダ誤記なし） |

### この領域で見つかった主な問題（2026-09-04）

| # | 内容 | 状態 |
|---|---|:--:|
| 1 | **`create_violation_detector` を「軽量モデル（`claude-haiku-4-5-20251001`）」と説明していた。** 実装は `detect_model(config)`＝**本モデル**を使う。指摘文・修正案の生成を伴うため軽量では足りない、というのが実装の判断 | ✅ 是正 |
| 2 | プロバイダ誤記 14 箇所（Mermaid ノード・技術スタック表・IPO の Process 行を含む） | ✅ 是正 |
| 3 | 削除済みの `ANTHROPIC_API_KEY` 起動ガードを前提にした記述（Mermaid ノード・IPO・使用例・コード片の 4 箇所） | ✅ 是正 |
| 4 | 未記載の内部ヘルパー 4 件（`_document_segment` / `_is_too_broad` / `_brief` / `select_document_rules`） | ⚠️ 未対応（公開シンボルは **39/43** 記載。残りは内部ヘルパー中心） |

> 📌 **GRACE-Review の文書は GRACE-Support より状態が良い。** シンボル網羅は 39/43 で、
> 設計と実装の対応も取れている。問題はプロバイダ表記に集中していた。

### 実装側に残る注意点（ドキュメントではなくコードの話）

`review_gates.py::detect_model` の docstring が記録している実測障害は、モデル解決経路の設計上の罠として
Support 側にも当てはまる:

> `ModelConfig.DEFAULT_MODEL` は環境変数だけを import 時に畳み込むモジュール定数で `grace_config.yml` を見ない。
> 一方クライアント本体や groundedness は yml の `llm.model` を読む。両者が食い違うと **Detect だけが存在しない
> モデル名で呼ばれて 404 になる。** 実測 2026-08-31 の GRACE-Review 3 回の実行では、全 33 回の Detect が
> すべて `NotFoundError` で落ち、指摘が全件「自動判定に失敗したため要確認」になった。

回帰テスト: `backend/tests/test_judge_model_resolution.py`。

---

## 3. API・基盤

| ドキュメント名 | 概要 | 重要度 | 必要性 |
|---|---|:---:|---|
| [`main.md`](./main.md) | `backend/app/main.py`（FastAPI アプリ・CORS・ルータ結線） | 高 | **要修正**（Anthropic 表記 10 件。「LLM は Anthropic Claude、鍵は `ANTHROPIC_API_KEY`」と本文・Mermaid の両方で記述） |
| [`install_and_setup.md`](./install_and_setup.md) | 環境構築手順（依存・`.env`・起動） | 高 | **現行**（2026-09-04 是正。**`.env` に `ANTHROPIC_API_KEY=sk-ant-...` を必須と案内していた**のを削除し、Ollama の導入手順と `/api/health` の実レスポンスへ差し替え） |
| [`api_support.md`](./api_support.md) | `/api/support/*` の API 仕様 | 高 | **現行** |
| [`api_meta.md`](./api_meta.md) | `/api/meta/*`・`/api/health` の API 仕様 | 中 | 要修正（Anthropic 表記 2 件・未確認） |
| [`schemas.md`](./schemas.md) | `backend/app/schemas.py` の Pydantic スキーマ | 高 | **現行**（プロバイダ誤記なし） |
| [`core_jobs.md`](./core_jobs.md) | ジョブ管理（SSE・ワーカースレッド） | 高 | **現行** |
| [`core_intervention_bridge.md`](./core_intervention_bridge.md) | HITL CONFIRM の Web 連携 | 高 | **現行** |
| [`data_pipeline.md`](./data_pipeline.md) | データ管理タブ（チャンク化・Qdrant 登録） | 中 | **現行** |
| [`backend_flow.md`](./backend_flow.md) | backend 全体の処理フロー | 中 | **要修正**（Anthropic 表記 6 件・未確認） |
| [`react_processing_flow.md`](./react_processing_flow.md) | ReAct ループの処理フロー | 中 | **要修正**（Anthropic 表記 9 件・未確認） |
| [`confidence_flow_grace_vs_backend.md`](./confidence_flow_grace_vs_backend.md) | `grace/` と backend の confidence 経路の対比 | 中 | 要修正（Anthropic 表記 3 件。うち 1 件は変更履歴の記述で正当） |
| [`core_gates.md`](./core_gates.md) | （§1 と重複掲載） | 高 | **現行** |

---

## 4. ドキュメントではないファイル

`backend/docs/` には `.md` 以外に次の `.txt` が置かれている。**ドキュメントではなく、
GRACE-Review / GRACE-Support の入力サンプルや作業メモ**である。

| ファイル | 内容 |
|---|---|
| `a_rules.txt` | GRACE-Review に投入する React コードのレビュー依頼サンプル |
| `reactバグ修正.txt` | 同上の実行結果メモ |
| `パスワードを忘れました.txt` / `住民票の写しの取り方は.txt` / `明日の東京の天気は.txt` / `領収書を発行できますか.txt` | GRACE-Support の実行トレース（各業種の代表質問） |

> 📝 これらは**実行ログのスナップショット**なので、実装が変わっても自動では古くならない
> （「その時点でこう動いた」という記録として読む）。ドキュメント規約（`docs/` 複数形・IPO 形式）の対象外。

---

## 5. 優先対応の提案

1. **`main.md` のプロバイダ誤記（10 件）** — アプリの入口の説明で「LLM は Anthropic Claude」と
   書かれており、`install_and_setup.md` と並んで**新規参加者が最初に読む**文書。優先度が高い。
2. **`react_processing_flow.md`（9 件）/ `backend_flow.md`（6 件）** — 横断フロー資料。
   Mermaid ノードにプロバイダ名が入っている可能性が高く、図の中まで直す必要がある。
3. **`core_support_agent.md`（3 件）/ `core_verticals.md`（4 件）/ `api_meta.md`（2 件）** — モジュール仕様。
   §1 の 3 点と同じ領域なので、0-(A) の反映状況も併せて確認する。
4. `review_rules_collection.md` に Version ヘッダーを付けて更新管理下に置く。
5. **`eval/vertical/` の扱いを決める**（§1 の問題 #2）。選択肢は 3 つ:
   (a) `grace_v2` から移植する、(b) 本リポジトリ向けに作り直す、(c) `agent_support_verticals.md` から
   KPI 章を落として「設計書」に徹する。現状は **(c) の手前**（存在しない旨を明記しただけ）で止めてある。

---

## 6. 変更履歴

| Version | 日付 | 内容 |
|---|---|---|
| 1.0 | 2026-09-04 | 初版作成。`backend/docs/` 全 24 ファイル（＋ `.txt` 6 件）の棚卸し。GRACE-Support 3 点と GRACE-Review 8 点を実装と突き合わせて監査し、問題を §1・§2 の表に記録した |

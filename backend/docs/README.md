# backend/docs/ - ドキュメント一覧・棚卸し

**Version 1.3** | 最終更新: 2026-09-04

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
| [`agent_support_verticals.md`](./agent_support_verticals.md) | 業界特化（自治体/SaaS/EC）設計書。`VerticalProfile`・しきい値・エスカレ語・アクション対応 | 高 | **現行**（2026-09-04 v3.0。**KPI 評価まわり（旧 §8・旧 §9.1・参照 18 箇所）を章ごと削除**し、`VerticalProfile` と判定ロジックの設計書に徹する形へ。存在しないテストパス 6 件・リンク切れ 4 件も是正済み） |
| [`core_support_agent.md`](./core_support_agent.md) | `core/support_agent.py` のモジュール仕様 | 高 | **現行**（2026-09-04 是正。プロバイダ表記に加え、**依存表の「`os` — `ANTHROPIC_API_KEY` の存在チェック」を削除** — `support_agent.py` は `os` を import していない） |
| [`core_gates.md`](./core_gates.md) | 回答ゲート・強制エスカレ・④' 情報なし検知・0-(A) 質問分析の判定ロジック | 高 | **現行**（2026-09-03 v2.0 で全面刷新済み） |
| [`core_verticals.md`](./core_verticals.md) | `VerticalProfile` / `PROFILES` / `SCOPE_POLICY` の定義 | 高 | **現行**（2026-09-04 是正。`INTENT_MODEL` の値を実装どおり `get_default_ollama_model()` へ。**直接使わず `judge_model()` 経由にする理由**も追記） |

### この領域で見つかった主な問題（2026-09-04）

| # | 内容 | 状態 |
|---|---|:--:|
| 1 | **0-(A) 入力・質問分析が 3 文書のどこにも無かった。** 現行の `run_support_agent_core` は `SUPPORT_STEPS` の先頭に `analyze` を持ち、業界プロファイル適用より前に走る。棚卸し前の公開シンボル網羅は **18/51** だった | ✅ 是正（26/26） |
| 2 | **KPI 評価基盤 `eval/vertical/` が存在しない。** `run.py` / `metrics.py` / `cases/*.jsonl` / `register_test_collections.py` / `data/*.csv` のいずれも無く、`git log --all --full-history -- 'eval/*'` も空。`agent_support_verticals.md` は 18 箇所からこれを参照し、**ヘッダーで「gov 7/7・saas 8/8・ec 9/9＝decision_accuracy 1.000」と実測値を主張**していた | ✅ **削除**（2026-09-04。他プロジェクト由来の記述と判明したため、旧 §8・旧 §9.1 と参照 18 箇所を章ごと除去） |
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
| [`main.md`](./main.md) | `backend/app/main.py`（FastAPI アプリ・CORS・ルータ結線） | 高 | **現行**（2026-09-04 是正。本文・Mermaid・`load_dotenv` の IPO・`/api/health` の戻り値例まで） |
| [`install_and_setup.md`](./install_and_setup.md) | 環境構築手順（依存・`.env`・起動） | 高 | **現行**（2026-09-04 是正。**`.env` に `ANTHROPIC_API_KEY=sk-ant-...` を必須と案内していた**のを削除し、Ollama の導入手順と `/api/health` の実レスポンスへ差し替え） |
| [`api_support.md`](./api_support.md) | `/api/support/*` の API 仕様 | 高 | **現行** |
| [`api_meta.md`](./api_meta.md) | `/api/meta/*`・`/api/health` の API 仕様 | 中 | **現行**（2026-09-04 是正。**`/api/health` の戻り値から `anthropic_api_key` を削除** — 実装は `{status, google_api_key}` のみ返す） |
| [`schemas.md`](./schemas.md) | `backend/app/schemas.py` の Pydantic スキーマ | 高 | **現行**（プロバイダ誤記なし） |
| [`core_jobs.md`](./core_jobs.md) | ジョブ管理（SSE・ワーカースレッド） | 高 | **現行** |
| [`core_intervention_bridge.md`](./core_intervention_bridge.md) | HITL CONFIRM の Web 連携 | 高 | **現行** |
| [`data_pipeline.md`](./data_pipeline.md) | データ管理タブ（チャンク化・Q/A 生成・Qdrant 登録・削除） | 中 | **現行**（v1.2） |
| [`backend_flow.md`](./backend_flow.md) | backend 全体の処理フロー | 中 | **現行**（2026-09-04 是正。Mermaid の `HAIKU` ノードを `JUDGE` へ改名し、外部サービス表・`INTENT_MODEL` の既定値も追随） |
| [`react_processing_flow.md`](./react_processing_flow.md) | ReAct ループの処理フロー | 中 | **現行**（2026-09-04 是正。Mermaid ノード・技術スタック・起動前提に加え、**存在しない `grace/benchmark.py` を `grace/step_trace/benchmark.py` へ**訂正） |
| [`confidence_flow_grace_vs_backend.md`](./confidence_flow_grace_vs_backend.md) | `grace/` と backend の confidence 経路の対比 | 中 | **現行**（2026-09-04 是正。技術スタック行） |
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

## 5. 残作業（TODO）

> **対象リポジトリは `grace_v2_local` と `grace_v2` の 2 つだけ。**
> `anthropic_grace_agent_v2` / `ollama_grace_agent_v2` / `grace_agent_v2_react_anthropic` /
> `openai_grace_agent` は**別プロジェクト**であり、移植元にも参照先にもしない。

### 5.1 `grace_v2_local`（本リポジトリ）

| # | 内容 | 優先度 |
|---|---|:--:|
| 1 | ~~プロバイダ誤記の一掃~~ → **完了**（2026-09-04）。`backend/docs/` に残る `Anthropic` の出現は、「Anthropic 経路は無い / 後方互換として残してある」という**正しい説明**、モデル挙動の実測比較、変更履歴のみ | ✅ |
| 2 | ~~`eval/vertical/` の扱いを決める~~ → **完了**（2026-09-04）。KPI 評価まわりを章ごと削除。§5.3 を参照 | ✅ |
| 3 | `review_rules_collection.md` に Version ヘッダーを付けて更新管理下に置く | 低 |
| 4 | GRACE-Review の未記載シンボル 4 件（`_document_segment` / `_is_too_broad` / `_brief` / `select_document_rules`）。内部ヘルパー中心 | 低 |

### 5.2 `grace_v2`（姉妹リポジトリ・Anthropic 版）

**本リポジトリで 2026-09-04 に是正した負債を、`grace_v2` はそのまま抱えている。**
実測した差分は次のとおり。

| 項目 | `grace_v2` の状態 | `grace_v2_local` |
|---|---|---|
| `eval/vertical/` 参照（実体なし） | **18 件**（`grace/docs/agent_support_verticals.md` 17 + `agent_support_example.md` 1） | ✅ 冒頭に「存在しない」旨を明記済み |
| `agent_example.py` 参照（実体なし） | **13 件**（`grace/docs/grace_core_flow.md`） | ✅ 「本書内の解説用コード片」と明示済み |
| 単数形パス `grace/doc/` | **17 件** | ✅ 全廃 |
| 行番号参照（`*.py:NNN`） | **13 件**（`grace_core.md`） | ✅ 全廃 |
| `old_docs/`（`agent_example_core8.md` / `benchmark.md`） | **残存**（対象スクリプトは git 履歴上不在） | ✅ 削除済み |
| `web_search.md` | **残存**（独立モジュールは存在しない） | ✅ `tools.md` へ統合済み |
| `memory.md` | **未作成** | ✅ 作成済み |
| ドキュメント棚卸し（`grace/docs/README.md`・`backend/docs/README.md`） | **未作成** | ✅ 作成済み |
| GRACE-Support 3 点の所在 | `grace/docs/` のまま | `backend/docs/` へ移設済み |

> ⚠️ **プロバイダ表記だけは逆になる。** `grace_v2` は **Anthropic 版**なので、
> そちらの `Anthropic Claude` / `ANTHROPIC_API_KEY` は**正しい記述**である。
> 本リポジトリで行った Ollama への置換を `grace_v2` へ持ち込んではいけない
> （CLAUDE.md §5「双方向に乖離している。ファイル単位のコピーは壊れる」）。
> 移せるのは**構造的な是正だけ**（存在しないパス・行番号参照・棚卸し）。

**実装も遅れている。** `grace_v2` の `STEP_IDS` は 8 段で、先頭の `analyze`（0-(A) 入力・質問分析）が無い。

| シンボル | `grace_v2_local` | `grace_v2` |
|---|:--:|:--:|
| `STEP_IDS` の `analyze` 段 | ✅ | ❌（`profile` 始まりの 8 段） |
| `analyze_questions` | ✅ | ❌ |
| `split_by_scope` | ✅ | ❌ |
| `ensure_out_of_scope_notice` | ✅ | ❌ |
| `judges_enabled` | ✅ | ❌ |
| `reconstruct_query` / `detect_question_clusters` | ✅ | ✅（複数質問ステップ 1〜3 まで） |

### 5.3 `eval/vertical/` の扱い → **削除で決着**（2026-09-04）

`eval/vertical/`（KPI 評価ランナー・テストケース・テストデータ）は**対象 2 リポジトリのどちらにも存在せず、
git 全履歴にも無い**。他プロジェクト由来の記述だったため、**`agent_support_verticals.md` から
KPI 評価まわりを章ごと削除した**（同ファイル v3.0）。

| 削除したもの | 理由 |
|---|---|
| 旧 §8「テスト用データ」 | 全内容が `eval/vertical/data/*.csv` と `register_test_collections.py` に依存 |
| 旧 §9.1「KPI 評価」 | `eval/vertical/run.py` の実行手順と、その実測 KPI |
| ヘッダーの「gov 7/7・saas 8/8・ec 9/9＝decision_accuracy 1.000」 | 存在しない基盤での計測値 |
| 本文中の残り 18 箇所の参照 | 同上 |

残タスク表の #3（KPI 評価スクリプト）/ #8（テスト用コレクション）/ #13（実運用ナレッジ取得）は
「✅ 実装済み」から **「❌ 本リポジトリには無い」** へ訂正した。品質を数値で確かめたくなったら、
評価基盤の**新規実装**が前提になる。

`agent_support_verticals.md` は **`VerticalProfile` と判定ロジックの設計書**に徹する形になった。
現存するテストは `backend/tests/` 配下のみ（同ファイル §8）。

---

## 6. 変更履歴

| Version | 日付 | 内容 |
|---|---|---|
| 1.3 | 2026-09-04 | `eval/vertical/` の扱いが**削除で決着**したため §5.3 を「要判断」から結果の記録へ差し替え、§5.1 の該当行と §1 の問題 #2 も完了に更新 |
| 1.2 | 2026-09-04 | §5 を「優先対応の提案」から**「残作業（TODO）」**へ改め、対象リポジトリを `grace_v2_local` / `grace_v2` の 2 つに限定することを明記。**`eval/vertical/` の選択肢から「`grace_v2` から移植する」を削除**（`grace_v2` にも存在せず成立しないため — 旧版の誤り）。姉妹リポジトリ `grace_v2` が同じ負債（存在しないパス 31 件・単数形リンク 17 件・行番号参照 13 件・棚卸し未作成）と**実装の遅れ**（`STEP_IDS` に `analyze` 段が無い）を抱えていることを実測して §5.2 に記録 |
| 1.1 | 2026-09-04 | §3 の 6 文書（`main.md` / `react_processing_flow.md` / `backend_flow.md` / `core_support_agent.md` / `core_verticals.md` / `api_meta.md`）と `confidence_flow_grace_vs_backend.md` のプロバイダ誤記を是正し「現行」へ。表記以外の誤り 3 件（`/api/health` の戻り値・`support_agent.py` の `os` 依存・`grace/benchmark.py` の所在）も併せて訂正。優先対応 1 を完了に更新 |
| 1.0 | 2026-09-04 | 初版作成。`backend/docs/` 全 24 ファイル（＋ `.txt` 6 件）の棚卸し。GRACE-Support 3 点と GRACE-Review 8 点を実装と突き合わせて監査し、問題を §1・§2 の表に記録した |

# grace/README.MD  grace/docs/ - ドキュメント一覧・棚卸し

**Version 1.6** | 最終更新: 2026-09-04

> 📎 **姉妹版**: `backend/` 側の棚卸しは [`backend/docs/README.md`](../../backend/docs/README.md)。

`grace/docs/` 配下の全ドキュメントを棚卸しし、ドキュメント名・概要・重要度・必要性（現状の課題）を一覧化する。

各行の判定は、対応する `grace/*.py`（または関連ソース）の最終コミット日と、ドキュメント側の
「最終更新」ヘッダーを突き合わせ、さらに本文を実際に読んで事実誤りの有無を確認した結果である （CLAUDE.md
冒頭「作業原則」に従い、確認していないものは「確認していない」と明記する）。

---

## 凡例

- **重要度**: 高＝現行パイプラインの中核（実行経路で常時使われる） / 中＝周辺機能・横断資料 / 低＝実装の裏付けが取れない・使用実績が薄い
- **必要性**: 現行＝内容・用語とも最新実装と一致 / 要更新＝内容は概ね正しいが実装から遅れている / 要修正＝ **事実誤り**
  （用語・プロバイダ名等）を含み優先度高で直すべき / 要確認＝対応ソースの実在が確認できず、保持・削除の判断にユーザー確認が必要

---

## 1. コアモジュール 1:1 対応ドキュメント（`grace/*.py`）

| ドキュメント名                         | 概要                                                                                                               | 重要度 | 必要性                                                                                                                                                                                                                                                                    |
|----------------------------------------|--------------------------------------------------------------------------------------------------------------------|:------:|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`planner.md`](./planner.md)           | `Planner`：質問複雑度推定・LLM/ルールベース計画生成（`create_plan`）の IPO 仕様書                                  |   高   | **現行**（2026-09-03 に v4.0 へ全面更新済み。`planner.py` と同期）                                                                                                                                                                                                        |
| [`executor.md`](./executor.md)         | `Executor`：計画実行オーケストレータ。内部RAG→reasoning、S3 ハイブリッド ReAct ループ、締切ベース実行の IPO 仕様書 |   高   | **現行**（2026-09-03 に v5.0 へ全面更新済み。`executor.py` と同期）                                                                                                                                                                                                       |
| [`confidence.md`](./confidence.md)     | `GroundednessVerifier`/`ConfidenceCalculator` 等：根拠検証・多軸信頼度算出の IPO 仕様書                            |   高   | **現行**（2026-09-03 に v3.0 へ全面更新済み。`confidence.py` と同期）                                                                                                                                                                                                     |
| [`calibration.md`](./calibration.md)   | `calibration.py`：温度スケーリングによる confidence の事後較正（ECE 縮小）                                         |   高   | **現行**（2026-09-04 v1.1 で再確認。公開シンボル 9/9 記載、LLM 非使用のためプロバイダ誤記なし。`calibration.py` は初回投入以降未変更で本書は追随済み）                                                                                                                    |
| [`intervention.md`](./intervention.md) | `intervention.py`：HITL 4 段階介入（SILENT/NOTIFY/CONFIRM/ESCALATE）管理                                           |   高   | **現行**（2026-09-04 v1.3 で訂正済み。概要の「Anthropic Claude」誤記を Ollama へ、§6.4 の Streamlit 前提の統合例を `InterventionBridge`（FastAPI+SSE）へ差し替え。シンボル 23/23）                                                                                        |
| [`memory.md`](./memory.md)             | `memory.py`：実行メモリ層（P4）。実行実績からコレクション優先順位を学習（`planner` が読み、`executor` が書く）     |   高   | **現行**（2026-09-04 新規作成 v1.0。公開シンボル 14 件と `MemoryConfig` の既定値を実装から確認）                                                                                                                                                                          |
| [`llm_compat.md`](./llm_compat.md)     | `llm_compat.py`：全 LLM 呼び出し（planner/executor/confidence/tools）が経由する互換アダプタ層                      |   高   | **現行**（2026-09-04 v2.0 へ全面改訂。**既定である `OllamaGenaiClient`/`_OllamaModels` が未記載**だった重大な欠落を解消し、`parse_score` / `_strip_think` も追加。Anthropic は「明示時のみの後方互換」として限定記述）                                                    |
| [`replan.md`](./replan.md)             | `replan.py`：ステップ失敗・低信頼度時の動的リプラン（全体/部分再計画・フォールバック・スキップ・中断）             | 中〜高 | **現行**（2026-09-04 v1.6 で訂正済み。プロバイダ誤記（本文＋Mermaid ノード 2 箇所）と、存在しない `agent_rag.py (Streamlit)` 参照を修正。シンボル 20/20）                                                                                                                 |
| [`schemas.md`](./schemas.md)           | `schemas.py`：`ExecutionPlan`/`PlanStep`/`ExecutionResult`・S3 ReAct（`Scratchpad`/`AgentThought`）等 Pydantic スキーマ定義 |   高   | **現行**（2026-09-04 v2.0。**未記載だった公開シンボル 4 件**（`ScratchpadEntry`/`Scratchpad`/`AgentThought`/`repair_plan_dependencies`）を追加し、`PlanStep.dynamic` と `ExecutionResult` の計測 3 フィールドも補完。公開シンボル 14/14） |
| [`tools.md`](./tools.md)               | `tools.py`：`ToolResult` ほかツール群（内部RAG検索・Web検索・推論・ask_user・opt-in の code_execute）の定義。**`tools.py` の唯一のドキュメント** |   高   | **現行**（2026-09-04 v4.0。v3.0 の訂正に加え、**`web_search.md` を統合**して `WebSearchTool` の全メソッド（バックエンド 3 種・`_parse_to_rag_format`・`_calculate_confidence_factors`）と `WebSearchConfig` 全 11 項目を収録。統合時に旧稿の誤り 3 件を実装基準で是正） |
| [`config.md`](./config.md)             | `config.py`：LLM/Embedding/信頼度/介入/リプラン/コスト/Qdrant 等の Pydantic 階層設定                               |   高   | **現行**（2026-09-04 v2.0。プロバイダ誤記を訂正し、**`llm.timeout` の既定値の誤り（doc 30 → 実際 180）**も修正。未記載だった `OllamaConfig`/`JudgeConfig`/`MemoryConfig`/`CodeExecuteConfig` を追加）                                                                     |

**このカテゴリの欠落は解消済み**: `grace/memory.py` のドキュメントが存在しなかったが、2026-09-04 に [
`memory.md`](./memory.md) を新規作成した（IPO 形式・公開シンボル 14 件を実装から確認）。

**`web_search.md` は削除した**（2026-09-04）。`web_search.py` という独立モジュールは存在せず、
`WebSearchTool` は `tools.py` に定義されたクラスの一つなので、内容を [`tools.md`](./tools.md) §4.6・§5.2・§5.3
へ統合し、**`grace/*.py` とドキュメントの 1:1 対応**を回復した。
統合は旧稿のコピーではなく実装との突き合わせで行っており、旧稿にあった次の誤りは持ち込んでいない:
`_calculate_confidence_factors` が修正前のキー（`top_score` / `score_spread` のみ）で書かれていた点、
DuckDuckGo のパッケージ名が旧名 `duckduckgo_search` だった点、`max_retries` を `2` 固定と書いていた点。

---

## 2. 横断・アーキテクチャ概説ドキュメント

| ドキュメント名                               | 概要                                                                                                                                   |              重要度              | 必要性                                                                                                                                                                                              |
|----------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------|:--------------------------------:|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`grace.md`](./grace.md)                     | GRACE 自律型エージェントの設計思想・アーキテクチャ概説（「入口となる傘ドキュメント」の位置づけ）。ReAct→Reflection→GRACE の経緯        | 高（オンボーディング資料として） | **現行**（2026-09-04 v2.0。Version ヘッダーを新設して更新管理下に置き、単数形パス `grace/doc/` を是正、プロバイダ表記を Ollama へ訂正。**新設の第5部**で「本書の 5 段階（`grace/` 汎用エンジン）」と「製品パイプラインの 8 段（0-(A)〜⑥）」の対応関係を明示）                                     |
| [`grace_core.md`](./grace_core.md)           | 8 コアモジュール（planner/executor/confidence/calibration/memory/intervention/replan/tools）の横断アーキテクチャ・構成図・IPO リンク集 |                中                | **現行**（2026-09-04 v2.0。**行番号参照 4 件を全廃**（4 件すべて実装とずれていた）、`_record_memory` の成否判定を現行仕様へ、設定既定値のずれ 2 件（`llm.provider` / `max_parallel_steps`）を訂正、Streamlit 前提のクライアント層を React UI + FastAPI へ差し替え） |
| [`grace_core_flow.md`](./grace_core_flow.md) | `grace_core.md` の姉妹編。自律 Agent の 5 段階設計と、最小実行サンプル（本書内のコード例）＋発行される API・プロンプト全文             |                中                | **現行**（2026-09-04 v2.0。**存在しない `agent_example.py`** を「本書内の解説用コード片」と明示し実物のエントリポイントを案内、§E.2 の LLM 発行部を既定経路の `_OllamaModels` へ差し替え、**§E.4.3 の推論プロンプトを現行の 7 規則へ更新**）                                    |

---

## 3. GRACE-Support（`agent_support_example.py`）関連ドキュメント

> ⚠️ **この 3 件は `grace/docs/` ではなく `backend/docs/` にある**（master `93481c7` で移設済み）。
> 本表は棚卸しの連続性のために残すが、リンク先は `../../backend/docs/` である。

| ドキュメント名                                                     | 概要                                                                                                                     |             重要度             | 必要性                                                                                                                                                                                                              |
|--------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------|:------------------------------:|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`agent_support_example.md`](../../backend/docs/agent_support_example.md)           | GRACE-Support 本体（v1〜v3＋業界特化）の設計書。回答判定フロー・groundedness ゲート・データ契約・ActionTool 仕様         |               高               | 要更新（doc 2026-07-08 ／ `agent_support_example.py` 最終更新 2026-08-21、約 44 日遅れ。現行実装は `backend/app/core/support_agent.py` 経由の 0-(A)/0-(B) 質問分析等が追加されており、本 doc は旧世代の設計を記述） |
| [`agent_support_example_flow.md`](../../backend/docs/agent_support_example_flow.md) | `agent_support_example.md` の姉妹編。`--vertical gov` 実行 1 本のステップ別トレース（モジュール・コード・IN/OUT データ） | 高（デバッグ時の実用性が高い） | 要更新（同上、44 日遅れ。S3 ReActループ等の反映状況は未確認）                                                                                                                                                       |
| [`agent_support_verticals.md`](../../backend/docs/agent_support_verticals.md)       | GRACE-Support 業界特化（自治体/SaaS/EC）設計書。`VerticalProfile`・しきい値・エスカレ語・アクション対応                  |               高               | 要更新（doc 2026-07-11 ／ `verticals.py` 最終更新 2026-08-30、約 50 日遅れ。GA'（担当範囲判定・`SCOPE_POLICY`）等の追加が未反映の可能性が高い）                                                                     |
| [`confidence_calibration.md`](./confidence_calibration.md)         | `confidence.py`×`calibration.py` の横断整理（処理順・データフロー）。個別 docs を補うアーキテクチャ資料                  |               中               | **現行**（2026-09-04 v2.0。技術スタック行・Mermaid ノード・モデル記述を Ollama へ訂正。補助 LLM 判定が既定無効である点も注記）                                                                                      |

---

## 4. 実在が確認できなかったドキュメント（削除済み）

以下 2 件は、記述対象のスクリプトが **git 全履歴（`git log --all --full-history`）を通じて一度も見つからなかった**。
本リポジトリ内で書かれてから削除された形跡もなく、姉妹リポジトリからの文書コピー、または実装されないまま
構想段階で書かれた設計書とみられる。**2026-09-04 にユーザー承認のうえ削除し、`grace/old_docs/` ごと無くなった。**

| ドキュメント名 | 概要 | 削除理由 |
|---|---|---|
| `old_docs/benchmark.md` | `run_benchmark.py --fast` の実行ログ・ベンチマーク集計の解説 | `run_benchmark.py` が git 履歴上に存在しない。文中のコレクション名 `cc_news_2per_anthropic` も現行命名規則 `*_ollama` と矛盾しており、現行実装との対応が取れていなかった |
| `old_docs/agent_example_core8.md` | コア 8 モジュールを明示的に呼び出す最小サンプル `agent_example_core8.py` の設計書 | `agent_example_core8.py` が git 履歴上に存在しない。`grace_core_flow.md` §D が参照していた同名の `agent_example.py` も同じ状態で、そちらは v2.0 で「本書内の解説用コード片」と明示済み |

> 📝 内容が必要になった場合は `git log --all --diff-filter=D -- grace/old_docs/` から復元できる。

---

## 5. 優先対応の提案（本ドキュメント作成時点の所見）

1. ~~**事実誤り（プロバイダ誤記）の修正**~~ → **完了**（2026-09-04）。8 コアモジュール分（`intervention.md` / `replan.md` /
   `tools.md`）に続き、`llm_compat.md` / `config.md` / `confidence_calibration.md` も訂正済み。誤記の **再生産元**だった
   `.claude/skills/grace-agent-docs/SKILL.md` §3 も Ollama へ是正済み。
   > 📌 `llm_compat.md` は表記だけでなく **既定経路（Ollama 系クラス）が丸ごと未記載**という欠落があり、`config.md` は **
   `llm.timeout` の既定値が実装と食い違って**いた（doc 30 / 実際 180）。表記の一致だけを確認しても見つからない種類の誤りなので、以後も
   **シンボル網羅と既定値の照合**まで行うこと。
2. ~~**`grace/memory.py` のドキュメント欠落**を埋める~~ → **完了**（2026-09-04 `memory.md` 新規作成）。
3. ~~**`grace/doc/`（単数形）への内部リンク**~~ → **完了**（2026-09-04）。
   リポジトリ全体で解消した（変更履歴に残る「`grace/doc/` → `grace/docs/` に訂正した」という
   記述だけが残る）。是正先はリンクの**ラベル**で、移設に合わせて実体のパスへ書き換えている:
   `backend/docs/agent_support_example.md`（5 件）/ `agent_support_verticals.md`（2 件）/
   `grace/old_docs/agent_example_core8.md`（2 件）/ `grace/docs/web_search.md`（3 件・本ファイルは後に `tools.md` へ統合）/
   `backend/app/core/support_agent.py`・`backend/app/core/verticals.py`・`agent_support_example.py`・
   `grace/step_trace/_trace.py` の docstring。
   `grace/step_trace/README.md` の**リンク切れ**（移設前の `../docs/agent_support_example*.md` を参照）と、
   同ファイルの実行要件表の `ANTHROPIC_API_KEY`（本リポジトリの LLM は Ollama で API キー不要）も是正済み。
4. ~~`../old_docs/benchmark.md` / `../old_docs/agent_example_core8.md` の実在確認~~ → **完了**（2026-09-04）。
   ユーザー承認のうえ 2 件とも削除し、`grace/old_docs/` ディレクトリごと無くなった（§4 参照）。
5. ~~上記以外の「要更新」判定分の追随~~ → **完了**（2026-09-04）。
   `grace_core.md` / `grace_core_flow.md` / `schemas.md` に続き、`backend/docs/` へ移設された
   `agent_support_example.md` / `agent_support_example_flow.md` / `agent_support_verticals.md` の
   3 件も棚卸しを終えた（結果は [`backend/docs/README.md`](../../backend/docs/README.md) §1）。
   `web_search.md` は `tools.md` へ統合して削除した。

---

## 5.1 残作業（TODO）

> **対象リポジトリは `grace_v2_local` と `grace_v2` の 2 つだけ。**
> `anthropic_grace_agent_v2` / `ollama_grace_agent_v2` / `grace_agent_v2_react_anthropic` /
> `openai_grace_agent` は**別プロジェクト**であり、移植元にも参照先にもしない。

**`grace/docs/` に残る作業は無い。** §1 のコアモジュール 1:1 対応ドキュメントは全 13 件が「現行」、
§2 の横断 3 点も v2.0 で最新化済み。

横断的な残作業は [`backend/docs/README.md`](../../backend/docs/README.md) **§5** に集約した:

| # | 内容 | 場所 |
|---|---|---|
| 1 | **`eval/vertical/` の扱いの決定**（対象 2 リポジトリのどちらにも存在しない） | `backend/docs/README.md` §5.3 |
| 2 | 姉妹リポジトリ `grace_v2` が抱える同種の負債（存在しないパス 31 件・単数形リンク 17 件・行番号参照 13 件・棚卸し未作成）と**実装の遅れ**（`STEP_IDS` に `analyze` 段が無い） | 同 §5.2 |
| 3 | `review_rules_collection.md` の Version ヘッダー、GRACE-Review の内部ヘルパー 4 件 | 同 §5.1 |

---

## 6. 変更履歴

| Version | 日付       | 内容                                                                                                                                                                                                                           |
|---------|------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1.6     | 2026-09-04 | §5 に「残作業（TODO）」を新設し、対象リポジトリを `grace_v2_local` / `grace_v2` の 2 つに限定することを明記。`grace/docs/` 自体に残作業は無く、横断的な TODO は `backend/docs/README.md` §5 へ集約した |
| 1.5     | 2026-09-04 | `web_search.md` を `tools.md` へ統合し削除。`grace/*.py` とドキュメントの 1:1 対応が回復し、§1 は全件「現行」になった。統合は実装との突き合わせで行い、旧稿の誤り 3 件（confidence キー・DDG パッケージ名・`max_retries`）は持ち込んでいない |
| 1.4     | 2026-09-04 | `schemas.md` を v2.0 へ最新化（未記載の公開シンボル 4 件を追加、14/14 網羅）。単数形パス `grace/doc/` をリポジトリ全体で解消し、優先対応 3 を完了に更新 |
| 1.3     | 2026-09-04 | 横断 3 点（`grace.md` / `grace_core.md` / `grace_core_flow.md`）を v2.0 へ最新化した結果を反映。**行番号参照の全廃**（`grace_core.md` 4 件・すべて実装とずれていた）と、**存在しない `agent_example.py`** の扱い確定（`grace_core_flow.md` §D を「本書内のコード例」と明示）が主眼。§3 の 3 件が `backend/docs/` へ移設済みであることを反映しリンクを修正。優先対応 3・5 を更新 |
| 1.2     | 2026-09-04 | `llm_compat.md` / `config.md` / `confidence_calibration.md` の訂正を反映し、優先対応 1 を完了に更新。8 コアモジュールを追加観点（廃止パス・禁止表記・設定既定値のドリフト）で再点検し、未修正が無いことを確認                  |
| 1.1     | 2026-09-04 | 8 コアモジュール（planner/executor/confidence/calibration/memory/intervention/replan/tools）を**日付ではなく内容**（公開シンボル網羅・プロバイダ表記・廃止ファイル参照）で再判定し、該当行を更新。`memory.md` の新規作成を反映 |
| 1.0     | 2026-09-03 | 初版作成。`grace/docs/` 全 20 ファイルの棚卸し                                                                                                                                                                                 |

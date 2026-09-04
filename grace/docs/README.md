# grace/docs/ - ドキュメント一覧・棚卸し

**Version 1.1** | 最終更新: 2026-09-04

`grace/docs/` 配下の全ドキュメントを棚卸しし、ドキュメント名・概要・重要度・必要性（現状の課題）を一覧化する。

各行の判定は、対応する `grace/*.py`（または関連ソース）の最終コミット日と、ドキュメント側の
「最終更新」ヘッダーを突き合わせ、さらに本文を実際に読んで事実誤りの有無を確認した結果である
（CLAUDE.md 冒頭「作業原則」に従い、確認していないものは「確認していない」と明記する）。

---

## 凡例

- **重要度**: 高＝現行パイプラインの中核（実行経路で常時使われる） / 中＝周辺機能・横断資料 / 低＝実装の裏付けが取れない・使用実績が薄い
- **必要性**: 現行＝内容・用語とも最新実装と一致 / 要更新＝内容は概ね正しいが実装から遅れている / 要修正＝**事実誤り**（用語・プロバイダ名等）を含み優先度高で直すべき / 要確認＝対応ソースの実在が確認できず、保持・削除の判断にユーザー確認が必要

---

## 1. コアモジュール 1:1 対応ドキュメント（`grace/*.py`）

| ドキュメント名 | 概要 | 重要度 | 必要性 |
|---|---|:--:|---|
| [`planner.md`](./planner.md) | `Planner`：質問複雑度推定・LLM/ルールベース計画生成（`create_plan`）の IPO 仕様書 | 高 | **現行**（2026-09-03 に v4.0 へ全面更新済み。`planner.py` と同期） |
| [`executor.md`](./executor.md) | `Executor`：計画実行オーケストレータ。内部RAG→reasoning、S3 ハイブリッド ReAct ループ、締切ベース実行の IPO 仕様書 | 高 | **現行**（2026-09-03 に v5.0 へ全面更新済み。`executor.py` と同期） |
| [`confidence.md`](./confidence.md) | `GroundednessVerifier`/`ConfidenceCalculator` 等：根拠検証・多軸信頼度算出の IPO 仕様書 | 高 | **現行**（2026-09-03 に v3.0 へ全面更新済み。`confidence.py` と同期） |
| [`calibration.md`](./calibration.md) | `calibration.py`：温度スケーリングによる confidence の事後較正（ECE 縮小） | 高 | **現行**（2026-09-04 v1.1 で再確認。公開シンボル 9/9 記載、LLM 非使用のためプロバイダ誤記なし。`calibration.py` は初回投入以降未変更で本書は追随済み） |
| [`intervention.md`](./intervention.md) | `intervention.py`：HITL 4 段階介入（SILENT/NOTIFY/CONFIRM/ESCALATE）管理 | 高 | **現行**（2026-09-04 v1.3 で訂正済み。概要の「Anthropic Claude」誤記を Ollama へ、§6.4 の Streamlit 前提の統合例を `InterventionBridge`（FastAPI+SSE）へ差し替え。シンボル 23/23） |
| [`memory.md`](./memory.md) | `memory.py`：実行メモリ層（P4）。実行実績からコレクション優先順位を学習（`planner` が読み、`executor` が書く） | 高 | **現行**（2026-09-04 新規作成 v1.0。公開シンボル 14 件と `MemoryConfig` の既定値を実装から確認） |
| [`llm_compat.md`](./llm_compat.md) | `llm_compat.py`：全 LLM 呼び出し（planner/executor/confidence/tools）が経由する互換アダプタ層 | 高 | **要修正**（doc 2026-08-01 ／ ソース最終更新 2026-08-14、約 13 日遅れ。**加えて概要文が「Anthropic Claude を呼び出すためのアダプター層」と誤記** — 実装済みソースの docstring は既に「Ollama（ローカル LLM）を LLM プロバイダーとする」と明記しており矛盾。CLAUDE.md §3/9.3 違反） |
| [`replan.md`](./replan.md) | `replan.py`：ステップ失敗・低信頼度時の動的リプラン（全体/部分再計画・フォールバック・スキップ・中断） | 中〜高 | **現行**（2026-09-04 v1.6 で訂正済み。プロバイダ誤記（本文＋Mermaid ノード 2 箇所）と、存在しない `agent_rag.py (Streamlit)` 参照を修正。シンボル 20/20） |
| [`schemas.md`](./schemas.md) | `schemas.py`：`ExecutionPlan`/`PlanStep`/`ExecutionResult` 等 Pydantic スキーマ定義 | 高 | 要更新（doc 2026-08-01 ／ ソース最終更新 2026-08-29、約 28 日遅れ。内容未確認） |
| [`tools.md`](./tools.md) | `tools.py`：`ToolResult` ほかツール群（内部RAG検索・Web検索・アクション実行等）の定義 | 高 | **現行**（2026-09-04 v3.0 で全面訂正。Anthropic 表記 18 箇所を Ollama へ、未記載だった `CodeExecuteTool`（opt-in）と `clear_collections_cache` を追加、Qdrant 未接続の区別・Web 検索フォールバック連鎖・`prompt_closing` の位置を反映） |
| [`web_search.md`](./web_search.md) | `WebSearchTool`（`tools.py` 内のクラス）の詳細仕様 | 中 | **要整理**（`web_search.py` という独立モジュールは存在しない。`WebSearchTool` は `tools.py` に定義されたクラスの一つで、`tools.md` と内容が重複しうる。ファイル名が実体と乖離しており、`tools.md` への統合、または「`tools.py` 内の章である」旨を明記するリネームを検討） |
| [`config.md`](./config.md) | `config.py`：LLM/Embedding/信頼度/介入/リプラン/コスト/Qdrant 等の Pydantic 階層設定 | 高 | **要修正**（doc 2026-08-01 ／ ソース最終更新 2026-08-30、約 29 日遅れ。**加えて概要文が「LLM（Anthropic Claude）」と誤記** — 現行既定は Ollama。CLAUDE.md §3/9.3 違反） |

**このカテゴリの欠落は解消済み**: `grace/memory.py` のドキュメントが存在しなかったが、2026-09-04 に [`memory.md`](./memory.md) を新規作成した（IPO 形式・公開シンボル 14 件を実装から確認）。

---

## 2. 横断・アーキテクチャ概説ドキュメント

| ドキュメント名 | 概要 | 重要度 | 必要性 |
|---|---|:--:|---|
| [`grace.md`](./grace.md) | GRACE 自律型エージェントの設計思想・アーキテクチャ概説（「入口となる傘ドキュメント」の位置づけ）。ReAct→Reflection→GRACE の経緯 | 高（オンボーディング資料として） | 要修正（**最終更新日ヘッダー自体が存在せず更新管理外**。加えて本文中に廃止済みの単数形パス `grace/doc/confidence.md` 等への内部リンクが残存 — 実ディレクトリは `grace/docs/`。CLAUDE.md §9.1 違反） |
| [`grace_core.md`](./grace_core.md) | 8 コアモジュール（planner/executor/confidence/calibration/memory/intervention/replan/tools）の横断アーキテクチャ・構成図・IPO リンク集 | 中 | 要更新（doc 2026-06-28 で停止。GA/GA'・S3 ReActループ等それ以降の大幅な変更が未反映と見られる。`grace/doc/` 誤リンクあり。個別 docs との内容重複あり） |
| [`grace_core_flow.md`](./grace_core_flow.md) | `grace_core.md` の姉妹編。自律 Agent の 5 段階設計と最小実行サンプル `agent_example.py` の行ごと解説 | 中 | 要更新・**要確認**（doc 2026-06-28 で停止に加え、解説対象の `agent_example.py` が git 全履歴上確認できない。`grace/doc/` 誤リンクあり） |

---

## 3. GRACE-Support（`agent_support_example.py`）関連ドキュメント

| ドキュメント名 | 概要 | 重要度 | 必要性 |
|---|---|:--:|---|
| [`agent_support_example.md`](./agent_support_example.md) | GRACE-Support 本体（v1〜v3＋業界特化）の設計書。回答判定フロー・groundedness ゲート・データ契約・ActionTool 仕様 | 高 | 要更新（doc 2026-07-08 ／ `agent_support_example.py` 最終更新 2026-08-21、約 44 日遅れ。現行実装は `backend/app/core/support_agent.py` 経由の 0-(A)/0-(B) 質問分析等が追加されており、本 doc は旧世代の設計を記述） |
| [`agent_support_example_flow.md`](./agent_support_example_flow.md) | `agent_support_example.md` の姉妹編。`--vertical gov` 実行 1 本のステップ別トレース（モジュール・コード・IN/OUT データ） | 高（デバッグ時の実用性が高い） | 要更新（同上、44 日遅れ。S3 ReActループ等の反映状況は未確認） |
| [`agent_support_verticals.md`](./agent_support_verticals.md) | GRACE-Support 業界特化（自治体/SaaS/EC）設計書。`VerticalProfile`・しきい値・エスカレ語・アクション対応 | 高 | 要更新（doc 2026-07-11 ／ `verticals.py` 最終更新 2026-08-30、約 50 日遅れ。GA'（担当範囲判定・`SCOPE_POLICY`）等の追加が未反映の可能性が高い） |
| [`confidence_calibration.md`](./confidence_calibration.md) | `confidence.py`×`calibration.py` の横断整理（処理順・データフロー）。個別 docs を補うアーキテクチャ資料 | 中 | **要修正**（**概要文に「技術スタック: LLM = Anthropic Claude」の誤記あり** — CLAUDE.md §3/9.3 違反。加えて `confidence.md` が本セッションで v3.0 へ全面刷新されたため、二重に内容が陳腐化） |

---

## 4. 実在が確認できないドキュメント（要確認）

以下 2 件は、記述対象のスクリプトが **git 全履歴（`git log --all --full-history`）を通じて一度も見つからない**。
本リポジトリ内で書かれてから削除された形跡もないため、姉妹リポジトリ（`grace_v2` 等）からの文書コピー、
または実装されないまま構想段階で書かれた設計書である可能性が高い。**保持するか削除するかはユーザー確認が必要。**

| ドキュメント名 | 概要 | 重要度 | 必要性 |
|---|---|:--:|---|
| [`benchmark.md`](./benchmark.md) | `run_benchmark.py --fast` の実行ログ・ベンチマーク集計の解説 | 低 | **要確認・削除候補**（`run_benchmark.py` が git 履歴上確認できず。文中のコレクション名 `cc_news_2per_anthropic` も現行命名規則 `*_ollama` と矛盾しており、少なくとも現行実装との対応は取れていない） |
| [`agent_example_core8.md`](./agent_example_core8.md) | コア 8 モジュールを明示的に呼び出す最小サンプル `agent_example.py` の設計書 | 低 | **要確認・削除候補**（`agent_example.py` が git 履歴上確認できず。`grace_core_flow.md` §D が参照する同名スクリプトも同じ状態） |

---

## 5. 優先対応の提案（本ドキュメント作成時点の所見）

1. **事実誤り（プロバイダ誤記）の修正**: 2026-09-04 に **8 コアモジュール分（`intervention.md` / `replan.md` / `tools.md`）は訂正済み**。⚠️ **未対応が残る**: `llm_compat.md` / `config.md` / `confidence_calibration.md` は依然「LLM = Anthropic Claude」等の誤記を含む（CLAUDE.md §3・§9.3 違反）。
   併せて、誤記の**再生産元**だった `.claude/skills/grace-agent-docs/SKILL.md` §3 を Ollama へ是正済み。
2. ~~**`grace/memory.py` のドキュメント欠落**を埋める~~ → **完了**（2026-09-04 `memory.md` 新規作成）。
3. **`grace/doc/`（単数形）への内部リンク**が `grace.md` / `grace_core.md` / `grace_core_flow.md` / `agent_support_example.md` / `agent_support_verticals.md` / `agent_example_core8.md` に残存 — 実ディレクトリ `grace/docs/`（複数形）に合わせて一括修正する（CLAUDE.md §9.1）。
4. `benchmark.md` / `agent_example_core8.md` の実在確認をユーザーに依頼し、削除または「構想止まりの設計書」である旨の明記を行う。
5. 上記以外の「要更新」判定分（schemas.md / agent_support_example*.md / agent_support_verticals.md / grace_core*.md）は、対応ソースの実装差分を精査した上で内容の追随を行う。

---

## 6. 変更履歴

| Version | 日付 | 内容 |
|---|---|---|
| 1.1 | 2026-09-04 | 8 コアモジュール（planner/executor/confidence/calibration/memory/intervention/replan/tools）を**日付ではなく内容**（公開シンボル網羅・プロバイダ表記・廃止ファイル参照）で再判定し、該当行を更新。`memory.md` の新規作成を反映 |
| 1.0 | 2026-09-03 | 初版作成。`grace/docs/` 全 20 ファイルの棚卸し |

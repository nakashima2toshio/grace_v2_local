# backend/tests/ — テストスイート索引

**Version 1.7** | 最終更新: 2026-10-04

---

## 目次

- [概要](#概要)
- [1. 実行方法](#1-実行方法)
- [2. 構成と件数（実測 2026-09-10）](#2-構成と件数実測-2026-09-10)
- [3. conftest](#3-conftest)
- [4. 既定でスキップされる 46 件](#4-既定でスキップされる-46-件)
  - [4.1 結合テスト（`integration/`・実 Qdrant / Redis）](#41-結合テストintegration実-qdrant--redis)
  - [4.2 E2E（`e2e/`・実 LLM・実データ・Mac 専用）](#42-e2ee2e実-llm実データmac-専用)
- [5. テストを追加するときの約束](#5-テストを追加するときの約束)
- [6. GRACE-Review 系テストの地図（18 ファイル・実測 2026-09-16）](#6-grace-review-系テストの地図18-ファイル実測-2026-09-16)
- [7. 変更履歴](#7-変更履歴)

---

## 概要

CI の `pytest (backend)` ゲートが実行する唯一のテストツリー。
`pyproject.toml` の `testpaths = ["backend/tests"]` がこのディレクトリを指す。

> 本ファイルは、削除した `tests/README.md`（Gemini 時代の索引。プロバイダ・パス・
> 件数すべてが現状と食い違っていた）の置き換えである。**下表の件数は実行して数えた
> 実測値**（2026-09-10）で、記憶で書いていない。

### 結論

- 実行は `PYTHONPATH=. uv run pytest backend/tests -q -rs`（§1）。実 Ollama・Qdrant は不要
- 既定でスキップされる 46 件の内訳（旧 Gemini 版のレガシーテスト 14 件、`integration/` の結合テスト 18 件、`e2e/` の E2E 6 件と、実キー・稼働中 Qdrant・稼働中 Ollama などを要する統合テスト）は §4
- **`integration/`（§4.1）は Qdrant / Redis が起動していれば走る。** クラウド VM では SessionStart hook が両方を起動する
- **`e2e/`（§4.2）は Mac で `GRACE_E2E=1` を付けたときだけ走る**（実 Ollama・実 Gemini Embedding・実データ）
- テストを足すときの約束は §5、GRACE-Review 系の地図は §6

### 対象モジュール

| # | モジュール | 関係 |
|---|---|---|
| 1 | `backend/tests/` | テスト本体（§2 の構成と件数） |
| 2 | `backend/tests/conftest.py` | 共通フィクスチャ（§3） |
| 3 | `pyproject.toml`（`testpaths`・`markers`） | CI の `pytest (backend)` ゲートが読むテストツリーの指定と `integration` マーカー |
| 4 | `backend/tests/integration/` | 実 Qdrant / Redis の結合テスト（§4.1） |
| 5 | `.claude/hooks/session-start.sh` | クラウド VM でテスト依存を入れ、Qdrant / Redis を起動する |
| 6 | `backend/tests/e2e/` / `requirements-e2e.txt` | E2E（§4.2）とその追加依存 |

---

## 1. 実行方法

```bash
# 全件（CI と同じコマンド）
PYTHONPATH=. uv run pytest backend/tests -q -rs

# ディレクトリ単位
uv run pytest backend/tests/grace -v
uv run pytest backend/tests/services -v

# 単一ファイル / 単一テスト
uv run pytest backend/tests/test_data_jobs.py -v
uv run pytest backend/tests/test_model_info_api.py::TestModelEndpoint -v
```

> `pyproject.toml` に `pythonpath` 指定は無い。CI は `PYTHONPATH=.` を env で与えている。
> `python backend/tests/x.py` を直接叩くと `ModuleNotFoundError: No module named 'backend'`
> になる → `uv run python -m backend.tests.x` を使う。

### ⚠️ ローカルで通っても CI が通るとは限らない

開発用 venv は `uv sync --extra dev` で `pyproject.toml` の全依存が入るが、
CI の pytest ジョブは `.github/workflows/ci.yml` に**明示列挙したリストだけ**を入れる。
テストが新しい import を持ち込んだときは、そのリストだけの venv を作って実際に走らせる。

```bash
python3 -m venv /tmp/civenv
# ci.yml の pip install 行をそのままコピーして実行
PYTHONPATH=. /tmp/civenv/bin/pytest backend/tests -q -rs
```

実例 2026-09-10: `spacy` は `pyproject.toml` にはあったが ci.yml のリストに無く、
移設した `qa_generation/test_keyword_extraction.py` が collection error になった。

---

## 2. 構成と件数（実測 2026-09-10）

| ディレクトリ | ファイル | テスト | 内容 |
|---|---:|---:|---|
| `backend/tests/`（直下） | 87 | 1389 | イベント駆動コア・HITL ブリッジ・FastAPI・判定ゲート・データ準備ジョブ・回帰テスト |
| `grace/` | 18 | 263 | GRACE 自律エージェント（planner / executor / confidence / replan / intervention / memory / tools / schemas / config） |
| `services/` | 10 | 59 | サービス層（cache / config / dataset / file / json / log / qa / token / agent / qdrant） |
| `legacy/` | 4 | 56 | 旧構成向けレガシーテスト（`conftest.py` が `temp_dir` 等を提供） |
| `qa_generation/` | 7 | 31 | Q/A 生成パイプライン（semantic / evaluation / structure / keyword / 逐次永続化） |
| `helpers/` | 2 | 23 | プロバイダ抽象化（`helper/helper_llm.py` / `helper/helper_embedding.py`） |
| `chunking/` | 1 | 5 | チャンキング（最大トークン強制分割） |
| `agents/` | 1 | 1 | エージェントの実 API 結合テスト（既定でスキップ・後述） |
| **合計** | **130** | **1807 passed, 22 skipped** | |

`backend/tests` は `__init__.py` を持つパッケージで、各サブディレクトリも同様。

---

## 3. conftest

| ファイル | 提供するもの |
|---|---|
| `backend/tests/conftest.py` | `pipeline_stub` / `review_stub` / `review_hitl_stub`。`run_support_agent_core` と `review_agent` の外部依存（planner / executor / verifier / tools / LLM 判定器）をスタブへ差し替え、実 API キー・Qdrant・実 LLM なしで配線を検証できるようにする |
| `backend/tests/grace/conftest.py` | `sample_plan` / `mock_qdrant_client` ほか。**`LLM_PROVIDER` は設定しない**（`os.environ` はプロセス全体で共有され、`config.py` も `helper/helper_llm.py` も import 時に読むため、他のテストの既定プロバイダまで変えてしまう）。`EMBEDDING_PROVIDER=gemini` / `GOOGLE_API_KEY` は Embedding 用途で正しい |
| `backend/tests/legacy/conftest.py` | `temp_dir` / `qa_output_dir` / `sample_qa_df` / `sample_text_df` |

---

## 4. 既定でスキップされる 46 件

CI（Qdrant / Redis も Ollama も無い）での実測（2026-10-03）: `2068 passed, 46 skipped`。

| 件数 | 対象 | ゲート |
|---:|---|---|
| 14 | `legacy/test_agent_service_legacy.py` | 旧 Gemini 版エージェントのテスト。`services/test_agent_service.py` が後継 |
| 2 | `grace/test_executor_integration.py` | `RUN_AGENT_INTEGRATION=1` ＋ 稼働中 Ollama ＋ 稼働中 Qdrant ＋ 実 `GOOGLE_API_KEY`（Embedding） |
| 2 | `grace/test_planner_integration.py` | `RUN_AGENT_INTEGRATION=1` ＋ 稼働中 Ollama（LLM が代替値へ倒れたら fail する） |
| 6 | `e2e/test_support_e2e.py`（3）/ `e2e/test_review_e2e.py`（3） | `GRACE_E2E=1` ＋ `GOOGLE_API_KEY` ＋ 稼働中 Ollama（モデル pull 済み）＋ Qdrant に実データ（§4.2） |
| 18 | `integration/test_*_live.py`（3 ファイル） | 稼働中 Qdrant / Redis（§4.1）。`GRACE_SKIP_INTEGRATION=1` で強制 skip |
| 1 | `test_collection.py` | 稼働中 Qdrant（localhost:6333）**かつ登録済みコレクションがある**こと（2026-10-03 に追加。空の Qdrant では 0 件の assert で fail していた） |
| 1 | `test_helper_llm_step1.py` | `RUN_GEMINI_LLM_LIVE=1` ＋ 実 Gemini API キー（後方互換の `GeminiClient` を実 LLM API で呼ぶ。キーだけで走らせると、Embedding 用のキーを持つ全員が pytest のたびに課金されるため） |
| 1 | `agents/test_agent_service_paris_income.py` | `RUN_AGENT_INTEGRATION=1` ＋ 稼働中 Ollama ＋ 稼働中 Qdrant |
| 1 | `test_config_file_and_memory.py` | `logs/` が存在する環境のみ（gitignore 対象） |

### `agents/test_agent_service_paris_income.py` の走らせ方

```bash
ollama serve
docker-compose -f docker-compose/docker-compose.yml up -d
RUN_AGENT_INTEGRATION=1 uv run pytest \
  backend/tests/agents/test_agent_service_paris_income.py -q -s
```

`wikipedia_ja` コレクションが Qdrant に登録済みであることが前提。

> ⚠️ **既知の負債**: `grace/test_executor_integration.py` と
> `grace/test_planner_integration.py` のスキップ理由はまだ
> `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` を名指ししている（移植前の名残）。
> 本リポジトリの LLM は Ollama（CLAUDE.md §3）なので、走らせる条件としては正しくない。

### 4.1 結合テスト（`integration/`・実 Qdrant / Redis）

`backend/tests/integration/` は**スタブを使わず**、`docker-compose/docker-compose.yml` の
Qdrant（:6333）と Redis（:6379）に実際に接続する。API キーも Ollama も使わない
（Embedding は固定ベクトル、LLM は固定応答の生成器へ差し替える）。grace_v2 と同じテストである。

```bash
# Mac: Docker Desktop で起動してから
docker compose -f docker-compose/docker-compose.yml up -d
PYTHONPATH=. uv run pytest backend/tests/integration -q -rs

# クラウド VM（Claude Code on the web）: .claude/hooks/session-start.sh が
# テスト依存（.venv）と Qdrant / Redis を用意済み
PYTHONPATH=. .venv/bin/python -m pytest backend/tests/integration -q -rs
```

| 状況 | 挙動 |
|---|---|
| Qdrant / Redis が起動している | 走る（18 件・約 5 秒） |
| 起動していない（CI・Docker を止めた Mac） | **skip**（理由に起動コマンドを出す） |
| `GRACE_SKIP_INTEGRATION=1` | 起動していても skip |
| `-m "not integration"` | 収集から外す（`deselected`） |

> ⚠️ **共用 Qdrant（grace_v2 と同じもの）を壊さない。** 作るコレクションは
> `grace_it_<乱数>` だけで、テストごとに削除する。Redis は **db 15** を使い
> （Celery の既定は db 0）、Celery アプリのキャッシュ済み接続を捨てて
> **db 15 を向いたことを assert してから**投入する。

| テスト | 見ていること |
|---|---|
| `test_qdrant_live.py`（12 件） | コレクション作成（sparse・`domain` 索引・recreate）、`stable_point_id` による再登録の冪等性、`search_collection` の経路選択（dense / sparse 未設定なら hybrid を投げない / hybrid で sparse が順位を変える / 無いコレクションは `[]`）、一覧と閲覧 |
| `test_register_to_qdrant_live.py`（4 件） | `register_to_qdrant` の CSV → Qdrant 登録（重複行は Embedding 前に落とす・Embedding メタデータ・再登録の冪等性・先読みパイプライン） |
| `test_celery_redis_live.py`（2 件） | テスト内で本物の Celery ワーカー（solo）を立て、投入 → Redis → タスク → Redis → `collect_results` を往復させる（本番の `rate_limit` はテスト中だけ外す。効いたままだと 3 タスクで約 10 秒） |

**実効性の確認（2026-10-03）**: `stable_point_id` を乱数にすると 2 件、`register_to_qdrant` の
重複除去を外すと 1 件が fail することを確かめた。

**実測（2026-10-03・クラウド VM・サービス起動中）**: `2086 passed, 28 skipped`
（結合 18 件が走り、空の Qdrant なので `test_collection.py` は skip。E2E 6 件も skip）。

### 4.2 E2E（`e2e/`・実 LLM・実データ・Mac 専用）

`backend/tests/e2e/` は、**本物の Ollama・Gemini Embedding と Mac の Qdrant の実データ**で
`run_support_agent_core` / `run_review_agent_core` を丸ごと流す。grace_v2 と同じケース・同じ期待値で、
前提（LLM が Ollama）だけが違う。`GRACE_E2E=1` を付けたときだけ走る（CI は skip）。

```bash
ollama serve                                  # 別ターミナル
uv pip install -r requirements-e2e.txt        # 初回（fastembed / ddgs）
GRACE_E2E=1 PYTHONPATH=. uv run --no-sync pytest backend/tests/e2e -m e2e -rs   # 結果は logs/e2e/*.json
```

| ケース | 入力（画面の例文ボタンから読む） | 期待 |
|---|---|---|
| Support / gov | 住民票の写しの取り方は？ | `answer`・社内ナレッジの出典あり・根拠検証で判定できた主張 > 0 |
| Support / saas | サービスが落ちています | エスカレーション語で強制エスカレ → `escalate_to_human`。Web の出典が混ざらない |
| Support / ec | 返品したい | アクションあり・社内ナレッジの出典あり・判定とアクションが一致・本人確認を通る |
| Review / 化粧品LP案 | NG 例 | 指摘 ≥ 1・high ≥ 1 |
| Review / 表記漏れLP案 | NG 例 | `tokusho-01`（送料の欠落）が出る |
| Review / 適正LP案 | OK 例 | **指摘 0 件** |

- Web 検索は既定で使わない（`GRACE_E2E_USE_WEB=1` で使う）。Support の各テストは、このとき **Web を検索していない（`used_web=False`）・Web の出典が無い**ことも確かめる（2026-10-04 までは `use_web=False` が ⑤ しか止めず、grace_v2 の E2E で saas に無関係な URL が 9 件並んだ。executor の全経路で止めるよう直した。`test_web_search_toggle.py` / `test_uncited_web_citations.py`）。
- 文面は `QueryForm.tsx` / `ReviewForm.tsx` から読む（`cases.py`）。期待値とのずれは `test_e2e_cases.py`（CI で走る）が検出する。
- **クラウド VM では走らない**（Ollama が無い）。実データの持ち運び（スナップショット）は grace_v2 の
  `scripts/qdrant_snapshot.py` が担う（Qdrant は共用なので、本リポジトリには置かない）。

> ⚠️ **LLM が失敗してもパイプラインは例外を出さず、安全側の結果を返す**（Support はエスカレ、
> Review は全ルールを「自動判定に失敗したため要確認」で残す）。素朴な期待値だと Ollama が落ちていても
> 合格してしまう（grace_v2 でダミーキーにより実測: 6 件中 4 件）。対策は 2 段:
>
> 1. `e2e_ready` が最初に Ollama の `/v1/models` に**使うモデルが pull 済みか**と、Gemini Embedding を確かめる。
>    駄目なら全件 ERROR（実測: 偽 Ollama でモデル無し → 「`ollama pull gemma4:26b-a4b-it-qat`」と出る）
> 2. `api_errors` が実行中の LLM / Embedding エラーのログを拾い、各テストは 1 件でもあれば fail
>    （実測: 1 を外し、チャットが 500 を返す偽 Ollama で 6 failed）

---

## 5. テストを追加するときの約束

- **判定ロジックは純関数へ出す。** フロントは `frontend/src/state/`（CLAUDE.md §6）、
  バックエンドは `backend/app/core/gates.py` / `review_gates.py` に判定を置き、
  テストはそこを直接叩く。
- **回帰修正には、修正前のコードで fail するテストを付ける。** fail しないテストは
  回帰を捕まえていない（CLAUDE.md 冒頭「作業原則」）。
- **モデル名を literal で書かない。** 既定モデルは
  `config.py::get_default_ollama_model()` の 1 箇所で管理する。テストに固定値を書くと
  真実の源が 2 つになり、既定を変えるたびに的外れに落ちる。
- **環境の有無で結果が変わるテストを書かない。** 「CI には Ollama も API キーも無いから
  緑」というテストは、開発者の手元でだけ落ちる。外部に触れるものは必ずスタブするか、
  §4 のように明示的なゲートを付ける。

---

## 6. GRACE-Review 系テストの地図（18 ファイル・実測 2026-09-16）

**`backend/tests` 直下の約 1/5 が Review 系**である。共用部品
（`GroundednessVerifier` / `InterventionBridge` / `support_actions.py` / `core/jobs.py` /
`gates.py::_match_keyword` / `judge_model`）を触ったら必ず流すこと。

| テスト | 件数 | 対象 |
|---|---:|---|
| `test_review_agent_core.py` | 57 | パイプライン S1・①〜⑦ の配線と KPI カウンタ（① Segment の `split_segments` もここ） |
| `test_review_gates.py` | 36 | しきい値による status 判定・救済・severity 調整・強制 high |
| `test_rulesets.py` | 24 | `RuleSet` / `RuleItem` の整合（`always_check` と `keywords` の排他ほか） |
| `test_review_api.py` | 21 | submit / stream / confirm / result の応答、422 ガード |
| `test_review_policy_evidence.py` | 15 | `policy-01`（社内規程）の根拠検索の上書き |
| `test_review_safety_claim.py` | 14 | `yakki-04`（安全性の保証表現） |
| `test_review_document_scope.py` | 13 | 文書全体スコープ（`always_check`）の扱い |
| `test_review_yakki_product_scope.py` | 12 | 薬機法ルールの主題限定 |
| `test_review_detect_criteria_in_prompt.py` / `test_review_evidence_threshold.py` / `test_review_evidence_top_ratio.py` / `test_review_rule_subject_scope.py` | 各 11 | ③ Detect のプロンプト、② Retrieve の根拠しきい値、ルール主題の限定 |
| `test_review_document_excerpt.py` | 10 | 文書全体スコープの指摘の抜粋 |
| `test_review_multi_item_rules.py` / `test_review_undecided_groundedness.py` | 各 9 | 複数項目ルール、判定できていない groundedness の扱い |
| `test_review_absence_excerpt.py` / `test_review_ground_sources.py` | 各 7 | 表記漏れ指摘の抜粋、④ Ground の出典 |
| `test_review_no_duplicate_findings.py` | 5 | 重複指摘の抑止 |
| `test_review_facts.py` | 15 | 文字列で決まる事実（`review_facts.py`）: 購入時の送料の有無、返品条件が規程より不利でないか |
| `test_review_detect_failure_status.py` | 3 | ③ Detect 判定失敗時の安全側（`review_required`） |

**過検知の回帰テスト**を重視している（`backend/tests/data/` の 3 サンプル）。

| サンプル | 期待 |
|---|---|
| `ec_ad_ng_sample.txt` | 意図的に違反を仕込んだ LP |
| `ec_ad_ok_sample.txt` | 適正表記の LP → **指摘 0 件**（過検知テスト） |
| `ec_ad_edge_sample.txt` | 否定文脈の「No.1」等 → **強制 high にしない**（誤検知抑止テスト） |

あわせて `test_jobs_generic.py`（18 件）が、**ジョブ基盤の汎用化で Support の既存挙動が
変わらないこと**を回帰として固定している。

> 📝 **設計時の想定と実装は一致していない。** 統合前の `review_agent_spec.md` §9 は
> `test_review_segment.py` を挙げていたが、**そのファイルは存在しない**（実測 2026-09-16）。
> ① Segment の検証は `test_review_agent_core.py`（`split_segments` を直接呼ぶ）にある。

> フロントは `vitest` で `reviewReducer` とハイライト分割ロジックを対象にする
> （`frontend/src/**/*.test.ts`。**`.test.tsx` は収集されない**）。

---

## 7. 変更履歴

| Version | 日付 | 変更内容 |
|---|---|---|
| 1.7 | 2026-10-04 | `use_web=False` で executor が Web を検索していた不具合の修正（grace_v2 から移植）に合わせ、§4.2 の Support の E2E に「Web を検索していない・Web の出典が無い」確認を追記 |
| 1.6 | 2026-10-03 | §4.2 E2E（`e2e/`・Mac 専用・画面の例文を実 Ollama・実データで流す）を追加し、§4 を 46 件に更新 |
| 1.5 | 2026-10-03 | §4.1 結合テスト（`integration/`・実 Qdrant / Redis・未起動なら skip）を追加し、§4 を 40 件に更新。`test_collection.py` は登録済みコレクションが無ければ skip する（クラウド VM の空の Qdrant で fail しないように） |
| 1.4 | 2026-10-03 | §3 に `test_review_facts.py`（15 件）を追加 |
| 1.3 | 2026-09-26 | §4 のゲートを実装に合わせた。`grace/test_planner_integration.py` / `test_executor_integration.py` は `RUN_AGENT_INTEGRATION=1` ＋ 稼働中 Ollama へ（2026-09-26 の是正）、`test_helper_llm_step1.py` は `RUN_GEMINI_LLM_LIVE=1` を追加（Embedding 用のキーだけで Gemini LLM API を呼んでいた） |
| 1.2 | 2026-09-24 | `a_cross_doc_md_format.md` v1.1（種別 B）に準拠（2026-09-24）。概要（結論・対象モジュール）を追加し、冒頭の説明文を概要へ移した。H2 が 7 個あるため目次も追加した。本文の章番号は変えていない |
| 1.1 | 2026-09-16 | 文書再編 Phase 2 に伴い、`review_flow.md` §9（テスト方針）を §8 として取り込み、**実測したファイル別件数**へ置き換えた（設計時に挙がっていた `test_review_segment.py` が存在しないことも明記） |
| 1.0 | 2026-09-10 | 初版（削除した `tests/README.md` の置き換え） |

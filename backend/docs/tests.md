# backend/tests/ — テストスイート索引

**Version 1.2** | 最終更新: 2026-09-24

---

## 目次

- [概要](#概要)
- [1. 実行方法](#1-実行方法)
- [2. 構成と件数（実測 2026-09-10）](#2-構成と件数実測-2026-09-10)
- [3. conftest](#3-conftest)
- [4. 既定でスキップされる 22 件](#4-既定でスキップされる-22-件)
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
- 既定でスキップされる 22 件の内訳（旧 Gemini 版のレガシーテスト 14 件と、実キー・稼働中 Qdrant・稼働中 Ollama などを要する統合テスト）は §4
- テストを足すときの約束は §5、GRACE-Review 系の地図は §6

### 対象モジュール

| # | モジュール | 関係 |
|---|---|---|
| 1 | `backend/tests/` | テスト本体（§2 の構成と件数） |
| 2 | `backend/tests/conftest.py` | 共通フィクスチャ（§3） |
| 3 | `pyproject.toml`（`testpaths`） | CI の `pytest (backend)` ゲートが読むテストツリーの指定 |

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

## 4. 既定でスキップされる 22 件

| 件数 | 対象 | ゲート |
|---:|---|---|
| 14 | `legacy/test_agent_service_legacy.py` | 旧 Gemini 版エージェントのテスト。`services/test_agent_service.py` が後継 |
| 2 | `grace/test_executor_integration.py` | 実キー ＋ 稼働中 Qdrant |
| 2 | `grace/test_planner_integration.py` | 実キー |
| 1 | `test_collection.py` | 稼働中 Qdrant（localhost:6333） |
| 1 | `test_helper_llm_step1.py` | 実 Gemini API キー |
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
| 1.2 | 2026-09-24 | `a_cross_doc_md_format.md` v1.1（種別 B）に準拠（2026-09-24）。概要（結論・対象モジュール）を追加し、冒頭の説明文を概要へ移した。H2 が 7 個あるため目次も追加した。本文の章番号は変えていない |
| 1.1 | 2026-09-16 | 文書再編 Phase 2 に伴い、`review_flow.md` §9（テスト方針）を §8 として取り込み、**実測したファイル別件数**へ置き換えた（設計時に挙がっていた `test_review_segment.py` が存在しないことも明記） |
| 1.0 | 2026-09-10 | 初版（削除した `tests/README.md` の置き換え） |

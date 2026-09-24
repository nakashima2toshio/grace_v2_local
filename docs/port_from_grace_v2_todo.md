# grace_v2 → grace_v2_local 移植 TODO

**Version 1.5** | 作成: 2026-09-20 | 最終更新: 2026-09-24

---

> **本書の位置づけ**: 姉妹リポジトリ `grace_v2`（Anthropic 版）に入っていて本リポジトリ
> （Ollama 版）に入っていない**コード上の修正**を洗い出し、移植の要否・手順・
> プロバイダ差の扱いを 1 本にまとめたもの。
>
> **比較の実測日**: 2026-09-20。`grace_v2` master `fdefb8d` と
> `grace_v2_local` master `2a89392` を `diff -r` および AST（def/class 名）で突き合わせた。
> 以降のコミットで状況は変わるので、着手前に再確認すること。

---

## 目次

- [0. 結論（先に全体像）](#0-結論先に全体像)
- [1. プロバイダ差の整理（移植判断の物差し）](#1-プロバイダ差の整理移植判断の物差し)
- [2. A. 実バグ修正（最優先）](#2-a-実バグ修正最優先)
- [3. B. アクセシビリティ修正](#3-b-アクセシビリティ修正)
- [4. C. フロント機能（`state/` の純関数 4 本 ＋ コンポーネント 1 本）](#4-c-フロント機能state-の純関数-4-本--コンポーネント-1-本)
- [5. D. テストのみ移植（コードは本リポジトリにも入っている）](#5-d-テストのみ移植コードは本リポジトリにも入っている)
- [6. E. 掃除](#6-e-掃除)
- [7. F. 移植しない（理由つき）](#7-f-移植しない理由つき)
- [8. 実施順序の提案](#8-実施順序の提案)
- [9. 移植で壊してはいけない「本リポジトリにしかないもの」](#9-移植で壊してはいけない本リポジトリにしかないもの)
- [10. 検証（どの TODO でも共通）](#10-検証どの-todo-でも共通)
- [11. 実施記録](#11-実施記録)
- [変更履歴](#変更履歴)

---

## 0. 結論（先に全体像）

| 区分 | 件数 | 状態 | 内容 |
|---|:--:|:--:|---|
| **A. 実バグ修正（コードが必要）** | 2 | ✅ **完了**（2026-09-20） | 実行メモリが除外リストを素通り／自己評価に質問を渡していない |
| **B. アクセシビリティ修正** | 2 | ✅ **完了**（2026-09-20） | `CollectionPanel` の中止バナー／`ReviewForm` の上限超過通知 |
| **C. フロント機能（5 ファイル）** | 4 | ✅ **完了**（2026-09-20） | `formMemory` / `metaFetch` / `timelineAnnounce` / `documentLimit`（＋ `MetaErrorBanner`） |
| **D. テストのみ移植（コードは既にある）** | 5 | ✅ **完了**（2026-09-20） | `test_rag_adoption` / `test_no_info_judge` / `test_observability` / `test_silent_failures` / `test_chunking_abort` |
| **E. 掃除** | 2 | ✅ **完了**（2026-09-20） | ① 死にコード `services/dataset_service.py` / `file_service.py` の削除　② `streamlit` / `altair` / `pydeck` 依存の削除とドキュメント是正 |
| **F. 移植しない（プロバイダ差・設計差）** | 3 | — | `/api/model` のモデル表設計／`ModelChoice` の単価・上限／`test_model_table_coverage` |

**A〜E はすべて 2026-09-20 に実施済み**（§11 に結果）。F は「移植しない」と結論済み。

---

## 1. プロバイダ差の整理（移植判断の物差し）

移植の可否は、対象コードが**どの層に属するか**で決まる。

| 層 | grace_v2 | grace_v2_local | 移植の可否 |
|---|---|---|---|
| **判断ロジック**（ゲート・メモリ・計画・信頼度の使い方） | 共通 | 共通 | ✅ **そのまま移植できる**。A・D はここ |
| **フロント（判断は `state/` の純関数）** | 共通 | 共通 | ✅ 移植できる。B・C はここ |
| **LLM クライアント** | `helper_llm.py` の Anthropic 実装（437 行） | 同 Ollama 実装（1,373 行） | ❌ 別物。触らない |
| **モデル名・単価・上限** | `claude-*` ＋ `MODEL_PRICING` | `gemma4:*` ほか。**ローカル実行でコストは 0** | ❌ 移植しない（F） |
| **構造化出力・出力上限パラメータ** | `max_output_tokens` ほか | `max_tokens` のみ・`json_schema` 必須 | ❌ 触らない（CLAUDE.md §3「Ollama 固有の落とし穴」） |
| **Embedding** | Gemini | **Gemini（同じ）** | — 差が無い |

> ⚠️ **A の 2 件は「LLM に何を渡すか」であってプロバイダ非依存**である。
> プロンプトに質問文を足す・推薦候補から除外リストを外す、という処理で、
> Anthropic / Ollama どちらでも同じ意味を持つ。

---

## 2. A. 実バグ修正（最優先）

### A-1. 実行メモリの推薦が `excluded_collections` を素通りする

| | |
|---|---|
| **grace_v2 の該当** | `grace/planner.py::_is_excluded()` ＋ `_prioritized_collection()` が `best_collection(exclude=...)` を渡す／`grace/memory.py::best_collection(exclude=...)` |
| **本リポジトリの現状（実測）** | `planner.py` に `_is_excluded` が**無い**。`memory.py::best_collection()` に `exclude` 引数が**無い**（シグネチャは `query` / `min_count` / `min_score` のみ） |
| **影響** | 運用者が `qdrant.excluded_collections` で外した汎用コーパスを、実行メモリが「過去に当たった」という理由で推薦しうる。推薦値は `PlanStep.collection` に入り、`RAGSearchTool` 側では**明示指定と区別が付かない**ため除外を通り抜け、毎回無駄に検索される |
| **なぜ気づけないか** | 例外も警告も出ない。検索結果が少し悪くなるだけ |

**作業**

1. `grace/memory.py::best_collection()` に `exclude: Optional[Callable[[str], bool]] = None` を追加し、
   候補を走査するループで `exclude(name)` が真のものを**飛ばして次点を採る**（`None` で諦めない）。
2. `grace/planner.py` に `_is_excluded()` を追加（`config.qdrant.excluded_collections` への部分一致）。
   本リポジトリにも `excluded_collections` 設定は既にある（`grace/config.py:268`）。
3. `_prioritized_collection()` から `exclude=self._is_excluded` を渡す。
4. `backend/tests/test_memory_exclusion.py` を移植する。

> ⚠️ **「除外に当たったら None を返す」にしないこと。** 誤学習が首位に居座ると
> メモリ機構が毎回 `None` になり事実上死ぬ。**次点へ進む**のが正しい（grace_v2 のコメント参照）。

**検証**: 修正前のコードにテストを当てて **fail することを確認**してから直す（CLAUDE.md 作業原則）。

---

### A-2. ステップ信頼度の自己評価にユーザーの質問を渡していない

| | |
|---|---|
| **grace_v2 の該当** | `grace/confidence.py:494 evaluate_with_factors(..., query: str = "")` ＋ 呼び出し側 `:222` が `query=query` を渡す。プロンプトに `query_block` を差し込む |
| **本リポジトリの現状（実測）** | `evaluate_with_factors()` の引数は `description` / `output` / `factors` の 3 つで **`query` が無い**。呼び出し側（`confidence.py:221`）も渡していない |
| **影響** | 評価基準 1 が「ユーザーの質問に答えているか」を問うのに、**評価者が質問文を知らないまま**スコアを付ける。結果として、質問と無関係な出力にも高い信頼度が付きうる |

**作業**

1. `evaluate_with_factors()` に `query: str = ""` を追加（既定値ありなので後方互換）。
2. プロンプト組み立てで、`query` が空でなければ「【ユーザーの質問】」ブロックを差し込む。
3. 呼び出し側から `query=query` を渡す。
4. `backend/tests/test_self_eval_query.py` を移植する。

> 🔵 **Ollama 固有の注意**: プロンプトが長くなるので、`num_ctx` を絞った小型モデルでは
> 切り詰めが起きうる。`config.OllamaConfig` の上限と、実測での応答の欠けを確認すること。
> プロンプト自体は日本語の素のテキストなので、構造化出力の制約には関係しない。

---

## 3. B. アクセシビリティ修正

| # | grace_v2 の該当 | 本リポジトリの現状（実測） | 作業 |
|---|---|---|---|
| B-1 | `CollectionPanel.tsx` に `role="status"` が 2 箇所 | **0 箇所** | 中止バナーに `role="status"` を付け、支援技術へ読み上げられるようにする |
| B-2 | `ReviewForm.tsx` に `aria-live` / `role="status"` が 1 箇所 | **0 箇所** | 文字数上限の超過通知を読み上げ対象にする（C-4 の `documentLimit` とセット） |

プロバイダに一切関係しない。**そのまま移植できる。**

---

## 4. C. フロント機能（`state/` の純関数 4 本 ＋ コンポーネント 1 本）

本リポジトリに**無い**ファイル（実測）:

| ファイル | 役割 | 依存 |
|---|---|---|
| `state/formMemory.ts`（＋ `.test.ts`） | タブ切替時の入力退避と復元（選んだモデルを含む） | なし |
| `state/metaFetch.ts`（＋ `.test.ts`） | メタ取得失敗を対処可能な文言へ（silent failure を出さない） | `MetaErrorBanner.tsx` |
| `state/timelineAnnounce.ts`（＋ `.test.ts`） | 支援技術へ読み上げる 1 行の決定 | なし |
| `state/documentLimit.ts`（＋ `.test.ts`） | 文字数上限の判定・表示文言・アナウンス文言 | B-2 |
| `components/MetaErrorBanner.tsx` | メタ取得失敗の表示 | `metaFetch.ts` |

**作業の順番**: `documentLimit` → `timelineAnnounce` → `formMemory` → `metaFetch` ＋ `MetaErrorBanner`
（前 3 つは独立、最後の 2 つはセット）。

> ⚠️ **ファイルを丸ごとコピーしないこと**（CLAUDE.md §5）。`QueryForm.tsx` や `ReviewForm.tsx` に
> 差分を足す形で入れる。本リポジトリにしかない `models` prop / `ModelSelect` の配線を消さないよう、
> 必ず `diff -u` を取ってから編集する。
>
> ⚠️ `formMemory` は**モデル選択も退避対象**に含む設計である。本リポジトリのモデル一覧は
> Ollama のものなので、型（`string`）は同じでも**値の意味が違う**。テストの固定値は
> `gemma4:12b-mlx` 等へ読み替える。

---

## 5. D. テストのみ移植（コードは本リポジトリにも入っている）

実測で**対象コードの存在を確認済み**。テストが無いだけなので、移植すれば回帰を止められる。

| テスト | 何を固定するか | 本リポジトリ側の対象（実測） |
|---|---|---|
| `test_rag_adoption.py` | 無関係な文書を「社内ナレッジ」として採用しない／**最高スコア**が勝つ（検索順ではない） | `grace/tools.py`（`fallback_top_score` あり） |
| `test_no_info_judge.py` | ④' 実質回答判定の理由・エスカレ条件・使用モデル | `core/gates.py`（`JUDGE_UNEXPECTED_OUTPUT` あり） |
| `test_observability.py` | 矛盾クレームの観測（`_contradicted_claims`） | `core/gates.py:554`・`core/support_agent.py:693` |
| `test_silent_failures.py` | factors の canonical keys / `max_score` / variance が既定値に落ちない | `grace/executor.py:1772`（`_REQUIRED_SCORE_KEYS`） |
| `test_chunking_abort.py` | LLM 連続失敗でチャンク化を中断する | `chunking/async_api_client.py`（実装あり） |

**移植時の読み替え**（プロバイダ差）:

- `test_no_info_judge.py` は `INTENT_MODEL` を import する。本リポジトリの判定系モデルは
  Ollama 側の値なので、**固定値ではなく実装から引く**形にする。
- `test_rag_adoption.py` のコレクション名 `cc_news_2per_anthropic` は、本リポジトリの
  命名（`*_anthropic` のまま使っているか）を確認して合わせる。
- スタブが `create_llm_client("anthropic")` を patch している箇所は `"ollama"` へ。

---

## 6. E. 掃除

`services/dataset_service.py` / `services/file_service.py` は grace_v2 で
**2026-09-12 に削除済み**（Streamlit 時代の名残で、`services/__init__.py` の再エクスポート以外に
呼び出し元が無かった）。本リポジトリには**まだ残っている**。

**作業**: 呼び出し元がゼロであることを grep で確認したうえで削除し、`services/__init__.py` の
docstring に削除記録を残す（grace_v2 と同じ書式）。**不可逆なので着手前に確認を取ること。**

### E-2. `streamlit` / `altair` / `pydeck` 依存とドキュメント残骸

grace_v2 は E-1 と同時に（2026-09-12）**不要依存 3 件も削除**している。本リポジトリには
まだ残っていた。実コードに `import streamlit` は **1 件も無い**（`*.py` 全件 grep で確認）。
`altair` / `pydeck` は streamlit の連れ依存。

| 対象 | 残っていた場所 |
|---|---|
| 依存宣言 | `pyproject.toml`（`altair==5.5.0` / `pydeck==0.9.1` / `streamlit==1.48.1`）・`requirements.txt`（`altair==4.2.2` / `pydeck==0.9.1` / `streamlit==1.52.1`） |
| ドキュメント | `services/docs/log_service.md` §6.2（Streamlit UI 連携の使用例）・`qa_qdrant/docs/01_install.md`（`streamlit run agent_rag.py --server.port=8500` 前提の手順） |

> 📌 pyproject と requirements で **streamlit の版が食い違っていた**（1.48.1 / 1.52.1）。
> 誰も使っていないことの傍証である。
>
> 📌 `.claude/skills/grace-agent-docs/a_pages_md_format.md` の Streamlit 記述は
> **意図的に残す**（他リポジトリ用のフォーマット仕様・CLAUDE.md §9.2）。

---

## 7. F. 移植しない（理由つき）

| 対象 | grace_v2 の内容 | 移植しない理由 |
|---|---|---|
| `GET /api/model` の `chunking_model` / `qa_model` | データ準備の既定をスキーマ既定値から引く | 本リポジトリは `core/data_jobs.py::_resolve_model()` で**全ジョブが同じ Ollama モデルへ解決する**設計。grace_v2 はジョブごとに別モデル（QA は sonnet、チャンキングは haiku）なので前提が違う |
| `ModelChoice` の `input_price` / `output_price` / `context_window` / `max_output` | 単価・上限をセレクタに出す | **ローカル実行はコスト 0**。本リポジトリは代わりに `supports_tool_calls` / `notes`（tool calling 対応可否）を出しており、こちらの方が有用 |
| `test_model_table_coverage.py` | `MODEL_PRICING` / `MODEL_LIMITS` の網羅を検査 | 上と同じ理由。必要なら「`OllamaConfig.MODEL_CONSTRAINTS` の網羅」という**別のテスト**として書き起こす（移植ではなく新規） |

---

## 8. 実施順序の提案

```
1. A-1 実行メモリの除外（コード＋テスト）        ← 実害があり独立
2. A-2 自己評価に質問を渡す（コード＋テスト）    ← 同上
3. D   テスト 5 本の移植                         ← 回帰の網を先に張る
4. B   a11y 2 件                                 ← 小さく独立
5. C   フロント 4 モジュール＋1 コンポーネント   ← B-2 と documentLimit はセット
6. E   死にコード削除（要確認）                  ← 不可逆なので最後
```

1 と 2 は互いに独立なので、別々の PR に分けた方がレビューしやすい。

---

## 9. 移植で壊してはいけない「本リポジトリにしかないもの」

`diff` で AST を突き合わせた実測結果（2026-09-20）。**grace_v2 からファイルを丸ごと持ち込むと消える。**

| ファイル | 本リポジトリにしかない実装 |
|---|---|
| `backend/app/core/gates.py` | `judges_enabled()` / `multi_question_enabled()` / `_disabled_judge()` / `_should_rescue_unverified()`（判定系の**フィーチャーフラグ**） |
| `grace/executor.py` | `_step_timeout()` / `_start_with_deadline()` / `_web_search_budget_seconds()` / `class _Pending` / `_dedupe_sources()` / `_filter_low_relevance_sources()` / `_is_web_source()` / `_source_identity()`（**タイムアウト制御と出典の重複排除**） |
| `grace/tools.py` | `_generate()` / `_minimal_sources()` |
| `backend/app/core/data_jobs.py` | `_resolve_model()` / `_ollama_unreachable_message()` / `_model_not_pulled_message()`（**Ollama 特有のエラー案内**） |
| `helper/helper_llm.py` | `OllamaClient` 一式（1,373 行。grace_v2 は 437 行の Anthropic 実装） |
| フロント | `ModelSelect.tsx` / `state/modelLabel.ts`（grace_v2 にも同名があるが**中身は別物**。Ollama のモデル一覧・tool calling 注記） |

**手順（CLAUDE.md §5）**: ① `diff -u` を取る → ② 目的の差分だけ足す → ③ 消えた参照が無いか grep →
④ 既存テストが通ることで温存を担保する。

---

## 10. 検証（どの TODO でも共通）

```bash
uv run ruff check . --no-cache                         # lint
uv run --no-sync pytest backend/tests -q               # 現状 1851 passed / 22 skipped
python -m compileall -q -x '\.venv|/\.git/|/logs/' .   # 構文ゲート
cd frontend && npm run lint && npm test && npm run build
```

- **回帰修正を入れるときは、修正前のコードにテストを当てて fail することを先に確認する**
  （fail しないテストは回帰を捕まえていない）。
- A-1 / A-2 は LLM を実際に呼ばずスタブで固定できる。実 Ollama での確認は任意。

---

## 11. 実施記録

### A・D 実施（2026-09-20）

| 項目 | 内容 | 結果 |
|---|---|---|
| **A-1** | `grace/memory.py::best_collection(exclude=...)` ＋ `grace/planner.py::_is_excluded()` | 修正前のコードに `test_memory_exclusion.py` を当てて **3 件 fail** することを確認してから修正 |
| **A-2** | `grace/confidence.py::evaluate_with_factors(query=...)` ＋ `llm_calculate(query=...)` ＋ `grace/executor.py` から `step.query or state.plan.original_query` を渡す | 修正前に `test_self_eval_query.py` が **5 件 fail** することを確認してから修正 |
| **D** | テスト 5 本を移植（14+18+15+15+5 件） | 対象コードは既存のため、読み替えのみで通過 |

**テスト件数**: 1851 → **1935 passed / 22 skipped**（いずれも実行して計測）。

#### 実施中に分かったこと

1. **`backend/tests/grace/test_memory.py::test_rule_based_plan_uses_prior` が A-1 で落ちた。**
   題材が `cc_news_2per_anthropic` で、これは既定の除外リストに載っている。
   除外を尊重する修正が正しく効いた結果なので、題材を `gov_faq_anthropic` へ変更し、
   「除外対象は計画へ持ち込まない」ケースを 1 件追加した。

2. **grace_v2 の「明示指定・許可リストは除外しない」は、本リポジトリでは別の形で既に満たされていた。**
   grace_v2 は `execute()` 側で `protected` を組み立てて除外を適用するが、本リポジトリは
   `_get_all_collections_dynamic(apply_exclusions=not allowed)` で「スコープ指定が無いときだけ
   除外する」方式を採る。移植したテストの該当 3 件は**修正前から pass** した。
   **構造を grace_v2 に揃える変更は入れていない**（挙動が同じなら、揃えるためだけの
   restructure は回帰リスクに見合わない）。

3. **テストの読み替えが必要だった箇所**（丸写しでは通らない）:
   - 中断メッセージの確認項目（API キー／モデル名 → `ollama serve` / `pull` / `CHUNKING_LLM_TIMEOUT`）
   - 判定系モデルの比較対象（`claude-*` → `gemma4:*`。`INTENT_MODEL` は `get_default_ollama_model()`）
   - `_get_all_collections_dynamic` の引数と、除外を適用する位置
   - `_apply_excluded_collections` のシグネチャ（`(candidates, excluded)` の staticmethod）

### B・C 実施（2026-09-20）

| 項目 | 内容 |
|---|---|
| **C** | `state/documentLimit.ts` / `timelineAnnounce.ts` / `formMemory.ts` / `metaFetch.ts` と `components/MetaErrorBanner.tsx` を追加（テスト 42 件: 10 / 9 / 13 / 10） |
| **C 配線** | `QueryForm`・`ReviewForm` → `formMemory`（タブ切替で入力が既定値へ戻らない）／`SupportPanel`・`ReviewPanel` → `metaFetch` ＋ `MetaErrorBanner`（取得失敗の理由と再取得ボタン）／`Timeline` → `timelineAnnounce`（実行中ステップだけを読み上げ）／`ReviewForm` → `documentLimit` |
| **B-1** | `CollectionPanel` の削除中止バナーへ `role="status"`（拒否・タイムアウトは非破壊なので割り込む `alert` ではなく polite な `status`） |
| **B-2** | `ReviewForm` の textarea に `aria-invalid` / `aria-describedby` と `.sr-only` のライブ領域。文言は `documentLimit` が返す**長さを含まない固定文**なので、超過したまま入力を続けても読み上げは繰り返されない |

**フロントのテスト件数**: 263 → **305 passed**（実行して計測）。

⚠️ **ファイルの丸ごとコピーはしていない。** 新規モジュール 5 本はそのまま持ち込めたが、
配線先（`QueryForm` / `ReviewForm` / `SupportPanel` / `ReviewPanel` / `Timeline` /
`CollectionPanel`）は**差分だけを足した**。本リポジトリにしかない `models` prop /
`ModelSelect` の配線は温存されている（`npm run lint` と 305 件のテストで担保）。

追随したドキュメント: `frontend/docs/{QueryForm,Timeline,SupportPanel,CollectionPanel}.md`、
`CLAUDE.md` §5（乖離表）・§6（純関数一覧）。

### E 実施（2026-09-20）

削除前に**本番コードからの参照がゼロ**であることを確認した（`services/__init__.py` の
再エクスポート以外に呼び出し元なし）。`load_uploaded_file` は同名の関数が
`qa_generation/data_io.py` に**自前で定義**されており、`services` 版とは別物だった。

| 削除したもの | 件数 |
|---|---|
| `services/dataset_service.py` / `file_service.py` | 2 |
| それぞれの IPO ドキュメント（`services/docs/`） | 2 |
| 専用テスト（`backend/tests/services/` / `backend/tests/legacy/`） | 4 ファイル・**29 件** |

追随: `services/__init__.py`（import・`__all__`・docstring に削除記録）、
`services/docs/__init__.md`（責務表・Mermaid 2 図・エクスポート表・由来対応表）。

**テスト件数**: 1935 → **1906 passed**（差の 29 件は削除したモジュール専用のテスト。
対象コードが無くなったための減少であり、他のテストは 1 件も落としていない）。

### E-2 実施（2026-09-20）

**依存削除**: `pyproject.toml` / `requirements.txt` から `streamlit` / `altair` / `pydeck` を
削除し、`uv lock` を再生成した（`Removed altair v5.5.0` / `Removed pydeck v0.9.1` /
`Removed streamlit v1.48.1`・解決 180 パッケージ）。実コードの `import` は元から 0 件。

**ドキュメント是正**:

| ファイル | 内容 |
|---|---|
| `services/docs/log_service.md` | §6.2 を「Streamlit UI 連携」→「未回答ログの確認」（CLI 例）へ差し替え。呼び出し元が `services/agent_service.py` であること、読み出し側を画面から叩く経路が React UI に無いことを明記。v1.0 → **v1.1** |
| `qa_qdrant/docs/01_install.md` | **全面改訂（v1 → v2.0）**。下表のとおり |

`01_install.md` は grace_v2 の v2.0 を下敷きにしつつ、**プロバイダ差を読み替えた**
（§5 の手順どおり、ファイルの丸ごとコピーはしていない）。

| 論点 | grace_v2 の v2.0 | 本リポジトリの v2.0 |
|---|---|---|
| LLM | Anthropic（`ANTHROPIC_API_KEY` 必須） | **Ollama**（`gemma4:12b-mlx`・**キー不要**・`ollama serve` が前提） |
| 必須キー | `ANTHROPIC_API_KEY` ＋ `GOOGLE_API_KEY` の 2 本 | **`GOOGLE_API_KEY` の 1 本だけ**（Embedding 専用） |
| `/api/health` | `{"status":"ok","anthropic_api_key":…,"google_api_key":…}` | `{"status":"ok","google_api_key":…}`（`backend/app/api/meta.py::health` の実装どおり） |
| 疎通確認 | — | `curl http://localhost:11434/api/tags` を追加（ポート 11434 も一覧へ） |
| トラブルシュート | API キーの切り分け | 「**キーの問題ではない**」を先に書き、`ollama serve` 未起動／モデル未 pull／tool calling 非対応モデル（`phi3` / `gemma2`）を分けた |

あわせて実装と食い違っていた記述も是正した（grace_v2 と共通）:
`celery -A celery_tasks … --queues=qa_generation` → **`-A celery_config` ＋ 実在する 4 キュー**、
`start_celery.sh` の引数（`-w NUM`/`-l` → **`-c` ＋ `--flower`**）、
`curl localhost:6333/health` → **`/healthz`**、`a02_make_qa_para.py`（存在しない）。

**検証**: 4 ゲートすべて緑。`ruff` All checks passed / `pytest backend/tests -q`
**1906 passed, 22 skipped** / `compileall` exit 0 / frontend lint・**305 passed**・build 成功。

---

## 変更履歴

| バージョン | 変更内容 |
|-----------|---------|
| 1.5 | `a_cross_doc_md_format.md`（TODO＝種別 C）に準拠（2026-09-24）。H2 が 13 個あるため目次を追加 |
| 1.4 | E-2（`streamlit` / `altair` / `pydeck` 依存の削除、`log_service.md` §6.2 と `01_install.md` の是正）を実施（2026-09-20） |
| 1.3 | E（死にコード削除）を実施し、A〜E の全項目が完了（2026-09-20） |
| 1.2 | B（a11y 2 件）と C（フロント 4 モジュール＋1 コンポーネント）を実施し、§0 の状態と §11 の実施記録を更新。残りは E のみ（2026-09-20） |
| 1.1 | A（実バグ修正 2 件）と D（テスト 5 本）を実施し、§0 の状態列と §11 実施記録を追加（2026-09-20） |
| 1.0 | 初版。grace_v2 master `fdefb8d` と grace_v2_local master `2a89392` を突き合わせ、移植対象を A〜F に分類（2026-09-20） |

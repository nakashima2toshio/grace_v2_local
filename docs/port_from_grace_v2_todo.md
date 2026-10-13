# grace_v2 → grace_v2_local 移植 TODO

**Version 2.9** | 作成: 2026-09-20 | 最終更新: 2026-10-13

---

> **本書の位置づけ**: 姉妹リポジトリ `grace_v2`（Anthropic 版）に入っていて本リポジトリ
> （Ollama 版）に入っていない**コード上の修正**を洗い出し、移植の要否・手順・
> プロバイダ差の扱いを 1 本にまとめたもの。
>
> **比較の実測日**: 2026-09-20。`grace_v2` master `fdefb8d` と
> `grace_v2_local` master `2a89392` を `diff -r` および AST（def/class 名）で突き合わせた。
> 以降のコミットで状況は変わるので、着手前に再確認すること。
>
> **2026-10-13 に圧縮した。** A〜E・G の作業記録（§2〜§6・§8・§10・§11）は完了済みなので外し、
> 今後も効く §0 結論・§1 物差し・§7 移植しないもの・§9 壊してはいけないもの・§12 共用の Qdrant だけを残した
> （外部から §12 を参照しているので番号は振り直さない）。外した記録は git 履歴（本書 v2.8）にある。

---

## 目次

- [0. 結論（先に全体像）](#0-結論先に全体像)
- [1. プロバイダ差の整理（移植判断の物差し）](#1-プロバイダ差の整理移植判断の物差し)
- [7. F. 移植しない（理由つき）](#7-f-移植しない理由つき)
- [9. 移植で壊してはいけない「本リポジトリにしかないもの」](#9-移植で壊してはいけない本リポジトリにしかないもの)
- [12. 共用の Qdrant と、本リポジトリの残作業（2026-10-01）](#12-共用の-qdrant-と本リポジトリの残作業2026-10-01)
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
| **G. GRACE-Review の修正（2026-10-01 に追加）** | 5 | ✅ **完了**（2026-10-01。G-1: #161・G-2: 本 PR） | G-1: grace_v2#229 / #230 / #238（根拠の要旨化・確信度の減衰・ルール単位の検索・並列化）　G-2: grace_v2#234 / #240（⑥ Web の並列化と打ち切り・クライアント重複作成の防止） |
| **F. 移植しない（プロバイダ差・設計差）** | 3 | — | `/api/model` のモデル表設計／`ModelChoice` の単価・上限／`test_model_table_coverage` |

**A〜E はすべて 2026-09-20 に実施済み**（実施記録は git 履歴の本書 v2.8 §11）。F は「移植しない」と結論済み。

> **G（2026-10-01 追加）**: 規程コレクション `ec_ad_rules_anthropic` は grace_v2 と**共用**で、中身は
> grace_v2 の雛形（要旨＋条文＋通知）から登録されている。ところが本リポジトリの Review は
> 広告の文で規程を検索していたため、登録した条文が根拠に届かず（grace_v2 の実測 0.67 で下限 0.70 割れ）、
> 要旨に差し替わる際は `description` 全文（LLM 向け指示文を含む）が根拠として画面に出ていた。
> **G-1 は `review_agent.py` / `rulesets.py` が grace_v2 の移植元（#229 直前）と Ollama 部分を除いて
> 同一だったため、上流のパッチを適用して衝突箇所だけ手で直した**（ファイルの丸ごとコピーはしていない。
> Ollama 固有の `get_selectable_ollama_models` / `model_used` / API キー検査なし は温存）。
>
> **実機確認（2026-10-01・「化粧品LP案」）**: 移植後のコードで、ルール自身の文での検索（15 件を並列・約 4 秒）、
> yakki-02 0.8684 / yakki-04 0.8437 で自分の行（条文つき）を採用、フォールバック 0 件、根拠に指示文が出ない、
> Embedding / SPLADE の初期化が 1 回ずつ、⑥ Web の 5 秒打ち切りを確認した。所要 9 分 6 秒（移植前 6 分 55 秒）。
> 増えた分は、条文・通知が根拠として LLM に届くようになった yakki-02 の判定（1 回 60 秒前後）。
> ⚠️ 1 回目の実機確認は手元の master が移植前（`1d7bc62`）のままで、古いコードが動いていた。
> 確認の前に `git log --oneline -1` で master の先頭を見ること。

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

## 7. F. 移植しない（理由つき）

| 対象 | grace_v2 の内容 | 移植しない理由 |
|---|---|---|
| `GET /api/model` の `chunking_model` / `qa_model` | データ準備の既定をスキーマ既定値から引く | 本リポジトリは `core/data_jobs.py::_resolve_model()` で**全ジョブが同じ Ollama モデルへ解決する**設計。grace_v2 はジョブごとに別モデル（QA は sonnet、チャンキングは haiku）なので前提が違う |
| `ModelChoice` の `input_price` / `output_price` / `context_window` / `max_output` | 単価・上限をセレクタに出す | **ローカル実行はコスト 0**。本リポジトリは代わりに `supports_tool_calls` / `notes`（tool calling 対応可否）を出しており、こちらの方が有用 |
| **`qa_output/`（規程の雛形 `ec_ad_rules_statutes_template.csv` ほか）** | 規程コレクションの元データ・条文置換用の雛形 | **規程コレクション `ec_ad_rules_anthropic` は grace_v2 と共用の 1 個**なので、元データも grace_v2 の 1 か所に置く。コピーすると片方だけに条文を足す食い違いが起き、登録するともう片方の Review も黙って変わる。本リポジトリの Review / Support は Qdrant を読むだけで `qa_output/` を使わない（テストも依存しない。2026-10-01 確認）。登録し直すときは grace_v2 のファイルを直接指す（§12） |
| `test_export_ruleset_to_csv.py` の雛形・鮮度テスト（grace_v2#231 / #233） | `qa_output/` の CSV が `rulesets.py` と食い違わないかを検査 | 上と同じ理由で、検査対象のファイルを本リポジトリに置かない。検査は grace_v2 側で行う |
| `test_model_table_coverage.py` | `MODEL_PRICING` / `MODEL_LIMITS` の網羅を検査 | 上と同じ理由。必要なら「`OllamaConfig.MODEL_CONSTRAINTS` の網羅」という**別のテスト**として書き起こす（移植ではなく新規） |

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
| フロント | `state/modelLabel.ts` / `state/headerModel.ts`（grace_v2 にも同名があるが**中身は別物**。Ollama のモデル一覧・tool calling 注記。当時あった `ModelSelect.tsx` は 2026-09-23 に削除） |

**手順（CLAUDE.md §5）**: ① `diff -u` を取る → ② 目的の差分だけ足す → ③ 消えた参照が無いか grep →
④ 既存テストが通ることで温存を担保する。

---

## 12. 共用の Qdrant と、本リポジトリの残作業（2026-10-01）

### 12.1 方針: 規程の雛形は grace_v2 にだけ置く

§7 F のとおり `qa_output/` は持たない。規程コレクションを登録し直すときは、grace_v2 のファイルを直接指定する。

```bash
python qa_qdrant/register_to_qdrant.py \
  --input-file ../grace_v2/qa_output/ec_ad_rules_statutes_template.csv \
  --collection ec_ad_rules_anthropic --recreate --no-create-ui-csv
```

⚠️ `--recreate` は**両リポジトリの Review に同時に効く**（同じコレクションを作り直す）。
条文の追記・監修の状況は grace_v2 の [`docs/review_rag_rules_todo.md`](https://github.com/nakashima2toshio/grace_v2/blob/master/docs/review_rag_rules_todo.md) が正本。

### 12.2 本リポジトリの残作業・課題

| # | 項目 | 内容 | 状態 |
|---|---|---|---|
| 1 | ③④ の並列化の実験 | `OLLAMA_NUM_PARALLEL=2 ollama serve` ＋ `GRACE_REVIEW_WORKERS=2`。9 分のうち約 9 分が直列の Ollama 待ち。`ollama ps` でメモリを見る。速くならなければ 1 に戻す（既定は 1。`review_agent._judge_workers` の docstring） | 任意（利用者） |
| 2 | gemma4 の判定の質 | 当初（12b）は keihyo-08 / keihyo-09 の誤り。既定を 26b にした後、化粧品LP案は grace_v2（クラウド）と同じ 10 ルールになった（#9）。keihyo-09 の「期間限定」は除外語（`keyword_excludes`）で解消。残った「判定基準の例の読み落とし」2 件は #10 の文字列の判定で補った。**LLM の判定そのものは直っていない**（OK 例で policy-01 は今も ③ で検出され、④' で抑止している）。他の場面の読み落としは未確認 | ✅ 対処済み（監視のみ） |
| 3 | コレクション一覧の取得が重複 | ② の並列化で、最初の検索に 4 スレッドが `GET /collections` 等をそれぞれ実行（計 0.1 秒程度） | 実害なし・直さない |
| 4 | `config.py::AgentConfig.RAG_AVAILABLE_COLLECTIONS` | 実在しない `cc_news_5per` が入っている（参照ゼロ）。grace_v2 も同じ | 低優先 |
| 5 | GRACE-Review の Web 裏取りの既定 | 画面の既定を ON → **OFF** にした（2026-10-01・利用者判断。API は元から OFF。grace_v2 と同時に変更） | ✅ 済 |
| 6 | GRACE-Review のルールと指示文の修正（grace_v2#244） | grace_v2 の 3 サンプル × 2 モデル比較（2026-10-02）を受けた修正を移植した。keihyo-07 は確定にせず要確認で止める（`RuleItem.confirm_needs_human`）、keihyo-08 は条件の付かない「送料無料」を指摘しない、修正案に今より厳しい条件や架空の値を書かない、文面の言葉づかい。差分はそのまま当たった（`review_agent.py` / `review_gates.py` / `rulesets.py` は該当箇所が grace_v2 と同一）。化粧品LP案の期待値テストも移植。gemma4 で keihyo-08 / keihyo-09 がどう変わるかは未実測 | ✅ 済（実測は利用者） |
| 7 | 修正後の実測と追加修正（2026-10-02・化粧品LP案） | 12b（gemma4:12b-mlx）: 9 件・8 分 28 秒。26b（gemma4:26b-a4b-it-qat）: 10 件・**3 分 40 秒（約 2.3 倍速い）**。どちらも keihyo-08 / keihyo-09 の誤りは解消したが、**tokusho-01（税込表記）を取りこぼし**、26b は **yakki-01（食品のルール）で美容液を誤検知**。段落単位の判定に文書の文脈（題名＋冒頭）を渡し、tokusho-01 に税込表記の確認を足した（grace_v2 と同時の PR） | ✅ 実装済（再測定は利用者） |
| 8 | 既定モデルを 26b にするか | **26b（`gemma4:26b-a4b-it-qat`）へ変更した**（2026-10-03・利用者判断）。`config.py::get_default_ollama_model()` と `config/grace_config.yml` のミラー値を更新し、候補一覧の先頭を 26b にした。12b は `OLLAMA_DEFAULT_MODEL=gemma4:12b-mlx` で使える | ✅ 済 |
| 9 | 2.12 / 2.13 の後の実測と keihyo-09 の除外語（2026-10-03・化粧品LP案） | 26b: 10 件・4 分 32 秒で **grace_v2（クラウド）と同じ 10 ルール**（tokusho-01 あり、yakki-01 / keihyo-09 なし）。12b: 10 件・8 分 15 秒。tokusho-01 は検出したが自分の ④ Ground が「1 矛盾」として抑止、keihyo-09 が「期間限定」で再発。keihyo-09 は第1段の除外語（`keyword_excludes`）で LLM に依らず止めた（grace_v2 と同時の PR） | ✅ 実装済（12b の tokusho-01 は調査待ち） |
| 10 | 26b が判定基準の例を読み落とす（2026-10-03・各 2 回再現） | 表記漏れLP案で tokusho-01 が購入時の送料漏れを見落とし、OK 例で policy-01 が広告「未開封」対 規程「未使用・未開封」を逆向き（広告が不利）に判定した。文字列で決まる部分を `backend/app/core/review_facts.py` で決めるようにした（送料の語が無ければ違反、返品条件が規程より不利でないと言い切れれば ④' で抑止）。あわせて、例文ボタンで文書を差し替えただけで実行していない結果を読み違えないよう、結果が古いときに警告を出す（`state/staleResult.ts`）。grace_v2 と同時 | ✅ 済。実機で確認（2026-10-03・26b）: OK 例は指摘 0 件（判定 9 回・検出 1・抑止 1・1 分 7 秒、抑止理由「期限: 広告 14 日 ≥ 規程 14 日／条件: 広告「未開封」は規程「未使用・未開封」以下」）、表記漏れLP案は tokusho-01 を「購入時の送料…が広告文に書かれていません」で検出、古い結果の警告も表示された |

Qdrant 上の不要コレクション（空の `cc_news_2per_openai`、768 次元で検索できない `cc_news_2per_ollama` /
`cc_news_100_ollama` ほか）の整理は、grace_v2 の TODO §2.2 にまとめた（共用なのでどちらから消しても同じ）。

---

## 変更履歴

| バージョン | 日付 | 変更内容 |
|---|---|---|
| 1.0 | 2026-09-20 | 初版。grace_v2 master `fdefb8d` と grace_v2_local master `2a89392` を突き合わせ、移植対象を A〜F に分類（2026-09-20） |
| 1.1 | 2026-09-20 | A（実バグ修正 2 件）と D（テスト 5 本）を実施し、§0 の状態列と §11 実施記録を追加（2026-09-20） |
| 1.2 | 2026-09-20 | B（a11y 2 件）と C（フロント 4 モジュール＋1 コンポーネント）を実施し、§0 の状態と §11 の実施記録を更新。残りは E のみ（2026-09-20） |
| 1.3 | 2026-09-20 | E（死にコード削除）を実施し、A〜E の全項目が完了（2026-09-20） |
| 1.4 | 2026-09-20 | E-2（`streamlit` / `altair` / `pydeck` 依存の削除、`log_service.md` §6.2 と `01_install.md` の是正）を実施（2026-09-20） |
| 1.5 | 2026-09-24 | `a_cross_doc_md_format.md`（TODO＝種別 C）に準拠（2026-09-24）。H2 が 13 個あるため目次を追加 |
| 1.6 | 2026-10-01 | G（GRACE-Review の修正 5 件）を追加し、G-1（grace_v2#229 / #230 / #238）を実施（2026-10-01） |
| 1.7 | 2026-10-01 | G-2（grace_v2#234 / #240）を実施し、G を完了（2026-10-01） |
| 1.8 | 2026-10-01 | §7 F に `qa_output/`（規程の雛形）と雛形の鮮度テストを追加（共用の規程コレクションの元データは grace_v2 の 1 か所に置く）。§12（共用の Qdrant の方針と本リポジトリの残作業）を新設。G の実機確認を追記（2026-10-01） |
| 1.9 | 2026-10-01 | §12.2 に GRACE-Review の Web 裏取りの既定を OFF にしたことを追記（2026-10-01） |
| 2.0 | 2026-10-02 | §12.2 に grace_v2#244（ルールと指示文の修正）の移植を追記（2026-10-02） |
| 2.1 | 2026-10-02 | §12.2 に修正後の実測（gemma4 12b / 26b）と追加修正（段落に文書の文脈・tokusho-01 の税込表記）、既定モデルの検討を追記（2026-10-02） |
| 2.2 | 2026-10-03 | §12.2 #9 に 2.12 / 2.13 の後の実測と keihyo-09 の除外語を追記（2026-10-03） |
| 2.3 | 2026-10-03 | §12.2 #8 既定モデルを 26b へ変更（2026-10-03） |
| 2.4 | 2026-10-03 | §12.2 #10 OK 例の誤検知・送料漏れの見落としを文字列の判定で補う／結果が古いことの表示（2026-10-03） |
| 2.5 | 2026-10-03 | §12.2 #10 を実機確認済みに、#2（gemma4 の判定の質）を現状に更新（2026-10-03） |
| 2.6 | 2026-10-10 | §10 の注記を更新: `a_pages_md_format.md` は当時意図的に残したが、2026-10-10 に仕様書ごと削除した。変更履歴を 3 列（`バージョン \| 日付 \| 変更内容`）へ移した |
| 2.7 | 2026-10-10 | テストの所在を `backend/tests/` からリポジトリ直下の `tests/` へ移したのに追随（パス・コマンド・import の表記） |
| 2.8 | 2026-10-11 | `qa_qdrant/docs/01_install.md` を直下 `docs/data_prep_install.md` へ移したのに合わせ、本文の参照先を直した（履歴の行は当時のまま） |
| 2.9 | 2026-10-13 | 完了した作業記録（§2〜§6・§8・§10・§11）を外して圧縮（§0・§1・§7・§9・§12 を残す。外した記録は git 履歴の v2.8）。§9 のフロントの行を現状（ModelSelect.tsx は削除済み）に合わせた |

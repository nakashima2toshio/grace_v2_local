# ローカル LLM のタイムアウト予算と、遅さの内訳

**最終更新: 2026-08-16** | ステータス: 実装済み・**解決を実測で確認**

同じ質問に対する実測の推移。

| 時点 | LLM | 所要時間 | 結果 |
|---|---|--:|---|
| 参考: `grace_v2` | Anthropic `claude-sonnet-4-6` | 63 秒 | answer（groundedness 1.00） |
| 修正前 | Ollama `gemma4:26b-a4b-it-qat` | **1:17:46** | escalate（groundedness 判定不能） |
| §1〜§5 の修正後 | 同上 | 6:27 | answer（groundedness 判定不能） |
| §3.6 まで修正後 | 同上 | 34:19 | escalate（本文が返らない） |
| **`reasoning_effort=none` 適用後** | 同上 | **1:58** | **answer（groundedness 1.00 / 5-5 主張）** |

**34:19 → 1:58（17 倍）。** 決め手は §3.5 の思考抑止だった。

```
06:49:36  OllamaClient initialized: ... reasoning_effort=none   ← 未対応警告なし
06:50:09  HTTP 200                                              ← 33.5 秒
06:50:10  Reasoning completed: 569 chars                        ← 本文が返った
```

この実行では **空応答の warning が 1 件も出ていない**。

パイプラインは終始同一で、LLM 呼び出し回数も 10 数回で変わらない。効いていたのは
**1 呼び出しの所要時間**と、その前提で組まれていなかった予算設計である。
本書はその設計を記録する。

---

## 1. `openai._base_client` は不具合ではない

ログにこう出るため「ローカル LLM なのに OpenAI を使っている」と読めてしまう。

```
DEBUG openai._base_client - Request options: {...}
INFO  httpx - HTTP Request: POST http://localhost:11434/v1/chat/completions "200 OK"
```

**これは正しい構成である。**

- Ollama は **OpenAI 互換エンドポイント** `/v1/chat/completions` を提供する
- `openai` Python SDK はその標準クライアントであり、`base_url` を
  `http://localhost:11434/v1` に向けて使う
- `openai._base_client` は **ロガー名**にすぎない。宛先は 2 行目のとおり
  `localhost:11434`（＝手元の Ollama）で、`api.openai.com` へは接続しない
- `api_key="ollama"` は SDK が空文字を拒むためのダミー。実際の認証は無い

つまり **タイムアウトの原因は SDK ではない**。因果は逆で、原因はモデルの
生成速度と、次章の予算設計にある。

実装: `helper/helper_llm.py`（`DEFAULT_OLLAMA_BASE_URL` 付近のコメント）

---

## 2. 予算はリトライ込みで数える

期限は 3 層ある。**下位ほど先に諦める**のが不変条件で、逆転すると上位が
先に諦めて HTTP だけが生き残り、捨てたはずの生成が Ollama の GPU を
占有し続けて後続をさらに遅らせる（＝タイムアウトするほど遅くなる）。

| 層 | 設定 | 既定 |
|---|---|--:|
| LLM 1 リクエスト | `helper_llm.DEFAULT_OLLAMA_TIMEOUT` / `llm.timeout` | 180 s |
| SDK 自動リトライ | `helper_llm.DEFAULT_OLLAMA_MAX_RETRIES` | **0** |
| ステップ | `planner.step_timeout_seconds` | 240 s |

### ⚠️ 比較するのは `timeout` 単体ではない

`openai` SDK の `timeout` は **1 リクエストあたり**の期限なので、実際に
費やす時間は

```
実費 = timeout × (max_retries + 1)
```

になる。以前この不変条件を「`llm.timeout`(180) < `step_timeout`(240)」と
だけ書いていたため、`max_retries=1` のとき実費 360 秒がステップ期限
240 秒を追い越していた。実測ログ:

```
14:57:31  リクエスト開始
15:00:31  Retrying request ...        ← 180 s でタイムアウト、2 回目突入
15:01:31  step timed out after 240s   ← 2 回目が 60 s 走ったところで殺される
```

2 回目は構造上 **必ず** 途中で殺される。60 秒を捨てるだけの純粋な無駄
だった。

### なぜ `max_retries = 0` なのか

ローカル LLM の timeout はネットワーク瞬断ではなく「モデルが遅い／出力を
終端できない」ことが原因なので、同じプロンプトを投げ直しても結果は同じ。
再試行が要る場面は上位が持っている:

- planner の `llm_plan_max_attempts`
- executor の `fallback_chain`
- `ReasoningTool` の最小プロンプト再試行

現行の不変条件（`backend/tests/test_timeout_budget.py` が検証）:

```
llm.timeout × (DEFAULT_OLLAMA_MAX_RETRIES + 1) < planner.step_timeout_seconds
180 × (0 + 1) = 180 < 240  ✅
```

---

## 3. 補助 LLM 判定は既定で切る

`judges.enabled` / `judges.step_confidence_llm` の既定は **false**。

対象は「LLM に 1 語だけ言わせる」判定（意図分類・情報なし判定・強調表現の
分類・空虚な指摘の判定・ステップ確信度評価）で、いずれも**失敗したら
キーワード判定・検索スコアへフォールバックする補助処理**である。

実測では 8 回以上呼ばれ、そのすべてが `finish_reason=length` の空応答
（本文 0 文字）で捨てられていた。1 件 90〜250 秒なので **約 13 分**を
確実に無駄にしていた計算になる。

無効化しても壊れない。もともと LLM 失敗時に通る経路（安全側の既定）を
常時使うだけである。判定精度を優先したい場合や十分に速い LLM を指して
いる場合だけ `config/grace_config.yml` の `judges` で true に戻す。

> 補足: `finish_reason=length` で本文 0 文字になる理由は §3.5 を参照。
> 枠の問題ではない。

---

## 3.5 本文が空になる本当の理由 — 思考しか返っていない

診断ログ（§1 の観測強化で追加）が原因を確定させた。

```
finish_reason=length, max_tokens=4096, completion_tokens=2766,
prompt_tokens=1330, response_format=なし,
thinking=10007 chars (key=reasoning),
message_keys=['reasoning', 'role']      ← content が存在しない
```

**`content` というキー自体が応答に無い。** 生成した 10007 文字はすべて
`reasoning`（思考）に入り、本文には 1 文字も到達していない。
`gemma4:26b-a4b-it-qat` は思考モデルで、思考だけで枠を使い切っている。

⚠️ この観測は、それ以前に立てていた仮説を **2 つとも否定**した。

| 仮説 | 反証 |
|---|---|
| 「JSON スキーマの出力が枠に収まらない」 | `response_format=なし` の素のテキスト生成でも同じく空。JSON は無関係 |
| 「出力が 1024 に収まらない／枠を上げても無駄」 | 512 / 4096 / 8192 のいずれでも同じ。枠は主因ではない |

### 対処

1. **思考を抑止する。** `reasoning_effort="none"` を送る
   （`OLLAMA_REASONING_EFFORT` で変更可）。Ollama のバージョンによっては
   未対応なので、拒否されたら自動的に外して再送し、以降は送らない。
2. **「思考だけ」を検出する。** `OllamaClient.last_thinking_only` に記録する。
   同じプロンプトを投げ直しても同じ思考を繰り返すだけなので、上位が
   「再試行しても無駄」と判断できるようにする。

### 実測結果: 1 の思考抑止だけで解決した

`reasoning_effort="none"` はこの Ollama で **受理された**（未対応の警告が出ない）。
`gemma4:26b-a4b-it-qat` のまま、同じ質問が **34:19 → 1:58** で answer になった。

| | 抑止なし | **抑止あり** |
|---|--:|--:|
| reasoning 1 回 | 129〜180 秒 → 空 or timeout | **33.5 秒 → 569 文字** |
| groundedness | 判定不能 | **1.00（5/5 主張）** |
| 空応答 warning | 9 件 | **0 件** |

> ⚠️ 以前ここには「根本的にはこのモデルはこのパイプラインに向いていない。
> 上記は緩和策」と書いていた。**実測がそれを否定した。** 思考さえ切れば
> 26B でも実用範囲に入る。ただし `reasoning_effort` の受理は Ollama の
> バージョン依存なので、未対応環境では引き続きモデル選択が必要になる。

### 補足: gemma4 系はどれも思考モデル

「`-it-qat` が付いていないものは思考しない」というのは**誤り**。実測:

| モデル | content | thinking | 所要 | スループット |
|---|--:|--:|--:|--:|
| `gemma4:12b` | 117 chars | **2755 chars** | 99.3 秒 | 9.4 tok/s |
| `gemma4:e4b` | 244 chars | **1205 chars** | 24.6 秒 | 15.4 tok/s |

どちらも `message keys: ['content', 'reasoning', 'role']`。
**e4b が 4 倍速く思考も短い**ため、既定モデルの派生元に採用している。

---

## 3.6 失敗の増幅を止める

本文が返らない ⇒ reasoning 失敗 ⇒ リプラン、が繰り返され、1 リクエストが
**34 分 19 秒**になった。内訳のほとんどは「直らないことを繰り返す」時間。

| 増幅源 | 実測 | 対処 |
|---|--:|---|
| リプランのたびに LLM 計画生成（空 → 2 リトライ、1 回 140〜165 秒） | 約 17 分 | 一度空応答で倒れたら以降ルールベース固定（`Planner._llm_plan_disabled`） |
| 部分再計画が `rag_search` を 1 本ずつ追加（steps=3 → 4 → 5、すべて同じクエリ・同じコレクション） | — | reasoning の失敗に対しては検索ステップを足さない（`ReplanManager._drop_redundant_search_steps`） |

追加された検索はすべて `query='明日の東京の天気は？'` /
`collection='wikipedia_ja_5per'` で、当然まったく同じ 5 件を返していた。
検索自体は速いので致命傷ではないが、**問題を一切解決しないまま計画だけが
伸びる**ため、リプラン上限まで必ず走り切ることになる。

⚠️ 検索ステップ自体が失敗した場合は従来どおりやり直す（正当な再試行）。

---

## 4. 検証器の失敗で回答を捨てない

`GroundednessVerifier.verify()` は例外・タイムアウト・空応答のとき
`verified=False` を返し、`_answer_gate` はこれを一律 escalate にしていた。
その結果:

```
16:07:10  reasoning 成功（107 文字・内容も正しい）
16:08:43  Retrying request ...        ← 検証 LLM がタイムアウト
16:11:43  Reasoning failed: Request timed out
          → verified=False → escalate → answer=internal_answer（空）
```

**答えを作れているのに答えない**、という一番まずい壊れ方をしていた。

原因は `verified=False` が性質の違う 2 つを混ぜていたこと。

| 事態 | `verified` | `verification_failed` | 判定 |
|---|:--:|:--:|---|
| 主張 0 件で判定できず | False | False | escalate（妥当） |
| ソース無し／回答が空 | False | False | escalate（妥当） |
| **検証 LLM が落ちた** | False | **True** | **未確認注記つきで回答を維持** |
| 判定できた | True | False | `support_rate` で判定 |

`GroundednessResult.verification_failed` で区別し、⑤ Web フォールバックの
`_should_rescue_unverified()` が **矛盾なし・出典 1 件以上・回答本文あり**
のときだけ `("answer", True)`（未確認注記つき）へ倒す。

救済後も後段 ④' の情報なし検知ゲートを必ず通るため、「見つかりません
でした」型の回答は従来どおり有人へエスカレする。

---

## 5. fastembed の警告が大量に出る件

```
WARNING fastembed.common.model_management:download_files_from_huggingface:225
        - Local file sizes do not match the metadata.
```

`get_sparse_embedding_client()` が**成功だけをキャッシュ**していたため、
構築が例外を投げると `_sparse_client_instance` は None のまま残り、

```
検索のたびにモデル構築を試す → HuggingFace へ再ダウンロード → 警告 →
失敗 → agent_tools.py が logger.debug で握り潰す
```

を コレクション数 × リプラン回数 ぶん繰り返していた。ログの
`search_collection: ... sparse=False` が、一度も成功していない証拠である。

失敗も記録（negative cache）し、2 回目以降は構築を試みず同じ例外を即座に
返すようにした。原因が見えるよう **初回だけ warning** で実際の例外を出す。

sparse が使えない環境では dense 検索へ倒れるだけで、機能上の劣化は無い
（修正前も実質 dense のみで動いていた）。

なお実測ログで出た実際の原因はこれだった（warning 化して初めて見えた）。

```
[ONNXRuntimeError] : 3 : NO_SUCHFILE : Load model from
.../models--Qdrant--Splade_PP_en_v1/snapshots/.../model.onnx failed.
File doesn't exist.
```

＝ fastembed のモデルキャッシュが壊れている。sparse を使いたい場合は
キャッシュディレクトリを消して再取得する。

### 消すべきディレクトリを warning に出す

「壊れたキャッシュを消せ」と分かっても、**どこを消すのかが分からない。**
macOS の tempdir は `/var/folders/8b/xxxxxxxx/T/` のように推測できないため、
実パスを出さないと利用者は自力で復旧できない。

`resolve_cache_dir()` が FastEmbed 側の解決順（引数 → `FASTEMBED_CACHE_PATH`
→ `<tempdir>/fastembed_cache`）を再現し、warning に実パスと復旧コマンドを載せる。

```
WARNING helper.helper_embedding_sparse - Sparse Embedding の初期化に失敗しました
  （model=prithivida/Splade_PP_en_v1）: [ONNXRuntimeError] ... File doesn't exist.
  → 以降このプロセスでは sparse を試行せず dense 検索のみで動作します（検索は継続します）。
  → キャッシュ破損が原因の場合は次を削除して再実行してください: rm -rf /var/folders/8b/.../T/fastembed_cache
```

⚠️ 削除は**利用者が実行する**。プロセスが勝手に消すと、同時に動いている
別プロセス（データ登録バッチ等）のモデルを引き抜くことになる。

---

## 6. 関連ファイル

| 対象 | 場所 |
|---|---|
| Ollama クライアント（期限・リトライ・思考抑止・空応答診断） | `helper/helper_llm.py` |
| 予算の既定値 | `grace/config.py`（`LLMConfig` / `PlannerConfig` / `JudgeConfig`） |
| 予算の実効値 | `config/grace_config.yml` |
| 根拠検証 | `grace/confidence.py`（`GroundednessResult` / `GroundednessVerifier`） |
| 回答ゲートと救済 | `backend/app/core/gates.py` |
| ⑤ Web フォールバック | `backend/app/core/support_agent.py` |
| LLM 計画生成の無効化 | `grace/planner.py`（`_llm_plan_disabled`） |
| 冗長な検索ステップの除去 | `grace/replan.py`（`_drop_redundant_search_steps`） |
| Sparse Embedding | `helper/helper_embedding_sparse.py` |
| reasoning プロンプト（現在日時の注入） | `grace/tools.py`（`ReasoningTool._now_text`） |
| 出典の種別ラベル | `grace/tools.py`（`ReasoningTool._source_origin`） |
| 検索スコープ（汎用コーパスの除外） | `grace/config.py`（`QdrantConfig.excluded_collections`）/ `grace/tools.py`（`_apply_excluded_collections`） |
| 閾値の実測スクリプト | `scripts/measure_rag_threshold.py` |

### 環境変数

| 変数 | 既定 | 用途 |
|---|---|---|
| `OLLAMA_TIMEOUT` | `180` | LLM 1 リクエストの期限（秒） |
| `OLLAMA_REASONING_EFFORT` | `none` | 思考抑止。`off` で送らない |
| `OLLAMA_DEFAULT_MODEL` | `config.py` 参照 | 非思考モデルへ替えるならここ |

### テスト

| 内容 | 場所 |
|---|---|
| 予算の不変条件 | `backend/tests/test_timeout_budget.py` |
| 補助判定のスイッチ | `backend/tests/test_local_llm_degradation.py` |
| 検証器の失敗と救済 | `backend/tests/test_verification_failure.py` |
| Sparse の negative cache | `backend/tests/test_sparse_embedding_cache.py` |
| 空応答の診断ログ | `backend/tests/test_empty_response_diagnostics.py` |
| 思考抑止と増幅の停止 | `backend/tests/test_thinking_only_model.py` |
| 緩和結果の採用ルール | `backend/tests/test_rag_relaxed_adoption.py` |
| reasoning プロンプトの現在日時 | `backend/tests/test_current_date_in_prompt.py` |
| 閾値スクリプトの判定ロジック | `backend/tests/test_measure_rag_threshold.py` |
| 出典の帰属 | `backend/tests/test_source_attribution.py` |
| 統計キーの正準名 | `backend/tests/test_confidence_factor_keys.py` |
| 検証・埋め込みの重複排除 | `backend/tests/test_groundedness_cache.py` / `test_query_vector_reuse.py` |
| 汎用コーパスの除外 | `backend/tests/test_excluded_collections.py` |

---

## 7. 既定モデル `gemma4-e4b-ctx8k` について

**これは `ollama pull` で取れる公開モデルではない。** 手元で作る派生モデルなので、
新しい環境ではセットアップが 1 手増える。

```bash
ollama pull gemma4:e4b
printf 'FROM gemma4:e4b\nPARAMETER num_ctx 8192\n' > /tmp/Modelfile
ollama create gemma4-e4b-ctx8k -f /tmp/Modelfile
```

### なぜ num_ctx を広げるのか

Ollama の既定 `num_ctx` は 4096。空応答 8 件のトークン数がそれを示している。

| max_tokens | prompt_tokens | completion_tokens | 合計 |
|--:|--:|--:|--:|
| 4096 | 1330 | 2766 | **4096** |
| 4096 | 1225 | 2871 | **4096** |
| 8192 | 2177 | 1919 | **4096** |
| 8192 | 2163 | 1933 | **4096** |

**`prompt + completion = 4096` が全件で成立する。** `max_tokens=8192` は
到達不可能で、プロンプトが 2163 トークンあると生成に使えるのは 1933 だけ。
そこを思考が食い尽くして本文が 0 文字になっていた。

### 未作成のまま起動すると

`ollama` が 404 を返す。**モデル名が間違っているわけではない**ので、
`ollama list` に `gemma4-e4b-ctx8k` があるか先に確認すること。

暫定的に元のモデルへ戻すなら環境変数で上書きできる。

```bash
OLLAMA_DEFAULT_MODEL=gemma4:26b-a4b-it-qat ./run_dev.sh
```

⚠️ **26b も `reasoning_effort=none` が効く環境なら 1:58 で完走する**
（§3.5 の実測）。既定を e4b にしているのは速度と VRAM の余裕のためで、
26b が使えないからではない。

---

## 8. 社内ナレッジに無い話題で無関係文書が出典に載る件

「明日の東京の天気は？」に対し、出典一覧の先頭に
**「社内 qa_pairs_combined_chunks.csv」** が出ていた。中身は AI の変遷・
インドネシア首都移転・著作権・地理学・日本語学で、天気とは無関係。

⚠️ 混同しやすい点: ログの `[RAG SEARCH IPO: OUTPUT]` に見える
`payload.question`（「コンピュータ科学における…」等）は**ヒットした文書側の
質問文**であって、検索クエリではない。クエリは全行で
`query='明日の東京の天気は？'` と正しく送られている。

原因は `RAGSearchTool.execute` の採用判定にあった 2 つの欠陥。

### 欠陥 A: 保留が「最初に検索した 1 つ」だった

```python
if not fallback_results:      # ← 後続がどれだけ高くても捨てる
    fallback_results = results
```

実測（10 コレクションの Top スコア）:

| コレクション | Top | |
|---|--:|---|
| `wikipedia_ja_5per` | 0.5375 | ← **採用**（最初に検索されたから） |
| `cc_news_2per_anthropic` | 0.6658 | 最高スコアなのに破棄 |
| `fineweb_edu_ja_5per` | 0.6058 | 破棄 |
| `ec_faq_anthropic` | 0.6009 | 破棄 |

**最下位が採用されていた。** 選択基準が「関連度」ではなく「検索順」だった。
→ 最高スコアのコレクションを保留するように修正。

### 欠陥 B: スコアがいくつでも無条件に採用していた

12 コレクションすべてが一次閾値 0.7 に届かない＝**社内ナレッジに該当なし**、
という判定自体は正しかった。にもかかわらず最後に緩和結果を採用していた。

しかもこれらは `reasoning_min_rag_score`(0.55) で reasoning プロンプトから
除外されており（ログの「14 件 → 9 件」）、**回答には 1 文字も寄与せず、
出典としてだけ表示される**状態だった。

→ 採用の下限を `executor.reasoning_min_rag_score` と**同じ値**にした。

> **不変条件: 推論に使えない文書は、出典としても採用しない。**

別々の定数に分けると、この食い違いが再発する。

### 影響

| 影響先 | 修正前 | 修正後 |
|---|---|---|
| 出典表示 | 社内 CSV が先頭に載る | RAG 0 件 → Web 出典のみ |
| groundedness | 検証ソース 14 件中 5 件が無関係 | 無関係分が消える |
| step confidence | `search_max_score=0.5375` に引きずられる | 低スコアに引きずられない |
| reasoning | 影響なし（もともと除外されていた） | 同左 |

テスト: `backend/tests/test_rag_relaxed_adoption.py`

---

## 9. 「明日」が解決できない件 — reasoning プロンプトに現在日付が無い

### 現象

質問「明日の東京の天気は？」に対し、Web 検索は 8/16 の予報を取得済み、
groundedness も **1.00**（＝述べた内容は情報源に忠実）。それでも回答は:

> 「明日」という日付が具体的にいつを指すのかについての定義が不足している
> ため、確定した情報を提示することができませんでした

### 原因

**LLM は「今日が何日か」を知らない。** プロンプトに日付が無いので、
参照情報に答えがあっても相対表現（明日・今週・先月）を解決できない。

**どのゲートでも弾けない。** groundedness は満点、出典もあり、回答も空でない。
「情報が足りない」と誠実に答えているだけで、ルール上は正しい振る舞いである。
落ちているのは**入力プロンプトの欠落**だけ。

### 修正

`ReasoningTool._build_prompt` の先頭（システム指示の直後・参照情報より前）に
`### 【現在日時】` を挿入する。

```
今日は 2026年08月16日（日曜日）14:53 です。
「明日」は 2026年08月17日（月曜日）を指します。
質問に「明日」「今週」「先月」などの相対的な日付表現が含まれる場合は、
上記を基準に具体的な日付へ読み替えて参照情報を解釈してください。
```

明日の日付を**こちらで計算して渡す**（`timedelta(days=1)`）。LLM に
計算させると月末・年末をまたぐケースを落とす。曜日は「今週の金曜」等の
解決に要るので日本語で添える。

### 期待できる効果の範囲

これは **推論が正しくなる**修正であって、**答えが増える**修正ではない。
上の実測では Web 検索が 8/16（＝今日）の予報しか取ってきていないので、
日付を渡した後の正しい回答は「明日（8/17）の予報は情報源に無い」になりうる。
それが正しい挙動である（誤った日付の予報を明日として出す方が有害）。

テスト: `backend/tests/test_current_date_in_prompt.py`

---

## 10. 採用閾値 0.55 の妥当性 — 測り方

`executor.reasoning_min_rag_score`(0.55) は §8 で「推論にも引用にも使う下限」
に格上げしたが、**0.55 という値自体に根拠は無い。**

実測では、社内ナレッジに該当が 1 件も無い質問（「明日の東京の天気は？」）でも
緩和検索の最良スコアが **0.6658** ある。この 1 例だけを見れば 0.7 まで上げたく
なるが、上げすぎると本当に答えられる質問まで出典 0 件になる。**両側を測らないと
決められない。**

### 手順

```bash
# 前提: Qdrant 起動済み・Embedding が使える
PYTHONPATH=. python3 scripts/measure_rag_threshold.py --vertical gov
PYTHONPATH=. python3 scripts/measure_rag_threshold.py --vertical all

# 実運用の質問ログを使う（推奨）
PYTHONPATH=. python3 scripts/measure_rag_threshold.py --queries-file myqueries.json
```

`myqueries.json`:

```json
{
  "in_scope":     ["住民票の写しの取り方は？", "..."],
  "out_of_scope": ["明日の東京の天気は？", "..."]
}
```

### 読み方

| 指標 | 意味 |
|---|---|
| TP フロア = `in_scope` の**最小** Top スコア | これ未満にすると取りこぼす |
| FP シーリング = `out_of_scope` の**最大** Top スコア | これ以下だと誤採用する |

- `FP シーリング < TP フロア` → その中間が安全。スクリプトが推奨値を出す。
- `FP シーリング >= TP フロア` → **スコアだけでは分離できない。**
  閾値をどこに置いても取りこぼすか誤採用するかになる。
  → Embedding モデル・チャンク粒度・コレクション分割の側を見直す。

平均ではなく**最小 / 最大**で見るのが要点。平均で決めると、1 件の低い
`in_scope` を取りこぼす閾値や、1 件の高い `out_of_scope` を通す閾値を
推奨してしまう。

得られた値は `config/grace_config.yml` の `executor.reasoning_min_rag_score`
に書く（コード変更は不要）。

> ⚠️ **無関係コレクションの混在も疑うこと。** 業界プロファイル（gov/saas/ec）で
> 絞れば `cc_news_*` や `wikipedia_*` は検索対象から外れる。閾値を上げる前に、
> そもそもそのコレクションを検索範囲に入れるべきかを見直す方が効く場合がある。

テスト: `backend/tests/test_measure_rag_threshold.py`（判定ロジックのみ。
検索本体は Qdrant が要るので CI では回さない）

---

## 11. 出典の帰属を偽らない（P0-1）

### 現象

「明日の東京の天気は？」への回答:

> Yahoo!天気によると、…確認できる情報源があります（**社内ナレッジ（web_search）**）。

Yahoo!天気は社内ナレッジではない。

### 原因

回答規則が出典の種別を問わず 1 つの書式を強制していた。

```python
"3. **出典の明示**: 回答の根拠となった情報がある場合、"
"「社内ナレッジ（出典ファイル名）によると...」の形式で出典を明示してください。\n"
```

LLM は指示どおりに従っただけである。

### なぜゲートで捕まらないのか

| 指標 | 実測 | 判定 |
|---|--:|---|
| groundedness | 1.00 | 述べている内容自体は情報源に忠実 → 減点なし |
| 出典数 | 10 | あり |
| 回答本文 | 984 字 | あり |

**どのゲートも通る。** 根拠の信頼性を売りにするシステムで、外部 Web を
社内の裏付けとして提示するのは、静かに起きるぶん最も危険な壊れ方である。

### 修正

参照情報の見出しに種別を付け、規則を種別で分岐させる。

```
--- 情報源 6 【Web】 (信頼度: 1.00, コレクション: web_search) ---
--- 情報源 1 【社内】 (信頼度: 0.67, コレクション: cc_news_2per_anthropic) ---
```

```
3. 出典の明示（種別を偽らない）: 各情報源の見出しにある種別を必ずそのまま使う
   - 【社内】→「社内ナレッジ（出典ファイル名）によると...」
   - 【Web】 →「Web 検索結果（サイト名または URL）によると...」
   ⚠️ Web で得た情報を「社内ナレッジ」と書いてはいけません。
```

判定は `ReasoningTool._source_origin()`。`collection == "web_search"` を第 1 の印、
出典が URL 形式かを第 2 の印とする（画面の出典ラベルを作る
`gates._collect_citations` と同じ規則）。

テスト: `backend/tests/test_source_attribution.py`

---

## 12. Web ステップの信頼度が静かに壊れていた（P1-2）

`Executor` はツールの統計をこう読む。

```python
search_max_score      = factors.get("max_score", factors.get("avg_score", 0.0))
search_score_variance = factors.get("score_variance", 1.0)
```

**キー名が違っても例外にならず、黙って既定値へ落ちる。** `WebSearchTool` は
`top_score` / `score_spread` を返していたため、実測でこうなっていた。

```
Initial factors  : {'result_count': 9, 'avg_score': 0.6, 'top_score': 1.0, 'score_spread': 0.8}
ConfidenceFactors: search_max_score=0.6        ← avg が入る（実際は 1.0）
                   search_score_variance=1.0   ← 既定（実際は 0.067）
```

最高スコアが平均に潰れ、ばらつきは常に最悪値。Web ステップの信頼度が
不当に低く出ていた（実測の `[CONFIRM] 信頼度が低いため確認が必要です 66.6%`）。
RAG 側は正準名を返していたので、**Web だけが壊れていた**。

修正は 2 つ。

1. `WebSearchTool._calculate_confidence_factors` が正準キー
   （`max_score` / `min_score` / `score_variance`）を返す。`top_score` /
   `score_spread` はログ互換のため併存させる（`score_spread` は range であって
   variance ではないので流用しない）。
2. `Executor._warn_on_missing_score_keys` を追加し、検索ステップの統計に
   正準キーが無ければ **warning を出す**。次の乖離を沈黙させない。

テスト: `backend/tests/test_confidence_factor_keys.py`

---

## 13. 同じ検証・同じ埋め込みを繰り返さない（P1-1 / P1-3）

実測 2:00 の内訳。

| 区間 | 処理 | 秒 |
|---|---|--:|
| 20:11:13→20:11:23 | RAG 12 コレクション（Embedding ×12） | 10.2 |
| 20:11:23→20:11:25 | Web 検索 | 1.4 |
| 20:11:25→20:11:29 | source_agreement（Embedding ×9） | 4.3 |
| 20:11:29→20:12:10 | reasoning | 40.8 |
| 20:12:10→20:12:26 | evaluate_final | 16.3 |
| 20:12:26→20:12:53 | **groundedness（executor）** | 27.3 |
| 20:12:53→20:13:13 | **groundedness（③ 根拠評価）** | 19.9 |

### P1-1: groundedness の二重実行

`executor._blend_groundedness_confidence` と `support_agent` の ③ が、
**同じ回答・同じ 14 件のソース**を別インスタンスで検証していた（起動ログに
`GroundednessVerifier initialized` が 2 行出ていたのがその印）。合計 47 秒＝
リクエスト全体の 39%。温度 0 なので 2 回目に新しい情報は無い。

- `GroundednessVerifier` に入力キーのメモを持たせる（インスタンス単位・
  リクエスト単位・上限 4 件）
- `support_agent` は `executor.groundedness_verifier` を共有する

⚠️ **失敗（`verification_failed`）はキャッシュしない。** タイムアウトや空応答は
入力ではなく実行時の事情なので、次は成功しうる。失敗を覚えると 1 回の瞬断で
後続の全経路が「検証不能」に固定され、§4 の救済判断まで巻き添えになる。

入力が違えば従来どおり検証する（⑤ の Web 回答検証はそのまま動く）。

### P1-3: 同一クエリの再埋め込み

`RAGSearchTool.execute` は全コレクションを順に舐めるが、
`search_rag_knowledge_base_structured` に `precomputed_query_vector` を
渡していなかったため、**1 質問でコレクション数ぶんの Embedding API 呼び出し**が
飛んでいた（実測 12 回、すべて同じクエリ）。クエリベクトルはコレクションに
依存しないので結果は毎回同じ。外部 API なので課金にも効く。

`_embed_query_once()` で dense / sparse を 1 回だけ作って配る。作れなかった側は
kwargs に載せないので、失敗時は**この最適化が無い状態へ戻るだけ**で検索は続く。

テスト: `backend/tests/test_groundedness_cache.py` /
`backend/tests/test_query_vector_reuse.py`

---

## 14. コンソール出力を二重にしない（P2-1）

`grace/tools.py` が IPO ログを `logger.info` と `print` の**両方**で出していた。
root logger は stdout に出すため、コンソールに同じ JSON が 2 回並ぶ。実測ログが
倍に膨れていた原因がこれ。`print` を削除し、logger に一本化した（コレクション
探索の進捗・バックエンド切替・コレクション取得失敗も同様）。

---

## 15. 閾値の実測結果 — 問題は閾値ではなくスコープだった

§10 の手順で実測した（`--vertical all` ＝「基本版」タブの条件）。

| | n | 最小 | 中央 | 最大 |
|---|--:|--:|--:|--:|
| in_scope（拾いたい） | 12 | **0.6650** | 0.7714 | 0.8253 |
| out_scope（拾いたくない） | 5 | 0.6507 | 0.6658 | **0.7054** |

```
TP フロア 0.6650  <  FP シーリング 0.7054   → ✗ 分離できない
```

**閾値をどこに置いても取りこぼすか誤採用するかになる。**
0.6650 未満なら「SSO の設定手順は？」を落とし、0.7054 以上なら
「今日の日経平均株価は？」を通す。

### 決定的だったのは Top を取ったコレクション

| | Top を取ったコレクション |
|---|---|
| in_scope 12 件 | **12/12 が `gov_*` / `saas_*` / `ec_*`**（業務コレクション） |
| out_scope 5 件 | **5/5 が `cc_news_*` / `fineweb_*`**（検証用の汎用コーパス） |

重なりを作っているのは業務データではない。`cc_news_2per_anthropic` は
ニュース記事の Q&A なので、**時事的な質問すべてに中程度にマッチする**。

| out_scope クエリ | Top | コレクション |
|---|--:|---|
| 今日の日経平均株価は？ | **0.7054** | `cc_news_2per_anthropic` |
| 円ドル相場の見通しは？ | 0.6813 | `cc_news_2per_anthropic` |
| 明日の東京の天気は？ | 0.6658 | `cc_news_2per_anthropic` |
| 今年のノーベル物理学賞は？ | 0.6578 | `cc_news_2per_anthropic` |
| 近くのおいしいラーメン屋 | 0.6507 | `fineweb_edu_ja_5per` |

**検索の質そのものは良好である**（in_scope の 12/12 で意図どおりの業務
コレクションが Top を取っている）。壊れているのは検索範囲の方。

### とくにまずい 0.7054

一次閾値 0.7 を**超える**ため、緩和採用ではなく「十分なヒット」として即採用
され、`RAG score sufficient (0.7054 >= 0.7)` で **Web 裏取りまで飛ばされる**。
古いニュース記事だけで「今日の株価」に答える経路に入る。天気のケース
（0.6658 → 緩和採用 → Web 併用）より一段悪い。

### 対処: 閾値ではなくスコープを直す

`qdrant.excluded_collections` を新設し、汎用コーパスを**横断フォールバックの
候補から外す**。

```yaml
qdrant:
  excluded_collections:
    - "cc_news"
    - "fineweb"
    - "wikipedia"
    - "livedoor"
    - "japanese_text"
```

判定は `search_priority` / `allowed_collections` と同じ部分一致。3 つの安全弁を置く。

| 条件 | 挙動 |
|---|---|
| `allowed_collections` あり（業界プロファイル） | **除外を重ねない。** プロファイルが指定した範囲がスコープそのもの（gov は `wikipedia_ja` を明示的に含む） |
| `rag_search(collection=...)` で名指し | 除外しない |
| 除外すると候補が 0 件になる | **除外を見送り、警告する。** 業務コレクション未登録の環境でデモを殺さない |

### 除外後に測り直した — 分離が成立した

対象コレクションが 20 → 6（業務コレクションのみ）になった状態で再実行。

| | n | 最小 | 中央 | 最大 |
|---|--:|--:|--:|--:|
| in_scope | 12 | **0.6650** | 0.7714 | 0.8253 |
| out_scope | 5 | 0.5615 | 0.6004 | **0.6190** |

```
FP シーリング 0.6190  <  TP フロア 0.6650   → ✓ 分離できる
推奨閾値 = 0.64
```

**in_scope は 12 件すべて Top スコア・採用コレクションとも除外前と完全に一致**
した。汎用コーパスは業務質問の答えを 1 つも持っていなかった、ということ。
一方 out_scope は全件下がった。

| out_scope クエリ | 除外前 | 除外後 | 差 |
|---|--:|--:|--:|
| 今日の日経平均株価は？ | 0.7054 | 0.6004 | −0.105 |
| 今年のノーベル物理学賞は？ | 0.6578 | 0.5615 | −0.096 |
| 円ドル相場の見通しは？ | 0.6813 | 0.5948 | −0.087 |
| 明日の東京の天気は？ | 0.6658 | 0.6009 | −0.065 |
| 近くのおいしいラーメン屋 | 0.6507 | 0.6190 | −0.032 |

一次閾値 0.7 を超える out_scope は消えた（＝Web 裏取りを飛ばす経路が塞がった）。

`executor.reasoning_min_rag_score` を **0.55 → 0.64** に反映する。

> ⚠️ **マージンは 0.046 しかない。暫定値として扱うこと。**
> サンプルが in 12 / out 5 と少なく、新しい質問 1 件で崩れうる。とくに最小の
> in_scope（「SSO の設定手順は？」0.6650）との余裕は 0.025。運用の質問ログが
> 溜まったら `--queries-file` で測り直す。
>
> また FP シーリングを作っているのは、もはや汎用コーパスではなく
> `ec_faq_anthropic`（配送・支払いの一般的な FAQ）である。ここから先を
> 下げるにはスコープではなく Embedding かデータ側の話になる。

テスト: `backend/tests/test_adoption_threshold.py`

---

## 16. 残る課題

| # | 内容 | 状況 |
|---|---|---|
| P0-2 | 採用閾値の確定 | **解決（0.64）。** ただしマージン 0.046 の暫定値。質問セットを増やして測り直す |
| P0-3 | ④' 情報なし検知が素通りする | **解決。** ただし想定と違う経路で発火した。下記参照 |
| — | Web 情報源どうしの誤帰属 | 種別（社内/Web）は正しくなったが、**どの Web 情報源か**を取り違える |
| — | 回答に内部番号が露出（「情報源7」） | プロンプト内部の通し番号がユーザーに見える |
| — | Web はスニペットのみ（`content` が空） | 本文取得は未実装 |
| — | `cc_news_2per` の同一元データ 6 バリアント | データ側の整理（`_768` / `_anthropic` / `_gemini` / `_ollama` / 無印 / `cc_news_100_ollama`） |
| — | `cc_news_2per_768` が 3072 次元として扱われる | 名前と実体の不一致 |

### 再測定の手順

```bash
# 除外あり（＝本番と同じ条件）
PYTHONPATH=. python3 scripts/measure_rag_threshold.py --vertical all

# 除外の効果を before/after で比べる
PYTHONPATH=. python3 scripts/measure_rag_threshold.py --vertical all --include-excluded

# 業界プロファイル使用時（除外は重ねない＝プロファイル範囲がスコープ）
PYTHONPATH=. python3 scripts/measure_rag_threshold.py --vertical gov
```

> 測定スクリプトは `RAGSearchTool._get_all_collections_dynamic()` を直接呼ぶ
> ようにした。**測定条件が本番と一致していることを構造的に保証する。**
> 以前は Qdrant の全コレクションを素で舐めていたため、768 次元のコレクションへ
> 3072 次元のクエリを投げて 1 実行あたり **272 回の 400 Bad Request** を出し、
> かつ本番では検索されないコレクションのスコアを測っていた。

### P0-3 が P0-2 の連鎖である理由

実測の回答は「断定することはできません」「専門の天気予報サイトを参照される
ことをお勧めします」＝ `gates.py` の判定基準そのままの**情報なし回答**。
それでもゲートを通ったのは 2 つの理由による。

1. **第 1 段のマーカーが当たらない。** `NO_INFO_MARKERS` は「見当たりません／
   見つかりません／確認できません／情報がありません」の 6 語のみ。
2. **`force_judge` が発火しない。** `web_only`（出典が全部 `[Web]`）のときだけ
   第 2 段の LLM 判定を強制するが、P0-2 で採用された**無関係な社内 CSV が
   1 件混ざったせいで False** になった。

つまり誤採用が、それを捕まえるはずの安全網を無効化している。閾値で 0.6658 を
弾いていれば出典は Web のみ → `force_judge=True` → `judges.enabled=false` なので
判定器は None → `_detect_no_info_answer` は安全側の True へ倒れて escalate した。

**P0-2 を直せば P0-3 は連鎖で解消する。** マーカー拡充は、その後に必要かどうかを
判断する（先に足すと過検知でエスカレが増える）。

### 実測: 除外だけで解消した — ただし想定と違う経路で

§15 の除外を入れて「明日の東京の天気は？」を再実行したところ、
④' が発火して escalate へ倒れた。

```
7. ✓ ④' 情報なし回答検知  情報なし回答を検知 → escalate
8. ✓ ⑥ Action  escalate_to_human
```

⚠️ **予測していた `force_judge` 経由ではない。** 出典に社内 `ec_faq.csv` が
1 件残っていた（0.6009 ≥ 当時の閾値 0.55）ため、`web_only` は False のまま。

実際に効いたのは**第 1 段のマーカー**だった。回答本文に「見当たりませんでした」
が 2 回出ている。

> 明日の日付（8月17日（月））に関する情報は、この情報源の提示された範囲内には
> **見当たりませんでした**

つまり除外の効果は「安全網を開いた」ではなく **「モデルが正直になった」**。
`cc_news_*` の天気 Q&A（バトンルージュの予報など）が消えたことで断定材料を
失い、素直に「見当たらない」と言うようになった。

### 副次的に、前回の捏造 3 件がすべて消えた

| 除外前 | 除外後 |
|---|---|
| `webath.co.jp` という**存在しないドメイン**を出典として提示 | 消滅（全 URL が実在） |
| 新宿区「明後日は曇のち雨 32/25」← 実際は **8/21 のデータ**（4 日ずれ） | 「明日の日付に関する情報は見当たりませんでした」＝正しい |
| 千代田区の値を「東京都」の予報として一般化 | 消滅 |

⚠️ **この 3 件はいずれも groundedness 1.00（6/6 supported）を通っていた。**
「支持率 1.00」は「正しい」を意味しない。ローカル軽量モデルの検証器は
日付の取り違えもドメイン名の捏造も検出できない。

閾値を 0.64 にすると `ec_faq.csv`（0.6009）も落ちるため、出典が Web のみ＝
`web_only=True` となり、**マーカーに依存しない `force_judge` 経路も開く**
（二重の安全網）。

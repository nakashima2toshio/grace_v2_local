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
`/var/folders/.../fastembed_cache/` を消して再取得する。

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

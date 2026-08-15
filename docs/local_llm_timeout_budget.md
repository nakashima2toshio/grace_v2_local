# ローカル LLM のタイムアウト予算と、遅さの内訳

**最終更新: 2026-08-15** | ステータス: 実装済み

同じ質問（gov「住民票の写しの取り方は？」）に対する実測。

| リポジトリ | LLM | 所要時間 | 結果 |
|---|---|--:|---|
| `grace_v2` | Anthropic `claude-sonnet-4-6` | **63 秒** | answer（groundedness 1.00 / confidence 0.82 / 出典 10 件） |
| `grace_v2_local`（修正前） | Ollama `gemma4:26b-a4b-it-qat` | **1 時間 17 分 46 秒** | **escalate**（groundedness 判定不能） |

パイプラインは同一で、LLM 呼び出し回数も 10 数回で変わらない。差は
**1 呼び出しの所要時間（4〜10 秒 vs 90〜250 秒）**と、その前提で組まれて
いなかった予算設計にある。本書はその設計を記録する。

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

> 補足: `finish_reason=length` で本文 0 文字になるのは枠不足ではない。
> 512 / 1024 / 4096 / 8192 のいずれでも同じく 0 文字で返る（モデルが出力を
> 終端できていない）。**枠を上げても直らない。**

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

---

## 6. 関連ファイル

| 対象 | 場所 |
|---|---|
| Ollama クライアント（期限・リトライ） | `helper/helper_llm.py` |
| 予算の既定値 | `grace/config.py`（`LLMConfig` / `PlannerConfig` / `JudgeConfig`） |
| 予算の実効値 | `config/grace_config.yml` |
| 根拠検証 | `grace/confidence.py`（`GroundednessResult` / `GroundednessVerifier`） |
| 回答ゲートと救済 | `backend/app/core/gates.py` |
| ⑤ Web フォールバック | `backend/app/core/support_agent.py` |
| Sparse Embedding | `helper/helper_embedding_sparse.py` |

### テスト

| 内容 | 場所 |
|---|---|
| 予算の不変条件 | `backend/tests/test_timeout_budget.py` |
| 補助判定のスイッチ | `backend/tests/test_local_llm_degradation.py` |
| 検証器の失敗と救済 | `backend/tests/test_verification_failure.py` |
| Sparse の negative cache | `backend/tests/test_sparse_embedding_cache.py` |

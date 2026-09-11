# チャンク化の所要時間を測る

**Version 1.0** | 最終更新: 2026-09-10

チャンク化が「遅い」のか「壊れている」のかを、**目視ではなく数字で**切り分ける手順。

---

## 1. なぜ測るのか

2026-09 に同じ症状を 4 回追いかけた。そのたびに 1 リクエストあたりの秒数を
tqdm の表示から目で拾っていたが、画面を流れて消えるうえ、条件を変えて
比べられない。

```
Step1: 段落分割:  0%| | 1/1229 [09:03<185:17:07, 543.18s/it]
```

**543 秒/ブロックは「遅い」ではなく「全部失敗している」**。
`CHUNKING_LLM_TIMEOUT` の既定 180 秒 × リトライ 3 回 = 540 秒であり、
1 件も成功していない。この区別が付かないまま「遅いのだろう」と待ち続けると、
1229 ブロック分の時間を捨てたうえに**機械的な分割だけの CSV** が出来上がる。

実行の最後にサマリが出るようになったので、1 回の実行で判断できる。

---

## 2. 20 行で測る

全件回す前に、小さく回して 1 件あたりの秒数を掴む。

```bash
# 前提: ollama serve が動いている
ollama list                     # 使うモデルが pull 済みか確認

uv run python -m chunking.csv_text_to_chunks_text_csv \
  --input-file OUTPUT/cc_news_2per.csv \
  --output output_chunked \
  --max-rows 20 \
  --workers 8 \
  --verbose
```

実行の最後にこれが出る。

```
============================================================
所要時間（実測）
============================================================
  モデル: gemma4:12b-mlx / 並列ワーカー数: 8
  Step1 段落分割             62.4 秒  /    20 件  =     3.12 秒/件
  Step2 意味的分割           41.8 秒  /    34 件  =     1.23 秒/件
  Step3 連続性チェック       28.1 秒  /    29 件  =     0.97 秒/件
------------------------------------------------------------
  合計                      132.3 秒  /    83 件  =     1.59 秒/件
```

---

## 3. 数字の読み方

| 秒/件 | 判断 |
|---:|---|
| **540 前後** | **失敗している。** タイムアウト 180 秒 × リトライ 3 回。ログに 404 / timeout が出ているはず |
| **180 前後** | タイムアウト 1 回ぶん。リトライで成功しているが、実質ぎりぎり |
| 数十秒 | 遅い。モデルが大きすぎるか、同時実行がさばけていない（§4） |
| 数秒 | 正常 |

⚠️ **秒/件は待ち時間込みである。** `workers` 個を同時に投げても Ollama 側が
直列に処理していれば後続はキューで待つので、1 件あたりの見かけの時間は
「実際の生成時間 × 同時実行数」に近づく。

---

## 4. workers を変えて比べる

既定は `--workers 8`。ローカル LLM ではこれが**遅くする側に働く**ことがある。

```bash
# 直列
uv run python -m chunking.csv_text_to_chunks_text_csv \
  --input-file OUTPUT/cc_news_2per.csv --output output_chunked \
  --max-rows 20 --workers 1 --verbose

# 既定
uv run python -m chunking.csv_text_to_chunks_text_csv \
  --input-file OUTPUT/cc_news_2per.csv --output output_chunked \
  --max-rows 20 --workers 8 --verbose
```

両方のサマリの「合計 … 秒/件」を比べる。

| 結果 | 意味 | 対処 |
|---|---|---|
| workers=1 の方が**速い**（秒/件が小さい） | Ollama が同時実行をさばけておらず、キュー待ちが乗っている | `--workers 2` 程度へ下げる。または `OLLAMA_NUM_PARALLEL` を上げる |
| workers=8 の方が速い | 同時実行が効いている | 既定のままでよい |
| どちらも 540 前後 | 並列度の問題ではない。**全リクエストが失敗している** | §5 へ |

`OLLAMA_NUM_PARALLEL` は Ollama サーバ側の設定。

```bash
OLLAMA_NUM_PARALLEL=4 ollama serve
```

---

## 5. まず診断スクリプトを回す

秒/件が 180 の倍数（＝タイムアウト）だったら、原因の切り分けはこれ 1 回で済む。

```bash
uv run python -m chunking.diagnose_ollama
uv run python -m chunking.diagnose_ollama --model gemma4:26b-mlx   # 別モデルを見る
```

タイムアウト無しで 3 回生成し、**生成トークン数・速度・停止理由・本文の
有無**を直接出す。タイムアウトのログは「途中で切った」としか言わないので、
何回眺めても下の 4 つを区別できない。

| | 内容 |
|---|---|
| ① | 短い生成（`num_predict=64`）— 素の速度とモデルロード時間 |
| ② | チャンク化と同じ「形」だが**短縮プロンプト** — 参考値 |
| ③ | **本番の Step1 リクエストそのもの** — ここが結論 |

> ⚠️ **結論は ③ でしか出ない。** ② は `/api/generate` に短縮プロンプトを
> 投げるだけで、本番（`/v1/chat/completions`・JSON モード・分割ルール＋
> スキーマ添付・1000 文字を逐語で出力し直す）より**ずっと軽い**。
>
> 実測 2026-09-11 はこの差で判断を誤った。**② は 74.3 秒で成功したのに、
> 本番リクエストは 180 秒で切れ続けていた。** 軽い方を測って「動く」と
> 読むのが一番まずい。③ は実際に失敗した block_24 の逐語を使う。

| 出力 | 原因 |
|---|---|
| ③ が 180 秒超 | `CHUNKING_LLM_TIMEOUT` の既定では必ず落ちる。延ばすか軽いモデルへ |
| 生成速度が 1 桁 tok/s | モデルが重すぎる。小さいモデルを試す |
| **思考だけで本文が空** | 思考モデル。`reasoning_effort='none'` が効いていない → 別モデルへ |
| 停止理由が `length` | 上限まで生成し切っている。num_predict がそのまま最悪時間になる |

### 思考モデルの実測例

`gemma4:26b-a4b-it-qat` は本文を一度も出さないことがある:

```
finish_reason=length, max_tokens=4096, completion_tokens=2766,
thinking=10007 chars (key=reasoning),
message_keys=['reasoning', 'role']      ← content が存在しない
```

生成した 10007 文字はすべて `reasoning` に入り、本文には 1 文字も到達しない。
`helper/helper_llm.py` は `reasoning_effort="none"` を送って抑止しているが、
**対応は Ollama のバージョン依存**なので効かない環境があり得る。

### ⚠️ 構造化出力が抑止を素通りしていた（2026-09-11 修正）

チャンク化は `generate_structured()` を使う。この関数は
`_create_completion()` を通らず `client.chat.completions.create()` を
**直接**呼んでいたため、**`reasoning_effort` が 1 度も送られていなかった**。
起動ログと実際の送信内容が食い違う:

```
OllamaClient initialized: ... reasoning_effort=none   ← 設定済みに見える
Request options: {... 'max_tokens': 8192,             ← 実際の payload に
                      'response_format': {...}}          reasoning_effort が無い
```

思考モデルは本文へ到達する前に枠を使い切るので、抑止の効かない経路では
1 リクエストが上限まで走り、180 秒で切られ続ける。

同時に、本文が空のとき `model_validate_json("")` へ直行していたため、
残るログは «Invalid JSON: EOF while parsing» と空の `Raw response:` だけで、
`finish_reason` も生成トークン数も思考の有無も**失われていた**。

どちらも `backend/tests/test_thinking_only_model.py::
TestStructuredOutputTakesTheSamePath` が固定している。既存のテストは
`generate_content()` だけを見ていたので、この経路をすり抜けていた。
**片方の経路にだけ入れた対策は、もう片方を見ないと気付けない。**

---

## 6. 失敗しているときに見る順番

1. **モデル名** — サマリの「モデル:」が `ollama list` に**そのままの文字列で**
   あるか。実例 2026-09-11: `OLLAMA_DEFAULT_MODEL=gemma4:e4b` に対し、
   実在するのは `gemma4:e4b-mlx`（`-mlx` 付き）だった。**差は 1 語**。

   実行前チェックが pull 済み一覧つきで弾くので、まずその出力を読む。
   一覧を取れない環境（Ollama 停止中など）では判定不能として素通りする。

   ### `OLLAMA_DEFAULT_MODEL` がどこから来ているか分からないとき

   ⚠️ **リポジトリ直下に `.env` が無くても、この値は来る。**
   `load_dotenv()` は引数なしだと `find_dotenv()` で**親ディレクトリを
   遡って** `.env` を探すため、1 つ上や home の `.env` を拾う。
   シェルの環境変数や `~/.zshrc` から来ていることもある。

   ```bash
   env | grep OLLAMA_DEFAULT_MODEL                       # ① シェルの環境変数
   python3 -c "from dotenv import find_dotenv; print(find_dotenv() or '(なし)')"
                                                          # ② 実際に読まれる .env
   grep -n OLLAMA_DEFAULT_MODEL ~/.zshrc ~/.zprofile ~/.zshenv 2>/dev/null
                                                          # ③ プロファイル
   ls -la .env ../.env ../../.env ~/.env 2>/dev/null      # ④ 上位の .env
   ```
2. **ログの 404 / timeout** — `model '...' not found` なら 1 に戻る
3. **中断メッセージ** — 連続失敗が既定 3 回に達すると
   `ChunkingAbortedError` で止まる（`CHUNKING_ABORT_AFTER_FAILURES`）。
   1229 ブロック分を捨てる前に理由付きで止まる
4. **タイムアウトを延ばす** — モデルが単に遅いだけなら
   `CHUNKING_LLM_TIMEOUT=600` で伸ばせる。既定は 180 秒

---

## 7. 関連する環境変数

| 変数 | 既定 | 効果 |
|---|---|---|
| `OLLAMA_DEFAULT_MODEL` | `gemma4:12b-mlx` | 既定モデル（`config.py::get_default_ollama_model()`） |
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | 接続先 |
| `CHUNKING_LLM_TIMEOUT` | 未設定（`OllamaClient` の既定） | 1 リクエストの期限（秒） |
| `CHUNKING_ABORT_AFTER_FAILURES` | `3` | 連続失敗で中断する回数。`0` で無効 |
| `OLLAMA_NUM_PARALLEL` | Ollama 側の既定 | サーバの同時処理数（`ollama serve` に渡す） |

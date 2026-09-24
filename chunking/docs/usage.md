# チャンク化の使い方

**Version 1.1** | 最終更新: 2026-09-24

---

## 目次

1. [概要](#概要)
2. [前提](#1-前提)
3. [最小の実行](#2-最小の実行)
4. [まず 20 行で試す](#3-まず-20-行で試す)
5. [分割チャンキング（刻んで回す）](#4-分割チャンキング刻んで回す)
6. [全件チャンキング](#5-全件チャンキング)
7. [出力の確認](#6-出力の確認)
8. [環境変数](#7-環境変数)
9. [Celery は使わない](#8-celery-は使わない)
10. [うまくいかないとき](#9-うまくいかないとき)
11. [変更履歴](#10-変更履歴)

---

## 概要

`chunking/` を実際に動かすための手順書。**運用の入口はこの 1 本**にまとめてある。

| 目的 | 参照先 |
|---|---|
| 動かしたい・全件を回したい | **本書** |
| 遅い・止まらない・失敗する | [`timing.md`](timing.md) |
| 関数の入出力を知りたい | [`csv_text_to_chunks_text_csv.md`](csv_text_to_chunks_text_csv.md)（IPO 形式） |

> **種別 B**（`a_cross_doc_md_format.md` §6。手順書）。**状態: 現行（チャンク化の運用の唯一の入口）**

### 結論

- `ollama serve` が動いていれば、`python -m chunking.csv_text_to_chunks_text_csv` でチャンク化できる（Embedding を使わないので `GOOGLE_API_KEY` は不要）
- まず §3 の 20 行で試し、大きいデータは §4 の分割チャンキングで刻んで回す
- チャンク化は Celery を使わない（§8）。遅い・失敗するときは [`timing.md`](timing.md) を見る

### 対象モジュール

| # | モジュール | 関係 |
|---|---|---|
| 1 | `chunking/csv_text_to_chunks_text_csv.py` | チャンク化 CLI（本書の対象） |
| 2 | `chunking/async_api_client.py` | Ollama への並列呼び出し |
| 3 | `config.py`（`OllamaConfig`） | 接続先・既定モデル |

---

## 1. 前提

ローカル LLM（Ollama）が動いていること。**Embedding は使わない**ので `GOOGLE_API_KEY` は不要。

```bash
ollama serve                    # 別ターミナルで常駐（または open -a Ollama）
ollama list                     # 使うモデルが pull 済みか確認
```

Ollama が落ちていると**実行前に 1 秒で止まる**（起動コマンド付きのエラーが出る）。
モデルが未 pull の場合も同様に、pull 済み一覧を添えて止まる。

> ⚠️ モデル名は `ollama list` の表示と**そのままの文字列**で一致させること。
> `gemma4:e4b` と `gemma4:e4b-mlx` のような 1 語差が実際に起きている。

### Ollama を起動し直す

設定（`OLLAMA_NUM_PARALLEL` など）は**起動時にしか読まれない**ので、変えたら
必ず再起動する。

```bash
lsof -nP -iTCP:11434 -sTCP:LISTEN   # 誰が掴んでいるか
pkill -x Ollama                     # macOS アプリ本体を落とす（-x は完全一致）
sleep 2 && pgrep -fl ollama         # 空になったことを確認

OLLAMA_NUM_PARALLEL=4 ollama serve  # 設定を変えて起動
```

⚠️ **`Ollama.app` はヘルパーを監視していて、子プロセスだけ `kill` しても
即座に起動し直す。** ポートが空かないのはこれが理由。落とすのは本体
（大文字 `Ollama`）で、サーバは小文字 `ollama` と別物。

---

## 2. 最小の実行

```bash
uv run python -m chunking.csv_text_to_chunks_text_csv \
  --input-file OUTPUT/cc_news_2per.csv \
  --output output_chunked
```

### ⚠️ `--output` は省略しないこと

CLI の既定は **`chunks_output`** だが、本プロジェクトの規約（CLAUDE.md §8.2）と
下流の Q/A 生成（`qa_qdrant/make_qa_register_qdrant.py`）が見るのは
**`output_chunked`** である。省略すると次の工程がファイルを見つけられない。

### オプション一覧

| オプション | 既定 | 内容 |
|---|---|---|
| `--input-file` | **必須** | 入力 CSV / テキスト |
| `--output` | `chunks_output` | 出力先。**`output_chunked` を明示する** |
| `--model` | `gemma4:12b-mlx` | `config.py::get_default_ollama_model()` |
| `--workers` | `1` | 並列数。**上げても速くならない**（§5） |
| `--block-size` | `1000` | Step1 の入力ブロック（文字） |
| `--max-rows` | 全行 | **先頭 N 行**。オフセット指定は無い（§4） |
| `--text-column` | 自動判定 | テキスト列名 |
| `--combine-rows` | off | 全行を結合して 1 本として扱う |
| `--resume` | なし | ジョブ ID を指定して**ステップ単位**で再開（§4） |
| `--verbose` | off | DEBUG ログ。プロンプト全文が流れるので通常は不要 |

---

## 3. まず 20 行で試す

全件は長い（§5）。**必ず小さく回して所要時間と品質を確かめてから**本番へ進む。

```bash
uv run python -m chunking.csv_text_to_chunks_text_csv \
  --input-file OUTPUT/cc_news_2per.csv --output output_chunked \
  --max-rows 20
```

実行の最後にサマリが出る。

```
Step1 段落分割       3319.0 秒 /  55 件 = 60.35 秒/件
Step2 意味的分割      4006.2 秒 /  79 件 = 50.71 秒/件
Step3 連続性チェック   1446.1 秒 / 204 件 =  7.09 秒/件
------------------------------------------------------
合計                8771.3 秒 / 338 件 = 25.95 秒/件
```

`gemma4:12b-mlx` での実測は **20 行で約 2 時間 26 分**。
秒/件の読み方と異常時の切り分けは [`timing.md`](timing.md) を参照。

---

## 4. 分割チャンキング（刻んで回す）

### ⚠️ `--max-rows` と `--resume` では刻めない

| オプション | 実際の挙動 |
|---|---|
| `--max-rows N` | `df.head(N)` ＝ **常に先頭 N 行**。21〜40 行目は指定できない |
| `--resume <job_id>` | **ステップ単位**の再開。`checkpoints/<job_id>/step1.json` があれば Step1 を飛ばす。Step2 の途中では再開できない |

`--resume` が救えるのは「Step1 が終わって Step2 で落ちた」場合に Step1 の時間だけ。
**行の分割実行には使えない。**

### 入力 CSV を先に分ける

```bash
# ① 100 行ずつに分割
uv run python -c "
import pandas as pd
df = pd.read_csv('OUTPUT/cc_news_2per.csv')
for i in range(0, len(df), 100):
    df.iloc[i:i+100].to_csv(f'OUTPUT/cc_news_2per_part{i//100}.csv', index=False)
    print(f'part{i//100}: {len(df.iloc[i:i+100])} 行')"

# ② 1 本ずつ回す（各 約 12 時間）
for p in 0 1 2 3 4; do
  uv run python -m chunking.csv_text_to_chunks_text_csv \
    --input-file OUTPUT/cc_news_2per_part${p}.csv --output output_chunked
done

# ③ 出力を 1 本にまとめる
uv run python -c "
import glob, pandas as pd
files = sorted(glob.glob('output_chunked/cc_news_2per_part*_chunks.csv'))
out = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
out.to_csv('output_chunked/cc_news_2per_chunks.csv', index=False)
print(f'{len(files)} ファイル → {len(out)} チャンク')"
```

**利点**: 途中で止められる。1 本が失敗してもその 1 本だけやり直せる。
**注意**: `chunk_id` は分割ファイルごとに振り直されるので、結合後に重複しうる。
Qdrant 登録時に一意な ID が要る場合は結合後に振り直すこと。

---

## 5. 全件チャンキング

### 所要時間（実測からの外挿）

| 対象 | 行数比 | 所要 |
|---|---|---|
| 20 行 | 1× | **2 時間 26 分**（実測） |
| 100 行 | 5× | 約 12 時間 |
| 500 行（`cc_news_2per.csv` 全件） | 25× | **約 61 時間** |

### 一気に回す

```bash
nohup uv run python -m chunking.csv_text_to_chunks_text_csv \
  --input-file OUTPUT/cc_news_2per.csv --output output_chunked \
  > logs/chunking_full.log 2>&1 &
```

`--resume` はステップ間の落ちだけを救う（Step1 完了後に落ちれば Step1 を飛ばせる）。

### ⚠️ `--workers` を上げても速くならない

既定は `1`（`config.py::get_default_chunking_workers()`）。**Ollama は既定で
1 本ずつしか処理しない**ので、8 本投げても 7 本はキューで待つだけで、
その待ち時間が各リクエストのタイムアウトを食いつぶす。

実測では `--workers 8` で 509 秒かけて 7 ブロックしか進まず、単発の
62.7 秒/ブロックと変わらないまま後続がタイムアウトした（詳細は
[`timing.md`](timing.md) §2.5）。

本当に並列化するには Ollama 側を増やす。`--workers` の既定はそれに追随する。

```bash
OLLAMA_NUM_PARALLEL=4 ollama serve
```

上げたら必ず 秒/件 を測り、**実際に下がったことを確かめること。**

### そのほかの短縮手段

- **軽いモデルにする** — ただし §6 の文字数チェックが必須
- **分割して回す**（§4）— 時間は変わらないが、途中で止められる

---

## 6. 出力の確認

### 2 つの CSV が出る

| ファイル | カラム |
|---|---|
| `<名前>_chunks.csv` | **`text`**（小文字）＋ `tokens` / `chunk_id` / `chunk_idx` / `dataset_type` / `sentence_count` / `source_file` |
| `<名前>_chunks_simple.csv` | **`Text`**（大文字）のみ |

⚠️ **大文字小文字を取り違えると `KeyError` になる。**

### 文字落ちが無いか突き合わせる

このパイプラインは**入力を逐語で書き写す**工程を含む。指示追従の弱いモデルは
文を落とすことがあり、**それはエラーにならない** — スキーマとして正しい JSON が
返るので成功扱いになり、中身だけが欠ける。

```bash
uv run python -c "
import pandas as pd
df = pd.read_csv('output_chunked/cc_news_2per_chunks.csv')
chars = df['text'].astype(str).str.len().sum()
print(f'チャンク数: {len(df)} / 総文字数: {chars:,} / 総トークン: {df[\"tokens\"].sum():,}')"
```

実行ログ冒頭の「総サイズ」と突き合わせる。

| 総文字数 | 判断 |
|---|---|
| 入力とほぼ同じ | 正常。`gemma4:12b-mlx` の実測は 54,767 / 54,800 ＝ **99.94%** |
| 少し下回る | 改行が空白へ畳まれる分（`normalize_whitespace=True`）。許容 |
| **大きく下回る** | **文を落としている。** 速くてもそのモデルは採用できない |

---

## 7. 環境変数

| 変数 | 既定 | 効果 |
|---|---|---|
| `OLLAMA_DEFAULT_MODEL` | `gemma4:12b-mlx` | 既定モデル（`config.py::get_default_ollama_model()`） |
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | 接続先 |
| `OLLAMA_NUM_PARALLEL` | Ollama 側の既定 | サーバの同時処理数。`--workers` の既定が追随する |
| `CHUNKING_LLM_TIMEOUT` | 未設定（`OllamaClient` の既定 180 秒） | 1 リクエストの期限（秒） |
| `CHUNKING_ABORT_AFTER_FAILURES` | `3` | 連続失敗で中断する回数。`0` で無効 |

> ⚠️ `OLLAMA_DEFAULT_MODEL` はリポジトリ直下に `.env` が無くても効く。
> `load_dotenv()` が親ディレクトリを遡るほか、シェルのプロファイル
> （`~/.zshrc` 等）も見る。出所の追い方は [`timing.md`](timing.md) §6。

---

## 8. Celery は使わない

**本スクリプトは Celery を経由しない。** `asyncio` で直接 Ollama を叩く。
`./start_celery.sh` が要るのは次の工程（Q/A 生成）で `--use-celery` を
付けるときだけで、チャンク化の速度には一切関係しない。

```bash
# Step2: Q/A 生成 + Qdrant 登録（こちらは Celery を使える）
./start_celery.sh restart -c 4 --flower

uv run python qa_qdrant/make_qa_register_qdrant.py \
  --input-file output_chunked/cc_news_2per_chunks.csv \
  --collection cc_news_2per \
  --use-celery --recreate
```

⚠️ Q/A 生成も同じ 1 台の Ollama を使うので、**concurrency を上げれば速くなる
わけではない**。ここでも効くのは `OLLAMA_NUM_PARALLEL` の方である。

---

## 9. うまくいかないとき

[`timing.md`](timing.md) に症状別の切り分けがある。

| 症状 | 参照 |
|---|---|
| 秒/件が 180 の倍数（180 / 360 / 543） | §1 — タイムアウト。「遅い」ではなく「1 件も成功していない」 |
| `Connection error` が連続 | §5.4 — Ollama が起動していない |
| `Field required` ＋ スキーマが `Raw response` に出る | §5.5 — モデルがスキーマをオウム返し |
| 原因が分からない | §5 — `uv run python -m chunking.diagnose_ollama` |

---

## 10. 変更履歴

| バージョン | 変更内容 |
|---|---|
| 1.1 | `a_cross_doc_md_format.md` の種別 B の骨格へ揃えた（2026-09-24）。目次と番号なしの「概要」（状態・結論・対象モジュール）を追加した。本文と章番号は変えていない |
| 1.0 | 新規作成。モジュール docstring と `timing.md` §2.6 に散っていた運用手順を集約（2026-09-11） |

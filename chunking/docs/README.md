# chunking/docs/ 棚卸し

**Version 1.1** | 最終更新: 2026-09-24

> 📎 **姉妹版**: [`grace/docs/README.md`](../../grace/docs/README.md) /
> [`backend/docs/README.md`](../../backend/docs/README.md)

`chunking/docs/` 配下のドキュメントを一覧化する。**目的から入口を引ける**ようにするのが狙い。

---

## 1. 目的別の入口

| やりたいこと | 読む文書 |
|---|---|
| **動かす・全件を回す** | [`usage.md`](usage.md) |
| **遅い・止まらない・失敗する** | [`timing.md`](timing.md) |
| **関数の入出力を知る** | [`csv_text_to_chunks_text_csv.md`](csv_text_to_chunks_text_csv.md) |
| **非同期クライアントの内部** | [`async_api_client.md`](async_api_client.md) |

---

## 2. 一覧

> 形式の「手順書」は**種別 B**（`a_cross_doc_md_format.md` §6）、「IPO」は**種別 E**（`a_class_method_md_format.md`。使用例は IPO 詳細の冒頭 `### 4.1`）。本索引は種別 C。
>
> `memo.txt` はコマンドの走り書き（種別 D）で、旧モジュール名 `csv_to_chunks_text_para` と旧オプション（`-i` / `-o` / `-w` / `--list-jobs`）のままである。現行の手順は `usage.md` を見ること。

| ファイル | 形式 | 内容 | 重要度 |
|---|---|---|---|
| [`usage.md`](usage.md) | 手順書 | 前提・オプション・分割チャンキング・全件・出力の確認。**運用の唯一の入口** | ★★★ |
| [`timing.md`](timing.md) | 手順書 | 所要時間の測り方と症状別の切り分け。実測基準値（60 秒台＝健全 / 180 の倍数＝タイムアウト）、診断スクリプトの読み方 | ★★★ |
| [`csv_text_to_chunks_text_csv.md`](csv_text_to_chunks_text_csv.md) | IPO | 主モジュールのクラス・関数仕様。アーキテクチャ図とモジュール構成図を含む | ★★☆ |
| [`async_api_client.md`](async_api_client.md) | IPO | `AsyncAPIClient` の仕様。並列制御・リトライ・中断 | ★★☆ |
| [`check_async.md`](check_async.md) | IPO | 非同期処理の検証ツール。⚠️ **対象の `check_async.py` は本リポジトリに存在しない**（git 履歴にも無い）。削除候補 | ☆☆☆ |

---

## 3. 書き分けの約束

**運用手順は `usage.md` にだけ書く。**

同じ手順を複数箇所に書くと、片方だけ直したときに食い違う。実際に
`--max-rows` で刻む誤った手順を 3 回案内した（`--max-rows` は `df.head(N)` で
オフセットが無く、刻めない）。

| 置き場所 | 書くもの | 書かないもの |
|---|---|---|
| `usage.md` | 実行手順・オプション・運用判断 | 内部実装 |
| `timing.md` | 測り方・症状別の切り分け・実測値 | 実行手順（`usage.md` へリンク） |
| `<module>.md` | IPO（入出力・副作用） | 実行手順 |
| モジュール docstring | 最小の実行例と `usage.md` へのリンク | 詳細な手順 |

> IPO 形式の仕様は `.claude/skills/grace-agent-docs/a_class_method_md_format.md`。
> 使い方を混ぜると形式が崩れるので、`<module>.md` には入れない。

---

## 4. 変更履歴

| Version | 日付 | 変更 |
|---|---|---|
| 1.0 | 2026-09-11 | 新規作成。`usage.md` 新設にあわせて棚卸しを整備 |
| 1.1 | 2026-09-24 | 文書種別（B / C / D / E）を明記し、`check_async.md` の対象実装が存在しないこと・`memo.txt` が旧コマンドのままであることを注記。`usage.md` / `timing.md` を種別 B の骨格へ、IPO 2 文書の使用例を IPO 詳細の冒頭へ揃えた |

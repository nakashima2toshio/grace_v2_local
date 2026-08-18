# 規程コレクションの準備（`ec_ad_rules_anthropic`）

GRACE-Review が「条文つきの指摘」を出すために必要な Qdrant コレクションの作り方。

---

## 1. なぜ必要か

`ec_ad` の検索スコープは 2 つだが、**`ec_ad_rules_anthropic` が未登録**である。

```
検索スコープ: ec_ad_rules_anthropic, ec_policy_anthropic
（未登録コレクションは条文フォールバックを使用）
```

実測（2026-08-17 20:07 〜 2026-08-18 21:41）では、常時チェック 7 ルール中 **6 つが
規程 0 件**だった。

```
doc/tokusho-01: 文書全体で判定 / 規程 0 件
doc/tokusho-02: 文書全体で判定 / 規程 0 件
doc/tokusho-03: 文書全体で判定 / 規程 0 件
doc/tokusho-05: 文書全体で判定 / 規程 0 件
doc/tokusho-06: 文書全体で判定 / 規程 0 件
doc/policy-01:  文書全体で判定 / 規程 0 件
```

実在するのは `ec_policy_anthropic`（返品・返金・交換の FAQ）だけで、**特商法の条文は
1 件も入っていない**。そのため:

- 指摘の根拠がすべて `RuleItem.description`（条文フォールバック）になる
- `policy-01`（社内規程との整合）が**原理的に機能しない** — 自社の「返品 14 日」を
  根拠として引けないため、「8 日 vs 14 日」を検出できない
- **RAG 経路が一度も検証されていない**

---

## 2. CSV の形式

`qa_qdrant/register_to_qdrant.py` は `question` と `answer` があれば、
`question + "\n" + answer` を Embedding 対象として自動検出する。

| 列 | 必須 | 用途 |
|---|---|---|
| `question` | ✅ | Embedding 対象。**UI の引用ラベル**（`[規程] …`）にもなる |
| `answer` | ✅ | Embedding 対象。**④ Ground へ渡す根拠本文**になる |
| `topic` | — | payload の来歴（任意）。`chunk_id` / `doc_id` も同様に保持される |

Review 側の読み取り（`review_agent._retrieve_evidence`）:

```python
title = payload.get("title") or payload.get("question") or "(規程)"
body  = payload.get("answer") or payload.get("text") or ""
```

### ⚠️ `evidence_min_score = 0.70` を超える必要がある

`RuleSet.evidence_min_score`（既定 0.70）未満の規程は根拠として採用されない。
② Retrieve の検索クエリは **ルール本文**である。

```python
_retrieve_evidence(tool_registry, f"{rule.title} {rule.description}", rs, ...)
```

したがって `question` に**ルールのタイトル**、`answer` に**その条文の内容**を置くと、
埋め込み対象がクエリとほぼ同じ文になり、閾値を余裕で超える。逆に、無関係な語彙で
書くと 0.70 に届かず条文フォールバックのままになる（`ec_policy_anthropic` の
返品 FAQ が 0.6784 止まりだったのがその例）。

---

## 3. 手順

### 3-1. 雛形 CSV を書き出す

```bash
PYTHONPATH=. python3 scripts/export_ruleset_to_csv.py --ruleset ec_ad
# → qa_output/ec_ad_rules.csv（22 行）
```

出力例:

```csv
question,answer,topic
景品表示法 第5条第1号（優良誤認表示）,商品・サービスの品質、規格その他の内容について…,優良誤認
特定商取引法 第11条（販売価格・送料の明示）,通信販売の広告には、商品の販売価格（消費税込み）を…,表記漏れ
```

### 3-2. ⚠️ `answer` を実際の条文へ置き換える

**ここが本番。** 書き出された `answer` は `RuleItem.description`、つまり
**このリポジトリ自身の要約**である。`rulesets.py` の冒頭にあるとおり:

> 本ルールセットは技術検証用のサンプルであり、法務レビューを受けていない。

**登録しただけでは、根拠の中身は条文フォールバックと同じ**になる。RAG が意味を
持つのは、`answer` を実際の条文・ガイドライン本文へ置き換えてからである。

置き換えの出典として想定されるもの:

| 法令 | 一次情報 |
|---|---|
| 特定商取引法 | e-Gov 法令検索の条文、消費者庁「特定商取引法ガイド」の通信販売の広告表示 |
| 景品表示法 | e-Gov の条文、消費者庁の運用基準（No.1 表示・打消し表示・二重価格表示の各ガイドライン） |
| 医薬品医療機器等法 | e-Gov の条文、「医薬品等適正広告基準」および解説通知 |

1 条文が長い場合は、**判定に使う単位で行を分ける**（例: 第11条を「販売価格・送料」
「支払時期・方法」「引渡時期」「返品特約」「事業者情報」の 5 行に分ける）。
`question` は必ず対応するルールのタイトルを含めること — 含めないと検索が当たらない。

> **法務監修を通してから本番運用すること。** 誤った条文を根拠として提示すると、
> 指摘そのものが誤りになる。

### 3-3. `policy-01` 用に自社規程の行を足す

`policy-01`（表示内容と社内規程の不一致）は**自社の実データ**が無いと機能しない。
検索クエリはこれ:

```
表示内容と社内規程の不一致 広告に表示した取引条件（返品期限・送料負担・解約条件・
価格など）が、社内規程に定めた条件と食い違っていないかを確認する。…
```

現状 `ec_policy_anthropic` の返品 FAQ は **0.6784** で 0.70 に届かない。以下のように
**取引条件を一覧化した行**を足すと当たりやすくなる。

```csv
question,answer,topic
社内規程（取引条件の一覧）,返品期限: 商品到着後14日以内かつ未使用・未開封。お客様都合の返品は返送料をお客様負担。不良品・誤配送は当ストア負担。交換: 商品到着後14日以内、未使用品に限り1回まで。解約: 定期購入は次回お届け予定日の5日前までにマイページから。解約手数料なし。,規程不一致
```

> ⚠️ **この行の中身は各社の実際の規程に置き換えること。** 上の例は
> `ec_policy_anthropic` に既に登録されている値を一覧化しただけで、
> 監修済みの規程ではない。

#### `policy-01` は条文コレクションを検索しない

`policy-01` は `RuleItem.evidence_query` / `evidence_collections` で **② の検索を
上書き**している（`rulesets.py`）。

| | 既定のルール | `policy-01` |
|---|---|---|
| クエリ | `f"{title} {description}"` | 取引条件の語（`返品 交換 解約 送料 期限 …`） |
| 検索先 | `RuleSet.collections`（2 つ） | `ec_policy_anthropic` **のみ** |

**理由は自己一致。** `ec_ad_rules_anthropic` にはルール自身が 1 行として入って
いるので、ルール本文で検索すると自分を引き当てる（実測 2026-08-19 06:11 で
**0.9380**）。本命の「返品規定（14日）」は 0.6647 で、絶対閾値にも
`evidence_top_ratio`（0.9380 × 0.92 = 0.863）にも阻まれて**構造的に採用されない**。

したがって **`policy-01` を機能させるには `ec_policy_anthropic` 側に取引条件が
入っている必要がある。** 上の 3-3 の行はそちらへ登録する。

```bash
python qa_qdrant/register_to_qdrant.py \
  --input-file qa_output/ec_policy_terms.csv \
  --collection ec_policy_anthropic
```

⚠️ **`--recreate` を付けないこと。** 既存の返品・返金 FAQ を消してしまう。

### 3-4. Qdrant へ登録する

```bash
python qa_qdrant/register_to_qdrant.py \
  --input-file qa_output/ec_ad_rules.csv \
  --collection ec_ad_rules_anthropic \
  --recreate
```

| オプション | 意味 |
|---|---|
| `--recreate` | 既存の同名コレクションを削除して作り直す（初回・入れ替え時） |
| `--provider gemini` | 既定。`gemini-embedding-001`（3072 次元）で他コレクションと揃う |
| `--domain` | payload の `domain`（既定はコレクション名） |
| `--batch-size` | 既定 100。22 行なら指定不要 |

⚠️ **`--provider` は既定（gemini）のまま使う。** `openai` にすると次元が変わり、
`RAGSearchTool` の「次元不一致」フィルタで**検索対象から静かに外れる**。

登録は**冪等**である（ポイント ID が `question + answer` の内容ハッシュ）。同じ CSV を
再登録しても重複しない。

「データ管理」タブ（`./run_dev.sh` → :5173）からも登録できる。`qa_output/` は
`ALLOWED_INPUT_DIRS` に含まれているので、入力ファイルとして選択できる。

---

## 4. 登録後の確認

```bash
# 件数
curl -s http://localhost:6333/collections/ec_ad_rules_anthropic | jq '.result.points_count'

# 次元（3072 であること）
curl -s http://localhost:6333/collections/ec_ad_rules_anthropic \
  | jq '.result.config.params.vectors'
```

そのうえで Review を実行し、ログがこう変わることを確認する。

```
# 変更前
doc/tokusho-01: 文書全体で判定 / 規程 0 件

# 変更後（期待）
doc/tokusho-01: 文書全体で判定 / 規程 5 件
```

`規程 0 件` のままなら、`[retrieve] 関連度が低い規程を根拠にしません（< 0.70）: …`
のログにスコアが出ているので、`question` の文言がルールのタイトルと噛み合っているかを
見直す（3-2 の注意点）。

### ⚠️ 採用件数は 5 件のうち 1〜2 件が正常

登録すると `検索: 5 件` になるが、**根拠として採用されるのはそのうち上位の
1〜2 件だけ**である。コレクションの中身は「互いに似た条文 22 行」なので、どの
ルールで検索しても他ルールの条文が絶対閾値 0.70 を超えて付いてくる。

```
  [retrieve] 最上位より離れた規程を根拠にしません（< 0.7903）:
    特定商取引法 第11条（代金の支払時期・方法）(0.7422),
    特定商取引法 第11条（事業者名・住所・連絡先）(0.7374), …
```

これは正常な動作である（`RuleSet.evidence_top_ratio`、既定 0.92）。落とさないと
③ Detect の【規程】に他ルールの主題が混ざり、**指摘文が越境する**（実測: 支払時期の
指摘が引渡時期まで書き、引渡時期のルールも別途発火して二重計上になった）。

条文を実データへ差し替えて **1 条を複数行に分けた**場合、同じルールの条文は僅差で
並ぶので両方採用される。逆に「同じルールの条文なのに落ちている」なら、その行の
`question` がルールのタイトルと噛み合っていない（3-2）。

---

## 5. 関連

| 項目 | 場所 |
|---|---|
| ルール定義 | `backend/app/core/rulesets.py` |
| 検索と閾値 | `backend/app/core/review_agent.py::_retrieve_evidence` |
| 閾値の値と根拠 | `rulesets.DEFAULT_EVIDENCE_MIN_SCORE` |
| 登録 CLI | `qa_qdrant/register_to_qdrant.py` |
| 雛形の書き出し | `scripts/export_ruleset_to_csv.py` |
| パイプライン全体 | `backend/docs/data_pipeline.md` |

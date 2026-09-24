# docs 棚卸し（リポジトリ直下 `docs/`）

**Version 2.2** | 最終更新: 2026-09-24

リポジトリ直下 `docs/` の一覧と、**どのディレクトリに何を置くかの境界**をまとめる。
各領域の棚卸しは [`backend/docs/README.md`](../backend/docs/README.md) /
[`grace/docs/README.md`](../grace/docs/README.md) /
[`frontend/docs/README.md`](../frontend/docs/README.md) /
[`chunking/docs/README.md`](../chunking/docs/README.md) にある。

> ⚠️ **本リポジトリは Ollama 版。** LLM は `gemma4:12b-mlx`
> （`config.py::get_default_ollama_model()`）で **API キーは不要**、
> Embedding のみ Gemini `gemini-embedding-001`（3072 次元・`GOOGLE_API_KEY`）。
> 姉妹リポジトリ `grace_v2` は Anthropic 版で**表記が逆**（CLAUDE.md §3・§5・§9.3）。

---

## 目次

- [1. なぜ本書が要るか](#1-なぜ本書が要るか)
- [2. 配置の境界（どこに何を置くか）](#2-配置の境界どこに何を置くか)
- [3. 文書一覧](#3-文書一覧)
- [4. 横断文書の重複禁止ルール](#4-横断文書の重複禁止ルール)
- [5. 重複の検出](#5-重複の検出)
- [6. 残タスク](#6-残タスク)
- [7. 変更履歴](#7-変更履歴)

---

## 1. なぜ本書が要るか

**直下 `docs/` だけ索引が無かった。** `backend/docs/` / `grace/docs/` /
`frontend/docs/` / `chunking/docs/` には棚卸しの `README.md` があるのに、
直下 `docs/` には無く、12 文書＋2 ディレクトリが並んでいるだけだった。
（`services/docs/` / `qa_generation/docs/` / `qa_qdrant/docs/` にも無かったが、
2026-09-20 に作成して**全 8 領域が索引を持つ**状態になった。）

索引が無いと**境界が誰にも見えない**ので、同じ内容が別の場所へもう一度書かれる。
実際、`frontend/docs/` では索引が無かったためにコンポーネント 5 件の文書欠落が
長く検知されずに残っていた（2026-09-20 に `frontend/docs/README.md` で解消）。

本書は同じ事故を直下 `docs/` で防ぐためのもので、
**境界（§2）・正本の一覧（§4）・重複の検出（§5）**を持つ。

---

## 2. 配置の境界（どこに何を置くか）

CLAUDE.md §9.1 の表を、判断に使える形へ具体化したもの。

| 置き場所 | 置くもの | 判断の目安 |
|---|---|---|
| `<package>/docs/<module>.md` | Python モジュールの IPO | **1 ファイル = 1 文書**。`chunking/` `qa_generation/` `qa_qdrant/` `services/` `grace/` |
| `backend/docs/` | `backend/app/**` の IPO ＋ Support / Review の spec・flow | 対象が `backend/app/` に閉じているか |
| `frontend/docs/` | React コンポーネント 1 件ごと | 対象が `frontend/src/` に閉じているか |
| **直下 `docs/`** | **2 つ以上の領域にまたがる横断文書**、およびトップレベル `.py` の IPO | 「`backend/` だけ」「`grace/` だけ」で説明しきれないもの |

### 2.1 直下 `docs/` に置いてよいかの判定

```
その文書が説明する実装は、1 つの領域（backend / grace / frontend / <package>）に閉じているか？
  ├─ はい  → その領域の docs/ へ置く。直下 docs/ には置かない
  └─ いいえ → 直下 docs/ へ置く。ただし §4 の「正本」欄に同じ資産が無いか先に確認する
```

**実例**: `guardrails.md` は `backend/app/core/gates.py` ＋ `grace/confidence.py` ＋
`grace/executor.py` ＋ `support_actions.py` にまたがるので直下。
`local_llm_timeout_budget.md` は `config.py` ＋ `grace/` ＋ `chunking/` ＋
Ollama の実測にまたがるので直下。

### 2.2 書式（フォーマット仕様）

直下 `docs/` の文書は、まず**種別**を決め、種別に応じた仕様で書く
（`.claude/skills/grace-agent-docs/a_cross_doc_md_format.md` §1）。§3 の各表の「種別」列がそれである。

| 種別 | 内容 | 仕様 |
|---|---|---|
| A 横断文書 | 2 領域以上にまたがる機構の説明 | `a_cross_doc_md_format.md` §2〜§5（**概要に主な責務・各責務対応のモジュール・3 層の構成図**） |
| B 調査メモ・設計案 | 調査結果・提案・手順メモ | 同 §6（**概要に結論・対象モジュール**） |
| C TODO・索引 | 進行中タスク・本書 | 同 §7.1 |
| D 資材 | ログ・画像 | 同 §7.2（書式不問。空ファイル・空白入りファイル名は禁止） |
| E モジュール IPO | トップレベル `.py` の IPO | `a_class_method_md_format.md` |

> ⚠️ **トップレベル `.py` の IPO は直下 `docs/` が現状の置き場所**である
> （`agent_parallel_search.md`）。パッケージに属さないため `<package>/docs/` が作れない。
> 横断文書と混ざるが、これを分けるために 1 ファイルのためのディレクトリは切らない。

---

## 3. 文書一覧

> 行数・Ver は **2026-09-24 の実測値**（`wc -l` と各文書の Version ヘッダー）。記憶で書かないこと。

### 3.1 横断文書（2 つ以上の領域にまたがる）

| 文書 | 種別 | 内容 | またがる領域 | 行数 | Ver |
|---|:--:|---|---|---:|---|
| `pipelines.md` | A | **3 モード対照のハブ**（基本版 / Support / Review）。ステップ対照表・実行順・基本版との差・ガードレール有効表 | backend + frontend | 248 | 1.3 |
| `guardrails.md` | A | ガードレール GA〜G9 の機構 → 実装 → **失敗時の既定** | backend + grace + ルート | 393 | 1.2 |
| `reasoning_flow.md` | A | 生成の 2 ステップ（Support の `reasoning` / Review の `detect`） | grace + backend | 387 | 2.1 |
| `performance_levers.md` | A | 回答品質・レイテンシを決めている箇所と未実装レバー | 全域 | 551 | 2.1 |
| `api_flow.md` | A | GRACE-Support の API フロー一覧（0 〜 ⑥ の 8 段階） | backend + grace | 591 | 2.3 |
| `multi_question_handling.md` | B | 複数質問クエリへの対応（0-(A) 入力・質問分析）。§0 が実装の正、§1 以降は採用しなかった案の記録 | backend + frontend + grace | 709 | 3.1 |
| `agent_layers.md` | A | **一般エージェント用語 → 実装の対応表**（L0〜L4）。実装を読む前の見取り図 | 全域 | 450 | 1.1 |

### 3.2 本リポジトリ固有（Ollama 版であることに由来）

| 文書 | 種別 | 内容 | 行数 | Ver |
|---|:--:|---|---:|---|
| `local_llm_timeout_budget.md` | B | **ローカル LLM のタイムアウト予算と、遅さの内訳**。実測に基づく | 1012 | 1.1 |
| `migration_anthropic2ollama_inventory.md` | B | Anthropic → Ollama 移植インベントリ | 444 | 1.2 |

> 📌 この 2 本は `grace_v2`（Anthropic 版）には**存在しない**。
> 姉妹リポジトリへ持っていこうとしないこと（前提が違う）。

### 3.3 モジュール IPO（トップレベル `.py`）

| 文書 | 種別 | 対象 | 行数 | Ver |
|---|:--:|---|---:|---|
| `agent_parallel_search.md` | E | `agent_parallel_search.py` — 並列検索エンジン（`ThreadPoolExecutor`）。⚠️ **Legacy ReAct 経路専用で Web アプリからは未稼働**（CLAUDE.md §1・§9.4） | 714 | 1.1 |

### 3.4 進行中・完了した TODO

| 文書 | 種別 | 内容 | 行数 | Ver |
|---|:--:|---|---:|---|
| `port_from_grace_v2_todo.md` | C | grace_v2 からの移植 TODO。**A〜E は 2026-09-20 に完了**、F は「移植しない」と結論済み | 373 | 1.5 |
| `data_tab_port_todo.md` | C | データ管理タブ移植の記録（2026-08-03 時点。⚠️ 以降の実装で状況が変わった箇所がある旨を冒頭に明記済み） | 453 | 1.3 |

### 3.5 その他

| 文書 | 種別 | 内容 | 行数 | Ver |
|---|:--:|---|---:|---|
| `pytest_coverage.md` | B | pytest カバレッジレポートの読み方（手順メモ） | 107 | 1.1 |

### 3.6 資材ディレクトリ

種別はすべて D（書式不問）。

| ディレクトリ | 内容 |
|---|---|
| `images/` | README・各文書が参照するスクリーンショット 6 件（`comparison/` を含む） |
| `LLM/` | モデル別の ReAct 実行ログ 4 ファイル（`react_ollama_*.md` 3 件と課題文 `bug_code_check_llm.txt`） |


### 3.7 各領域の棚卸し索引

| 索引 | 対象 | 作成日 |
|---|---|---|
| [`../backend/docs/README.md`](../backend/docs/README.md) | `backend/app/**` | — |
| [`../grace/docs/README.md`](../grace/docs/README.md) | `grace/**` | — |
| [`../frontend/docs/README.md`](../frontend/docs/README.md) | React コンポーネント | 2026-09-20 |
| [`../chunking/docs/README.md`](../chunking/docs/README.md) | ① チャンク化 | 2026-09-11 |
| [`../qa_generation/docs/README.md`](../qa_generation/docs/README.md) | ② Q/A 生成 | 2026-09-20 |
| [`../qa_qdrant/docs/README.md`](../qa_qdrant/docs/README.md) | ③ Qdrant 登録 | 2026-09-20 |
| [`../services/docs/README.md`](../services/docs/README.md) | `services/**` | 2026-09-20 |
| 本書 | 直下 `docs/`（横断文書） | 2026-09-20 |

---

## 4. 横断文書の重複禁止ルール

§3.1 の 7 本は**同じ表・同じ Mermaid 図を 2 箇所に置かない**。正本は次のとおり。

| 資産 | 正本 | 他の文書での扱い |
|---|---|---|
| 3 モードのステップ対照表・実行順フロー図 | `pipelines.md` | リンクで参照 |
| 基本版と GRACE-Support の差 | `pipelines.md` | リンクで参照 |
| モード別に効くガードレールの有効表 | `pipelines.md` | リンクで参照 |
| ガードレール GA〜G9 の機構・実装・失敗時の既定 | `guardrails.md` | リンクで参照 |
| reasoning / detect のプロンプト構造 | `reasoning_flow.md` | リンクで参照 |
| 性能レバーの現況一覧 | `performance_levers.md` | リンクで参照 |
| API の段階別一覧（0 〜 ⑥） | `api_flow.md` | リンクで参照 |
| 0-(A) 複数質問の検知・選択・再構成 | `multi_question_handling.md` | リンクで参照 |
| **タイムアウト予算と遅さの内訳** | `local_llm_timeout_budget.md` | リンクで参照 |
| 一般用語 → 実装の対応（L0〜L4） | `agent_layers.md` | リンクで参照 |
| 関数・クラスの IPO | **各領域の `docs/`**（`backend/docs/` / `services/docs/` 等） | 横断文書は IPO を持たない |

> ⚠️ **重複は必ず片方だけ腐る。** 新しい表や図を足すときは、まずこの表の「正本」欄に
> 該当するものが無いか確認する。あれば**リンクにする**。

---

## 5. 重複の検出

同じ Mermaid ブロック・同じ表が 2 箇所に無いかを見る（リポジトリ直下で実行）。

```bash
python3 - <<'PYEOF'
import re, pathlib, collections
ROOTS = ['docs', 'backend/docs', 'grace/docs', 'frontend/docs', 'services/docs',
         'chunking/docs', 'qa_generation/docs', 'qa_qdrant/docs']
files = [p for r in ROOTS for p in sorted(pathlib.Path(r).glob('*.md'))
         if pathlib.Path(r).is_dir() and 'archive' not in str(p)]

blocks = collections.defaultdict(list)
tables = collections.defaultdict(list)
for md in files:
    text = md.read_text(encoding='utf-8')
    for b in re.findall(r'```mermaid\n(.*?)```', text, re.S):
        blocks[b.strip()].append(str(md))
    cur = []
    for line in text.split('\n'):
        if line.startswith('|'):
            cur.append(line.strip())
        else:
            if len(cur) >= 4:
                tables['\n'.join(cur)].append(str(md))
            cur = []

for label, store in (('Mermaid', blocks), ('表', tables)):
    for body, fs in store.items():
        if len(set(fs)) > 1:
            print(f"{label} 重複 {len(body.splitlines())}行: {sorted(set(fs))}")
PYEOF
```

> 📝 **検出されても自動的に違反とは限らない。** `frontend/docs/` の各コンポーネント文書は
> **定型の枠（4〜5 行）を各ファイルが持つ設計**である。
> §3.1 の横断文書どうし、または横断文書と領域別 docs のあいだで出たものが
> **本当の違反**である。

---

## 6. 残タスク

| # | タスク | 内容 | 状態 |
|---|---|---|---|
| 1 | ~~Version ヘッダーの無い文書 6 件~~ | 6 件すべてにヘッダーと変更履歴を追加した。版と最終更新日は **git の履歴から起こした実測値**（記憶で書いていない） | ✅ 完了（2026-09-20） |
| 2 | ~~`services/docs` / `qa_generation/docs` / `qa_qdrant/docs` に棚卸し README が無い~~ | 3 領域すべてに作成。**全 8 領域が索引を持つ**状態になった。作成時の調査で残タスク 10 件を新たに記録している（下の「各領域の残タスク」） | ✅ 完了（2026-09-20） |
| 3 | `data_tab_port_todo.md` の実機確認 | **手順は用意済み**（2026-09-22・同書 §7 に前提・コマンド・期待結果・切り分けを記載）。実行には実 Ollama ＋ Qdrant のある環境が要るため、この開発環境では実施できない | ⏳ 環境（実行待ち） |
| 4 | ~~`process.txt` の扱い~~ | **削除した**（2026-09-22・ユーザー判断）。単なる作業メモで、内容（3 エージェントのステップ一覧）は CLAUDE.md §1 と `backend/docs/support_flow.md` / `review_flow.md` が正本として持っている | ✅ 完了 |
| 5 | ~~`frontend/docs/` を React 仕様 v1.1 の共通骨格へ~~ | `a_react_page_md_format.md` v1.1 で概要に「各責務対応のモジュール」、`## 1.` に「1.1 システム全体での位置づけ（3 層）」が加わった。既存のコンポーネント文書は未追随 | ✅ 2026-09-24（あわせて `backend/docs/` も基本フォーマット・横断文書フォーマットへ追随） |
| 6 | ~~`LLM/` の空ファイルと空白入りファイル名~~ | 空の `react_anthropic.md` を削除し、`react_ollama_gemma4_e4b .md` を `react_ollama_gemma4_e4b.md` へリネームした（ユーザー判断） | ✅ 2026-09-24 |

### 各領域の残タスク（2026-09-20 の索引作成時に記録）

各索引の §6 が正本。ここは一覧のみ。

| 領域 | 件数 | 主なもの |
|---|---:|---|
| [`services/docs`](../services/docs/README.md) | 0 | ✅ 2026-09-21 にすべて完了（`agent_service` ＋ **`qa_service`**（同日に新規発見）の Anthropic 表記） |
| [`qa_generation/docs`](../qa_generation/docs/README.md) | **0** | ✅ 2026-09-21 にすべて完了（文書欠落 3 件・`QAPair` 3 重定義・Celery の import 副作用・`evaluation.md` の Version ヘッダー・死んだ `provider` 引数） |
| [`qa_qdrant/docs`](../qa_qdrant/docs/README.md) | **0** | ✅ 2026-09-21 にすべて完了（Version ヘッダー 7 件・`00_learning.md` の H1 位置） |
| [`frontend/docs`](../frontend/docs/README.md) | **0** | ✅ 2026-09-21 にすべて完了（a11y 3 件＝バナーの `role`・フォーカストラップ・キーボード操作、`ModelSelect` の `notes` 表示、`ReviewForm` の送信ショートカット） |
| [`backend/docs`](../backend/docs/docs_audit.md) | **0** | ✅ 2026-09-21 にすべて完了（GRACE-Review の未記載シンボル 4 件。公開シンボルは **43/43** 記載になった） |
| [`grace/docs`](../grace/docs/README.md) | 0 | 自領域に残なし（横断分は `backend/docs/docs_audit.md` §5 へ集約） |
| [`chunking/docs`](../chunking/docs/README.md) | 0 | 残タスク節なし |

> 📌 **全 8 領域の残タスクが 0 件になった**（2026-09-21）。直下 `docs/` の §6 に残るのは
> **実機確認 1 件だけ**である（実 Ollama ＋ Qdrant のある環境が要る。手順は
> [`data_tab_port_todo.md`](data_tab_port_todo.md) §「実機確認の手順」）。

---

## 7. 変更履歴

| バージョン | 変更内容 |
|-----------|---------|
| 2.2 | §6 残タスク 6 を完了（2026-09-24・ユーザー判断）。空ファイル `LLM/react_anthropic.md` を削除し、`LLM/react_ollama_gemma4_e4b .md` を空白なしの名前へ `git mv` した（内容は grace_v2 の同名ファイルとバイト単位で同一） |
| 2.1 | §6 残タスク 5（`frontend/docs/` の React 仕様 v1.1 追随）を完了（2026-09-24）。`backend/docs/` も `reference/` は基本フォーマット（IPO 冒頭の使用例）、それ以外は `a_cross_doc_md_format.md` v1.1 の種別 A / B / C へ追随させた |
| 2.0 | **`a_cross_doc_md_format.md`（横断文書フォーマット）を新設し、直下 `docs/` を準拠させた**（2026-09-24）。§2.2 に種別 A〜E と仕様の対応を追加し、§3 の各表に「種別」列を足して行数・Ver を実測へ更新（`multi_question_handling.md` は実装済みの設計案として種別 B とした）。種別 A の 6 文書へ概要（主な責務／各責務対応のモジュール／3 層のアーキテクチャ構成図）、種別 B の 4 文書へ目次と概要（結論・対象モジュール）、種別 C の 2 文書へ目次を追加（いずれも本文の章番号は不変）。`multi_question_handling.md` のヘッダー 3.0 と変更履歴 1.1 の不一致を解消。§6 に残タスク 5・6 を追加 |
| 1.9 | `process.txt` を削除（2026-09-22・ユーザー判断）。内容は CLAUDE.md §1 と `backend/docs/` の flow 文書が正本として持っており、重複していた。あわせて `data_tab_port_todo.md` へ**実機確認の手順・期待結果**を追記し、残タスク 3 を「手順は用意済み・実行待ち」へ更新した |
| 1.8 | **全 8 領域の残タスクが 0 件になった**（2026-09-21）。`frontend/docs` の 5 件（a11y 3 件＋`ModelSelect` の `notes` 表示＋`ReviewForm` の送信ショートカット）と `backend/docs` の 1 件（GRACE-Review の未記載シンボル 4 件 → 公開シンボル 43/43）を完了。§6 の一覧に `frontend` / `backend` / `grace` / `chunking` の行を足し、**8 領域すべてを一望できる**ようにした |
| 1.7 | `qa_generation` / `qa_qdrant` の残タスクを**すべて完了**（2026-09-21）。死んだ `provider="anthropic"` 引数を受け側ごと削除し、Version ヘッダー 8 件を追加（`evaluation` ＋ `qa_qdrant` 7 件）、`00_learning.md` の H1 を先頭へ移した。あわせて **`qa_service` の Anthropic 表記を新規発見**して是正（文書 27 箇所・実装の docstring 3 箇所）し、索引の古い参照 3 件（`backend/docs/README.md` §5 → `docs_audit.md` §5、`data_pipeline.md` のヘッダー、統合済みの `review_rules_collection.md`）も直した |
| 1.6 | `qa_generation` の残タスクを 3 件決着（2026-09-21）。**文書欠落 3 件を作成**し実装との 1:1 対応が揃った（`qa_generation/docs/README.md` v1.3）。あわせて調査で見つかった 2 件も処理 — `pipeline.py` の `celery_tasks` を遅延 import へ移して **1,799 → 1,689 モジュール**（9.58 → 1.82 秒）、`QAPair` の 3 重定義は**統合せず**に docstring 相互参照＋テストで固定した。副産物として `helper/helper_rag_qa.py` の裸 import（`celery_tasks` の `sys.path` 挿入に依存）も是正 |
| 1.5 | 各領域に残っていた Anthropic 表記の是正を反映（2026-09-21）。`services` は残タスク 0 件、`qa_generation` 4→3 件、`qa_qdrant` 3→2 件 |
| 1.4 | `qa_qdrant/__init__.py` の対処を反映（`qa_qdrant/docs/README.md` v1.2）。`register_to_qdrant.py` のログ format を `celery_config.py` と統一したうえで docstring のみへ整理し、**ログの見た目を変えずに**不要な 116 モジュールを外した（2026-09-21） |
| 1.3 | `qa_qdrant/__init__.py` の実測調査を反映（`qa_qdrant/docs/README.md` v1.1）。**当初「import 副作用でログ設定が変わる」としていた見立てを訂正** — ログ設定の変化は `__init__.py` を空にしても起きる（`basicConfig` をモジュールレベルで呼ぶファイルが 7 件あり、最初の 1 つが勝つ）。実際の影響は不要な 116 モジュール（+0.2 s）の読み込みだった（2026-09-21） |
| 1.2 | §6 残タスク 2 を完了。`services` / `qa_generation` / `qa_qdrant` に棚卸し索引を作成し、全 8 領域が索引を持つ状態になった。§3.7 に索引一覧、§6 に各領域の残タスク 10 件の要約を追加（2026-09-20） |
| 1.1 | §6 残タスク 1 を完了。Version ヘッダーが無かった 6 件にヘッダーと変更履歴を追加し、§3 の Ver 列・行数を実測値へ更新した（2026-09-20） |
| 1.0 | 初版作成（2026-09-20）。直下 `docs/` だけ棚卸しの索引が無く、**どこに何を置くかの境界が明文化されていなかった**。§2 に配置の判定基準、§4 に重複禁止ルールと正本の一覧、§5 に全 docs ディレクトリを横断する検出スクリプトを置いた。あわせて `agent_layers.md` を新規作成して §3.1 へ登録した |

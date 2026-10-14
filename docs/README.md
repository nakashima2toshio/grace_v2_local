# docs 棚卸し（リポジトリ直下 `docs/`）

**Version 3.19** | 最終更新: 2026-10-14

リポジトリ直下 `docs/` の一覧と、**どのディレクトリに何を置くかの境界**をまとめる。
各領域の棚卸しは backend（[`README_app.md`](../backend/app/docs/README_app.md)・[`README_backend_api.md`](../backend/app/api/docs/README_backend_api.md)・[`README_backend_core.md`](../backend/app/core/docs/README_backend_core.md)） /
[`grace/docs/README_grace.md`](../grace/docs/README_grace.md) /
[`frontend/docs/README_frontend.md`](../frontend/docs/README_frontend.md) /
[`chunking/docs/README_chunking.md`](../chunking/docs/README_chunking.md) にある。

> ⚠️ **本リポジトリは Ollama 版。** LLM は `gemma4:26b-a4b-it-qat`
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
長く検知されずに残っていた（2026-09-20 に `frontend/docs/README_frontend.md` で解消）。

本書は同じ事故を直下 `docs/` で防ぐためのもので、
**境界（§2）・正本の一覧（§4）・重複の検出（§5）**を持つ。

---

## 2. 配置の境界（どこに何を置くか）

CLAUDE.md §9.1 の表を、判断に使える形へ具体化したもの。

| 置き場所 | 置くもの | 判断の目安 |
|---|---|---|
| `<package>/docs/<module>.md` | Python モジュールの IPO | **1 ファイル = 1 文書**。`chunking/` `qa_generation/` `qa_qdrant/` `services/` `grace/` `helper/` `scripts/`・`backend/app/` `backend/app/api/` `backend/app/core/` |
| `backend/app/**/docs/` の `README_<dir>.md` / `core_process_flow.md` | backend 全体の構成（`README_app.md`）・API 契約（`README_backend_api.md`）・Support / Review の流れとジョブ基盤（`core_process_flow.md`）。`backend/docs/` は 2026-10-12 に廃止 | 対象が `backend/app/` に閉じているか（1 モジュールの IPO は `backend/app/**/docs/`） |
| `frontend/docs/` | React コンポーネント 1 件ごと | 対象が `frontend/src/` に閉じているか |
| **直下 `docs/`** | **2 つ以上の領域にまたがる横断文書**、およびトップレベル `.py` の IPO | 「`backend/` だけ」「`grace/` だけ」で説明しきれないもの |

### 2.1 直下 `docs/` に置いてよいかの判定

```
その文書が説明する実装は、1 つの領域（backend / grace / frontend / <package>）に閉じているか？
  ├─ はい  → その領域の docs/ へ置く。直下 docs/ には置かない
  └─ いいえ → 直下 docs/ へ置く。ただし §4 の「正本」欄に同じ資産が無いか先に確認する
```

**実例**: ガードレール一覧（GA〜G9）は `gates.py` ＋ `grace/confidence.py` ＋ `grace/executor.py` にまたがるが、**読む人が backend の流れから辿る**ので
2026-10-13 に直下の `guardrails.md` から [`core_process_flow.md` §4](../backend/app/core/docs/core_process_flow.md) へ移した。
またがる領域の数だけでなく、**誰がどこから辿るか**でも置き場所を決める。
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
> （唯一あった `agent_parallel_search.md` は、対象のモジュールを 2026-10-10 に削除したので `archive/` へ移し、2026-10-13 に文書も削除した。現在は無い）。パッケージに属さないため `<package>/docs/` が作れない。
> 横断文書と混ざるが、これを分けるために 1 ファイルのためのディレクトリは切らない。

---

## 3. 文書一覧

> 行数・Ver は **2026-09-24 の実測値**（`wc -l` と各文書の Version ヘッダー）。記憶で書かないこと。

### 3.1 横断文書（2 つ以上の領域にまたがる）

> 📝 2026-10-13 に読み物の横断文書 5 本（`pipelines.md`・`guardrails.md`・`reasoning_flow.md`・`agent_layers.md`・`app_tabs_overview.md`）を統合して削除した。
> 3 モードの対照・ガードレール・生成ステップは [`core_process_flow.md` §4](../backend/app/core/docs/core_process_flow.md)、タブごとの見え方は直下 [`README.md`](../README.md) §1〜§5、一般用語との対応は同 §6.5 にある。

| 文書 | 種別 | 内容 | またがる領域 | 行数 | Ver |
|---|:--:|---|---|---:|---|
| `data_prep_install.md` | B | データ準備（チャンク化 → Q/A 生成 → Qdrant 登録）の環境構築。Ollama・MeCab・Docker（Qdrant / Redis）・Celery（2026-10-11 に `qa_qdrant/docs/01_install.md` から移した。§5 に旧 `celery_quick_start.md` を 2026-10-13 に統合した） | chunking + qa_generation + qa_qdrant | 985 | 2.6 |
| `testing.md` | B | テストの地図。実行方法（結合テスト・E2E〔Mac 専用〕）・どこを触ったらどれを流すか・CI の 4 ゲート・カバレッジレポートの読み方（2026-10-12 に `backend/docs/tests.md` から移した。旧 `pytest_coverage.md` を 2026-10-13 に統合した） | tests + backend + frontend | 326 | 2.9 |

### 3.2 本リポジトリ固有（Ollama 版であることに由来）

| 文書 | 種別 | 内容 | 行数 | Ver |
|---|:--:|---|---:|---|
| `local_llm_timeout_budget.md` | B | **ローカル LLM のタイムアウト予算と、遅さの内訳**。実測に基づく | 1012 | 1.1 |

> 📌 この 1 本は `grace_v2`（Anthropic 版）には**存在しない**（移植インベントリ `migration_anthropic2ollama_inventory.md` は完了した記録なので 2026-10-13 に削除した）。
> 姉妹リポジトリへ持っていこうとしないこと（前提が違う）。

### 3.3 モジュール IPO（トップレベル `.py`）

| 文書 | 種別 | 対象 | 行数 | Ver |
|---|:--:|---|---:|---|
| `qdrant_delete_collection.md` | E | `qdrant_delete_collection.py` — Qdrant のコレクション削除 CLI（2026-10-11 に `qa_qdrant/docs/` から移した） | 344 | 1.2 |

### 3.4 TODO・改善候補

| 文書 | 種別 | 内容 | 行数 | Ver |
|---|:--:|---|---:|---|
| `port_from_grace_v2_todo.md` | C | grace_v2 からの移植の結論・物差し・移植しないもの・壊してはいけないもの・§12 共用 Qdrant の方針と残作業。完了した A〜E・G の作業記録は 2026-10-13 に外した（git 履歴に残る） | 170 | 2.9 |
| `performance_levers.md` | C | 回答品質・レイテンシを決めている箇所と、**未実装の改善候補**（P-03b・P-05・P-10 ほか）と実施順序。2026-10-13 に実装済みレバーの経緯を要約して縮めた | 363 | 2.9 |

### 3.5 その他

**無い**（`pytest_coverage.md` は 2026-10-13 に `testing.md` へ統合した）。
完了した TODO・記録は `archive/` へ移さずに削除し、残すべき結論だけを現行の文書へ移す（2026-10-13 に `archive/` も空になったので消した）。

### 3.6 資材ディレクトリ

種別はすべて D（書式不問）。

> 📌 GRACE とは別の実験（React の useReducer をモデルに直させた比較）の資材 `LLM/` と、プロンプトの下書き 2 本は、
> 2026-10-13 に grace_v2 の `docs/LLM/` へ集約して本リポジトリからは削除した（両リポジトリで同じものを持っていた）。

| ディレクトリ | 内容 |
|---|---|
| `examples/` | 画面の例文を流した実行ログ（`GRACE-Support_例文4件.txt`・`GRACE-Review_例文3件.txt`・1 問ずつの実行例 4 本）。直下 `README.md` §2.3・§3.3・§4.3 が参照する。2026-10-12 に `backend/docs/` から移した |
| `images/` | README・各文書が参照するスクリーンショット 5 件（どこからも参照されていない `comparison/img123.png` は 2026-10-13 に削除） |


### 3.7 各領域の棚卸し索引

| 索引 | 対象 | 作成日 |
|---|---|---|
| [`README_app.md`](../backend/app/docs/README_app.md) | `backend/app/**` | — |
| [`../grace/docs/README_grace.md`](../grace/docs/README_grace.md) | `grace/**` | — |
| [`../frontend/docs/README_frontend.md`](../frontend/docs/README_frontend.md) | React コンポーネント | 2026-09-20 |
| [`../chunking/docs/README_chunking.md`](../chunking/docs/README_chunking.md) | ① チャンク化 | 2026-09-11 |
| [`../qa_generation/docs/README_qa_generation.md`](../qa_generation/docs/README_qa_generation.md) | ② Q/A 生成 | 2026-09-20 |
| [`../qa_qdrant/docs/README_qa_qdrant.md`](../qa_qdrant/docs/README_qa_qdrant.md) | ③ Qdrant 登録（サブディレクトリ `qa_qdrant/command/` は [`README_command.md`](../qa_qdrant/command/docs/README_command.md)） | 2026-09-20 |
| [`../services/docs/README_services.md`](../services/docs/README_services.md) | `services/**` | 2026-09-20 |
| [`../helper/docs/README_helper.md`](../helper/docs/README_helper.md) | `helper/**`（LLM・Embedding のクライアントほか） | 2026-10-11 |
| [`../scripts/docs/README_scripts.md`](../scripts/docs/README_scripts.md) | `scripts/**`（規程 CSV の雛形・RAG のしきい値の実測） | 2026-10-11 |
| 本書 | 直下 `docs/`（横断文書） | 2026-09-20 |

---

## 4. 横断文書の重複禁止ルール

直下 `docs/` と各領域の `docs/` は**同じ表・同じ Mermaid 図を 2 箇所に置かない**。正本は次のとおり。

| 資産 | 正本 | 他の文書での扱い |
|---|---|---|
| 3 モードのステップ対照表・モード別のガードレール | `backend/app/core/docs/core_process_flow.md` §4.1・§4.2 | リンクで参照 |
| 基本版と GRACE-Support の差 | `backend/app/core/docs/core_process_flow.md` §1.7 | リンクで参照 |
| ガードレール GA〜G9 の機構・実装・失敗時の既定 | `backend/app/core/docs/core_process_flow.md` §4.2 | リンクで参照 |
| reasoning / detect の比較 | `backend/app/core/docs/core_process_flow.md` §4.3（プロンプトの文面は `grace/docs/tools.md` §5.6） | リンクで参照 |
| 性能レバーの現況一覧 | `performance_levers.md` | リンクで参照 |
| 0-(A) 複数質問の検知・選択・再構成 | `backend/app/core/docs/core_process_flow.md` §1.6 | リンクで参照 |
| **タイムアウト予算と遅さの内訳** | `local_llm_timeout_budget.md` | リンクで参照 |
| 一般用語 → 実装の対応（L0〜L4） | 直下 `README.md`「エージェントの階層と一般用語の対応」 | リンクで参照 |
| 関数・クラスの IPO | **各領域の `docs/`**（`backend/app/core/docs/` / `services/docs/` 等） | 横断文書は IPO を持たない |

> ⚠️ **重複は必ず片方だけ腐る。** 新しい表や図を足すときは、まずこの表の「正本」欄に
> 該当するものが無いか確認する。あれば**リンクにする**。

---

## 5. 重複の検出

同じ Mermaid ブロック・同じ表が 2 箇所に無いかを見る（リポジトリ直下で実行）。

```bash
python3 - <<'PYEOF'
import re, pathlib, collections
ROOTS = ['docs', 'backend/app/docs', 'backend/app/api/docs', 'backend/app/core/docs', 'grace/docs', 'frontend/docs', 'services/docs',
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

無い（以前の 6 件と各領域の残タスクはすべて完了した。唯一残っていた `data_tab_port_todo.md` の実機確認は、同書を 2026-10-13 に削除したので外した。経緯は変更履歴と git 履歴にある）。

---

## 7. 変更履歴

| バージョン | 日付 | 変更内容 |
|---|---|---|
| 1.0 | 2026-09-20 | 初版作成（2026-09-20）。直下 `docs/` だけ棚卸しの索引が無く、**どこに何を置くかの境界が明文化されていなかった**。§2 に配置の判定基準、§4 に重複禁止ルールと正本の一覧、§5 に全 docs ディレクトリを横断する検出スクリプトを置いた。あわせて `agent_layers.md` を新規作成して §3.1 へ登録した |
| 1.1 | 2026-09-20 | §6 残タスク 1 を完了。Version ヘッダーが無かった 6 件にヘッダーと変更履歴を追加し、§3 の Ver 列・行数を実測値へ更新した（2026-09-20） |
| 1.2 | 2026-09-20 | §6 残タスク 2 を完了。`services` / `qa_generation` / `qa_qdrant` に棚卸し索引を作成し、全 8 領域が索引を持つ状態になった。§3.7 に索引一覧、§6 に各領域の残タスク 10 件の要約を追加（2026-09-20） |
| 1.3 | 2026-09-21 | `qa_qdrant/__init__.py` の実測調査を反映（`qa_qdrant/docs/README.md` v1.1）。**当初「import 副作用でログ設定が変わる」としていた見立てを訂正** — ログ設定の変化は `__init__.py` を空にしても起きる（`basicConfig` をモジュールレベルで呼ぶファイルが 7 件あり、最初の 1 つが勝つ）。実際の影響は不要な 116 モジュール（+0.2 s）の読み込みだった（2026-09-21） |
| 1.4 | 2026-09-21 | `qa_qdrant/__init__.py` の対処を反映（`qa_qdrant/docs/README.md` v1.2）。`register_to_qdrant.py` のログ format を `celery_config.py` と統一したうえで docstring のみへ整理し、**ログの見た目を変えずに**不要な 116 モジュールを外した（2026-09-21） |
| 1.5 | 2026-09-21 | 各領域に残っていた Anthropic 表記の是正を反映（2026-09-21）。`services` は残タスク 0 件、`qa_generation` 4→3 件、`qa_qdrant` 3→2 件 |
| 1.6 | 2026-09-21 | `qa_generation` の残タスクを 3 件決着（2026-09-21）。**文書欠落 3 件を作成**し実装との 1:1 対応が揃った（`qa_generation/docs/README.md` v1.3）。あわせて調査で見つかった 2 件も処理 — `pipeline.py` の `celery_tasks` を遅延 import へ移して **1,799 → 1,689 モジュール**（9.58 → 1.82 秒）、`QAPair` の 3 重定義は**統合せず**に docstring 相互参照＋テストで固定した。副産物として `helper/helper_rag_qa.py` の裸 import（`celery_tasks` の `sys.path` 挿入に依存）も是正 |
| 1.7 | 2026-09-21 | `qa_generation` / `qa_qdrant` の残タスクを**すべて完了**（2026-09-21）。死んだ `provider="anthropic"` 引数を受け側ごと削除し、Version ヘッダー 8 件を追加（`evaluation` ＋ `qa_qdrant` 7 件）、`00_learning.md` の H1 を先頭へ移した。あわせて **`qa_service` の Anthropic 表記を新規発見**して是正（文書 27 箇所・実装の docstring 3 箇所）し、索引の古い参照 3 件（`backend/docs/README.md` §5 → `docs_audit.md` §5、`data_pipeline.md` のヘッダー、統合済みの `review_rules_collection.md`）も直した |
| 1.8 | 2026-09-21 | **全 8 領域の残タスクが 0 件になった**（2026-09-21）。`frontend/docs` の 5 件（a11y 3 件＋`ModelSelect` の `notes` 表示＋`ReviewForm` の送信ショートカット）と `backend/docs` の 1 件（GRACE-Review の未記載シンボル 4 件 → 公開シンボル 43/43）を完了。§6 の一覧に `frontend` / `backend` / `grace` / `chunking` の行を足し、**8 領域すべてを一望できる**ようにした |
| 1.9 | 2026-09-22 | `process.txt` を削除（2026-09-22・ユーザー判断）。内容は CLAUDE.md §1 と `backend/docs/` の flow 文書が正本として持っており、重複していた。あわせて `data_tab_port_todo.md` へ**実機確認の手順・期待結果**を追記し、残タスク 3 を「手順は用意済み・実行待ち」へ更新した |
| 2.0 | 2026-09-24 | **`a_cross_doc_md_format.md`（横断文書フォーマット）を新設し、直下 `docs/` を準拠させた**（2026-09-24）。§2.2 に種別 A〜E と仕様の対応を追加し、§3 の各表に「種別」列を足して行数・Ver を実測へ更新（`multi_question_handling.md` は実装済みの設計案として種別 B とした）。種別 A の 6 文書へ概要（主な責務／各責務対応のモジュール／3 層のアーキテクチャ構成図）、種別 B の 4 文書へ目次と概要（結論・対象モジュール）、種別 C の 2 文書へ目次を追加（いずれも本文の章番号は不変）。`multi_question_handling.md` のヘッダー 3.0 と変更履歴 1.1 の不一致を解消。§6 に残タスク 5・6 を追加 |
| 2.1 | 2026-09-24 | §6 残タスク 5（`frontend/docs/` の React 仕様 v1.1 追随）を完了（2026-09-24）。`backend/docs/` も `reference/` は基本フォーマット（IPO 冒頭の使用例）、それ以外は `a_cross_doc_md_format.md` v1.1 の種別 A / B / C へ追随させた |
| 2.2 | 2026-09-24 | §6 残タスク 6 を完了（2026-09-24・ユーザー判断）。空ファイル `LLM/react_anthropic.md` を削除し、`LLM/react_ollama_gemma4_e4b .md` を空白なしの名前へ `git mv` した（内容は grace_v2 の同名ファイルとバイト単位で同一） |
| 2.3 | 2026-10-01 | `port_from_grace_v2_todo.md` を v1.8 へ（G の完了・`qa_output/` を移植しない方針・§12 共用 Qdrant と残作業。2026-10-01） |
| 2.4 | 2026-10-02 | `port_from_grace_v2_todo.md` を v2.0 へ（grace_v2#244 の移植。2026-10-02） |
| 2.5 | 2026-10-02 | `port_from_grace_v2_todo.md` を v2.1 へ（gemma4 の実測と追加修正。2026-10-02） |
| 2.6 | 2026-10-03 | `port_from_grace_v2_todo.md` を v2.2 へ（keihyo-09 の除外語と実測。2026-10-03） |
| 2.7 | 2026-10-03 | `port_from_grace_v2_todo.md` を v2.3 へ（既定モデルを 26b へ。2026-10-03） |
| 2.8 | 2026-10-03 | `port_from_grace_v2_todo.md` を v2.4 へ（文字列の判定で補う・結果が古いことの表示。2026-10-03） |
| 2.9 | 2026-10-03 | `port_from_grace_v2_todo.md` を v2.5 へ（§12.2 #10 の実機確認・#2 の更新。2026-10-03） |
| 3.0 | 2026-10-06 | §3.1 に `app_tabs_overview.md`（処理 3 タブの概要・種別 A）を追加し、`pipelines.md` を v1.4 へ（G7 の Review 列を実装に合わせて是正）（2026-10-06） |
| 3.1 | 2026-10-03 | 現在の既定モデルの記載 `gemma4:12b-mlx` を、2026-10-03 の変更後の値 `gemma4:26b-a4b-it-qat`（`config.py::get_default_ollama_model()` の戻り値）へ是正（冒頭の注記）（2026-10-08） |
| 3.2 | 2026-10-10 | `agent_layers.md` を v1.4・`port_from_grace_v2_todo.md` を v2.6 へ（削除した `a_pages_md_format.md` への言及を更新。`agent_layers.md` の版は v1.1 のまま取り残されていたので実測値へ直した）。変更履歴を 3 列へ移した |
| 3.3 | 2026-10-10 | `agent_layers.md` を v1.5 へ（`grace/step_trace/` を 2026-10-10 にディレクトリごと削除したのに追随） |
| 3.4 | 2026-10-10 | Legacy ReAct 経路（`services/agent_service.py`・`agent_parallel_search.py`・`agent_cache.py`・`executor._execute_legacy_agent_step`・`run_legacy_agent` アクション）を 2026-10-10 に削除したのに追随 |
| 3.5 | 2026-10-10 | `agent_layers.md` の行数・版を実測値へ更新（Tool Use の削除に追随） |
| 3.6 | 2026-10-10 | 索引の行数・版を実測値へ更新（テストを直下 `tests/` へ移した変更に追随） |
| 3.7 | 2026-10-10 | `grace/docs/` の構成整理（`README.md` → `README_grace.md`、`grace.md` / `grace_core.md` / `grace_core_flow.md` / `confidence_calibration.md` を `README_grace.md` / `grace_process_flow.md` / `grace_data_flow.md` へ統合）に合わせてリンクを直した |
| 3.8 | 2026-10-10 | `services/docs/` の構成整理（`README.md` → `README_services.md`、`__init__.md` を統合）に合わせてリンクを直した |
| 3.9 | 2026-10-11 | §3.7 の索引に helper/docs/README_helper.md（2026-10-11 新設）を足した |
| 3.10 | 2026-10-11 | `qa_qdrant/docs/` の構成整理に合わせ、移ってきた 3 文書（`data_prep_install.md`（旧 `qa_qdrant/docs/01_install.md`）・`celery_quick_start.md`・`qdrant_delete_collection.md`）を §3.1・§3.3 に足し、`qa_qdrant/docs/README.md` → `README_qa_qdrant.md` の改名に合わせてリンクを直した |
| 3.11 | 2026-10-11 | `chunking/docs/` の構成整理（`README.md` → `README_chunking.md`）に合わせてリンクを直した |
| 3.12 | 2026-10-11 | `qa_generation/docs/` の構成整理（`README.md` → `README_qa_generation.md`、`__init__.md` を統合、`make_qa_qapipeline.md` → `qa_generation_process_flow.md`）に合わせてリンクを直した |
| 3.13 | 2026-10-11 | §3.7 の索引に scripts/docs/README_scripts.md（2026-10-11 新設）を足した |
| 3.14 | 2026-10-11 | backend のモジュール文書を `backend/docs/reference/` から `backend/app/docs/`・`backend/app/api/docs/`・`backend/app/core/docs/` へ移したのに追随（リンクの付け替え）（冒頭の索引・§2 の配置の境界） |
| 3.15 | 2026-10-12 | backend/docs/ の廃止（2026-10-12）に追随し、参照先を backend/app/docs/README_app.md・backend/app/api/docs/README_api.md・backend/app/core/docs/core_process_flow.md などへ付け替えた（テストの地図は docs/testing.md、実行例の txt は docs/examples/） |
| 3.16 | 2026-10-13 | docs/ の整理: api_flow・multi_question_handling・celery_quick_start・pytest_coverage・data_tab_port_todo・migration_anthropic2ollama_inventory・archive/・LLM/・プロンプトの下書き 2 本・未参照の画像 1 枚を削除（LLM 資材は grace_v2 の docs/LLM/ へ集約）。§3・§4 を更新し §6 を畳んだ |
| 3.17 | 2026-10-13 | 読み物の横断文書 5 本（pipelines・guardrails・reasoning_flow・agent_layers・app_tabs_overview）を統合して削除。performance_levers を種別 C（改善候補）へ移し、§4 の正本表を付け替えた |
| 3.18 | 2026-10-13 | 直下 README.md の組み直し（v4.0・タブ単位の構成）に合わせて、旧節番号への参照を新しい節へ付け替えた |
| 3.19 | 2026-10-14 | 索引の改名（frontend/docs/README.md → README_frontend.md、README_api.md → README_backend_api.md、README_core.md → README_backend_core.md）に合わせて参照を付け替えた |

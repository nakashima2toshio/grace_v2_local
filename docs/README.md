# docs 棚卸し（リポジトリ直下 `docs/`）

**Version 1.2** | 最終更新: 2026-09-20

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

> ⚠️ **トップレベル `.py` の IPO は直下 `docs/` が現状の置き場所**である
> （`agent_parallel_search.md`）。パッケージに属さないため `<package>/docs/` が作れない。
> 横断文書と混ざるが、これを分けるために 1 ファイルのためのディレクトリは切らない。

---

## 3. 文書一覧

> 行数・Ver は **2026-09-20 の実測値**（`wc -l` と各文書の Version ヘッダー）。記憶で書かないこと。

### 3.1 横断文書（2 つ以上の領域にまたがる）

| 文書 | 内容 | またがる領域 | 行数 | Ver |
|---|---|---|---:|---|
| `pipelines.md` | **3 モード対照のハブ**（基本版 / Support / Review）。ステップ対照表・実行順・基本版との差・ガードレール有効表 | backend + frontend | 166 | 1.2 |
| `guardrails.md` | ガードレール GA〜G9 の機構 → 実装 → **失敗時の既定** | backend + grace + ルート | 300 | 1.0 |
| `reasoning_flow.md` | 生成の 2 ステップ（Support の `reasoning` / Review の `detect`） | grace + backend | 320 | 2.0 |
| `performance_levers.md` | 回答品質・レイテンシ・コストを決めている箇所と未実装レバー | 全域 | 478 | 2.0 |
| `api_flow.md` | GRACE-Support の API フロー一覧（0 〜 ⑥ の 8 段階） | backend + grace | 509 | 2.1 |
| `multi_question_handling.md` | 複数質問クエリへの対応（0-(A) 入力・質問分析） | backend + frontend + grace | 693 | 3.0 |
| `agent_layers.md` | **一般エージェント用語 → 実装の対応表**（L0〜L4）。実装を読む前の見取り図 | 全域 | 377 | 1.0 |

### 3.2 本リポジトリ固有（Ollama 版であることに由来）

| 文書 | 内容 | 行数 | Ver |
|---|---|---:|---|
| `local_llm_timeout_budget.md` | **ローカル LLM のタイムアウト予算と、遅さの内訳**。実測に基づく | 962 | 1.0 |
| `migration_anthropic2ollama_inventory.md` | Anthropic → Ollama 移植インベントリ | 400 | 1.1 |

> 📌 この 2 本は `grace_v2`（Anthropic 版）には**存在しない**。
> 姉妹リポジトリへ持っていこうとしないこと（前提が違う）。

### 3.3 モジュール IPO（トップレベル `.py`）

| 文書 | 対象 | 行数 | Ver |
|---|---|---:|---|
| `agent_parallel_search.md` | `agent_parallel_search.py` — 並列検索エンジン（`ThreadPoolExecutor`）。⚠️ **Legacy ReAct 経路専用で Web アプリからは未稼働**（CLAUDE.md §1・§9.4） | 705 | 1.0 |

### 3.4 進行中・完了した TODO

| 文書 | 内容 | 行数 | Ver |
|---|---|---:|---|
| `port_from_grace_v2_todo.md` | grace_v2 からの移植 TODO。**A〜E は 2026-09-20 に完了**、F は「移植しない」と結論済み | 352 | 1.4 |
| `data_tab_port_todo.md` | データ管理タブ移植の記録（2026-08-03 時点。⚠️ 以降の実装で状況が変わった箇所がある旨を冒頭に明記済み） | 315 | 1.1 |

### 3.5 その他

| 文書 | 内容 | 行数 | Ver |
|---|---|---:|---|
| `pytest_coverage.md` | pytest カバレッジレポートの読み方 | 72 | 1.0 |

### 3.6 資材ディレクトリ

| ディレクトリ | 内容 |
|---|---|
| `images/` | README・各文書が参照するスクリーンショット 6 件（`comparison/` を含む） |
| `LLM/` | モデル別の ReAct 実行ログと比較 5 ファイル（`react_ollama_*.md` / `react_anthropic.md` ほか） |

> 📌 `process.txt` は Markdown ではない作業メモ。索引の対象外。

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
| 3 | `data_tab_port_todo.md` の実機確認 2 件 | §「チャンク化が Ollama で動くかは未検証」「実機確認（未実施）」。実 Ollama ＋ Qdrant のある環境が要る | ⏳ 環境 |
| 4 | `process.txt` の扱い | Markdown ではない作業メモ。残すなら `.md` 化して索引へ、不要なら削除 | ⏳ 判断待ち |

### 各領域の残タスク（2026-09-20 の索引作成時に記録）

各索引の §6 が正本。ここは一覧のみ。

| 領域 | 件数 | 主なもの |
|---|---:|---|
| [`services/docs`](../services/docs/README.md) | 2 | `agent_service.py` の docstring が Anthropic 表記（Legacy ReAct 経路） |
| [`qa_generation/docs`](../qa_generation/docs/README.md) | 4 | **文書 3 件が欠落**（`data_io` / `models` / `__init__`）・文書に Anthropic 表記が残る（実装は Ollama 済み） |
| [`qa_qdrant/docs`](../qa_qdrant/docs/README.md) | 4 | **`qa_qdrant/__init__.py` が `make_qa.py` の古い写し**（import 副作用あり）・`make_qa.md` の Anthropic 表記 |

---

## 7. 変更履歴

| バージョン | 変更内容 |
|-----------|---------|
| 1.2 | §6 残タスク 2 を完了。`services` / `qa_generation` / `qa_qdrant` に棚卸し索引を作成し、全 8 領域が索引を持つ状態になった。§3.7 に索引一覧、§6 に各領域の残タスク 10 件の要約を追加（2026-09-20） |
| 1.1 | §6 残タスク 1 を完了。Version ヘッダーが無かった 6 件にヘッダーと変更履歴を追加し、§3 の Ver 列・行数を実測値へ更新した（2026-09-20） |
| 1.0 | 初版作成（2026-09-20）。直下 `docs/` だけ棚卸しの索引が無く、**どこに何を置くかの境界が明文化されていなかった**。§2 に配置の判定基準、§4 に重複禁止ルールと正本の一覧、§5 に全 docs ディレクトリを横断する検出スクリプトを置いた。あわせて `agent_layers.md` を新規作成して §3.1 へ登録した |

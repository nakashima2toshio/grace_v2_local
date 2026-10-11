# README_helper.md - helper/ ディレクトリ概要

**Version 1.0** | 最終更新: 2026-10-11

`helper/` の入口となる文書。ディレクトリの役割・モジュール索引・組み合わせ方・import のしかた・既知の制約をまとめる。
文書を新しく書く／直す前に、まずここを見る。

> ⚠️ **本リポジトリは Ollama 版。** 姉妹リポジトリ `grace_v2`（Anthropic 版）にも同じ構成の `helper/` があるが、
> `helper_llm.py` は別物（こちらは `OllamaClient`・883 行。GRACE の推論もここを通る）で、`helper_embedding.py`（既定値の直書き）・
> `helper_embedding_sparse.py`（キャッシュの場所をログに出す）・`helper_rag_qa.py`（LLM のプロバイダ）も違う。文書をコピーで持ち込まないこと（CLAUDE.md §5）。

---

## 目次

- [概要](#概要)
  - [主な責務](#主な責務)
  - [各責務対応のモジュール](#各責務対応のモジュール)
  - [アーキテクチャ構成図](#アーキテクチャ構成図)
- [1. モジュール一覧（索引）](#1-モジュール一覧索引)
- [2. 使い方（代表的なワークフロー）](#2-使い方代表的なワークフロー)
- [3. 公開 API（import のしかた）](#3-公開-apiimport-のしかた)
- [4. 処理フロー・データフロー](#4-処理フローデータフロー)
- [5. 既知の制約・残課題](#5-既知の制約残課題)
- [6. 変更履歴](#6-変更履歴)

---

## 概要

`helper/` は、**外部サービスのクライアントの抽象化**（LLM・Embedding・疎ベクトル）と、**テキスト・データの前処理の小さな部品**を集めたディレクトリである。
本番で効いているのは主に 3 つ:

- `helper_llm.py` — **すべての LLM 呼び出しの土台**（`OllamaClient`）。GRACE-Support / GRACE-Review の推論（`grace/llm_compat.py::OllamaGenaiClient` が内部で使う）と、
  データ準備（チャンク化・Q/A 生成）の両方がここを通る
- `helper_embedding.py` — Gemini Embedding（3072 次元）。検索クエリ・登録文書の埋め込み（`qdrant_client_wrapper.py` / `services/qdrant_service.py` 経由）と、根拠検証（`grace/confidence.py`）が使う
- `helper_embedding_sparse.py` — hybrid 検索の疎ベクトル（任意。`grace/tools.py` が検索の前に 1 回だけ作る）

ほかの 4 つ（`helper_text` / `helper_rag` / `helper_rag_qa` / `helper_embedding_fastembed`）は、`clean_text` 1 関数を除いて本番の呼び出し元が無い（後方互換・旧画面の名残。§1）。

### 主な責務

- LLM クライアントの抽象化と Ollama（既定）/ OpenAI / Gemini の実装（期限・思考モデル対策・構造化出力）
- Embedding（密ベクトル）クライアントの抽象化と Gemini / OpenAI / FastEmbed の実装
- hybrid 検索用の疎ベクトル（SPLADE）の生成と、失敗の記憶
- テキストのクレンジング・日本語の文分割・トークン数でのチャンク分割
- Q&A 形式 CSV の前処理（検証・結合・`OUTPUT/` への保存）
- 文書からの Q&A 生成（キーワード・ルール・LLM・カバレッジ。`qa_generation/` の前身）

### 各責務対応のモジュール

| # | 責務 | 対応モジュール | 説明 |
|---|------|--------------|------|
| 1 | LLM クライアント | `helper_llm.py` | `create_llm_client()` / `OllamaClient`（`generate_structured`・`SchemaEchoError`・`last_usage`） |
| 2 | Embedding クライアント | `helper_embedding.py`（FastEmbed は `helper_embedding_fastembed.py`） | `create_embedding_client()` / `GeminiEmbedding` / `separate_contents()` |
| 3 | 疎ベクトル | `helper_embedding_sparse.py` | `get_sparse_embedding_client()`（negative cache・`resolve_cache_dir()`） |
| 4 | テキストの整形と分割 | `helper_text.py` | `clean_text()`（本番はこれだけ）/ `split_into_chunks*()` ほか |
| 5 | Q&A CSV の前処理 | `helper_rag.py` | `RAGConfig` / `process_rag_data()` / `save_files_to_output()`・`clean_text` の再エクスポート |
| 6 | Q&A 生成（後方互換） | `helper_rag_qa.py` | `OptimizedHybridQAGenerator` ほか約 20 クラス（後継は `qa_generation/`） |

### アーキテクチャ構成図

```mermaid
flowchart TB
    subgraph CALLER["呼び出し側"]
        GR["grace/llm_compat.py / grace/tools.py / grace/confidence.py<br>（推論・検索・根拠検証）"]
        QCW["qdrant_client_wrapper.py<br>services/qdrant_service.py"]
        PREP["chunking/async_api_client.py<br>qa_generation/（SmartQAGenerator・semantic・data_io）<br>services/qa_service.py"]
        TST["tests/（helper_rag_qa など）"]
    end
    subgraph HLP["helper/"]
        LLM["helper_llm.py"]
        EMB["helper_embedding.py"]
        SPA["helper_embedding_sparse.py"]
        FE["helper_embedding_fastembed.py"]
        TXT["helper_text.py / helper_rag.py"]
        RQA["helper_rag_qa.py"]
    end
    subgraph EXT["外部・下位モジュール"]
        OLA["Ollama（localhost:11434/v1）"]
        GEM["Gemini Embedding API"]
        FEL["fastembed（任意・HuggingFace）"]
        CFG["config.py::get_default_ollama_model()"]
        SVC["services/token_service.py"]
    end
    GR --> LLM
    GR --> QCW
    GR --> EMB
    QCW --> EMB
    QCW --> SPA
    PREP --> LLM
    PREP --> EMB
    PREP --> TXT
    TST --> RQA
    RQA --> LLM
    RQA --> EMB
    EMB -.-> FE
    LLM --> OLA
    LLM --> CFG
    EMB --> GEM
    SPA --> FEL
    FE --> FEL
    TXT --> SVC
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class GR,QCW,PREP,TST,LLM,EMB,SPA,FE,TXT,RQA,OLA,GEM,FEL,CFG,SVC default
style CALLER fill:#1a1a1a,stroke:#fff,color:#fff
style HLP fill:#1a1a1a,stroke:#fff,color:#fff
style EXT fill:#1a1a1a,stroke:#fff,color:#fff
```

**データフロー**:

1. GRACE の推論（計画・推論・根拠検証の判定ほか）は `grace/llm_compat.py` → `helper_llm.OllamaClient` → 手元の Ollama（外部 API へは出ない）
2. 検索のたびに、クエリを `helper_embedding`（`retrieval_query`）で密ベクトルに、`helper_embedding_sparse` で疎ベクトルにする（疎ベクトルは失敗しても続ける）
3. 登録では、Q/A の文を `helper_embedding.embed_texts`（100 件ずつ・`retrieval_document`）で埋め込む。失敗したまとまりはゼロベクトルになる
4. データ準備では、チャンク化・Q/A 生成が `helper_llm.create_llm_client("ollama")` の `generate_structured` で JSON を受け、Q/A 生成の入力 CSV は `clean_text` で整える

---

## 1. モジュール一覧（索引）

`helper/*.py` は 7 モジュール（`__init__.py` は無い。§3）。処理フロー・データフローの別文書は置かない（理由は §4）。

| モジュール | 文書 | 種別 | 行数 | Ver | 本番の呼び出し元 | 概要 |
|---|---|---|---:|---|---|---|
| `helper_llm.py` | [`helper_llm.md`](./helper_llm.md) | E | 684 | 1.0 | `grace/llm_compat` / `chunking/async_api_client`・`diagnose_ollama` / `qa_generation/smart_qa_generator`・`semantic` / `services/qa_service` | LLM クライアント（Ollama 既定） |
| `helper_embedding.py` | [`helper_embedding.md`](./helper_embedding.md) | E | 639 | 1.0 | `qdrant_client_wrapper` / `services/qdrant_service` / `grace/confidence` / `qa_generation/semantic` | Embedding クライアント（Gemini 既定） |
| `helper_embedding_sparse.py` | [`helper_embedding_sparse.md`](./helper_embedding_sparse.md) | E | 503 | 1.0 | `qdrant_client_wrapper`（← `grace/tools` / `agent_tools`） | 疎ベクトル（SPLADE・negative cache） |
| `helper_text.py` | [`helper_text.md`](./helper_text.md) | E | 758 | 1.0 | `clean_text` だけ（`helper_rag` 経由で `qa_generation/data_io`） | クレンジング・文分割・チャンク分割 |
| `helper_rag.py` | [`helper_rag.md`](./helper_rag.md) | E | 727 | 1.0 | `clean_text` だけ（`qa_generation/data_io`） | Q&A CSV の前処理と保存 |
| `helper_rag_qa.py` | [`helper_rag_qa.md`](./helper_rag_qa.md) | E | 726 | 1.0 | 無し（テストだけ） | Q&A 生成（後方互換。後継は `qa_generation/`） |
| `helper_embedding_fastembed.py` | [`helper_embedding_fastembed.md`](./helper_embedding_fastembed.md) | E | 323 | 1.0 | 無し（`provider="fastembed"` のときだけ） | ローカル Embedding（384 次元・英語向け） |
| ~~`helper_api.py`~~ | — | — | — | — | — | **2026-10-11 に削除**。誰も import しておらず（パッケージ名なしの `from helper_llm import ...` で、単体では import すらできなかった）、中身は `services/` に切り出し済みだった |
| ~~`helper_rag_ui.py`~~ | — | — | — | — | — | **2026-10-11 に削除**。削除済みの `ui/`（Streamlit）を import しており、読み込むだけでエラーになっていた |

> 行数は文書（`.md`）の行数、Ver は各文書の Version ヘッダーの実測値（2026-10-11。`services/docs/README_services.md` §1 と同じ数え方）。

**依存関係**（各 `.py` の import。2026-10-11 実測）:

| モジュール | `helper/` の中の依存先 | `helper/` の外の依存先 |
|---|---|---|
| `helper_llm` | なし | `config`（`get_default_ollama_model`）・`openai`・`httpx`・`google-genai`（遅延）・`tiktoken`・`pydantic`・`dotenv` |
| `helper_embedding` | `helper_embedding_fastembed`（`provider="fastembed"` のときだけ遅延 import） | `google-genai`・`openai`・`dotenv` |
| `helper_embedding_fastembed` | `helper_embedding`（`EmbeddingClient`） | `fastembed`（任意） |
| `helper_embedding_sparse` | なし | `fastembed`（任意）・`tqdm`（任意） |
| `helper_text` | なし | `services.token_service`・`tiktoken` |
| `helper_rag` | `helper_text` | `services.token_service`・`pandas` |
| `helper_rag_qa` | `helper_embedding`・`helper_llm` | `config`・`models`・`regex_mecab`・`qa_generation.semantic`・`spacy`・`numpy`・`tiktoken` |

---

## 2. 使い方（代表的なワークフロー）

各モジュール単体の使い方は、それぞれの文書の **§4.1 使用例**（処理パターンごとの例）にある。ここには**モジュールを組み合わせる**流れだけを置く。

| 使い方 | 組み合わせ | 向いている場面 | 例 |
|---|---|---|---|
| Q/A を作って埋め込む | `helper_rag.clean_text` → `helper_llm`（構造化出力）→ `helper_embedding.embed_texts` | データ準備（整形 → Q/A 生成 → 登録）の流れを 1 本のスクリプトで確かめる | 2.1 |
| 検索クエリの dense と sparse を作る | `qdrant_client_wrapper.embed_query`（`helper_embedding`）＋ `embed_sparse_query_unified`（`helper_embedding_sparse`） | 本番の検索（`grace/tools.py`）と同じ組み立て方 | 2.2 |
| import の副作用を知る | `helper_text` / `helper_rag` を import する | ログやカレントの `config.yml` が変わる理由を知る | 2.3 |

> 📝 2.1〜2.3 は**別プロセスでそのまま実行し**、出力を確かめてある（2026-10-11・リポジトリ直下で実行）。
> Ollama は **代わりの小さな HTTP サーバ**（OpenAI 互換の `/v1/chat/completions`。`OLLAMA_BASE_URL` で向けた）、Gemini Embedding（`google.genai.Client`）・`fastembed` は
> スタブに差し替えたので、**Q/A の文・usage・疎ベクトルの個数はスタブの値**。2.2 は `fastembed` のスタブ無し（この VM の実際の状態）でも動かし、dense だけに落ちることを確かめた。

### 2.1 Q/A を作って埋め込む（整形 → 構造化出力 → Embedding）

```python
from helper.helper_rag import clean_text
from helper.helper_llm import create_llm_client
from helper.helper_embedding import create_embedding_client
from models import QAPairsList

cell = "  住民票の写しは\n市役所の窓口で取得できます。  手数料は1通300円です。 "
text = clean_text(cell)                                        # 1. 入力 CSV のセルを整える（qa_generation/data_io.py）
llm = create_llm_client("ollama")                              # 2. Q/A を構造化出力で作る（SmartQAGenerator と同じ型）
qa = llm.generate_structured(f"次の文から Q/A を 2 つ作ってください。\n{text}", QAPairsList, max_output_tokens=1024)
emb = create_embedding_client("gemini")                        # 3. 質問を Embedding にする（登録は services/qdrant_service.py）
vecs = emb.embed_texts([p.question for p in qa.qa_pairs])
print(repr(text))
print([p.question for p in qa.qa_pairs], llm.last_usage)
print(len(vecs), len(vecs[0]))
```

```
# 出力例（Q/A の文と usage は Ollama の代わりのサーバの値）:
# '住民票の写しは 市役所の窓口で取得できます。 手数料は1通300円です。'
# ['住民票の写しはどこで取得できますか？', '住民票の写しの手数料はいくらですか？'] {'input_tokens': 42, 'output_tokens': 17}
# 2 3072
```

> 📝 本番では 2. を `qa_generation.QAPipeline` → `SmartQAGenerator`、3. を `services/qdrant_service.py` の登録関数（Q と A をつないだ文を埋め込む）が行う。
> ここでは `helper/` の 3 モジュールの役割が見えるよう、直接つないだ。LLM は手元の Ollama で、Embedding だけが外部（Gemini）へ出る。

### 2.2 検索クエリの dense と sparse を作る（本番の検索と同じ）

```python
from qdrant_client_wrapper import embed_query, embed_sparse_query_unified

query = "住民票の写しはどこで取れますか"
dense = embed_query(query)                                     # helper_embedding（Gemini・retrieval_query）
try:
    sparse = embed_sparse_query_unified(query)                 # helper_embedding_sparse（FastEmbed。任意）
except Exception as e:                                         # grace/tools.py と同じく、失敗したら dense だけで検索する
    sparse = None
    print("sparse なし:", type(e).__name__)
print(len(dense), None if sparse is None else len(sparse.indices))
```

```
# 出力例（fastembed がある環境。疎ベクトルの個数はスタブの値）:
# 3072 14
# 出力例（fastembed が無い環境。CI・この VM）:
# sparse なし: ImportError
# 3072 None
# （標準エラーに 1 回だけ「Sparse Embedding の初期化に失敗しました … dense 検索のみで動作します」と、消すべきキャッシュの場所）
```

### 2.3 import の副作用を知る

```python
import logging

root = logging.getLogger()
print(root.level, len(root.handlers))
import helper.helper_text   # noqa: E402  services を import → config_service がカレントの config.yml を読む
print(root.level, len(root.handlers))
import helper.helper_rag    # noqa: E402  logging.basicConfig(level=INFO) を呼ぶ
print(root.level, len(root.handlers))
```

```
# 出力例（リポジトリ直下で実行）:
# 30 0
# 30 0
# 20 1
```

> ⚠️ **`helper_rag` を import すると、ルートロガーが INFO・ハンドラ 1 つになる**（`logging.basicConfig`）。`qa_generation` を import しても同じことが起きる
> （`qa_generation/data_io.py` が `helper_rag` を import するため）。`helper_text` / `helper_rag` は `services` を import するので、
> カレントに `config.yml` が無いと「設定ファイルが見つかりません: config.yml」と表示される。
> `helper_embedding` / `helper_llm` は import 時に `load_dotenv()` でカレントの `.env` を読み、`helper_llm` はそのとき既定モデル（`DEFAULT_OLLAMA_MODEL`）を決める。

---

## 3. 公開 API（import のしかた）

`helper/` に **`__init__.py` は無い**（名前空間パッケージ）。`from helper.helper_xxx import ...` とモジュール名まで書いて import する。
`from helper import X` で取れる名前は無い。

> ⚠️ **パッケージ名を付けない裸 import（`from helper_llm import ...`）を書かない。** `helper/` が `sys.path` に入っているとき（`celery_tasks` を先に読んだとき）だけ
> 解決するため、環境によって動いたり動かなかったりする。2026-10-11 まで `helper_embedding.py` の fastembed 分岐と `helper_embedding_fastembed.py`・削除した `helper_api.py` に残っており、
> `create_embedding_client("fastembed")` は必ず失敗していた。同日に直し、`tests/qa_generation/test_import_side_effects.py` が `helper/*.py` を `ast` で走査して検査するようにした（grace_v2 と同じ検査）。

各モジュールの公開名は、それぞれの文書の「エクスポート」節にある。`__all__` を持つのは `helper_rag.py` だけ（11 名）。

---

## 4. 処理フロー・データフロー

**別文書（`helper_process_flow.md` / `helper_data_flow.md`）は置かない。** `helper/` のモジュールは、LLM・Embedding・テキストの部品として
**1 回の呼び出しで完結する**ものが多く、3 モジュール以上をまたぐ多段の処理は呼び出し側（`grace/`・`qdrant_client_wrapper.py`・`qa_generation/`）にあるため
（`a_cross_doc_md_format.md` §1.3）。モジュールをまたぐ処理の流れは各領域の文書を見る:

| 流れ | 文書 |
|---|---|
| 推論（LLM の呼び出し・期限・思考モデル） | `grace/docs/llm_compat.md`・`grace/docs/grace_process_flow.md` |
| 検索（dense＋sparse・hybrid） | `grace/docs/tools.md` |
| 登録（Q/A CSV → Embedding → Qdrant） | `services/docs/qdrant_service.md`・`backend/docs/data_pipeline.md` |
| チャンク化・Q/A 生成 | `chunking/docs/README.md`・`qa_generation/docs/README.md` |

読み書きするファイル・外部サービス:

| 対象 | 場所 | モジュール | 読む／書く |
|---|---|---|---|
| Ollama | `OLLAMA_BASE_URL`（既定 `http://localhost:11434/v1`） | `helper_llm` | 呼ぶ（手元。API キー不要） |
| Gemini Embedding API | `GOOGLE_API_KEY` / `GEMINI_API_KEY` | `helper_embedding` | 呼ぶ |
| FastEmbed のモデル | HuggingFace → `FASTEMBED_CACHE_PATH`（既定 `<tempdir>/fastembed_cache`） | `helper_embedding_sparse` / `helper_embedding_fastembed` | 初回に取得 |
| tiktoken の符号表 | `openaipublic.blob.core.windows.net` → tiktoken のキャッシュ | `helper_text` / `helper_llm`（`count_tokens`）/ `helper_rag_qa` | 初回に取得（LLM がローカルでもここは外部へ出る） |
| `.env` | カレント | `helper_embedding` / `helper_llm` / `helper_rag_qa` | 読む |
| `OUTPUT/` | カレントからの相対 | `helper_rag.save_files_to_output` | 書く（同名は上書き） |

---

## 5. 既知の制約・残課題

2026-10-11 に 7 モジュールの文書を書き、使用例を動かす中で確かめた制約（詳細は各文書の §5）。#6 以外のコードは変えていない。

| # | 制約 | 状態 |
|---|---|---|
| 1 | `helper_llm._resolve_schema_refs` が `title` という名前のプロパティまでスキーマから取り除く（`required` には残る）。構造化出力のスキーマ制約付きデコードで `title` を出力できなくなるおそれ | 未修正。本番の構造化出力のスキーマ（チャンク化・Q/A 生成）に `title` フィールドは無いので未発現（`helper_llm.md` §4.1.3） |
| 2 | `helper_llm.OllamaClient.last_thinking_only` を読む本番のコードが無い（「思考だけで本文ゼロ」を上位が見分けられない） | テストだけが読む（`helper_llm.md` §5.3） |
| 3 | `helper_embedding.GeminiEmbedding.embed_texts` は API エラー・件数の合わない応答をゼロベクトルで埋め、例外を出さない。登録は成功したように見え、そのテキストは検索で当たらない | 並びを保つための設計（`helper_embedding.md` §4.1.3） |
| 4 | `helper_embedding` はモデル名・次元・単価を文字列で直書きしている（grace_v2 は `config.py::ModelConfig` を参照） | Embedding モデルを変えるときは本モジュールも直す |
| 5 | `helper_embedding_fastembed.FastEmbedEmbedding.embed_text` が `task_type` を受け取らず、`EMBEDDING_PROVIDER=fastembed` では検索クエリの埋め込みが `TypeError` | 本番では選ばないので未修正 |
| 6 | `helper_embedding.py`（fastembed 分岐）と `helper_embedding_fastembed.py` の裸 import で、`create_embedding_client("fastembed")` が必ず失敗していた | **2026-10-11 に修正**（§3） |
| 7 | `helper_text.clean_text` の「引用符の正規化」は何もしていない | 呼び出し元は当てにしていない（`helper_text.md` §5.3） |
| 8 | `helper_rag_qa.QAGenerationConsiderations.analyze_document_characteristics` は定義されていないメソッドを 7 つ呼び、必ず `AttributeError`。`OptimizedHybridQAGenerator.supported_models` は古いモデル名のまま | 本番の呼び出し元が無い（`helper_rag_qa.md` §5.2） |
| 9 | `helper_rag` は import 時に `logging.basicConfig(level=INFO)` を呼ぶ | 実装どおり（§2.3） |
| 10 | `helper_text`（`clean_text` 以外）・`helper_rag`（`clean_text` 以外）・`helper_rag_qa`・`helper_embedding_fastembed` は本番の呼び出し元が無い | 削除するかは未決定（`helper_api.py` / `helper_rag_ui.py` は 2026-10-11 に削除済み） |

### 5.1 文書を保守するときの約束とテスト件数

- `helper/*.py` を足したら、`<module>.md` を `a_class_method_md_format.md` で書き、§1 の表に行を足す。
- 使用例（各文書の §4.1）は**動かして確かめる**。Ollama・Gemini・fastembed はスタブ（Ollama は代わりの HTTP サーバ）でよいが、スタブの値であることを出力例に書く。
- 姉妹リポジトリの文書をコピーしない（本文の冒頭の注記）。Anthropic / Claude のモデル名・クライアントを足さない（CLAUDE.md §5）。

関連するテスト（2026-10-11 に `env -u OLLAMA_DEFAULT_MODEL PYTHONPATH=. .venv/bin/python -m pytest <file> -q` で実測）:

| テスト | 件数 | 対象 |
|---|---:|---|
| `tests/helpers/test_helper_llm.py` | 15 | `helper_llm` の `create_llm_client`（プロバイダ名）と後方互換の `OpenAIClient` / `GeminiClient`（SDK を差し替え） |
| `tests/test_schema_echo.py` | 13 | `OllamaClient.generate_structured` のスキーマのオウム返しと `json_schema` の退避 |
| `tests/test_thinking_only_model.py` | 19 | 思考だけの応答・`reasoning_effort` の機能検出 |
| `tests/test_empty_response_diagnostics.py` | 16 | 本文が空のときの観測ログ（`_log_empty_content`） |
| `tests/test_ollama_llm_client.py` | 2 | `_resolve_schema_refs`（`$ref` の展開・自己参照の打ち切り） |
| `tests/test_llm_provider_validation.py` | 6 | `create_llm_client` の未知のプロバイダ・既定が ollama |
| `tests/test_timeout_budget.py` | 17（うち 4） | `TestOllamaClientDeadline`（期限が有限・接続 5 秒・SDK のリトライ 0・上書き） |
| `tests/test_no_anthropic_path.py` | 13（うち 4） | `helper_llm` が `anthropic` を拒むこと・`LLM_*` の表に Claude が無いこと |
| `tests/test_model_info_api.py` | 15（うち 1） | 既定モデルが `helper_llm` の表に登録されていること |
| `tests/test_helper_llm_step1.py` | 1 skipped | `GeminiClient` のライブテスト（`RUN_GEMINI_LLM_LIVE=1` のときだけ） |
| `tests/helpers/test_helper_embedding.py` | 8 | `helper_embedding`（ファクトリ・`OpenAIEmbedding` / `GeminiEmbedding` の 1 件・まとめて・次元） |
| `tests/test_gemini_embedding_batch.py` | 3 | `helper_embedding`（1 件 = 1 Content・件数の合わない応答のゼロ埋め） |
| `tests/test_sparse_embedding_cache.py` | 13 | `helper_embedding_sparse`（失敗の記憶・モデルの切り替え・警告に出すキャッシュの場所・`resolve_cache_dir`） |
| `tests/test_silent_failures.py` | 15（うち 5） | `TestSparseInitFailureIsRemembered`（negative cache） |
| `tests/qa_generation/test_structure.py` | 2 | `helper_text.merge_small_chunks` |
| `tests/qa_generation/test_hybrid_qa_generator.py` | 29 | `helper_rag_qa.HybridQAGenerator` / `QAGenerationOptimizer` / `TemplateBasedQAGenerator` |
| `tests/qa_generation/test_keyword_extraction.py` | 8 | `helper_rag_qa` のキーワード抽出 |
| `tests/qa_generation/test_qa_pair_definitions.py` | 6 | `QAPair` / `QAPairsList` の定義が `models.py` の 1 箇所 |
| `tests/qa_generation/test_import_side_effects.py` | 3（うち 1） | `helper/*.py` に裸 import が無い（2026-10-11 に追加） |

`helper_rag.py` / `helper_embedding_fastembed.py` を直接検査するテストは無い。

---

## 6. 変更履歴

| バージョン | 日付 | 変更内容 |
|-----------|------|---------|
| 1.0 | 2026-10-11 | 初版。`helper/docs/` を新設し、7 モジュールの文書（`a_class_method_md_format.md`）と本 README（`a_cross_doc_md_format.md` §1.2）を作成した（grace_v2 の `helper/docs/` と同じ構成で、本リポジトリの実装〔`OllamaClient`・直書きの Embedding 既定値・`resolve_cache_dir`・`tests/` の配置〕に合わせて書いた）。未使用だった `helper_api.py`（誰も import しておらず、裸 import で単体では読み込めなかった）と `helper_rag_ui.py`（削除済みの `ui/` を import し、読み込めない）を削除した。`helper_embedding.py` / `helper_embedding_fastembed.py` の裸 import を直し、`tests/qa_generation/test_import_side_effects.py` に検査を足した（修正前のコードで失敗することを確かめた）。各文書の §4.1 使用例と本 README の §2 を別プロセスで実行して出力を確かめ（Ollama は代わりの HTTP サーバ、Gemini・fastembed はスタブ）、見つかった制約を §5 にまとめた |

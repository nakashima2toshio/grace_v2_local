# semantic.py - セマンティックチャンク分割・埋め込み ドキュメント

**Version 1.2** | 最終更新: 2026-09-24

---

## 目次

1. [概要](#概要)
2. [アーキテクチャ構成図](#1-アーキテクチャ構成図)
3. [モジュール構成図](#2-モジュール構成図)
4. [セマンティックチャンク分割](#3-セマンティックチャンク分割)
5. [埋め込みベクトル生成](#4-埋め込みベクトル生成)
6. [クラス・関数一覧表](#5-クラス関数一覧表)
7. [クラス・関数 IPO詳細](#6-クラス関数-ipo詳細)
8. [設定・定数](#7-設定定数)
9. [関連モジュール](#8-関連モジュール)
10. [ベストプラクティス](#9-ベストプラクティス)
11. [変更履歴](#10-変更履歴)

---

## 概要

`qa_generation/semantic.py` は、**セマンティック（意味的）分析とカバレッジ測定を行うモジュール**です。文書のセマンティックチャンク分割、埋め込みベクトル生成、コサイン類似度計算など、Q/A生成システムの基盤となる機能を提供します。

### 主な責務

- 文書を意味のまとまりでチャンクに分割する
- 段落・文の境界を検出する
- 上限を超える文やチャンクを調整する
- 埋め込みベクトルを生成する
- ベクトル間の類似度を計算する

### 各責務対応のモジュール

| # | 責務 | 対応モジュール | 説明 |
|---|---|---|---|
| 1 | 文書を意味のまとまりでチャンクに分割する | `SemanticCoverage.create_semantic_chunks()` | 段落 → 文の優先順で分割し、トークン上限・下限・オーバーラップを守る |
| 2 | 段落・文の境界を検出する | `_split_into_paragraphs()` / `_split_into_sentences()` / `_split_sentences_mecab()` | 日本語の文境界を正規表現（MeCab があれば MeCab）で判定 |
| 3 | 上限を超える文やチャンクを調整する | `_force_split_sentence()` / `_adjust_chunks_for_topic_continuity()` / `_apply_chunk_overlap()` | 強制分割・短いチャンクの結合・前後の重なりを付ける |
| 4 | 埋め込みベクトルを生成する | `generate_embeddings()` / `generate_embedding()` / `generate_embeddings_batch()` | Gemini Embedding（`gemini-embedding-001`・3072 次元）をバッチで呼ぶ |
| 5 | ベクトル間の類似度を計算する | `cosine_similarity()` / `_calculate_sentence_similarities()` | コサイン類似度（類似度ベース分割にも使う） |

### 主要機能一覧

| 機能 | 説明 |
|---|---|
| `SemanticCoverage(embedding_model="gemini-embedding-001")` | Embedding クライアントとトークナイザを用意する |
| `create_semantic_chunks(document, max_tokens, min_tokens, ...)` | セマンティックチャンク分割 |
| `generate_embeddings(doc_chunks)` / `generate_embeddings_batch(texts)` | 埋め込み生成 |
| `cosine_similarity(doc_emb, qa_emb)` | 類似度計算 |

---

## 1. アーキテクチャ構成図

### 1.1 システム全体構成

```mermaid
flowchart TB
    subgraph CALLER["呼び出し側"]
        EV["evaluation.analyze_coverage()"]
        HR["helper/helper_rag_qa.py（後方互換の再公開）"]
    end
    subgraph TARGET["semantic.py"]
        SC["SemanticCoverage（分割・埋め込み・類似度）"]
    end
    subgraph EXTERNAL["外部（LLM・Embedding・ファイル・基盤）"]
        EMB["Gemini Embedding API（create_embedding_client('gemini')）"]
        TOK["トークン数: create_llm_client('ollama').count_tokens（tiktoken 近似）"]
        MEC["MeCab（任意・文分割）"]
    end
    EV --> SC
    HR --> SC
    SC -->|"埋め込み"| EMB
    SC -->|"トークン数"| TOK
    SC -->|"文分割"| MEC
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class EV,HR,SC,EMB,TOK,MEC default
style CALLER fill:#1a1a1a,stroke:#fff,color:#fff
style TARGET fill:#1a1a1a,stroke:#fff,color:#fff
style EXTERNAL fill:#1a1a1a,stroke:#fff,color:#fff
```

### 1.2 データフロー

1. 呼び出し側が `SemanticCoverage` を生成する（Embedding は Gemini、トークン数は LLM クライアントの `count_tokens`＝tiktoken 近似）
2. `create_semantic_chunks()` が段落 → 文の順に分割し、トークン上限を超える文は強制分割する
3. 短すぎるチャンクを結合し、必要ならオーバーラップを付ける
4. `generate_embeddings()` / `generate_embeddings_batch()` がチャンクや Q/A の埋め込みを生成する
5. `cosine_similarity()` で類似度を計算し、呼び出し側（`evaluation`）がカバレッジ行列を作る

---

## 2. モジュール構成図

### 2.1 全体構成

```
┌─────────────────────────────────────────────────────────────┐
│                      semantic.py                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              SemanticCoverage クラス                 │    │
│  ├─────────────────────────────────────────────────────┤    │
│  │  【初期化】                                          │    │
│  │  __init__()                  # 初期化・クライアント設定 │   │
│  │  _check_mecab_availability() # MeCab利用可否チェック   │   │
│  │                                                     │   │
│  │  【チャンク分割】                                     │   │
│  │  create_semantic_chunks()    # メインチャンク分割      │   │
│  │  _chunk_by_paragraphs()      # 段落ベース分割         │   │
│  │  _split_into_paragraphs()    # 段落抽出             │   │
│  │  _split_into_sentences()     # 文分割               │   │
│  │  _split_sentences_mecab()    # MeCab文分割          │   │
│  │  _force_split_sentence()     # 強制分割             │   │
│  │  _adjust_chunks_for_topic_continuity()             │   │
│  │                              # トピック連続性調整     │   │
│  │  _apply_chunk_overlap()      # オーバーラップ適用     │   │
│  │  _calculate_sentence_similarities()                │   │
│  │                              # 文間類似度計算        │   │
│  │                                                    │   │
│  │  【埋め込み生成】                                     │   │
│  │  generate_embeddings()       # チャンク埋め込み生成    │   │
│  │  generate_embedding()        # 単一テキスト埋め込み    │   │
│  │  generate_embeddings_batch() # バッチ埋め込み生成      │   │
│  │                                                     │    │
│  │  【類似度計算】                                       │    │
│  │  cosine_similarity()         # コサイン類似度計算      │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│  helper_llm     │ │ helper_embedding│ │    MeCab        │
│  (トークン計算)   │ │  (埋め込み生成)   │ │  (日本語分割)     │
└─────────────────┘ └─────────────────┘ └─────────────────┘
```

### 2.2 処理フロー概要

```mermaid
graph TB
    subgraph CHUNK["チャンク分割"]
        A[文書入力] --> B{段落優先?}
        B -->|Yes| C[段落ベース分割]
        B -->|No| D[文ベース分割]
        C --> E[トークン数チェック]
        D --> E
        E --> F[トピック連続性調整]
        F --> G{オーバーラップ?}
        G -->|Yes| H[オーバーラップ適用]
        G -->|No| I[チャンク完成]
        H --> I
    end

    subgraph EMBED["埋め込み生成"]
        J[チャンク/テキスト] --> K[Gemini Embedding API呼び出し]
        K --> L[L2正規化]
        L --> M[埋め込みベクトル]
    end

    subgraph SIM["類似度計算"]
        N[ベクトルA] --> O[コサイン類似度]
        P[ベクトルB] --> O
        O --> Q[類似度スコア]
    end
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class A,B,C,D,E,F,G,H,I,J,K,L,M,N,O,P,Q default
style CHUNK fill:#1a1a1a,stroke:#fff,color:#fff
style EMBED fill:#1a1a1a,stroke:#fff,color:#fff
style SIM fill:#1a1a1a,stroke:#fff,color:#fff
```

---

## 3. セマンティックチャンク分割

### 3.1 分割の優先順位

```
1. 段落（paragraph）
   ├─ 筆者が意図的に作成した意味的まとまり
   └─ 最も重要なセマンティック境界

2. 文グループ（sentence_group）
   ├─ 段落が大きすぎる場合に文単位で分割
   └─ トークン数制限を考慮

3. セマンティックグループ（semantic_group）
   ├─ 類似度ベースの分割を使用した場合
   └─ 文間の類似度が閾値を下回る箇所で分割

4. マージ済み（merged）
   ├─ 短いチャンクを隣接チャンクと結合
   └─ トピック連続性を維持

5. 強制分割（forced_split）
   ├─ 単一文が上限超過の場合
   └─ セマンティック境界を無視してトークン分割
```

### 3.2 チャンクタイプの決定フロー

```mermaid
flowchart TD
    A[テキスト入力] --> B{段落サイズ適切?}
    B -->|Yes| C["type='paragraph'"]
    B -->|No| D{文単位で分割可能?}
    D -->|Yes| E{類似度分割?}
    E -->|Yes| F["type='semantic_group'"]
    E -->|No| G["type='sentence_group'"]
    D -->|No| H["type='forced_split'"]

    C --> I{短すぎてマージ?}
    F --> I
    G --> I
    H --> I
    I -->|Yes| J["type='merged'"]
    I -->|No| K[タイプ確定]
    J --> K
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class A,B,C,D,E,F,G,H,I,J,K default
```

### 3.3 トークン数の基準

| 設定項目 | デフォルト値 | 説明 |
|---------|:-----------:|------|
| max_tokens | 200 | チャンクの最大トークン数 |
| min_tokens | 50 | これより小さい場合はマージを検討 |
| マージ上限 | 300 | マージ後のトークン数上限 |

---

## 4. 埋め込みベクトル生成

### 4.1 Gemini Embedding仕様

| 項目 | 値 |
|-----|---|
| モデル | gemini-embedding-001 |
| 次元数 | 3072 |
| 正規化 | L2正規化（自動適用） |

### 4.2 埋め込み生成の最適化

```python
# バッチ処理による効率化
embeddings = analyzer.generate_embeddings_batch(texts, batch_size=100)

# 正規化済みのため、内積でコサイン類似度が計算可能
similarity_matrix = np.dot(embeddings, embeddings.T)
```

### 4.3 エラーハンドリング

```python
# APIキーなし or APIエラー時
return np.zeros((len(texts), 3072))  # ゼロベクトルを返却
```

---

## 5. クラス・関数一覧表

### 5.1 SemanticCoverageクラス

| メソッド名 | 可視性 | 機能概要 |
|-----------|:-----:|---------|
| `__init__` | public | 初期化。埋め込みクライアント、LLMクライアント、トークナイザーの設定 |
| `_check_mecab_availability` | private | MeCabの利用可能性をチェック |
| `create_semantic_chunks` | public | 文書をセマンティックチャンクに分割（メイン関数） |
| `_chunk_by_paragraphs` | private | 段落単位でチャンク化 |
| `_split_into_paragraphs` | private | テキストを段落に分割 |
| `_split_into_sentences` | private | テキストを文に分割（言語自動判定） |
| `_split_sentences_mecab` | private | MeCabを使用した日本語文分割 |
| `_force_split_sentence` | private | 単一文を強制的にトークン分割 |
| `_adjust_chunks_for_topic_continuity` | private | 短いチャンクを隣接チャンクとマージ |
| `_apply_chunk_overlap` | private | チャンク間にオーバーラップを適用 |
| `_calculate_sentence_similarities` | private | 隣接文間のコサイン類似度を計算 |
| `generate_embeddings` | public | チャンクリストから埋め込みベクトルを生成 |
| `generate_embedding` | public | 単一テキストの埋め込みを生成 |
| `generate_embeddings_batch` | public | 複数テキストの埋め込みを一括生成 |
| `cosine_similarity` | public | 2つのベクトルのコサイン類似度を計算 |

---

## 6. クラス・関数 IPO詳細

### 6.1 使用例

#### 6.1.1 基本的な使用例

```python
from qa_generation.semantic import SemanticCoverage

# 初期化
analyzer = SemanticCoverage()

# セマンティックチャンク分割
document = """
これは最初の段落です。重要な情報が含まれています。

これは2番目の段落です。別のトピックについて説明しています。
詳細な情報も含まれています。
"""

chunks = analyzer.create_semantic_chunks(
    document,
    max_tokens=200,
    min_tokens=50,
    prefer_paragraphs=True
)

for chunk in chunks:
    print(f"ID: {chunk['id']}, Type: {chunk['type']}")
    print(f"Text: {chunk['text'][:50]}...")
```

#### 6.1.2 埋め込み生成

```python
# チャンクの埋め込み生成
embeddings = analyzer.generate_embeddings(chunks)
print(f"Shape: {embeddings.shape}")  # (N, 3072)

# 単一テキストの埋め込み
embedding = analyzer.generate_embedding("質問テキスト")
print(f"Shape: {embedding.shape}")  # (3072,)

# バッチ埋め込み
texts = ["テキスト1", "テキスト2", "テキスト3"]
embeddings = analyzer.generate_embeddings_batch(texts)
```

#### 6.1.3 類似度計算

```python
# 2つのテキストの類似度を計算
emb1 = analyzer.generate_embedding("AES-256は256ビットの鍵長を持つ")
emb2 = analyzer.generate_embedding("AES-256の鍵長は256ビット")

similarity = analyzer.cosine_similarity(emb1, emb2)
print(f"類似度: {similarity:.3f}")  # 0.95程度
```

#### 6.1.4 オーバーラップ付きチャンク分割

```python
chunks = analyzer.create_semantic_chunks(
    document,
    max_tokens=200,
    overlap_tokens=50,  # 50トークン分の重複
    prefer_paragraphs=True
)

for chunk in chunks:
    if chunk.get('is_overlapped'):
        print(f"オーバーラップ: {chunk['overlap_text'][:30]}...")
```

#### 6.1.5 類似度ベース分割

```python
chunks = analyzer.create_semantic_chunks(
    document,
    use_similarity=True,
    similarity_threshold=0.7,
    prefer_paragraphs=False  # 文ベース分割を使用
)
```

### 6.2 \_\_init\_\_()

#### IPO

| 区分 | 内容 |
|-----|------|
| **Input** | `embedding_model`: str（埋め込みモデル名、デフォルト: "gemini-embedding-001"） |
| **Process** | 1. 埋め込みクライアント初期化（Gemini / gemini-embedding-001）<br>2. 埋め込み次元数取得（3072）<br>3. LLMクライアント初期化（ローカル LLM / Ollama・トークン計算用）<br>4. tiktokenエンコーダ初期化<br>5. MeCab利用可否チェック |
| **Output** | SemanticCoverageインスタンス |

#### プロセスフロー

```mermaid
flowchart TD
    A[embedding_model受信] --> B[embedding_client作成]
    B --> C[embedding_dims取得: 3072]
    C --> D[unified_client作成]
    D --> E[tiktoken初期化]
    E --> F[MeCab利用可否チェック]
    F --> G[インスタンス完成]
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class A,B,C,D,E,F,G default
```

### 6.3 create_semantic_chunks()

#### IPO

| 区分 | 内容 |
|-----|------|
| **Input** | `document`: str（分割対象文書）<br>`max_tokens`: int（最大トークン数、デフォルト200）<br>`min_tokens`: int（最小トークン数、デフォルト50）<br>`overlap_tokens`: int（オーバーラップトークン数、デフォルト0）<br>`use_similarity`: bool（類似度分割使用、デフォルトFalse）<br>`similarity_threshold`: float（類似度閾値、デフォルト0.7）<br>`prefer_paragraphs`: bool（段落優先、デフォルトTrue）<br>`verbose`: bool（詳細出力、デフォルトTrue） |
| **Process** | 1. 段落ベース or 文ベース分割選択<br>2. トークン数に基づくチャンク作成<br>3. トピック連続性調整（短チャンクマージ）<br>4. オーバーラップ適用（指定時） |
| **Output** | `List[Dict]`: チャンク辞書のリスト |

#### プロセスフロー

```mermaid
flowchart TD
    A[document受信] --> B{prefer_paragraphs?}

    subgraph PARA["段落ベース分割"]
        B -->|Yes| C[_chunk_by_paragraphs]
        C --> D[標準フォーマット変換]
    end

    subgraph SENT["文ベース分割"]
        B -->|No| E[_split_into_sentences]
        E --> F{use_similarity?}
        F -->|Yes| G[_calculate_sentence_similarities]
        F -->|No| H[トークン数ベース分割]
        G --> I[類似度+トークン数分割]
        H --> J[チャンク生成]
        I --> J
    end

    D --> K[_adjust_chunks_for_topic_continuity]
    J --> K
    K --> L{overlap_tokens > 0?}
    L -->|Yes| M[_apply_chunk_overlap]
    L -->|No| N[チャンクリスト返却]
    M --> N
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class A,B,C,D,E,F,G,H,I,J,K,L,M,N default
style PARA fill:#1a1a1a,stroke:#fff,color:#fff
style SENT fill:#1a1a1a,stroke:#fff,color:#fff
```

#### 出力構造

```python
[
    {
        "id": "chunk_0",
        "text": "チャンクテキスト...",
        "type": "paragraph",  # paragraph/sentence_group/semantic_group/merged/forced_split
        "sentences": ["文1。", "文2。", ...],
        "start_sentence_idx": 0,
        "end_sentence_idx": 3,
        # オーバーラップ時のみ
        "overlap_text": "前チャンクの末尾文...",
        "is_overlapped": True
    },
    ...
]
```

### 6.4 _chunk_by_paragraphs()

#### IPO

| 区分 | 内容 |
|-----|------|
| **Input** | `text`: str（分割対象テキスト）<br>`max_tokens`: int（最大トークン数）<br>`min_tokens`: int（最小トークン数） |
| **Process** | 1. 段落抽出<br>2. 各段落のトークン数チェック<br>3. 大きすぎる段落は文単位に分割<br>4. 超長文は強制分割 |
| **Output** | `List[Dict]`: {text, type}のリスト |

#### プロセスフロー

```mermaid
flowchart TD
    A[text受信] --> B[段落抽出]
    B --> C[各段落をループ]
    C --> D{para_tokens <= max_tokens?}
    D -->|Yes| E["type='paragraph'として追加"]
    D -->|No| F[文単位に分割]
    F --> G[各文をループ]
    G --> H{sent_tokens > max_tokens?}
    H -->|Yes| I[強制分割]
    H -->|No| J{追加で上限超過?}
    J -->|Yes| K[現チャンク確定、新チャンク開始]
    J -->|No| L[文を追加]
    I --> M[次の文へ]
    K --> M
    L --> M
    E --> N{次の段落?}
    M --> N
    N -->|Yes| C
    N -->|No| O[チャンクリスト返却]
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class A,B,C,D,E,F,G,H,I,J,K,L,M,N,O default
```

### 6.5 _split_into_sentences()

#### IPO

| 区分 | 内容 |
|-----|------|
| **Input** | `text`: str（分割対象テキスト） |
| **Process** | 1. 日本語判定（最初の100文字）<br>2. 日本語かつMeCab利用可能→MeCab使用<br>3. それ以外→正規表現で句点分割 |
| **Output** | `List[str]`: 文のリスト |

#### プロセスフロー

```mermaid
flowchart TD
    A[text受信] --> B[日本語判定]
    B --> C{日本語?}
    C -->|Yes| D{MeCab利用可能?}
    D -->|Yes| E[_split_sentences_mecab]
    D -->|No| F[正規表現分割]
    C -->|No| F
    E --> G{成功?}
    G -->|Yes| H[文リスト返却]
    G -->|No| F
    F --> I[句点で分割]
    I --> J[残余テキスト処理]
    J --> H
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class A,B,C,D,E,F,G,H,I,J default
```

#### 日本語判定パターン

```python
# ひらがな、カタカナ、漢字のいずれかを含むか
is_japanese = bool(re.search(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]', text[:100]))
```

### 6.6 generate_embeddings()

#### IPO

| 区分 | 内容 |
|-----|------|
| **Input** | `doc_chunks`: List[Dict]（チャンクのリスト、各チャンクに'text'キーが必要） |
| **Process** | 1. APIキー有無チェック<br>2. テキスト抽出<br>3. Gemini Embedding API呼び出し<br>4. L2正規化 |
| **Output** | `np.ndarray`: 埋め込みベクトル配列 (N × 3072) |

#### プロセスフロー

```mermaid
flowchart TD
    A[doc_chunks受信] --> B{has_api_key?}
    B -->|No| C[ゼロベクトル返却]
    B -->|Yes| D[テキスト抽出]
    D --> E[embed_texts呼び出し]
    E --> F{成功?}
    F -->|Yes| G[各ベクトルをループ]
    G --> H[np.array変換]
    H --> I[L2正規化]
    I --> J{次のベクトル?}
    J -->|Yes| G
    J -->|No| K[np.array化して返却]
    F -->|No| C
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class A,B,C,D,E,F,G,H,I,J,K default
```

#### L2正規化の重要性

```python
# 正規化によりコサイン類似度が内積で計算可能に
norm = np.linalg.norm(embedding)
if norm > 0:
    embedding = embedding / norm

# 正規化済みベクトル同士の場合
cosine_similarity = np.dot(vec_a, vec_b)  # 内積で計算可能
```

### 6.7 generate_embeddings_batch()

#### IPO

| 区分 | 内容 |
|-----|------|
| **Input** | `texts`: List[str]（テキストのリスト）<br>`batch_size`: int（バッチサイズ、デフォルト100） |
| **Process** | 1. APIキー有無チェック<br>2. Gemini Embedding API呼び出し（バッチ処理）<br>3. L2正規化 |
| **Output** | `np.ndarray`: 埋め込みベクトル配列 (N × 3072) |

#### プロセスフロー

```mermaid
flowchart TD
    A[texts, batch_size受信] --> B{has_api_key?}
    B -->|No| C[ゼロベクトル返却]
    B -->|Yes| D[embed_texts呼び出し]
    D --> E{成功?}
    E -->|Yes| F[各ベクトルを正規化]
    F --> G[np.array化して返却]
    E -->|No| C
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class A,B,C,D,E,F,G default
```

### 6.8 cosine_similarity()

#### IPO

| 区分 | 内容 |
|-----|------|
| **Input** | `doc_emb`: np.ndarray（文書埋め込み）<br>`qa_emb`: np.ndarray（Q/A埋め込み） |
| **Process** | 1. 正規化済みチェック<br>2. 正規化済み→内積で計算<br>3. 未正規化→完全な計算 |
| **Output** | `float`: コサイン類似度 (-1.0 〜 1.0) |

#### プロセスフロー

```mermaid
flowchart TD
    A[doc_emb, qa_emb受信] --> B{両方正規化済み?}
    B -->|Yes| C[内積計算]
    B -->|No| D[完全なコサイン類似度計算]
    C --> E[float返却]
    D --> F{ノルムがゼロ?}
    F -->|Yes| G[0.0返却]
    F -->|No| H[dot / norm_a * norm_b]
    H --> E
classDef default fill:#000,stroke:#fff,color:#fff
classDef subgraphStyle fill:#1a1a1a,stroke:#fff,color:#fff
class A,B,C,D,E,F,G,H default
```

#### 計算式

```
正規化済み: similarity = dot(a, b)
未正規化:   similarity = dot(a, b) / (||a|| × ||b||)
```

---

## 7. 設定・定数

### 7.1 初期化パラメータ

| パラメータ | 型 | デフォルト | 説明 |
|----------|---|----------|------|
| `embedding_model` | str | "gemini-embedding-001" | 使用する埋め込みモデル |

### 7.2 create_semantic_chunksパラメータ

| パラメータ | 型 | デフォルト | 説明 |
|----------|---|----------|------|
| `document` | str | - | 分割対象の文書 |
| `max_tokens` | int | 200 | チャンクの最大トークン数 |
| `min_tokens` | int | 50 | チャンクの最小トークン数 |
| `overlap_tokens` | int | 0 | オーバーラップトークン数 |
| `use_similarity` | bool | False | 類似度ベース分割を使用 |
| `similarity_threshold` | float | 0.7 | 分割判定の類似度閾値 |
| `prefer_paragraphs` | bool | True | 段落ベース分割を優先 |
| `verbose` | bool | True | 詳細なログ出力 |

### 7.3 内部設定値

| 項目 | 値 | 説明 |
|-----|---|------|
| 埋め込み次元数 | 3072 | Gemini Embeddingの出力次元 |
| バッチサイズ（デフォルト） | 100 | 埋め込み生成時のデフォルトバッチサイズ |
| トークナイザー | cl100k_base | tiktoken使用 |
| マージ上限 | 300 | チャンクマージ時の最大トークン数 |

---

## 8. 関連モジュール

| モジュール | 関係 |
|-----------|------|
| `qa_generation/evaluation.py` | SemanticCoverageを使用してカバレッジ分析 |
| `qa_generation/pipeline.py` | チャンク分割にSemanticCoverageを使用（v2.x以前） |
| `helper/helper_embedding.py` | 埋め込みクライアントを提供 |
| `helper/helper_llm.py` | LLMクライアント（トークン計算用）を提供 |

### 8.1 evaluation.pyでの使用例

```python
# evaluation.py内部
analyzer = SemanticCoverage()

# チャンク埋め込み
doc_embeddings = analyzer.generate_embeddings(chunks)

# Q/A埋め込み（バッチ）
qa_embeddings = analyzer.generate_embeddings_batch(qa_texts, batch_size=2048)

# カバレッジ行列計算
coverage_matrix = np.dot(doc_embeddings, qa_embeddings.T)
```

---

## 9. ベストプラクティス

### 9.1 チャンク分割の選択

```python
# 通常のドキュメント（段落構造あり）
chunks = analyzer.create_semantic_chunks(
    document,
    prefer_paragraphs=True
)

# 段落構造がない/不明確なテキスト
chunks = analyzer.create_semantic_chunks(
    document,
    prefer_paragraphs=False,
    use_similarity=True
)
```

### 9.2 適切なトークン数設定

```python
# 短めのチャンク（詳細なQ/A生成向け）
chunks = analyzer.create_semantic_chunks(
    document,
    max_tokens=150,
    min_tokens=30
)

# 長めのチャンク（コンテキスト重視）
chunks = analyzer.create_semantic_chunks(
    document,
    max_tokens=300,
    min_tokens=100
)
```

### 9.3 オーバーラップの活用

```python
# 文脈を維持したい場合
chunks = analyzer.create_semantic_chunks(
    document,
    overlap_tokens=50  # 約1-2文分
)
```

### 9.4 大量テキストの埋め込み生成

```python
# 大量のテキストを処理する場合はバッチサイズを調整
embeddings = analyzer.generate_embeddings_batch(
    texts,
    batch_size=2048  # APIの制限に応じて調整
)
```

---

**作成日**: 2025-01-27
**対象ファイル**: `qa_generation/semantic.py`
**総行数**: 537行

---

## 10. 変更履歴

| バージョン | 変更内容 |
|---|---|
| 1.2 | 基本フォーマット `a_class_method_md_format.md` の章構成へ組み替え（2026-09-24）。概要に「主な責務」と「各責務対応のモジュール」（1:1）を置き、`## 1. アーキテクチャ構成図`（3 層＋データフロー）を新設。既存の構成図は `## 2. モジュール構成図` へ、使用方法は IPO 詳細の冒頭（`### 6.1 使用例`）へ移した。固有の解説章（「セマンティックチャンク分割」・「埋め込みベクトル生成」）は §1.3 に従い一覧表の前に置き、章・小節に番号を振った。本文の内容は変えていない |
| 1.1 | **LLM 表記を Ollama へ是正**（2026-09-21）。実装は `create_llm_client(provider="ollama")`（`semantic.py:32`）だが、文書は Anthropic Claude のままだった。あわせて `**Version X.X**` ヘッダーを追加 |
| 1.0 | 初版（2026-06-21 時点。当時は LLM 表記を Anthropic Claude に統一していた） |

# csv_text_to_chunks_text_csv.py
"""
csv_text_to_chunks_text_csv.py - LLMベースセマンティックチャンキング（統一版）

主要機能:
- chunks_all_async(): テキストからチャンクを作成（LLMベース、asyncio並列処理）
- load_text_from_csv(): CSVファイルからテキストを読み込み
- save_chunks_as_csv(): チャンクをCSV形式で保存（改行正規化対応 + シンプルCSV追加出力）
- save_chunks_as_simple_csv(): チャンクをシンプルCSV形式で保存（Textカラムのみ）
- generate_output_filename(): 出力ファイル名の自動生成

テキストまたはCSVファイルを意味的なチャンクに分割するパイプライン。
非同期・並列処理により高速化。CSV出力時に改行を削除してクリーンなCSVを作成。

# ----------------------------------------------
# Step1: テキストファイル → チャンク分割、CSV
# ----------------------------------------------
uv run python -m chunking.csv_text_to_chunks_text_csv \
  --input-file OUTPUT/cc_news_2per_anthropic.csv \
  --output output_chunked \
  --model claude-haiku-4-5 \
  --workers 2

# ----------------------------------------------
# tep2: Q/A生成 + Qdrant登録
# ----------------------------------------------
# Worker起動
# ./start_celery.sh stop
# ./start_celery.sh status
# ./start_celery.sh restart -w 2 --flower

uv run python qa_qdrant/make_qa_register_qdrant.py \
  --input-file output_chunked/cc_news_2per_anthropic_chunks.csv \
  --collection cc_news_2per_anthropic \
  --model claude-haiku-4-5 \
  --concurrency 2 \
  --recreate


# 出力例:
# chunks_output/wikipedia_ja_5per_chunks.csv （メタデータ付き）
# chunks_output/wikipedia_ja_5per_chunks_simple.csv （シンプル版、Textのみ）

# ----------------------------------------------
# テキストファイル → チャンクCSV
python -m chunking.csv_text_to_chunks_text_csv.py \
  --input-file ./data/document.txt \
  --output chunks_output \
  --model claude-haiku-4-5 \
  --workers 8

# デフォルト出力ディレクトリ使用
python -m chunking.csv_text_to_chunks_text_csv.py \
  --input-file ./data/document.txt
  # → chunks_output/document_chunks.csv が生成される
  # → chunks_output/document_chunks_simple.csv （シンプル版）も同時生成
"""

import argparse
import asyncio
import logging
import re
from pathlib import Path
from typing import List, Optional

import pandas as pd
import tiktoken
from tqdm.asyncio import tqdm as async_tqdm

# 既存のインポート
from chunking.async_api_client import AsyncAPIClient
from chunking.checkpoint_manager import CheckpointManager
from chunking.models import ContinuityResult, StructuralResult
from chunking.prompts import (
    CONTINUITY_CHECK_PROMPT,
    PARAGRAPH_SEPARATION_PROMPT,
    SEMANTIC_CHUNKING_PROMPT,
)
from chunking.regex_string import chunk_text
from chunking.utils import format_size, setup_logging

logger = logging.getLogger(__name__)


# チャンクの最大トークン数（tiktoken cl100k_base 換算）。
# 最終チャンク全件に強制分割の上限として使う。Embedding
# （gemini-embedding-001）の入力上限 2048 トークンを超えると超過分が
# 無言で切り捨てられるため、上限は必ずそれ未満にすること。
MAX_CHUNK_TOKENS = 512

# Embedding モデル（gemini-embedding-001）の入力トークン上限。
EMBEDDING_INPUT_TOKEN_LIMIT = 2048

_TOKENIZER = None
_TOKENIZER_FAILED = False


def _count_tokens(text: str) -> int:
    """トークン数を数える。

    tiktoken の BPE 取得に失敗する環境（オフライン等）では
    文字数ベースの概算にフォールバックする。
    """
    global _TOKENIZER, _TOKENIZER_FAILED
    if _TOKENIZER is None and not _TOKENIZER_FAILED:
        try:
            _TOKENIZER = tiktoken.get_encoding("cl100k_base")
        except Exception as e:
            _TOKENIZER_FAILED = True
            logger.warning(f"tiktoken 初期化失敗、文字数ベースで概算します: {e}")
    if _TOKENIZER is not None:
        return len(_TOKENIZER.encode(text))
    # 概算: ASCII は約4文字≈1トークン、非ASCII（日本語等）は約1文字≈1トークン
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    return max(1, ascii_chars // 4 + (len(text) - ascii_chars))


# ================================================================
# ✅ 新規追加: テキスト正規化関数
# ================================================================

def _normalize_whitespace(text: str) -> str:
    """
    テキストの改行・空白を正規化

    - 改行(\n)を半角スペースに置換
    - 連続する空白を1つに正規化
    - 先頭・末尾の空白を削除

    Args:
        text: 正規化対象テキスト

    Returns:
        正規化されたテキスト

    Examples:
        _normalize_whitespace("行1\\n\\n行2")
        '行1 行2'
        _normalize_whitespace("  複数    空白  ")
        '複数 空白'
    """
    # 改行を半角スペースに置換
    text = text.replace('\n', ' ')
    text = text.replace('\r', ' ')

    # タブを半角スペースに置換
    text = text.replace('\t', ' ')

    # 連続する空白を1つに正規化
    text = re.sub(r'\s+', ' ', text)

    # 先頭・末尾の空白を削除
    text = text.strip()

    return text


# ================================================================
# ✅ 新規追加: Step1用 前処理・後処理関数（step1_2_3.pyより移植）
# ================================================================

def _preprocess_text(text: str) -> str:
    """
    テキストの前処理：長い1行を適切に分割する

    改行のない長いテキストを句読点（日本語: 。、英語: . ）で
    適切に分割し、LLMへの入力を整形する。

    Args:
        text: 前処理対象のテキスト

    Returns:
        前処理されたテキスト（句読点で改行区切り）
    """
    lines = text.split('\n')
    processed_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            processed_lines.append('')
            continue
        # chunk_text: 日本語・英語対応の文分割
        chunks = chunk_text(line, keep_delimiter=True)
        if len(chunks) > 1:
            processed_lines.extend(chunks)
        else:
            processed_lines.append(line)
    return '\n'.join(processed_lines)


def _postprocess_paragraph(paragraph: str) -> str:
    """
    段落の後処理：句読点で文を分割し、改行で区切る

    Step1の出力（段落）を後処理し、各文を改行で区切ることで、
    Step2・Step3での処理精度を向上させる。

    Args:
        paragraph: 後処理対象の段落テキスト

    Returns:
        後処理された段落テキスト（文ごとに改行区切り）
    """
    lines = paragraph.split('\n') if '\n' in paragraph else [paragraph]
    processed = []
    for line in lines:
        line = line.strip()
        if line:
            processed.extend(chunk_text(line, keep_delimiter=True))
    return '\n'.join(processed)


# ================================================================
# CSV読み込み機能
# ================================================================

def load_text_from_csv(
        csv_path: str,
        text_column: Optional[str] = None,
        max_rows: Optional[int] = None,
        combine_rows: bool = False
) -> str:
    """CSVファイルからテキストを読み込む"""
    logger.info("=" * 60)
    logger.info("CSV読み込み処理")
    logger.info("=" * 60)

    try:
        df = pd.read_csv(csv_path)
        logger.info(f"  📁 読み込み: {len(df)} 行")
    except Exception as e:
        logger.error(f"CSV読み込みエラー: {e}")
        raise

    if max_rows and len(df) > max_rows:
        df = df.head(max_rows)
        logger.info(f"  ✂️  制限: {len(df)} 行に制限")

    if text_column:
        if text_column not in df.columns:
            raise ValueError(
                f"指定されたカラム '{text_column}' が見つかりません。\n"
                f"利用可能なカラム: {list(df.columns)}"
            )
        col = text_column
    else:
        text_candidates = [
            'text', 'Text', 'TEXT',
            'content', 'Content', 'CONTENT',
            'Combined_Text', 'combined_text',
            'body', 'Body', 'BODY',
            'document', 'Document',
            'answer', 'Answer'
        ]

        col = None
        for candidate in text_candidates:
            if candidate in df.columns:
                col = candidate
                break

        if col is None:
            col = df.columns[0]
            logger.warning(
                f"テキストカラムを自動検出できませんでした。\n"
                f"  最初のカラム '{col}' を使用します。"
            )

    logger.info(f"  📝 テキストカラム: '{col}'")

    texts = df[col].fillna('').astype(str).tolist()
    texts = [t.strip() for t in texts if t.strip()]

    logger.info(f"  ✅ 抽出: {len(texts)} 件の非空テキスト")

    if combine_rows:
        combined_text = "\n\n".join(texts)
        logger.info(f"  🔗 結合モード: 全 {len(texts)} 行を1つのテキストに結合")
    else:
        combined_text = "\n\n".join(texts)
        logger.info(f"  📄 個別モード: {len(texts)} 個のテキストを改行区切りで処理")

    logger.info(f"  📊 総サイズ: {format_size(len(combined_text))}")
    return combined_text


# ================================================================
# ✅ 新規追加: シンプルCSV保存機能（Textカラムのみ）
# ================================================================

def save_chunks_as_simple_csv(
        chunks: List[str],
        output_file: str,
        normalize_whitespace: bool = True
) -> str:
    """
    チャンクをシンプルなCSV形式で保存（Textカラムのみ）

    Args:
        chunks: チャンクのリスト
        output_file: 出力ファイルパス
        normalize_whitespace: 改行・空白を正規化するか（デフォルト: True）

    Returns:
        保存したCSVファイルパス

    Example:
        >>> output_file = "chunks_output/wikipedia_ja_5per_chunks_simple.csv"
        >>> save_chunks_as_simple_csv(chunks, output_file)

        出力CSV:
        Text
        "チャンク1のテキスト..."
        "チャンク2のテキスト..."
    """
    data = []
    for ct in chunks:
        # 改行・空白を正規化
        if normalize_whitespace:
            chunk_text_cleaned = _normalize_whitespace(ct)
        else:
            chunk_text_cleaned = ct

        data.append({'Text': chunk_text_cleaned})

    df = pd.DataFrame(data)
    df.to_csv(output_file, index=False, encoding='utf-8')

    logger.info("")
    logger.info("=" * 60)
    logger.info("✅ シンプルCSV保存完了（Textカラムのみ）")
    logger.info("=" * 60)
    logger.info(f"  ファイル: {output_file}")
    logger.info(f"  チャンク数: {len(df)}")
    logger.info("  カラム: Text のみ")
    logger.info("=" * 60)

    return output_file


# ================================================================
# ✅ 改修: CSV保存機能（改行削除対応 + シンプルCSV追加出力）
# ================================================================

def save_chunks_as_csv(
        chunks: List[str],
        output_file: str,
        dataset_type: str = "custom",
        source_file: Optional[str] = None,
        normalize_whitespace: bool = True,
        save_simple_csv: bool = True
) -> str:
    """
    チャンクをCSV形式で保存（メタデータ付き + シンプルCSV）

    Args:
        chunks: チャンクのリスト
        output_file: 出力ファイルパス（メタデータ付きCSV）
        dataset_type: データセット種別
        source_file: 元ファイル名
        normalize_whitespace: 改行・空白を正規化するか（デフォルト: True）
        save_simple_csv: シンプルCSV（Textのみ）も保存するか（デフォルト: True）

    Returns:
        保存したCSVファイルパス（メタデータ付き）

    Note:
        save_simple_csv=True の場合、以下の2つのファイルが生成されます:
        1. {output_file}: メタデータ付きCSV（chunk_id, text, tokens, ...）
        2. {output_file_stem}_simple.csv: シンプルCSV（Text カラムのみ）

        例:
        - wikipedia_ja_5per_chunks.csv （メタデータ付き）
        - wikipedia_ja_5per_chunks_simple.csv （シンプル版）
    """
    tokenizer = tiktoken.get_encoding("cl100k_base")

    data = []
    for i, ct in enumerate(chunks):
        # ✅ 改行・空白を正規化（CSV出力をクリーンにする）
        if normalize_whitespace:
            chunk_text_cleaned = _normalize_whitespace(ct)
        else:
            chunk_text_cleaned = ct

        # センテンス分割（正規化前のテキストで実施）
        sentences = _split_sentences_simple(ct)

        data.append({
            'chunk_id'      : f"{dataset_type}_chunk_{i}",
            'text'          : chunk_text_cleaned,
            'tokens'        : len(tokenizer.encode(chunk_text_cleaned)),
            'chunk_idx'     : i,
            'dataset_type'  : dataset_type,
            'type'          : 'llm_chunk',
            'sentence_count': len(sentences),
            'source_file'   : source_file or ''
        })

    df = pd.DataFrame(data)
    df.to_csv(output_file, index=False, encoding='utf-8')

    logger.info("")
    logger.info("=" * 60)
    logger.info("✅ CSV保存完了（メタデータ付き）")
    logger.info("=" * 60)
    logger.info(f"  ファイル: {output_file}")
    logger.info(f"  チャンク数: {len(df)}")
    logger.info(f"  総トークン数: {df['tokens'].sum()}")
    logger.info(f"  平均トークン数: {df['tokens'].mean():.1f}")
    logger.info(f"  改行正規化: {'有効' if normalize_whitespace else '無効'}")
    logger.info("=" * 60)

    if save_simple_csv:
        output_path = Path(output_file)
        simple_csv_name = output_path.stem + "_simple.csv"
        simple_csv_path = output_path.parent / simple_csv_name

        save_chunks_as_simple_csv(
            chunks=chunks,
            output_file=str(simple_csv_path),
            normalize_whitespace=normalize_whitespace
        )

    return output_file


def save_chunks_as_text(chunks: List[str], output_file: str) -> str:
    """テキスト形式で保存（既存形式・後方互換性）"""
    with open(output_file, 'w', encoding='utf-8') as f:
        for chunk in chunks:
            f.write(chunk + '\n---\n')

    logger.info(f"テキストファイル保存: {output_file} ({len(chunks)}チャンク)")
    return output_file


# ================================================================
# 出力ファイル名自動生成機能
# ================================================================

def generate_output_filename(
        input_file: str,
        output_dir: str,
        dataset_type: str = "custom"
) -> str:
    """
    入力ファイル名から出力ファイル名を自動生成

    Args:
        input_file: 入力ファイルパス
        output_dir: 出力ディレクトリ
        dataset_type: データセット種別（未使用、互換性のため残存）

    Returns:
        出力ファイルの絶対パス

    Examples:
        generate_output_filename("data/input.txt", "chunks_output", "custom")
        'chunks_output/input_chunks.csv'

        generate_output_filename("data/cc_news.csv", "chunks_output", "cc_news")
        'chunks_output/cc_news_chunks.csv'
    """
    import os

    input_path = Path(input_file)
    base_name = input_path.stem

    output_filename = f"{base_name}_chunks.csv"

    os.makedirs(output_dir, exist_ok=True)

    output_path = os.path.join(output_dir, output_filename)
    return output_path


def _split_sentences_simple(text: str) -> List[str]:
    """簡易的な文分割（日本語対応）"""
    sentences = re.findall(r'[^。．.！？!?]+[。．.！？!?]\s*', text)

    if not sentences:
        sentences = [text.strip()] if text.strip() else []
    else:
        last_pos = text.rfind(sentences[-1]) + len(sentences[-1])
        if last_pos < len(text):
            remaining = text[last_pos:].strip()
            if remaining:
                sentences.append(remaining)

    return [s.strip() for s in sentences if s.strip()]


def _split_oversized_text(text: str, max_tokens: int) -> List[str]:
    """max_tokens を超えるテキストを文境界で複数ピースに分割する。

    1文単独で max_tokens を超える場合はその文をそのまま1ピースとする
    （文の途中では切らない。Embedding 側の切り捨ては警告で可視化）。
    """
    sentences = _split_sentences_simple(text)
    if not sentences:
        return [text]

    pieces: List[str] = []
    current: List[str] = []
    current_tokens = 0
    for sent in sentences:
        sent_tokens = _count_tokens(sent)
        if current and current_tokens + sent_tokens > max_tokens:
            pieces.append(" ".join(current))
            current, current_tokens = [], 0
        current.append(sent)
        current_tokens += sent_tokens
    if current:
        pieces.append(" ".join(current))
    return pieces


def _enforce_max_chunk_tokens(chunks: List[str], max_tokens: int) -> List[str]:
    """全チャンクに最大トークン数を強制する（超過分は文境界で分割）。

    Step3 は「結合時」のみ上限を見ており、Step2 が出力する単一チャンクや
    フォールバックで保全されたブロックには上限がなかった。Embedding
    （gemini-embedding-001, 入力上限 2048 トークン）の無言切り捨てを防ぐため、
    最終チャンク全件に対して上限を強制する。
    """
    enforced: List[str] = []
    split_count = 0
    for chunk in chunks:
        tokens = _count_tokens(chunk)
        if tokens <= max_tokens:
            enforced.append(chunk)
            continue
        pieces = _split_oversized_text(chunk, max_tokens)
        if len(pieces) == 1:
            logger.warning(
                f"  1文で {tokens} トークン（上限 {max_tokens} 超）のチャンクは分割不可のため保持。"
                f"Embedding 入力上限（{EMBEDDING_INPUT_TOKEN_LIMIT}）超過時は切り捨てに注意"
            )
            enforced.append(chunk)
            continue
        split_count += 1
        enforced.extend(pieces)

    if split_count:
        logger.info(
            f"  📏 上限強制分割: {split_count} チャンクが {max_tokens} トークン超のため"
            f"文境界で分割（{len(chunks)} → {len(enforced)} チャンク）"
        )
    return enforced


# ================================================================
# chunks_all_async関数
# ================================================================

async def chunks_all_async(
        text: str,
        model: str = "claude-haiku-4-5",
        max_workers: int = 8,
        block_size: int = 1000,
        checkpoint_manager: Optional[CheckpointManager] = None,
        output_file: Optional[str] = None,
        dataset_type: str = "custom",
        source_file: Optional[str] = None
) -> List[str]:
    """テキストを3段階で意味的にチャンク化"""
    import os

    # [MIGRATION gemini→anthropic] チャンク化の LLM は Anthropic Claude を使用する。
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEYが設定されていません")

    client = AsyncAPIClient(
        api_key=api_key,
        max_workers=max_workers,
        max_retries=3,
        max_output_tokens=16384
    )

    if checkpoint_manager is None:
        checkpoint_manager = CheckpointManager()

    logger.info("=" * 60)
    logger.info("チャンク化処理開始 (3段階)")
    logger.info("=" * 60)
    logger.info(f"入力テキスト: {format_size(len(text))}")
    logger.info(f"モデル: {model}")
    logger.info(f"並列ワーカー数: {max_workers}")

    step1_chunks = await _step1_hierarchical_split(
        text, client, model, block_size, checkpoint_manager
    )

    step2_chunks = await _step2_semantic_chunking(
        step1_chunks, client, model, checkpoint_manager
    )

    final_chunks = await _step3_continuity_check(
        step2_chunks, client, model, checkpoint_manager
    )

    # 最終チャンク全件に最大トークン上限を強制（Embedding の無言切り捨て防止）。
    # Step3 は結合時のみ上限を見るため、Step2 の単一チャンクやフォールバック保全分は
    # ここで初めて上限が掛かる。
    final_chunks = _enforce_max_chunk_tokens(final_chunks, MAX_CHUNK_TOKENS)

    if output_file:
        output_path = Path(output_file)

        if output_path.suffix.lower() == '.csv':
            save_chunks_as_csv(
                chunks=final_chunks,
                output_file=output_file,
                dataset_type=dataset_type,
                source_file=source_file,
                normalize_whitespace=True,
                save_simple_csv=True
            )
        else:
            save_chunks_as_text(
                chunks=final_chunks,
                output_file=output_file
            )

    return final_chunks


async def _step1_hierarchical_split(
        text: str,
        client: AsyncAPIClient,
        model: str,
        block_size: int,
        checkpoint_manager: CheckpointManager
) -> List[str]:
    """
    Step 1: 階層構造化（段落分割）

    テキストを段落単位に分割する。

    【step1_2_3.pyからの改善点】
    - 前処理: _preprocess_text() で句読点区切りに変換
    - 後処理: _postprocess_paragraph() で文ごとに改行区切り

    【分割基準】
    - 章・節の切り替わり → 新しい段落
    - トピック転換 → 新しい段落
    - 文脈の連続性 → 同じ段落

    Args:
        text: 分割対象テキスト
        client: 非同期APIクライアント
        model: 使用するLLMモデル名
        block_size: 入力テキストのブロックサイズ（文字数）
        checkpoint_manager: チェックポイント管理

    Returns:
        段落単位に分割されたテキストリスト
    """
    if checkpoint_manager.exists("step1"):
        logger.info("Step1: チェックポイントから再開")
        return checkpoint_manager.load("step1")

    logger.info("\n[Step 1/3] 階層構造化（段落分割）")
    logger.info(f"  入力: {format_size(len(text))}")

    text = _preprocess_text(text)

    blocks = [text[i:i + block_size] for i in range(0, len(text), block_size)]
    logger.info(f"  ブロック分割: {len(blocks)} ブロック（{block_size}文字ごと）")

    tasks = []
    for i, block in enumerate(blocks):
        prompt = f"{PARAGRAPH_SEPARATION_PROMPT}\n\n【入力テキスト】\n{block}"
        task = client.generate_content(
            model=model,
            contents=prompt,
            response_schema=StructuralResult,
            task_id=f"step1_block_{i}"
        )
        tasks.append(task)

    results = await async_tqdm.gather(
        *tasks,
        desc="Step1: 段落分割",
        total=len(tasks)
    )

    paragraphs = []
    for result_json in results:
        if result_json:
            try:
                result = StructuralResult.model_validate_json(result_json)
                for para in result.paragraphs:
                    para_text = _postprocess_paragraph(para.full_text)
                    paragraphs.append(para_text)
            except Exception as e:
                logger.warning(f"パース失敗: {e}")

    logger.info(f"  出力: {len(paragraphs)} 段落")
    checkpoint_manager.save("step1", paragraphs)

    return paragraphs


async def _step2_semantic_chunking(
        paragraphs: List[str],
        client: AsyncAPIClient,
        model: str,
        checkpoint_manager: CheckpointManager
) -> List[str]:
    """
    Step 2: 意味的チャンキング

    Step1の段落を意味的なチャンクに分割する。

    【step1_2_3.pyからの改善点】
    - Step1で生成された段落は既に後処理済み（句読点改行区切り）
    - LLMが「文のまとまり」を理解しやすくなっている

    【分割基準】
    - 意味の単位（例: 問題提起と解決策を1つのチャンクに）
    - Q/A生成に最適なサイズ（トークン数を考慮）
    - 独立して理解可能な情報の塊

    Args:
        paragraphs: 段落のリスト（Step1の出力）
        client: 非同期APIクライアント
        model: 使用するLLMモデル名
        checkpoint_manager: チェックポイント管理

    Returns:
        意味的に分割されたチャンクリスト
    """
    if checkpoint_manager.exists("step2"):
        logger.info("Step2: チェックポイントから再開")
        return checkpoint_manager.load("step2")

    logger.info("\n[Step 2/3] 意味的チャンキング")
    logger.info(f"  入力: {len(paragraphs)} 段落")

    tasks = []
    for i, para in enumerate(paragraphs):
        prompt = f"{SEMANTIC_CHUNKING_PROMPT}\n\n【入力テキスト】\n{para}"
        task = client.generate_content(
            model=model,
            contents=prompt,
            response_schema=StructuralResult,
            task_id=f"step2_para_{i}"
        )
        tasks.append(task)

    results = await async_tqdm.gather(
        *tasks,
        desc="Step2: 意味的分割",
        total=len(tasks)
    )

    chunks = []
    for result_json in results:
        if result_json:
            try:
                result = StructuralResult.model_validate_json(result_json)
                for para in result.paragraphs:
                    chunks.append(para.full_text)
            except Exception as e:
                logger.warning(f"パース失敗: {e}")

    logger.info(f"  出力: {len(chunks)} チャンク")
    checkpoint_manager.save("step2", chunks)

    return chunks


async def _step3_continuity_check(
        chunks: List[str],
        client: AsyncAPIClient,
        model: str,
        checkpoint_manager: CheckpointManager
) -> List[str]:
    """
    Step 3: 文脈連続性チェック

    隣接するチャンク間の文脈連続性を判定し、
    連続している場合は結合、非連続の場合は分離する。

    【Step2との違い】
    - Step2: 分割（1段落→複数チャンク、チャンク数が増加）
    - Step3: 結合（複数チャンク→少数チャンク、チャンク数が減少）
    - Step3はStep2の「過分割」を修正する役割

    【検証パターン】
    - 前方依存: 「この」「それ」等の指示語で前を参照 → 結合（True）
    - 後方依存: 専門用語が未定義のまま使用 → 結合（True）
    - 話題転換: 完全に別のトピック → 分離（False）
    - 独立判定: 話題は同じでも単独で理解可能 → 分離（False）
    - 章構造: 章が変わった場合 → 分離（False）

    Args:
        chunks: チャンクのリスト（Step2の出力）
        client: 非同期APIクライアント
        model: 使用するLLMモデル名
        checkpoint_manager: チェックポイント管理

    Returns:
        連続性に基づいて結合/分離された最終チャンクリスト
    """
    if checkpoint_manager.exists("step3"):
        logger.info("Step3: チェックポイントから再開")
        return checkpoint_manager.load("step3")

    logger.info("\n[Step 3/3] 文脈連続性チェック")
    logger.info(f"  入力: {len(chunks)} チャンク")

    if len(chunks) <= 1:
        checkpoint_manager.save("step3", chunks)
        return chunks

    tasks = []
    for i in range(len(chunks) - 1):
        prompt = f"{CONTINUITY_CHECK_PROMPT}\n\n【前のテキスト】\n{chunks[i]}\n\n【次のテキスト】\n{chunks[i + 1]}"
        task = client.generate_content(
            model=model,
            contents=prompt,
            response_schema=ContinuityResult,
            task_id=f"step3_pair_{i}"
        )
        tasks.append(task)

    results = await async_tqdm.gather(
        *tasks,
        desc="Step3: 連続性チェック",
        total=len(tasks)
    )

    logger.debug("マージ処理...")
    final_chunks = [chunks[0]]
    for i, result_json in enumerate(results):
        if result_json:
            try:
                result = ContinuityResult.model_validate_json(result_json)
                if result.is_connected:
                    final_chunks[-1] += "\n\n" + chunks[i + 1]
                    logger.debug(f"  チャンク{i + 1} + チャンク{i + 2} → 結合")
                else:
                    final_chunks.append(chunks[i + 1])
                    logger.debug(f"  チャンク{i + 2} → 新規追加")
            except Exception as e:
                logger.warning(f"パース失敗: {e}")
                final_chunks.append(chunks[i + 1])
        else:
            final_chunks.append(chunks[i + 1])

    logger.info(f"  出力: {len(final_chunks)} チャンク（マージ後）")
    checkpoint_manager.save("step3", final_chunks)

    return final_chunks


# ================================================================
# メイン関数
# ================================================================

async def main():
    parser = argparse.ArgumentParser(
        description="LLMベースセマンティックチャンキング（統一版 - make_qa形式互換）"
    )

    parser.add_argument(
        "--input-file",
        type=str,
        required=True,
        help="入力ファイル (.txt, .csv)"
    )

    parser.add_argument(
        "--output",
        type=str,
        default="chunks_output",
        help="出力ディレクトリ（デフォルト: chunks_output）"
    )

    parser.add_argument(
        "--model",
        type=str,
        default="claude-haiku-4-5",
        help="使用するLLMモデル"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="並列ワーカー数"
    )
    parser.add_argument(
        "--block-size",
        type=int,
        default=1000,
        help="ブロックサイズ（文字数）。大きすぎるとMAX_TOKENSエラーが発生"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="詳細ログ出力"
    )

    parser.add_argument(
        "--resume",
        type=str,
        default=None,
        help="再開するジョブID"
    )
    parser.add_argument(
        "--text-column",
        type=str,
        default=None,
        help="CSVのテキストカラム名"
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=None,
        help="最大処理行数（CSV用）"
    )
    parser.add_argument(
        "--combine-rows",
        action="store_true",
        help="CSV全行を結合"
    )

    args = parser.parse_args()

    setup_logging(verbose=args.verbose)

    input_path = Path(args.input_file)
    if not input_path.exists():
        logger.error(f"入力ファイルが見つかりません: {args.input_file}")
        return

    file_extension = input_path.suffix.lower()

    text = ""

    if file_extension == '.csv':
        text = load_text_from_csv(
            csv_path=args.input_file,
            text_column=args.text_column,
            max_rows=args.max_rows,
            combine_rows=args.combine_rows
        )
    else:
        with open(args.input_file, 'r', encoding='utf-8') as f:
            text = f.read()

    logger.info("")
    logger.info("=" * 60)
    logger.info("チャンキング処理開始")
    logger.info("=" * 60)
    logger.info(f"📁 入力ファイル: {args.input_file}")
    logger.info(f"📊 テキストサイズ: {format_size(len(text))}")
    logger.info(f"🤖 モデル: {args.model}")
    logger.info(f"👥 並列ワーカー数: {args.workers}")
    logger.info("=" * 60)

    dataset_type = input_path.stem
    output_file = generate_output_filename(
        args.input_file,
        args.output,
        dataset_type
    )

    logger.info(f"📝 出力ファイル: {output_file}")
    logger.info("=" * 60)
    logger.info("")

    checkpoint_manager = CheckpointManager(job_id=args.resume) if args.resume else CheckpointManager()

    final_chunks = await chunks_all_async(
        text=text,
        model=args.model,
        max_workers=args.workers,
        block_size=args.block_size,
        checkpoint_manager=checkpoint_manager,
        output_file=output_file,
        dataset_type=dataset_type,
        source_file=input_path.name
    )

    logger.info("")
    logger.info("=" * 60)
    logger.info("✅ チャンク作成完了")
    logger.info("=" * 60)
    logger.info(f"📊 生成チャンク数: {len(final_chunks)}")
    logger.info(f"📁 出力ファイル: {output_file}")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())

"""
Sparse Embedding (SPLADE) 実装モジュール

QdrantのHybrid Search用に、Sparse Vector (キーワード重み付きベクトル) を生成します。
FastEmbedライブラリを使用し、ローカルCPUで高速に動作します。

依存:
    pip install fastembed

使用モデル:
    デフォルト: "prithivida/Splade_PP_en_v1" (英語向け)
    ※ 日本語等の多言語対応が必要な場合は、Qdrant推奨の多言語モデルを検討
"""

import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

try:
    from fastembed import SparseTextEmbedding
except ImportError:
    SparseTextEmbedding = None
    # FastEmbed は sparse/hybrid 検索でのみ使う optional 依存。import しただけの
    # 時点では未使用かもしれないため error で叫ばない（実際に使うと __init__ で
    # ImportError を送出する）。
    logger.debug("FastEmbed is not installed; sparse embedding is unavailable until `pip install fastembed`.")

# Sparse Embeddingのデフォルトモデル
DEFAULT_SPARSE_MODEL = "prithivida/Splade_PP_en_v1"


def resolve_cache_dir(cache_dir: str = None) -> Path:
    """FastEmbed が実際に使うキャッシュディレクトリを再現する。

    FastEmbed 側の解決順（`cache_dir` 引数 → `FASTEMBED_CACHE_PATH` →
    `<tempdir>/fastembed_cache`）に合わせる。**ログに出すためだけ**に使う。

    ⚠️ ここを出す理由: 初期化失敗の実測原因は「前回のダウンロードが途中で
    切れて壊れたキャッシュ」だった。エラー本文
    （`Local file sizes do not match the metadata`）だけでは、どこを消せば
    直るのかが利用者に分からない。macOS では tempdir が
    `/var/folders/8b/..../T/` のような推測不能なパスになるため、
    **実際のパスを出さないと自力で復旧できない。**
    """
    if cache_dir:
        return Path(cache_dir)
    env_dir = os.getenv("FASTEMBED_CACHE_PATH")
    if env_dir:
        return Path(env_dir)
    return Path(tempfile.gettempdir()) / "fastembed_cache"

class SparseEmbeddingClient:
    """Sparse Embedding生成クライアント"""

    def __init__(
        self,
        model_name: str = DEFAULT_SPARSE_MODEL,
        threads: int = None,
        cache_dir: str = None
    ):
        if SparseTextEmbedding is None:
            raise ImportError("FastEmbed library is missing.")
        
        # Handle explicit None
        if model_name is None:
            model_name = DEFAULT_SPARSE_MODEL
        
        logger.info(f"Initializing SparseEmbedding with model: {model_name}")
        self.model_name = model_name
        self._model = SparseTextEmbedding(
            model_name=model_name,
            threads=threads,
            cache_dir=cache_dir
        )

    def embed_text(self, text: str) -> Dict[int, float]:
        """
        単一テキストのSparse Embedding生成
        
        Returns:
            {index: weight, ...} 形式の辞書 (QdrantのSparseVector形式に対応可能)
        """
        # embedメソッドはジェネレータを返す
        # 戻り値は SparseEmbedding オブジェクト (indices, values)
        sparse_vectors = list(self._model.embed([text]))
        vec = sparse_vectors[0]
        
        # Qdrant用に {index: value} の辞書形式、または (indices, values) のタプルで管理
        # ここでは処理しやすいように辞書で返すことも可能だが、
        # Qdrant Clientへの渡しやすさを考慮して raw object または indices/values を返す
        return self._format_output(vec)

    def embed_texts(
        self,
        texts: List[str],
        batch_size: int = 32,
        progress_callback: Any = None
    ) -> List[Dict[str, List[Any]]]:
        """
        バッチSparse Embedding生成
        
        Args:
            texts: テキストリスト
            batch_size: バッチサイズ
            progress_callback: 進捗コールバック関数 (current, total) -> None
        
        Returns:
            [{"indices": [...], "values": [...]}, ...] のリスト
        """
        try:
            from tqdm import tqdm
            use_tqdm = True
        except ImportError:
            use_tqdm = False

        results = []
        total = len(texts)
        
        # progress_callbackが指定されている場合はそちらを優先
        if progress_callback:
            logger.info(f"Starting sparse embedding generation with callback (total={total}, batch_size={batch_size})")
            # ジェネレータではなく手動で回してコールバックを呼び出す
            for i in range(0, total, batch_size):
                batch_texts = texts[i : i + batch_size]
                # FastEmbedのembedメソッドはジェネレータを返すが、ここではリスト化して処理
                batch_results = list(self._model.embed(batch_texts, batch_size=batch_size))
                for vec in batch_results:
                    results.append(self._format_output(vec))
                
                # 進捗更新
                current = min(i + batch_size, total)
                progress_callback(current, total)
                
        else:
            # 従来通りtqdmを使用
            generator = self._model.embed(texts, batch_size=batch_size)
            if use_tqdm:
                generator = tqdm(generator, total=total, desc="Sparse Embedding", unit="docs")
                
            for vec in generator:
                results.append(self._format_output(vec))
                
        return results

    def _format_output(self, sparse_vec) -> Dict[str, List[Any]]:
        """FastEmbedの出力をQdrantが受け入れやすい形式に変換"""
        # sparse_vec は indices と values を持つ
        return {
            "indices": sparse_vec.indices.tolist(),
            "values": sparse_vec.values.tolist()
        }

# シングルトン的な利用のためのファクトリ
_sparse_client_instance = None
# ⚠️ **失敗も覚える（negative cache）。**
#
# 以前は成功だけをキャッシュしていたため、`SparseEmbeddingClient.__init__` が
# 例外を投げると `_sparse_client_instance` は None のままだった。呼び出し側
# （agent_tools.py）はこの例外を `logger.debug` で握り潰すので、
#
#   検索のたびにモデル構築を試す → HuggingFace へ再ダウンロード →
#   「Local file sizes do not match the metadata」警告 → 失敗 → debug で沈黙
#
# を コレクション数 × リプラン回数 ぶん繰り返していた（実測ログで大量に出た
# fastembed 警告の正体）。しかも原因は debug ログなので誰にも見えない。
#
# 一度失敗したモデルは同じプロセス内で再試行しても結果は変わらないため、
# 失敗を記録して即座に同じ例外を送出する。sparse が使えない環境では
# 呼び出し側が dense 検索へ倒れるだけで、機能上の劣化は無い。
_sparse_client_failures: Dict[str, Exception] = {}


def get_sparse_embedding_client(model_name: str = DEFAULT_SPARSE_MODEL) -> SparseEmbeddingClient:
    # Handle explicit None passed from callers
    if model_name is None:
        model_name = DEFAULT_SPARSE_MODEL

    global _sparse_client_instance

    cached_failure = _sparse_client_failures.get(model_name)
    if cached_failure is not None:
        # 再ダウンロードを試さずに即座に返す（初回に warning 済み）
        raise cached_failure

    if _sparse_client_instance is not None and _sparse_client_instance.model_name == model_name:
        return _sparse_client_instance

    try:
        _sparse_client_instance = SparseEmbeddingClient(model_name=model_name)
    except Exception as e:
        _sparse_client_failures[model_name] = e
        # 初回だけ warning で実際の原因を出す。以降は上の negative cache が
        # 黙って同じ例外を返すため、ログが埋まることはない。
        logger.warning(
            f"Sparse Embedding の初期化に失敗しました（model={model_name}）: {e}\n"
            "  → 以降このプロセスでは sparse を試行せず dense 検索のみで動作します"
            "（検索は継続します）。\n"
            f"  → キャッシュ破損が原因の場合は次を削除して再実行してください: "
            f"rm -rf {resolve_cache_dir()}"
        )
        raise
    return _sparse_client_instance


def reset_sparse_embedding_client_cache() -> None:
    """成功・失敗の両キャッシュを捨てる（テストと明示的な再試行用）。"""
    global _sparse_client_instance
    _sparse_client_instance = None
    _sparse_client_failures.clear()

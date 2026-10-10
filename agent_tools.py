# agent_tools.py
"""
Rankの無効化：
1. [UI/Agent] RAGSearchTool.execute (GRACEエージェント)
   * ↘ 呼び出し: search_rag_knowledge_base_structured
       * ↘ [直接実行]: rerank_results (Cohere API使用)
2. 無効化 Code
    reranked_results = rerank_results(query, candidates, top_k=AgentConfig.RAG_SEARCH_LIMIT)
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

from qdrant_client import QdrantClient

from config import AgentConfig, CohereConfig
from qdrant_client_wrapper import (
    embed_query,
    embed_sparse_query_unified,
    get_qdrant_client,
    search_collection,
)

try:
    import cohere
except ImportError:
    cohere = None

logger = logging.getLogger(__name__)  # Configure logger for this module

# Initialize Client（シングルトン: Phase 2 STEP 4 改善）
client: QdrantClient = get_qdrant_client()


# ============ コサイン類似度閾値（二段構え） ============
# 一次閾値: 高精度を維持する既定値（Cohere Rerank 廃止 → コサイン類似度で直接フィルタ）。
COSINE_SIMILARITY_THRESHOLD: float = 0.7
# 二次閾値: 一次で出典が不足したときのみ適用する緩和値。
# 一次だけだと候補 20 件中 1 件しか残らないことがあり、後段の信頼度評価が
# 「単一ソースで検証できない」と減点して実質的な回答の信頼度を下げてしまう
# （docs/performance_levers.md P-04 の実測）。高スコアのケースは一次で完結するため
# 既存の挙動は変わらず、出典不足のケースだけを救う。
COSINE_SIMILARITY_THRESHOLD_RELAXED: float = 0.5
# 一次の結果がこの件数未満のときだけ緩和する（＝0 件・1 件が対象）。
MIN_RESULTS_BEFORE_RELAX: int = 2


def select_by_similarity(
        candidates: List[Dict[str, Any]],
        limit: int,
        threshold: float = COSINE_SIMILARITY_THRESHOLD,
        relaxed_threshold: float = COSINE_SIMILARITY_THRESHOLD_RELAXED,
        min_results: int = MIN_RESULTS_BEFORE_RELAX,
) -> Tuple[List[Dict[str, Any]], float]:
    """コサイン類似度による二段構えの選抜（純関数・副作用なし）。

    一次閾値で選抜し、件数が `min_results` 未満のときに限り緩和閾値で再選抜する。
    緩和しても件数が増えない場合は一次の結果を返す（無意味な緩和を避ける）。

    Args:
        candidates: 検索候補（`score` を持つ dict のリスト）
        limit: 最終的に残す件数
        threshold: 一次閾値
        relaxed_threshold: 二次（緩和）閾値
        min_results: この件数未満なら緩和する

    Returns:
        (選抜結果（score 降順・最大 limit 件）, 実際に採用した閾値)
    """
    def _pick(th: float) -> List[Dict[str, Any]]:
        picked = [r for r in candidates if r.get("score", 0.0) >= th]
        picked.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        return picked[:limit]

    primary = _pick(threshold)

    # 緩和が不要、または設定が無意味（緩和値が一次以上）なら一次で確定
    if len(primary) >= min_results or relaxed_threshold >= threshold:
        return primary, threshold

    relaxed = _pick(relaxed_threshold)
    if len(relaxed) > len(primary):
        return relaxed, relaxed_threshold
    return primary, threshold


# ============ コレクション一覧キャッシュ（Phase 3 STEP 6 改善）============
_collections_cache: Optional[List[str]] = None
_collections_cache_time: float = 0.0
_COLLECTIONS_CACHE_TTL: float = 60.0  # 60秒


def is_qdrant_connection_error(exc: BaseException) -> bool:
    """例外が「Qdrant に繋がらない」由来かを判定する。

    Qdrant が停止しているとき、`qdrant-client` は httpx の `ConnectError` を
    `ResponseHandlingException` に包んで送出する。型名は client のバージョンで
    変わりうるため、**例外連鎖をたどって文字列でも判定**する。

    ⚠️ これは「コレクションが 0 件」とは**別物**として扱うために要る。
       同一視すると、Qdrant 停止時に「登録済みコレクションが無い」と誤解して
       検索スコープの制限を外し、無関係なコレクションを走査してしまう
       （実測 2026-09-02: 停止中の Qdrant に対し 33 回のスタックトレースを
       出しつつ wikipedia_ja 等を検索し続けた）。
    """
    seen: set = set()
    cur: Optional[BaseException] = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        name = type(cur).__name__
        if name in ("ConnectError", "ConnectTimeout", "ResponseHandlingException"):
            return True
        text = str(cur)
        if "Connection refused" in text or "Errno 61" in text or "Errno 111" in text:
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def get_existing_collections_cached() -> List[str]:
    """
    コレクション一覧をキャッシュ付きで取得

    TTL（60秒）以内は前回結果を返す。
    並列検索時に N 回呼ばれても API は 1 回で済む。

    ⚠️ 接続できないときは `QdrantConnectionError` を送出する。空リストを返すと
       呼び出し側が「コレクションが 1 つも無い」と解釈してしまうため。
    """
    global _collections_cache, _collections_cache_time
    now = time.time()
    if _collections_cache is None or (now - _collections_cache_time) > _COLLECTIONS_CACHE_TTL:
        try:
            fetched = [c.name for c in client.get_collections().collections]
        except Exception as e:
            if is_qdrant_connection_error(e):
                raise QdrantConnectionError(
                    "Qdrant に接続できません。"
                    "`docker-compose -f docker-compose/docker-compose.yml up -d` で起動してください"
                ) from e
            raise
        _collections_cache = fetched
        _collections_cache_time = now
        logger.debug(f"コレクション一覧キャッシュ更新: {len(_collections_cache)}件")
    return _collections_cache


# ============ カスタム例外 ============
class RAGToolError(Exception):
    """RAGツール固有のエラー基底クラス"""
    pass


class QdrantConnectionError(RAGToolError):
    """Qdrant接続エラー"""
    pass


class CollectionNotFoundError(RAGToolError):
    """コレクション未存在エラー"""
    pass


class EmbeddingError(RAGToolError):
    """埋め込み生成エラー"""
    pass


# ============ 評価用メトリクス ============
@dataclass
class SearchMetrics:
    """検索結果のメトリクス（評価用）"""
    query: str
    collection_name: str
    latency_ms: float
    total_results: int
    filtered_results: int
    top_score: float
    scores: List[float] = field(default_factory=list)
    error: Optional[str] = None
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))


# Global metrics log (in-memory for evaluation session)
_search_metrics_log: List[SearchMetrics] = []


def get_search_metrics() -> List[SearchMetrics]:
    """評価用: 収集したメトリクスを取得"""
    return _search_metrics_log.copy()


def clear_search_metrics() -> None:
    """評価用: メトリクスをクリア"""
    _search_metrics_log.clear()


def export_metrics_to_dict() -> List[Dict[str, Any]]:
    """メトリクスを辞書形式でエクスポート"""
    from dataclasses import asdict
    return [asdict(m) for m in _search_metrics_log]


# ============ ヘルスチェック ============
def check_qdrant_health() -> bool:
    """Qdrantサーバーの接続確認"""
    try:
        client.get_collections()
        logger.info("Qdrant health check: OK")
        return True
    except Exception as e:
        logger.error(f"Qdrant health check failed: {e}")
        return False


# ============ ツール関数 ============
def filter_results_by_keywords(results: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
    """
    検索結果をクエリのキーワードでフィルタリングする（共通ロジック）
    Legacy Agentと同じく、スペース区切りのトークンを必須キーワードとして扱う。
    """

    # 必須キーワードの抽出（Legacyと同一ロジック: スペース区切り）
    tokens = query.split()
    required_keywords = []

    for t in tokens:
        # 2文字以上で、かつ記号のみでないものを採用
        if len(t) >= 2:
            required_keywords.append(t)

    required_keywords = list(set(required_keywords))
    logger.info(f"Filtering Logic - Required keywords: {required_keywords}")

    filtered_results = []
    for res in results:
        payload = res.get("payload", {})
        content = (str(payload.get("question", "")) + " " +
                   str(payload.get("answer", "")) + " " +
                   str(payload.get("content", "")))

        is_relevant = True
        if required_keywords:
            # キーワードが1つでも含まれていればOKとする（緩やかなAND条件）
            # Legacy Agentでは「キーワードを含めてください」と指示しているため、
            # 検索結果にそれらが含まれることを期待するが、
            # 全てが含まれるとは限らないため、ヒット数で判定。
            hit_count = sum(1 for k in required_keywords if k in content)

            # 1つもヒットしない場合は除外
            if hit_count == 0:
                is_relevant = False
                logger.debug(f"Keyword miss (score={res.get('score', 0):.3f}): Filtering out.")

        if is_relevant:
            filtered_results.append(res)

    return filtered_results


def rerank_results(
        query: str,
        results: List[Dict[str, Any]],
        top_k: int = 3,
        threshold: float = 0.5
) -> List[Dict[str, Any]]:
    """
    検索結果をCohere Rerank APIで再評価し、スコアを更新してソートする。
    Args:
        query: ユーザーの検索クエリ
        results: Qdrantからの検索結果リスト
        top_k: 最終的に残す件数
        threshold: スコアの足切りライン（Cohere APIがない場合は無視される）
    Returns:
        再ランク付けされた結果リスト
    """
    if not results:
        return []

    # Cohere APIキーがない場合、RRFスコアのままで結果を返す（threshold判定なし）
    if not CohereConfig.API_KEY or cohere is None:
        logger.info("Cohere APIキーがないため、RRFスコアのまま結果を返します（threshold判定なし）")
        # スコア順にソート（RRFスコア）
        sorted_results = sorted(results, key=lambda x: x.get("score", 0.0), reverse=True)
        return sorted_results[:top_k]

    try:
        co = cohere.Client(api_key=CohereConfig.API_KEY)

        # ドキュメントのテキストリストを作成
        documents = []
        for res in results:
            payload = res.get("payload", {})
            # QuestionとAnswerを組み合わせて文脈を作る
            doc_text = f"Question: {payload.get('question', '')}\nAnswer: {payload.get('answer', '')}"
            documents.append(doc_text)

        # Rerank実行
        rerank_response = co.rerank(
            model=CohereConfig.RERANK_MODEL,
            query=query,
            documents=documents,
            top_n=len(documents)
        )

        # スコアを更新
        reranked_results = []
        for r in rerank_response.results:
            # 元の結果を取得 (indexで対応)
            original_result = results[r.index]
            new_score = r.relevance_score

            # スコアを更新した新しい辞書を作成
            new_result = original_result.copy()
            # 元のQdrantスコアを保持
            new_result["original_score"] = original_result.get("score", 0.0)
            # CohereのRe-rankingスコアを設定
            new_result["rerank_score"] = new_score
            new_result["score"] = new_score  # 互換性のため

            # 閾値判定
            if new_score >= threshold:
                reranked_results.append(new_result)

        # スコア順はCohereが保証しているはずだが、念のためソート
        reranked_results.sort(key=lambda x: x["score"], reverse=True)

        logger.info(
            f"Re-ranking completed: {len(results)} -> {len(reranked_results)} results (Top score: {reranked_results[0]['score'] if reranked_results else 0.0:.4f})")

        return reranked_results[:top_k]

    except Exception as e:
        logger.error(f"Re-ranking failed: {e}")
        # 失敗時は元の結果をスコア順で返す（threshold判定なし）
        sorted_results = sorted(results, key=lambda x: x.get("score", 0.0), reverse=True)
        return sorted_results[:top_k]


# ★変更: use_hybrid_search パラメータを追加
def search_rag_knowledge_base_structured(
        query: str,
        collection_name: Optional[str] = None,
        use_hybrid_search: bool = True,  # ★追加
        precomputed_query_vector: Optional[List[float]] = None,      # Phase 3 STEP 7 改善
        precomputed_sparse_vector: Optional[Any] = None              # Phase 3 STEP 7 改善
) -> Union[List[Dict[str, Any]], str]:
    """
    Qdrantデータベースから専門的な知識を検索します（構造化データ版）。

    Args:
        query: 検索クエリ
        collection_name: 検索対象のコレクション名（省略時はデフォルト）
        use_hybrid_search: ハイブリッド検索（Sparse + Dense）を使用するか（デフォルト: True）
        precomputed_query_vector: 事前計算済みDenseベクトル（Noneの場合は内部で生成）
        precomputed_sparse_vector: 事前計算済みSparseベクトル（Noneの場合は内部で生成）
    """
    if collection_name is None:
        collection_name = AgentConfig.RAG_DEFAULT_COLLECTION

    start_time: float = time.time()
    # ★変更: ログにハイブリッド検索の状態を追加
    hybrid_status = "有効" if use_hybrid_search else "無効"
    logger.info(
        f"ツールアクション(Structured): RAG検索を実行: query='{query}', collection='{collection_name}', hybrid={hybrid_status}")

    metrics: SearchMetrics = SearchMetrics(
        query=query,
        collection_name=collection_name,
        latency_ms=0.0,
        total_results=0,
        filtered_results=0,
        top_score=0.0
    )

    try:
        # Phase 3 STEP 6 改善: ヘルスチェック削除 + コレクション一覧キャッシュ化
        existing_collections: List[str] = get_existing_collections_cached()
        if collection_name not in existing_collections:
            error_msg: str = f"コレクション '{collection_name}' はQdrantサーバーに存在しません。"
            logger.warning(error_msg)
            raise CollectionNotFoundError(error_msg)

        # Phase 3 STEP 7 改善: 事前計算ベクトルがあればそれを使用
        if precomputed_query_vector is not None:
            query_vector = precomputed_query_vector
            logger.debug(f"事前計算済みDenseベクトルを使用: {collection_name}")
        else:
            query_vector: List[float] = embed_query(query)
        if query_vector is None:
            raise EmbeddingError("クエリの埋め込み生成に失敗しました。")

        # ★変更: use_hybrid_search フラグに基づいてスパースベクトルを生成
        sparse_vector = None
        if use_hybrid_search:
            if precomputed_sparse_vector is not None:
                sparse_vector = precomputed_sparse_vector
                logger.debug(f"事前計算済みSparseベクトルを使用: {collection_name}")
            else:
                try:
                    sparse_vector = embed_sparse_query_unified(query)
                    logger.debug(f"スパースベクトル取得成功: {collection_name}")
                except Exception as e:
                    logger.debug(f"スパースベクトル取得スキップ ({collection_name}): {e}")
        else:
            logger.debug(f"ハイブリッド検索無効: スパースベクトルをスキップ ({collection_name})")

        # 1. Retrieval (Broad Search)
        # Re-rankingの効果を高めるため、最終的に欲しい数より多く取得する
        # Phase 3 STEP 8 改善: Sparseフォールバックは search_collection() に一元化
        # search_collection() 内で Hybrid → Dense → 最終フォールバック の3段階を処理
        candidates: List[Dict[str, Any]] = search_collection(
            client=client,
            collection_name=collection_name,
            query_vector=query_vector,
            sparse_vector=sparse_vector,
            limit=20  # 候補を広げる
        )

        metrics.total_results = len(candidates) if candidates else 0

        if not candidates:
            metrics.latency_ms = (time.time() - start_time) * 1000.0
            _search_metrics_log.append(metrics)
            return f"[[NO_RAG_RESULT]] 検索結果が見つかりませんでした。コレクション: '{collection_name}'."

        # 2. コサイン類似度閾値フィルタ（Cohere Rerank 廃止・二段構え）
        #    一次 0.7 で足り、不足時のみ 0.5 へ緩和して出典数を確保する。
        filtered_results, used_threshold = select_by_similarity(
            candidates, AgentConfig.RAG_SEARCH_LIMIT
        )
        if used_threshold != COSINE_SIMILARITY_THRESHOLD:
            logger.info(
                f"コサイン類似度フィルタ: 一次閾値 {COSINE_SIMILARITY_THRESHOLD} では出典不足のため "
                f"緩和閾値 {used_threshold:.4f} で再選抜 → {len(filtered_results)}件"
            )

        # 3. Metrics & Return
        scores: List[float] = [res.get("score", 0.0) for res in filtered_results]
        metrics.scores = scores
        metrics.top_score = max(scores) if scores else 0.0
        metrics.filtered_results = len(filtered_results)

        metrics.latency_ms = (time.time() - start_time) * 1000.0
        _search_metrics_log.append(metrics)

        if not filtered_results:
            all_scores = [r.get('score', 0.0) for r in candidates]
            max_score = max(all_scores) if all_scores else 0.0
            return (
                f"[[NO_RAG_RESULT_LOW_SCORE]] スコア閾値未満の結果のみでした。"
                f"最高スコア: {max_score:.2f} (閾値: {used_threshold:.4f})"
            )

        logger.info(
            f"コサイン類似度フィルタ: {len(candidates)} -> {len(filtered_results)}件 "
            f"(Top: {filtered_results[0]['score']:.4f}, 閾値: {used_threshold:.4f})"
        )

        return filtered_results

    except QdrantConnectionError as e:
        # 接続不能は原因が自明なので 1 行だけ出す。毎コレクション分の
        # スタックトレースでログを埋めない（実測で 1 実行 33 回出ていた）。
        logger.error(f"RAGツールエラー: {e}")
        return f"[[RAG_TOOL_ERROR]] エラーが発生しました: {str(e)}"
    except Exception as e:
        if is_qdrant_connection_error(e):
            logger.error(
                "RAGツールエラー: Qdrant に接続できません。"
                "`docker-compose -f docker-compose/docker-compose.yml up -d` で起動してください"
            )
            return "[[RAG_TOOL_ERROR]] エラーが発生しました: Qdrant に接続できません"
        logger.error(f"RAGツールエラー: {e}", exc_info=True)
        return f"[[RAG_TOOL_ERROR]] エラーが発生しました: {str(e)}"


# ★変更: use_hybrid_search パラメータを追加
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
services - ビジネスロジック分離モジュール
==========================================
agent_rag.pyから分離したビジネスロジック

モジュール構成:
- config_service.py: 設定管理（YAML、環境変数）
- cache_service.py: メモリキャッシュ（TTL対応）
- json_service.py: JSON処理（シリアライズ、ファイルI/O）
- token_service.py: トークン管理（カウント、コスト推定）
- qdrant_service.py: Qdrant操作（CRUD、ヘルスチェック）
- qa_service.py: Q/A生成（ローカル LLM / Ollama）

⚠️ 2026-09-20 に dataset_service.py / file_service.py を削除した。
   Streamlit 版アプリ（ui/）の時代の名残で、本モジュールの再エクスポート以外に
   **呼び出し元が 1 件も無かった**（リポジトリ全体を grep して確認）。
   データセット読み込みの現役経路は `qa_generation/data_io.py`
   （`load_uploaded_file` はそちらが自前で定義している）と
   `services/data_pipeline_service.py`。
   実装・テスト・ドキュメントは git 履歴に残る。
"""

from services.cache_service import (
    MemoryCache,
    cache,
    cache_result,
    get_global_cache,
    init_cache_from_config,
)
from services.config_service import (
    ConfigManager,
    config,
    get_config,
    logger,
    reload_config,
    set_config,
)
from services.json_service import (
    compact_json,
    is_valid_json,
    load_json_file,
    load_json_file_or_default,
    merge_json_files,
    pretty_print_json,
    safe_json_dumps,
    safe_json_loads,
    safe_json_serializer,
    save_json_file,
)
from services.qa_service import (
    generate_qa_pairs,
    save_qa_pairs_to_file,
)
from services.qdrant_service import (
    COLLECTION_CSV_MAPPING,
    COLLECTION_EMBEDDINGS_SEARCH,
    QDRANT_CONFIG,
    QdrantDataFetcher,
    QdrantHealthChecker,
    build_inputs_for_embedding,
    build_points_for_qdrant,
    create_or_recreate_collection_for_qdrant,
    delete_all_collections,
    embed_query_for_search,
    embed_texts_for_qdrant,
    get_all_collections,
    get_collection_stats,
    load_csv_for_qdrant,
    upsert_points_to_qdrant,
)
from services.token_service import (
    DEFAULT_ENCODING,
    EMBEDDING_PRICING,
    LLM_PRICING,
    MODEL_ENCODINGS,
    MODEL_LIMITS,
    TokenManager,
    count_tokens,
    estimate_tokens_simple,
    get_embedding_pricing,
    get_llm_pricing,
    get_model_limits,
    truncate_text,
)

__all__ = [
    # qdrant_service
    "QdrantHealthChecker",
    "QdrantDataFetcher",
    "get_collection_stats",
    "get_all_collections",
    "delete_all_collections",
    "load_csv_for_qdrant",
    "build_inputs_for_embedding",
    "embed_texts_for_qdrant",
    "create_or_recreate_collection_for_qdrant",
    "build_points_for_qdrant",
    "upsert_points_to_qdrant",
    "embed_query_for_search",
    "QDRANT_CONFIG",
    "COLLECTION_EMBEDDINGS_SEARCH",
    "COLLECTION_CSV_MAPPING",
    # qa_service
    "generate_qa_pairs",
    "save_qa_pairs_to_file",
    # token_service
    "TokenManager",
    "count_tokens",
    "estimate_tokens_simple",
    "truncate_text",
    "get_llm_pricing",
    "get_embedding_pricing",
    "get_model_limits",
    "DEFAULT_ENCODING",
    "MODEL_ENCODINGS",
    "LLM_PRICING",
    "EMBEDDING_PRICING",
    "MODEL_LIMITS",
    # config_service
    "ConfigManager",
    "config",
    "logger",
    "get_config",
    "set_config",
    "reload_config",
    # cache_service
    "MemoryCache",
    "cache_result",
    "cache",
    "get_global_cache",
    "init_cache_from_config",
    # json_service
    "safe_json_serializer",
    "safe_json_dumps",
    "safe_json_loads",
    "load_json_file",
    "save_json_file",
    "load_json_file_or_default",
    "merge_json_files",
    "is_valid_json",
    "pretty_print_json",
    "compact_json",
]

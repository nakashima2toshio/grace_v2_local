#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
list_collections.py - Qdrantコレクション一覧表示コマンド

使用例:
    python -m qa_qdrant.command.list_collections
    python -m qa_qdrant.command.list_collections --url http://localhost:6333
    python -m qa_qdrant.command.list_collections --detail   # ベクトル設定も表示
"""

import argparse
import os
import sys

# プロジェクトルートをPythonパスに追加（qa_qdrant/command/ から2階層上）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from qdrant_client_wrapper import (
    create_qdrant_client,
    get_all_collections,
    get_collection_stats,
)


def main():
    parser = argparse.ArgumentParser(description="Qdrant コレクション一覧表示コマンド")
    parser.add_argument("--url", default="http://localhost:6333", help="Qdrant URL")
    parser.add_argument("--detail", "-d", action="store_true", help="ベクトル設定など詳細情報も表示")
    args = parser.parse_args()

    client = create_qdrant_client(url=args.url)
    collections = get_all_collections(client)

    if not collections:
        print("コレクションが存在しません。")
        return

    print(f"コレクション一覧 ({len(collections)}件):")
    for col in collections:
        print(f"  - {col['name']:40s}  points={col['points_count']:>8,}  status={col['status']}")

        if args.detail:
            stats = get_collection_stats(client, col["name"])
            if stats:
                for vec_name, vec_info in stats["vector_config"].items():
                    print(f"        vector[{vec_name}]  size={vec_info['size']}  distance={vec_info['distance']}")


if __name__ == "__main__":
    main()

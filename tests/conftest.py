"""
Pytest configuration and hooks for custom output formatting.
"""
import os
import sys

# プロジェクトルートと helper/ ディレクトリを sys.path に追加する。
# 一部の production モジュール（helper/helper_rag_qa.py 等）は helper/ が
# sys.path 上にある前提で兄弟モジュールを bare import (例: `from helper_llm import ...`)
# している。本番では celery_config.py 等が同様の挿入を行うため、テストでも踏襲する。
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HELPER_DIR = os.path.join(_PROJECT_ROOT, "helper")
for _p in (_PROJECT_ROOT, _HELPER_DIR):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

# テストの総数と現在のカウントを追跡するためのグローバル変数
_test_count = 0
_total_tests = 0

def pytest_sessionstart(session):
    """セッション開始時にテスト総数を取得"""
    global _total_tests
    # 収集されたアイテム数はまだ確定していないため、collectionfinishで設定する手もあるが、
    # 簡易的に初期化のみ行う
    _total_tests = 0

def pytest_collection_modifyitems(session, config, items):
    """テスト収集完了後に総数を設定"""
    global _total_tests
    _total_tests = len(items)

def pytest_runtest_protocol(item, nextitem):
    """各テスト実行前にカスタムヘッダーを表示"""
    global _test_count
    _test_count += 1
    
    # 区切り線とカウントの表示
    header = f"\n----------------------------\nTest {_test_count}/{_total_tests}: {item.nodeid}\n----------------------------"
    sys.stdout.write(header + "\n")
    
    return None  # デフォルトの動作を続行

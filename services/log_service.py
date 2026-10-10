#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
log_service.py - ログ管理サービス
===============================
未回答質問ログ（logs/unanswered_questions.csv）を読み込み・クリアするサービス。
書き込み側の log_unanswered_question() は、唯一の呼び出し元だった Legacy ReAct
（services/agent_service.py）とともに 2026-10-10 に削除した。
"""

import csv
import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# ログファイルの保存先
LOG_DIR = Path("logs")
UNANSWERED_LOG_FILE = LOG_DIR / "unanswered_questions.csv"

def _ensure_log_dir():
    """ログディレクトリとファイルの初期化"""
    if not LOG_DIR.exists():
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    
    if not UNANSWERED_LOG_FILE.exists():
        with open(UNANSWERED_LOG_FILE, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "query", "collections", "reason", "agent_response"])

def load_unanswered_logs() -> pd.DataFrame:
    """
    未回答質問ログを読み込む

    Returns:
        pd.DataFrame: ログデータ
    """
    _ensure_log_dir()
    
    try:
        if not UNANSWERED_LOG_FILE.exists() or UNANSWERED_LOG_FILE.stat().st_size == 0:
            return pd.DataFrame(columns=["timestamp", "query", "collections", "reason", "agent_response"])
        
        df = pd.read_csv(UNANSWERED_LOG_FILE)
        # 日付の新しい順にソート
        if "timestamp" in df.columns:
            df = df.sort_values("timestamp", ascending=False)
        return df
        
    except Exception as e:
        logger.error(f"Failed to load unanswered logs: {e}")
        return pd.DataFrame(columns=["timestamp", "query", "collections", "reason", "agent_response"])

def clear_unanswered_logs():
    """未回答ログをクリア（ファイルを再作成）"""
    try:
        with open(UNANSWERED_LOG_FILE, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "query", "collections", "reason", "agent_response"])
        logger.info("Unanswered logs cleared.")
    except Exception as e:
        logger.error(f"Failed to clear logs: {e}")

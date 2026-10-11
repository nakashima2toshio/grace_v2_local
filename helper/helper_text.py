#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
helper_text.py - テキストのクレンジング
==========================================
`clean_text()` だけを持つ。

使用箇所:
- qa_generation/data_io.py（Q/A 生成の入力 CSV の本文を整える）

⚠️ 2026-10-11 に、呼び出し元の無かった関数（日本語の正規化・文分割・トークン数での
   チャンク分割・小さいチャンクの結合・複雑さの分析・キーワード抽出・切り詰め・統計）と
   `helper_rag.py` / `helper_rag_qa.py` を削除した（実装は git 履歴に残る。
   経緯は helper/docs/README_helper.md）。
"""

import re


def clean_text(text: str) -> str:
    """
    テキストのクレンジング処理

    Args:
        text: クレンジング対象のテキスト

    Returns:
        クレンジング済みのテキスト
    """
    if text is None or (hasattr(text, '__iter__') and not isinstance(text, str)):
        return ""

    # pandas NAチェック
    try:
        import pandas as pd
        if pd.isna(text):
            return ""
    except (ImportError, TypeError):
        pass

    if text == "":
        return ""

    # 文字列に変換
    text = str(text)

    # 改行を空白に置換
    text = text.replace('\n', ' ').replace('\r', ' ')

    # 連続した空白を1つの空白にまとめる
    text = re.sub(r'\s+', ' ', text)

    # 先頭・末尾の空白を除去
    text = text.strip()

    # 引用符の正規化
    text = text.replace('"', '"').replace('"', '"')
    text = text.replace(''', "'").replace(''', "'")

    return text

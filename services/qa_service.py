#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
qa_service.py - Q/A生成サービス
================================
Q/Aペアの生成と保存に関するビジネスロジック

機能:
- ローカル LLM（Ollama）によるQ/Aペアの生成
- Q/Aペアの保存

⚠️ **Q/A 生成パイプライン（`QAPipeline`）の実行口はここではない。**
CLI は `qa_qdrant/make_qa_register_qdrant.py`、Web は
`services/data_pipeline_service.py::run_qa_generation_sync()` を通る。
かつてここにあった `run_advanced_qa_generation()` は、存在しない
`qa_generator_runner` を import する死にコードだったため 2026-09-25 に削除した
（姉妹リポジトリ grace_v2 は 2026-09-12 に削除済み）。
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import pandas as pd

from config import get_default_ollama_model
from helper.helper_llm import create_llm_client

# モデルからインポート
from models import QAPair, QAPairsResponse

# ログ設定
logger = logging.getLogger(__name__)


def generate_qa_pairs(
    text: str,
    dataset_type: str,
    chunk_id: str,
    model: str = get_default_ollama_model(),
    qa_per_chunk: int = 3,
    log_callback=None,
) -> List[QAPair]:
    """
    テキストからQ/Aペアを生成（ローカル LLM / Ollama）

    Args:
        text: 対象テキスト
        dataset_type: データセットタイプ
        chunk_id: チャンクID
        model: 使用するモデル（既定は config.py::get_default_ollama_model()）
        qa_per_chunk: チャンクあたりのQ/A数
        log_callback: ログコールバック関数

    Returns:
        Q/Aペアのリスト
    """
    # LLM クライアント（ローカル LLM / Ollama）
    client = create_llm_client(provider="ollama")

    prompt = f"""あなたは教育用Q/Aペア生成の専門家です。

以下のテキストから、{qa_per_chunk}個の質問と回答のペアを生成してください。

テキスト:
{text}

要件:
1. 質問は具体的で明確なものにする
2. 回答はテキストの内容に基づいた正確なものにする
3. 質問タイプは以下から選択: factual, conceptual, application, analysis
4. テキストの重要な情報を網羅するようにする

JSON形式で出力してください。
"""

    try:
        # 構造化出力 API（Ollama の json_schema 制約付きデコード）
        qa_response = client.generate_structured(
            prompt=prompt,
            response_schema=QAPairsResponse,
            model=model
        )

        # Q/Aペアにメタデータを追加
        result_pairs = []
        for qa in qa_response.qa_pairs:
            qa_pair = QAPair(
                question=qa.question,
                answer=qa.answer,
                question_type=qa.question_type,
                source_chunk_id=chunk_id,
                dataset_type=dataset_type,
                auto_generated=True
            )
            result_pairs.append(qa_pair)

        if log_callback:
            log_callback(f"    └─ {len(result_pairs)}個のQ/Aペアを生成")

        return result_pairs

    except Exception as e:
        logger.error(f"Q/A生成エラー: {e}")
        if log_callback:
            log_callback(f"    └─ エラー: {str(e)}")
        return []


def save_qa_pairs_to_file(
    qa_pairs: List[QAPair], dataset_type: str, log_callback=None
) -> Dict[str, str]:
    """
    Q/AペアをCSVとJSONで保存

    Args:
        qa_pairs: Q/Aペアのリスト
        dataset_type: データセットタイプ
        log_callback: ログコールバック関数

    Returns:
        保存されたファイルパスの辞書
    """
    qa_output_dir = Path("qa_output")
    qa_output_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    saved_files = {}

    # DataFrameに変換
    qa_data = []
    for qa in qa_pairs:
        qa_data.append(
            {
                "question": qa.question,
                "answer": qa.answer,
                "question_type": qa.question_type,
                "source_chunk_id": qa.source_chunk_id,
                "dataset_type": qa.dataset_type,
                "auto_generated": qa.auto_generated,
            }
        )

    df_qa = pd.DataFrame(qa_data)

    # CSVファイル
    csv_filename = f"qa_pairs_{dataset_type}_{timestamp}.csv"
    csv_path = qa_output_dir / csv_filename
    df_qa.to_csv(csv_path, index=False, encoding="utf-8-sig")
    saved_files["csv"] = str(csv_path)

    if log_callback:
        log_callback(f"  📄 CSV保存: {csv_filename}")

    # JSONファイル
    json_filename = f"qa_pairs_{dataset_type}_{timestamp}.json"
    json_path = qa_output_dir / json_filename

    json_data = {
        "dataset_type": dataset_type,
        "created_at": datetime.now().isoformat(),
        "total_pairs": len(qa_pairs),
        "qa_pairs": qa_data,
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)

    saved_files["json"] = str(json_path)

    if log_callback:
        log_callback(f"  📋 JSON保存: {json_filename}")

    return saved_files

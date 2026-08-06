#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
services/data_pipeline_service.py - データ準備パイプラインの Web 向けラッパ層

【責務】
- CLI スクリプトに埋め込まれていた処理を、Web API から呼べる**関数**として提供する
- pandas DataFrame を返す既存 API を、JSON 化できる素の dict / list へ変換する
- async なチャンキング処理を、同期のジョブ runner から呼べるようにラップする
- 入力ファイルのブラウズを、許可ディレクトリ内に限定して提供する

**既存モジュールの中身は一切変更しない。** 呼び出し口だけをここへ集約する。
チャンク化・Q/A 生成・Qdrant 登録のロジックは
`chunking/` `qa_generation/` `qa_qdrant/` `services/qdrant_service.py` が持ち続ける。

【IPO（簡略）】
  Input  : QdrantClient / コレクション名 / CSV パス / チャンク化パラメータ
  Process: 既存関数への委譲 ＋ JSON 化 ＋ async→sync 変換 ＋ パス検証
  Output : dict / list[dict]（FastAPI がそのまま返せる形）

【この層が必要になった理由】
| 既存 | 問題 |
|---|---|
| `qdrant_delete_collection.py` | 単一コレクション削除が `main()` に直書きで、関数が無い |
| `QdrantDataFetcher` | pandas DataFrame を返すため API でそのまま返せない |
| `chunks_all_async()` | async。ジョブ runner は同期スレッドで動く |
| （なし） | 入力ファイル一覧を返す API が無い |
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from qdrant_client import QdrantClient

logger = logging.getLogger(__name__)

# =============================================================================
# ファイルブラウズ（許可ディレクトリのホワイトリスト）
# =============================================================================

# 画面から入力ファイルを選ばせる対象。**ここに無いディレクトリは参照させない。**
# ローカル専用のツールだが、`../../etc/passwd` のような相対パスを弾くために
# ホワイトリスト＋`resolve()` の二段で検証する。
ALLOWED_INPUT_DIRS: tuple[str, ...] = (
    "OUTPUT",          # 生データ（チャンク化の入力）
    "output_chunked",  # チャンク化の出力（Q/A 生成の入力）
    "qa_output",       # Q/A 生成の出力（Qdrant 登録の入力）
    "datasets",        # ダウンロードしたデータセット
)


class PathNotAllowedError(ValueError):
    """許可ディレクトリの外を指すパスが渡された。"""


def resolve_allowed_dir(dir_name: str, base: Optional[Path] = None) -> Path:
    """許可ディレクトリ名を絶対パスへ解決する。

    Args:
        dir_name: `ALLOWED_INPUT_DIRS` のいずれか
        base: 基点。省略時はカレントディレクトリ（リポジトリルート想定）

    Raises:
        PathNotAllowedError: ホワイトリスト外、または解決結果が基点の外へ出た場合

    Note:
        ホワイトリスト照合だけでは足りない。`dir_name` に `OUTPUT/../..` のような
        値が来た場合に備え、`resolve()` した結果が基点配下にあることも確認する。
    """
    if dir_name not in ALLOWED_INPUT_DIRS:
        raise PathNotAllowedError(
            f"許可されていないディレクトリです: {dir_name!r}（許可: {list(ALLOWED_INPUT_DIRS)}）"
        )
    root = (base or Path.cwd()).resolve()
    target = (root / dir_name).resolve()
    # resolve() 後に基点の外へ出ていないことを確認する
    if not (target == root or root in target.parents):
        raise PathNotAllowedError(f"基点の外を指しています: {dir_name!r}")
    return target


def list_input_files(
    dir_name: str,
    base: Optional[Path] = None,
    suffixes: tuple[str, ...] = (".csv", ".txt"),
) -> List[Dict[str, Any]]:
    """許可ディレクトリ内の入力ファイル候補を列挙する。

    Returns:
        `[{"name", "path", "size", "modified", "suffix"}, ...]` を更新日時の降順で返す。
        ディレクトリが存在しない場合は空リスト（エラーにしない）。
    """
    target = resolve_allowed_dir(dir_name, base=base)
    if not target.is_dir():
        return []

    files: List[Dict[str, Any]] = []
    for entry in target.iterdir():
        if not entry.is_file() or entry.suffix.lower() not in suffixes:
            continue
        stat = entry.stat()
        files.append({
            "name": entry.name,
            # API が受け取り直す用のパス。`dir/name` 形式に限定して絶対パスは出さない
            "path": f"{dir_name}/{entry.name}",
            "size": stat.st_size,
            "modified": stat.st_mtime,
            "suffix": entry.suffix.lower(),
        })
    files.sort(key=lambda f: f["modified"], reverse=True)
    return files


def resolve_input_file(rel_path: str, base: Optional[Path] = None) -> Path:
    """`list_input_files` が返した `dir/name` を実パスへ戻す。

    Raises:
        PathNotAllowedError: 形式不正・許可外ディレクトリ・基点の外を指す場合
        FileNotFoundError: 実ファイルが無い場合
    """
    parts = rel_path.split("/")
    if len(parts) != 2 or not parts[1]:
        raise PathNotAllowedError(
            f"入力パスは 'ディレクトリ名/ファイル名' の形式で指定してください: {rel_path!r}"
        )
    dir_name, file_name = parts
    # ファイル名側にも区切りが混ざらないことを確認（'a/../b' 等）
    if file_name in (".", "..") or os.sep in file_name or "/" in file_name:
        raise PathNotAllowedError(f"不正なファイル名です: {file_name!r}")

    target_dir = resolve_allowed_dir(dir_name, base=base)
    target = (target_dir / file_name).resolve()
    if target_dir not in target.parents:
        raise PathNotAllowedError(f"基点の外を指しています: {rel_path!r}")
    if not target.is_file():
        raise FileNotFoundError(f"ファイルが見つかりません: {rel_path}")
    return target


# =============================================================================
# Qdrant コレクション操作
# =============================================================================

def delete_collection(client: QdrantClient, collection_name: str) -> bool:
    """コレクションを 1 つ削除する。

    `qdrant_delete_collection.py` の `main()` に直書きされていた
    `client.delete_collection(...)` を関数として切り出したもの。
    **確認プロンプトは持たない** — 承認は呼び出し側（Web は HITL CONFIRM、
    CLI は `--yes`）の責務とする。

    Returns:
        削除できたら True。存在しない・失敗した場合は False（例外は投げない）。
    """
    try:
        client.delete_collection(collection_name=collection_name)
        logger.info(f"コレクション削除: {collection_name}")
        return True
    except Exception as e:
        logger.error(f"コレクション削除エラー {collection_name}: {e}")
        return False


def collection_exists(client: QdrantClient, collection_name: str) -> bool:
    """コレクションの存在を確認する（削除前チェック用）。"""
    try:
        names = {c.name for c in client.get_collections().collections}
        return collection_name in names
    except Exception as e:
        logger.error(f"コレクション一覧の取得に失敗: {e}")
        return False


def dataframe_to_records(df: Any) -> List[Dict[str, Any]]:
    """pandas DataFrame を JSON 化できる list[dict] へ変換する。

    `QdrantDataFetcher.fetch_collection_points()` は DataFrame を返すため、
    FastAPI の応答にそのまま載せられない。NaN は None へ寄せる
    （JSON に NaN は無く、`json.dumps` が `NaN` という不正なトークンを出すため）。

    エラー時に `{"Error": [...]}` / `{"Info": [...]}` という 1 列 DataFrame を
    返す実装なので、その形もそのまま records 化される（呼び出し側で判定する）。
    """
    if df is None:
        return []
    if hasattr(df, "empty") and df.empty:
        return []
    # object 型の NaN も落とすため where ではなく astype(object).where を使う
    try:
        import pandas as pd

        cleaned = df.astype(object).where(pd.notnull(df), None)
        return cleaned.to_dict(orient="records")
    except Exception as e:  # pragma: no cover - pandas 側の想定外
        logger.error(f"DataFrame の変換に失敗: {e}")
        return []


def collection_columns(records: List[Dict[str, Any]]) -> List[str]:
    """レコード列から列名を順序を保って抽出する。

    payload のキーはコレクションごとに違うため、画面側は列を固定できない。
    最初に現れた順で列を並べる（`dict` は挿入順を保つ）。
    """
    columns: List[str] = []
    for record in records:
        for key in record:
            if key not in columns:
                columns.append(key)
    return columns


# =============================================================================
# チャンキング（async → sync）
# =============================================================================

def run_chunking_sync(
    text: str,
    *,
    model: str,
    max_workers: int,
    block_size: int,
    output_file: str,
    dataset_type: str,
    source_file: Optional[str] = None,
    job_id: Optional[str] = None,
) -> List[str]:
    """`chunks_all_async()` を同期呼び出しできるようにラップする。

    ジョブ runner はワーカースレッドで動く同期関数なので、async をそのまま呼べない。
    `asyncio.run()` で新しいイベントループを立てる。

    Args:
        job_id: CheckpointManager の再開用ジョブ ID。None なら新規発行。

    Note:
        **`asyncio.run()` は「実行中のイベントループが無いこと」を要求する。**
        FastAPI のリクエストハンドラ（async）から直接呼ぶと
        `RuntimeError: asyncio.run() cannot be called from a running event loop`
        になる。必ずジョブのワーカースレッド側から呼ぶこと。
    """
    # 遅延 import: chunking は tqdm / openai SDK 等に依存するため、
    # この関数を呼ばない経路（コレクション一覧など）で import コストを払わない。
    from chunking.checkpoint_manager import CheckpointManager
    from chunking.csv_text_to_chunks_text_csv import chunks_all_async

    checkpoint_manager = CheckpointManager(job_id=job_id) if job_id else CheckpointManager()

    return asyncio.run(chunks_all_async(
        text=text,
        model=model,
        max_workers=max_workers,
        block_size=block_size,
        checkpoint_manager=checkpoint_manager,
        output_file=output_file,
        dataset_type=dataset_type,
        source_file=source_file,
    ))


def load_input_text(
    path: Path,
    *,
    text_column: Optional[str] = None,
    max_rows: Optional[int] = None,
    combine_rows: bool = False,
) -> str:
    """チャンク化の入力テキストを読み込む（CSV / テキスト）。

    `csv_text_to_chunks_text_csv.py` の `main()` が行っている分岐と同じ。
    CSV は `load_text_from_csv()` に委譲し、それ以外は素読みする。
    """
    if path.suffix.lower() == ".csv":
        from chunking.csv_text_to_chunks_text_csv import load_text_from_csv

        return load_text_from_csv(
            csv_path=str(path),
            text_column=text_column,
            max_rows=max_rows,
            combine_rows=combine_rows,
        )
    return path.read_text(encoding="utf-8")

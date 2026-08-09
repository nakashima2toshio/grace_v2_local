# backend/app/core/data_jobs.py
"""データ準備パイプラインのジョブ runner（チャンキング / 登録 / 削除）。

GRACE-Support・GRACE-Review と**同じジョブ基盤**（`core/jobs.py`）に乗せる。
`register_runner(params_type, runner, kind)` で params の型から runner を解決する
仕組みがすでにあるため、`jobs.py` 側に手を入れずに 3 種類を追加できる。

| params | kind | 実処理 |
|---|---|---|
| `ChunkingParams` | `chunking` | `chunking/csv_text_to_chunks_text_csv.py` |
| `RegisterParams` | `register` | `qa_qdrant/register_to_qdrant.py` |
| `DeleteParams` | `delete` | `services/data_pipeline_service.delete_collection` |

## 進捗の出し方

3 パッケージとも進捗コールバックを持たないため、`core/job_logs.py` の
`capture_logs()` で `logging` 出力を横取りして SSE の log イベントへ流す
（既存コードは無改修）。ステップの区切りだけは runner 側で `step` イベントを出す。

## プロバイダ

- **チャンク化の LLM**: ローカル（Ollama / 既定は config.py::get_default_ollama_model() 参照）。API キー不要
- **登録時の Embedding**: Gemini（`gemini-embedding-001` / 3072次元）。`GOOGLE_API_KEY` が必要

⚠️ LLM をローカル化しても Embedding は Gemini のままである。既存 Qdrant
コレクションの次元を変えないための決定なので、`RegisterParams.provider` を
`"ollama"` にしてはいけない。

## 破壊的操作の承認（HITL CONFIRM）

- **削除**は常に承認を求める
- **登録**は `recreate=True`（既存コレクションを作り直す）のときだけ承認を求める
  — 毎回ダイアログが出ると煩わしいため、破壊を伴う場合に限定する

承認は Support / Review と同じ `InterventionBridge` を通るので、
フロントは既存の `ConfirmModal` をそのまま使える。タイムアウト時は
**実行しない**（安全側）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.app.core.job_logs import capture_logs
from backend.app.core.jobs import register_runner
from backend.app.core.support_agent import ConfirmFn, EmitFn, SupportEvent
from config import get_default_ollama_model
from grace.intervention import (
    InterventionLevel,
    InterventionRequest,
)

logger = logging.getLogger(__name__)


# =============================================================================
# ステップ定義（フロントの Timeline が使う ID と 1:1）
# =============================================================================

CHUNKING_STEP_IDS: tuple[str, ...] = ("load", "chunk", "save")
CHUNKING_STEP_LABELS: Dict[str, str] = {
    "load": "① 入力読み込み（CSV / テキスト）",
    "chunk": "② セマンティックチャンク化（LLM・3 段階）",
    "save": "③ CSV 出力",
}

REGISTER_STEP_IDS: tuple[str, ...] = ("prepare", "confirm", "embed", "upsert")
REGISTER_STEP_LABELS: Dict[str, str] = {
    "prepare": "① 入力検証・コレクション名の決定",
    "confirm": "② HITL CONFIRM（recreate 時のみ）",
    "embed": "③ Embedding 生成",
    "upsert": "④ Qdrant へ登録",
}

DELETE_STEP_IDS: tuple[str, ...] = ("inspect", "confirm", "delete")
DELETE_STEP_LABELS: Dict[str, str] = {
    "inspect": "① 削除対象の確認",
    "confirm": "② HITL CONFIRM（承認が必要）",
    "delete": "③ 削除実行",
}


# =============================================================================
# パラメータ
# =============================================================================

@dataclass
class ChunkingParams:
    """POST /api/chunking/run のパラメータ（CLI 引数と 1:1 対応）。"""

    # 'ディレクトリ名/ファイル名' 形式（許可ディレクトリ内に限る）
    input_file: str
    output_dir: str = "output_chunked"
    # LLM はローカル（Ollama）。既定は config.py::get_default_ollama_model() の1箇所で管理する
    model: str = get_default_ollama_model()
    workers: int = 8
    block_size: int = 1000
    text_column: Optional[str] = None
    max_rows: Optional[int] = None
    combine_rows: bool = False
    # CheckpointManager の再開用ジョブ ID（--resume 相当）
    resume: Optional[str] = None
    verbose: bool = False


@dataclass
class RegisterParams:
    """POST /api/qdrant/register のパラメータ。"""

    input_file: str
    collection: str
    # ⚠️ True は既存コレクションを削除して作り直す ＝ 破壊的。CONFIRM を通す
    recreate: bool = False
    batch_size: int = 100
    embed_workers: int = 2
    text_col: Optional[str] = None
    domain: Optional[str] = None
    max_docs: Optional[int] = None
    # ⚠️ Embedding は Gemini のまま（LLM 用途とは別系統）。
    #    LLM をローカル化しても既存 Qdrant コレクション（3072次元）を
    #    そのまま使うための決定であり、"ollama" にしてはいけない。
    provider: str = "gemini"
    normalize_filename: bool = True
    create_ui_csv: bool = True
    ui_output_dir: str = "qa_output"
    verbose: bool = False


@dataclass
class DeleteParams:
    """POST /api/qdrant/delete のパラメータ。**必ず CONFIRM を通る。**"""

    collections: List[str]
    verbose: bool = False


# =============================================================================
# 共通ヘルパ
# =============================================================================

def _make_emitters(emit: EmitFn):
    """`support_agent.py` と同じ形の step/log ヘルパを作る。"""

    def log(message: str, step: Optional[str] = None, **data: Any) -> None:
        emit(SupportEvent(type="log", step=step, message=message, data=data))

    def step_started(step: str, title: str, **data: Any) -> None:
        emit(SupportEvent(type="step", step=step, status="started", title=title, data=data))

    def step_finished(step: str, **data: Any) -> None:
        emit(SupportEvent(type="step", step=step, status="finished", data=data))

    def step_skipped(step: str, **data: Any) -> None:
        emit(SupportEvent(type="step", step=step, status="skipped", data=data))

    def error(message: str, **data: Any) -> None:
        emit(SupportEvent(type="error", message=message, data=data))

    return log, step_started, step_finished, step_skipped, error


def _ask_confirmation(
    confirm: ConfirmFn,
    message: str,
    reason: str,
) -> tuple[bool, bool]:
    """HITL CONFIRM を要求する。

    Returns:
        `(承認されたか, タイムアウトしたか)`

    Note:
        `confirm` が None のケースは呼び出し側で潰してある（Web は必ず
        `InterventionBridge.resolver` が渡る）。CLI から使う場合に備えて
        呼び出し側で `confirm or ...` を用意すること。
    """
    response = confirm(InterventionRequest(
        level=InterventionLevel.CONFIRM,
        message=message,
        reason=reason,
    ))
    return response.should_continue, response.timeout_reached


# =============================================================================
# チャンキング
# =============================================================================

def _chunking_runner(
    params: ChunkingParams, emit: EmitFn, confirm: ConfirmFn
) -> Optional[Dict[str, Any]]:
    """CSV / テキスト → セマンティックチャンク CSV。

    `confirm` は使わない（チャンク化は既存データを壊さないため承認不要）。
    出力ファイルが既にあっても、CLI と同じく上書きする。

    ⚠️ LLM の事前チェックは行わない。ローカル（Ollama）実行のため API キーが
       存在せず、キーの有無で弾くと常に失敗する。Ollama への疎通不良は
       チャンク化の例外として捕捉し、error イベントで返す。
    """
    from services.data_pipeline_service import (
        load_input_text,
        resolve_input_file,
        run_chunking_sync,
    )

    log, step_started, step_finished, _step_skipped, error = _make_emitters(emit)

    # --- ① 入力読み込み -----------------------------------------------------
    step_started("load", CHUNKING_STEP_LABELS["load"], input_file=params.input_file)
    try:
        input_path = resolve_input_file(params.input_file)
    except (ValueError, FileNotFoundError) as e:
        error(f"❌ 入力ファイルを開けません: {e}")
        return None

    with capture_logs(emit, step="load"):
        text = load_input_text(
            input_path,
            text_column=params.text_column,
            max_rows=params.max_rows,
            combine_rows=params.combine_rows,
        )

    if not text.strip():
        error("❌ 入力テキストが空です。text_column / max_rows の指定を確認してください。")
        return None

    step_finished("load", chars=len(text), source_file=input_path.name)
    log(f"  読み込み完了: {len(text):,} 文字", step="load")

    # --- ② チャンク化 -------------------------------------------------------
    from chunking.csv_text_to_chunks_text_csv import generate_output_filename

    dataset_type = input_path.stem
    output_file = generate_output_filename(str(input_path), params.output_dir, dataset_type)

    step_started(
        "chunk",
        CHUNKING_STEP_LABELS["chunk"],
        model=params.model,
        workers=params.workers,
        block_size=params.block_size,
        output_file=output_file,
    )
    try:
        with capture_logs(emit, step="chunk"):
            chunks = run_chunking_sync(
                text,
                model=params.model,
                max_workers=params.workers,
                block_size=params.block_size,
                output_file=output_file,
                dataset_type=dataset_type,
                source_file=input_path.name,
                job_id=params.resume,
            )
    except Exception as e:
        logger.exception("チャンク化に失敗")
        error(f"❌ チャンク化に失敗しました: {type(e).__name__}: {e}")
        return None

    step_finished("chunk", chunks=len(chunks))

    # --- ③ 出力（chunks_all_async が CSV まで書く）--------------------------
    step_started("save", CHUNKING_STEP_LABELS["save"], output_file=output_file)
    exists = Path(output_file).exists()
    step_finished("save", output_file=output_file, written=exists)
    if not exists:
        log(f"  ⚠️ 出力ファイルが見つかりません: {output_file}", step="save")

    return {
        "kind": "chunking",
        "input_file": params.input_file,
        "output_file": output_file,
        "chunks": len(chunks),
        "chars": len(text),
        "model": params.model,
    }


# =============================================================================
# Qdrant 登録
# =============================================================================

def _register_runner(
    params: RegisterParams, emit: EmitFn, confirm: ConfirmFn
) -> Optional[Dict[str, Any]]:
    """Q/A CSV → Qdrant コレクション。

    `recreate=True` は既存コレクションを削除して作り直すため、**そのときだけ**
    HITL CONFIRM を通す（案1）。
    """
    from services.data_pipeline_service import collection_exists, resolve_input_file
    from services.qdrant_service import get_all_collections

    log, step_started, step_finished, step_skipped, error = _make_emitters(emit)

    # --- ① 入力検証 ---------------------------------------------------------
    step_started(
        "prepare",
        REGISTER_STEP_LABELS["prepare"],
        collection=params.collection,
        recreate=params.recreate,
    )
    try:
        input_path = resolve_input_file(params.input_file)
    except (ValueError, FileNotFoundError) as e:
        error(f"❌ 入力ファイルを開けません: {e}")
        return None

    try:
        from qdrant_client_wrapper import get_qdrant_client

        client = get_qdrant_client()
        exists = collection_exists(client, params.collection)
        existing_points = 0
        if exists:
            for c in get_all_collections(client):
                if c.get("name") == params.collection:
                    existing_points = int(c.get("points_count") or 0)
                    break
    except Exception as e:
        error(
            f"❌ Qdrant へ接続できません: {e}\n"
            "docker-compose -f docker-compose/docker-compose.yml up -d で起動してください。"
        )
        return None

    step_finished(
        "prepare",
        collection=params.collection,
        exists=exists,
        existing_points=existing_points,
        input_file=params.input_file,
    )

    # --- ② CONFIRM（recreate かつ既存があるときだけ）------------------------
    if params.recreate and exists:
        step_started(
            "confirm",
            REGISTER_STEP_LABELS["confirm"],
            action_type="recreate_collection",
            args={"collection": params.collection, "existing_points": existing_points},
            backend="qdrant",
            dry_run=False,
            requires_confirmation=True,
        )
        approved, timed_out = _ask_confirmation(
            confirm,
            message=(
                f"コレクション '{params.collection}' を削除して作り直します。"
                f"既存の {existing_points:,} 件は失われます。実行してよろしいですか？"
            ),
            reason="recreate=True による既存コレクションの再作成",
        )
        if not approved:
            reason = "承認待ちがタイムアウトしました" if timed_out else "承認されませんでした"
            step_finished("confirm", approved=False, timeout=timed_out)
            log(f"  {reason} — 登録を中止します（既存データは維持）", step="confirm")
            return {
                "kind": "register",
                "collection": params.collection,
                "registered": False,
                "cancelled": True,
                "reason": reason,
            }
        step_finished("confirm", approved=True, timeout=False)
    else:
        step_skipped(
            "confirm",
            reason="recreate=False" if not params.recreate else "対象コレクションが未作成",
        )

    # --- ③④ Embedding + 登録（register_to_qdrant が両方やる）----------------
    step_started(
        "embed",
        REGISTER_STEP_LABELS["embed"],
        provider=params.provider,
        batch_size=params.batch_size,
        embed_workers=params.embed_workers,
    )
    from qa_qdrant.register_to_qdrant import register_to_qdrant

    try:
        with capture_logs(emit, step="embed") as handler:
            ok = register_to_qdrant(
                input_file=str(input_path),
                collection_name=params.collection,
                recreate=params.recreate,
                batch_size=params.batch_size,
                text_col=params.text_col,
                domain=params.domain,
                max_docs=params.max_docs,
                provider=params.provider,
                normalize_filename=params.normalize_filename,
                create_ui_csv=params.create_ui_csv,
                ui_output_dir=params.ui_output_dir,
                embed_workers=params.embed_workers,
            )
            handler.set_step("upsert")
    except Exception as e:
        logger.exception("Qdrant 登録に失敗")
        error(f"❌ 登録に失敗しました: {type(e).__name__}: {e}")
        return None

    step_finished("embed", provider=params.provider)

    # --- 登録後の件数を確認 -------------------------------------------------
    step_started("upsert", REGISTER_STEP_LABELS["upsert"], collection=params.collection)
    points_after = 0
    try:
        for c in get_all_collections(client):
            if c.get("name") == params.collection:
                points_after = int(c.get("points_count") or 0)
                break
    except Exception as e:  # 登録自体は成功しているので警告に留める
        log(f"  ⚠️ 登録後の件数取得に失敗: {e}", step="upsert")

    step_finished("upsert", collection=params.collection, points=points_after, ok=ok)

    if not ok:
        error("❌ 登録処理が失敗を返しました。ログを確認してください。")
        return None

    return {
        "kind": "register",
        "collection": params.collection,
        "input_file": params.input_file,
        "registered": True,
        "cancelled": False,
        "points": points_after,
        "points_before": existing_points,
        "recreate": params.recreate,
    }


# =============================================================================
# コレクション削除
# =============================================================================

def _delete_runner(
    params: DeleteParams, emit: EmitFn, confirm: ConfirmFn
) -> Optional[Dict[str, Any]]:
    """コレクションを削除する。**必ず HITL CONFIRM を通る。**

    単発の `DELETE` エンドポイントにしていないのは、誤操作で不可逆に消えるのを
    防ぐため。承認画面には対象名と件数を出す。
    """
    from services.data_pipeline_service import delete_collection
    from services.qdrant_service import get_all_collections

    log, step_started, step_finished, _step_skipped, error = _make_emitters(emit)

    if not params.collections:
        error("❌ 削除対象が指定されていません。")
        return None

    # --- ① 対象の確認 -------------------------------------------------------
    step_started("inspect", DELETE_STEP_LABELS["inspect"], collections=list(params.collections))
    try:
        from qdrant_client_wrapper import get_qdrant_client

        client = get_qdrant_client()
        all_collections = {c["name"]: c for c in get_all_collections(client)}
    except Exception as e:
        error(
            f"❌ Qdrant へ接続できません: {e}\n"
            "docker-compose -f docker-compose/docker-compose.yml up -d で起動してください。"
        )
        return None

    targets: List[Dict[str, Any]] = []
    missing: List[str] = []
    for name in params.collections:
        if name in all_collections:
            targets.append({
                "name": name,
                "points_count": int(all_collections[name].get("points_count") or 0),
            })
        else:
            missing.append(name)

    total_points = sum(t["points_count"] for t in targets)
    step_finished("inspect", targets=targets, missing=missing, total_points=total_points)

    if missing:
        log(f"  ⚠️ 存在しないため対象外: {', '.join(missing)}", step="inspect")
    if not targets:
        error("❌ 削除できる対象がありません（すべて存在しませんでした）。")
        return None

    # --- ② CONFIRM ----------------------------------------------------------
    names = ", ".join(t["name"] for t in targets)
    step_started(
        "confirm",
        DELETE_STEP_LABELS["confirm"],
        action_type="delete_collections",
        args={"collections": targets},
        backend="qdrant",
        dry_run=False,
        requires_confirmation=True,
    )
    approved, timed_out = _ask_confirmation(
        confirm,
        message=(
            f"コレクション {len(targets)} 件（{names}）を削除します。"
            f"合計 {total_points:,} 件のデータが失われ、元に戻せません。実行してよろしいですか？"
        ),
        reason="コレクション削除（不可逆）",
    )
    if not approved:
        reason = "承認待ちがタイムアウトしました" if timed_out else "承認されませんでした"
        step_finished("confirm", approved=False, timeout=timed_out)
        log(f"  {reason} — 削除を中止します", step="confirm")
        return {
            "kind": "delete",
            "deleted": [],
            "failed": [],
            "missing": missing,
            "cancelled": True,
            "reason": reason,
        }
    step_finished("confirm", approved=True, timeout=False)

    # --- ③ 削除 -------------------------------------------------------------
    step_started("delete", DELETE_STEP_LABELS["delete"], count=len(targets))
    deleted: List[str] = []
    failed: List[str] = []
    with capture_logs(emit, step="delete"):
        for target in targets:
            if delete_collection(client, target["name"]):
                deleted.append(target["name"])
            else:
                failed.append(target["name"])

    step_finished("delete", deleted=deleted, failed=failed)
    log(f"  削除完了: {len(deleted)} 件 / 失敗: {len(failed)} 件", step="delete")

    return {
        "kind": "delete",
        "deleted": deleted,
        "failed": failed,
        "missing": missing,
        "cancelled": False,
        "total_points": total_points,
    }


# =============================================================================
# runner 登録（この import 時点で jobs.py に効く）
# =============================================================================

register_runner(ChunkingParams, _chunking_runner, "chunking")
register_runner(RegisterParams, _register_runner, "register")
register_runner(DeleteParams, _delete_runner, "delete")

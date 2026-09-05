# backend/app/core/data_jobs.py
"""データ準備パイプラインのジョブ runner（チャンキング / 登録 / 削除）。

GRACE-Support・GRACE-Review と**同じジョブ基盤**（`core/jobs.py`）に乗せる。
`register_runner(params_type, runner, kind)` で params の型から runner を解決する
仕組みがすでにあるため、`jobs.py` 側に手を入れずに 3 種類を追加できる。

| params | kind | 実処理 |
|---|---|---|
| `ChunkingParams` | `chunking` | `chunking/csv_text_to_chunks_text_csv.py` |
| `QaGenerationParams` | `qa` | `qa_generation/pipeline.py::QAPipeline` |
| `RegisterParams` | `register` | `qa_qdrant/register_to_qdrant.py` |
| `DeleteParams` | `delete` | `services/data_pipeline_service.delete_collection` |

## 進捗の出し方

3 パッケージとも進捗コールバックを持たないため、`core/job_logs.py` の
`capture_logs()` で `logging` 出力を横取りして SSE の log イベントへ流す
（既存コードは無改修）。ステップの区切りだけは runner 側で `step` イベントを出す。

## プロバイダ

- **チャンク化・Q/A 生成の LLM**: ローカル（Ollama / 既定は config.py::get_default_ollama_model() 参照）。API キー不要
- **登録時の Embedding**: Gemini（`gemini-embedding-001` / 3072次元）。`GOOGLE_API_KEY` が必要

⚠️ LLM をローカル化しても Embedding は Gemini のままである。既存 Qdrant
コレクションの次元を変えないための決定なので、`RegisterParams.provider` を
`"ollama"` にしてはいけない。

## 破壊的操作の承認（HITL CONFIRM）

- **削除**は常に承認を求める
- **登録**は `recreate=True`（既存コレクションを作り直す）のときだけ承認を求める
  — 毎回ダイアログが出ると煩わしいため、破壊を伴う場合に限定する
- **チャンク化・Q/A 生成**は承認不要（どちらも既存データを壊さない）。
  Q/A 生成の出力はタイムスタンプ付きの新規ファイルなので、既存の Q/A CSV も残る

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

QA_STEP_IDS: tuple[str, ...] = ("load", "generate", "coverage", "save")
QA_STEP_LABELS: Dict[str, str] = {
    "load": "① チャンク済み CSV の読み込み",
    "generate": "② Q/A ペア生成（ローカル LLM）",
    "coverage": "③ カバレージ分析（任意）",
    "save": "④ Q/A CSV・JSON 出力",
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
    # ⚠️ **既定値をここで評価しない。** dataclass の既定は import 時に 1 度だけ
    # 確定するため、`.env` の OLLAMA_DEFAULT_MODEL と grace_config.yml の
    # llm.model が食い違うと「ヘッダーは A・実行は B」になる（詳細は
    # `_resolve_model()` のコメント）。None のまま持ち回り runner で解決する。
    model: Optional[str] = None
    workers: int = 8
    block_size: int = 1000
    text_column: Optional[str] = None
    max_rows: Optional[int] = None
    combine_rows: bool = False
    # CheckpointManager の再開用ジョブ ID（--resume 相当）
    resume: Optional[str] = None
    verbose: bool = False


@dataclass
class QaGenerationParams:
    """POST /api/qa/generate のパラメータ。

    入力は**チャンク済み CSV**（`chunking` ジョブの出力）。`text` /
    `Combined_Text` / `content` / `chunk_text` のいずれかのカラムが要る。
    """

    # 'ディレクトリ名/ファイル名' 形式（許可ディレクトリ内に限る）
    input_file: str
    output_dir: str = "qa_output/pipeline"
    # ⚠️ **既定値をここで評価しない**（`ChunkingParams.model` と同じ理由）。
    # None のまま持ち回り、`_resolve_model()` の 1 箇所で解決する。
    model: Optional[str] = None
    max_docs: Optional[int] = None
    # ⚠️ True にするなら Celery ワーカーが起動していること。落ちていると
    #    パイプラインが例外を投げる（runner が error イベントへ変換する）
    use_celery: bool = False
    concurrency: int = 8
    batch_chunks: int = 3
    analyze_coverage: bool = True
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

def _resolve_model(explicit: Optional[str]) -> str:
    """使う LLM モデル名を決める。未指定（None / 空文字）なら既定へ倒す。

    ⚠️ **既定は `GET /api/model`（画面ヘッダーの「利用モデル名」）と
    同じ解決を使う。** すなわち `grace_config.yml` 適用後の
    `get_config().llm.model`。`config.py::get_default_ollama_model()` を
    直接使ってはいけない。

    ## なぜ 2 つの既定が割れるのか

    | 経路 | 既定の出どころ |
    |---|---|
    | ヘッダー・GRACE エージェント | `grace_config.yml` の `llm.model`（無ければクラス既定） |
    | `config.py::get_default_ollama_model()` | 環境変数 `OLLAMA_DEFAULT_MODEL`（無ければ固定文字列） |

    `grace_config.yml` は `llm.model` を明示しているため、`.env` に
    `OLLAMA_DEFAULT_MODEL` を書いても**ヘッダー側は変わらない**。
    その状態でデータジョブだけが環境変数を見ていると、

        画面: 利用モデル名 gemma4:12b-mlx
        実行: model 'gemma4:e4b' not found（404 が全ブロックに出る）

    という食い違いになる。しかもチャンク化は 404 を 3 回リトライしてから
    フォールバック分割へ落ちるため、**エラーで止まらずゴミを作り続ける**。
    既定の解決をヘッダーと 1 本にしておけば、この食い違いは起き得ない。

    Args:
        explicit: フォームで選ばれたモデル名。未指定は None / 空文字

    Returns:
        実際に使うモデル名（前後の空白は落とす）
    """
    chosen = (explicit or "").strip()
    if chosen:
        return chosen

    try:
        from grace.config import get_config

        resolved = (get_config().llm.model or "").strip()
    except Exception:  # 設定が壊れていても既定でジョブは動かす
        logger.warning("grace_config.yml から既定モデルを解決できませんでした", exc_info=True)
        resolved = ""

    return resolved or get_default_ollama_model()


def _model_not_pulled_message(model: str) -> Optional[str]:
    """モデルが Ollama に無ければエラーメッセージを、あれば None を返す。

    ⚠️ **LLM ループに入る前に弾くのが要点。** チャンク化も Q/A 生成も、
    1 ブロックあたり 3 回リトライしてからフォールバックへ落ちる作りなので、
    未 pull のモデル名で走らせると **止まらずにゴミを作り続ける**
    （実測: 1229 ブロックで 404 が 3,687 回、結果は機械的な分割のまま「成功」）。
    数百回の 404 を眺めてから気づくのではなく、最初の 1 回で返す。

    Returns:
        エラーメッセージ。問題なし・**判定不能**（一覧を取れない）なら None
    """
    from services.data_pipeline_service import list_pulled_ollama_models

    pulled = list_pulled_ollama_models()
    if not pulled or model in pulled:
        # 空 = 判定不能。事前確認を理由に、実際には動くジョブを止めない
        return None

    return (
        f"❌ モデル '{model}' は Ollama に見つかりません。\n"
        f"   pull 済み: {', '.join(sorted(pulled)) or '(なし)'}\n"
        f"   `ollama pull {model}` で取得するか、モデル欄から別のモデルを選んでください。"
    )


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

    # 未指定（None / 空文字）ならヘッダーと同じ既定へ。解決はここ 1 箇所だけ
    model = _resolve_model(params.model)

    # --- ① 入力読み込み -----------------------------------------------------
    step_started("load", CHUNKING_STEP_LABELS["load"], input_file=params.input_file, model=model)
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

    not_pulled = _model_not_pulled_message(model)
    if not_pulled:
        error(not_pulled)
        return None

    step_finished("load", chars=len(text), source_file=input_path.name, model=model)
    log(f"  読み込み完了: {len(text):,} 文字（モデル: {model}）", step="load")

    # --- ② チャンク化 -------------------------------------------------------
    from chunking.csv_text_to_chunks_text_csv import generate_output_filename

    dataset_type = input_path.stem
    output_file = generate_output_filename(str(input_path), params.output_dir, dataset_type)

    step_started(
        "chunk",
        CHUNKING_STEP_LABELS["chunk"],
        model=model,
        workers=params.workers,
        block_size=params.block_size,
        output_file=output_file,
    )
    try:
        with capture_logs(emit, step="chunk"):
            chunks = run_chunking_sync(
                text,
                model=model,
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
        "model": model,
    }


# =============================================================================
# Q/A 生成
# =============================================================================

def _qa_runner(
    params: QaGenerationParams, emit: EmitFn, confirm: ConfirmFn
) -> Optional[Dict[str, Any]]:
    """チャンク済み CSV → Q/A ペア CSV。

    `qa_qdrant/make_qa_register_qdrant.py` の **Phase 1 と同じ経路**
    （`QAPipeline`）を通る。CLI と Web で結果が食い違わないよう、
    パイプライン本体には手を入れていない。

    `confirm` は使わない（Q/A 生成は既存データを壊さないため承認不要）。
    出力はタイムスタンプ付きの新規ファイルなので、既存の Q/A CSV も消えない。

    ⚠️ LLM の事前チェックは行わない。ローカル（Ollama）実行のため API キーが
       存在せず、キーの有無で弾くと常に失敗する。Ollama への疎通不良は
       生成の例外として捕捉し、error イベントで返す（チャンク化と同じ方針）。
    """
    from services.data_pipeline_service import (
        resolve_input_file,
        run_qa_generation_sync,
    )

    log, step_started, step_finished, step_skipped, error = _make_emitters(emit)

    # 未指定（None / 空文字）ならヘッダーと同じ既定へ。解決はここ 1 箇所だけ
    model = _resolve_model(params.model)

    # --- ① 入力読み込み -----------------------------------------------------
    step_started("load", QA_STEP_LABELS["load"], input_file=params.input_file)
    try:
        input_path = resolve_input_file(params.input_file)
    except (ValueError, FileNotFoundError) as e:
        error(f"❌ 入力ファイルを開けません: {e}")
        return None

    if input_path.suffix.lower() != ".csv":
        error(
            f"❌ Q/A 生成の入力はチャンク済み CSV です（渡された拡張子: {input_path.suffix}）。"
            "先に「① チャンキング」を実行してください。"
        )
        return None

    # ⚠️ **カラムの検証はここで先に行う。** QAPipeline は読み込んだあとに
    #    ValueError を投げるが、それだと「LLM を呼ぶ前に分かる誤り」なのに
    #    生成ステップの失敗として見えてしまう。入力の問題は入力ステップで返す。
    try:
        import pandas as pd

        df_head = pd.read_csv(input_path, nrows=1)
    except Exception as e:
        error(f"❌ CSV を読めません: {type(e).__name__}: {e}")
        return None

    text_col = next(
        (c for c in ("text", "Combined_Text", "content", "chunk_text") if c in df_head.columns),
        None,
    )
    if text_col is None:
        error(
            "❌ テキストカラムが見つかりません。"
            f"利用可能なカラム: {list(df_head.columns)} / "
            "必要: 'text' / 'Combined_Text' / 'content' / 'chunk_text' のいずれか"
        )
        return None

    not_pulled = _model_not_pulled_message(model)
    if not_pulled:
        error(not_pulled)
        return None

    step_finished("load", source_file=input_path.name, text_column=text_col, model=model)
    log(f"  入力: {input_path.name}（テキストカラム: {text_col} / モデル: {model}）", step="load")

    # --- ② Q/A 生成 ---------------------------------------------------------
    step_started(
        "generate",
        QA_STEP_LABELS["generate"],
        model=model,
        use_celery=params.use_celery,
        concurrency=params.concurrency,
        batch_chunks=params.batch_chunks,
        max_docs=params.max_docs,
    )
    if params.use_celery:
        log(
            f"  Celery 並列モード（並列タスク数 {params.concurrency}）"
            " — ワーカーが起動している必要があります",
            step="generate",
        )

    try:
        with capture_logs(emit, step="generate") as handler:
            result = run_qa_generation_sync(
                str(input_path),
                model=model,
                output_dir=params.output_dir,
                max_docs=params.max_docs,
                use_celery=params.use_celery,
                concurrency=params.concurrency,
                batch_chunks=params.batch_chunks,
                analyze_coverage=params.analyze_coverage,
            )
            # 生成が終わった時点で以降のログは次のステップへ寄せる
            handler.set_step("coverage" if params.analyze_coverage else "save")
    except Exception as e:
        logger.exception("Q/A 生成に失敗")
        hint = ""
        if params.use_celery:
            hint = (
                "\n   Celery ワーカーが起動しているか確認してください"
                "（起動していない場合は Celery を外して再実行）。"
            )
        error(f"❌ Q/A 生成に失敗しました: {type(e).__name__}: {e}{hint}")
        return None

    qa_count = int(result.get("qa_count") or 0)
    step_finished("generate", qa_count=qa_count, model=model)

    if qa_count == 0:
        # 例外は出ていないが 1 件も作れていない。後続の登録が空振りするので
        # ここで失敗として返す（「成功したのに 0 件」を黙って通さない）。
        error(
            "❌ Q/A ペアが 1 件も生成されませんでした。"
            "モデル名・チャンク内容・（Celery 使用時は）ワーカーの状態を確認してください。"
        )
        return None

    # --- ③ カバレージ分析 ---------------------------------------------------
    coverage = result.get("coverage_results") or {}
    coverage_rate = coverage.get("coverage_rate")
    if params.analyze_coverage:
        step_started("coverage", QA_STEP_LABELS["coverage"])
        step_finished(
            "coverage",
            coverage_rate=coverage_rate,
            covered_chunks=coverage.get("covered_chunks"),
            total_chunks=coverage.get("total_chunks"),
        )
        if isinstance(coverage_rate, (int, float)):
            log(f"  カバレージ率: {coverage_rate:.1%}", step="coverage")
    else:
        step_skipped("coverage", reason="analyze_coverage=False")

    # --- ④ 出力 -------------------------------------------------------------
    saved = result.get("saved_files") or {}
    qa_csv = saved.get("qa_csv")
    step_started("save", QA_STEP_LABELS["save"], output_dir=params.output_dir)
    exists = bool(qa_csv) and Path(qa_csv).exists()
    step_finished("save", qa_csv=qa_csv, qa_json=saved.get("qa_json"), written=exists)
    if not exists:
        log(f"  ⚠️ 出力ファイルが見つかりません: {qa_csv}", step="save")

    return {
        "kind": "qa",
        "input_file": params.input_file,
        "qa_csv": qa_csv,
        "qa_json": saved.get("qa_json"),
        "qa_count": qa_count,
        "coverage_rate": coverage_rate,
        "total_chunks": coverage.get("total_chunks"),
        "model": model,
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
register_runner(QaGenerationParams, _qa_runner, "qa")
register_runner(RegisterParams, _register_runner, "register")
register_runner(DeleteParams, _delete_runner, "delete")

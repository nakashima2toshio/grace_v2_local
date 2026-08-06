# backend/app/core/job_logs.py
"""既存モジュールの `logging` 出力を、ジョブの進捗イベントへ転送する仕組み。

## なぜ必要か

`chunking/` `qa_generation/` `qa_qdrant/` の各パイプラインは、進捗を
`logger.info(...)` と `tqdm` にしか出しておらず、**進捗コールバックを持たない**。
GRACE-Support / GRACE-Review が `emit(SupportEvent(...))` で SSE へ流しているのに対し、
データ準備側にはその経路が無い。

そこで各モジュールへ `emit` 引数を足す（＝3 パッケージを改修する）代わりに、
**ジョブ実行スレッドに紐づく `logging.Handler` を一時的に取り付けて
ログレコードを横取りする**。既存コードは 1 行も変えずに進捗が SSE へ流れる。

## スレッドで絞る理由（重要）

`JobManager.start()` はジョブごとにワーカースレッドを立てる。ハンドラは
ロガー（`chunking` 等のパッケージロガー）に付くため、**取り付けたハンドラは
他のジョブのログレコードも受け取ってしまう**。同時に 2 本のチャンキングを
走らせると、片方の進捗にもう片方のログが混ざる。

これを防ぐため、ハンドラは生成時に `threading.get_ident()` を記録し、
`record.thread` が一致するレコードだけを転送する。ハンドラの取り付け自体は
プロセス全体に効くが、**転送は自分のスレッド分だけ**になる。

## 使い方

    with capture_logs(emit, ["chunking"], step="chunk"):
        chunks_all_async(...)      # 中の logger.info が log イベントとして流れる

`finally` で必ず取り外す（コンテキストマネージャなので例外時も外れる）。
外し忘れるとハンドラが積み上がり、1 行のログが N 回転送されるようになる。
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Callable, Iterator, List, Optional, Sequence

from backend.app.core.support_agent import SupportEvent

# 進捗イベントを送る関数（`Job.emit` と同じ形）
EmitFn = Callable[[SupportEvent], None]

# 既定で横取りするロガー名。パッケージロガーに付ければ
# `logging.getLogger(__name__)` で作られた子ロガーの出力も propagate で拾える。
DEFAULT_LOGGER_NAMES: tuple[str, ...] = (
    "chunking",
    "qa_generation",
    "qa_qdrant",
    "services",
)


class JobLogHandler(logging.Handler):
    """自スレッドのログレコードだけを `emit` へ転送する Handler。

    `logging.Handler.emit(record)` を実装するのが本体。進捗送信用のコールバックは
    メソッド名と衝突するため `_emit_fn` という別名で保持する。
    """

    def __init__(
        self,
        emit_fn: EmitFn,
        step: Optional[str] = None,
        thread_ident: Optional[int] = None,
    ) -> None:
        super().__init__()
        self._emit_fn = emit_fn
        self._step = step
        # 生成した時点のスレッド ＝ ジョブのワーカースレッド
        self._thread_ident = thread_ident if thread_ident is not None else threading.get_ident()

    def emit(self, record: logging.LogRecord) -> None:  # noqa: A003 (Handler の API 名)
        """レコードを log イベントとして転送する。

        - 自スレッド以外のレコードは無視する（他ジョブとの混線防止）
        - 転送中の例外は握りつぶす。**ログ出力の失敗で本処理を落とさない**
          （`logging` の慣行どおり `handleError` に委ねる）
        """
        if record.thread != self._thread_ident:
            return
        try:
            message = self.format(record)
        except Exception:  # pragma: no cover - フォーマット失敗は握りつぶす
            self.handleError(record)
            return
        if not message.strip():
            return
        try:
            self._emit_fn(SupportEvent(
                type="log",
                step=self._step,
                message=message,
                data={"level": record.levelname},
            ))
        except Exception:  # pragma: no cover - 購読者側の失敗を本処理へ波及させない
            self.handleError(record)

    def set_step(self, step: Optional[str]) -> None:
        """転送先のステップ ID を切り替える（同じジョブ内で段階が進んだとき）。"""
        self._step = step


# ロガー level の退避テーブル。`{ロガー名: (元の level, 参照数)}`。
#
# ⚠️ **参照カウントが必要な理由（実測で見つかった不具合）**
#
# 素朴に「入るとき logger.level を控え、出るとき戻す」と書くと、
# **同時に 2 本のジョブが走ったときに level が復元されない**。
#
#   ジョブA: level=NOTSET(0) を控える → INFO(20) に上げる
#   ジョブB: **すでに 20 になっている**ものを「元の値」として控える → 20 のまま
#   ジョブA: 終了 → 0 に戻す
#   ジョブB: 終了 → **20 に戻してしまう**（元は 0 だったのに）
#
# 結果、全ジョブ終了後もロガーが INFO のまま残り、コンソール出力が増え続ける。
# 最初に入った 1 本だけが元の値を持ち、最後に出る 1 本がそれを戻す形にする。
_level_lock = threading.Lock()
_level_refs: dict[str, tuple[int, int]] = {}


def _acquire_level(logger: logging.Logger, level: int) -> None:
    """ロガーの level を引き上げ、参照数を 1 増やす（`_level_lock` 内で呼ぶ）。"""
    entry = _level_refs.get(logger.name)
    if entry is None:
        # 最初の 1 本だけが「本当の元の値」を持つ
        _level_refs[logger.name] = (logger.level, 1)
        if logger.level == logging.NOTSET or logger.level > level:
            logger.setLevel(level)
    else:
        original, count = entry
        _level_refs[logger.name] = (original, count + 1)


def _release_level(logger: logging.Logger) -> None:
    """参照数を 1 減らし、0 になったら元の level へ戻す（`_level_lock` 内で呼ぶ）。"""
    entry = _level_refs.get(logger.name)
    if entry is None:
        return
    original, count = entry
    if count <= 1:
        logger.setLevel(original)
        _level_refs.pop(logger.name, None)
    else:
        _level_refs[logger.name] = (original, count - 1)


@contextmanager
def capture_logs(
    emit_fn: EmitFn,
    logger_names: Sequence[str] = DEFAULT_LOGGER_NAMES,
    step: Optional[str] = None,
    level: int = logging.INFO,
) -> Iterator[JobLogHandler]:
    """`logger_names` のログ出力を `emit_fn` へ転送する。

    Args:
        emit_fn: 進捗イベントの送信先（`Job.emit`）
        logger_names: 横取りするロガー名。既定は データ準備 4 パッケージ
        step: 転送するイベントの step ID。途中で `handler.set_step()` で変えられる
        level: 転送する最低レベル（既定 INFO）

    Yields:
        取り付けた `JobLogHandler`。`set_step()` でステップを切り替えられる。

    Note:
        対象ロガーの `level` を一時的に下げる。既定では多くのロガーが未設定
        （＝ root の WARNING を継承）なので、下げないと INFO が届かない。
        復元は参照カウント方式（`_level_refs` のコメント参照）。

        **同時に走るジョブが違う `level` を要求した場合、先に入った方が勝つ。**
        全ジョブが既定の INFO を使う限り問題にならない。
    """
    handler = JobLogHandler(emit_fn, step=step)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(message)s"))

    loggers: List[logging.Logger] = [logging.getLogger(name) for name in logger_names]

    with _level_lock:
        for logger in loggers:
            _acquire_level(logger, level)
            logger.addHandler(handler)

    try:
        yield handler
    finally:
        with _level_lock:
            for logger in loggers:
                logger.removeHandler(handler)
                _release_level(logger)

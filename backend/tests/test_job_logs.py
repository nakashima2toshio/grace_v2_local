"""`backend/app/core/job_logs.py` — ログ横取りによる進捗転送のテスト。

実 Qdrant・実 API キーは不要（`logging` だけで完結する）。

## なぜこのテストが要るか

`capture_logs` は「既存 3 パッケージを改修せずに進捗を SSE へ流す」ための仕組みで、
**プロセス全体の logging 設定に触る**。したがって次の 2 点が壊れると影響が広い。

1. **他ジョブとの混線** — ハンドラはロガーに付くので、絞らないと他スレッドの
   ログまで自分の進捗として流れる
2. **level の復元漏れ** — 復元に失敗するとジョブ終了後もロガーが INFO のまま残り、
   以降のコンソール出力が増え続ける（実装当初の素朴な退避方式で実際に起きた）
"""
from __future__ import annotations

import logging
import threading
import time

from backend.app.core.job_logs import JobLogHandler, capture_logs
from backend.app.core.support_agent import SupportEvent

CAPTURED_LOGGER = "chunking"


def _worker_logger() -> logging.Logger:
    """`capture_logs(["chunking"])` の子として拾われるロガー。"""
    return logging.getLogger(f"{CAPTURED_LOGGER}.test_target")


def test_captures_log_as_event():
    """`logger.info` が log イベントとして転送される。"""
    events: list[SupportEvent] = []
    with capture_logs(events.append, [CAPTURED_LOGGER], step="chunk"):
        _worker_logger().info("チャンク化開始")

    assert len(events) == 1
    assert events[0].type == "log"
    assert events[0].step == "chunk"
    assert events[0].message == "チャンク化開始"
    assert events[0].data["level"] == "INFO"


def test_blank_messages_are_dropped():
    """区切り線の前後に出る空行はイベントにしない（UI がスカスカになる）。"""
    events: list[SupportEvent] = []
    with capture_logs(events.append, [CAPTURED_LOGGER]):
        logger = _worker_logger()
        logger.info("")
        logger.info("   ")
        logger.info("本文")

    assert [e.message for e in events] == ["本文"]


def test_handler_removed_after_exit():
    """コンテキストを抜けたらハンドラが残らない（残ると多重転送になる）。"""
    logger = logging.getLogger(CAPTURED_LOGGER)
    before = list(logger.handlers)

    with capture_logs(lambda _e: None, [CAPTURED_LOGGER]):
        assert any(isinstance(h, JobLogHandler) for h in logger.handlers)

    assert logger.handlers == before


def test_handler_removed_on_exception():
    """例外で抜けてもハンドラと level が戻る。"""
    logger = logging.getLogger(CAPTURED_LOGGER)
    logger.setLevel(logging.NOTSET)
    before = list(logger.handlers)

    try:
        with capture_logs(lambda _e: None, [CAPTURED_LOGGER]):
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    assert logger.handlers == before
    assert logger.level == logging.NOTSET


def test_emit_failure_does_not_break_caller():
    """購読者側が例外を投げても本処理を巻き込まない。

    進捗の送信失敗でチャンク化が落ちるのは本末転倒なので、
    `logging` の慣行どおり握りつぶす（`handleError` に委ねる）。
    """
    def broken_emit(_event: SupportEvent) -> None:
        raise ValueError("購読者側の失敗")

    with capture_logs(broken_emit, [CAPTURED_LOGGER]):
        _worker_logger().info("これで落ちてはいけない")  # 例外が漏れないこと


def test_set_step_switches_target():
    """同じジョブ内でステップが進んだら転送先を切り替えられる。"""
    events: list[SupportEvent] = []
    with capture_logs(events.append, [CAPTURED_LOGGER], step="split") as handler:
        _worker_logger().info("分割中")
        handler.set_step("merge")
        _worker_logger().info("結合中")

    assert [(e.step, e.message) for e in events] == [
        ("split", "分割中"),
        ("merge", "結合中"),
    ]


def test_concurrent_jobs_do_not_mix():
    """**同時実行されたジョブのログが混ざらない。**

    ハンドラはロガー（プロセス全体）に付くため、スレッド ident で絞らないと
    相手のログが自分の進捗として流れる。
    """
    collected: dict[str, list[str]] = {"A": [], "B": []}

    def job(name: str, count: int) -> None:
        with capture_logs(
            lambda e, n=name: collected[n].append(e.message), [CAPTURED_LOGGER]
        ):
            for i in range(count):
                _worker_logger().info(f"{name}-{i}")
                time.sleep(0.005)

    threads = [
        threading.Thread(target=job, args=("A", 5)),
        threading.Thread(target=job, args=("B", 5)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert collected["A"] == [f"A-{i}" for i in range(5)]
    assert collected["B"] == [f"B-{i}" for i in range(5)]


def test_nested_capture_keeps_level_until_outermost_exits():
    """入れ子のとき、内側が抜けても外側がいる間は level が下がらない。

    ⚠️ **これは回帰テストではない。** 入れ子（後入れ先出し）では、素朴な
    「入るとき控えて出るとき書き戻す」実装でも**外側が最後に正しい値を戻す**ため
    偶然通ってしまう（素朴実装に当てて緑になることを確認済み）。
    参照カウントの回帰を捕まえるのは `test_sequenced_jobs_restore_level` の方。

    ここで担保しているのは「内側の離脱で早々に level を落とさない」ことだけ。
    """
    logger = logging.getLogger(CAPTURED_LOGGER)
    logger.setLevel(logging.NOTSET)

    with capture_logs(lambda _e: None, [CAPTURED_LOGGER]):
        assert logger.level == logging.INFO          # 外側が引き上げた
        with capture_logs(lambda _e: None, [CAPTURED_LOGGER]):
            assert logger.level == logging.INFO
        # 内側が抜けても外側がまだ中にいるので INFO のまま
        assert logger.level == logging.INFO, "内側の離脱で level を落としてはいけない"

    assert logger.level == logging.NOTSET, (
        f"level が復元されていない: {logger.level}（参照カウントの不具合）"
    )
    assert not any(isinstance(h, JobLogHandler) for h in logger.handlers)


def test_sequenced_jobs_restore_level():
    """**実行順を固定した 2 スレッドでも level が元へ戻る。**

    「A が入る → B が入る → A が出る → B が出る」を Event で強制する。
    素朴な実装ではこの順序のとき B が INFO を「元の値」として控えるため、
    最後に出る B が NOTSET ではなく INFO を書き戻してしまう。
    """
    logger = logging.getLogger(CAPTURED_LOGGER)
    logger.setLevel(logging.NOTSET)

    a_entered = threading.Event()
    b_entered = threading.Event()
    a_exited = threading.Event()

    def job_a() -> None:
        with capture_logs(lambda _e: None, [CAPTURED_LOGGER]):
            a_entered.set()             # (1) A が level を引き上げた
            b_entered.wait(timeout=5)   # (2) B が入るまで中にいる
        a_exited.set()                  # (3) A が先に出た

    def job_b() -> None:
        a_entered.wait(timeout=5)       # A の後に入る
        with capture_logs(lambda _e: None, [CAPTURED_LOGGER]):
            b_entered.set()
            a_exited.wait(timeout=5)    # (4) A が完全に出るまで中にいる

    threads = [threading.Thread(target=job_a), threading.Thread(target=job_b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert logger.level == logging.NOTSET, (
        f"level が復元されていない: {logger.level}（参照カウントの不具合）"
    )
    assert not any(isinstance(h, JobLogHandler) for h in logger.handlers)


def test_uncaptured_logger_is_ignored():
    """対象外のロガーは転送されない（uvicorn 等のログを拾わない）。"""
    events: list[SupportEvent] = []
    with capture_logs(events.append, [CAPTURED_LOGGER]):
        logging.getLogger("some.other.package").info("無関係なログ")

    assert events == []

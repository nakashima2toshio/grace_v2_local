"""celery_config.configure_worker_process（ワーカー起動時の初期化）のテスト。

ワーカープロセスが起動するたびに走り、Q/A 生成タスクが使うモジュールを
import できるかを確かめてログへ出す。以前は**削除済みの** ``qa_generation.generation``
を確かめていたため、正常なワーカーでも起動のたびに
``❌ [Worker] インポート失敗: No module named 'qa_generation.generation'`` が
ERROR で出ていた（2026-10-03 に結合テストでワーカーを立てて発見）。

実際の依存は ``celery_tasks._get_generator`` が読む
``qa_generation.smart_qa_generator.SmartQAGenerator`` である。
実 Redis / Celery ワーカー / API キーは不要（関数を直接呼ぶ）。
"""

import logging
import sys

import celery_config


def _run_init(monkeypatch, caplog):
    # configure_worker_process は sys.path へ挿入するので、テスト後に戻す
    monkeypatch.setattr(sys, "path", list(sys.path))
    with caplog.at_level(logging.INFO, logger=celery_config.logger.name):
        celery_config.configure_worker_process()
    return [r for r in caplog.records if r.name == celery_config.logger.name]


def test_worker_init_logs_no_error(monkeypatch, caplog):
    records = _run_init(monkeypatch, caplog)

    errors = [r.getMessage() for r in records if r.levelno >= logging.ERROR]
    assert errors == []


def test_worker_init_probes_the_generator_tasks_actually_use(monkeypatch, caplog):
    records = _run_init(monkeypatch, caplog)

    messages = "\n".join(r.getMessage() for r in records)
    assert "インポート成功: qa_generation.smart_qa_generator" in messages
    assert "qa_generation.generation" not in messages


def test_running_celery_config_as_script_probes_the_real_generator():
    """``python celery_config.py``（設定確認用の ``__main__``）も同じモジュールを確かめる。

    ワーカー起動時の確認（上の 2 件）は直してあったが、``__main__`` 側は
    削除済みの ``qa_generation.generation`` を見たまま残り、実行するたびに
    ``❌ qa_generation.generation: No module named ...`` を表示していた
    （2026-10-09 に修正。grace_v2 と同じ修正）。
    """
    import pathlib
    import subprocess

    root = pathlib.Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, "celery_config.py"],
        cwd=root, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "✅ qa_generation.smart_qa_generator.SmartQAGenerator" in proc.stdout
    assert "qa_generation.generation" not in proc.stdout

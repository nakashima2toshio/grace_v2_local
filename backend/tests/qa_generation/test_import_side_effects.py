"""`qa_generation` パッケージの import 副作用を固定するテスト。

背景: `qa_generation/__init__.py` は公開 API として `QAPipeline` を再エクスポート
するため、パッケージ内のどのモジュールを import しても `pipeline.py` が走る。
`pipeline.py` が `celery_tasks` をモジュールレベルで import していたころは、
`qa_generation.data_io`（pandas とファイル I/O しか使わない）を import しただけで
Celery 本体（amqp / billiard / kombu ほか）まで読み込まれていた
（実測 +117 モジュール・+7.7 秒）。

`pipeline._generate_with_celery()` 内の遅延 import へ移したので、その状態へ
戻っていないことを確かめる。Celery を「使う」経路の動作は変えていない。

実 LLM / Qdrant / Celery ワーカーは不要。サブプロセスを 1 つ起動して
`sys.modules` を見るだけ。
"""
import subprocess
import sys

_PROBE = """
import sys
import qa_generation.data_io  # noqa: F401
leaked = sorted(m for m in ("celery", "amqp", "billiard", "kombu") if m in sys.modules)
print(",".join(leaked))
"""

_PROBE_LAZY = """
import sys
from qa_generation.pipeline import QAPipeline  # noqa: F401
before = "celery" in sys.modules
import celery_tasks  # noqa: F401
print("%s,%s" % (before, "celery" in sys.modules))
"""


def _run(code: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return result.stdout.strip()


class TestImportSideEffects:
    """import しただけで Celery が載らないこと"""

    def test_data_io_import_does_not_pull_celery(self):
        """`import qa_generation.data_io` で Celery 系が読み込まれない。

        失敗する場合、`pipeline.py` のどこかに `celery_tasks` の
        モジュールレベル import が戻っている可能性が高い。
        """
        assert _run(_PROBE) == ""

    def test_celery_is_still_importable_on_demand(self):
        """遅延 import に落とした結果、Celery が使えなくなっていないこと。"""
        assert _run(_PROBE_LAZY) == "False,True"

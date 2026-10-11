"""`qa_generation` パッケージの import 副作用を固定するテスト。

背景: `qa_generation/__init__.py` は公開 API として `QAPipeline` を再エクスポート
するため、パッケージ内のどのモジュールを import しても `pipeline.py` が走る。
`pipeline.py` が `celery_tasks` をモジュールレベルで import していたころは、
`qa_generation.data_io`（pandas とファイル I/O しか使わない）を import しただけで
Celery 本体（amqp / billiard / kombu ほか）まで読み込まれていた
（実測 +117 モジュール・+7.7 秒）。

`pipeline._generate_with_celery()` 内の遅延 import へ移したので、その状態へ
戻っていないことを確かめる。Celery を「使う」経路の動作は変えていない。

もう 1 つ、`helper/*.py` の裸 import（`from helper_embedding import ...` のような
パッケージ名なしの import）が無いことを静的に検査する。裸 import は `helper/` が
`sys.path` に入っているとき（= `celery_tasks` を先に読んだとき）だけ解決する。
2026-10-11 まで `helper_embedding.py` の fastembed 分岐と `helper_embedding_fastembed.py` に
残っており、`create_embedding_client("fastembed")` は「fastembed が未導入」という
誤ったメッセージで必ず失敗していた（grace_v2 は 2026-09-25 に修正済み・同じ検査を移植）。

実 LLM / Qdrant / Celery ワーカーは不要。サブプロセスを 1 つ起動して
`sys.modules` を見るだけ。
"""
import ast
import subprocess
import sys
from pathlib import Path

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


_ROOT = Path(__file__).resolve().parents[2]


class TestHelperImports:
    """`helper/*.py` がパッケージ名なしで `helper_xxx` を import しないこと"""

    def test_helper_modules_have_no_bare_helper_imports(self):
        """裸 import は `helper/` が `sys.path` に入っているときだけ解決する。

        `from helper.helper_xxx import ...` と書く。
        """
        helper_modules = {p.stem for p in (_ROOT / "helper").glob("helper_*.py")}
        offenders = []
        for path in sorted((_ROOT / "helper").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module.split(".")[0]]
                elif isinstance(node, ast.Import):
                    names = [alias.name.split(".")[0] for alias in node.names]
                else:
                    continue
                for name in names:
                    if name in helper_modules:
                        offenders.append(f"{path.name}:{node.lineno} {name}")
        assert offenders == []

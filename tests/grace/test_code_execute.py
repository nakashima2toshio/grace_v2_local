"""P2 CodeExecuteTool（サンドボックス Python 実行）のテスト。

実サブプロセスを起動するが print 中心で高速。タイムアウトのみ短縮設定で検証。
"""

from grace.config import CodeExecuteConfig, GraceConfig, ToolsConfig
from grace.tools import CodeExecuteTool, ToolRegistry


def _cfg(timeout=3):
    return GraceConfig(
        tools=ToolsConfig(enabled=["rag_search", "reasoning", "code_execute"]),
        code_execute=CodeExecuteConfig(timeout_seconds=timeout),
    )


class TestCodeExecuteTool:
    def test_simple_success(self):
        tool = CodeExecuteTool(config=_cfg())
        result = tool.execute(code="print(2 + 2)")
        assert result.success is True
        assert "4" in result.output
        assert result.confidence_factors.get("returncode") == 0

    def test_code_via_query_fallback(self):
        tool = CodeExecuteTool(config=_cfg())
        result = tool.execute(query="print('hello')")
        assert result.success is True
        assert "hello" in result.output

    def test_empty_code_rejected(self):
        tool = CodeExecuteTool(config=_cfg())
        result = tool.execute(code="   ")
        assert result.success is False
        assert "コード" in (result.error or "")

    def test_denied_import_rejected(self):
        tool = CodeExecuteTool(config=_cfg())
        result = tool.execute(code="import socket\nprint('x')")
        assert result.success is False
        assert "socket" in (result.error or "")
        assert result.confidence_factors.get("rejected") is True

    def test_dangerous_attr_rejected(self):
        tool = CodeExecuteTool(config=_cfg())
        result = tool.execute(code="import os\nos.system('echo hi')")
        assert result.success is False
        assert "system" in (result.error or "")

    def test_eval_rejected(self):
        tool = CodeExecuteTool(config=_cfg())
        result = tool.execute(code="print(eval('1+1'))")
        assert result.success is False
        assert "eval" in (result.error or "")

    def test_syntax_error_rejected(self):
        tool = CodeExecuteTool(config=_cfg())
        result = tool.execute(code="print(")
        assert result.success is False
        assert "SyntaxError" in (result.error or "")

    def test_runtime_error_reports_stderr(self):
        tool = CodeExecuteTool(config=_cfg())
        result = tool.execute(code="raise ValueError('boom')")
        assert result.success is False
        assert "boom" in (result.error or "") or "ValueError" in (result.error or "")

    def test_cpu_or_wall_timeout(self):
        """無限ループは CPU 制限(SIGXCPU/-9)か実時間タイムアウトで停止する。"""
        tool = CodeExecuteTool(config=_cfg(timeout=1))
        result = tool.execute(code="while True:\n    pass")
        assert result.success is False
        cf = result.confidence_factors
        # 実時間タイムアウト or resource 制限による異常終了（負の returncode）
        assert cf.get("timed_out") is True or (cf.get("returncode", 0) or 0) < 0


class TestRegistryOptIn:
    def test_code_execute_opt_in(self):
        # 既定（code_execute 無効）では未登録
        reg_default = ToolRegistry(config=GraceConfig())
        assert reg_default.get("code_execute") is None
        # 有効化すると登録される
        reg_enabled = ToolRegistry(config=_cfg())
        assert reg_enabled.get("code_execute") is not None

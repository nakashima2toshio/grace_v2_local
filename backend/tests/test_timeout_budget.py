# backend/tests/test_timeout_budget.py
"""タイムアウト予算（LLM ↔ ステップ ↔ ツール）の不変条件を固定するテスト。

## 何を守っているのか

ローカル LLM（Ollama）は 1 呼び出しに 90〜250 秒かかる。この前提が崩れると
次の 2 つの壊れ方をする。実測で 1 リクエスト 34 分の「止まって見える」状態を
起こした 3 つの欠陥を、ここで回帰として固定する。

1. **下位に期限が無い** — `OllamaClient` が openai SDK の既定
   （600 秒 × リトライ 2 回 = 最悪 30 分）で走る。
2. **予算の逆転** — ステップ側の期限が LLM 側より短いと、ステップが先に
   諦めて HTTP だけが生き残る。捨てた生成が Ollama の GPU を占有し続け、
   後続をさらに遅らせる（＝タイムアウトするほど遅くなる正のフィードバック）。
   ⚠️ 比較すべきは `llm.timeout` 単体ではなく **リトライ込みの総予算**
   `timeout × (max_retries + 1)`。ここを単体で比較していたため、
   180s × 2 = 360s が step_timeout 240s を追い越し、2 回目のリクエストが
   必ず途中で殺される（60 秒を捨てるだけ）状態になっていた。
3. **見捨てたスレッドが非デーモン** — `ThreadPoolExecutor` のワーカーは
   インタプリタ終了時に join されるので、Ctrl-C / uvicorn 停止がハングする。

⚠️ 実際の Ollama サーバへは接続しない。
"""
from __future__ import annotations

import threading
import time

import httpx
import pytest
import yaml

from grace.config import GraceConfig, LLMConfig, PlannerConfig, WebSearchConfig
from grace.executor import _start_with_deadline
from grace.llm_compat import OllamaGenaiClient, create_chat_client
from grace.schemas import PlanStep
from helper.helper_llm import (
    DEFAULT_OLLAMA_MAX_RETRIES,
    DEFAULT_OLLAMA_TIMEOUT,
    OllamaClient,
)

CONFIG_YML = "config/grace_config.yml"


# =============================================================================
# ① LLM クライアント自身が有限の期限を持つ
# =============================================================================

class TestOllamaClientDeadline:
    """openai SDK の既定（600 秒 × 3 回）に落ちていないこと。"""

    def test_default_timeout_is_finite_and_not_sdk_default(self):
        client = OllamaClient()
        timeout = client.client.timeout

        assert isinstance(timeout, httpx.Timeout)
        assert timeout.read is not None, "read タイムアウトが無い＝無期限"
        # openai SDK の既定は 600 秒。そこへ落ちていたら回帰。
        assert timeout.read < 600, f"SDK 既定 600 秒に落ちている: {timeout.read}"
        assert timeout.read == DEFAULT_OLLAMA_TIMEOUT

    def test_connect_timeout_is_short(self):
        """Ollama 未起動を read タイムアウト分待たずに検出できること。"""
        client = OllamaClient()
        assert client.client.timeout.connect <= 10

    def test_sdk_retries_are_disabled(self):
        """SDK 層の自動リトライを持たないこと。

        openai SDK の timeout は 1 リクエストあたりの期限なので、リトライが
        あると実費は `timeout × (max_retries + 1)` になる。ローカル LLM の
        timeout は「モデルが遅い／終端できない」ことが原因で、同じプロンプトを
        投げ直しても結果は同じ。再試行は呼び出し側（planner の retry /
        executor の fallback / ReasoningTool の最小プロンプト再試行）が持つ。
        """
        client = OllamaClient()
        assert client.client.max_retries == 0, (
            "SDK 層でリトライすると総予算が step_timeout を追い越し、"
            "2 回目のリクエストが必ず途中で殺される"
        )

    def test_timeout_is_overridable(self):
        client = OllamaClient(timeout=42)
        assert client.client.timeout.read == 42


# =============================================================================
# ② config.llm.timeout が実際に配線されている（死に設定でない）
# =============================================================================

class TestTimeoutIsWired:

    def test_create_chat_client_passes_llm_timeout(self):
        config = GraceConfig(llm=LLMConfig(timeout=77))
        client = create_chat_client(config)

        assert isinstance(client, OllamaGenaiClient)
        # 遅延生成なので、実クライアントを作らせてから確認する
        assert client._ensure_client().timeout == 77

    def test_yaml_llm_timeout_reaches_the_http_client(self):
        """grace_config.yml の値が HTTP クライアントまで届くこと。"""
        with open(CONFIG_YML, encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        expected = raw["llm"]["timeout"]
        client = create_chat_client(GraceConfig(llm=LLMConfig(timeout=expected)))
        assert client._ensure_client().client.timeout.read == expected


# =============================================================================
# ③ 予算の逆転を許さない（LLM < ステップ < ツール）
# =============================================================================

class TestBudgetOrdering:
    """**下位ほど先に諦める**。逆転するとゾンビリクエストが生まれる。"""

    def test_class_defaults_llm_budget_shorter_than_step(self):
        """⚠️ 比較するのは `timeout` 単体ではなく **リトライ込みの総予算**。

        openai SDK の timeout は 1 リクエストあたりの期限なので、実費は
        `timeout × (max_retries + 1)` になる。ここを timeout 単体で比較して
        いたため、max_retries=1 のとき実費 360s がステップ期限 240s を
        追い越し、2 回目のリクエストが必ず途中で殺されていた（実測ログ:
        14:57:31 開始 → 15:00:31 Retrying → 15:01:31 step timeout）。
        """
        budget = LLMConfig().timeout * (DEFAULT_OLLAMA_MAX_RETRIES + 1)
        assert budget < PlannerConfig().step_timeout_seconds, (
            f"LLM 総予算 {budget}s が step_timeout "
            f"{PlannerConfig().step_timeout_seconds}s を超えている"
        )

    def test_yaml_llm_budget_shorter_than_step(self):
        with open(CONFIG_YML, encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        budget = raw["llm"]["timeout"] * (DEFAULT_OLLAMA_MAX_RETRIES + 1)
        assert budget < raw["planner"]["step_timeout_seconds"]

    def test_step_timeout_accommodates_local_llm(self):
        """9B 級モデルの実測（90〜250 秒）を吸収できる長さであること。

        旧既定の 30 秒だと reasoning が構造的に必ずタイムアウトし、
        replan ループへ落ちて終わらなくなる。
        """
        assert PlannerConfig().step_timeout_seconds >= 120

    def test_web_search_budget_covers_tool_retries(self):
        """動的 web_search ステップの期限が、ツール自身の予算を下回らないこと。

        下回ると **リトライの途中で必ず打ち切られて 0 件**になり、
        「情報なし回答 → 誤エスカレ」の連鎖になる（旧実装は固定 15 秒）。
        """
        from grace.executor import Executor

        cfg = GraceConfig(
            web_search=WebSearchConfig(timeout=30, max_retries=3, retry_backoff_seconds=2.0)
        )
        budget = Executor._web_search_budget_seconds.__get__(
            type("_", (), {"config": cfg})()
        )()

        # 30×3 ＋ バックオフ(2+4) = 96 秒が下限
        assert budget >= 96


# =============================================================================
# ④ 見捨てたスレッドがプロセスの終了をブロックしない
# =============================================================================

class TestDeadlineThreads:

    def test_worker_is_daemon(self):
        """非デーモンだと `_python_exit()` が join し、終了がハングする。"""
        pending = _start_with_deadline(lambda: time.sleep(0.05), {}, "unit")
        assert pending.thread.daemon is True
        pending.wait(1.0)

    def test_returns_value_when_finished(self):
        pending = _start_with_deadline(lambda x: x * 2, {"x": 21}, "unit")
        assert pending.wait(1.0) is True
        assert pending.result() == 42

    def test_reraises_exception(self):
        def _boom():
            raise ValueError("boom")

        pending = _start_with_deadline(_boom, {}, "unit")
        assert pending.wait(1.0) is True
        with pytest.raises(ValueError, match="boom"):
            pending.result()

    def test_wait_reports_false_while_running(self):
        done = threading.Event()
        pending = _start_with_deadline(lambda: done.wait(5), {}, "unit")
        try:
            assert pending.wait(0.05) is False
        finally:
            done.set()
            pending.wait(5)

    def test_no_threadpool_executor_left_in_executor(self):
        """ThreadPoolExecutor へ戻っていないこと（非デーモン回帰の検出）。

        コメント中の言及は許す（なぜ使わないかを書いてある）。
        **import 文＝実使用**だけを禁止する。
        """
        from pathlib import Path

        source = Path("grace/executor.py").read_text(encoding="utf-8")
        code_lines = [
            line for line in source.splitlines()
            if not line.lstrip().startswith("#")
        ]
        offenders = [
            line for line in code_lines
            if "import" in line and "concurrent.futures" in line
        ]
        assert not offenders, f"concurrent.futures を再導入している: {offenders}"


# =============================================================================
# ⑤ ReAct ステップがタイムアウトをハードコードしない
# =============================================================================

class TestReactStepTimeout:

    def test_react_steps_use_configured_timeout(self):
        """`timeout_seconds=30` のハードコードが残っていないこと。"""
        from pathlib import Path

        source = Path("grace/executor.py").read_text(encoding="utf-8")
        assert "timeout_seconds=30," not in source
        assert "timeout_seconds=15," not in source

    def test_planstep_accepts_configured_value(self):
        step = PlanStep(
            step_id=1,
            action="reasoning",
            description="d",
            expected_output="o",
            timeout_seconds=PlannerConfig().step_timeout_seconds,
        )
        assert step.timeout_seconds == 240

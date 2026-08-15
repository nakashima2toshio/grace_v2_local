# backend/tests/test_local_llm_degradation.py
"""ローカル LLM 特有の「壊れ方」に対する防御を固定するテスト。

クラウド LLM を前提に書かれたコードをローカル（Ollama）で回すと、
次の 3 つが**沈黙したまま**パイプラインを狂わせる。

1. **思考タグ** — qwen3.5 等は `<think>…</think>` を本文の前に出す。
   呼び出しサイトは `response.text` をそのまま回答 / JSON / 数値として扱うため、
   思考中の数字や波括弧を拾って誤動作する。
2. **空応答** — 思考で出力枠を使い切ると本文が 0 文字になる。以前はこれを
   `success=True` で通していたため、フォールバックも replan も起動しなかった。
3. **補助判定の総コスト** — 1 語だけ返させる判定が、ローカルでは 1 件
   90〜250 秒。失敗時はキーワード判定へ落ちるだけなので、切れる必要がある。

⚠️ 実際の Ollama サーバへは接続しない。
"""
from __future__ import annotations

from types import SimpleNamespace

from grace.config import GraceConfig, JudgeConfig
from grace.llm_compat import OllamaGenaiClient, _strip_think


class _StubClient:
    """OllamaClient.generate_content のスタブ。返す文字列を固定する。"""

    def __init__(self, text: str):
        self._text = text
        self.calls = 0

    def generate_content(self, prompt, **kwargs):
        self.calls += 1
        return self._text


def _through_compat(text: str, config: dict | None = None) -> str:
    """llm_compat の Ollama 経路を通した後の `response.text` を返す。"""
    client = OllamaGenaiClient(default_model="qwen3.5:9b")
    client._client = _StubClient(text)
    return client.models.generate_content(contents="q", config=config).text


# =============================================================================
# ① 思考タグの除去
# =============================================================================

class TestStripThink:

    def test_removes_closed_think_block(self):
        assert _strip_think("<think>まず考える</think>答えは A です") == "答えは A です"

    def test_removes_multiline_block(self):
        text = "<think>\n1行目\n2行目\n</think>\n本文"
        assert _strip_think(text) == "本文"

    def test_handles_thinking_tag_variant(self):
        assert _strip_think("<thinking>x</thinking>本文") == "本文"

    def test_unterminated_think_yields_empty(self):
        """閉じタグ前に枠を使い切った＝本文へ到達していない → 空扱い。

        中途半端な思考を回答として通すと、そのまま画面に出てしまう。
        """
        assert _strip_think("<think>ずっと考えている途中で枠が尽きた") == ""

    def test_leaves_plain_text_untouched(self):
        assert _strip_think("ふつうの回答") == "ふつうの回答"

    def test_empty_input(self):
        assert _strip_think("") == ""

    def test_applied_in_ollama_compat_path(self):
        assert _through_compat("<think>内緒</think>答え") == "答え"


class TestThinkStrippedBeforeJson:
    """思考の中身が JSON 抽出を汚さないこと。"""

    def test_json_inside_think_is_not_picked_up(self):
        """`<think>` 内のサンプル JSON ではなく、本物を返すこと。

        `_strip_to_json` は最初の `{` から拾うため、除去順を誤ると
        思考中の例をパースしてしまう。
        """
        raw = '<think>例えば {"score": 0.1} かな</think>{"score": 0.9}'
        text = _through_compat(raw, {"response_mime_type": "application/json"})
        assert text == '{"score": 0.9}'

    def test_score_is_not_taken_from_thinking(self):
        """`parse_score` が思考中の数字を拾わないこと。"""
        from grace.llm_compat import parse_score

        raw = "<think>0.1 くらい？いや違う</think>0.9"
        assert parse_score(_through_compat(raw)) == 0.9


# =============================================================================
# ② 空応答を成功にしない
# =============================================================================

class TestReasoningEmptyAnswer:

    def _tool(self, text: str):
        from grace.tools import ReasoningTool

        tool = ReasoningTool.__new__(ReasoningTool)
        tool.config = GraceConfig()
        tool.model_name = "qwen3.5:9b"
        tool.client = SimpleNamespace(
            models=SimpleNamespace(
                generate_content=lambda **_kw: SimpleNamespace(
                    text=text, usage_metadata=SimpleNamespace()
                )
            )
        )
        return tool

    def test_empty_answer_is_a_failure(self):
        """`success=True` で空文字を下流へ流さないこと。

        以前は "Reasoning completed: 0 chars" とログに出るだけで成功扱いになり、
        フォールバック連鎖も replan も起動しなかった。
        """
        result = self._tool("").execute(query="Q")
        assert result.success is False
        assert result.output is None
        assert "空" in (result.error or "")

    def test_whitespace_only_answer_is_a_failure(self):
        result = self._tool("   \n\t  ").execute(query="Q")
        assert result.success is False

    def test_none_answer_is_a_failure(self):
        result = self._tool(None).execute(query="Q")
        assert result.success is False

    def test_real_answer_still_succeeds(self):
        result = self._tool("これが回答です").execute(query="Q")
        assert result.success is True
        assert result.output == "これが回答です"


# =============================================================================
# ③ 補助 LLM 判定を切れる
# =============================================================================

class TestJudgeSwitch:

    def _config(self, enabled: bool) -> GraceConfig:
        return GraceConfig(judges=JudgeConfig(enabled=enabled))

    def test_defaults_to_disabled_for_local_llm(self):
        """本リポジトリはローカル LLM 専用なので既定は「切」。

        実測（gemma4:26b-a4b-it-qat）では補助判定が 8 回以上呼ばれ、その
        すべてが finish_reason=length の空応答で捨てられていた。1 件
        90〜250 秒なので約 13 分の純粋な待ち時間。精度を優先したい場合
        だけ `config/grace_config.yml` の judges で opt-in する。
        """
        assert JudgeConfig().enabled is False
        assert JudgeConfig().step_confidence_llm is False

    def test_yaml_keeps_judges_disabled(self):
        """クラス既定だけ直しても YAML が true なら効かないので併せて固定する。"""
        import yaml

        with open("config/grace_config.yml", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        assert raw["judges"]["enabled"] is False
        assert raw["judges"]["step_confidence_llm"] is False

    def test_intent_classifier_returns_none_without_llm(self):
        from backend.app.core.gates import create_intent_classifier

        classify = create_intent_classifier(self._config(False))
        assert classify("パスワードを再設定したい") is None

    def test_no_info_judge_returns_none_without_llm(self):
        from backend.app.core.gates import create_no_info_judge

        judge = create_no_info_judge(self._config(False))
        assert judge("Q", "A") is None

    def test_mention_classifier_returns_none_without_llm(self):
        from backend.app.core.review_gates import create_mention_classifier

        classify = create_mention_classifier(self._config(False))
        assert classify("業界No.1です") is None

    def test_vacuous_judge_returns_none_without_llm(self):
        from backend.app.core.review_gates import create_vacuous_judge

        judge = create_vacuous_judge(self._config(False))
        assert judge("特に問題ありません") is None

    def test_config_stub_without_judges_section_stays_enabled(self):
        """`judges` を持たない既存の config スタブを壊さないこと。

        属性が無い（＝意思表示が無い）スタブは従来どおり「有効」に倒す。
        実 `GraceConfig` は既定 false なので、両者を取り違えないよう
        同じテストで並べて固定する。
        """
        from backend.app.core.gates import judges_enabled

        assert judges_enabled(SimpleNamespace()) is True
        assert judges_enabled(GraceConfig()) is False
        assert judges_enabled(self._config(False)) is False
        assert judges_enabled(self._config(True)) is True


# =============================================================================
# ④ 判定系の出力枠が thinking を吸収できる
# =============================================================================

class TestJudgeTokenBudget:

    def test_judge_budget_is_not_ten(self):
        """枠 10 は thinking 系モデルで必ず空応答になる。"""
        from backend.app.core.verticals import JUDGE_MAX_OUTPUT_TOKENS

        assert JUDGE_MAX_OUTPUT_TOKENS >= 128

    def test_no_ten_token_budget_left_in_gates(self):
        from pathlib import Path

        for path in ("backend/app/core/gates.py", "backend/app/core/review_gates.py"):
            source = Path(path).read_text(encoding="utf-8")
            assert '"max_output_tokens": 10' not in source, path

    def test_complexity_budget_accommodates_thinking(self):
        from grace.config import PlannerConfig

        assert PlannerConfig().complexity_max_output_tokens >= 128

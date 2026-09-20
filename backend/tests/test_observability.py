# backend/tests/test_observability.py
"""**後から原因を切り分けられる**ようにする観測の追加を固定するテスト。

## 背景（grace_v2_local で実測、本リポジトリにも同じコードが残っていた）

### ① 矛盾と判定された主張が追えなかった

実測ログにはこうとしか出ていなかった:

    [groundedness] supported=2 / total=3 / contradiction=True / verified=True
    Groundedness contradiction detected (supported=2, contradicted=1);
      capping answer_conf at 0.300

矛盾 1 件で `answer_conf` が 0.30 に cap されるのに、**どの主張が矛盾と判定
されたのかがどこにも出ていない**ため、

  - 回答が本当に情報源と矛盾していた（正しい検知）のか
  - 検証器の誤検知で、正しい回答の信頼度を不当に下げたのか

を後から切り分けられなかった。`GroundednessVerifier` は LLM から主張ごとの
判定（`ClaimVerdict`）を受け取っているのに、件数だけ集計して中身を捨てていた。

### ② reasoning プロンプトに現在日時が無かった

「明日の東京の天気は？」に対し、Web 検索は予報を取得済み・groundedness も
1.00 だったのに、回答が「『明日』が具体的にいつを指すのか定義が不足している
ため確定した情報を提示できません」になっていた。

LLM は今日が何日かを知らない。日付が無いと相対表現（明日・今週・先月）を
解決できず、参照情報に答えがあっても取り出せない。groundedness 満点・出典
あり・本文ありなので、**どのゲートでも弾けない**。

⚠️ これは推論が正しくなる修正であって、答えが増える修正ではない。情報源に
明日の予報が無ければ「無い」が正しい回答になる。誤った日付の予報を明日として
出すよりそちらが正しい。

ここで固定すること:
  1. 主張ごとの判定が結果に残ること
  2. 矛盾主張がログ・ステップイベントへ出ること（件数だけにしない）
  3. プロンプトに今日と明日の日付が入ること（明日は計算して渡す）
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from backend.app.core.gates import _contradicted_claims
from backend.app.core.support_agent import (
    AUTO_PROCEED,
    SupportEvent,
    run_support_agent_core,
)
from backend.tests.conftest import GroundednessStub
from grace.tools import ReasoningTool

QUERY = "明日の東京の天気は？"


def collect(events):
    return lambda e: events.append(e)


def _claim(text, verdict):
    return SimpleNamespace(claim=text, verdict=verdict)


# =============================================================================
# ① 矛盾主張を取り出す
# =============================================================================

class TestContradictedClaims:

    def test_only_contradicted_claims_are_returned(self):
        gres = SimpleNamespace(claims=[
            _claim("東京は晴れます", "supported"),
            _claim("明日の最高気温は 35 度です", "contradicted"),
            _claim("傘は不要です", "neutral"),
        ])

        assert _contradicted_claims(gres) == ["明日の最高気温は 35 度です"]

    def test_whitespace_is_flattened(self):
        gres = SimpleNamespace(claims=[_claim("明日は\n  雨です", "contradicted")])

        assert _contradicted_claims(gres) == ["明日は 雨です"]

    def test_long_claims_are_truncated(self):
        gres = SimpleNamespace(claims=[_claim("あ" * 500, "contradicted")])

        [out] = _contradicted_claims(gres)
        assert len(out) <= 161
        assert out.endswith("…")

    def test_count_is_capped(self):
        gres = SimpleNamespace(claims=[
            _claim(f"主張{i}", "contradicted") for i in range(10)
        ])

        assert len(_contradicted_claims(gres)) == 5

    def test_empty_claims_are_skipped(self):
        gres = SimpleNamespace(claims=[_claim("   ", "contradicted")])

        assert _contradicted_claims(gres) == []

    def test_result_without_claims_does_not_crash(self):
        """旧シリアライズやテスト用スタブでも落ちないこと。"""
        assert _contradicted_claims(SimpleNamespace()) == []
        assert _contradicted_claims(SimpleNamespace(claims=None)) == []


# =============================================================================
# ② 実行記録へ出る
# =============================================================================

class TestClaimsReachTheRunLog:

    def _confidence_event(self, events):
        return [e for e in events if e.type == "step"
                and e.step == "confidence" and e.status == "finished"][0]

    def test_contradicted_claim_appears_in_the_step_log(self, pipeline_stub):
        pipeline_stub.groundedness = GroundednessStub(
            support_rate=0.67, supported=2, contradicted=1, total=3,
            verified=True, has_contradiction=True,
        )
        pipeline_stub.groundedness.claims = [
            _claim("東京は晴れます", "supported"),
            _claim("明日の最高気温は 35 度です", "contradicted"),
        ]
        events: list[SupportEvent] = []
        run_support_agent_core(
            QUERY, emit=collect(events), confirm=lambda _r: AUTO_PROCEED,
        )

        logs = [e.message for e in events
                if e.type == "log" and "矛盾と判定された主張" in e.message]
        assert any("最高気温は 35 度" in m for m in logs), (
            "件数だけでは誤検知か本物かを切り分けられない"
        )
        assert self._confidence_event(events).data["contradicted_claims"] == [
            "明日の最高気温は 35 度です"
        ]

    def test_no_contradiction_keeps_the_log_clean(self, pipeline_stub):
        events: list[SupportEvent] = []
        run_support_agent_core(
            QUERY, emit=collect(events), confirm=lambda _r: AUTO_PROCEED,
        )

        assert not [e for e in events
                    if e.type == "log" and "矛盾と判定された主張" in e.message]
        assert self._confidence_event(events).data["contradicted_claims"] == []


# =============================================================================
# ③ プロンプトに現在日時が入る
# =============================================================================

class TestCurrentDateInPrompt:

    def test_today_and_tomorrow_are_both_given(self):
        text = ReasoningTool._now_text(datetime(2026, 8, 16, 14, 30))

        assert "今日は 2026年08月16日" in text
        assert "「明日」は 2026年08月17日" in text

    def test_weekday_is_japanese(self):
        text = ReasoningTool._now_text(datetime(2026, 8, 16, 9, 0))  # 日曜

        assert "（日曜日）" in text
        assert "（月曜日）" in text   # 8/17 は月曜

    def test_month_end_rolls_over(self):
        """明日を LLM に計算させない理由（月末をまたぐ）。"""
        text = ReasoningTool._now_text(datetime(2026, 8, 31, 23, 0))

        assert "「明日」は 2026年09月01日" in text

    def test_year_end_rolls_over(self):
        text = ReasoningTool._now_text(datetime(2026, 12, 31, 10, 0))

        assert "「明日」は 2027年01月01日" in text

    def test_relative_expressions_are_explained(self):
        text = ReasoningTool._now_text(datetime(2026, 8, 16))

        assert "相対的な日付表現" in text

    def test_prompt_carries_the_datetime_block(self):
        prompt = ReasoningTool()._build_prompt(QUERY, None, None)

        assert "### 【現在日時】" in prompt
        assert "今日は" in prompt

    def test_datetime_comes_before_the_sources(self):
        """参照情報より前に置く（読み替えの基準を先に示す）。"""
        source = {
            "collection": "web_search",
            "score": 0.7,
            "payload": {"content": "予報です", "source": "https://example.com/w"},
        }
        prompt = ReasoningTool()._build_prompt(QUERY, None, [source])

        assert prompt.index("### 【現在日時】") < prompt.index("### 【参照情報】")

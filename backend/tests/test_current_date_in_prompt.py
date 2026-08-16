# backend/tests/test_current_date_in_prompt.py
"""reasoning プロンプトが **現在日付** を含むことを固定するテスト。

## 背景（実測）

質問「明日の東京の天気は？」に対し、Web 検索は 8/16 の予報を取ってきており、
groundedness も 1.00（＝述べた内容は情報源に忠実）だった。にもかかわらず
回答は次のようになった:

    「明日」という日付が具体的にいつを指すのかについての定義が不足している
    ため、確定した情報を提示することができませんでした

LLM は「今日が何日か」を知らない。日付を渡していなかったので、参照情報に
答えがあっても相対表現（明日・今週・先月）を解決できなかった。

**ゲートでは弾けない類の不具合である。** groundedness は満点、出典もあり、
回答も空でない。落ちているのは「入力プロンプトの欠落」だけなので、
ここを固定しないと同じ回帰が静かに戻ってくる。

## 「明日」を LLM に計算させない理由

月末・年末をまたぐケースを LLM は落とす。こちら側で `timedelta(days=1)` を
計算して**明示的に渡す**（その境界も下でテストする）。

⚠️ LLM にも Qdrant にも接続しない（`_build_prompt` は純粋な文字列組み立て）。
"""
from __future__ import annotations

from datetime import datetime

import pytest

from grace.config import GraceConfig
from grace.tools import ReasoningTool

# =============================================================================
# ① 日時文字列そのもの
# =============================================================================

class TestNowText:

    def test_contains_today_and_tomorrow(self):
        text = ReasoningTool._now_text(datetime(2026, 8, 16, 14, 53))

        assert "2026年08月16日" in text, "今日の日付が無い"
        assert "2026年08月17日" in text, "明日の日付が無い"

    def test_weekday_is_japanese_and_correct(self):
        # 2026-08-16 は日曜日
        text = ReasoningTool._now_text(datetime(2026, 8, 16, 14, 53))

        assert "（日曜日）" in text
        assert "（月曜日）" in text  # 翌 8/17

    @pytest.mark.parametrize(
        "date,weekday",
        [
            (datetime(2026, 8, 10), "月"),
            (datetime(2026, 8, 11), "火"),
            (datetime(2026, 8, 12), "水"),
            (datetime(2026, 8, 13), "木"),
            (datetime(2026, 8, 14), "金"),
            (datetime(2026, 8, 15), "土"),
            (datetime(2026, 8, 16), "日"),
        ],
    )
    def test_weekday_mapping_covers_the_whole_week(self, date, weekday):
        """`_WEEKDAYS_JA` の並びが `datetime.weekday()`（月=0）と一致すること。"""
        assert f"今日は {date:%Y年%m月%d日}（{weekday}曜日）" in ReasoningTool._now_text(date)

    def test_crosses_month_boundary(self):
        text = ReasoningTool._now_text(datetime(2026, 8, 31, 9, 0))

        assert "今日は 2026年08月31日" in text
        assert "「明日」は 2026年09月01日" in text

    def test_crosses_year_boundary(self):
        """年末は「明日 = 12月32日」のような破綻が起きやすい。"""
        text = ReasoningTool._now_text(datetime(2026, 12, 31, 23, 0))

        assert "今日は 2026年12月31日" in text
        assert "「明日」は 2027年01月01日" in text

    def test_handles_leap_day(self):
        text = ReasoningTool._now_text(datetime(2028, 2, 28, 12, 0))
        assert "「明日」は 2028年02月29日" in text

    def test_includes_time_of_day(self):
        """「今日の夜」等のために時刻も渡す。"""
        assert "14:53" in ReasoningTool._now_text(datetime(2026, 8, 16, 14, 53))

    def test_defaults_to_now(self):
        """引数なしなら実時刻を使うこと（本番経路はこちら）。"""
        now = datetime.now()
        assert f"{now:%Y年%m月%d日}" in ReasoningTool._now_text()

    def test_explains_how_to_use_it(self):
        """日付を置くだけでなく、相対表現を読み替えるよう指示すること。"""
        text = ReasoningTool._now_text(datetime(2026, 8, 16))
        assert "明日" in text and "今週" in text


# =============================================================================
# ② プロンプトに実際に載っていること
# =============================================================================

class TestPromptContainsDate:

    def test_prompt_has_the_date_section(self):
        prompt = _build_prompt("明日の東京の天気は？")

        assert "【現在日時】" in prompt, "reasoning プロンプトに現在日時が入っていない"
        assert f"{datetime.now():%Y年%m月%d日}" in prompt

    def test_date_comes_before_the_sources(self):
        """参照情報を読む前に基準日を知っている必要がある。"""
        prompt = _build_prompt(
            "明日の東京の天気は？",
            sources=[{"score": 0.9, "payload": {"answer": "16日は晴れ", "source": "web"}}],
        )

        # ⚠️ 冒頭のシステム指示にも「【参照情報】」の語が出るので、
        #    見出し（`### 【参照情報】`）で位置を取る。
        assert prompt.index("### 【現在日時】") < prompt.index("### 【参照情報】")

    def test_date_is_present_even_without_sources(self):
        """出典 0 件でも日付は渡す（Web フォールバック前の経路）。"""
        assert "【現在日時】" in _build_prompt("明日の天気は？", sources=[])

    def test_does_not_break_the_existing_sections(self):
        """既存の構成（参照情報・質問・ルール）を壊していないこと。"""
        prompt = _build_prompt(
            "明日の東京の天気は？",
            context="前ステップの結果",
            sources=[{"score": 0.9, "payload": {"answer": "16日は晴れ", "source": "web"}}],
        )

        for section in ("【参照情報】", "【補足コンテキスト】", "【ユーザーの質問】",
                        "【回答の構成ルール（最重要）】"):
            assert section in prompt, f"{section} が消えている"


# =============================================================================
# helpers
# =============================================================================

def _build_prompt(query: str, *, context=None, sources=None) -> str:
    """LLM クライアントを作らずに `_build_prompt` だけを回す。"""
    tool = ReasoningTool.__new__(ReasoningTool)
    tool.config = GraceConfig()
    return tool._build_prompt(query, context, sources)

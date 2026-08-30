# backend/tests/test_reasoning_prompt_order.py
"""reasoning プロンプトの**並び順**を固定する（`grace/tools.py::_build_prompt`）。

## 背景（実測 2026-08-30）

担当範囲外の断り指示を【業務方針（遵守）】＝参照情報の手前で渡していたとき、
モデルが 2 回連続でそれを無視した:

    03:00  回答は住民票の説明で終わり、断り無し
    04:07  同上（同一の注入・同一のモデル）

プロンプトの後半には【回答の構成ルール（**最重要**）】があり、
「参照情報にある事実のみ」「捏造禁止」と書かれている。断りの指示はそれより
前にあるうえ、内容が衝突して見える。**位置で負けていた**と考えられる。

そこで「必ず書く」類の指示は `llm.prompt_closing` として構成ルールの**後ろ**へ
置くことにした。ここではその並び順だけを固定する。

⚠️ **モデルが従うかどうかはここでは検証できない**（LLM を呼ばない）。
実機での確認が別途必要。順序が崩れていないことだけを保証する。
"""
from __future__ import annotations

from types import SimpleNamespace

from grace.tools import ReasoningTool

ADDENDUM = "条例・公式案内に基づき、断定を避けること。"
CLOSING = "【この問い合わせに含まれる担当範囲外の質問】\n- 明日の東京の天気は？"


def _tool(*, addendum: str = "", closing: str = "") -> ReasoningTool:
    tool = ReasoningTool.__new__(ReasoningTool)      # __init__ を通さず最小構成
    tool.config = SimpleNamespace(
        llm=SimpleNamespace(prompt_addendum=addendum, prompt_closing=closing),
    )
    return tool


def _prompt(**kwargs) -> str:
    return _tool(**kwargs)._build_prompt("住民票の写しの取り方は？", None, None)


class TestClosingPosition:
    def test_構成ルールの後ろに置かれる(self):
        prompt = _prompt(addendum=ADDENDUM, closing=CLOSING)
        rules = prompt.index("【回答の構成ルール（最重要）】")
        closing = prompt.index("【この回答で必ず守ること】")
        assert rules < closing, (
            "断りの指示が構成ルールより前にある"
            "（実測ではこの位置でモデルに 2 回連続で無視された）"
        )

    def test_業務方針は構成ルールより前のまま(self):
        """業界方針は参照情報の読み方に効くので、位置は変えない。"""
        prompt = _prompt(addendum=ADDENDUM, closing=CLOSING)
        assert prompt.index("【業務方針（遵守）】") < prompt.index("【回答の構成ルール（最重要）】")

    def test_最後の指示は末尾側にある(self):
        prompt = _prompt(addendum=ADDENDUM, closing=CLOSING)
        assert prompt.index("【この回答で必ず守ること】") > prompt.index("【ユーザーの質問】")

    def test_本文がそのまま入る(self):
        prompt = _prompt(closing=CLOSING)
        assert "明日の東京の天気は？" in prompt


class TestNoClosing:
    def test_空なら見出しごと出さない(self):
        """範囲外が無い問い合わせでプロンプトを膨らませない。"""
        prompt = _prompt(addendum=ADDENDUM)
        assert "【この回答で必ず守ること】" not in prompt

    def test_属性が無い設定でも動く(self):
        """`prompt_closing` を持たない config スタブでも壊れない（後方互換）。"""
        tool = ReasoningTool.__new__(ReasoningTool)
        tool.config = SimpleNamespace(llm=SimpleNamespace(prompt_addendum=""))
        prompt = tool._build_prompt("質問", None, None)
        assert "【回答の構成ルール（最重要）】" in prompt

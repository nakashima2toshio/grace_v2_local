# backend/tests/test_parse_score.py
"""`grace.llm_compat.parse_score` のテスト。

ローカル LLM（Ollama）は「数値のみを出力してください」と指示しても
「答えは 0.8 です。」のように前置きを付けて返すことがある。従来の
`float(text)` 直変換は ValueError になり、呼び出し側の既定値
（自己評価 0.5 / 網羅度 0.5 / 複雑度はヒューリスティック）へ落ちてしまう。

`parse_score()` は本文から 0.0〜1.0 のスコアを抽出してこれを防ぐ。
"""
from __future__ import annotations

import pytest

from grace.llm_compat import parse_score


class TestParseScore:

    @pytest.mark.parametrize("text,expected", [
        ("0.8", 0.8),
        ("0.85", 0.85),
        (" 0.35 \n", 0.35),
        ("1", 1.0),
        ("0", 0.0),
        ("1.0", 1.0),
    ])
    def test_bare_numbers(self, text, expected):
        assert parse_score(text) == pytest.approx(expected)

    @pytest.mark.parametrize("text,expected", [
        # float() では ValueError になっていた形
        ("答えは 0.8 です。", 0.8),
        ("スコア: 0.65", 0.65),
        ("この回答の信頼度は0.42と評価します。", 0.42),
        ("0.7/1.0", 0.7),
        ("評価は 1 です", 1.0),
    ])
    def test_numbers_embedded_in_prose(self, text, expected):
        assert parse_score(text) == pytest.approx(expected)

    def test_float_would_have_raised(self):
        """回帰の証明: 従来の float() 直変換ではこの入力で例外になる。"""
        text = "答えは 0.8 です。"
        with pytest.raises(ValueError):
            float(text)
        assert parse_score(text) == pytest.approx(0.8)

    @pytest.mark.parametrize("text,expected", [
        ("1.5", 1.0),    # 上限クランプ
        ("-0.3", 0.3),   # 符号は拾わないため絶対値側で一致（範囲内に収まる）
    ])
    def test_clamped_to_unit_range(self, text, expected):
        assert parse_score(text) == pytest.approx(expected)

    @pytest.mark.parametrize("text", [
        "判定できません",
        "",
        None,
    ])
    def test_returns_none_when_no_number(self, text):
        """抽出できないときは None を返し、呼び出し側の既定値へ倒す。"""
        assert parse_score(text) is None

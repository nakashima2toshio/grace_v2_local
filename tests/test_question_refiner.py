"""0-(A) 質問の整理（`backend.app.core.question_refiner`）の単体テスト。

LLM はスタブ。実際の LLM での出力は `tests/usage/usage_refine_question.py` で確かめる。

- 毎回 LLM を 1 回呼ぶ（文字列一致で呼ぶかどうかを決めない）
- プロンプトに今日・明日の日付、担当範囲、問い合わせが入る
- **試す問い合わせがプロンプトの例に入っていない**（入っていると LLM が例を写すだけになる）
- 形式違反・元の文に無い主質問・例外では元の文へ戻す（回答は止めない）
"""
from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest

from backend.app.core.question_refiner import (
    MAX_REFINED_ITEMS,
    REFINE_PROMPT,
    build_refine_prompt,
    parse_refine_output,
    refine_question,
)

SCREEN_INPUT = (
    "Jed York CEOが新しいGMとコーチを探す背景には、どのような経緯がありますか？\n"
    "その件の経緯を詳しく教えて\n"
    "最新ニュースを検索して、49ersの新GMとヘッドコーチの人事を教えて"
)
TODAY = date(2026, 10, 22)


class _StubClient:
    """`client.models.generate_content(...)` を記録し、決めた文字列（または例外）を返す。"""

    def __init__(self, text: str = "", raises: Exception = None):
        self.calls = []
        self._text = text
        self._raises = raises
        self.models = self

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises:
            raise self._raises
        return SimpleNamespace(text=self._text)


def _item(main, related=(), **kw):
    base = {
        "main": main, "related": list(related),
        "answer_question": kw.get("answer_question", main),
        "search_query": kw.get("search_query", main),
        "web_query": kw.get("web_query", main),
        "wants_latest_web": kw.get("wants_latest_web", False),
        "in_scope": kw.get("in_scope"),
    }
    return base


def _json(*items):
    return json.dumps({"questions": list(items)}, ensure_ascii=False)


SCREEN_OUTPUT = _json(_item(
    "Jed York CEOが新しいGMとコーチを探す背景には、どのような経緯がありますか？",
    ["その件の経緯を詳しく教えて", "最新ニュースを検索して、49ersの新GMとヘッドコーチの人事を教えて"],
    answer_question="49ersのJed York CEOが新しいGMとヘッドコーチを探すことになった背景と経緯を、詳しく教えてください。",
    search_query="49ersのJed York CEOが新しいGMとヘッドコーチを探すことになった経緯",
    web_query="49ers 新GM ヘッドコーチ 人事 Jed York",
    wants_latest_web=True,
))


def _config():
    return SimpleNamespace(llm=SimpleNamespace(light_model="light-model-for-test"))


class TestPrompt:
    def test_dates_and_query_are_filled_in(self):
        prompt = build_refine_prompt(SCREEN_INPUT, today=TODAY)

        assert "2026年10月22日" in prompt
        assert "2026年10月23日" in prompt          # 例 3 の「明日」
        assert prompt.rstrip().endswith("JSON だけを出力してください。")
        assert SCREEN_INPUT in prompt
        assert "<<" not in prompt                   # 埋め残しが無い

    def test_scope_block_only_with_profile(self):
        profile = SimpleNamespace(name="行政", scope_description="住民票・税・ごみの手続き")

        assert "【担当範囲（行政）】" in build_refine_prompt("q", today=TODAY, profile=profile)
        assert "【担当範囲" not in build_refine_prompt("q", today=TODAY)

    @pytest.mark.parametrize("probe", [
        "Jed York", "49ers", "ヘッドコーチ",          # 画面で試した 3 行
        "住民票の写しを取りたい", "領収書は発行できますか",  # usage スクリプトで試す入力
        "その件の経緯を詳しく教えて", "東京の天気",
    ])
    def test_inputs_we_try_are_not_in_the_examples(self, probe):
        """例と同じ文を試すと、LLM が例の出力を写すだけで確認にならない。"""
        assert probe not in REFINE_PROMPT


class TestParse:
    def test_valid_output(self):
        parsed = parse_refine_output(SCREEN_OUTPUT, SCREEN_INPUT)

        assert len(parsed.questions) == 1
        assert parsed.questions[0].wants_latest_web is True

    def test_code_fence_is_removed(self):
        parsed = parse_refine_output(f"```json\n{SCREEN_OUTPUT}\n```", SCREEN_INPUT)

        assert len(parsed.questions) == 1

    @pytest.mark.parametrize("text, why", [
        ("", "空"),
        ("了解しました。整理します。", "JSON でない"),
        ('{"questions": []}', "項目が 0"),
        (_json(*[_item("Jed York CEOが新しいGMとコーチを探す背景には")] * (MAX_REFINED_ITEMS + 1)),
         "項目が上限超え"),
        (_json(_item("サンフランシスコのチーム運営について知りたい")), "元の文に無い主質問"),
        (_json(_item("Jed York CEOが新しいGMとコーチを探す背景には", search_query=" ")), "空の欄"),
    ])
    def test_invalid_output_raises(self, text, why):
        with pytest.raises(ValueError):
            parse_refine_output(text, SCREEN_INPUT)


class TestRefineQuestion:
    def test_calls_llm_once_with_json_schema_and_light_model(self):
        client = _StubClient(SCREEN_OUTPUT)

        result = refine_question(SCREEN_INPUT, _config(), today=TODAY, client=client)

        assert len(client.calls) == 1
        call = client.calls[0]
        assert call["model"] == "light-model-for-test"
        assert call["config"]["response_mime_type"] == "application/json"
        assert call["config"]["response_schema"].__name__ == "RefinedQuestion"
        assert result.failed is False
        assert result.items[0].web_query == "49ers 新GM ヘッドコーチ 人事 Jed York"

    @pytest.mark.parametrize("query", ["領収書は発行できますか？", "その件の経緯を詳しく教えて"])
    def test_single_sentence_also_calls_llm(self, query):
        """1 文の問い合わせでも呼ぶ（文字列一致で呼ぶかどうかを決めない）。"""
        client = _StubClient(_json(_item(query)))

        refine_question(query, _config(), today=TODAY, client=client)

        assert len(client.calls) == 1

    @pytest.mark.parametrize("client", [
        _StubClient(raises=RuntimeError("API down")),
        _StubClient("了解しました"),
        _StubClient(_json(_item("サンフランシスコのチーム運営について知りたい"))),
    ], ids=["exception", "not_json", "invented_question"])
    def test_failure_falls_back_to_the_original_text(self, client):
        result = refine_question(SCREEN_INPUT, _config(), today=TODAY, client=client)

        assert result.failed is True
        assert result.reason
        assert len(result.items) == 1
        item = result.items[0]
        assert item.answer_question == item.search_query == item.web_query == SCREEN_INPUT
        assert item.wants_latest_web is False

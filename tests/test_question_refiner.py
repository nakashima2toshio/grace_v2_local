"""0-(A) 質問の整理（`backend.app.core.question_refiner`）の単体テスト。

⚠️ **LLM はスタブ。プロンプトの良し悪しはここでは確かめない。** 確かめるのは周辺処理だけ:

- 毎回 LLM を 1 回呼ぶ（文字列一致で呼ぶかどうかを決めない）
- プロンプトに今日・明日の日付、担当範囲、問い合わせが入る
- **試す問い合わせがプロンプトの例に入っていない**（入っていると LLM が例を写すだけになる）
- 出力の検査（話題・観点の数、文の観点、relation、書き写し）に落ちたら元の文へ戻す

実際の LLM での出力は `tests/usage/usage_refine_question.py` で確かめる。
"""
from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest

from backend.app.core.question_refiner import (
    MAX_ASPECTS_PER_TOPIC,
    MAX_TOPICS,
    REFINE_PROMPT,
    build_refine_prompt,
    parse_refine_output,
    refine_question,
)

S1 = "Jed York CEOが新しいGMとコーチを探す背景には、どのような経緯がありますか？"
S2 = "その件の経緯を詳しく教えて"
S3 = "最新ニュースを検索して、49ersの新GMとヘッドコーチの人事を教えて"
SCREEN_INPUT = "\n".join([S1, S2, S3])
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


def _aspect(name, wants=False):
    return {"name": name, "search_query": f"{name}の検索文", "web_query": f"{name} 語句",
            "wants_latest_web": wants}


def _topic(sentences, aspects, answer="回答用の質問文"):
    return {"answer_question": answer, "in_scope": None,
            "sentences": [{"text": t, "aspect": a, "relation": r} for t, a, r in sentences],
            "aspects": aspects}


def _json(*topics):
    return json.dumps({"topics": list(topics)}, ensure_ascii=False)


SCREEN_OUTPUT = _json(_topic(
    [(S1, "経緯", "主"), (S2, "経緯", "同一"), (S3, "人事", "別観点")],
    [_aspect("経緯"), _aspect("人事", wants=True)],
))


def _config():
    return SimpleNamespace(llm=SimpleNamespace(light_model="light-model-for-test"))


class TestPrompt:
    def test_dates_and_query_are_filled_in(self):
        prompt = build_refine_prompt(SCREEN_INPUT, today=TODAY)

        assert "2026年10月22日" in prompt
        assert "2026年10月23日" in prompt          # 例 3 の「明日」
        assert SCREEN_INPUT in prompt
        assert "<<" not in prompt                   # 埋め残しが無い

    def test_scope_block_only_with_profile(self):
        profile = SimpleNamespace(name="行政", scope_description="住民票・税・ごみの手続き")

        assert "【担当範囲（行政）】" in build_refine_prompt("q", today=TODAY, profile=profile)
        assert "【担当範囲" not in build_refine_prompt("q", today=TODAY)

    @pytest.mark.parametrize("probe", [
        "Jed York", "49ers", "ヘッドコーチ",                 # 画面で試した 3 行
        "住民票の写しを取りたい", "領収書は発行できますか",  # usage スクリプトで試す入力
        "その件の経緯を詳しく教えて", "東京の天気",
    ])
    def test_inputs_we_try_are_not_in_the_examples(self, probe):
        """例と同じ文を試すと、LLM が例の出力を写すだけで確認にならない。"""
        assert probe not in REFINE_PROMPT


    @pytest.mark.parametrize("rule", [
        "対象の補足を書き足さない",          # 実測: 「サンフランシスコ・49ers」と書き足した
        "出来事を推測して書き足さない",      # 実測: web_query に入力に無い「解任」を入れた
        "聞かれていないことを足さない",      # 実測: 「領収書は発行できますか？」に「方法も」を足した
        "前置きの文は状況の説明",            # 実測: 「住民票の写しを取りたい。」を「手続き」の観点にした
    ])
    def test_rules_found_on_the_real_llm_stay_in_the_prompt(self, rule):
        """Mac の実 LLM（2026-10-26）で見つかった書き足しを防ぐ規則を消さない。"""
        assert rule in REFINE_PROMPT


class TestParse:
    def test_valid_output(self):
        parsed = parse_refine_output(SCREEN_OUTPUT, SCREEN_INPUT)

        topic = parsed.topics[0]
        assert [s.relation for s in topic.sentences] == ["主", "同一", "別観点"]
        assert [a.wants_latest_web for a in topic.aspects] == [False, True]

    def test_code_fence_is_removed(self):
        assert parse_refine_output(f"```json\n{SCREEN_OUTPUT}\n```", SCREEN_INPUT).topics

    @pytest.mark.parametrize("text", [
        "",
        "了解しました。整理します。",
        '{"topics": []}',
        _json(*[_topic([(S1, "経緯", "主")], [_aspect("経緯")])] * (MAX_TOPICS + 1)),
        _json(_topic([(S1, "a", "主")], [_aspect(f"a{i}") for i in range(MAX_ASPECTS_PER_TOPIC + 1)])),
        _json(_topic([(S1, "経緯", "関連")], [_aspect("経緯")])),              # relation の値が違う
        _json(_topic([(S1, "経緯", "同一")], [_aspect("経緯")])),              # 最初の文が「主」でない
        _json(_topic([(S1, "人事", "主")], [_aspect("経緯")])),                # 文の観点が一覧に無い
        _json(_topic([(S1, "経緯", "主")], [_aspect("経緯"), _aspect("人事")])),  # 文の無い観点
        _json(_topic([("サンフランシスコのチーム運営について知りたい", "経緯", "主")], [_aspect("経緯")])),
        _json(_topic([(S1, "経緯", "主")], [_aspect("経緯")], answer=" ")),   # 空の answer_question
    ], ids=["empty", "prose", "no_topic", "too_many_topics", "too_many_aspects", "bad_relation",
            "first_not_main", "unknown_aspect", "aspect_without_sentence", "invented_sentence",
            "blank_answer"])
    def test_invalid_output_raises(self, text):
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
        assert len(result.topics[0].aspects) == 2

    @pytest.mark.parametrize("query", ["領収書は発行できますか？", S2])
    def test_single_sentence_also_calls_llm(self, query):
        """1 文の問い合わせでも呼ぶ（文字列一致で呼ぶかどうかを決めない）。"""
        client = _StubClient(_json(_topic([(query, "x", "主")], [_aspect("x")])))

        refine_question(query, _config(), today=TODAY, client=client)

        assert len(client.calls) == 1

    @pytest.mark.parametrize("client", [
        _StubClient(raises=RuntimeError("API down")),
        _StubClient("了解しました"),
        _StubClient(_json(_topic([("サンフランシスコのチーム運営について知りたい", "x", "主")], [_aspect("x")]))),
    ], ids=["exception", "not_json", "invented_sentence"])
    def test_failure_falls_back_to_the_original_text(self, client):
        result = refine_question(SCREEN_INPUT, _config(), today=TODAY, client=client)

        assert result.failed is True
        assert result.reason
        topic = result.topics[0]
        assert topic.answer_question == SCREEN_INPUT
        assert [(a.search_query, a.web_query, a.wants_latest_web) for a in topic.aspects] == [
            (SCREEN_INPUT, SCREEN_INPUT, False)]

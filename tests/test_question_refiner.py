"""0-(A) 質問の整理（`backend.app.core.question_refiner`）の単体テスト。

⚠️ **LLM はスタブ。プロンプトの良し悪しはここでは確かめない。** 確かめるのは周辺処理だけ:

- 毎回 LLM を 1 回呼ぶ（文字列一致で呼ぶかどうかを決めない）
- プロンプトに今日・明日の日付、担当範囲、問い合わせが入る
- **試す問い合わせがプロンプトの例に入っていない**（入っていると LLM が例を写すだけになる）
- 出力の検査（話題・観点の数、文の観点、relation、書き写し）に落ちたら元の文へ戻す
- 質問ではない文は relation「前置き」で表し、観点を持たない
- web_query から問い合わせに無い語を外す（`restrict_web_query`）

実際の LLM での出力は `tests/usage/usage_refine_question.py` で確かめる。
"""
from __future__ import annotations

import json
import re
from datetime import date
from types import SimpleNamespace

import pytest

from backend.app.core.question_refiner import (
    MAX_ASPECTS_PER_TOPIC,
    MAX_TOPICS,
    REFINE_PROMPT,
    Relation,
    build_refine_prompt,
    parse_refine_output,
    refine_question,
    restrict_web_query,
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
        "前置きの文から観点を作らない",      # 実測: 「住民票の写しを取りたい。」を「取得方法」の観点にした
        "問い合わせの文に書かれている語だけ",  # 実測: web_query に入力に無い「解任」を入れた
    ])
    def test_rules_found_on_the_real_llm_stay_in_the_prompt(self, rule):
        """Mac の実 LLM（2026-10-26）で見つかった書き足しを防ぐ規則を消さない。"""
        assert rule in REFINE_PROMPT

    def test_relation_has_a_value_for_sentences_that_are_not_questions(self):
        """質問ではない文を表す値が無いと、前置きの文も「主」にして観点を付けるしかない。

        実測（2026-10-29・軽量モデル・3 回中 3 回）: 「住民票の写しを取りたい。その件の手数料は？」を
        「取得方法」と「手数料」の 2 観点に分けた。禁止の文を足しても直らなかった。
        """
        assert "前置き" in Relation.__args__
        assert '"relation": "前置き"' in REFINE_PROMPT   # 例にも使い方を示す

    def test_example_web_queries_use_only_words_of_the_example_query(self):
        """プロンプトの例が「問い合わせにある語だけ」の規則を破っていると、LLM は例をまねる。"""
        for block in REFINE_PROMPT.split("【例 ")[1:]:
            example_query = block.split("問い合わせ:", 1)[1].split("出力:", 1)[0]
            for web_query in re.findall(r'"web_query": "([^"]*)"', block):
                for word in web_query.split():
                    assert word in example_query or word == "<<TOMORROW>>", (word, block[:20])


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
        _json(_topic([(S1, "", "前置き")], [_aspect("経緯")])),               # 質問の文が無い
        _json(_topic([(S1, "", "前置き"), (S2, "経緯", "同一")], [_aspect("経緯")])),  # 前置きの次が「主」でない
        _json(_topic([(S1, "取得", "前置き"), (S2, "経緯", "主")],
                     [_aspect("取得"), _aspect("経緯")])),                   # 前置きの文だけから作った観点
    ], ids=["empty", "prose", "no_topic", "too_many_topics", "too_many_aspects", "bad_relation",
            "first_not_main", "unknown_aspect", "aspect_without_sentence", "invented_sentence",
            "blank_answer", "only_preface", "preface_then_not_main", "aspect_only_from_preface"])
    def test_invalid_output_raises(self, text):
        with pytest.raises(ValueError):
            parse_refine_output(text, SCREEN_INPUT)

    def test_preface_sentence_has_no_aspect(self):
        """前置きの文は観点を持たず、前置きを除いた最初の文が「主」なら合格。"""
        query = "住民票の写しを取りたい。その件の手数料はいくらですか？"
        text = _json(_topic([("住民票の写しを取りたい。", "", "前置き"),
                             ("その件の手数料はいくらですか？", "手数料", "主")], [_aspect("手数料")]))

        topic = parse_refine_output(text, query).topics[0]

        assert [(s.relation, s.aspect) for s in topic.sentences] == [("前置き", ""), ("主", "手数料")]
        assert [a.name for a in topic.aspects] == ["手数料"]


class TestRestrictWebQuery:
    @pytest.mark.parametrize("web_query, expected", [
        ("Jed York 49ers GM ヘッドコーチ 解任 経緯", "Jed York 49ers GM ヘッドコーチ 経緯"),  # 実測 2026-10-29
        ("サンフランシスコ 49ers 新GM ヘッドコーチ 人事", "49ers 新GM ヘッドコーチ 人事"),
        ("Jed York\u3000解任\u3000経緯", "Jed York 経緯"),                                  # 全角空白で区切っても同じ
    ])
    def test_words_not_in_the_query_are_removed(self, web_query, expected):
        assert restrict_web_query(web_query, SCREEN_INPUT, today=TODAY) == expected

    def test_today_and_tomorrow_dates_are_kept(self):
        query = "明日の東京の天気は？"
        assert restrict_web_query("東京 天気 2026年10月23日", query, today=TODAY) == "東京 天気 2026年10月23日"
        assert restrict_web_query("東京 天気 2026年10月25日", query, today=TODAY) == "東京 天気"

    def test_paraphrases_are_removed_too(self):
        assert restrict_web_query("領収書 発行 可否", "領収書は発行できますか？", today=TODAY) == "領収書 発行"

    def test_falls_back_to_the_query_when_nothing_is_left(self):
        query = "その件の経緯を詳しく教えて"
        assert restrict_web_query("解任 退任", query, today=TODAY) == query


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

    def test_web_query_is_restricted_to_words_of_the_query(self):
        aspects = [{"name": "経緯", "search_query": "経緯の検索文",
                    "web_query": "Jed York 49ers GM ヘッドコーチ 解任 経緯", "wants_latest_web": False},
                   _aspect("人事", wants=True)]
        client = _StubClient(_json(_topic(
            [(S1, "経緯", "主"), (S2, "経緯", "同一"), (S3, "人事", "別観点")], aspects)))

        result = refine_question(SCREEN_INPUT, _config(), today=TODAY, client=client)

        assert result.failed is False
        assert result.topics[0].aspects[0].web_query == "Jed York 49ers GM ヘッドコーチ 経緯"

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

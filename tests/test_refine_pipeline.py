# tests/test_refine_pipeline.py
"""0-(A) 質問の整理（`refine_question`）をパイプラインへつないだ挙動（`run_support_agent_core`）。

設計: `backend/app/core/question_refiner.py`（モジュールの docstring）。

- **毎回 1 回**整理する（文字列一致で呼ぶかどうかを決めない）
- 採用した話題を planner へ渡し（`create_plan_for_topic`）、ReAct へは回さない
- 話題が複数なら利用者に選ばせる。担当範囲外の話題は選択肢に出さず、断り＋窓口案内で返す
- 整理に失敗したら・選択が得られなかったら、元の文のまま 1 つの質問として進める（回答は止めない）
- ⑤ Web フォールバックは観点ごとの `web_query` で検索する

旧 `test_multi_question_pipeline.py`（接続語・「？」の数で LLM を呼ぶか決めていた頃のテスト）から、
今も意味のある場面（選択・保留・担当範囲外）を移した。LLM・planner・executor はスタブ（`tests/conftest.py`）。
"""
from __future__ import annotations

from backend.app.core.question_refiner import Aspect, Sentence, Topic
from backend.app.core.support_agent import (
    AUTO_PROCEED,
    SupportEvent,
    run_support_agent_core,
)
from backend.app.core.verticals import PROFILES
from grace import InterventionAction, InterventionResponse
from tests.conftest import GroundednessStub

SCREEN_INPUT = (
    "Jed York CEOが新しいGMとコーチを探す背景には、どのような経緯がありますか？\n"
    "その件の経緯を詳しく教えて\n"
    "最新ニュースを検索して、49ersの新GMとヘッドコーチの人事を教えて"
)
MULTI_QUERY = "住民票の写しの取り方は？ また、明日の東京の天気は？"


def topic(answer, *aspects, in_scope=None):
    """`Topic` を組み立てる。aspects は (name, search_query, web_query, wants_latest_web)。"""
    aspects = aspects or ((answer, answer, answer, False),)
    return Topic(
        answer_question=answer,
        sentences=[Sentence(text=answer, aspect=aspects[0][0], relation="主")],
        aspects=[Aspect(name=n, search_query=sq, web_query=wq, wants_latest_web=w)
                 for n, sq, wq, w in aspects],
        in_scope=in_scope,
    )


SCREEN_TOPIC = topic(
    "Jed York CEOが新しいGMとコーチを探す背景にはどのような経緯があるのか、詳しく教えてください。"
    "あわせて、49ersの新GMとヘッドコーチの人事についても最新の情報を教えてください。",
    ("経緯", "Jed York CEOが新しいGMとコーチを探す背景の経緯", "Jed York CEO GM コーチ 経緯", False),
    ("人事", "49ersの新しいGMとヘッドコーチの人事", "49ers 新GM ヘッドコーチ 人事", True),
)
JUMINHYO = "住民票の写しの取り方を教えてください。"
WEATHER = "2026年10月30日の東京の天気を教えてください。"


def _steps(events, step):
    return [e.status for e in events if e.type == "step" and e.step == step]


def _logs(events, step):
    return [e.message for e in events if e.type == "log" and e.step == step]


def _selections(requests):
    return [r for r in requests if r.reason == "multi_question_selection"]


def _pick(option):
    return lambda _r: InterventionResponse(action=InterventionAction.PROCEED, selected_option=option)


class TestAlwaysRefined:
    def test_1行の問い合わせでも1回整理する(self, pipeline_stub):
        events: list[SupportEvent] = []
        run_support_agent_core("パスワードを忘れました", emit=events.append,
                               confirm=lambda _r: AUTO_PROCEED)

        assert [q for q, _p in pipeline_stub.refine_calls] == ["パスワードを忘れました"]
        assert _steps(events, "analyze") == ["started", "finished"]

    def test_業界プロファイルを整理に渡す(self, pipeline_stub):
        run_support_agent_core("住民票の写しの取り方は？", vertical="gov",
                               confirm=lambda _r: AUTO_PROCEED, do_action=False)

        assert pipeline_stub.refine_calls[0][1] is PROFILES["gov"]

    def test_採用した話題を計画に渡し_ReActへは回さない(self, pipeline_stub):
        pipeline_stub.topics = [SCREEN_TOPIC]

        result = run_support_agent_core(SCREEN_INPUT, confirm=lambda _r: AUTO_PROCEED,
                                        do_action=False)

        assert pipeline_stub.planned_topics == [SCREEN_TOPIC]
        assert pipeline_stub.execute_kwargs == [{"allow_react": False}]
        # 話題が 1 つなら選択も保留も無い
        assert result.is_multi_question is False
        assert result.adopted_cluster_index is None
        assert result.deferred_questions == []
        assert result.reconstructed_query == SCREEN_TOPIC.answer_question

    def test_観点と検索文をログに出す(self, pipeline_stub):
        """何を質問として解釈し、何で検索したかを画面のタイムラインで確かめられること。"""
        pipeline_stub.topics = [SCREEN_TOPIC]
        events: list[SupportEvent] = []

        run_support_agent_core(SCREEN_INPUT, emit=events.append,
                               confirm=lambda _r: AUTO_PROCEED, do_action=False)

        logs = "\n".join(_logs(events, "analyze"))
        assert SCREEN_TOPIC.answer_question in logs
        assert "49ers 新GM ヘッドコーチ 人事" in logs
        assert "最新情報を求める" in logs
        finished = [e for e in events if e.type == "step" and e.step == "analyze"
                    and e.status == "finished"]
        assert finished[0].data["topics"][0]["aspects"][1]["wants_latest_web"] is True

    def test_整理に失敗しても元の文のまま回答する(self, pipeline_stub):
        pipeline_stub.refine_failed = True
        events: list[SupportEvent] = []

        result = run_support_agent_core("パスワードを忘れました", emit=events.append,
                                        confirm=lambda _r: AUTO_PROCEED)

        assert result.decision == "answer"
        assert pipeline_stub.planned_topics[0].answer_question == "パスワードを忘れました"
        assert any("整理に失敗" in m for m in _logs(events, "analyze"))


class TestSelection:
    def test_話題が複数なら選択が提示される(self, pipeline_stub):
        pipeline_stub.topics = [topic(JUMINHYO), topic(WEATHER)]
        seen: list = []

        result = run_support_agent_core(
            MULTI_QUERY, do_action=False,
            confirm=lambda r: (seen.append(r), _pick(WEATHER)(r))[1],
        )

        assert [r.options for r in _selections(seen)] == [[JUMINHYO, WEATHER]]
        assert result.adopted_cluster_index == 1
        assert pipeline_stub.planned_topics[0].answer_question == WEATHER

    def test_採用しなかった話題を保留として返し_ログにも残す(self, pipeline_stub):
        """🔴 黙って落とさない。"""
        pipeline_stub.topics = [topic(JUMINHYO), topic(WEATHER)]
        events: list[SupportEvent] = []

        result = run_support_agent_core(MULTI_QUERY, emit=events.append, do_action=False,
                                        confirm=_pick(JUMINHYO))

        assert result.is_multi_question is True
        assert result.deferred_questions == [WEATHER]
        assert result.reconstructed_query == JUMINHYO
        assert any("保留した質問" in m for m in _logs(events, "analyze"))

    def test_選択肢に無い値が返れば先頭を採用し残りを保留する(self, pipeline_stub):
        """自動承認（selected_option なし）もこの経路を通る。"""
        pipeline_stub.topics = [topic(JUMINHYO), topic(WEATHER)]

        result = run_support_agent_core(MULTI_QUERY, confirm=lambda _r: AUTO_PROCEED,
                                        do_action=False)

        assert result.adopted_cluster_index == 0
        assert result.deferred_questions == [WEATHER]

    def test_タイムアウトなら元の文のまま1つの質問として処理する(self, pipeline_stub):
        pipeline_stub.topics = [topic(JUMINHYO), topic(WEATHER)]
        events: list[SupportEvent] = []

        result = run_support_agent_core(
            MULTI_QUERY, emit=events.append, do_action=False,
            confirm=lambda _r: InterventionResponse(action=InterventionAction.CANCEL,
                                                    timeout_reached=True),
        )

        assert result.decision == "answer", "選択できないことを理由に escalate しない"
        assert result.is_multi_question is False
        assert result.deferred_questions == []
        assert result.reconstructed_query is None
        assert pipeline_stub.planned_topics[0].answer_question == MULTI_QUERY
        assert _steps(events, "analyze") == ["started", "finished"]


class TestOutOfScope:
    """担当範囲外の話題は、選択肢に出さず断り＋窓口案内で返す（実測 2026-08-29）。"""

    def _topics(self, in_scope_weather=False):
        return [topic(JUMINHYO, in_scope=True), topic(WEATHER, in_scope=in_scope_weather)]

    def _closing(self, events) -> str:
        finished = [e for e in events
                    if e.type == "step" and e.step == "profile" and e.status == "finished"]
        return finished[0].data["injected_prompt_closing"]

    def test_範囲内が1つなら選択を出さず_範囲外として返す(self, pipeline_stub):
        pipeline_stub.topics = self._topics()
        seen: list = []
        events: list[SupportEvent] = []

        result = run_support_agent_core(
            MULTI_QUERY, vertical="gov", emit=events.append, do_action=False,
            confirm=lambda r: (seen.append(r), AUTO_PROCEED)[1],
        )

        assert _selections(seen) == []
        assert result.adopted_cluster_index == 0
        assert result.out_of_scope_questions == [WEATHER]
        assert result.deferred_questions == [], "範囲外を保留に混ぜない"
        assert result.out_of_scope_guidance
        assert "気象庁" in result.answer
        # 断りの指示は回答の構成ルールの後ろ（prompt_closing）へ入る
        assert WEATHER in self._closing(events)
        assert "同じ回答の末尾に" in self._closing(events)

    def test_範囲内が複数なら選択を出す(self, pipeline_stub):
        pipeline_stub.topics = self._topics(in_scope_weather=True)
        seen: list = []

        run_support_agent_core(MULTI_QUERY, vertical="gov", do_action=False,
                               confirm=lambda r: (seen.append(r), AUTO_PROCEED)[1])

        assert [r.options for r in _selections(seen)] == [[JUMINHYO, WEATHER]]

    def test_判定できない話題は範囲内として扱う(self, pipeline_stub):
        pipeline_stub.topics = [topic(JUMINHYO, in_scope=True), topic(WEATHER, in_scope=None)]
        seen: list = []

        result = run_support_agent_core(MULTI_QUERY, vertical="gov", do_action=False,
                                        confirm=lambda r: (seen.append(r), AUTO_PROCEED)[1])

        assert _selections(seen)
        assert result.out_of_scope_questions == []

    def test_全部が範囲外なら全部を範囲内に戻す(self, pipeline_stub):
        """判定の誤りで質問が丸ごと消えるのを防ぐ（生成側の SCOPE_POLICY が断る）。"""
        pipeline_stub.topics = [topic(JUMINHYO, in_scope=False), topic(WEATHER, in_scope=False)]

        result = run_support_agent_core(MULTI_QUERY, vertical="gov", do_action=False,
                                        confirm=lambda _r: AUTO_PROCEED)

        assert result.out_of_scope_questions == []

    def test_基本版は範囲判定をしない(self, pipeline_stub):
        pipeline_stub.topics = self._topics()

        result = run_support_agent_core(MULTI_QUERY, confirm=lambda _r: AUTO_PROCEED,
                                        do_action=False)

        assert result.out_of_scope_questions == []
        assert result.out_of_scope_guidance == ""


class TestWebFallbackUsesWebQuery:
    def test_観点ごとのweb_queryで検索する(self, pipeline_stub):
        """⑤ は回答用の質問文ではなく、観点ごとの Web 検索語で検索する（同じ語は 1 回）。"""
        pipeline_stub.topics = [topic(
            SCREEN_TOPIC.answer_question,
            ("経緯", "s1", "Jed York CEO GM コーチ 経緯", False),
            ("人事", "s2", "49ers 新GM ヘッドコーチ 人事", True),
            ("人事2", "s3", "49ers 新GM ヘッドコーチ 人事", False),
        )]
        pipeline_stub.groundedness = GroundednessStub(
            support_rate=0.1, supported=0, contradicted=1, total=3,
            verified=True, has_contradiction=True,  # 矛盾あり → ④救済なし → ⑤ へ
        )
        pipeline_stub.web_output = [
            {"payload": {"title": "公式", "source": "https://example.com/g", "answer": "本文"}},
        ]

        result = run_support_agent_core(SCREEN_INPUT, confirm=lambda _r: AUTO_PROCEED,
                                        do_action=False)

        assert result.used_web is True
        assert pipeline_stub.web_queries == [
            "Jed York CEO GM コーチ 経緯", "49ers 新GM ヘッドコーチ 人事"]

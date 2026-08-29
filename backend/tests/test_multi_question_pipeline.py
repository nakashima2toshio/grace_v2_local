# backend/tests/test_multi_question_pipeline.py
"""0-(A) 入力・質問分析をパイプラインへ組み込んだ挙動（`run_support_agent_core`）。

設計: `docs/multi_question_handling.md` §13.2 / §13.8。

⚠️ **最優先は「単一質問の挙動が 1 ミリも変わらないこと」**（受け入れ基準 #1）。
前処理を足しただけで、planner / executor / gates の判定は無改変である。

⚠️ ここでの安全側は「原文のまま 1 回だけ実行する」であって escalate ではない。
分析は前処理でありゲートではないため、選択が得られないことを理由に回答自体を
諦めるのは過剰（基準 #7）。
"""
from __future__ import annotations

from backend.app.core.support_agent import (
    AUTO_PROCEED,
    SupportEvent,
    run_support_agent_core,
)
from grace import InterventionAction, InterventionResponse

MULTI_QUERY = "住民票の写しの取り方は？ また、他の市町村に住民票を移動する方法は？"
RELATED_QUERY = "住民票の写しの取り方は？ その手数料は？"


def _steps(events, step):
    return [e.status for e in events if e.type == "step" and e.step == step]


def _logs(events, step):
    return [e.message for e in events if e.type == "log" and e.step == step]


class TestSingleQuestionUnchanged:
    """基準 #1: 単一質問では 0-(A) が何もしない。"""

    def test_analyzeステップはスキップされる(self, pipeline_stub):
        events: list[SupportEvent] = []
        run_support_agent_core(
            "パスワードを忘れました",
            emit=events.append,
            confirm=lambda _r: AUTO_PROCEED,
        )
        assert _steps(events, "analyze") == ["skipped"]

    def test_複数質問のフィールドは既定値のまま(self, pipeline_stub):
        result = run_support_agent_core(
            "パスワードを忘れました", confirm=lambda _r: AUTO_PROCEED
        )
        assert result.is_multi_question is False
        assert result.question_clusters == []
        assert result.adopted_cluster_index is None
        assert result.reconstructed_query is None
        assert result.deferred_questions == []

    def test_第1段で弾かれれば解析器は呼ばれない(self, pipeline_stub):
        """接続表現も疑問符 2 つも無いクエリでは LLM を 1 回も呼ばない。"""
        calls: list[str] = []
        pipeline_stub.clusters = [("Aは？", []), ("Bは？", [])]

        def spy(_q):
            calls.append(_q)
            return pipeline_stub.clusters

        import backend.app.core.support_agent as core

        original = core.create_cluster_analyzer
        core.create_cluster_analyzer = lambda _c: spy
        try:
            result = run_support_agent_core(
                "パスワードを忘れました", confirm=lambda _r: AUTO_PROCEED
            )
        finally:
            core.create_cluster_analyzer = original

        assert calls == []
        assert result.is_multi_question is False


class TestSingleClusterNoSelection:
    """基準 #3: 主質問 1 ＋ 関連質問 N では選択を出さない。"""

    def test_選択を求めずに再構成する(self, pipeline_stub):
        pipeline_stub.clusters = [("住民票の写しの取り方は？", ["その手数料は？"])]
        asked: list[object] = []

        def confirm(request):
            asked.append(request)
            return AUTO_PROCEED

        result = run_support_agent_core(RELATED_QUERY, confirm=confirm, do_action=False)

        assert [r.reason for r in asked] != ["multi_question_selection"]
        assert result.is_multi_question is True
        assert result.adopted_cluster_index == 0
        assert result.deferred_questions == []
        # 再構成の結果が原文と同じなら `reconstructed_query` は出さない
        # （「こう解釈しました」と原文をそのまま見せても情報が無い）。
        # スタブでは素朴な連結（fallback_reconstruct）なので原文に一致する。
        assert result.reconstructed_query is None


class TestSelection:
    """基準 #2 / #5 / #6: 選択・保留質問・再構成後クエリ。"""

    def _clusters(self):
        return [
            ("住民票の写しの取り方は？", []),
            ("他の市町村に住民票を移動する方法は？", []),
        ]

    def test_主質問が複数なら選択が提示される(self, pipeline_stub):
        pipeline_stub.clusters = self._clusters()
        seen: list[object] = []

        def confirm(request):
            seen.append(request)
            return InterventionResponse(
                action=InterventionAction.PROCEED,
                selected_option="他の市町村に住民票を移動する方法は？",
            )

        result = run_support_agent_core(MULTI_QUERY, confirm=confirm, do_action=False)

        selection = [r for r in seen if r.reason == "multi_question_selection"]
        assert len(selection) == 1
        assert selection[0].options == [
            "住民票の写しの取り方は？",
            "他の市町村に住民票を移動する方法は？",
        ]
        assert result.adopted_cluster_index == 1

    def test_採用しなかった主質問が保留として返る(self, pipeline_stub):
        pipeline_stub.clusters = self._clusters()
        result = run_support_agent_core(
            MULTI_QUERY,
            do_action=False,
            confirm=lambda _r: InterventionResponse(
                action=InterventionAction.PROCEED,
                selected_option="住民票の写しの取り方は？",
            ),
        )
        assert result.deferred_questions == ["他の市町村に住民票を移動する方法は？"]

    def test_保留質問はログにも残る(self, pipeline_stub):
        """🔴 黙って落とさない。ログに出ないと事故と区別できない。"""
        pipeline_stub.clusters = self._clusters()
        events: list[SupportEvent] = []
        run_support_agent_core(
            MULTI_QUERY,
            emit=events.append,
            do_action=False,
            confirm=lambda _r: InterventionResponse(
                action=InterventionAction.PROCEED,
                selected_option="住民票の写しの取り方は？",
            ),
        )
        assert any("保留した主質問" in m for m in _logs(events, "analyze"))

    def test_再構成後クエリが原文と違えば必ず返す(self, pipeline_stub):
        """基準 #6: 利用者が「何を質問として解釈されたか」を検証できること。"""
        pipeline_stub.clusters = [
            ("住民票の写しの取り方は？", ["その手数料は？"]),
            ("他の市町村に住民票を移動する方法は？", []),
        ]
        result = run_support_agent_core(
            MULTI_QUERY, confirm=lambda _r: AUTO_PROCEED, do_action=False
        )
        assert result.reconstructed_query == "住民票の写しの取り方は？ その手数料は？"
        assert result.reconstructed_query != MULTI_QUERY
        assert result.deferred_questions == ["他の市町村に住民票を移動する方法は？"]

    def test_選択肢に無い値が返れば先頭を採用し残りを保留する(self, pipeline_stub):
        """CLI の自動承認（selected_option なし）もこの経路を通る。"""
        pipeline_stub.clusters = self._clusters()
        result = run_support_agent_core(
            MULTI_QUERY, confirm=lambda _r: AUTO_PROCEED, do_action=False
        )
        assert result.adopted_cluster_index == 0
        assert result.deferred_questions == ["他の市町村に住民票を移動する方法は？"]


class TestSelectionDeclined:
    """基準 #7: 選択が得られなくてもハングせず、原文のまま 1 周する。"""

    def _run(self, response):
        events: list[SupportEvent] = []
        result = run_support_agent_core(
            MULTI_QUERY, emit=events.append, do_action=False,
            confirm=lambda _r: response,
        )
        return result, events

    def test_タイムアウトなら単一質問として処理する(self, pipeline_stub):
        pipeline_stub.clusters = [("Aは？", []), ("Bは？", [])]
        result, events = self._run(
            InterventionResponse(action=InterventionAction.CANCEL, timeout_reached=True)
        )
        assert result.decision == "answer", "選択できないことを理由に escalate しない"
        assert result.is_multi_question is False
        assert result.reconstructed_query is None
        assert result.deferred_questions == []
        assert _steps(events, "analyze") == ["started", "finished"]

    def test_拒否でも単一質問として処理する(self, pipeline_stub):
        pipeline_stub.clusters = [("Aは？", []), ("Bは？", [])]
        result, _events = self._run(
            InterventionResponse(action=InterventionAction.CANCEL)
        )
        assert result.decision == "answer"
        assert result.is_multi_question is False

    def test_analyzeステップのイベントは1回だけ決着する(self, pipeline_stub):
        """finished と skipped の両方を出さない（タイムラインが二重に決着する）。"""
        pipeline_stub.clusters = [("Aは？", []), ("Bは？", [])]
        _result, events = self._run(
            InterventionResponse(action=InterventionAction.CANCEL, timeout_reached=True)
        )
        settled = [s for s in _steps(events, "analyze") if s in ("finished", "skipped")]
        assert settled == ["finished"]

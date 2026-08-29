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


class TestOutOfScopeClusters:
    """担当範囲外の主質問は、選択肢に出さず断り＋窓口案内で返す。

    実測 2026-08-29:「住民票の写しの取り方は？ ところで、明日の東京の天気は？」で
    天気（gov の範囲外）が選択肢に並び、利用者に 1 往復させたうえ保留として
    落ちた。同じ質問を選択なしで通したクラウド版は、住民票に回答しつつ天気は
    「担当範囲外です → 気象庁へ」と 1 パスで返している。
    """

    CLUSTERS = [("住民票の写しの取り方は？", []), ("明日の東京の天気は？", [])]

    def _stub(self, pipeline_stub):
        pipeline_stub.clusters = list(self.CLUSTERS)
        pipeline_stub.scope_verdicts = [True, False]   # 2 問目が範囲外
        return pipeline_stub

    def test_範囲内が1つなら選択を出さない(self, pipeline_stub):
        self._stub(pipeline_stub)
        asked: list = []

        def confirm(request):
            asked.append(request)
            return AUTO_PROCEED

        result = run_support_agent_core(
            MULTI_QUERY, vertical="gov", confirm=confirm, do_action=False
        )
        assert [r for r in asked if r.reason == "multi_question_selection"] == []
        assert result.adopted_cluster_index == 0

    def test_範囲外は保留ではなく範囲外として返す(self, pipeline_stub):
        self._stub(pipeline_stub)
        result = run_support_agent_core(
            MULTI_QUERY, vertical="gov", confirm=lambda _r: AUTO_PROCEED, do_action=False
        )
        assert result.out_of_scope_questions == ["明日の東京の天気は？"]
        assert result.deferred_questions == [], "範囲外を保留に混ぜない"

    def test_窓口案内を添える(self, pipeline_stub):
        """断るだけで終わらせない（SCOPE_POLICY も窓口案内まで求めている）。"""
        self._stub(pipeline_stub)
        result = run_support_agent_core(
            MULTI_QUERY, vertical="gov", confirm=lambda _r: AUTO_PROCEED, do_action=False
        )
        assert result.out_of_scope_guidance
        assert "気象庁" in result.out_of_scope_guidance

    def test_範囲内が複数なら従来どおり選択を出す(self, pipeline_stub):
        pipeline_stub.clusters = list(self.CLUSTERS)
        pipeline_stub.scope_verdicts = [True, True]
        seen: list = []

        def confirm(request):
            seen.append(request)
            return AUTO_PROCEED

        run_support_agent_core(
            MULTI_QUERY, vertical="gov", confirm=confirm, do_action=False
        )
        selection = [r for r in seen if r.reason == "multi_question_selection"]
        assert len(selection) == 1
        assert selection[0].options == [c[0] for c in self.CLUSTERS]

    def test_選択肢は範囲内だけ_採用は元の添字へ戻す(self, pipeline_stub):
        """範囲外を挟んだ状態で選んでも、正しいクラスタが採用されること。"""
        pipeline_stub.clusters = [
            ("明日の東京の天気は？", []),          # 0: 範囲外
            ("住民票の写しの取り方は？", []),      # 1: 範囲内
            ("印鑑登録の方法は？", []),            # 2: 範囲内
        ]
        pipeline_stub.scope_verdicts = [False, True, True]
        seen: list = []

        def confirm(request):
            seen.append(request)
            return InterventionResponse(
                action=InterventionAction.PROCEED,
                selected_option="印鑑登録の方法は？",
            )

        result = run_support_agent_core(
            MULTI_QUERY, vertical="gov", confirm=confirm, do_action=False
        )
        assert seen[0].options == ["住民票の写しの取り方は？", "印鑑登録の方法は？"]
        assert result.adopted_cluster_index == 2
        assert result.deferred_questions == ["住民票の写しの取り方は？"]
        assert result.out_of_scope_questions == ["明日の東京の天気は？"]

    def test_基本版は範囲判定をしない(self, pipeline_stub):
        """vertical なしのタブには担当範囲という概念が無い。"""
        self._stub(pipeline_stub)
        result = run_support_agent_core(
            MULTI_QUERY, confirm=lambda _r: AUTO_PROCEED, do_action=False
        )
        assert result.out_of_scope_questions == []
        assert result.out_of_scope_guidance == ""

    def test_判定不能なら従来どおり選択を出す(self, pipeline_stub):
        pipeline_stub.clusters = list(self.CLUSTERS)
        pipeline_stub.scope_verdicts = None     # 判定不能
        seen: list = []
        run_support_agent_core(
            MULTI_QUERY, vertical="gov", do_action=False,
            confirm=lambda r: (seen.append(r), AUTO_PROCEED)[1],
        )
        assert [r for r in seen if r.reason == "multi_question_selection"]


class TestOutOfScopeAnsweredInline:
    """範囲外の質問を「1 回の回答」で扱わせる（分解して先送りしない）。

    実測 2026-08-29 の比較で、選択なしで 1 パスで通したクラウド版が
    「住民票に回答しつつ、天気は担当範囲外です → 気象庁へ」と返しており、
    利用者体験として良かった。検索は絞ったまま同じ結果を得る。
    """

    CLUSTERS = [("住民票の写しの取り方は？", []), ("明日の東京の天気は？", [])]

    def _injected(self, events) -> str:
        """実際に reasoning へ注入された業務方針を取り出す。

        ⚠️ **ログ行を見て済ませない。** ログは「注入した」と書くだけで、
        本当に `config.llm.prompt_addendum` へ入ったかを保証しない
        （実測 2026-08-29: ログだけを見ていたテストが、注入を外しても通った）。
        """
        finished = [
            e for e in events
            if e.type == "step" and e.step == "profile" and e.status == "finished"
        ]
        assert finished, "profile ステップが決着していない"
        return finished[0].data["injected_prompt_addendum"]

    def _run(self, pipeline_stub, verdicts, vertical="gov"):
        pipeline_stub.clusters = list(self.CLUSTERS)
        pipeline_stub.scope_verdicts = verdicts
        events: list[SupportEvent] = []
        run_support_agent_core(
            MULTI_QUERY, vertical=vertical, emit=events.append, do_action=False,
            confirm=lambda _r: AUTO_PROCEED,
        )
        return events

    def test_範囲外の質問文が生成側の方針へ入る(self, pipeline_stub):
        events = self._run(pipeline_stub, [True, False])
        injected = self._injected(events)
        assert "明日の東京の天気は？" in injected

    def test_窓口案内も方針へ入る(self, pipeline_stub):
        events = self._run(pipeline_stub, [True, False])
        assert "気象庁" in self._injected(events)

    def test_同じ回答の中で扱うよう指示される(self, pipeline_stub):
        """別の問い合わせとして先送りさせない（＝1 回のやり取りで両方に対応）。"""
        events = self._run(pipeline_stub, [True, False])
        assert "同じ回答の末尾に" in self._injected(events)

    def test_業界方針とスコープ方針は消えない(self, pipeline_stub):
        events = self._run(pipeline_stub, [True, False])
        injected = self._injected(events)
        assert "条例・公式案内に基づき" in injected
        assert "担当範囲は上記の業務領域に限る" in injected

    def test_ログにも残る(self, pipeline_stub):
        events = self._run(pipeline_stub, [True, False])
        logs = _logs(events, "profile")
        assert any("範囲外の質問を回答内で断るよう注入" in m for m in logs)

    def test_プロファイルステップのデータにも載る(self, pipeline_stub):
        events = self._run(pipeline_stub, [True, False])
        finished = [
            e for e in events
            if e.type == "step" and e.step == "profile" and e.status == "finished"
        ]
        assert finished[0].data["out_of_scope_questions"] == ["明日の東京の天気は？"]

    def test_範囲外が無ければ注入しない(self, pipeline_stub):
        events = self._run(pipeline_stub, [True, True])
        injected = self._injected(events)
        assert "担当範囲外の質問" not in injected
        assert not any("範囲外の質問を回答内で断る" in m for m in _logs(events, "profile"))

    def test_検索クエリは絞ったまま(self, pipeline_stub):
        """範囲外の質問文を渡すのは**生成側だけ**。検索は再構成後のクエリで行う。

        混在クエリで検索すると意味の重心がボケる（実測 0.7225 vs 0.8011）。
        """
        pipeline_stub.clusters = list(self.CLUSTERS)
        pipeline_stub.scope_verdicts = [True, False]
        result = run_support_agent_core(
            MULTI_QUERY, vertical="gov", do_action=False,
            confirm=lambda _r: AUTO_PROCEED,
        )
        assert result.adopted_cluster_index == 0
        assert result.out_of_scope_questions == ["明日の東京の天気は？"]


class TestSkipReason:
    """0-(A) がスキップされた理由を残す。

    「第 1 段で不一致（＝そもそも単一質問）」と「第 2 段が単一と判断（＝解析器や
    モデルの問題）」は原因がまったく違うのに、結果はどちらも skipped で見分けが
    つかなかった。実測 2026-08-29（クラウド版）で第 2 段が単一と判断した際、
    ログが 1 行も無く切り分けできなかった。
    """

    def _skipped(self, events):
        return [
            e for e in events
            if e.type == "step" and e.step == "analyze" and e.status == "skipped"
        ]

    def test_第1段で弾かれた場合(self, pipeline_stub):
        events: list[SupportEvent] = []
        run_support_agent_core(
            "パスワードを忘れました", emit=events.append,
            confirm=lambda _r: AUTO_PROCEED, do_action=False,
        )
        skipped = self._skipped(events)
        assert skipped and "第 1 段" in skipped[0].data["reason"]
        # 単一質問のたびにログを増やさない
        assert not _logs(events, "analyze")

    def test_第2段が単一と判断した場合(self, pipeline_stub):
        """解析器が呼ばれたのに単一へ倒れた＝調べる価値があるのでログも出す。"""
        pipeline_stub.clusters = None      # 解析器が「単一」を返す
        events: list[SupportEvent] = []
        run_support_agent_core(
            MULTI_QUERY, emit=events.append,
            confirm=lambda _r: AUTO_PROCEED, do_action=False,
        )
        skipped = self._skipped(events)
        assert skipped and "第 2 段" in skipped[0].data["reason"]
        assert any("複数質問として扱いません" in m for m in _logs(events, "analyze"))

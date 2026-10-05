# backend/tests/test_measure_rag_threshold.py
"""閾値計測スクリプトの **判定ロジック** を固定するテスト。

`scripts/measure_rag_threshold.py` は Qdrant と Embedding が要るので全体は
CI で回せない。ただし「測った値から閾値を決める」部分は純粋な計算なので、
ここだけ固定しておく。ここを間違えると**誤った閾値を推奨する**（＝
無関係文書の誤採用が本番に残る）ので、検索より先に守る価値がある。

判定の定義:
    TP フロア      = in_scope の最小 Top スコア（これ未満にすると取りこぼす）
    FP シーリング  = out_of_scope の最大 Top スコア（これ以下だと誤採用する）
    分離可能       = FP シーリング < TP フロア

2026-10-05 に grace_v2 の `scripts/measure_rag_scores.py` を統合したので、今のしきい値での
帯（不採用・強制 Web・適合性チェック）の判定と、業界ごとの質問の組み立ても守る（⑥〜⑧）。
grace_v2 と grace_v2_local に同じ内容で置く。

⚠️ Qdrant にも Embedding にも接続しない。
"""
from __future__ import annotations

import json

import pytest

from scripts.measure_rag_threshold import (
    DEFAULT_IN_SCOPE,
    DEFAULT_OUT_OF_SCOPE,
    analyze,
    main,
    measure,
    queries_for,
    report,
    zone,
)


def _rows(*scores):
    """`(query, score, collection)` の行を作る。"""
    return [(f"q{i}", s, "col") for i, s in enumerate(scores)]


# =============================================================================
# ① 分離できるとき
# =============================================================================

class TestSeparable:

    def test_returns_zero_when_separable(self, capsys):
        assert report(_rows(0.82, 0.79), _rows(0.45, 0.51)) == 0
        assert "分離できる" in capsys.readouterr().out

    def test_recommends_the_midpoint(self, capsys):
        """推奨値は FP シーリングと TP フロアの中間。"""
        report(_rows(0.80), _rows(0.50))
        # (0.50 + 0.80) / 2 = 0.65
        assert "reasoning_min_rag_score: 0.65" in capsys.readouterr().out

    def test_uses_the_worst_in_scope_not_the_average(self, capsys):
        """1 件でも低い in_scope があれば、そこがフロアになること。

        平均で決めると、その 1 件を取りこぼす閾値を推奨してしまう。
        """
        report(_rows(0.95, 0.95, 0.62), _rows(0.40))
        # 平均(0.84)ではなく最小(0.62)を使う → (0.40+0.62)/2 = 0.51
        assert "reasoning_min_rag_score: 0.51" in capsys.readouterr().out

    def test_uses_the_best_out_of_scope_not_the_average(self, capsys):
        """out_of_scope も最大で見る（1 件でも高ければ誤採用する）。"""
        report(_rows(0.90), _rows(0.20, 0.20, 0.70))
        # 平均(0.37)ではなく最大(0.70)を使う → (0.70+0.90)/2 = 0.80
        assert "reasoning_min_rag_score: 0.8" in capsys.readouterr().out


# =============================================================================
# ② 分離できないとき（実測の状況）
# =============================================================================

class TestNotSeparable:

    def test_reports_failure_when_overlapping(self, capsys):
        """実測「明日の東京の天気は？」= 0.6658 が in_scope 下限を超える状況。"""
        exit_code = report(_rows(0.60), _rows(0.6658))

        assert exit_code == 1
        out = capsys.readouterr().out
        assert "分離できない" in out
        assert "閾値調整では解決しない" in out, "誤った閾値を勧めてはいけない"

    def test_equal_values_are_not_separable(self):
        """境界が重なるだけでも分離不可（`>=`）。"""
        assert report(_rows(0.60), _rows(0.60)) == 1

    def test_shows_the_offending_queries(self, capsys):
        """どの質問が原因かを出すこと（出さないと次の手が打てない）。"""
        in_rows = [("答えられるべき質問", 0.55, "gov_faq_anthropic")]
        out_rows = [("明日の東京の天気は？", 0.6658, "cc_news_2per_anthropic")]

        report(in_rows, out_rows)
        out = capsys.readouterr().out

        assert "答えられるべき質問" in out
        assert "明日の東京の天気は？" in out
        assert "cc_news_2per_anthropic" in out


# =============================================================================
# ③ 測れなかったとき
# =============================================================================

class TestInsufficientData:

    @pytest.mark.parametrize(
        "in_rows,out_rows",
        [
            ([], _rows(0.5)),
            (_rows(0.5), []),
            ([("q", None, "-")], _rows(0.5)),   # 全件 0 件ヒット
            ([], []),
        ],
    )
    def test_returns_two_and_does_not_recommend(self, in_rows, out_rows, capsys):
        """データ不足のときに閾値を推奨しないこと（当てずっぽうが最悪）。"""
        assert report(in_rows, out_rows) == 2
        assert "reasoning_min_rag_score:" not in capsys.readouterr().out


# =============================================================================
# ④ 計測は「全コレクション中の最良」を取る
# =============================================================================

class TestMeasureTakesTheBestCollection:
    """`RAGSearchTool` が最高スコアのコレクションを採用する以上、
    採用是非を決めるのは「全コレクション中の最良スコア」である。
    最初に当たったコレクションで測ると閾値がずれる。
    """

    def test_picks_the_highest_across_collections(self, monkeypatch):
        scores = {"a": 0.30, "b": 0.71, "c": 0.55}
        monkeypatch.setattr(
            "scripts.measure_rag_threshold._top_score",
            lambda _q, collection: scores[collection],
        )

        rows = measure(["質問"], ["a", "b", "c"])

        assert rows == [("質問", 0.71, "b")]

    def test_missing_collections_are_skipped(self, monkeypatch):
        monkeypatch.setattr(
            "scripts.measure_rag_threshold._top_score",
            lambda _q, collection: None if collection == "a" else 0.42,
        )

        rows = measure(["質問"], ["a", "b"])

        assert rows == [("質問", 0.42, "b")]

    def test_all_empty_yields_none(self, monkeypatch):
        monkeypatch.setattr(
            "scripts.measure_rag_threshold._top_score", lambda _q, _c: None
        )

        assert measure(["質問"], ["a", "b"]) == [("質問", None, "-")]


# =============================================================================
# ⑤ 入力（質問セット）
# =============================================================================

class TestQueryFile:

    def test_queries_file_overrides_the_defaults(self, tmp_path, monkeypatch, capsys):
        path = tmp_path / "q.json"
        path.write_text(
            json.dumps({"in_scope": ["社内の質問"], "out_of_scope": ["外の質問"]}),
            encoding="utf-8",
        )

        seen = []

        def _fake_measure(queries, collections):
            seen.extend(queries)
            return [(q, 0.9 if q == "社内の質問" else 0.2, "col") for q in queries]

        monkeypatch.setattr("scripts.measure_rag_threshold.measure", _fake_measure)
        monkeypatch.setattr(
            "scripts.measure_rag_threshold._searchable_collections",
            lambda apply_exclusions=True: ["col"],
        )
        monkeypatch.setattr(
            "scripts.measure_rag_threshold._all_registered_collections", lambda: ["col"]
        )
        monkeypatch.setattr(
            "scripts.measure_rag_threshold._collections_for", lambda _v: None
        )

        out = tmp_path / "result.json"
        assert main(["--queries-file", str(path), "--out", str(out)]) == 0
        assert seen == ["社内の質問", "外の質問"]
        capsys.readouterr()

    def test_unknown_vertical_is_rejected(self):
        from scripts.measure_rag_threshold import _collections_for

        with pytest.raises(SystemExit):
            _collections_for("nonexistent")

    def test_all_vertical_means_every_collection(self):
        from scripts.measure_rag_threshold import _collections_for

        assert _collections_for("all") is None


# =============================================================================
# ⑥ 今のしきい値での帯（2026-10-05 に統合）
# =============================================================================

class TestZone:

    @pytest.mark.parametrize("score, expected", [
        (None, "rejected"),      # 緩和閾値にも届かない／0 件
        (0.50, "rejected"),      # 採用されない → Web へ
        (0.6399, "rejected"),
        (0.64, "relevance"),     # 採用される → LLM の適合性チェックが Web の要否を決める
        (0.665, "relevance"),
        (0.90, "relevance"),
    ])
    def test_zone_with_aligned_thresholds(self, score, expected):
        assert zone(score, 0.64, 0.64) == expected

    def test_forced_web_only_when_thresholds_disagree(self):
        """しきい値が食い違うと、採用したのに無条件で Web も検索する帯ができる（2026-10-04 までの 0.7）。"""
        assert zone(0.665, 0.64, 0.7) == "forced_web"


# =============================================================================
# ⑦ 業界ごと・全体の集計
# =============================================================================

class TestAnalyze:

    def _row(self, vertical, label, top):
        return {"vertical": vertical, "label": label, "query": "q", "top": top, "collection": "c"}

    def test_counts_behaviour_under_current_thresholds(self):
        rows = [
            self._row("gov", "in", 0.80),
            self._row("gov", "in", 0.665),
            self._row("gov", "in", None),     # 範囲内なのに採用されない
            self._row("gov", "out", 0.55),
            self._row("gov", "out", 0.66),    # 範囲外なのに採用される
        ]

        gov = analyze(rows, adopt=0.64, sufficient=0.7)["gov"]

        assert gov["in"] == {"n": 3, "min": 0.665, "median": 0.7325, "max": 0.8}
        assert gov["out"] == {"n": 2, "max": 0.66}
        assert gov["in_rejected"] == 1
        assert gov["in_forced_web"] == 1          # 0.665（0.64 ≤ s < 0.7）
        assert gov["out_adopted"] == 1
        assert gov["margin"] == pytest.approx(0.005)
        assert gov["midpoint"] == pytest.approx(0.6625)

    def test_midpoint_is_none_when_overlapping(self):
        rows = [self._row("ec", "in", 0.62), self._row("ec", "out", 0.66)]

        ec = analyze(rows, 0.64, 0.64)["ec"]

        assert ec["margin"] == pytest.approx(-0.04)
        assert ec["midpoint"] is None             # 1 本のしきい値では分けられない
        assert analyze(rows, 0.64, 0.64)["(all)"]["in"]["n"] == 1


# =============================================================================
# ⑧ 質問の組み立て
# =============================================================================

class TestQueriesFor:

    def test_all_uses_every_vertical_and_common_out_of_scope(self):
        ins, outs = queries_for("all")

        assert ins == [q for v in ("gov", "saas", "ec") for q in DEFAULT_IN_SCOPE[v]]
        assert outs == DEFAULT_OUT_OF_SCOPE

    def test_vertical_adds_other_verticals_as_out_of_scope(self):
        """他業界の質問は本番でいちばん起きやすい誤採用（実測: saas で「返品したい」0.6707）。"""
        ins, outs = queries_for("saas")

        assert ins == DEFAULT_IN_SCOPE["saas"]
        assert "返品したい" in outs and "住民票の写しの取り方は？" in outs
        assert not set(ins) & set(outs)

    def test_per_vertical_payload(self):
        payload = {"gov": {"in_scope": ["a"], "out_of_scope": ["b"]}}

        assert queries_for("gov", payload) == (["a"], ["b"])
        with pytest.raises(SystemExit):
            queries_for("ec", payload)

    def test_each_mode_writes_one_report_for_three_verticals(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("scripts.measure_rag_threshold._target_collections", lambda _v, _x: ["col"])
        monkeypatch.setattr("scripts.measure_rag_threshold._thresholds", lambda: (0.64, 0.64))
        monkeypatch.setattr(
            "scripts.measure_rag_threshold.measure",
            lambda queries, _c: [(q, 0.8 if q in DEFAULT_IN_SCOPE["gov"] + DEFAULT_IN_SCOPE["saas"]
                                  + DEFAULT_IN_SCOPE["ec"] else 0.55, "col") for q in queries],
        )
        out = tmp_path / "r.json"

        code = main(["--vertical", "each", "--out", str(out)])

        report_json = json.loads(out.read_text(encoding="utf-8"))
        capsys.readouterr()
        assert {r["vertical"] for r in report_json["results"]} == {"gov", "saas", "ec"}
        assert set(report_json["summary"]) == {"(all)", "gov", "saas", "ec"}
        assert report_json["thresholds"] == {"reasoning_min_rag_score": 0.64, "rag_sufficient_score": 0.64}
        # 他業界の質問は範囲外として 0.8 で測られる → 範囲外が採用され、分離できない
        assert report_json["summary"]["(all)"]["out_adopted"] > 0
        assert code == 1


# =============================================================================
# ⑨ 候補一覧は本番の RAGSearchTool から取る（両リポジトリのシグネチャに対応）
# =============================================================================

class TestSearchableCollections:

    def _install(self, monkeypatch, tool):
        import grace.tools

        monkeypatch.setattr(grace.tools, "RAGSearchTool", lambda config=None: tool)

    def test_tool_that_applies_exclusions_itself(self, monkeypatch):
        """grace_v2_local: `_get_all_collections_dynamic(apply_exclusions=...)`。"""
        from scripts.measure_rag_threshold import _searchable_collections

        seen = {}

        class Tool:
            def _get_all_collections_dynamic(self, apply_exclusions=True):
                seen["apply"] = apply_exclusions
                return ["gov_faq"]

        self._install(monkeypatch, Tool())

        assert _searchable_collections(apply_exclusions=False) == ["gov_faq"]
        assert seen == {"apply": False}

    def test_tool_with_separate_exclusion_step(self, monkeypatch):
        """grace_v2: `_get_all_collections_dynamic()` の後に `_apply_excluded_collections()`。"""
        from scripts.measure_rag_threshold import _searchable_collections

        class Tool:
            def _get_all_collections_dynamic(self):
                return ["gov_faq", "wikipedia_ja_5per"]

            def _apply_excluded_collections(self, names):
                return [n for n in names if "wikipedia" not in n]

        self._install(monkeypatch, Tool())

        assert _searchable_collections(apply_exclusions=True) == ["gov_faq"]
        assert _searchable_collections(apply_exclusions=False) == ["gov_faq", "wikipedia_ja_5per"]

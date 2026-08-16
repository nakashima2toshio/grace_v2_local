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

⚠️ Qdrant にも Embedding にも接続しない。
"""
from __future__ import annotations

import json

import pytest

from scripts.measure_rag_threshold import main, measure, report


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
            "scripts.measure_rag_threshold._all_collections", lambda: ["col"]
        )
        monkeypatch.setattr(
            "scripts.measure_rag_threshold._collections_for", lambda _v: ["col"]
        )

        assert main(["--queries-file", str(path)]) == 0
        assert seen == ["社内の質問", "外の質問"]
        capsys.readouterr()

    def test_unknown_vertical_is_rejected(self):
        from scripts.measure_rag_threshold import _collections_for

        with pytest.raises(SystemExit):
            _collections_for("nonexistent")

    def test_all_vertical_means_every_collection(self):
        from scripts.measure_rag_threshold import _collections_for

        assert _collections_for("all") is None

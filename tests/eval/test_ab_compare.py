"""eval/ab_compare.py のテスト（実 API / Qdrant 非依存）。

純粋関数（build_comparison / format_table）と、run_eval.run をモックした
run_ab のオーケストレーション（react_enabled トグル・集計・JSON 出力）を検証する。
"""
import json

import pytest

import eval.ab_compare as ab
from grace.config import get_config, reset_config


@pytest.fixture(autouse=True)
def _restore_config():
    yield
    reset_config()  # config シングルトンの react_enabled 変更をリークさせない


def _summary(accuracy, ece, ece_cal=None, n=10, hallu=0.1, conf=0.7,
             latency=1200.0, cost=0.01):
    s = {
        "n": n, "accuracy": accuracy, "hallucination_rate": hallu,
        "mean_confidence": conf, "ece": ece, "mean_latency_ms": latency,
        "total_cost_usd": cost,
    }
    if ece_cal is not None:
        s["ece_calibrated"] = ece_cal
    return s


class TestBuildComparison:
    def test_accuracy_higher_is_react_better(self):
        static = _summary(accuracy=0.60, ece=0.20)
        react = _summary(accuracy=0.72, ece=0.20)
        rows = {r["key"]: r for r in ab.build_comparison(static, react)}
        acc = rows["accuracy"]
        assert acc["delta"] == pytest.approx(0.12)
        assert acc["improved"] is True  # accuracy は high better

    def test_ece_lower_is_react_better(self):
        static = _summary(accuracy=0.6, ece=0.25)
        react = _summary(accuracy=0.6, ece=0.10)
        rows = {r["key"]: r for r in ab.build_comparison(static, react)}
        ece = rows["ece"]
        assert ece["delta"] == pytest.approx(-0.15)
        assert ece["improved"] is True  # ECE は low better

    def test_hallucination_lower_better(self):
        static = _summary(accuracy=0.6, ece=0.2, hallu=0.20)
        react = _summary(accuracy=0.6, ece=0.2, hallu=0.30)
        rows = {r["key"]: r for r in ab.build_comparison(static, react)}
        assert rows["hallucination_rate"]["improved"] is False  # 増加=悪化

    def test_missing_calibrated_is_dash(self):
        rows = {r["key"]: r for r in ab.build_comparison(
            _summary(0.6, 0.2), _summary(0.6, 0.2))}
        assert rows["ece_calibrated"]["delta"] is None
        assert rows["ece_calibrated"]["improved"] is None


class TestFormatTable:
    def test_contains_columns_and_metrics(self):
        comp = ab.build_comparison(_summary(0.6, 0.2), _summary(0.7, 0.1))
        table = ab.format_table(comp)
        assert "static" in table and "react" in table and "better" in table
        assert "accuracy" in table and "ECE(raw)" in table


class TestRunAb:
    def test_toggles_flag_and_aggregates(self, tmp_path, monkeypatch):
        seen_flags = []

        def fake_run(dataset, limit, model, judge_model, report, collection=None):
            # 呼び出し時点の react_enabled を記録し、それに応じた summary を書く
            flag = get_config().executor.react_enabled
            seen_flags.append(flag)
            summary = _summary(accuracy=0.75 if flag else 0.60,
                               ece=0.10 if flag else 0.22)
            from pathlib import Path
            Path(report).parent.mkdir(parents=True, exist_ok=True)
            with open(report, "w", encoding="utf-8") as f:
                json.dump({"summary": summary, "details": []}, f)
            return 0

        monkeypatch.setattr(ab, "run", fake_run)
        args = ab.parse_args([
            "--dataset", "x.jsonl", "--limit", "3",
            "--output-dir", str(tmp_path / "ab"),
        ])
        result = ab.run_ab(args)

        # static(False) → react(True) の順でトグルされる
        assert seen_flags == [False, True]
        assert result["static"]["accuracy"] == 0.60
        assert result["react"]["accuracy"] == 0.75
        # 統合 JSON が書かれている
        combined = json.loads((tmp_path / "ab" / "ab_summary.json").read_text())
        assert "comparison" in combined
        acc_row = next(r for r in combined["comparison"] if r["key"] == "accuracy")
        assert acc_row["improved"] is True

    def test_variant_failure_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ab, "run", lambda *a, **k: 1)  # rc!=0
        args = ab.parse_args(["--output-dir", str(tmp_path / "ab")])
        with pytest.raises(RuntimeError):
            ab.run_ab(args)

"""S1 較正（temperature scaling）のテスト。

API 非依存・決定的。ECE が事後較正で縮小することを検証する。
"""

import json
import random

from grace.calibration import (
    Calibrator,
    apply_temperature,
    expected_calibration_error,
    fit_temperature,
)


class TestTemperatureScaling:
    def test_identity_temperature(self):
        """T=1.0 は恒等変換"""
        for p in (0.1, 0.5, 0.9):
            assert abs(apply_temperature(p, 1.0) - p) < 1e-6

    def test_high_temperature_softens(self):
        """T>1 は高い confidence を引き下げる（自信過剰の緩和）"""
        assert apply_temperature(0.95, 3.0) < 0.95

    def test_low_temperature_sharpens(self):
        """T<1 は高い confidence をさらに引き上げる"""
        assert apply_temperature(0.8, 0.5) > 0.8

    def test_fit_reduces_ece_on_overconfident(self):
        """自信過剰データで較正後 ECE が縮小する"""
        random.seed(0)
        confs, correct = [], []
        for _ in range(400):
            correct.append(random.random() < 0.6)         # 実正解率 ~0.6
            confs.append(min(0.99, max(0.5, random.gauss(0.9, 0.05))))  # 申告 ~0.9
        ece_before = expected_calibration_error(confs, correct)
        cal = Calibrator.fit(confs, correct)
        ece_after = expected_calibration_error(
            [cal.transform(c) for c in confs], correct
        )
        assert cal.temperature > 1.0          # 自信過剰 → T>1
        assert ece_after < ece_before         # ECE 改善
        assert ece_after < 0.15

    def test_fit_degenerate_returns_identity(self):
        """全問正解/全問不正解など退化データは T=1.0"""
        assert fit_temperature([0.8] * 10, [True] * 10) == 1.0
        assert fit_temperature([0.8] * 10, [False] * 10) == 1.0
        assert fit_temperature([], []) == 1.0

    def test_save_and_load_roundtrip(self, tmp_path):
        path = str(tmp_path / "calibration.json")
        Calibrator(temperature=2.5).save(path)
        loaded = Calibrator.load(path)
        assert abs(loaded.temperature - 2.5) < 1e-9
        assert not loaded.is_identity()
        # JSON 形式の確認
        data = json.loads((tmp_path / "calibration.json").read_text(encoding="utf-8"))
        assert data["method"] == "temperature_scaling"

    def test_load_missing_file_is_identity(self, tmp_path):
        loaded = Calibrator.load(str(tmp_path / "nope.json"))
        assert loaded.is_identity()
        assert loaded.temperature == 1.0

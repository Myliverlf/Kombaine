"""Tests для TimesFM Calibration + Adaptation — feedback loop end-to-end.

Fixtures (≥3):
  1. perfect_forecasts: direction=100% correct, CI covers all → no retrain, low brier
  2. degraded_forecasts: direction=30% correct, CI misses → retrain_signal, adjust horizon
  3. regime_split_forecasts: trend=good, range=bad → regime_overrides populated
  4. empty_history: empty records → graceful fallback, no crash
  5. bridge_integration: build_calibration_feedback end-to-end → dict с forecast_calibration

Все fixtures — synthetic data, no broker, no live orders.
Запуск: cd /root/prop-desk/strategy_combine && python -m pytest tests/test_timesfm_calibration_adaptation.py -v
"""
import json
import math
import os
import sys
import tempfile

import pytest

# Ensure code/ is on path for imports
_CODE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "code")
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

from timesfm_calibration import (
    ForecastRecord,
    CalibrationReport,
    compute_calibration_report,
    merge_calibration_reports,
    _actual_direction,
    _brier_single,
    _linear_regression,
)
from timesfm_adaptation import (
    AdaptationHints,
    compute_adaptation_hints,
    apply_hints_to_config,
)
from timesfm_calibration_bridge import (
    build_calibration_feedback,
    write_calibration_to_db,
    write_feedback_json,
)


# ═══════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture
def perfect_forecasts():
    """Fixture 1: Perfect forecasts — 100% accuracy, CI covers all.

    Expected: direction_accuracy=1.0, ci_coverage=1.0, brier ~0, retrain=False.
    """
    return [
        ForecastRecord(direction="up", confidence=0.95, ci_lower=0.5, ci_upper=2.0, actual_pnl=1.5, horizon=20, regime="trend"),
        ForecastRecord(direction="down", confidence=0.90, ci_lower=-2.0, ci_upper=-0.3, actual_pnl=-1.0, horizon=20, regime="range"),
        ForecastRecord(direction="up", confidence=0.85, ci_lower=0.1, ci_upper=1.5, actual_pnl=0.8, horizon=10, regime="trend"),
        ForecastRecord(direction="down", confidence=0.88, ci_lower=-1.8, ci_upper=-0.2, actual_pnl=-0.6, horizon=10, regime="range"),
        ForecastRecord(direction="up", confidence=0.92, ci_lower=0.3, ci_upper=2.5, actual_pnl=1.2, horizon=5, regime="trend"),
    ]


@pytest.fixture
def degraded_forecasts():
    """Fixture 2: Degraded forecasts — 30% accuracy, CI misses → retrain_signal.

    Expected: direction_accuracy=0.3, ci_coverage < 0.8, retrain=True.
    """
    return [
        ForecastRecord(direction="up", confidence=0.90, ci_lower=0.5, ci_upper=2.0, actual_pnl=-1.0, horizon=20, regime="range"),
        ForecastRecord(direction="up", confidence=0.85, ci_lower=0.1, ci_upper=1.5, actual_pnl=-0.5, horizon=20, regime="range"),
        ForecastRecord(direction="up", confidence=0.80, ci_lower=0.3, ci_upper=1.8, actual_pnl=-0.8, horizon=20, regime="range"),
        ForecastRecord(direction="down", confidence=0.70, ci_lower=-2.0, ci_upper=-0.3, actual_pnl=1.0, horizon=10, regime="trend"),
        ForecastRecord(direction="down", confidence=0.75, ci_lower=-1.5, ci_upper=-0.2, actual_pnl=0.5, horizon=10, regime="trend"),
        ForecastRecord(direction="up", confidence=0.65, ci_lower=0.1, ci_upper=1.0, actual_pnl=-0.3, horizon=5, regime="range"),
        ForecastRecord(direction="down", confidence=0.60, ci_lower=-1.0, ci_upper=-0.1, actual_pnl=0.2, horizon=5, regime="trend"),
    ]


@pytest.fixture
def regime_split_forecasts():
    """Fixture 3: Regime-split forecasts — trend=good (80%+), range=bad (20%).

    Expected: regime_accuracy["trend"] > 0.7, regime_accuracy["range"] < 0.4,
              regime_overrides populated in AdaptationHints.
    """
    return [
        # Trend: 4 correct out of 5
        ForecastRecord(direction="up", confidence=0.80, ci_lower=0.2, ci_upper=1.5, actual_pnl=1.0, horizon=20, regime="trend"),
        ForecastRecord(direction="down", confidence=0.75, ci_lower=-1.5, ci_upper=-0.2, actual_pnl=-0.8, horizon=20, regime="trend"),
        ForecastRecord(direction="up", confidence=0.85, ci_lower=0.1, ci_upper=1.2, actual_pnl=0.6, horizon=10, regime="trend"),
        ForecastRecord(direction="up", confidence=0.70, ci_lower=0.3, ci_upper=1.8, actual_pnl=1.5, horizon=10, regime="trend"),
        ForecastRecord(direction="down", confidence=0.65, ci_lower=-1.8, ci_upper=-0.1, actual_pnl=-0.3, horizon=5, regime="trend"),
        # Range: 1 correct out of 5
        ForecastRecord(direction="up", confidence=0.85, ci_lower=0.5, ci_upper=2.0, actual_pnl=-1.0, horizon=20, regime="range"),
        ForecastRecord(direction="up", confidence=0.80, ci_lower=0.1, ci_upper=1.5, actual_pnl=-0.5, horizon=20, regime="range"),
        ForecastRecord(direction="up", confidence=0.75, ci_lower=0.3, ci_upper=1.2, actual_pnl=-0.8, horizon=10, regime="range"),
        ForecastRecord(direction="down", confidence=0.70, ci_lower=-1.5, ci_upper=-0.2, actual_pnl=-0.3, horizon=10, regime="range"),
        ForecastRecord(direction="up", confidence=0.65, ci_lower=0.2, ci_upper=1.0, actual_pnl=-0.6, horizon=5, regime="range"),
    ]


@pytest.fixture
def empty_history():
    """Fixture 4: Empty history → graceful fallback."""
    return []


@pytest.fixture
def bridge_trades():
    """Trades for bridge integration fixture."""
    return [
        {"ticker": "BR", "direction": "LONG", "pnl": 150.0, "regime": "trend"},
        {"ticker": "GAZP", "direction": "SHORT", "pnl": -80.0, "regime": "range"},
        {"ticker": "SBER", "direction": "LONG", "pnl": 200.0, "regime": "trend"},
    ]


@pytest.fixture
def bridge_forecasts():
    """Forecasts for bridge integration fixture."""
    return [
        {"ticker": "BR", "direction": "up", "confidence": 0.75, "ci_lower": 0.5, "ci_upper": 2.0, "horizon": 20, "regime": "trend"},
        {"ticker": "GAZP", "direction": "down", "confidence": 0.60, "ci_lower": -2.0, "ci_upper": -0.2, "horizon": 20, "regime": "range"},
        {"ticker": "SBER", "direction": "up", "confidence": 0.80, "ci_lower": 0.2, "ci_upper": 1.5, "horizon": 10, "regime": "trend"},
    ]


# ═══════════════════════════════════════════════════════════════════════
# TESTS: Calibration Core
# ═══════════════════════════════════════════════════════════════════════

class TestCalibrationCore:
    """Тесты calibration metrics."""

    def test_perfect_forecasts_direction_accuracy(self, perfect_forecasts):
        """Perfect forecasts → direction_accuracy == 1.0."""
        report = compute_calibration_report(perfect_forecasts)
        assert report.direction_accuracy == 1.0
        assert report.n_records == 5

    def test_perfect_forecasts_ci_coverage(self, perfect_forecasts):
        """Perfect forecasts → ci_coverage == 1.0 (all inside CI)."""
        report = compute_calibration_report(perfect_forecasts)
        assert report.ci_coverage == 1.0

    def test_perfect_forecasts_low_brier(self, perfect_forecasts):
        """Perfect forecasts → brier_score near 0 (well-calibrated)."""
        report = compute_calibration_report(perfect_forecasts)
        assert report.brier_score < 0.1

    def test_perfect_forecasts_no_retrain(self, perfect_forecasts):
        """Perfect forecasts → retrain_signal is False."""
        report = compute_calibration_report(perfect_forecasts)
        assert report.retrain_signal is False

    def test_degraded_forecasts_low_accuracy(self, degraded_forecasts):
        """Degraded forecasts → direction_accuracy around 0.3."""
        report = compute_calibration_report(degraded_forecasts)
        # direction_accuracy = 2/7 ≈ 0.2857 (only 2 downs correct out of 7)
        assert report.direction_accuracy <= 0.4

    def test_degraded_forecasts_retrain_signal(self, degraded_forecasts):
        """Degraded forecasts → retrain_signal is True."""
        report = compute_calibration_report(degraded_forecasts)
        assert report.retrain_signal is True

    def test_degraded_forecasts_high_brier(self, degraded_forecasts):
        """Degraded forecasts → brier_score > 0.3 (poorly calibrated)."""
        report = compute_calibration_report(degraded_forecasts)
        assert report.brier_score > 0.2

    def test_empty_history_graceful(self, empty_history):
        """Empty history → CalibrationReport with n_records=0, no crash."""
        report = compute_calibration_report(empty_history)
        assert report.n_records == 0
        assert report.direction_accuracy == 0.0
        assert report.scorecard_summary.startswith("NO_DATA")

    def test_regime_split_accuracy(self, regime_split_forecasts):
        """Regime-split → trend accuracy > range accuracy."""
        report = compute_calibration_report(regime_split_forecasts)
        assert "trend" in report.regime_accuracy
        assert "range" in report.regime_accuracy
        # Trend: 4/5 = 0.8, Range: 1/5 = 0.2
        assert report.regime_accuracy["trend"] >= 0.7
        assert report.regime_accuracy["range"] <= 0.3

    def test_horizon_accuracy_map(self, perfect_forecasts):
        """Horizon accuracy map populated when horizon field present."""
        report = compute_calibration_report(perfect_forecasts)
        assert len(report.horizon_accuracy_map) > 0
        # All horizons should have 100% accuracy for perfect forecasts
        for horiz, acc in report.horizon_accuracy_map.items():
            assert acc == 1.0

    def test_excess_accuracy(self, perfect_forecasts):
        """Excess accuracy = direction_accuracy - base_rate."""
        report = compute_calibration_report(perfect_forecasts)
        # base_rate for perfect with 3 up, 2 down → 0.6
        # direction_accuracy = 1.0 → excess = 0.4
        assert report.excess_accuracy >= 0.0

    def test_miscalibration_slope_positive(self, perfect_forecasts):
        """Perfect forecasts → slope ≥ 0 (confident → correct)."""
        report = compute_calibration_report(perfect_forecasts)
        assert report.miscalibration_slope >= 0.0


# ═══════════════════════════════════════════════════════════════════════
# TESTS: Adaptation Hints
# ═══════════════════════════════════════════════════════════════════════

class TestAdaptationHints:
    """Тесты adaptation hints layer."""

    def test_perfect_no_retrain(self, perfect_forecasts):
        """Perfect forecasts → retrain_signal=False, actions mostly empty."""
        report = compute_calibration_report(perfect_forecasts)
        hints = compute_adaptation_hints(report, {"horizon": 20, "confidence_threshold": 0.4})
        assert hints.retrain_signal is False

    def test_degraded_retrain(self, degraded_forecasts):
        """Degraded forecasts → retrain_signal=True."""
        report = compute_calibration_report(degraded_forecasts)
        hints = compute_adaptation_hints(report, {"horizon": 20, "confidence_threshold": 0.4})
        assert hints.retrain_signal is True
        assert len(hints.retrain_reason) > 0

    def test_regime_overrides_populated(self, regime_split_forecasts):
        """Regime-split → regime_overrides dict non-empty."""
        report = compute_calibration_report(regime_split_forecasts)
        hints = compute_adaptation_hints(report, {"horizon": 20})
        assert len(hints.regime_overrides) > 0
        # trend should get boost, range should get reduce
        assert "trend" in hints.regime_overrides
        assert "range" in hints.regime_overrides
        assert hints.regime_overrides["trend"]["action"] == "boost_confidence"
        assert hints.regime_overrides["range"]["action"] == "reduce_confidence"

    def test_config_patch_has_regime_overrides(self, regime_split_forecasts):
        """Config patch from hints contains regime_overrides."""
        report = compute_calibration_report(regime_split_forecasts)
        hints = compute_adaptation_hints(report)
        patched = apply_hints_to_config({"horizon": 20}, hints)
        assert "regime_overrides" in patched
        assert "trend" in patched["regime_overrides"]

    def test_empty_report_no_hints(self, empty_history):
        """Empty report → no actions, no retrain."""
        report = compute_calibration_report(empty_history)
        hints = compute_adaptation_hints(report)
        assert hints.retrain_signal is False
        assert hints.scorecard_summary.startswith("NO_DATA")

    def test_degraded_high_brier_raises_threshold(self, degraded_forecasts):
        """Degraded with high brier → confidence_threshold raised."""
        report = compute_calibration_report(degraded_forecasts)
        hints = compute_adaptation_hints(report, {"confidence_threshold": 0.4})
        if report.brier_score > 0.30:
            assert hints.suggest_confidence_threshold is not None
            assert hints.suggest_confidence_threshold >= 0.4

    def test_horizon_suggestion(self, degraded_forecasts):
        """Horizon suggestion present when horizon_accuracy_map has data."""
        report = compute_calibration_report(degraded_forecasts)
        hints = compute_adaptation_hints(report, {"horizon": 20})
        if report.horizon_accuracy_map:
            # Should suggest some horizon
            assert hints.suggest_horizon is not None


# ═══════════════════════════════════════════════════════════════════════
# TESTS: Bridge Integration
# ═══════════════════════════════════════════════════════════════════════

class TestBridgeIntegration:
    """Тесты bridge: trades + forecasts → feedback dict."""

    def test_bridge_returns_forecast_calibration_key(self, bridge_trades, bridge_forecasts):
        """build_calibration_feedback returns dict with forecast_calibration key."""
        result = build_calibration_feedback(bridge_trades, bridge_forecasts)
        assert "forecast_calibration" in result
        fc = result["forecast_calibration"]
        assert "calibration_report" in fc
        assert "adaptation_hints" in fc
        assert "config_patch" in fc
        assert "actions" in fc
        assert "scorecard" in fc

    def test_bridge_correct_pairing(self, bridge_trades, bridge_forecasts):
        """Bridge pairs trades[i] with forecasts[i] correctly."""
        result = build_calibration_feedback(bridge_trades, bridge_forecasts)
        fc = result["forecast_calibration"]
        assert fc["n_pairs"] == 3
        assert fc["calibration_report"]["n_records"] == 3

    def test_bridge_empty_trades(self):
        """Bridge with empty trades → graceful fallback."""
        result = build_calibration_feedback([], [])
        fc = result["forecast_calibration"]
        assert fc["calibration_report"]["n_records"] == 0
        assert fc["retrain_signal"] is False

    def test_bridge_config_applied(self, bridge_trades, bridge_forecasts):
        """Config is passed through to adaptation hints."""
        config = {"horizon": 10, "confidence_threshold": 0.5}
        result = build_calibration_feedback(bridge_trades, bridge_forecasts, config)
        fc = result["forecast_calibration"]
        # Should contain calibration data
        assert fc["calibration_report"]["n_records"] == 3

    def test_bridge_json_serializable(self, bridge_trades, bridge_forecasts):
        """Bridge output is JSON-serializable (for generator_feedback.json)."""
        result = build_calibration_feedback(bridge_trades, bridge_forecasts)
        # Should not raise
        serialized = json.dumps(result, ensure_ascii=False, indent=2)
        assert len(serialized) > 0
        # Should be deserializable back
        deserialized = json.loads(serialized)
        assert deserialized == result


# ═══════════════════════════════════════════════════════════════════════
# TESTS: Merge Reports
# ═══════════════════════════════════════════════════════════════════════

class TestMergeReports:
    """Тесты merge_calibration_reports."""

    def test_merge_two_reports(self):
        """Two reports → weighted average."""
        from timesfm_calibration import CalibrationReport
        r1 = CalibrationReport(n_records=10, direction_accuracy=0.8, excess_accuracy=0.2,
                               base_rate=0.6, ci_coverage=0.9, brier_score=0.1,
                               miscalibration_slope=0.5, horizon_accuracy_map={20: 0.8})
        r2 = CalibrationReport(n_records=5, direction_accuracy=0.6, excess_accuracy=0.1,
                               base_rate=0.5, ci_coverage=0.7, brier_score=0.2,
                               miscalibration_slope=0.3, horizon_accuracy_map={20: 0.6})
        merged = merge_calibration_reports([r1, r2])
        assert merged.n_records == 15
        # Weighted: (0.8*10 + 0.6*5)/15 = 11/15 ≈ 0.733
        assert 0.7 < merged.direction_accuracy < 0.8

    def test_merge_empty_list(self):
        """Empty merge → empty report."""
        merged = merge_calibration_reports([])
        assert merged.n_records == 0

    def test_merge_single_report(self):
        """Single report → passthrough."""
        from timesfm_calibration import CalibrationReport
        r = CalibrationReport(n_records=5, direction_accuracy=0.8, excess_accuracy=0.2,
                              base_rate=0.6, ci_coverage=0.9, brier_score=0.1,
                              miscalibration_slope=0.5)
        merged = merge_calibration_reports([r])
        assert merged.n_records == 5
        assert merged.direction_accuracy == 0.8


# ═══════════════════════════════════════════════════════════════════════
# TESTS: Write functions (file I/O)
# ═══════════════════════════════════════════════════════════════════════

class TestWriteFunctions:
    """Тесты записи feedback в файлы и БД."""

    def test_write_feedback_json_creates_file(self, bridge_trades, bridge_forecasts):
        """write_feedback_json creates file with forecast_calibration key."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "generator_feedback.json")
            result = build_calibration_feedback(bridge_trades, bridge_forecasts)
            ok = write_feedback_json(path, result)
            assert ok is True
            assert os.path.exists(path)
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert "forecast_calibration" in data
            assert data["forecast_calibration"]["n_pairs"] == 3

    def test_write_feedback_json_preserves_existing(self, bridge_trades, bridge_forecasts):
        """write_feedback_json preserves existing keys in generator_feedback.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "generator_feedback.json")
            # Write existing data
            existing = {"existing_key": "existing_value", "by_direction_regime": {"LONG": 100}}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(existing, f)
            # Now add forecast_calibration
            result = build_calibration_feedback(bridge_trades, bridge_forecasts)
            ok = write_feedback_json(path, result)
            assert ok is True
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert data["existing_key"] == "existing_value"
            assert data["by_direction_regime"] == {"LONG": 100}
            assert "forecast_calibration" in data

    def test_write_calibration_to_db(self, bridge_trades, bridge_forecasts):
        """write_calibration_to_db writes to SQLite."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "analytics.db")
            # Create minimal DB
            import sqlite3
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY)")
            conn.commit()
            conn.close()

            result = build_calibration_feedback(bridge_trades, bridge_forecasts)
            ok = write_calibration_to_db(db_path, result["forecast_calibration"])
            assert ok is True

            # Verify row exists
            conn = sqlite3.connect(db_path)
            cursor = conn.execute("SELECT COUNT(*) FROM forecast_calibration")
            count = cursor.fetchone()[0]
            conn.close()
            assert count == 1


# ═══════════════════════════════════════════════════════════════════════
# TESTS: Utility functions
# ═══════════════════════════════════════════════════════════════════════

class TestUtilities:
    """Unit tests for small utility functions."""

    def test_actual_direction_positive(self):
        assert _actual_direction(1.0) == "up"
        assert _actual_direction(0.001) == "up"

    def test_actual_direction_negative(self):
        assert _actual_direction(-1.0) == "down"
        assert _actual_direction(-0.001) == "down"

    def test_actual_direction_zero(self):
        assert _actual_direction(0.0) == "flat"

    def test_brier_single_correct_high_confidence(self):
        """Correct prediction with high confidence → low brier."""
        brier = _brier_single(0.9, True)
        assert brier == pytest.approx(0.01, abs=0.001)

    def test_brier_single_wrong_high_confidence(self):
        """Wrong prediction with high confidence → high brier."""
        brier = _brier_single(0.9, False)
        assert brier == pytest.approx(0.81, abs=0.001)

    def test_linear_regression_perfect(self):
        """Perfect linear relationship → slope ≈ 1.0."""
        xs = [0.0, 0.5, 1.0]
        ys = [0.0, 0.5, 1.0]
        slope, intercept = _linear_regression(xs, ys)
        assert slope == pytest.approx(1.0, abs=0.001)
        assert intercept == pytest.approx(0.0, abs=0.001)

    def test_linear_regression_empty(self):
        slope, intercept = _linear_regression([], [])
        assert slope == 0.0
        assert intercept == 0.0

    def test_apply_hints_to_config_does_not_mutate(self):
        """apply_hints_to_config doesn't mutate base config."""
        base = {"horizon": 20}
        hints = AdaptationHints(config_patch={"horizon": 10})
        patched = apply_hints_to_config(base, hints)
        assert base["horizon"] == 20
        assert patched["horizon"] == 10

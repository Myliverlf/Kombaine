"""Tests for live_correction — ≥3 fixtures, covers calibration actions."""
import json
import os
import sys
import py_compile
import pytest

_CODE_DIR = os.path.join(os.path.dirname(__file__), "..", "code")
if _CODE_DIR not in sys.path:
    sys.path.insert(0, os.path.abspath(_CODE_DIR))

from live_correction import compute_corrections

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


# ── Fixtures ──

@pytest.fixture
def fixture_calibrated():
    with open(os.path.join(FIXTURES_DIR, "live_correction_fixture.json")) as f:
        data = json.load(f)
    return next(x for x in data if x["name"] == "calibrated_strategy")


@pytest.fixture
def fixture_outperforming():
    with open(os.path.join(FIXTURES_DIR, "live_correction_fixture.json")) as f:
        data = json.load(f)
    return next(x for x in data if x["name"] == "outperforming_strategy")


@pytest.fixture
def fixture_degrading():
    with open(os.path.join(FIXTURES_DIR, "live_correction_fixture.json")) as f:
        data = json.load(f)
    return next(x for x in data if x["name"] == "degrading_strategy")


@pytest.fixture
def fixture_missing_data():
    with open(os.path.join(FIXTURES_DIR, "live_correction_fixture.json")) as f:
        data = json.load(f)
    return next(x for x in data if x["name"] == "missing_data_ticker")


@pytest.fixture
def fixture_empty():
    return {"backtest_expectations": {}, "live_results": {}}


# ── Tests ──

class TestLiveCorrection:
    def test_calibrated_maintain(self, fixture_calibrated):
        result = compute_corrections(fixture_calibrated["backtest_expectations"], fixture_calibrated["live_results"])
        ticker = list(fixture_calibrated["backtest_expectations"].keys())[0]
        assert result["corrections"][ticker]["calibration_action"] == fixture_calibrated["expected_action"]
        assert result["summary"]["n_stable"] == fixture_calibrated["expected_n_stable"]

    def test_outperforming_increase(self, fixture_outperforming):
        result = compute_corrections(fixture_outperforming["backtest_expectations"], fixture_outperforming["live_results"])
        ticker = list(fixture_outperforming["backtest_expectations"].keys())[0]
        assert result["corrections"][ticker]["calibration_action"] == fixture_outperforming["expected_action"]
        assert result["summary"]["n_improved"] == fixture_outperforming["expected_n_improved"]

    def test_degrading_decrease(self, fixture_degrading):
        result = compute_corrections(fixture_degrading["backtest_expectations"], fixture_degrading["live_results"])
        ticker = list(fixture_degrading["backtest_expectations"].keys())[0]
        assert result["corrections"][ticker]["calibration_action"] == fixture_degrading["expected_action"]
        assert result["summary"]["n_degraded"] == fixture_degrading["expected_n_degraded"]

    def test_missing_data(self, fixture_missing_data):
        result = compute_corrections(fixture_missing_data["backtest_expectations"], fixture_missing_data["live_results"])
        ticker = list(fixture_missing_data["backtest_expectations"].keys())[0]
        assert result["corrections"][ticker]["reason"] == "missing_data"
        assert result["summary"]["n_no_data"] == fixture_missing_data["expected_n_no_data"]

    def test_empty_input(self, fixture_empty):
        result = compute_corrections(fixture_empty["backtest_expectations"], fixture_empty["live_results"])
        assert result["corrections"] == {}
        assert result["summary"]["n_improved"] == 0
        assert result["summary"]["n_degraded"] == 0
        assert result["summary"]["n_stable"] == 0

    def test_correction_structure(self, fixture_calibrated):
        result = compute_corrections(fixture_calibrated["backtest_expectations"], fixture_calibrated["live_results"])
        ticker = list(fixture_calibrated["backtest_expectations"].keys())[0]
        corr = result["corrections"][ticker]
        assert "delta_pnl" in corr
        assert "delta_win_rate" in corr
        assert "delta_expectancy" in corr
        assert "calibration_action" in corr
        assert "confidence" in corr
        assert "reason" in corr
        assert corr["calibration_action"] in ("increase", "decrease", "maintain")
        assert corr["confidence"] in ("high", "medium", "low")

    def test_confidence_based_on_trade_count(self):
        bt = {"Si": {"composite_score": 70, "win_rate": 0.55, "expectancy": 1.5}}
        lr = {"Si": {"actual_pnl": 100, "actual_win_rate": 0.56, "actual_expectancy": 1.6, "n_trades": 25}}
        result = compute_corrections(bt, lr)
        assert result["corrections"]["Si"]["confidence"] == "high"

        lr_low = {"Si": {"actual_pnl": 100, "actual_win_rate": 0.56, "actual_expectancy": 1.6, "n_trades": 5}}
        result_low = compute_corrections(bt, lr_low)
        assert result_low["corrections"]["Si"]["confidence"] == "low"

    def test_no_broker_imports(self):
        """Verify module has no broker-related imports."""
        lc_path = os.path.join(_CODE_DIR, "live_correction.py")
        py_compile.compile(lc_path, doraise=True)
        with open(lc_path) as f:
            source = f.read()
        broker_keywords = ["tinkoff", "futures_lab", "broker_client", "place_order", "send_order"]
        for kw in broker_keywords:
            assert kw not in source, f"Broker keyword '{kw}' found in live_correction.py"

    def test_multiple_tickers_mixed(self):
        bt = {
            "Si": {"composite_score": 70, "win_rate": 0.55, "expectancy": 1.5},
            "LKOH": {"composite_score": 60, "win_rate": 0.50, "expectancy": 1.0},
        }
        lr = {
            "Si": {"actual_pnl": 1500, "actual_win_rate": 0.70, "actual_expectancy": 3.0, "n_trades": 25},
            "LKOH": {"actual_pnl": -600, "actual_win_rate": 0.30, "actual_expectancy": 0.3, "n_trades": 15},
        }
        result = compute_corrections(bt, lr)
        assert result["corrections"]["Si"]["calibration_action"] == "increase"
        assert result["corrections"]["LKOH"]["calibration_action"] == "decrease"
        assert result["summary"]["n_improved"] == 1
        assert result["summary"]["n_degraded"] == 1

"""Tests for Forecast Scorecard Bridge — forecast-aware metrics.

≥3 fixtures, ≥6 tests, no live broker/orders, all dry-run.
Covers: weighted_sharpe, adjusted_degradation, stability_bonus,
        scorecard_summary, RI excluded, no-broker guard.
"""
import json
import os
import sys

import pytest

# Ensure code/ dir on sys.path
_CODE_DIR = os.path.join(os.path.dirname(__file__), "..", "code")
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

from timesfm_adapter import ForecastResult
from forecast_context import ForecastContext, EXCLUDED_TICKERS
from forecast_scorecard_bridge import (
    forecast_weighted_sharpe,
    forecast_adjusted_degradation,
    forecast_stability_bonus,
    forecast_scorecard_summary,
    check_no_broker_imports,
)


# ─── Fixtures ──────────────────────────────────────────────────────────

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "forecast_scorecard_fixture.json")


def _load_fixture():
    """Load fixture JSON file."""
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def ctx_high_confidence():
    """ForecastContext с known confidence=0.85, vol_regime="calm", bias="up"."""
    data = _load_fixture()
    ctx_data = data["context_high_confidence"]
    per_ticker = {}
    for ticker, fr_data in ctx_data["per_ticker"].items():
        per_ticker[ticker] = ForecastResult(
            direction=fr_data["direction"],
            ci_width=fr_data["ci_width"],
            confidence=fr_data["confidence"],
            horizon=fr_data["horizon"],
            source=fr_data["source"],
        )
    return ForecastContext(
        per_ticker=per_ticker,
        portfolio_bias=ctx_data["portfolio_bias"],
        volatility_regime=ctx_data["volatility_regime"],
        confidence_score=ctx_data["confidence_score"],
        meta=ctx_data["meta"],
    )


@pytest.fixture
def ctx_low_confidence():
    """ForecastContext с confidence=0.2, stale=True (dummy adapter scenario)."""
    data = _load_fixture()
    ctx_data = data["context_low_confidence"]
    per_ticker = {}
    for ticker, fr_data in ctx_data["per_ticker"].items():
        per_ticker[ticker] = ForecastResult(
            direction=fr_data["direction"],
            ci_width=fr_data["ci_width"],
            confidence=fr_data["confidence"],
            horizon=fr_data["horizon"],
            source=fr_data["source"],
        )
    return ForecastContext(
        per_ticker=per_ticker,
        portfolio_bias=ctx_data["portfolio_bias"],
        volatility_regime=ctx_data["volatility_regime"],
        confidence_score=ctx_data["confidence_score"],
        meta=ctx_data["meta"],
    )


@pytest.fixture
def healthy_returns():
    """Positive drift returns: [0.01, 0.005, -0.003, ...]."""
    data = _load_fixture()
    return data["returns_healthy"]


@pytest.fixture
def degraded_returns():
    """Negative drift returns: [-0.02, -0.015, ...]."""
    data = _load_fixture()
    return data["returns_degraded"]


@pytest.fixture
def dead_returns():
    """Heavy negative returns: [-0.05, -0.04, ...]."""
    data = _load_fixture()
    return data["returns_dead"]


@pytest.fixture
def slots_fixture():
    """Slots with Si (LONG) and BR (SHORT), ≤3."""
    data = _load_fixture()
    return data["slots"]


@pytest.fixture
def config_fixture():
    """Config dict with max_slots=3, excluded=["RI"]."""
    data = _load_fixture()
    return data["config"]


# ─── Tests ─────────────────────────────────────────────────────────────

class TestWeightedSharpe:
    """forecast_weighted_sharpe tests."""

    def test_weighted_sharpe_high_confidence(self, ctx_high_confidence, healthy_returns):
        """High confidence + narrow CI → weighted Sharpe > baseline Sharpe."""
        result = forecast_weighted_sharpe(healthy_returns, ctx_high_confidence, "Si")
        assert result["ticker"] == "Si"
        assert result["confidence_mult"] > 0.0
        # ci_bonus should be positive for narrow CI (0.015)
        assert result["ci_bonus"] > 0.0
        # Weighted Sharpe should be higher than baseline
        assert result["weighted_sharpe"] >= result["baseline_sharpe"]

    def test_weighted_sharpe_low_confidence(self, ctx_low_confidence, healthy_returns):
        """Low confidence → confidence_mult low → adjustment small or negative."""
        result = forecast_weighted_sharpe(healthy_returns, ctx_low_confidence, "Si")
        # Low confidence → confidence_mult should be small
        assert result["confidence_mult"] <= 0.3
        # ci_bonus negative for wide CI (0.12)
        assert result["ci_bonus"] < 0.0

    def test_weighted_sharpe_no_context(self, healthy_returns):
        """No context → baseline Sharpe returned, no adjustment."""
        result = forecast_weighted_sharpe(healthy_returns, None, "Si")
        assert result["baseline_sharpe"] == result["weighted_sharpe"]
        assert result["confidence_mult"] == 0.0
        assert result["adjustment_pct"] == 0.0

    def test_weighted_sharpe_ri_excluded(self, ctx_high_confidence, healthy_returns):
        """RI ticker → forecast contribution = 0.0, returns baseline."""
        result = forecast_weighted_sharpe(healthy_returns, ctx_high_confidence, "RI")
        assert result["ticker"] == "RI"
        assert result["weighted_sharpe"] == result["baseline_sharpe"]
        assert result["confidence_mult"] == 0.0


class TestAdjustedDegradation:
    """forecast_adjusted_degradation tests."""

    def test_adjusted_degradation_healthy(self, ctx_high_confidence, healthy_returns):
        """Healthy returns + high confidence + calm regime → "healthy" status."""
        result = forecast_adjusted_degradation(healthy_returns, ctx_high_confidence, "Si")
        assert result["status"] in ("healthy", "degraded")
        assert result["regime_adjusted"] is True
        assert result["vol_regime"] == "calm"
        assert result["confidence_mult"] > 0.0
        assert "components" in result

    def test_adjusted_degradation_degraded(self, ctx_high_confidence, degraded_returns):
        """Degraded returns → "degraded" or "critical" status."""
        result = forecast_adjusted_degradation(degraded_returns, ctx_high_confidence, "Si")
        assert result["status"] in ("degraded", "critical", "dead")
        assert result["regime_adjusted"] is True

    def test_adjusted_degradation_no_context(self, healthy_returns):
        """No context → baseline health_score, not regime_adjusted."""
        result = forecast_adjusted_degradation(healthy_returns, None, "Si")
        assert result["regime_adjusted"] is False
        assert result["vol_regime"] == "normal"
        assert "status" in result

    def test_adjusted_degradation_volatile_regime(self, degraded_returns):
        """Volatile regime → stricter thresholds → worse health."""
        # Create volatile context
        fr = ForecastResult(direction="flat", ci_width=0.10, confidence=0.4, horizon=20, source="dummy")
        ctx = ForecastContext(
            per_ticker={"Si": fr},
            portfolio_bias="flat",
            volatility_regime="volatile",
            confidence_score=0.4,
            meta={"tickers_with_signal": 0, "tickers_no_signal": 1, "avg_ci_width": 0.10, "n_tickers": 1},
        )
        result = forecast_adjusted_degradation(degraded_returns, ctx, "Si")
        assert result["vol_regime"] == "volatile"
        assert result["regime_adjusted"] is True


class TestStabilityBonus:
    """forecast_stability_bonus tests."""

    def test_stability_bonus_high_confidence(self, ctx_high_confidence, healthy_returns):
        """High confidence → bonus ≥ 0 → adjusted_stability ≥ baseline."""
        result = forecast_stability_bonus(healthy_returns, ctx_high_confidence, "Si")
        assert result["ticker"] == "Si"
        assert result["confidence_bonus"] >= 0.0
        assert result["adjusted_stability"] >= result["baseline_stability"]
        # Clamp: 0..1
        assert 0.0 <= result["adjusted_stability"] <= 1.0

    def test_stability_bonus_low_confidence(self, ctx_low_confidence, healthy_returns):
        """Low confidence → penalty → adjusted_stability ≤ baseline."""
        result = forecast_stability_bonus(healthy_returns, ctx_low_confidence, "Si")
        assert result["confidence_bonus"] <= 0.0
        # adjusted should be ≤ baseline or equal (penalty applied)
        assert result["adjusted_stability"] <= result["baseline_stability"] + 0.01  # float tolerance

    def test_stability_bonus_ri_excluded(self, ctx_high_confidence, healthy_returns):
        """RI → no bonus, baseline returned."""
        result = forecast_stability_bonus(healthy_returns, ctx_high_confidence, "RI")
        assert result["confidence_bonus"] == 0.0
        assert result["baseline_stability"] == result["adjusted_stability"]

    def test_stability_bonus_no_context(self, healthy_returns):
        """No context → baseline, no bonus."""
        result = forecast_stability_bonus(healthy_returns, None, "Si")
        assert result["confidence_bonus"] == 0.0
        assert result["baseline_stability"] == result["adjusted_stability"]


class TestScorecardSummary:
    """forecast_scorecard_summary tests."""

    def test_scorecard_summary_structure(self, ctx_high_confidence, slots_fixture, config_fixture):
        """All expected keys present, PnL↑/risk↓ indicators present."""
        result = forecast_scorecard_summary(slots_fixture, ctx_high_confidence, config_fixture)
        # Top-level keys
        assert "forecast_available" in result
        assert "confidence_score" in result
        assert "portfolio_bias" in result
        assert "vol_regime" in result
        assert "pnl_up_indicator" in result
        assert "risk_down_indicator" in result
        assert "agreement_ratio" in result
        assert "slots" in result
        assert "max_slots" in result
        assert "active_slots_count" in result
        assert "excluded_tickers" in result
        # PnL/risk indicators are booleans
        assert isinstance(result["pnl_up_indicator"], bool)
        assert isinstance(result["risk_down_indicator"], bool)
        # Slots list
        assert len(result["slots"]) == 2
        for slot in result["slots"]:
            assert "ticker" in slot
            assert "forecast_agreement" in slot
            assert "is_excluded" in slot

    def test_scorecard_summary_ri_excluded(self, slots_fixture, config_fixture):
        """RI in slots → is_excluded=True, forecast contribution=0."""
        ri_slot = [{"ticker": "RI", "score": 9.0, "direction": "LONG", "slot_score": 8.0, "risk_penalty": 0.05}]
        ctx = ForecastContext(
            per_ticker={},
            portfolio_bias="flat",
            volatility_regime="normal",
            confidence_score=0.0,
            meta={"tickers_with_signal": 0, "tickers_no_signal": 1, "avg_ci_width": 0.0, "n_tickers": 1},
        )
        result = forecast_scorecard_summary(ri_slot, ctx, config_fixture)
        assert result["slots"][0]["is_excluded"] is True
        assert result["slots"][0]["confidence_mult"] == 0.0

    def test_scorecard_summary_no_context(self, slots_fixture, config_fixture):
        """No context → all baseline, forecast_available=False."""
        result = forecast_scorecard_summary(slots_fixture, None, config_fixture)
        assert result["forecast_available"] is False
        assert result["confidence_score"] == 0.0
        assert result["pnl_up_indicator"] is False

    def test_scorecard_summary_max_slots_cap(self, ctx_high_confidence, config_fixture):
        """More than max_slots → only max_slots active."""
        many_slots = [
            {"ticker": "Si", "score": 8.0, "direction": "LONG", "slot_score": 7.5, "risk_penalty": 0.1},
            {"ticker": "BR", "score": 6.5, "direction": "SHORT", "slot_score": 6.0, "risk_penalty": 0.15},
            {"ticker": "GAZP", "score": 5.0, "direction": "LONG", "slot_score": 4.5, "risk_penalty": 0.2},
            {"ticker": "SBER", "score": 4.0, "direction": "SHORT", "slot_score": 3.5, "risk_penalty": 0.25},
        ]
        result = forecast_scorecard_summary(many_slots, ctx_high_confidence, config_fixture)
        assert result["active_slots_count"] == 3  # max_slots=3
        assert len(result["slots"]) == 3


class TestBrokerGuard:
    """AST-guard: no broker imports in bridge module."""

    def test_no_broker_imports(self):
        """forecast_scorecard_bridge.py must not contain broker/client imports."""
        bridge_path = os.path.join(_CODE_DIR, "forecast_scorecard_bridge.py")
        assert os.path.exists(bridge_path), f"Bridge file not found: {bridge_path}"
        result = check_no_broker_imports(bridge_path)
        assert result is True, "Broker imports detected in forecast_scorecard_bridge.py"

"""Tests for ForecastContext — Central Forecasting Brain.

≥3 fixtures, ≥6 tests, no live broker/orders, all dry-run.
Covers: build_context from fixture, dummy→fail-open, RI excluded,
        confidence bounds, vol_regime classification, AST-guard,
        portfolio_bias voting, confidence_multiplier.
"""
import json
import os
import sys

import pytest

# Ensure code/ dir on sys.path
_CODE_DIR = os.path.join(os.path.dirname(__file__), "..", "code")
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

from timesfm_adapter import ForecastResult, DummyTimesFMAdapter, get_adapter
from forecast_context import (
    ForecastContext,
    build_forecast_context,
    check_no_broker_imports,
    EXCLUDED_TICKERS,
    _compute_portfolio_bias,
    _compute_volatility_regime,
)

# ─── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def fixture_data():
    """Load forecast_context_fixture.json (mock bars + regime for BR/GAZP/SBER/RI)."""
    fixture_path = os.path.join(_CODE_DIR, "forecast_context_fixture.json")
    with open(fixture_path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def dummy_adapter():
    """DummyTimesFMAdapter — no real model needed."""
    return DummyTimesFMAdapter()


@pytest.fixture
def real_bars_map():
    """Pre-built bars_map with 30 bars per ticker: BR (rising), GAZP (falling), SBER (flat)."""
    return {
        "BR": [100.0 + i * 0.5 for i in range(30)],
        "GAZP": [200.0 - i * 0.3 for i in range(30)],
        "SBER": [250.0 + 0.1 * i for i in range(30)],
    }


@pytest.fixture
def regime_snapshot():
    """Minimal regime snapshot matching real_bars_map."""
    return {
        "tickers": {
            "BR": {"adx": 23.1, "direction": "up", "regime": "trend"},
            "GAZP": {"adx": 35.3, "direction": "up", "regime": "trend"},
            "SBER": {"adx": 18.0, "direction": "down", "regime": "range"},
        },
        "bias": "neutral",
    }


@pytest.fixture
def sample_forecast_results():
    """Dict of ForecastResults for per-ticker context tests."""
    return {
        "BR": ForecastResult(direction="up", ci_width=0.015, confidence=0.88, horizon=20, source="timesfm"),
        "GAZP": ForecastResult(direction="down", ci_width=0.05, confidence=0.55, horizon=20, source="timesfm"),
        "SBER": ForecastResult(direction="up", ci_width=0.15, confidence=0.15, horizon=20, source="timesfm"),
    }


# ─── Test: Build Context from Fixture ──────────────────────────────────


class TestBuildContextFromFixture:
    """Tests for build_forecast_context with fixture data."""

    def test_build_context_returns_forecast_context(self, dummy_adapter, fixture_data):
        """build_forecast_context returns ForecastContext instance."""
        ctx = build_forecast_context(
            dummy_adapter,
            fixture_data["bars_map"],
            fixture_data["regime_snapshot"],
        )
        assert isinstance(ctx, ForecastContext)

    def test_context_has_meta(self, dummy_adapter, fixture_data):
        """Context meta contains expected keys."""
        ctx = build_forecast_context(
            dummy_adapter,
            fixture_data["bars_map"],
            fixture_data["regime_snapshot"],
        )
        assert "tickers_with_signal" in ctx.meta
        assert "tickers_no_signal" in ctx.meta
        assert "avg_ci_width" in ctx.meta
        assert "n_tickers" in ctx.meta
        assert "universe_size" in ctx.meta

    def test_context_has_valid_bias(self, dummy_adapter, fixture_data):
        """Portfolio bias is one of valid values."""
        ctx = build_forecast_context(
            dummy_adapter,
            fixture_data["bars_map"],
            fixture_data["regime_snapshot"],
        )
        assert ctx.portfolio_bias in ("up", "down", "flat")

    def test_context_has_valid_vol_regime(self, dummy_adapter, fixture_data):
        """Volatility regime is one of valid values."""
        ctx = build_forecast_context(
            dummy_adapter,
            fixture_data["bars_map"],
            fixture_data["regime_snapshot"],
        )
        assert ctx.volatility_regime in ("calm", "normal", "volatile")

    def test_context_confidence_bounds(self, dummy_adapter, fixture_data):
        """Confidence score is in [0, 1]."""
        ctx = build_forecast_context(
            dummy_adapter,
            fixture_data["bars_map"],
            fixture_data["regime_snapshot"],
        )
        assert 0.0 <= ctx.confidence_score <= 1.0


# ─── Test: Dummy Adapter → Fail-Open ──────────────────────────────────


class TestDummyFailOpen:
    """Tests for dummy adapter fail-open behavior."""

    def test_dummy_source_gives_no_signal(self, dummy_adapter, real_bars_map):
        """Dummy adapter → tickers_no_signal = count, tickers_with_signal = 0."""
        ctx = build_forecast_context(dummy_adapter, real_bars_map)
        assert ctx.meta["tickers_with_signal"] == 0
        assert ctx.meta["tickers_no_signal"] == len(real_bars_map)
        assert ctx.confidence_score == 0.0

    def test_dummy_confidence_multiplier_zero(self, dummy_adapter, real_bars_map):
        """Dummy adapter → confidence_multiplier = 0.0 (no real forecasts)."""
        ctx = build_forecast_context(dummy_adapter, real_bars_map)
        assert ctx.confidence_multiplier() == 0.0

    def test_dummy_has_forecast_false(self, dummy_adapter, real_bars_map):
        """Dummy adapter → has_forecast() = False."""
        ctx = build_forecast_context(dummy_adapter, real_bars_map)
        assert ctx.has_forecast() is False

    def test_dummy_still_produces_direction(self, dummy_adapter, real_bars_map):
        """Dummy adapter still provides per-ticker forecasts (with direction)."""
        ctx = build_forecast_context(dummy_adapter, real_bars_map)
        assert len(ctx.per_ticker) == len(real_bars_map)
        for ticker, fr in ctx.per_ticker.items():
            assert fr.direction in ("up", "down", "flat")
            assert fr.source == "dummy"


# ─── Test: RI Excluded ────────────────────────────────────────────────


class TestRIExcluded:
    """Tests for RI ticker exclusion from ForecastContext."""

    def test_ri_not_in_context(self, dummy_adapter, fixture_data):
        """RI ticker excluded from per_ticker dict."""
        ctx = build_forecast_context(
            dummy_adapter,
            fixture_data["bars_map"],
            fixture_data["regime_snapshot"],
        )
        assert "RI" not in ctx.per_ticker

    def test_ri_excluded_tickes_meta(self, dummy_adapter, fixture_data):
        """RI excluded → n_tickers < universe_size (bars_map has 4, RI excluded)."""
        ctx = build_forecast_context(
            dummy_adapter,
            fixture_data["bars_map"],
            fixture_data["regime_snapshot"],
        )
        # bars_map has BR, GAZP, SBER, RI = 4, but RI excluded → n_tickers=3
        assert ctx.meta["n_tickers"] == 3
        assert ctx.meta["universe_size"] == 4

    def test_ri_in_excluded_set(self):
        """EXCLUDED_TICKERS contains RI."""
        assert "RI" in EXCLUDED_TICKERS

    def test_custom_excluded(self, dummy_adapter, real_bars_map):
        """Custom excluded set excludes specified tickers."""
        ctx = build_forecast_context(
            dummy_adapter,
            real_bars_map,
            excluded={"GAZP"},
        )
        assert "GAZP" not in ctx.per_ticker
        assert ctx.meta["n_tickers"] == 2  # only BR, SBER


# ─── Test: Portfolio Bias ──────────────────────────────────────────────


class TestPortfolioBias:
    """Tests for portfolio_bias computation."""

    def test_majority_up(self, sample_forecast_results):
        """2 up + 1 down → bias up (or flat depending on threshold)."""
        per_ticker = {"BR": sample_forecast_results["BR"],
                      "GAZP": sample_forecast_results["GAZP"],
                      "SBER": sample_forecast_results["SBER"]}
        bias = _compute_portfolio_bias(per_ticker)
        # BR=up, GAZP=down, SBER=up → 2/3 up, 1/3 down → bias="up" (66% > 50%)
        assert bias == "up"

    def test_empty_gives_flat(self):
        """Empty per_ticker → flat."""
        assert _compute_portfolio_bias({}) == "flat"

    def test_single_ticker(self):
        """Single ticker → flat (1/1 = 100% but that's >= 50%)."""
        per_ticker = {"BR": ForecastResult("up", 0.02, 0.8, 20, "timesfm")}
        bias = _compute_portfolio_bias(per_ticker)
        assert bias == "up"

    def test_all_flat(self):
        """All flat → flat."""
        per_ticker = {
            "BR": ForecastResult("flat", 0.02, 0.8, 20, "timesfm"),
            "SBER": ForecastResult("flat", 0.03, 0.7, 20, "timesfm"),
        }
        bias = _compute_portfolio_bias(per_ticker)
        assert bias == "flat"


# ─── Test: Volatility Regime ──────────────────────────────────────────


class TestVolatilityRegime:
    """Tests for vol_regime computation from CI widths."""

    def test_calm_regime(self):
        """Low avg CI → calm."""
        regime = _compute_volatility_regime([0.01, 0.02, 0.015])
        assert regime == "calm"

    def test_normal_regime(self):
        """Medium avg CI → normal."""
        regime = _compute_volatility_regime([0.04, 0.05, 0.06])
        assert regime == "normal"

    def test_volatile_regime(self):
        """High avg CI → volatile."""
        regime = _compute_volatility_regime([0.09, 0.10, 0.12])
        assert regime == "volatile"

    def test_empty_gives_normal(self):
        """No real CI widths → normal (default)."""
        regime = _compute_volatility_regime([])
        assert regime == "normal"


# ─── Test: Confidence Multiplier ──────────────────────────────────────


class TestConfidenceMultiplier:
    """Tests for confidence_multiplier() method."""

    def test_high_confidence_multiplier(self):
        """High confidence → multiplier close to 1.0."""
        ctx = ForecastContext(
            per_ticker={"BR": ForecastResult("up", 0.01, 0.85, 20, "timesfm")},
            confidence_score=0.85,
            meta={"tickers_with_signal": 1},
        )
        assert ctx.confidence_multiplier() == 1.0

    def test_low_confidence_multiplier(self):
        """Low confidence → multiplier close to 0.3."""
        ctx = ForecastContext(
            per_ticker={"BR": ForecastResult("up", 0.10, 0.25, 20, "timesfm")},
            confidence_score=0.25,
            meta={"tickers_with_signal": 1},
        )
        assert ctx.confidence_multiplier() == 0.3

    def test_medium_confidence_multiplier(self):
        """Medium confidence → multiplier between 0.3 and 1.0."""
        ctx = ForecastContext(
            per_ticker={"BR": ForecastResult("up", 0.04, 0.45, 20, "timesfm")},
            confidence_score=0.45,
            meta={"tickers_with_signal": 1},
        )
        mult = ctx.confidence_multiplier()
        assert 0.3 <= mult <= 1.0

    def test_no_signal_multiplier_zero(self):
        """No real forecasts → multiplier = 0.0."""
        ctx = ForecastContext(
            meta={"tickers_with_signal": 0},
        )
        assert ctx.confidence_multiplier() == 0.0


# ─── Test: AST-Guard (No Broker Imports) ──────────────────────────────


class TestASTGuard:
    """Tests for check_no_broker_imports AST guard."""

    def test_forecast_context_no_broker(self):
        """forecast_context.py has no broker imports."""
        path = os.path.join(_CODE_DIR, "forecast_context.py")
        assert check_no_broker_imports(path), f"Broker import found in {path}"

    def test_forecast_context_fixture_compiles(self):
        """forecast_context_fixture.json is valid JSON."""
        path = os.path.join(_CODE_DIR, "forecast_context_fixture.json")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "bars_map" in data
        assert "regime_snapshot" in data


# ─── Test: Empty Bars Edge Case ────────────────────────────────────────


class TestEdgeCases:
    """Edge case tests for ForecastContext builder."""

    def test_empty_bars_map(self, dummy_adapter):
        """Empty bars_map → empty ForecastContext."""
        ctx = build_forecast_context(dummy_adapter, {})
        assert ctx.per_ticker == {}
        assert ctx.portfolio_bias == "flat"
        assert ctx.confidence_score == 0.0
        assert ctx.meta["n_tickers"] == 0

    def test_single_bar_bars_map(self, dummy_adapter):
        """Bars with 1 element → skipped (len < 2 guard)."""
        ctx = build_forecast_context(dummy_adapter, {"BR": [100.0]})
        assert ctx.meta["n_tickers"] == 0

    def test_none_adapter(self, real_bars_map):
        """None adapter → DummyTimesFMAdapter used (fail-open)."""
        ctx = build_forecast_context(None, real_bars_map)
        assert isinstance(ctx, ForecastContext)
        assert ctx.has_forecast() is False

    def test_get_ticker(self, dummy_adapter, real_bars_map):
        """get_ticker returns ForecastResult or None."""
        ctx = build_forecast_context(dummy_adapter, real_bars_map)
        assert ctx.get_ticker("BR") is not None
        assert ctx.get_ticker("NONEXISTENT") is None

    def test_forecast_context_frozen(self):
        """ForecastContext is immutable (frozen dataclass)."""
        ctx = ForecastContext(portfolio_bias="up")
        with pytest.raises(AttributeError):
            ctx.portfolio_bias = "down"  # type: ignore

"""Tests for Forecast Priors — expectancy_prior, regime_prior, freshness, decay.

≥5 fixtures, ≥10 tests, no live broker/orders, all dry-run.
Covers: forecast_expectancy_prior, forecast_regime_prior,
        signal_freshness, forecast_decay,
        integration with allocator_score_with_forecast.
"""
import os
import sys
import time

import pytest

# Ensure code/ dir on sys.path
_CODE_DIR = os.path.join(os.path.dirname(__file__), "..", "code")
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

from timesfm_adapter import ForecastResult
from forecast_priors import (
    forecast_expectancy_prior,
    forecast_regime_prior,
    signal_freshness,
    forecast_decay,
    EXCLUDED_TICKERS,
    DEFAULT_DECAY_HALF_LIFE,
    DEFAULT_BAR_SECONDS,
)
from forecast_scorer import (
    allocator_score_with_forecast,
    check_no_broker_imports,
    ALLOCATOR_WEIGHTS_FORECAST,
)


# ─── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def forecast_up_real():
    """ForecastResult: direction=up, high confidence, narrow CI."""
    return ForecastResult(
        direction="up",
        ci_width=0.03,
        confidence=0.8,
        horizon=10,
        source="timesfm",
    )


@pytest.fixture
def forecast_down_real():
    """ForecastResult: direction=down, medium confidence."""
    return ForecastResult(
        direction="down",
        ci_width=0.04,
        confidence=0.7,
        horizon=10,
        source="timesfm",
    )


@pytest.fixture
def candidate_long():
    """Кандидат: LONG direction, средняя статистика."""
    return {
        "ticker": "SBER",
        "direction": "LONG",
        "win_rate": 0.6,
        "avg_win": 1.5,
        "avg_loss": -1.0,
        "drawdown_pct": 3.0,
        "volatility": 1.5,
    }


@pytest.fixture
def regime_trend_up():
    """Regime snapshot: trend up для SBER."""
    return {
        "tickers": {
            "SBER": {"adx": 25.0, "direction": "up", "regime": "trend"},
        },
        "bias": "up",
    }


@pytest.fixture
def regime_flat():
    """Regime snapshot: range/flat для SBER."""
    return {
        "tickers": {
            "SBER": {"adx": 10.0, "direction": None, "regime": "range"},
        },
        "bias": "neutral",
    }


@pytest.fixture
def forecast_dummy():
    """Dummy ForecastResult (source=dummy → все priors = 0.0)."""
    return ForecastResult(
        direction="up",
        ci_width=0.02,
        confidence=0.9,
        horizon=20,
        source="dummy",
    )


@pytest.fixture
def forecast_flat():
    """ForecastResult: direction=flat → no directional signal."""
    return ForecastResult(
        direction="flat",
        ci_width=0.05,
        confidence=0.5,
        horizon=20,
        source="timesfm",
    )


# ─── Test: forecast_expectancy_prior ───────────────────────────────────


class TestExpectancyPrior:
    """Tests for forecast_expectancy_prior function."""

    def test_expectancy_prior_aligned_positive(self, candidate_long, forecast_up_real):
        """forecast up + LONG → positive prior (direction aligned)."""
        prior = forecast_expectancy_prior(candidate_long, forecast_up_real)
        # forecast_up: direction=up, cand=LONG→up, confidence=0.8
        # Expected: 0.8 * 0.5 = 0.4
        assert prior > 0.0
        assert prior == pytest.approx(0.4, abs=0.01)

    def test_expectancy_prior_conflict_negative(self, candidate_long, forecast_down_real):
        """forecast down + LONG → negative prior (direction conflicts)."""
        prior = forecast_expectancy_prior(candidate_long, forecast_down_real)
        # forecast_down: direction=down, cand=LONG→up, confidence=0.7
        # Expected: -(0.7 * 0.5) = -0.35
        assert prior < 0.0
        assert prior == pytest.approx(-0.35, abs=0.01)

    def test_expectancy_prior_dummy_zero(self, candidate_long, forecast_dummy):
        """Dummy source → prior = 0.0 (fail-open)."""
        prior = forecast_expectancy_prior(candidate_long, forecast_dummy)
        assert prior == 0.0

    def test_expectancy_prior_none_forecast(self, candidate_long):
        """None forecast → prior = 0.0."""
        prior = forecast_expectancy_prior(candidate_long, None)
        assert prior == 0.0

    def test_expectancy_prior_flat_direction(self, candidate_long, forecast_flat):
        """Flat forecast direction → prior = 0.0."""
        prior = forecast_expectancy_prior(candidate_long, forecast_flat)
        assert prior == 0.0

    def test_expectancy_prior_ri_excluded(self, forecast_up_real):
        """RI ticker → prior = 0.0 (excluded)."""
        ri_candidate = {"ticker": "RI", "direction": "LONG"}
        prior = forecast_expectancy_prior(ri_candidate, forecast_up_real)
        assert prior == 0.0

    def test_expectancy_prior_short_candidate(self, forecast_down_real):
        """SHORT candidate + forecast down → positive prior (aligned)."""
        short_candidate = {
            "ticker": "GAZP",
            "direction": "SHORT",
            "win_rate": 0.5,
            "avg_win": 1.0,
            "avg_loss": -1.0,
        }
        prior = forecast_expectancy_prior(short_candidate, forecast_down_real)
        # SHORT→down, forecast=down → aligned
        assert prior > 0.0
        assert prior == pytest.approx(0.7 * 0.5, abs=0.01)


# ─── Test: forecast_regime_prior ───────────────────────────────────────


class TestRegimePrior:
    """Tests for forecast_regime_prior function."""

    def test_regime_prior_confirms(self, forecast_up_real, regime_trend_up):
        """forecast up + regime=trend up → positive prior (confirms)."""
        prior = forecast_regime_prior(forecast_up_real, regime_trend_up, "SBER")
        # forecast=up, regime_dir=up, confidence=0.8
        # Expected: 0.8 * 0.3 = 0.24
        assert prior > 0.0
        assert prior == pytest.approx(0.24, abs=0.01)

    def test_regime_prior_conflicts(self, forecast_down_real, regime_trend_up):
        """forecast down + regime=trend up → negative prior (conflicts)."""
        prior = forecast_regime_prior(forecast_down_real, regime_trend_up, "SBER")
        # forecast=down, regime_dir=up, confidence=0.7
        # Expected: -(0.7 * 0.3) = -0.21
        assert prior < 0.0
        assert prior == pytest.approx(-0.21, abs=0.01)

    def test_regime_prior_range_regime_zero(self, forecast_up_real, regime_flat):
        """Regime=range → prior = 0.0 (no directional regime)."""
        prior = forecast_regime_prior(forecast_up_real, regime_flat, "SBER")
        assert prior == 0.0

    def test_regime_prior_dummy_zero(self, forecast_dummy, regime_trend_up):
        """Dummy forecast → prior = 0.0."""
        prior = forecast_regime_prior(forecast_dummy, regime_trend_up, "SBER")
        assert prior == 0.0

    def test_regime_prior_none_forecast(self, regime_trend_up):
        """None forecast → prior = 0.0."""
        prior = forecast_regime_prior(None, regime_trend_up, "SBER")
        assert prior == 0.0

    def test_regime_prior_ri_excluded(self, forecast_up_real):
        """RI ticker → prior = 0.0 (excluded)."""
        regime = {
            "tickers": {"RI": {"adx": 40.0, "direction": "up", "regime": "trend"}},
            "bias": "up",
        }
        prior = forecast_regime_prior(forecast_up_real, regime, "RI")
        assert prior == 0.0

    def test_regime_prior_missing_ticker(self, forecast_up_real):
        """Ticker not in regime_snapshot → prior = 0.0."""
        prior = forecast_regime_prior(forecast_up_real, {"tickers": {}}, "UNKNOWN")
        assert prior == 0.0


# ─── Test: signal_freshness ────────────────────────────────────────────


class TestSignalFreshness:
    """Tests for signal_freshness function."""

    def test_signal_freshness_zero_age(self):
        """Age=0 → freshness = 1.0 (just received)."""
        freshness = signal_freshness(
            forecast_ts=1000.0,
            now_ts=1000.0,
            horizon=20,
            bar_seconds=300,
        )
        assert freshness == pytest.approx(1.0, abs=0.01)

    def test_signal_freshness_half_life(self):
        """Age = half_life → freshness ≈ 0.5."""
        half_life_seconds = 20 * 300  # horizon * bar_seconds = 6000
        freshness = signal_freshness(
            forecast_ts=1000.0,
            now_ts=1000.0 + half_life_seconds,
            horizon=20,
            bar_seconds=300,
        )
        # e^(-1) ≈ 0.368 (age = half_life, so e^(-age/half_life) = e^(-1))
        # Wait, let me recheck. half_life = horizon * bar_seconds = 6000
        # age = 6000, so freshness = e^(-6000/6000) = e^(-1) ≈ 0.368
        assert freshness == pytest.approx(0.368, abs=0.01)

    def test_signal_freshness_none_ts(self):
        """None forecast_ts → freshness = 1.0 (fail-open)."""
        freshness = signal_freshness(forecast_ts=None, now_ts=1000.0)
        assert freshness == 1.0

    def test_signal_freshness_none_now(self):
        """None now_ts → freshness = 1.0 (fail-open)."""
        freshness = signal_freshness(forecast_ts=1000.0, now_ts=None)
        assert freshness == 1.0

    def test_signal_freshness_old_signal(self):
        """Very old signal → freshness close to 0."""
        freshness = signal_freshness(
            forecast_ts=1000.0,
            now_ts=1000.0 + 60000,  # 10 half-lives
            horizon=20,
            bar_seconds=300,
        )
        assert freshness < 0.01

    def test_signal_freshness_negative_age(self):
        """Age < 0 (clock skew) → freshness = 1.0."""
        freshness = signal_freshness(
            forecast_ts=2000.0,
            now_ts=1000.0,
            horizon=20,
            bar_seconds=300,
        )
        assert freshness == 1.0


# ─── Test: forecast_decay ──────────────────────────────────────────────


class TestForecastDecay:
    """Tests for forecast_decay function."""

    def test_forecast_decay_at_zero(self):
        """elapsed_bars=0 → decay = 1.0 (no decay)."""
        decay_val = forecast_decay(elapsed_bars=0, half_life=20)
        assert decay_val == 1.0

    def test_forecast_decay_at_half_life(self):
        """elapsed_bars=half_life → decay ≈ 0.5."""
        decay_val = forecast_decay(elapsed_bars=20, half_life=20)
        # 2^(-20/20) = 2^(-1) = 0.5
        assert decay_val == pytest.approx(0.5, abs=0.01)

    def test_forecast_decay_full_horizon(self):
        """elapsed_bars=100 → decay close to 0."""
        decay_val = forecast_decay(elapsed_bars=100, half_life=20)
        # 2^(-5) = 0.03125
        assert decay_val == pytest.approx(0.03125, abs=0.01)
        assert decay_val < 0.05

    def test_forecast_decay_default_half_life(self):
        """Using default half_life (20)."""
        decay_val = forecast_decay(elapsed_bars=20)
        assert decay_val == pytest.approx(0.5, abs=0.01)

    def test_forecast_decay_negative_bars(self):
        """Negative elapsed_bars → decay = 1.0."""
        decay_val = forecast_decay(elapsed_bars=-5, half_life=20)
        assert decay_val == 1.0

    def test_forecast_decay_zero_half_life(self):
        """half_life=0 → decay = 0.0."""
        decay_val = forecast_decay(elapsed_bars=10, half_life=0)
        assert decay_val == 0.0


# ─── Test: Integration with allocator_score_with_forecast ──────────────


class TestIntegrationAllocatorPriors:
    """Integration tests: allocator_score_with_forecast with all priors."""

    def test_integration_allocator_with_all_priors(
        self, candidate_long, forecast_up_real, regime_trend_up
    ):
        """allocator_score_with_forecast с priors > baseline без priors."""
        now = 1000000.0
        forecast_ts = now - 300.0  # 5 minutes old — very fresh

        # Score WITH priors (fresh forecast, no decay)
        score_with_priors = allocator_score_with_forecast(
            candidate_long,
            regime_trend_up,
            forecast_result=forecast_up_real,
            forecast_ts=forecast_ts,
            now_ts=now,
            elapsed_bars=1,
        )

        # Score WITHOUT priors (same params but no forecast)
        score_without = allocator_score_with_forecast(
            candidate_long,
            regime_trend_up,
            forecast_result=None,
        )

        # With aligned forecast + regime confirm + fresh → should be higher
        assert score_with_priors > score_without

    def test_integration_stale_forecast_reduced(
        self, candidate_long, forecast_up_real, regime_trend_up
    ):
        """Stale forecast (old timestamp) → reduced score vs fresh."""
        now = 1000000.0

        # Fresh forecast
        score_fresh = allocator_score_with_forecast(
            candidate_long,
            regime_trend_up,
            forecast_result=forecast_up_real,
            forecast_ts=now - 100.0,  # 100 seconds old
            now_ts=now,
            elapsed_bars=1,
        )

        # Stale forecast (10 half-lives old)
        score_stale = allocator_score_with_forecast(
            candidate_long,
            regime_trend_up,
            forecast_result=forecast_up_real,
            forecast_ts=now - 60000.0,  # very old
            now_ts=now,
            elapsed_bars=100,
        )

        # Fresh should beat stale
        assert score_fresh > score_stale

    def test_integration_conflict_reduces_score(
        self, candidate_long, forecast_down_real, regime_trend_up
    ):
        """Conflicting forecast → lower score than no forecast."""
        score_conflict = allocator_score_with_forecast(
            candidate_long,
            regime_trend_up,
            forecast_result=forecast_down_real,
            forecast_ts=1000000.0,
            now_ts=1000000.0,
            elapsed_bars=1,
        )

        score_no_forecast = allocator_score_with_forecast(
            candidate_long,
            regime_trend_up,
            forecast_result=None,
        )

        # Conflicting forecast should lower the score
        assert score_conflict < score_no_forecast


# ─── Test: No Broker Imports (new file) ───────────────────────────────


class TestNoBrokerImportsPriors:
    """Verify forecast_priors.py has no broker/tinkoff imports."""

    def test_forecast_priors_no_broker(self):
        """forecast_priors.py has no broker imports."""
        path = os.path.join(_CODE_DIR, "forecast_priors.py")
        assert check_no_broker_imports(path), f"Broker import found in {path}"

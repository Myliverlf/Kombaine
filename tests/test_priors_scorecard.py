"""Tests for Scorecard — PnL/risk metrics with forecast priors.

≥3 fixtures, ≥4 tests, no live broker/orders, all dry-run.
Covers: priors improve ranking, priors reduce risk for conflict,
        RI excluded unchanged, no broker imports.
"""
import os
import sys

import pytest

# Ensure code/ dir on sys.path
_CODE_DIR = os.path.join(os.path.dirname(__file__), "..", "code")
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

from timesfm_adapter import ForecastResult
from forecast_scorer import (
    allocator_score_with_forecast,
    forecast_bonus,
    check_no_broker_imports,
    ALLOCATOR_WEIGHTS_FORECAST,
    EXCLUDED_TICKERS,
)
from forecast_priors import (
    forecast_expectancy_prior,
    forecast_regime_prior,
)


# ─── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def three_candidates():
    """3 кандидата: strong_long, weak_long, conflict_short."""
    return [
        {
            "ticker": "SBER",
            "direction": "LONG",
            "win_rate": 0.75,
            "avg_win": 2.0,
            "avg_loss": -0.8,
            "drawdown_pct": 2.0,
            "volatility": 1.0,
        },
        {
            "ticker": "GAZP",
            "direction": "LONG",
            "win_rate": 0.50,
            "avg_win": 1.0,
            "avg_loss": -1.0,
            "drawdown_pct": 5.0,
            "volatility": 2.0,
        },
        {
            "ticker": "LKOH",
            "direction": "SHORT",
            "win_rate": 0.65,
            "avg_win": 1.5,
            "avg_loss": -0.9,
            "drawdown_pct": 4.0,
            "volatility": 1.8,
        },
    ]


@pytest.fixture
def forecast_set():
    """Dict из 3 ForecastResult для каждого кандидата."""
    return {
        "SBER": ForecastResult(
            direction="up", ci_width=0.02, confidence=0.9, horizon=10, source="timesfm"
        ),
        "GAZP": ForecastResult(
            direction="up", ci_width=0.05, confidence=0.6, horizon=10, source="timesfm"
        ),
        "LKOH": ForecastResult(
            direction="up", ci_width=0.03, confidence=0.8, horizon=10, source="timesfm"
        ),
    }


@pytest.fixture
def regime_context():
    """Regime snapshot для 3 тикеров (все trend up)."""
    return {
        "tickers": {
            "SBER": {"adx": 35.0, "direction": "up", "regime": "trend"},
            "GAZP": {"adx": 20.0, "direction": "up", "regime": "trend"},
            "LKOH": {"adx": 28.0, "direction": "up", "regime": "trend"},
        },
        "bias": "up",
    }


@pytest.fixture
def now():
    """Current timestamp for freshness calculations."""
    return 1000000.0


# ─── Test: Priors improve ranking ──────────────────────────────────────


class TestPriorsImproveRanking:
    """Priors should ensure strong candidates rank higher."""

    def test_priors_improve_ranking(
        self, three_candidates, forecast_set, regime_context, now
    ):
        """With priors, strong_long (SBER) should score highest."""
        scores_with_priors = []
        for c in three_candidates:
            ticker = c["ticker"]
            fr = forecast_set.get(ticker)
            score = allocator_score_with_forecast(
                c,
                regime_context,
                forecast_result=fr,
                forecast_ts=now - 100.0,
                now_ts=now,
                elapsed_bars=1,
            )
            scores_with_priors.append((ticker, score))

        # Sort by score descending
        scores_with_priors.sort(key=lambda x: x[1], reverse=True)

        # SBER should be top — it has best stats + aligned forecast + confirmed regime
        assert scores_with_priors[0][0] == "SBER", (
            f"SBER should be first, got: {scores_with_priors}"
        )


# ─── Test: Priors reduce risk for conflict ─────────────────────────────


class TestPriorsReduceConflict:
    """Conflicting forecast should penalize candidates."""

    def test_priors_reduce_risk_for_conflict(
        self, three_candidates, forecast_set, regime_context, now
    ):
        """Conflicting forecast → lower score than aligned forecast (same candidate)."""
        c_lkoh = three_candidates[2]  # LKOH SHORT

        # Conflicting: forecast UP vs candidate SHORT
        fr_conflict = forecast_set["LKOH"]  # direction=up
        score_conflict = allocator_score_with_forecast(
            c_lkoh,
            regime_context,
            forecast_result=fr_conflict,
            forecast_ts=now - 100.0,
            now_ts=now,
            elapsed_bars=1,
        )

        # Aligned: forecast DOWN matches SHORT
        fr_aligned = ForecastResult(
            direction="down",
            ci_width=0.03,
            confidence=0.8,
            horizon=10,
            source="timesfm",
        )
        score_aligned = allocator_score_with_forecast(
            c_lkoh,
            regime_context,
            forecast_result=fr_aligned,
            forecast_ts=now - 100.0,
            now_ts=now,
            elapsed_bars=1,
        )

        # Aligned forecast should score higher than conflicting
        assert score_aligned > score_conflict, (
            f"Aligned {score_aligned} should be > conflict {score_conflict}"
        )


# ─── Test: No live broker ──────────────────────────────────────────────


class TestNoLiveBroker:
    """Ensure no broker/tinkoff imports in new files."""

    def test_scorecard_no_live_broker(self):
        """forecast_priors.py and test files have no broker imports."""
        priors_path = os.path.join(_CODE_DIR, "forecast_priors.py")
        assert check_no_broker_imports(priors_path), (
            f"Broker import found in {priors_path}"
        )
        # Also check the test file itself
        test_path = os.path.join(
            os.path.dirname(_CODE_DIR), "tests", "test_priors_scorecard.py"
        )
        assert check_no_broker_imports(test_path), (
            f"Broker import found in {test_path}"
        )


# ─── Test: RI excluded unchanged ───────────────────────────────────────


class TestRIExcludedUnchanged:
    """RI ticker: priors = 0, no bonus, no penalty."""

    def test_scorecard_excluded_tickers_unchanged(self, forecast_set, regime_context, now):
        """RI candidate: all priors = 0, forecast_bonus = 0."""
        ri_candidate = {
            "ticker": "RI",
            "direction": "LONG",
            "win_rate": 0.95,
            "avg_win": 10.0,
            "avg_loss": -0.1,
        }
        ri_forecast = forecast_set["SBER"]  # any real forecast

        # All priors should be 0 for RI
        assert forecast_expectancy_prior(ri_candidate, ri_forecast) == 0.0
        assert forecast_regime_prior(ri_forecast, regime_context, "RI") == 0.0
        assert forecast_bonus(ri_candidate, ri_forecast) == 0.0

        # RI in EXCLUDED_TICKERS
        assert "RI" in EXCLUDED_TICKERS


# ─── Test: Allocator weight integrity ──────────────────────────────────


class TestAllocatorWeightsIntegrity:
    """Ensure allocator weights are consistent and sum to 100."""

    def test_weights_sum_to_100(self):
        """Default weights sum to 100."""
        total = sum(ALLOCATOR_WEIGHTS_FORECAST.values())
        assert total == 100

    def test_weights_keys_present(self):
        """All required weight keys are present."""
        required = {"expectancy", "risk", "regime", "forecast"}
        assert required == set(ALLOCATOR_WEIGHTS_FORECAST.keys())

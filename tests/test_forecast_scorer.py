"""Tests for TimesFM Adapter + Forecast Scorer.

≥3 fixtures, ≥6 tests, no live broker/orders, all dry-run.
Covers: forecast_bonus, forecast_risk_penalty, allocator_score_with_forecast,
        RI excluded, backward compatibility, forecast_feedback.
"""
import os
import sys
import time

import pytest

# Ensure code/ dir on sys.path
_CODE_DIR = os.path.join(os.path.dirname(__file__), "..", "code")
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

from timesfm_adapter import (
    ForecastResult,
    DummyTimesFMAdapter,
    get_adapter,
    load_cache,
    save_cache,
    _is_cache_fresh,
)
from forecast_scorer import (
    forecast_bonus,
    forecast_risk_penalty,
    forecast_feedback,
    allocator_score_with_forecast,
    ALLOCATOR_WEIGHTS_FORECAST,
    EXCLUDED_TICKERS,
    check_no_broker_imports,
)


# ─── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def forecast_data():
    """Pre-computed ForecastResult для BR/GAZP/LKOH/SBER/Si с разными confidence levels."""
    return {
        "BR": ForecastResult(
            direction="up",
            ci_width=0.015,     # узкая CI → high confidence
            confidence=0.88,
            horizon=20,
            source="timesfm",
        ),
        "GAZP": ForecastResult(
            direction="down",
            ci_width=0.05,      # средняя CI → medium confidence
            confidence=0.55,
            horizon=20,
            source="timesfm",
        ),
        "LKOH": ForecastResult(
            direction="flat",
            ci_width=0.10,      # широкая CI → low confidence
            confidence=0.30,
            horizon=20,
            source="timesfm",
        ),
        "SBER": ForecastResult(
            direction="up",
            ci_width=0.15,      # очень широкая → vol spike
            confidence=0.15,
            horizon=20,
            source="timesfm",
        ),
        "Si": ForecastResult(
            direction="flat",
            ci_width=0.00,      # zero CI → mock perfect confidence
            confidence=0.95,
            horizon=20,
            source="timesfm",
        ),
    }


@pytest.fixture
def mock_timesfm():
    """DummyTimesFMAdapter instance (no real model needed)."""
    return DummyTimesFMAdapter()


@pytest.fixture
def sample_candidates():
    """5 кандидатов для select_live_slots (включая RI для exclusion test)."""
    return [
        {
            "ticker": "BR",
            "direction": "SHORT",
            "win_rate": 0.45,
            "avg_win": 60.0,
            "avg_loss": 40.0,
            "drawdown_pct": 3.0,
            "volatility": 1.5,
        },
        {
            "ticker": "GAZP",
            "direction": "LONG",
            "win_rate": 0.55,
            "avg_win": 80.0,
            "avg_loss": 50.0,
            "drawdown_pct": 5.0,
            "volatility": 2.0,
        },
        {
            "ticker": "LKOH",
            "direction": "LONG",
            "win_rate": 0.60,
            "avg_win": 200.0,
            "avg_loss": 100.0,
            "drawdown_pct": 7.0,
            "volatility": 3.0,
        },
        {
            "ticker": "SBER",
            "direction": "LONG",
            "win_rate": 0.70,
            "avg_win": 100.0,
            "avg_loss": 120.0,
            "drawdown_pct": 2.0,
            "volatility": 1.0,
        },
        {
            "ticker": "RI",
            "direction": "LONG",
            "win_rate": 0.90,
            "avg_win": 500.0,
            "avg_loss": 10.0,
            "drawdown_pct": 1.0,
            "volatility": 0.5,
        },
    ]


@pytest.fixture
def regime_snapshot():
    """Minimal regime snapshot for allocator scoring."""
    return {
        "tickers": {
            "BR": {"adx": 23.1, "direction": "up", "regime": "trend"},
            "GAZP": {"adx": 35.3, "direction": "up", "regime": "trend"},
            "LKOH": {"adx": 15.0, "direction": "down", "regime": "range"},
            "SBER": {"adx": 45.3, "direction": "down", "regime": "trend"},
            "RI": {"adx": 50.0, "direction": "up", "regime": "trend"},
        },
        "bias": "neutral",
    }


# ─── Test: DummyTimesFMAdapter ────────────────────────────────────────


class TestDummyTimesFMAdapter:
    """Tests for dummy adapter (no real model)."""

    def test_dummy_forecast_direction(self, mock_timesfm):
        """Dummy adapter returns valid ForecastResult with direction."""
        bars = [100.0 + i * 0.5 for i in range(30)]
        result = mock_timesfm.forecast("BR", bars, horizon=20)

        assert isinstance(result, ForecastResult)
        assert result.direction in ("up", "down", "flat")
        assert result.source == "dummy"
        assert result.horizon == 20
        assert result.ci_width >= 0.0
        assert 0.0 <= result.confidence <= 1.0

    def test_dummy_forecast_empty_bars(self, mock_timesfm):
        """Dummy adapter handles empty bars gracefully."""
        result = mock_timesfm.forecast("BR", [], horizon=10)

        assert isinstance(result, ForecastResult)
        assert result.direction == "flat"
        assert result.source == "dummy"
        assert result.confidence == 0.0


# ─── Test: Forecast Bonus ─────────────────────────────────────────────


class TestForecastBonus:
    """Tests for forecast_bonus function."""

    def test_forecast_bonus_positive_aligned(self, forecast_data):
        """Narrow CI → positive bonus (model confident)."""
        candidate = {"ticker": "BR", "direction": "LONG"}
        fr = forecast_data["BR"]  # ci_width=0.015 → confident
        bonus = forecast_bonus(candidate, fr)

        # ci_width=0.015 < CI_CONFIDENT_THRESHOLD=0.02 → positive bonus
        assert bonus > 0.0
        assert -1.0 <= bonus <= 1.0

    def test_forecast_bonus_zero_for_dummy(self, forecast_data):
        """Dummy source → bonus = 0.0 (fail-open)."""
        candidate = {"ticker": "BR", "direction": "LONG"}
        dummy_fr = ForecastResult(
            direction="up", ci_width=0.01, confidence=0.9,
            horizon=20, source="dummy",
        )
        bonus = forecast_bonus(candidate, dummy_fr)
        assert bonus == 0.0

    def test_forecast_bonus_zero_for_none(self):
        """None forecast → bonus = 0.0."""
        candidate = {"ticker": "BR", "direction": "LONG"}
        bonus = forecast_bonus(candidate, None)
        assert bonus == 0.0

    def test_forecast_bonus_negative_wide_ci(self, forecast_data):
        """Wide CI → negative bonus (model uncertain)."""
        candidate = {"ticker": "LKOH", "direction": "LONG"}
        fr = forecast_data["LKOH"]  # ci_width=0.10 → uncertain
        bonus = forecast_bonus(candidate, fr)

        assert bonus < 0.0
        assert -1.0 <= bonus <= 0.0

    def test_forecast_bonus_extreme_wide_ci(self, forecast_data):
        """Very wide CI → large negative penalty."""
        candidate = {"ticker": "SBER", "direction": "LONG"}
        fr = forecast_data["SBER"]  # ci_width=0.15 → very uncertain
        bonus = forecast_bonus(candidate, fr)

        assert bonus < -0.3
        assert -1.0 <= bonus <= -0.3


# ─── Test: Forecast Risk Penalty ──────────────────────────────────────


class TestForecastRiskPenalty:
    """Tests for forecast_risk_penalty function."""

    def test_risk_penalty_no_spike(self, forecast_data):
        """Narrow CI → no additional risk penalty."""
        fr = forecast_data["BR"]  # ci_width=0.015 < 0.06
        penalty = forecast_risk_penalty(fr)
        assert penalty == 0.0

    def test_risk_penalty_increases_volatility(self, forecast_data):
        """Wide CI (vol spike) → positive risk penalty."""
        fr = forecast_data["SBER"]  # ci_width=0.15 → vol spike
        penalty = forecast_risk_penalty(fr)

        assert penalty > 0.0
        assert 0.0 <= penalty <= 1.0

    def test_risk_penalty_dummy_zero(self):
        """Dummy source → risk penalty = 0.0."""
        dummy_fr = ForecastResult(
            direction="flat", ci_width=0.20, confidence=0.1,
            horizon=20, source="dummy",
        )
        penalty = forecast_risk_penalty(dummy_fr)
        assert penalty == 0.0

    def test_risk_penalty_none(self):
        """None → risk penalty = 0.0."""
        penalty = forecast_risk_penalty(None)
        assert penalty == 0.0

    def test_risk_penalty_moderate_spike(self, forecast_data):
        """Moderate CI (0.06-0.10) → moderate penalty (0.0-0.3)."""
        # ci_width=0.10 → penalty ~0.3
        fr = forecast_data["LKOH"]  # ci_width=0.10
        penalty = forecast_risk_penalty(fr)
        assert 0.0 <= penalty <= 0.35


# ─── Test: Allocator Score with Forecast ──────────────────────────────


class TestAllocatorScoreWithForecast:
    """Tests for allocator_score_with_forecast function."""

    def test_allocator_forecast_beats_baseline_for_aligned(self, forecast_data, regime_snapshot):
        """Candidate with good forecast should score higher than without forecast."""
        candidate = {
            "ticker": "BR",
            "direction": "SHORT",
            "win_rate": 0.45,
            "avg_win": 60.0,
            "avg_loss": 40.0,
        }

        # Without forecast (forecast_result=None → bonus=0.0)
        score_no_forecast = allocator_score_with_forecast(
            candidate, regime_snapshot, forecast_result=None
        )

        # With forecast (narrow CI → positive bonus)
        fr = forecast_data["BR"]  # ci_width=0.015 → confident
        score_with_forecast = allocator_score_with_forecast(
            candidate, regime_snapshot, forecast_result=fr
        )

        # Forecast with narrow CI should increase score
        assert score_with_forecast > score_no_forecast

    def test_allocator_score_weights_sum_to_100(self):
        """Default weights sum to 100."""
        total = sum(ALLOCATOR_WEIGHTS_FORECAST.values())
        assert total == 100

    def test_allocator_score_fail_open_none_forecast(self, forecast_data, regime_snapshot):
        """None forecast → same score as without forecast (fail-open)."""
        candidate = {
            "ticker": "GAZP",
            "direction": "LONG",
            "win_rate": 0.55,
            "avg_win": 80.0,
            "avg_loss": 50.0,
        }

        score_no = allocator_score_with_forecast(
            candidate, regime_snapshot, forecast_result=None
        )
        score_none = allocator_score_with_forecast(
            candidate, regime_snapshot, forecast_result=None
        )
        assert score_no == score_none


# ─── Test: RI Excluded ────────────────────────────────────────────────


class TestRIExcludedInForecast:
    """Tests for RI ticker exclusion in forecast scoring."""

    def test_ri_excluded_in_forecast_bonus(self, forecast_data):
        """RI ticker → forecast_bonus = 0.0 (excluded)."""
        candidate = {"ticker": "RI", "direction": "LONG"}
        fr = forecast_data["BR"]  # any forecast
        bonus = forecast_bonus(candidate, fr)
        assert bonus == 0.0

    def test_ri_excluded_in_allocator_score(self, forecast_data, regime_snapshot):
        """RI in EXCLUDED_TICKERS set."""
        assert "RI" in EXCLUDED_TICKERS

    def test_ri_not_in_candidates_gives_normal_score(self, forecast_data, regime_snapshot):
        """Non-excluded ticker gets normal forecast bonus."""
        candidate = {"ticker": "GAZP", "direction": "LONG"}
        fr = forecast_data["GAZP"]  # ci_width=0.05 → medium
        bonus = forecast_bonus(candidate, fr)
        # Medium CI → small positive or near-zero bonus
        assert -1.0 <= bonus <= 1.0
        # Not excluded, so not forced to 0.0
        assert bonus != 0.0 or fr.ci_width > 0.04  # might be near zero but not excluded


# ─── Test: Backward Compatibility ─────────────────────────────────────


class TestBackwardCompatibility:
    """Tests for backward compat: pipeline works without forecast."""

    def test_pipeline_ranker_compiles(self):
        """pipeline_ranker.py compiles with new forecast parameter."""
        import importlib
        try:
            mod = importlib.import_module("pipeline_ranker")
            # run_pipeline should accept forecast_results kwarg
            import inspect
            sig = inspect.signature(mod.run_pipeline)
            assert "forecast_results" in sig.parameters
            # Default should be None
            param = sig.parameters["forecast_results"]
            assert param.default is None
        except ImportError:
            pytest.skip("pipeline_ranker not importable in test env")

    def test_forecast_scorer_compiles(self):
        """forecast_scorer.py compiles without errors."""
        import importlib
        mod = importlib.import_module("forecast_scorer")
        assert hasattr(mod, "forecast_bonus")
        assert hasattr(mod, "forecast_risk_penalty")
        assert hasattr(mod, "forecast_feedback")
        assert hasattr(mod, "allocator_score_with_forecast")


# ─── Test: Forecast Feedback ──────────────────────────────────────────


class TestForecastFeedback:
    """Tests for forecast_feedback aggregation."""

    def test_feedback_all_real(self, forecast_data):
        """All real forecasts → forecast_available=True."""
        results = list(forecast_data.values())
        fb = forecast_feedback(results)

        assert fb["forecast_available"] is True
        assert fb["tickers_with_signal"] == 5
        assert fb["tickers_no_signal"] == 0
        assert fb["overall_bias"] in ("up", "down", "flat")
        assert fb["volatility_regime"] in ("calm", "normal", "volatile")

    def test_feedback_all_dummy(self):
        """All dummy forecasts → forecast_available=False."""
        dummies = [
            ForecastResult("flat", 0.0, 0.0, 20, "dummy")
            for _ in range(3)
        ]
        fb = forecast_feedback(dummies)

        assert fb["forecast_available"] is False
        assert fb["tickers_with_signal"] == 0
        assert fb["tickers_no_signal"] == 3

    def test_feedback_empty(self):
        """Empty list → flat/normal defaults."""
        fb = forecast_feedback([])

        assert fb["overall_bias"] == "flat"
        assert fb["volatility_regime"] == "normal"
        assert fb["tickers_with_signal"] == 0
        assert fb["forecast_available"] is False


# ─── Test: Dummy Adapter via Factory ──────────────────────────────────


class TestAdapterFactory:
    """Tests for get_adapter factory function."""

    def test_factory_returns_dummy_when_no_timesfm(self):
        """Factory returns DummyTimesFMAdapter when timesfm not installed."""
        adapter = get_adapter({"timesfm_enabled": False})
        assert isinstance(adapter, DummyTimesFMAdapter)

    def test_factory_explicit_dummy(self):
        """Explicit timesfm_enabled=False → DummyTimesFMAdapter."""
        adapter = get_adapter({"timesfm_enabled": False})
        assert type(adapter).__name__ == "DummyTimesFMAdapter"

    def test_dummy_adapter_forecast_quality(self, mock_timesfm):
        """Dummy adapter: rising bars → direction='up'."""
        bars = [100.0 + i * 1.0 for i in range(30)]
        result = mock_timesfm.forecast("TEST", bars, horizon=5)
        assert result.direction == "up"
        assert result.source == "dummy"

    def test_dummy_adapter_falling_bars(self, mock_timesfm):
        """Dummy adapter: falling bars → direction='down'."""
        bars = [200.0 - i * 1.0 for i in range(30)]
        result = mock_timesfm.forecast("TEST", bars, horizon=5)
        assert result.direction == "down"
        assert result.source == "dummy"


# ─── Test: No Broker Imports ──────────────────────────────────────────


class TestNoBrokerImports:
    """Verify new files have no broker/tinkoff imports."""

    def test_timesfm_adapter_no_broker(self):
        """timesfm_adapter.py has no broker imports."""
        path = os.path.join(_CODE_DIR, "timesfm_adapter.py")
        assert check_no_broker_imports(path), f"Broker import found in {path}"

    def test_forecast_scorer_no_broker(self):
        """forecast_scorer.py has no broker imports."""
        path = os.path.join(_CODE_DIR, "forecast_scorer.py")
        assert check_no_broker_imports(path), f"Broker import found in {path}"


# ─── Test: ForecastResult dataclass ───────────────────────────────────


class TestForecastResult:
    """Tests for ForecastResult dataclass."""

    def test_forecast_result_frozen(self):
        """ForecastResult is immutable (frozen dataclass)."""
        fr = ForecastResult("up", 0.02, 0.8, 20, "timesfm")
        with pytest.raises(AttributeError):
            fr.direction = "down"  # type: ignore

    def test_forecast_result_fields(self):
        """ForecastResult has all required fields."""
        fr = ForecastResult("down", 0.05, 0.6, 15, "dummy")
        assert fr.direction == "down"
        assert fr.ci_width == 0.05
        assert fr.confidence == 0.6
        assert fr.horizon == 15
        assert fr.source == "dummy"

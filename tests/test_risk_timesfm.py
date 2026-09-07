"""Tests: RiskTimesFM — risk-on/off, stale filter, conflict filter, regime-aware prior.

≥3 fixtures, ≥6 tests, all dry-run (no broker, no network).
Validates: py_compile, AST-guard, backward-compat, scoring logic.
"""
import sys
import os
import ast
import json
import time
import pytest
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

# Ensure code/ is on sys.path
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_code_dir = os.path.join(_project_root, "code")
if _code_dir not in sys.path:
    sys.path.insert(0, _code_dir)

# Import modules under test
from risk_timesfm_config import RiskTimesFMConfig, load_config
from risk_timesfm import (
    risk_mode_from_forecast,
    forecast_stale_filter,
    forecast_conflict_filter,
    regime_aware_risk_prior,
    adjust_scorecard_with_forecast,
    _position_matches_forecast,
)
from risk_timesfm_bridge import compute_forecast_risk_adjustments, _empty_adjustments
from forecast_context import ForecastContext, ForecastResult, check_no_broker_imports


# ─── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def forecast_context_fixture():
    """Synthetic ForecastContext с mixed directions + varying confidence.

    Scenario:
      - BR: up, confidence=0.8, ci_width=0.04 (aligned up, confident)
      - GAZP: down, confidence=0.7, ci_width=0.03 (down, medium)
      - SBER: flat, confidence=0.2, ci_width=0.12 (low conf, volatile)
      Portfolio bias: mixed, vol_regime: volatile (SBER CI wide)
    """
    per_ticker = {
        "BR": ForecastResult(
            direction="up", ci_width=0.04, confidence=0.8, horizon=20,
            source="timesfm",
        ),
        "GAZP": ForecastResult(
            direction="down", ci_width=0.03, confidence=0.7, horizon=20,
            source="timesfm",
        ),
        "SBER": ForecastResult(
            direction="flat", ci_width=0.12, confidence=0.2, horizon=20,
            source="timesfm",
        ),
    }
    meta = {
        "tickers_with_signal": 3,
        "tickers_no_signal": 0,
        "avg_ci_width": 0.0633,
        "n_tickers": 3,
    }
    return ForecastContext(
        per_ticker=per_ticker,
        portfolio_bias="mixed",
        volatility_regime="volatile",
        confidence_score=0.5667,
        meta=meta,
    )


@pytest.fixture
def regime_snapshot_fixture():
    """Regime snapshot: mixed trend/range.

    BR+GAZP in trend (up), SBER in range (down).
    2 trends out of 3 → trend-majority.
    """
    return {
        "ts": "2026-08-23T00:00:00",
        "tickers": {
            "BR": {"adx": 32.0, "direction": "up", "regime": "trend", "atr_pct": 0.279},
            "GAZP": {"adx": 28.5, "direction": "down", "regime": "trend", "atr_pct": 0.299},
            "SBER": {"adx": 14.0, "direction": "flat", "regime": "range", "atr_pct": 0.183},
        },
        "bias": "neutral",
        "ups": 1,
        "downs": 1,
        "trend_cnt": 2,
    }


@pytest.fixture
def slot_fixture():
    """Synthetic slot: LONG BR, age 2h."""
    return {
        "slot_id": "BR_trend_001",
        "ticker": "BR",
        "strategy": "trend_follow",
        "direction": "LONG",
        "contracts": 1,
        "go_rub": 574.59,
        "entry_price": 185.20,
        "age_hours": 2.0,
    }


@pytest.fixture
def config_fixture():
    """RiskTimesFMConfig with default thresholds."""
    return RiskTimesFMConfig.default()


@pytest.fixture
def high_confidence_context():
    """High confidence context → risk-on candidate.

    All forecasts aligned "up", high confidence, calm vol.
    """
    per_ticker = {
        "BR": ForecastResult(
            direction="up", ci_width=0.02, confidence=0.9, horizon=20,
            source="timesfm",
        ),
        "GAZP": ForecastResult(
            direction="up", ci_width=0.03, confidence=0.85, horizon=20,
            source="timesfm",
        ),
    }
    return ForecastContext(
        per_ticker=per_ticker,
        portfolio_bias="up",
        volatility_regime="calm",
        confidence_score=0.875,
        meta={"tickers_with_signal": 2, "tickers_no_signal": 0, "avg_ci_width": 0.025, "n_tickers": 2},
    )


@pytest.fixture
def low_confidence_context():
    """Low confidence context → risk-off candidate.

    All forecasts "down", low confidence, volatile vol.
    """
    per_ticker = {
        "BR": ForecastResult(
            direction="down", ci_width=0.10, confidence=0.15, horizon=20,
            source="timesfm",
        ),
        "GAZP": ForecastResult(
            direction="down", ci_width=0.09, confidence=0.20, horizon=20,
            source="timesfm",
        ),
    }
    return ForecastContext(
        per_ticker=per_ticker,
        portfolio_bias="down",
        volatility_regime="volatile",
        confidence_score=0.175,
        meta={"tickers_with_signal": 2, "tickers_no_signal": 0, "avg_ci_width": 0.095, "n_tickers": 2},
    )


# ─── Tests ─────────────────────────────────────────────────────────────

class TestRiskMode:
    """Tests for risk_mode_from_forecast."""

    def test_risk_mode_risk_on(self, high_confidence_context, config_fixture):
        """High confidence + aligned bias + calm vol → 'risk-on'."""
        mode = risk_mode_from_forecast(high_confidence_context, config_fixture)
        assert mode == "risk-on", f"Expected risk-on, got {mode}"

    def test_risk_mode_risk_off(self, low_confidence_context, config_fixture):
        """Low confidence + adverse bias + volatile vol → 'risk-off'."""
        mode = risk_mode_from_forecast(low_confidence_context, config_fixture)
        assert mode == "risk-off", f"Expected risk-off, got {mode}"

    def test_risk_mode_neutral_no_forecast(self, config_fixture):
        """No forecast → 'neutral' (fail-open)."""
        ctx = ForecastContext(
            per_ticker={},
            portfolio_bias="flat",
            volatility_regime="normal",
            confidence_score=0.0,
            meta={"tickers_with_signal": 0, "tickers_no_signal": 5, "avg_ci_width": 0.0, "n_tickers": 5},
        )
        mode = risk_mode_from_forecast(ctx, config_fixture)
        assert mode == "neutral"


class TestStaleFilter:
    """Tests for forecast_stale_filter."""

    def test_stale_filter_old_forecast(self, config_fixture):
        """Forecast >8h → is_stale=True."""
        per_ticker = {
            "BR": ForecastResult(
                direction="up", ci_width=0.04, confidence=0.7, horizon=20,
                source="timesfm",
            ),
        }
        ctx = ForecastContext(
            per_ticker=per_ticker,
            portfolio_bias="up",
            volatility_regime="normal",
            confidence_score=0.7,
            meta={"tickers_with_signal": 1, "tickers_no_signal": 0, "avg_ci_width": 0.04, "n_tickers": 1},
        )
        slot = {"slot_id": "BR_001", "ticker": "BR", "direction": "LONG", "age_hours": 12.0}
        result = forecast_stale_filter(slot, ctx, config_fixture)
        assert result["is_stale"] is True
        assert "age" in result["reason"]

    def test_stale_filter_aligned_direction(self, slot_fixture, forecast_context_fixture, config_fixture):
        """Fresh forecast + aligned direction → is_stale=False."""
        result = forecast_stale_filter(slot_fixture, forecast_context_fixture, config_fixture)
        assert result["is_stale"] is False

    def test_stale_filter_mismatched_direction(self, forecast_context_fixture, config_fixture):
        """SHORT position + up forecast (high confidence) → is_stale=True."""
        slot = {"slot_id": "BR_001", "ticker": "BR", "direction": "SHORT", "age_hours": 1.0}
        result = forecast_stale_filter(slot, forecast_context_fixture, config_fixture)
        assert result["is_stale"] is True
        assert "mismatch" in result["reason"]

    def test_stale_filter_no_forecast(self, config_fixture):
        """No forecast for ticker → is_stale=False (fail-open)."""
        ctx = ForecastContext(
            per_ticker={},
            portfolio_bias="flat",
            volatility_regime="normal",
            confidence_score=0.0,
            meta={"tickers_with_signal": 0, "tickers_no_signal": 1, "avg_ci_width": 0.0, "n_tickers": 1},
        )
        slot = {"slot_id": "UNKNOWN_001", "ticker": "UNKNOWN", "direction": "LONG", "age_hours": 1.0}
        result = forecast_stale_filter(slot, ctx, config_fixture)
        assert result["is_stale"] is False


class TestConflictFilter:
    """Tests for forecast_conflict_filter."""

    def test_conflict_filter_direction_mismatch(self, forecast_context_fixture, config_fixture):
        """LONG BR + down GAZP forecast → direction_mismatch on GAZP slot."""
        slot = {"slot_id": "GAZP_001", "ticker": "GAZP", "direction": "LONG", "age_hours": 1.0}
        result = forecast_conflict_filter(slot, forecast_context_fixture, config_fixture)
        # GAZP forecast is "down", position is LONG → mismatch
        assert result["has_conflict"] is True
        assert result["conflict_type"] == "direction_mismatch"
        assert result["severity"] > 0

    def test_conflict_filter_no_conflict(self, slot_fixture, forecast_context_fixture, config_fixture):
        """LONG BR + up forecast → no conflict."""
        result = forecast_conflict_filter(slot_fixture, forecast_context_fixture, config_fixture)
        assert result["has_conflict"] is False
        assert result["conflict_type"] == "none"

    def test_conflict_filter_vol_spike(self, config_fixture):
        """Volatile vol + high ci_width → vol_spike conflict."""
        per_ticker = {
            "BR": ForecastResult(
                direction="up", ci_width=0.10, confidence=0.8, horizon=20,
                source="timesfm",
            ),
        }
        ctx = ForecastContext(
            per_ticker=per_ticker,
            portfolio_bias="up",
            volatility_regime="volatile",
            confidence_score=0.8,
            meta={"tickers_with_signal": 1, "tickers_no_signal": 0, "avg_ci_width": 0.10, "n_tickers": 1},
        )
        slot = {"slot_id": "BR_001", "ticker": "BR", "direction": "LONG", "age_hours": 1.0}
        result = forecast_conflict_filter(slot, ctx, config_fixture)
        assert result["has_conflict"] is True
        assert result["conflict_type"] == "vol_spike"


class TestRegimePrior:
    """Tests for regime_aware_risk_prior."""

    def test_regime_prior_trend_vs_range(self, forecast_context_fixture, config_fixture):
        """Trend regime → higher max_slots, wider delta vs range."""
        trend_regime = {"tickers": {"BR": {"regime": "trend"}, "GAZP": {"regime": "trend"}}, "trend_cnt": 2}
        range_regime = {"tickers": {"SBER": {"regime": "range"}, "LKOH": {"regime": "range"}}, "trend_cnt": 0}

        trend_prior = regime_aware_risk_prior(trend_regime, forecast_context_fixture, config_fixture)
        range_prior = regime_aware_risk_prior(range_regime, forecast_context_fixture, config_fixture)

        # Trend should have higher max_slots and wider delta
        assert trend_prior["max_slots"] >= range_prior["max_slots"]
        assert trend_prior["delta_band_pct"] >= range_prior["delta_band_pct"]

    def test_regime_prior_risk_off_caps_slots(self, low_confidence_context, config_fixture):
        """Risk-off should cap max_slots to 2."""
        regime = {"tickers": {"BR": {"regime": "trend"}}, "trend_cnt": 1}
        prior = regime_aware_risk_prior(regime, low_confidence_context, config_fixture)
        assert prior["max_slots"] <= 2
        assert prior["risk_mode"] == "risk-off"

    def test_regime_prior_base_config(self, high_confidence_context, config_fixture):
        """Custom base_config values propagate through."""
        base = {"max_slots": 5, "slot_eject_pf": 0.8, "delta_band_pct": 25, "slot_eject_silent_days": 3}
        regime = {"tickers": {"BR": {"regime": "trend"}}, "trend_cnt": 1}
        prior = regime_aware_risk_prior(regime, high_confidence_context, config_fixture, base)
        assert prior["max_slots"] >= 1


class TestScorecardAdjustment:
    """Tests for adjust_scorecard_with_forecast."""

    def test_scorecard_risk_off_penalty(self, config_fixture):
        """Risk-off adds penalty to scorecard."""
        scorecard = {"risk_score": 50, "components": {}}
        result = adjust_scorecard_with_forecast(scorecard, "risk-off", config=config_fixture)
        assert result["forecast_adjustment"]["risk_mode"] == "risk-off"
        assert result["forecast_adjustment"]["forecast_penalty"] > 0

    def test_scorecard_risk_on_bonus(self, config_fixture):
        """Risk-on reduces risk score."""
        scorecard = {"risk_score": 50, "components": {}}
        result = adjust_scorecard_with_forecast(scorecard, "risk-on", config=config_fixture)
        assert result["forecast_adjustment"]["forecast_penalty"] < 0

    def test_scorecard_stale_conflict_penalty(self, config_fixture):
        """Stale + conflict slots add penalties."""
        scorecard = {"risk_score": 50}
        stale_map = {"slot_1": True, "slot_2": False}
        conflict_map = {
            "slot_1": {"has_conflict": True, "conflict_type": "direction_mismatch", "severity": 0.8},
            "slot_2": {"has_conflict": False, "conflict_type": "none", "severity": 0.0},
        }
        result = adjust_scorecard_with_forecast(
            scorecard, "neutral", stale_map, conflict_map, config=config_fixture
        )
        adj = result["forecast_adjustment"]
        assert adj["stale_count"] == 1
        assert adj["conflict_count"] == 1
        assert adj["forecast_penalty"] > 0


class TestBridge:
    """Tests for compute_forecast_risk_adjustments (integration bridge)."""

    def test_bridge_full_pipeline(
        self, forecast_context_fixture, regime_snapshot_fixture, config_fixture
    ):
        """Bridge returns complete adjustments dict."""
        slots = [
            {"slot_id": "BR_001", "ticker": "BR", "direction": "LONG", "age_hours": 2.0},
            {"slot_id": "GAZP_001", "ticker": "GAZP", "direction": "LONG", "age_hours": 1.0},
        ]
        result = compute_forecast_risk_adjustments(
            slots, forecast_context_fixture, regime_snapshot_fixture, config_fixture
        )
        assert result["meta"]["available"] is True
        assert len(result["per_slot"]) == 2
        assert result["risk_mode"] in ("risk-on", "risk-off", "neutral")
        assert "max_slots" in result["prior"]
        assert "eject_pf" in result["prior"]

    def test_bridge_no_context(self, config_fixture):
        """No forecast context → empty adjustments (backward-compat)."""
        slots = [{"slot_id": "BR_001", "ticker": "BR", "direction": "LONG"}]
        result = compute_forecast_risk_adjustments(slots, forecast_context=None)
        assert result["meta"]["available"] is False
        assert result["risk_mode"] == "neutral"

    def test_bridge_with_scorecard(
        self, high_confidence_context, regime_snapshot_fixture, config_fixture
    ):
        """Bridge with scorecard returns modified scorecard."""
        slots = [{"slot_id": "BR_001", "ticker": "BR", "direction": "LONG", "age_hours": 1.0}]
        scorecard = {"risk_score": 40, "components": {}}
        result = compute_forecast_risk_adjustments(
            slots, high_confidence_context, regime_snapshot_fixture, config_fixture,
            scorecard=scorecard,
        )
        assert result["scorecard"] is not None
        assert "forecast_adjustment" in result["scorecard"]


class TestPositionMatchesForecast:
    """Tests for _position_matches_forecast helper."""

    def test_long_up(self):
        assert _position_matches_forecast("LONG", "up") is True

    def test_long_down(self):
        assert _position_matches_forecast("LONG", "down") is False

    def test_short_down(self):
        assert _position_matches_forecast("SHORT", "down") is True

    def test_short_up(self):
        assert _position_matches_forecast("SHORT", "up") is False

    def test_flat_forecast(self):
        assert _position_matches_forecast("LONG", "flat") is True
        assert _position_matches_forecast("SHORT", "flat") is True


class TestConfig:
    """Tests for RiskTimesFMConfig."""

    def test_config_from_dict(self):
        """Config loads from dict correctly."""
        data = {"risk_on_threshold": 0.7, "risk_off_threshold": 0.2, "stale_age_hours": 12}
        cfg = RiskTimesFMConfig.from_dict(data)
        assert cfg.risk_on_threshold == 0.7
        assert cfg.risk_off_threshold == 0.2
        assert cfg.stale_age_hours == 12

    def test_config_default(self):
        """Default config has standard values."""
        cfg = RiskTimesFMConfig.default()
        assert cfg.risk_on_threshold == 0.6
        assert cfg.risk_off_threshold == 0.3
        assert cfg.stale_age_hours == 8

    def test_config_to_dict_roundtrip(self):
        """Config → dict → Config preserves values."""
        original = RiskTimesFMConfig(risk_on_threshold=0.75)
        d = original.to_dict()
        restored = RiskTimesFMConfig.from_dict(d)
        assert restored.risk_on_threshold == 0.75

    def test_config_unknown_keys_ignored(self):
        """Unknown keys in dict are ignored."""
        data = {"risk_on_threshold": 0.5, "unknown_field": 42, "also_unknown": "hello"}
        cfg = RiskTimesFMConfig.from_dict(data)
        assert cfg.risk_on_threshold == 0.5


# ─── AST-guard: no broker imports in new modules ──────────────────────

NEW_MODULES = [
    "risk_timesfm.py",
    "risk_timesfm_config.py",
    "risk_timesfm_bridge.py",
]

@pytest.mark.parametrize("module_name", NEW_MODULES)
def test_no_broker_imports_in_new_modules(module_name):
    """AST-guard: new modules must not import broker/client."""
    filepath = os.path.join(_code_dir, module_name)
    assert os.path.isfile(filepath), f"Module not found: {filepath}"
    result = check_no_broker_imports(filepath)
    assert result is True, f"Broker imports found in {module_name}"


def test_py_compile_all_new_modules():
    """All new modules must compile without errors."""
    import py_compile
    for module_name in NEW_MODULES:
        filepath = os.path.join(_code_dir, module_name)
        py_compile.compile(filepath, doraise=True)

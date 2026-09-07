"""Tests for direction enforcement + metrics validation.

≥3 fixtures, ≥5 test cases covering:
  - direction validation (LONG, SHORT, UNKNOWN+reason, missing, invalid)
  - metrics schema (complete, missing fields)
  - name-based direction inference
  - batch enforcement (enforce_directions)
"""
import sys
from pathlib import Path

import pytest

# Ensure code/ is importable
COMBINE_DIR = Path(__file__).resolve().parent.parent
CODE_DIR = COMBINE_DIR / "code"
sys.path.insert(0, str(CODE_DIR))

from direction_enforcer import (
    DirectionPolicy,
    REQUIRED_SCORING_METRICS,
    enforce_directions,
    infer_direction_from_strategy_name,
    validate_metrics_schema,
    validate_record_direction,
    validate_registry_directions,
)


# ─── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def direction_registry_fixture() -> list[dict]:
    """Registry with 3 records: LONG, SHORT, UNKNOWN+reason."""
    return [
        {
            "strategy_id": "s1",
            "ticker": "GAZP",
            "strategy": "trend_follow",
            "direction": "LONG",
            "metrics": {"pnl": 1200, "trades": 40},
        },
        {
            "strategy_id": "s2",
            "ticker": "LKOH",
            "strategy": "vwap_reversion",
            "direction": "SHORT",
            "metrics": {"pnl": 800, "trades": 25},
        },
        {
            "strategy_id": "s3",
            "ticker": "SBER",
            "strategy": "random_forest",
            "direction": "UNKNOWN",
            "direction_unknown_reason": "low win rate below threshold",
            "metrics": {"pnl": -200, "trades": 10},
        },
    ]


@pytest.fixture
def directionless_record_fixture() -> dict:
    """Record without direction and without direction_unknown_reason."""
    return {
        "strategy_id": "s_no_dir",
        "ticker": "BR",
        "strategy": "bollinger_breakout",
        "metrics": {"pnl": 500, "trades": 15},
    }


@pytest.fixture
def complete_metrics_fixture() -> dict:
    """Metrics dict with all REQUIRED_SCORING_METRICS present."""
    return {
        "win_rate": 0.55,
        "avg_win": 150.0,
        "avg_loss": 80.0,
        "trades": 45,
        "trades_per_day": 2.1,
        "drawdown_pct": 7.5,
        "pnl": 3200.0,
        "sharpe": 0.72,
        "pf": 1.85,
        "volatility": 3.2,
    }


# ─── Tests: direction validation ─────────────────────────────────────


class TestDirectionValidation:
    """validate_record_direction and validate_registry_directions."""

    def test_long_direction_passes(self, direction_registry_fixture):
        """LONG direction is always valid."""
        rec = direction_registry_fixture[0]
        is_valid, reason = validate_record_direction(rec)
        assert is_valid is True
        assert reason == ""

    def test_short_direction_passes(self, direction_registry_fixture):
        """SHORT direction is always valid."""
        rec = direction_registry_fixture[1]
        is_valid, reason = validate_record_direction(rec)
        assert is_valid is True
        assert reason == ""

    def test_unknown_with_reason_passes(self, direction_registry_fixture):
        """UNKNOWN with non-empty direction_unknown_reason is valid."""
        rec = direction_registry_fixture[2]
        is_valid, reason = validate_record_direction(rec)
        assert is_valid is True
        assert reason == ""

    def test_unknown_without_reason_fails(self):
        """UNKNOWN without reason is invalid."""
        rec = {"strategy_id": "x", "direction": "UNKNOWN", "direction_unknown_reason": ""}
        is_valid, reason = validate_record_direction(rec)
        assert is_valid is False
        assert "empty" in reason

    def test_missing_direction_fails(self, directionless_record_fixture):
        """Record with no direction at all is invalid."""
        is_valid, reason = validate_record_direction(directionless_record_fixture)
        assert is_valid is False
        assert "missing" in reason

    def test_invalid_direction_value_fails(self):
        """Non-enum direction value is invalid."""
        rec = {"strategy_id": "y", "direction": "BOTH"}
        is_valid, reason = validate_record_direction(rec)
        assert is_valid is False
        assert "invalid" in reason.lower()

    def test_registry_batch_valid(self, direction_registry_fixture):
        """All valid records → empty error list."""
        errors = validate_registry_directions(direction_registry_fixture)
        assert errors == []

    def test_registry_batch_with_errors(self, directionless_record_fixture):
        """Record without direction shows up in errors."""
        errors = validate_registry_directions([directionless_record_fixture])
        assert len(errors) == 1
        assert errors[0]["strategy_id"] == "s_no_dir"


# ─── Tests: metrics schema ────────────────────────────────────────────


class TestMetricsSchema:
    """validate_metrics_schema required-field checks."""

    def test_complete_metrics_pass(self, complete_metrics_fixture):
        """All required fields present → empty missing list."""
        missing = validate_metrics_schema(complete_metrics_fixture)
        assert missing == []

    def test_missing_win_rate(self):
        """Missing win_rate shows up in missing list."""
        metrics = {"avg_win": 100, "avg_loss": 50, "trades": 20, "trades_per_day": 1.0, "drawdown_pct": 3.0}
        missing = validate_metrics_schema(metrics)
        assert "win_rate" in missing

    def test_missing_multiple_fields(self):
        """Multiple missing fields are all reported."""
        metrics = {"pnl": 500}
        missing = validate_metrics_schema(metrics)
        assert set(missing) == {"avg_loss", "avg_win", "drawdown_pct", "trades", "trades_per_day", "win_rate"}

    def test_empty_metrics(self):
        """Empty metrics → all required fields missing."""
        missing = validate_metrics_schema({})
        assert set(missing) == REQUIRED_SCORING_METRICS


# ─── Tests: direction inference ───────────────────────────────────────


class TestDirectionInference:
    """infer_direction_from_strategy_name heuristic tests."""

    def test_trend_keyword_infers_long(self):
        """Trend-related keyword → LONG."""
        direction, reason = infer_direction_from_strategy_name("GAZP_trend_follow")
        assert direction == "LONG"
        assert "trend" in reason.lower()

    def test_mean_reversion_infers_short(self):
        """Mean-reversion keyword → SHORT."""
        direction, reason = infer_direction_from_strategy_name("LKOH_mean_reversion_bband")
        assert direction == "SHORT"
        assert "mean" in reason.lower() or "reversion" in reason.lower()

    def test_no_keyword_infers_unknown(self):
        """No recognizable keyword → UNKNOWN."""
        direction, reason = infer_direction_from_strategy_name("cryptic_alpha")
        assert direction == "UNKNOWN"
        assert "no" in reason.lower()

    def test_breakout_infers_long(self):
        """Breakout keyword → LONG."""
        direction, _ = infer_direction_from_strategy_name("SBER_breakout_momentum")
        assert direction == "LONG"

    def test_vwap_infers_short(self):
        """VWAP keyword → SHORT."""
        direction, _ = infer_direction_from_strategy_name("LKOH_vwap_reversion")
        assert direction == "SHORT"


# ─── Tests: batch enforcement ─────────────────────────────────────────


class TestEnforceDirections:
    """enforce_directions batch migration tests."""

    def test_valid_records_unchanged(self, direction_registry_fixture):
        """Records with valid direction are returned unchanged."""
        enriched = enforce_directions(direction_registry_fixture)
        assert len(enriched) == 3
        for orig, new in zip(direction_registry_fixture, enriched):
            assert new["direction"] == orig["direction"]

    def test_directionless_gets_inferred(self, directionless_record_fixture):
        """Record without direction gets direction from inference."""
        enriched = enforce_directions([directionless_record_fixture])
        assert len(enriched) == 1
        rec = enriched[0]
        assert rec["direction"] in ("LONG", "SHORT", "UNKNOWN")
        # bollinger_breakout → bollinger matches SHORT, breakout matches LONG
        # first pattern wins (mean_rev/bollinger)
        assert rec["direction"] == "SHORT"

    def test_mixed_registry(self, direction_registry_fixture, directionless_record_fixture):
        """Mix of valid + directionless → all get direction."""
        combined = direction_registry_fixture + [directionless_record_fixture]
        enriched = enforce_directions(combined)
        assert len(enriched) == 4
        for rec in enriched:
            assert "direction" in rec
            assert rec["direction"] in ("LONG", "SHORT", "UNKNOWN")

    def test_original_not_mutated(self, directionless_record_fixture):
        """enforce_directions does not mutate input dicts."""
        original = dict(directionless_record_fixture)
        enforce_directions([directionless_record_fixture])
        assert directionless_record_fixture == original

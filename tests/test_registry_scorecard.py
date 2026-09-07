"""Tests: Registry Scorecard — scoring + ranking for enriched registry.

>=3 fixtures, >=5 test cases covering:
  - scorecard ranked list is non-empty
  - RI strategy excluded from ranked
  - len(ranked) <= max_slots
  - all ranked strategies have direction LONG or SHORT
  - violations list populated for excluded / unknown-direction strategies
  - format_scorecard_report produces non-empty markdown
"""
import sys
from pathlib import Path

import pytest

# Ensure code/ is importable
COMBINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(COMBINE_DIR / "code"))

from registry_scorecard import build_registry_scorecard, format_scorecard_report


# ─── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def enriched_registry_valid():
    """Enriched registry: 5 strategies with direction + full metrics."""
    return {
        "version": 1,
        "strategies": {
            "s1_good": {
                "strategy_id": "s1_good",
                "ticker": "LKOH",
                "strategy": "vwap_reversion",
                "status": "active",
                "direction": "SHORT",
                "metrics": {
                    "win_rate": 0.60,
                    "avg_win": 200.0,
                    "avg_loss": 100.0,
                    "trades": 30,
                    "trades_per_day": 1.2,
                    "drawdown_pct": 3.0,
                    "pnl": 1500.0,
                },
            },
            "s2_ok": {
                "strategy_id": "s2_ok",
                "ticker": "GAZP",
                "strategy": "trend_follow",
                "status": "active",
                "direction": "LONG",
                "metrics": {
                    "win_rate": 0.55,
                    "avg_win": 180.0,
                    "avg_loss": 90.0,
                    "trades": 25,
                    "trades_per_day": 1.0,
                    "drawdown_pct": 5.0,
                    "pnl": 800.0,
                },
            },
            "s3_mid": {
                "strategy_id": "s3_mid",
                "ticker": "SBER",
                "strategy": "bband_rsi",
                "status": "active",
                "direction": "SHORT",
                "metrics": {
                    "win_rate": 0.45,
                    "avg_win": 120.0,
                    "avg_loss": 110.0,
                    "trades": 20,
                    "trades_per_day": 0.8,
                    "drawdown_pct": 7.0,
                    "pnl": 200.0,
                },
            },
            "s4_poor": {
                "strategy_id": "s4_poor",
                "ticker": "BR",
                "strategy": "meanrev",
                "status": "active",
                "direction": "SHORT",
                "metrics": {
                    "win_rate": 0.35,
                    "avg_win": 80.0,
                    "avg_loss": 150.0,
                    "trades": 15,
                    "trades_per_day": 0.5,
                    "drawdown_pct": 12.0,
                    "pnl": -500.0,
                },
            },
            "s5_unknown": {
                "strategy_id": "s5_unknown",
                "ticker": "Si",
                "strategy": "ml_classifier",
                "status": "active",
                "direction": "UNKNOWN",
                "direction_unknown_reason": "insufficient data",
                "metrics": {
                    "win_rate": 0.50,
                    "avg_win": 100.0,
                    "avg_loss": 100.0,
                    "trades": 5,
                    "trades_per_day": 0.3,
                    "drawdown_pct": 4.0,
                    "pnl": 0.0,
                },
            },
        },
    }


@pytest.fixture
def enriched_registry_with_ri():
    """Enriched registry with an RI strategy added."""
    base = {
        "strategies": {
            "good_strat": {
                "strategy_id": "good_strat",
                "ticker": "LKOH",
                "strategy": "vwap_reversion",
                "status": "active",
                "direction": "SHORT",
                "metrics": {
                    "win_rate": 0.6,
                    "avg_win": 200.0,
                    "avg_loss": 100.0,
                    "trades": 30,
                    "trades_per_day": 1.2,
                    "drawdown_pct": 3.0,
                    "pnl": 1500.0,
                },
            },
            "ri_strat": {
                "strategy_id": "ri_strat",
                "ticker": "RI",
                "strategy": "ri_pattern",
                "status": "active",
                "direction": "LONG",
                "metrics": {
                    "win_rate": 0.55,
                    "avg_win": 150.0,
                    "avg_loss": 80.0,
                    "trades": 20,
                    "trades_per_day": 1.0,
                    "drawdown_pct": 4.0,
                    "pnl": 700.0,
                },
            },
        }
    }
    return base


@pytest.fixture
def minimal_config():
    """Minimal config with standard constraints."""
    return {
        "excluded": ["RI"],
        "risk": {
            "max_slots": 3,
            "max_contracts_per_entry": 1,
        },
    }


# ─── Tests ────────────────────────────────────────────────────────────


class TestBuildRegistryScorecard:
    """Tests for build_registry_scorecard()."""

    def test_ranked_non_empty(self, enriched_registry_valid, minimal_config):
        """Scorecard produces a non-empty ranked list for valid input."""
        sc = build_registry_scorecard(enriched_registry_valid, minimal_config)
        assert len(sc["ranked"]) > 0

    def test_ri_excluded(self, enriched_registry_with_ri, minimal_config):
        """RI strategy is excluded from ranked list."""
        sc = build_registry_scorecard(enriched_registry_with_ri, minimal_config)
        ranked_ids = [r["strategy_id"] for r in sc["ranked"]]
        assert "ri_strat" not in ranked_ids
        # Violation should note RI exclusion
        assert any("ri_strat" in v for v in sc["violations"])

    def test_max_slots_respected(self, enriched_registry_valid, minimal_config):
        """Ranked list length <= max_slots."""
        sc = build_registry_scorecard(enriched_registry_valid, minimal_config)
        max_slots = minimal_config["risk"]["max_slots"]
        assert len(sc["ranked"]) <= max_slots

    def test_all_ranked_have_valid_direction(self, enriched_registry_valid, minimal_config):
        """All ranked strategies have direction LONG or SHORT."""
        sc = build_registry_scorecard(enriched_registry_valid, minimal_config)
        for entry in sc["ranked"]:
            assert entry["direction"] in ("LONG", "SHORT"), (
                f"{entry['strategy_id']} has invalid direction: {entry['direction']}"
            )

    def test_unknown_direction_not_ranked(self, enriched_registry_valid, minimal_config):
        """UNKNOWN direction strategies are not in ranked list."""
        sc = build_registry_scorecard(enriched_registry_valid, minimal_config)
        for entry in sc["ranked"]:
            assert entry["direction"] != "UNKNOWN"

    def test_violations_populated_for_excluded(self, enriched_registry_with_ri, minimal_config):
        """Violations list is populated when strategies are excluded."""
        sc = build_registry_scorecard(enriched_registry_with_ri, minimal_config)
        assert len(sc["violations"]) > 0

    def test_constraints_reflect_config(self, enriched_registry_valid, minimal_config):
        """Constraints in output match config values."""
        sc = build_registry_scorecard(enriched_registry_valid, minimal_config)
        assert sc["constraints"]["max_slots"] == 3
        assert sc["constraints"]["max_contracts_per_entry"] == 1
        assert "RI" in sc["constraints"]["excluded"]

    def test_ranked_sorted_by_score_desc(self, enriched_registry_valid, minimal_config):
        """Ranked list is sorted by allocator_score descending."""
        sc = build_registry_scorecard(enriched_registry_valid, minimal_config)
        scores = [r["allocator_score"] for r in sc["ranked"]]
        assert scores == sorted(scores, reverse=True)


class TestFormatScorecardReport:
    """Tests for format_scorecard_report()."""

    def test_report_non_empty(self, enriched_registry_valid, minimal_config):
        """Report is a non-empty string."""
        sc = build_registry_scorecard(enriched_registry_valid, minimal_config)
        report = format_scorecard_report(sc)
        assert isinstance(report, str)
        assert len(report) > 0

    def test_report_contains_table(self, enriched_registry_valid, minimal_config):
        """Report contains a markdown table with Rank header."""
        sc = build_registry_scorecard(enriched_registry_valid, minimal_config)
        report = format_scorecard_report(sc)
        assert "| Rank |" in report
        assert "|------|" in report

    def test_report_empty_when_no_ranked(self, minimal_config):
        """Report handles empty ranked list gracefully."""
        empty_sc = {"ranked": [], "constraints": {}, "violations": []}
        report = format_scorecard_report(empty_sc)
        assert "No strategies scored" in report

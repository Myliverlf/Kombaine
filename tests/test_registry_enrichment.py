"""Tests: Registry Enricher — direction enforcement + metrics validation.

>=3 fixtures, >=6 test cases covering:
  - enrichment adds direction to all strategies
  - UNKNOWN direction with reason passes validation
  - broken/missing metrics produce errors in readiness
  - RI strategy flagged in readiness warnings
  - validate_registry_readiness passes for valid registry
  - legacy direction-in-metrics is promoted to top-level
"""
import sys
from pathlib import Path

import pytest

# Ensure code/ is importable
COMBINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(COMBINE_DIR / "code"))

from registry_enricher import enrich_registry, validate_registry_readiness


# ─── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def real_registry():
    """Real registry data from state/strategy_registry.json (inline subset).

    5 strategies, none with top-level direction, metrics incomplete.
    Source: analysis.md F1/F2.
    """
    return {
        "version": 1,
        "strategies": {
            "LKOH_vwap_reversion": {
                "strategy_id": "LKOH_vwap_reversion",
                "ticker": "LKOH",
                "strategy": "vwap_reversion",
                "status": "active_signal_pool",
                "metrics": {
                    "direction": "SHORT",
                    "pnl": 538.21,
                    "rank_score": 538.21,
                    "trades": 4,
                },
            },
            "GAZP_ft_bband_rsi": {
                "strategy_id": "GAZP_ft_bband_rsi",
                "ticker": "GAZP",
                "strategy": "ft_bband_rsi",
                "status": "active_watchlist",
                "metrics": {
                    "pnl": -58.86,
                    "rank_score": -58.86,
                    "trades": 1,
                },
            },
            "LKOH_nfi_trend": {
                "strategy_id": "LKOH_nfi_trend",
                "ticker": "LKOH",
                "strategy": "nfi_trend",
                "status": "active_watchlist",
                "metrics": {
                    "pnl": 0.0,
                    "rank_score": 0.0,
                    "trades": 0,
                },
            },
            "GAZP_bollinger_reversion": {
                "strategy_id": "GAZP_bollinger_reversion",
                "ticker": "GAZP",
                "strategy": "bollinger_reversion",
                "status": "active_watchlist",
                "metrics": {
                    "pnl": 102.64,
                    "rank_score": 102.64,
                    "trades": 1,
                },
            },
            "LKOH_nateemma_basket_meanrev": {
                "strategy_id": "LKOH_nateemma_basket_meanrev",
                "ticker": "LKOH",
                "strategy": "nateemma_basket_meanrev",
                "status": "active_watchlist",
                "metrics": {
                    "pnl": 0.0,
                    "rank_score": 0.0,
                    "trades": 0,
                },
            },
        },
    }


@pytest.fixture
def synthetic_registry():
    """Synthetic registry with 3 strategies: LONG, SHORT, UNKNOWN-no-reason."""
    return {
        "version": 1,
        "strategies": {
            "s1_long": {
                "strategy_id": "s1_long",
                "ticker": "GAZP",
                "strategy": "trend_follow",
                "status": "active",
                "direction": "LONG",
                "metrics": {
                    "win_rate": 0.6,
                    "avg_win": 200.0,
                    "avg_loss": 100.0,
                    "trades": 40,
                    "trades_per_day": 1.5,
                    "drawdown_pct": 5.0,
                },
            },
            "s2_short": {
                "strategy_id": "s2_short",
                "ticker": "LKOH",
                "strategy": "vwap_reversion",
                "status": "active",
                "direction": "SHORT",
                "metrics": {
                    "win_rate": 0.55,
                    "avg_win": 150.0,
                    "avg_loss": 80.0,
                    "trades": 30,
                    "trades_per_day": 1.2,
                    "drawdown_pct": 3.5,
                },
            },
            "s3_unknown": {
                "strategy_id": "s3_unknown",
                "ticker": "SBER",
                "strategy": "random_forest",
                "status": "active",
                "direction": "UNKNOWN",
                "direction_unknown_reason": "low win rate below threshold",
                "metrics": {
                    "win_rate": 0.4,
                    "avg_win": 100.0,
                    "avg_loss": 120.0,
                    "trades": 10,
                    "trades_per_day": 0.5,
                    "drawdown_pct": 8.0,
                },
            },
        },
    }


@pytest.fixture
def broken_registry():
    """Registry with 2 broken strategies: missing metrics, missing name."""
    return {
        "version": 1,
        "strategies": {
            "broken_no_metrics": {
                "strategy_id": "broken_no_metrics",
                "ticker": "SBER",
                "strategy": "unknown_strat",
                "status": "active",
                # No metrics at all
            },
            "broken_no_name": {
                "strategy_id": "broken_no_name",
                "ticker": "",
                "strategy": "",
                "status": "active",
                "metrics": {"pnl": 100},
            },
        },
    }


# ─── Tests ────────────────────────────────────────────────────────────


class TestEnrichRegistry:
    """Tests for enrich_registry()."""

    def test_enrichment_adds_direction_to_all(self, real_registry):
        """After enrichment, every strategy has a top-level direction."""
        enriched = enrich_registry(real_registry)
        for sid, rec in enriched["strategies"].items():
            assert "direction" in rec, f"{sid} missing direction after enrichment"
            assert rec["direction"] in ("LONG", "SHORT", "UNKNOWN"), (
                f"{sid} has invalid direction: {rec['direction']}"
            )

    def test_enrichment_preserves_existing_direction(self, synthetic_registry):
        """Strategies with valid direction keep it unchanged."""
        enriched = enrich_registry(synthetic_registry)
        assert enriched["strategies"]["s1_long"]["direction"] == "LONG"
        assert enriched["strategies"]["s2_short"]["direction"] == "SHORT"

    def test_unknown_direction_has_reason(self, synthetic_registry):
        """UNKNOWN direction always has direction_unknown_reason."""
        enriched = enrich_registry(synthetic_registry)
        s3 = enriched["strategies"]["s3_unknown"]
        assert s3["direction"] == "UNKNOWN"
        assert s3.get("direction_unknown_reason", "").strip() != ""

    def test_legacy_direction_promoted_from_metrics(self):
        """Direction inside metrics is promoted to top-level."""
        reg = {
            "strategies": {
                "legacy": {
                    "strategy_id": "legacy",
                    "ticker": "LKOH",
                    "strategy": "vwap_reversion",
                    "status": "active",
                    "metrics": {"direction": "SHORT", "pnl": 100, "trades": 5},
                }
            }
        }
        enriched = enrich_registry(reg)
        assert enriched["strategies"]["legacy"]["direction"] == "SHORT"

    def test_enrichment_does_not_mutate_original(self, real_registry):
        """Original registry is not mutated by enrichment."""
        import copy
        original_strategies = copy.deepcopy(real_registry["strategies"])
        _ = enrich_registry(real_registry)
        assert real_registry["strategies"] == original_strategies

    def test_broken_registry_gets_direction(self, broken_registry):
        """Even broken strategies get direction (UNKNOWN with reason)."""
        enriched = enrich_registry(broken_registry)
        for sid, rec in enriched["strategies"].items():
            assert "direction" in rec
            assert rec["direction"] in ("LONG", "SHORT", "UNKNOWN")


class TestValidateRegistryReadiness:
    """Tests for validate_registry_readiness()."""

    def test_valid_registry_passes(self, synthetic_registry):
        """Registry with all required fields passes readiness."""
        result = validate_registry_readiness(synthetic_registry)
        assert result["passed"] is True
        assert len(result["errors"]) == 0

    def test_missing_direction_fails(self):
        """Strategy without direction fails readiness."""
        reg = {
            "strategies": {
                "no_dir": {
                    "strategy_id": "no_dir",
                    "ticker": "GAZP",
                    "strategy": "test",
                    "metrics": {
                        "win_rate": 0.5,
                        "avg_win": 100,
                        "avg_loss": 50,
                        "trades": 20,
                        "trades_per_day": 1.0,
                        "drawdown_pct": 3.0,
                    },
                }
            }
        }
        result = validate_registry_readiness(reg)
        assert result["passed"] is False
        assert any("direction" in e for e in result["errors"])

    def test_missing_metrics_produces_warnings(self):
        """Strategy with incomplete metrics generates warnings."""
        reg = {
            "strategies": {
                "partial": {
                    "strategy_id": "partial",
                    "ticker": "SBER",
                    "strategy": "test",
                    "direction": "LONG",
                    "metrics": {"pnl": 100, "trades": 10},
                }
            }
        }
        result = validate_registry_readiness(reg)
        # Should have warnings about missing metrics
        assert len(result["warnings"]) > 0
        assert any("missing required metrics" in w for w in result["warnings"])

    def test_ri_strategy_flagged(self):
        """RI strategy produces a warning about exclusion."""
        reg = {
            "strategies": {
                "ri_strat": {
                    "strategy_id": "ri_strat",
                    "ticker": "RI",
                    "strategy": "test",
                    "direction": "LONG",
                    "metrics": {
                        "win_rate": 0.5,
                        "avg_win": 100,
                        "avg_loss": 50,
                        "trades": 20,
                        "trades_per_day": 1.0,
                        "drawdown_pct": 3.0,
                    },
                }
            }
        }
        result = validate_registry_readiness(reg)
        assert any("RI" in w for w in result["warnings"])

    def test_empty_registry_fails(self):
        """Empty registry fails readiness."""
        result = validate_registry_readiness({"strategies": {}})
        assert result["passed"] is False
        assert any("no strategies" in e for e in result["errors"])

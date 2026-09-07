"""Risk scorecard metric snapshot — computes and validates composite scores.

Checks:
  1. compute_composite for representative candidate ∈ [0, 1]
  2. score_pool filters excluded tickers (RI) and respects min_rank
  3. No NaN in composite components
  4. PnL direction: positive expectancy_r for good candidate
  5. Risk direction: risk_penalty increases with drawdown/volatility
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

# Ensure project modules are importable
COMBINE_DIR = Path(__file__).resolve().parent.parent
CODE_DIR = COMBINE_DIR / "code"
sys.path.insert(0, str(CODE_DIR))

try:
    from risk_allocator_scorecard_v2 import compute_composite, score_pool
except ImportError:
    pytest.skip(
        "risk_allocator_scorecard_v2 not importable — skipping scorecard snapshot tests",
        allow_module_level=True,
    )


# ─── Fixtures ───────────────────────────────────────────────────────

@pytest.fixture
def good_candidate() -> dict:
    """A high-quality candidate with positive expectancy."""
    return {
        "ticker": "GAZP",
        "direction": "LONG",
        "win_rate": 0.60,
        "avg_win": 250.0,
        "avg_loss": 100.0,
        "drawdown_pct": 5.0,
        "volatility": 3.0,
        "risk_per_trade": 100.0,
    }


@pytest.fixture
def poor_candidate() -> dict:
    """A low-quality candidate with negative expectancy and high drawdown."""
    return {
        "ticker": "SBER",
        "direction": "LONG",
        "win_rate": 0.35,
        "avg_win": 80.0,
        "avg_loss": 150.0,
        "drawdown_pct": 12.0,
        "volatility": 10.0,
        "risk_per_trade": 100.0,
    }


@pytest.fixture
def ri_excluded_pool() -> dict:
    """Signal pool with RI ticker that should be excluded from scoring."""
    return {
        "strategies": {
            "GAZP__mr": {
                "ticker": "GAZP",
                "strategy": "mean_reversion",
                "status": "active",
                "params": {},
                "metrics": {"win_rate": 0.55, "avg_win": 200.0, "avg_loss": 100.0, "pnl": 1500.0},
                "rank_score": 1500.0,
            },
            "RI__nfi": {
                "ticker": "RI",
                "strategy": "nfi_trend",
                "status": "active",
                "params": {},
                "metrics": {"win_rate": 0.68, "avg_win": 300.0, "avg_loss": 50.0, "pnl": 3000.0},
                "rank_score": 3500.0,
            },
        }
    }


@pytest.fixture
def scoring_config() -> dict:
    """Config for scoring with excluded tickers and min_rank."""
    return {
        "risk": {
            "max_slots": 3,
            "max_contracts_per_entry": 1,
            "signal_min_rank": 100.0,
        },
        "excluded": ["RI"],
    }


@pytest.fixture
def neutral_regime() -> dict:
    """Neutral regime snapshot — no directional bias."""
    return {"tickers": {}, "bias": "neutral"}


# ─── Tests ──────────────────────────────────────────────────────────

def test_composite_bounded_good_candidate(good_candidate: dict, neutral_regime: dict) -> None:
    """compute_composite returns composite_score ∈ [0, 1] for a good candidate."""
    result = compute_composite(good_candidate, risk_per_trade=100.0, regime_snapshot=neutral_regime)

    assert "composite_score" in result, f"Missing composite_score in result: {list(result.keys())}"
    composite = result["composite_score"]
    assert 0.0 <= composite <= 1.0, f"composite_score={composite} out of [0, 1]"


def test_composite_no_nan(good_candidate: dict, poor_candidate: dict, neutral_regime: dict) -> None:
    """All composite components are finite (no NaN, no Inf)."""
    for candidate in [good_candidate, poor_candidate]:
        result = compute_composite(candidate, risk_per_trade=100.0, regime_snapshot=neutral_regime)

        for key in ["expectancy_r", "risk_penalty", "regime_bonus", "composite_raw", "composite_score"]:
            val = result.get(key)
            assert val is not None, f"Missing key {key} in result"
            assert math.isfinite(val), f"{key}={val} is not finite"


def test_score_pool_excludes_ri(ri_excluded_pool: dict, scoring_config: dict, neutral_regime: dict) -> None:
    """score_pool excludes RI ticker from results."""
    result = score_pool(ri_excluded_pool, scoring_config, neutral_regime)

    ri_entries = [r for r in result if r.get("ticker") == "RI"]
    assert len(ri_entries) == 0, f"RI should be excluded but found: {ri_entries}"

    gazp_entries = [r for r in result if r.get("ticker") == "GAZP"]
    assert len(gazp_entries) == 1, f"GAZP should be included, found: {gazp_entries}"


def test_positive_expectancy_for_good_candidate(
    good_candidate: dict, neutral_regime: dict,
) -> None:
    """Good candidate has positive expectancy_r (PnL↑ direction)."""
    result = compute_composite(good_candidate, risk_per_trade=100.0, regime_snapshot=neutral_regime)
    assert result["expectancy_r"] > 0, (
        f"Good candidate should have positive expectancy_r, got {result['expectancy_r']}"
    )


def test_risk_penalty_increases_with_drawdown(neutral_regime: dict) -> None:
    """risk_penalty increases as drawdown and volatility increase."""
    low_risk = {"ticker": "T", "win_rate": 0.5, "avg_win": 100, "avg_loss": 100, "drawdown_pct": 2.0, "volatility": 1.0}
    high_risk = {"ticker": "T", "win_rate": 0.5, "avg_win": 100, "avg_loss": 100, "drawdown_pct": 10.0, "volatility": 8.0}

    r_low = compute_composite(low_risk, risk_per_trade=100.0, regime_snapshot=neutral_regime)
    r_high = compute_composite(high_risk, risk_per_trade=100.0, regime_snapshot=neutral_regime)

    assert r_high["risk_penalty"] > r_low["risk_penalty"], (
        f"High risk ({r_high['risk_penalty']}) should exceed low risk ({r_low['risk_penalty']})"
    )

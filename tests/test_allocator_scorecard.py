"""Test Allocator Scorecard — pytest для allocator_metrics и risk_allocator_scorecard_v2.

Проверяет:
  (a) allocator_metrics.expectancy_r() и risk_penalty() на фикстурных данных
  (b) risk_allocator_scorecard_v2.compute_composite() возвращает PnL↑/risk↓ scorecard
  (c) allocator_score() <= 1.0 для seed-portfolio

Live orders запрещены. Все данные — fixtures, без внешних вызовов.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure project modules are importable
COMBINE_DIR = Path(__file__).resolve().parent.parent
CODE_DIR = COMBINE_DIR / "code"
sys.path.insert(0, str(CODE_DIR))

from allocator_metrics import (
    expectancy_r,
    risk_penalty,
    regime_bonus,
    allocator_score,
    WEIGHTS,
)
from risk_allocator_scorecard_v2 import compute_composite, score_pool

# ─── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def profitable_candidate() -> dict:
    """Candidate with positive expectancy (win_rate > 0.5, avg_win > avg_loss)."""
    return {
        "ticker": "SBER",
        "direction": "LONG",
        "win_rate": 0.6,
        "avg_win": 200.0,
        "avg_loss": 100.0,
        "drawdown_pct": 5.0,
        "volatility": 3.0,
    }


@pytest.fixture
def losing_candidate() -> dict:
    """Candidate with negative expectancy (low win_rate, high avg_loss)."""
    return {
        "ticker": "GAZP",
        "direction": "LONG",
        "win_rate": 0.35,
        "avg_win": 80.0,
        "avg_loss": 150.0,
        "drawdown_pct": 12.0,
        "volatility": 8.0,
    }


@pytest.fixture
def neutral_regime_snapshot() -> dict:
    """Regime snapshot with neutral regime for all tickers."""
    return {
        "tickers": {
            "SBER": {"adx": 20.0, "direction": "neutral", "regime": "range"},
            "GAZP": {"adx": 15.0, "direction": "neutral", "regime": "range"},
        },
        "bias": "neutral",
    }


@pytest.fixture
def trending_regime_snapshot() -> dict:
    """Regime snapshot with uptrend for SBER."""
    return {
        "tickers": {
            "SBER": {"adx": 35.0, "direction": "up", "regime": "trend"},
            "GAZP": {"adx": 10.0, "direction": "neutral", "regime": "range"},
        },
        "bias": "up",
    }


@pytest.fixture
def seed_signal_pool() -> dict:
    """Signal pool fixture with 2 active strategies for score_pool."""
    return {
        "strategies": {
            "SBER_vwap_reversion": {
                "ticker": "SBER",
                "strategy": "vwap_reversion",
                "status": "active",
                "direction": "LONG",
                "metrics": {
                    "win_rate": 0.55,
                    "avg_win": 180.0,
                    "avg_loss": 90.0,
                    "pnl": 4200.0,
                },
                "rank_score": 5000.0,
                "drawdown_pct": 4.0,
                "volatility": 3.0,
            },
            "GAZP_mean_rev": {
                "ticker": "GAZP",
                "strategy": "mean_reversion",
                "status": "active",
                "direction": "LONG",
                "metrics": {
                    "win_rate": 0.48,
                    "avg_win": 120.0,
                    "avg_loss": 100.0,
                    "pnl": 2500.0,
                },
                "rank_score": 3000.0,
                "drawdown_pct": 8.0,
                "volatility": 5.0,
            },
        }
    }


@pytest.fixture
def pool_config() -> dict:
    """Config for score_pool with excluded=["RI"] and max_slots=3."""
    return {
        "excluded": ["RI"],
        "risk": {
            "max_slots": 3,
            "signal_min_rank": 100,
        },
    }


# ─── Tests: expectancy_r ───────────────────────────────────────────────

class TestExpectancyR:
    """Test expectancy_r metric."""

    def test_profitable_positive_expectancy(self, profitable_candidate):
        """Profitable candidate must have positive expectancy_r."""
        er = expectancy_r(profitable_candidate, risk_per_trade=100.0)
        assert er > 0, f"Expected positive expectancy, got {er}"

    def test_losing_negative_expectancy(self, losing_candidate):
        """Losing candidate must have negative expectancy_r."""
        er = expectancy_r(losing_candidate, risk_per_trade=100.0)
        assert er < 0, f"Expected negative expectancy, got {er}"

    def test_zero_risk_per_trade_raw(self):
        """With risk_per_trade=0, returns raw expectancy in rubles."""
        stats = {"win_rate": 0.5, "avg_win": 200.0, "avg_loss": 100.0}
        er = expectancy_r(stats, risk_per_trade=0.0)
        # raw = 0.5*200 - 0.5*100 = 50.0
        assert abs(er - 50.0) < 1e-6

    def test_unit_risk_normalization(self):
        """With risk_per_trade=1, E[R] equals raw expectancy."""
        stats = {"win_rate": 0.6, "avg_win": 200.0, "avg_loss": 100.0}
        er = expectancy_r(stats, risk_per_trade=1.0)
        # raw = 0.6*200 - 0.4*100 = 80.0
        assert abs(er - 80.0) < 1e-6


# ─── Tests: risk_penalty ───────────────────────────────────────────────

class TestRiskPenalty:
    """Test risk_penalty metric."""

    def test_low_drawdown_low_penalty(self):
        """Low drawdown → low risk penalty."""
        rp = risk_penalty({"drawdown_pct": 2.0, "volatility": 2.0})
        assert 0.0 <= rp < 0.5

    def test_high_drawdown_high_penalty(self):
        """High drawdown → high risk penalty."""
        rp = risk_penalty({"drawdown_pct": 14.0, "volatility": 12.0})
        assert rp > 0.5

    def test_empty_slot_default_penalty(self):
        """Empty slot → conservative default 0.3."""
        rp = risk_penalty({})
        assert abs(rp - 0.3) < 1e-6

    def test_penalty_range(self):
        """Penalty must be in [0, 1]."""
        rp = risk_penalty({"drawdown_pct": 7.5, "volatility": 5.0})
        assert 0.0 <= rp <= 1.0


# ─── Tests: regime_bonus ───────────────────────────────────────────────

class TestRegimeBonus:
    """Test regime_bonus metric."""

    def test_range_regime_neutral(self, neutral_regime_snapshot):
        """Range regime → bonus = 0.0 (neutral)."""
        bonus = regime_bonus("SBER", "LONG", neutral_regime_snapshot)
        assert bonus == 0.0

    def test_trend_aligned_positive(self, trending_regime_snapshot):
        """Trend up + LONG → positive bonus."""
        bonus = regime_bonus("SBER", "LONG", trending_regime_snapshot)
        assert bonus > 0.0

    def test_trend_counter_negative(self, trending_regime_snapshot):
        """Trend up + SHORT → negative bonus (against trend)."""
        bonus = regime_bonus("SBER", "SHORT", trending_regime_snapshot)
        assert bonus < 0.0

    def test_no_direction_zero(self, neutral_regime_snapshot):
        """No direction → bonus = 0.0."""
        bonus = regime_bonus("SBER", None, neutral_regime_snapshot)
        assert bonus == 0.0


# ─── Tests: compute_composite (risk_allocator_scorecard_v2) ────────────

class TestComputeComposite:
    """Test risk_allocator_scorecard_v2.compute_composite."""

    def test_composite_score_range(self, profitable_candidate, neutral_regime_snapshot):
        """composite_score must be in [0, 1]."""
        result = compute_composite(profitable_candidate, regime_snapshot=neutral_regime_snapshot)
        score = result["composite_score"]
        assert 0.0 <= score <= 1.0, f"Score {score} outside [0,1]"

    def test_composite_has_all_fields(self, profitable_candidate, neutral_regime_snapshot):
        """Result must have all required fields."""
        result = compute_composite(profitable_candidate, regime_snapshot=neutral_regime_snapshot)
        required = {"ticker", "expectancy_r", "risk_penalty", "regime_bonus", "composite_score", "composite_raw"}
        assert required.issubset(set(result.keys())), f"Missing fields: {required - set(result.keys())}"

    def test_profitable_beats_losing(
        self, profitable_candidate, losing_candidate, neutral_regime_snapshot
    ):
        """Profitable candidate must score higher than losing one."""
        r_profit = compute_composite(profitable_candidate, regime_snapshot=neutral_regime_snapshot)
        r_losing = compute_composite(losing_candidate, regime_snapshot=neutral_regime_snapshot)
        assert r_profit["composite_score"] > r_losing["composite_score"], (
            f"Profitable {r_profit['composite_score']} <= Losing {r_losing['composite_score']}"
        )

    def test_regime_trend_boosts_score(
        self, profitable_candidate, neutral_regime_snapshot, trending_regime_snapshot
    ):
        """Trend regime aligned with direction should boost score vs neutral."""
        r_neutral = compute_composite(profitable_candidate, regime_snapshot=neutral_regime_snapshot)
        r_trend = compute_composite(profitable_candidate, regime_snapshot=trending_regime_snapshot)
        assert r_trend["composite_score"] >= r_neutral["composite_score"]


# ─── Tests: score_pool ─────────────────────────────────────────────────

class TestScorePool:
    """Test score_pool ranking."""

    def test_score_pool_returns_list(self, seed_signal_pool, pool_config, neutral_regime_snapshot):
        """score_pool must return a list."""
        result = score_pool(seed_signal_pool, pool_config, neutral_regime_snapshot)
        assert isinstance(result, list)

    def test_score_pool_excludes_ri(self, pool_config, neutral_regime_snapshot):
        """RI must be excluded from pool scoring."""
        pool_with_ri = {
            "strategies": {
                "RI_nfi_trend": {
                    "ticker": "RI",
                    "strategy": "nfi_trend",
                    "status": "active",
                    "direction": "LONG",
                    "metrics": {"win_rate": 0.6, "avg_win": 200, "avg_loss": 100},
                    "rank_score": 5000,
                },
                "SBER_vwap": {
                    "ticker": "SBER",
                    "strategy": "vwap",
                    "status": "active",
                    "direction": "LONG",
                    "metrics": {"win_rate": 0.5, "avg_win": 150, "avg_loss": 100},
                    "rank_score": 3000,
                },
            }
        }
        result = score_pool(pool_with_ri, pool_config, neutral_regime_snapshot)
        tickers = [r["ticker"] for r in result]
        assert "RI" not in tickers, "RI must not appear in scored pool"

    def test_score_pool_sorted_desc(
        self, seed_signal_pool, pool_config, neutral_regime_snapshot
    ):
        """Pool results must be sorted by composite_score descending."""
        result = score_pool(seed_signal_pool, pool_config, neutral_regime_snapshot)
        scores = [r["composite_score"] for r in result]
        assert scores == sorted(scores, reverse=True), "Not sorted DESC"

    def test_score_pool_respects_max_slots(
        self, seed_signal_pool, pool_config, neutral_regime_snapshot
    ):
        """Pool result length must not exceed max_slots."""
        result = score_pool(seed_signal_pool, pool_config, neutral_regime_snapshot)
        max_slots = pool_config["risk"]["max_slots"]
        assert len(result) <= max_slots, f"Got {len(result)} > max_slots={max_slots}"


# ─── Tests: allocator_score bounds ─────────────────────────────────────

class TestAllocatorScoreBounds:
    """Verify allocator_score stays in reasonable range."""

    def test_allocator_score_for_profitable(self, profitable_candidate, neutral_regime_snapshot):
        """allocator_score for profitable candidate must be <= 1.0."""
        score = allocator_score(profitable_candidate, neutral_regime_snapshot)
        assert score <= 1.0, f"allocator_score={score} > 1.0"

    def test_allocator_score_for_losing(self, losing_candidate, neutral_regime_snapshot):
        """allocator_score for losing candidate must be <= 1.0."""
        score = allocator_score(losing_candidate, neutral_regime_snapshot)
        assert score <= 1.0, f"allocator_score={score} > 1.0"

    def test_allocator_score_nonnegative(self, profitable_candidate, neutral_regime_snapshot):
        """allocator_score must be >= 0."""
        score = allocator_score(profitable_candidate, neutral_regime_snapshot)
        assert score >= 0.0, f"allocator_score={score} < 0.0"

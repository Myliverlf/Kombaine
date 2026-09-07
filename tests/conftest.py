"""Shared pytest fixtures for strategy_combine dry-run tests.

Provides deterministic dry-run bundles for pipeline, matrix, and signal pool tests.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

COMBINE_DIR = Path(__file__).resolve().parent.parent
CODE_DIR = COMBINE_DIR / "code"
CORE_DIR = COMBINE_DIR / "core"
TOOLS_DIR = COMBINE_DIR / "tools"
sys.path.insert(0, str(CODE_DIR))
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(TOOLS_DIR))


@pytest.fixture
def engine_config_fixture() -> dict:
    return {
        "mode": "paper",
        "paper_first": True,
        "deposit_rub": 21281,
        "risk": {
            "risk_per_trade_pct": 2.7,
            "go_budget_pct": 50,
            "max_slots": 3,
            "max_contracts_per_entry": 1,
            "portfolio_stop_drawdown_pct": 25,
            "delta_band_pct": 30,
            "min_reserve_pct": 30,
            "signal_pool_max": 10,
            "signal_rotation_days": 3,
            "signal_min_rank": 100,
            "signal_max_age_minutes": 16,
        },
        "universe": ["BR", "GAZP", "LKOH", "SBER", "Si"],
        "excluded": ["RI"],
        "risk_scorecard_weights": {
            "exposure": 20,
            "drawdown": 20,
            "volatility": 15,
            "correlation": 10,
            "signal_age": 15,
            "slots": 10,
            "caps": 10,
        },
    }


@pytest.fixture
def portfolio_fixture() -> dict:
    return {"slots": {}, "peak_equity": 0.0, "halted": False, "halt_reason": None}


@pytest.fixture
def signal_pool_fixture() -> dict:
    return {
        "strategies": {
            "LKOH_vwap": {
                "ticker": "LKOH",
                "strategy": "vwap_reversion",
                "params": {"lookback": 30},
                "metrics": {"pnl": 4859.0, "sharpe": 0.49, "win_rate": 51.3, "pf": 1.29, "trades": 30, "trades_per_day": 7.72},
                "rank_score": 5120.0,
                "price": 18000.0,
            },
            "GAZP_mean": {
                "ticker": "GAZP",
                "strategy": "mean_reversion_filtered",
                "params": {"lookback": 20},
                "metrics": {"pnl": 3866.0, "sharpe": 0.47, "win_rate": 46.5, "pf": 1.32, "trades": 30, "trades_per_day": 5.91},
                "rank_score": 4010.0,
                "price": 180.0,
            },
            "RI_nfi": {
                "ticker": "RI",
                "strategy": "nfi_trend",
                "params": {},
                "metrics": {"pnl": -2972.0, "sharpe": 0.68, "win_rate": 61.1, "pf": 2.84, "trades": 30, "trades_per_day": 1.45},
                "rank_score": 3500.0,
                "price": 75000.0,
            },
        },
        "last_rotation_ts": 0.0,
    }


@pytest.fixture
def proven_candidates_fixture() -> list[dict]:
    return [
        {
            "ticker": "SBER",
            "strategy": "trend_breakout",
            "score": 0.75,
            "quality_gate_passed": True,
            "consistency_score": 0.28,
            "n_trades": 64,
            "evidence_count": 4,
            "strategy_independent": True,
            "contracts_requested": 1,
            "contracts": 1,
            "expected_pnl": 160.0,
            "risk": 20.0,
            "volatility": 0.1,
        },
        {
            "ticker": "GAZP",
            "strategy": "mean_reversion",
            "score": 0.68,
            "quality_gate_passed": False,
            "consistency_score": 0.24,
            "n_trades": 58,
            "evidence_count": 3,
            "strategy_independent": True,
            "contracts_requested": 1,
            "contracts": 1,
            "expected_pnl": 110.0,
            "risk": 18.0,
            "volatility": 0.1,
        },
        {
            "ticker": "RI",
            "strategy": "trend_breakout",
            "score": 0.20,
            "quality_gate_passed": True,
            "consistency_score": 0.1,
            "n_trades": 40,
            "evidence_count": 2,
            "strategy_independent": True,
            "contracts_requested": 1,
            "contracts": 1,
            "expected_pnl": -50.0,
            "risk": 200.0,
            "volatility": 0.1,
        },
    ]


@pytest.fixture
def matrix_candidates_fixture() -> list[dict]:
    return [
        {
            "ticker": "SBER",
            "strategy": "trend_breakout",
            "score": 0.75,
            "quality_gate_passed": True,
            "consistency_score": 0.28,
            "n_trades": 64,
            "evidence_count": 4,
            "strategy_independent": True,
            "contracts_requested": 1,
        },
        {
            "ticker": "GAZP",
            "strategy": "mean_reversion",
            "score": 0.68,
            "quality_gate_passed": False,
            "consistency_score": 0.24,
            "n_trades": 58,
            "evidence_count": 3,
            "strategy_independent": True,
            "contracts_requested": 1,
        },
        {
            "ticker": "RI",
            "strategy": "trend_breakout",
            "score": 0.20,
            "quality_gate_passed": True,
            "consistency_score": 0.1,
            "n_trades": 40,
            "evidence_count": 2,
            "strategy_independent": True,
            "contracts_requested": 1,
        },
    ]


@pytest.fixture
def strategy_with_direction_fixture() -> dict:
    return {
        "ticker": "GAZP",
        "direction": "LONG",
        "win_rate": 0.55,
        "avg_win": 200.0,
        "avg_loss": 100.0,
        "drawdown_pct": 6.0,
        "volatility": 4.0,
        "trades": 50,
        "trades_per_day": 2.5,
        "pnl": 5000.0,
        "sharpe": 0.8,
        "pf": 1.9,
        "risk_per_trade": 100.0,
    }


@pytest.fixture
def directionless_candidates_fixture() -> list[dict]:
    return [
        {
            "strategy_id": "batch_1",
            "ticker": "GAZP",
            "strategy": "trend_follow",
            "metrics": {"pnl": 1200, "win_rate": 0.6, "avg_win": 150, "avg_loss": 80, "trades": 40, "trades_per_day": 2.0, "drawdown_pct": 5.0},
        },
        {
            "strategy_id": "batch_2",
            "ticker": "LKOH",
            "strategy": "mean_reversion_vwap",
            "metrics": {"pnl": 800, "win_rate": 0.5, "avg_win": 120, "avg_loss": 90, "trades": 30, "trades_per_day": 1.5, "drawdown_pct": 8.0},
        },
        {
            "strategy_id": "batch_3",
            "ticker": "SBER",
            "strategy": "momentum_breakout",
            "metrics": {"pnl": 200, "win_rate": 0.45, "avg_win": 100, "avg_loss": 60, "trades": 20, "trades_per_day": 1.0, "drawdown_pct": 12.0},
        },
    ]

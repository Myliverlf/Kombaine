"""Deterministic dry-run fixtures for the strategy_combine artifact.

These helpers support at least 3 tests and remain free of broker/live-order side effects.
"""
from __future__ import annotations

from typing import Any, Dict, List


def make_proven_candidate_fixtures() -> List[Dict[str, Any]]:
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
            "quality_gate_passed": True,
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
            "score": 0.99,
            "quality_gate_passed": True,
            "consistency_score": 0.5,
            "n_trades": 120,
            "evidence_count": 5,
            "strategy_independent": True,
            "contracts_requested": 1,
            "contracts": 1,
            "expected_pnl": 500.0,
            "risk": 2.0,
            "volatility": 0.1,
        },
    ]


def make_matrix_fixture_candidates() -> List[Dict[str, Any]]:
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
            "quality_gate_passed": True,
            "consistency_score": 0.24,
            "n_trades": 58,
            "evidence_count": 3,
            "strategy_independent": True,
            "contracts_requested": 1,
        },
        {
            "ticker": "RI",
            "strategy": "trend_breakout",
            "score": 0.99,
            "quality_gate_passed": True,
            "consistency_score": 0.5,
            "n_trades": 120,
            "evidence_count": 5,
            "strategy_independent": True,
            "contracts_requested": 1,
        },
    ]


def make_pipeline_fixture() -> Dict[str, Any]:
    return {
        "stage_order": ["analytics", "list", "pool", "risk", "live"],
        "safe_mode": "dry-run",
        "live_orders_allowed": False,
        "limits": {"max_live_slots": 3, "max_contracts_per_entry": 1},
        "excluded": ["RI"],
    }


__all__ = [
    "make_proven_candidate_fixtures",
    "make_matrix_fixture_candidates",
    "make_pipeline_fixture",
]

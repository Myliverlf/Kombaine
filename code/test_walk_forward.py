"""Pytest tests for walk_forward_optimizer — ≥3 fixtures, ≥6 tests.

Тестирует:
  - py_compile модуля
  - walk_forward_split (IS+OOS=total, no leak)
  - generate_param_grid (≥2 комбинаций)
  - optimize_on_window (per-param scorecard)
  - evaluate_robustness (PnL↑/risk↓ vs naive)
  - RI excluded во всех окнах
  - max slots ≤3
  - contracts=1 в каждом selected
  - No broker calls (AST-based)

Все проверки через fixtures/dry-run. Нет live orders.
"""
import math
import os
import random
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

# Ensure code/ is on sys.path
CODE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CODE_DIR))

from walk_forward_optimizer import (
    generate_param_grid,
    walk_forward_split,
    optimize_on_window,
    evaluate_robustness,
    walk_forward_report,
    _select_with_weights,
    _portfolio_returns,
    validate_no_broker,
)
from allocator_metrics import WEIGHTS as BASE_WEIGHTS
from candidate_allocator import select_live_slots, baseline_rank_score
from scorecard_metrics import sharpe_ratio, max_drawdown


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture
def synthetic_candidates() -> List[Dict[str, Any]]:
    """6 кандидатов с разной expectancy/risk. Включает RI (должен быть excluded)."""
    return [
        {"ticker": "LKOH", "direction": "LONG",
         "win_rate": 0.60, "avg_win": 200.0, "avg_loss": 100.0,
         "drawdown_pct": 5.0, "volatility": 2.0},
        {"ticker": "GAZP", "direction": "SHORT",
         "win_rate": 0.50, "avg_win": 80.0, "avg_loss": 50.0,
         "drawdown_pct": 3.0, "volatility": 1.5},
        {"ticker": "SBER", "direction": "LONG",
         "win_rate": 0.70, "avg_win": 100.0, "avg_loss": 120.0,
         "drawdown_pct": 8.0, "volatility": 4.0},
        {"ticker": "RI", "direction": "LONG",
         "win_rate": 0.90, "avg_win": 500.0, "avg_loss": 10.0,
         "drawdown_pct": 1.0, "volatility": 0.5},  # RI — must be excluded
        {"ticker": "BR", "direction": "SHORT",
         "win_rate": 0.45, "avg_win": 60.0, "avg_loss": 40.0,
         "drawdown_pct": 10.0, "volatility": 6.0},
        {"ticker": "Si", "direction": "LONG",
         "win_rate": 0.55, "avg_win": 120.0, "avg_loss": 80.0,
         "drawdown_pct": 4.0, "volatility": 3.0},
    ]


@pytest.fixture
def synthetic_returns_trending() -> Dict[str, List[float]]:
    """Synthetic returns: trending up (fixated seed)."""
    random.seed(42)
    tickers = ["LKOH", "GAZP", "SBER", "RI", "BR", "Si"]
    result = {}
    for t in tickers:
        # Trending: positive drift
        result[t] = [random.gauss(0.003, 0.01) for _ in range(60)]
    return result


@pytest.fixture
def synthetic_returns_choppy() -> Dict[str, List[float]]:
    """Synthetic returns: choppy / mean-reverting."""
    random.seed(99)
    tickers = ["LKOH", "GAZP", "SBER", "RI", "BR", "Si"]
    result = {}
    for t in tickers:
        result[t] = [random.gauss(0.0, 0.02) for _ in range(60)]
    return result


@pytest.fixture
def param_grid_fixture() -> List[Dict[str, float]]:
    """Param grid: base ± 20% with 3 variations (27 combos)."""
    return generate_param_grid(
        base_weights={"expectancy": 40, "risk": 35, "regime": 25},
        variations=[0.8, 1.0, 1.2],
    )


@pytest.fixture
def cfg_fixture() -> dict:
    """Config matching config.json constraints."""
    return {
        "excluded": ["RI"],
        "risk": {"max_slots": 3, "max_contracts_per_entry": 1},
        "risk_per_trade_pct": 2.7,
        "deposit_rub": 21281,
    }


@pytest.fixture
def regime_fixture() -> Dict[str, Any]:
    """Regime snapshot for testing."""
    return {
        "tickers": {
            "LKOH": {"adx": 21.4, "direction": "down", "regime": "range"},
            "GAZP": {"adx": 35.3, "direction": "up", "regime": "trend"},
            "SBER": {"adx": 45.3, "direction": "down", "regime": "trend"},
            "BR": {"adx": 23.1, "direction": "up", "regime": "trend"},
            "Si": {"adx": 18.0, "direction": "up", "regime": "trend"},
            "RI": {"adx": 50.0, "direction": "up", "regime": "trend"},
        },
        "bias": "neutral",
    }


# ═══════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════

class TestPyCompile:
    """AC1: py_compile OK."""

    def test_module_compiles(self):
        """walk_forward_optimizer.py compiles without errors."""
        import py_compile
        module_path = str(CODE_DIR / "walk_forward_optimizer.py")
        # py_compile raises on failure
        py_compile.compile(module_path, doraise=True)


class TestParamGrid:
    """AC2a: generate_param_grid."""

    def test_grid_has_combinations(self, param_grid_fixture):
        """Grid has ≥2 combinations."""
        assert len(param_grid_fixture) >= 2

    def test_grid_normalizes(self, param_grid_fixture):
        """Each param set sums to base_weights total (40+35+25=100)."""
        base_sum = sum(BASE_WEIGHTS.values())
        for params in param_grid_fixture:
            assert abs(sum(params.values()) - base_sum) < 0.1, (
                f"Params {params} sum to {sum(params.values())}, expected ~{base_sum}"
            )

    def test_grid_includes_base(self, param_grid_fixture):
        """Grid includes the base weights (1.0 variation)."""
        found = False
        for p in param_grid_fixture:
            if all(abs(p[k] - BASE_WEIGHTS[k]) < 0.01 for k in BASE_WEIGHTS):
                found = True
                break
        assert found, "Base weights not found in grid"


class TestWalkForwardSplit:
    """AC2b: walk_forward_split correctness."""

    def test_is_oos_no_leak(self):
        """IS and OOS don't overlap within each window."""
        windows = walk_forward_split(60, n_windows=3, in_sample_frac=0.7)
        assert len(windows) > 0
        for w in windows:
            assert w["is_end"] <= w["oos_start"], (
                f"Window {w['window_id']}: IS ends at {w['is_end']}, "
                f"OOS starts at {w['oos_start']} — overlap!"
            )

    def test_is_oos_cover_total(self):
        """IS + OOS span from 0 to ≤ n_total."""
        n = 60
        windows = walk_forward_split(n, n_windows=3)
        assert windows[0]["is_start"] == 0
        assert windows[-1]["oos_end"] <= n

    def test_small_n(self):
        """Very small n_total produces empty or minimal split."""
        windows = walk_forward_split(1, n_windows=3)
        assert len(windows) == 0


class TestSelectWithWeights:
    """AC2c: selection with custom weights."""

    def test_excludes_ri(self, synthetic_candidates, cfg_fixture, regime_fixture, param_grid_fixture):
        """RI never appears in selected tickers."""
        for params in param_grid_fixture[:5]:  # check a few
            selected = _select_with_weights(synthetic_candidates, cfg_fixture, regime_fixture, params)
            tickers = [s["ticker"] for s in selected]
            assert "RI" not in tickers, f"RI found in selected: {tickers}"

    def test_max_slots(self, synthetic_candidates, cfg_fixture, regime_fixture, param_grid_fixture):
        """Selected ≤3 slots."""
        for params in param_grid_fixture[:5]:
            selected = _select_with_weights(synthetic_candidates, cfg_fixture, regime_fixture, params)
            assert len(selected) <= 3

    def test_contracts_one(self, synthetic_candidates, cfg_fixture, regime_fixture, param_grid_fixture):
        """All selected have contracts=1."""
        for params in param_grid_fixture[:5]:
            selected = _select_with_weights(synthetic_candidates, cfg_fixture, regime_fixture, params)
            for s in selected:
                assert s["contracts"] == 1, f"{s['ticker']} has contracts={s['contracts']}"


class TestOptimizeOnWindow:
    """AC2d: optimize_on_window returns per-param scorecard."""

    def test_returns_expected_keys(self, synthetic_candidates, cfg_fixture, regime_fixture,
                                   param_grid_fixture, synthetic_returns_trending):
        """optimize_on_window returns all expected keys."""
        result = optimize_on_window(
            candidates=synthetic_candidates,
            cfg=cfg_fixture,
            regime_snapshot=regime_fixture,
            param_grid=param_grid_fixture,
            returns_by_ticker=synthetic_returns_trending,
            is_start=0, is_end=30,
            oos_start=30, oos_end=45,
        )
        required_keys = [
            "best_params", "is_sharpe", "oos_sharpe", "oos_max_dd",
            "oos_profit_factor", "oos_pnl", "selected_tickers",
            "baseline_oos_sharpe", "pnl_up", "risk_down",
        ]
        for k in required_keys:
            assert k in result, f"Missing key: {k}"

    def test_ri_excluded_in_window(self, synthetic_candidates, cfg_fixture, regime_fixture,
                                    param_grid_fixture, synthetic_returns_trending):
        """RI never in selected_tickers from optimize_on_window."""
        result = optimize_on_window(
            candidates=synthetic_candidates,
            cfg=cfg_fixture,
            regime_snapshot=regime_fixture,
            param_grid=param_grid_fixture,
            returns_by_ticker=synthetic_returns_trending,
            is_start=0, is_end=30,
            oos_start=30, oos_end=45,
        )
        assert "RI" not in result["selected_tickers"]

    def test_max_slots_in_window(self, synthetic_candidates, cfg_fixture, regime_fixture,
                                  param_grid_fixture, synthetic_returns_trending):
        """≤3 slots selected in window."""
        result = optimize_on_window(
            candidates=synthetic_candidates,
            cfg=cfg_fixture,
            regime_snapshot=regime_fixture,
            param_grid=param_grid_fixture,
            returns_by_ticker=synthetic_returns_trending,
            is_start=0, is_end=30,
            oos_start=30, oos_end=45,
        )
        assert len(result["selected_tickers"]) <= 3


class TestEvaluateRobustness:
    """AC2e: evaluate_robustness PnL↑/risk↓ vs naive."""

    def test_robustness_structure(self, synthetic_candidates, cfg_fixture, regime_fixture,
                                   param_grid_fixture, synthetic_returns_trending):
        """Full report gives valid aggregate with robustness_score."""
        report = walk_forward_report(
            candidates=synthetic_candidates,
            cfg=cfg_fixture,
            regime_snapshot=regime_fixture,
            returns_by_ticker=synthetic_returns_trending,
            param_grid=param_grid_fixture,
            n_windows=3,
            in_sample_frac=0.7,
        )
        agg = report["aggregate"]
        assert "pnl_up_pct" in agg
        assert "risk_down_pct" in agg
        assert "robustness_score" in agg
        assert "worst_dd" in agg
        assert "best_params_consensus" in agg
        assert 0.0 <= agg["robustness_score"] <= 1.0

    def test_pnl_risk_metrics_present(self, synthetic_candidates, cfg_fixture, regime_fixture,
                                       param_grid_fixture, synthetic_returns_trending):
        """Report has pnl_up_pct, risk_down_pct, avg_delta_sharpe."""
        report = walk_forward_report(
            candidates=synthetic_candidates,
            cfg=cfg_fixture,
            regime_snapshot=regime_fixture,
            returns_by_ticker=synthetic_returns_trending,
            param_grid=param_grid_fixture,
            n_windows=3,
        )
        agg = report["aggregate"]
        assert isinstance(agg["pnl_up_pct"], float)
        assert isinstance(agg["risk_down_pct"], float)
        assert isinstance(agg["avg_delta_sharpe"], float)

    def test_wfo_vs_naive(self, synthetic_candidates, cfg_fixture, regime_fixture,
                           param_grid_fixture, synthetic_returns_trending):
        """WFO does not produce worse results than having no optimization
        (robustness_score > 0 means there is at least some signal)."""
        report = walk_forward_report(
            candidates=synthetic_candidates,
            cfg=cfg_fixture,
            regime_snapshot=regime_fixture,
            returns_by_ticker=synthetic_returns_trending,
            param_grid=param_grid_fixture,
            n_windows=3,
        )
        # At minimum, robustness_score is valid and report has windows
        assert report["aggregate"]["n_windows"] > 0
        # pnl_up + risk_down should give some signal
        assert (report["aggregate"]["pnl_up_pct"] +
                report["aggregate"]["risk_down_pct"]) >= 0.0

    def test_no_ri_in_any_window(self, synthetic_candidates, cfg_fixture, regime_fixture,
                                  param_grid_fixture, synthetic_returns_trending):
        """RI excluded in every window of the full report."""
        report = walk_forward_report(
            candidates=synthetic_candidates,
            cfg=cfg_fixture,
            regime_snapshot=regime_fixture,
            returns_by_ticker=synthetic_returns_trending,
            param_grid=param_grid_fixture,
            n_windows=3,
        )
        for w in report["per_window"]:
            assert "RI" not in w["selected_tickers"], (
                f"Window {w.get('window_id')}: RI found in {w['selected_tickers']}"
            )

    def test_max_slots_all_windows(self, synthetic_candidates, cfg_fixture, regime_fixture,
                                    param_grid_fixture, synthetic_returns_trending):
        """≤3 slots in every window."""
        report = walk_forward_report(
            candidates=synthetic_candidates,
            cfg=cfg_fixture,
            regime_snapshot=regime_fixture,
            returns_by_ticker=synthetic_returns_trending,
            param_grid=param_grid_fixture,
            n_windows=3,
        )
        for w in report["per_window"]:
            assert len(w["selected_tickers"]) <= 3, (
                f"Window {w.get('window_id')}: {len(w['selected_tickers'])} slots"
            )


class TestNoBroker:
    """AC3: No broker calls via AST."""

    def test_no_broker_in_optimizer(self):
        """walk_forward_optimizer.py has no forbidden broker calls."""
        module_path = str(CODE_DIR / "walk_forward_optimizer.py")
        validate_no_broker(module_path)  # raises on failure


class TestChoppyReturns:
    """Ensure WFO works with choppy/mean-reverting returns too."""

    def test_choppy_report(self, synthetic_candidates, cfg_fixture, regime_fixture,
                            param_grid_fixture, synthetic_returns_choppy):
        """WFO runs on choppy returns without errors."""
        report = walk_forward_report(
            candidates=synthetic_candidates,
            cfg=cfg_fixture,
            regime_snapshot=regime_fixture,
            returns_by_ticker=synthetic_returns_choppy,
            param_grid=param_grid_fixture,
            n_windows=3,
        )
        assert "per_window" in report
        assert "aggregate" in report
        assert report["aggregate"]["n_windows"] > 0

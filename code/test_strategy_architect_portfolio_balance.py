#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from strategy_architect_autopilot import portfolio_balanced_top


def row(ticker: str, strategy: str, rank: float, trades: int = 50, r2: float = 0.8):
    return {
        "ticker": ticker,
        "timeframe": "1h",
        "strategy": strategy,
        "params": {"p": rank},
        "decision": "keep",
        "equity_shape_passed": True,
        "equity_shape_r2": r2,
        "equity_shape_positive_window_ratio": 0.8,
        "equity_shape_flat_window_ratio": 0.0,
        "equity_shape_dd_ratio": 0.3,
        "trades": trades,
        "smooth_ok": True,
        "smooth_live_ready": True,
        "smooth_score": 500.0,
        "recovery_factor": 3.0,
        "monotonicity_score": 85.0,
        "ulcer_index": 2.0,
        "drawdown_cluster_count": 1,
        "economic_ok": True,
        "economic_score": 20.0,
        "rank_score": rank,
    }


def test_portfolio_balanced_top_caps_ticker_and_strategy_without_fill_relax():
    rows = []
    # LKOH has the highest raw ranks, but should not dominate.
    for i in range(10):
        rows.append(row("LKOH", "vwap_reversion", 10000 - i * 100, trades=300))
    for i, ticker in enumerate(["SBER", "GAZP", "BR", "Si", "CNY"]):
        rows.append(row(ticker, f"strategy_{i}", 7000 - i * 100, trades=60))

    top = portfolio_balanced_top(rows, top_n=20, max_per_ticker=2, max_per_strategy=2, min_tickers=5)

    tickers = [r["ticker"] for r in top]
    strategies = [r["strategy"] for r in top]
    assert tickers.count("LKOH") <= 2
    assert strategies.count("vwap_reversion") <= 2
    assert len(set(tickers)) >= 5
    assert all(r["top_selection_reason"].startswith("portfolio_balanced:") for r in top)
    assert all("cap_relaxed_fill" not in r["top_selection_reason"] for r in top)

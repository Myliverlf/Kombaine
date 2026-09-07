#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from coverage_repair_generator import missing_repair_tickers, repair_families_for_ticker, render_markdown


def test_missing_repair_tickers_selects_failed_not_selected_only():
    cycle = {
        "coverage_reasons": [
            {"ticker": "BR", "in_top": False, "reason": "no_keep_candidates:shape_fail flat=1.00"},
            {"ticker": "SBER", "in_top": True, "reason": "selected"},
        ]
    }
    rows = missing_repair_tickers(cycle)
    assert [r["ticker"] for r in rows] == ["BR"]


def test_repair_families_are_ticker_specific_and_non_empty():
    fx = repair_families_for_ticker("USDRUB", "shape_fail")
    commodity = repair_families_for_ticker("BR", "shape_fail")
    assert fx
    assert commodity
    assert "atr_breakout" in fx
    assert "volatility_squeeze" in commodity


def test_render_markdown_declares_no_live_orders():
    md = render_markdown({
        "source_cycle": "cycle.json",
        "tickers_repaired": 1,
        "rows_tested": 2,
        "repair_keep_total": 0,
        "near_miss_total": 1,
        "live_orders": 0,
        "repairs": [{
            "ticker": "BR", "tested": 2, "keep_count": 0, "near_miss_count": 1,
            "action": "expand_param_cloud_around_near_miss",
            "best": {"strategy": "atr_breakout", "timeframe": "1h", "trades": 10, "total_pnl": 1.0, "profit_factor": 1.2, "archetype_match_score": 50, "equity_shape_flat_window_ratio": 1, "equity_shape_reason": "shape_fail"},
            "top_candidates": [],
        }],
    })
    assert "live_orders=0" in md
    assert "BR" in md

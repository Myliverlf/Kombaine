#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from portfolio_equity_analyzer import equity_profile, sum_curves, contribution_report, live_feedback


def test_equity_profile_rejects_tail_stagnation():
    vals = [float(i) for i in range(80)] + [79.0 for _ in range(20)]
    p = equity_profile(vals)
    assert p["ok"] is False
    assert p["micro_tail_pnl"] == 0.0
    assert "portfolio_tail_fail" in p["reason"]


def test_equity_profile_accepts_smooth_rising_portfolio():
    vals = [float(i) for i in range(120)]
    p = equity_profile(vals)
    assert p["ok"] is True
    assert p["tail_pnl"] > 0
    assert p["flat_window_ratio"] == 0.0


def test_contribution_marks_tail_dragger():
    good = [float(i) for i in range(100)]
    bad = [50.0 - i * 0.2 for i in range(100)]
    portfolio = sum_curves([good, bad])
    rows = [
        {"ticker": "GOOD", "timeframe": "1h", "strategy": "trend", "trades": 10},
        {"ticker": "BAD", "timeframe": "1h", "strategy": "fade", "trades": 10},
    ]
    c = contribution_report([good, bad], rows, portfolio)
    bad_row = [x for x in c if x["ticker"] == "BAD"][0]
    assert bad_row["role"] == "dead_weight"


def test_live_feedback_missing_db_is_safe(tmp_path):
    fb = live_feedback([{"ticker": "SBER", "strategy": "x"}], db_path=tmp_path / "missing.db")
    assert fb["available"] is False

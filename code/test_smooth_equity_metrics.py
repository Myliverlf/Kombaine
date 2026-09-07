#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from smooth_equity_metrics import smooth_equity_metrics, economic_value


def test_smooth_line_passes():
    vals = [float(i) for i in range(200)]
    m = smooth_equity_metrics(vals)
    assert m['smooth_ok'] is True
    assert m['ulcer_index'] == 0.0
    assert m['max_time_under_water'] == 0
    assert m['monotonicity_score'] >= 99


def test_zigzag_drawdown_fails():
    vals = []
    v = 0.0
    for i in range(80):
        v += 10 if i % 2 == 0 else -8
        vals.append(v)
    m = smooth_equity_metrics(vals)
    assert m['smooth_ok'] is False
    assert m['drawdown_cluster_count'] >= 1 or m['ulcer_index'] > 0


def test_micro_tail_down_fails():
    vals = [float(i) for i in range(100)] + [100.0 - i for i in range(10)]
    m = smooth_equity_metrics(vals)
    assert m['smooth_ok'] is False
    assert m['micro_tail_pnl'] < 0


def test_economic_value_rejects_micro_edge():
    e = economic_value({'total_pnl': 0.2, 'avg_trade': 0.01, 'trade_count': 50, 'profit_factor': 1.4})
    assert e['economic_ok'] is False


def test_economic_value_accepts_real_edge():
    e = economic_value({'total_pnl': 20.0, 'avg_trade': 0.5, 'trade_count': 50, 'profit_factor': 1.4})
    assert e['economic_ok'] is True

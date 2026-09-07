#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from equity_shape_filter import equity_shape_metrics


def test_small_pnl_smooth_curve_not_marked_flat_by_absolute_rub_floor():
    initial = 1_000_000.0
    # Small absolute move (+6 rub/points total), but every window rises smoothly.
    equity = pd.Series([initial + i * 0.1 for i in range(61)])
    m = equity_shape_metrics(equity, initial)
    assert m["equity_shape_positive_window_ratio"] == 1.0
    assert m["equity_shape_flat_window_ratio"] == 0.0
    assert m["equity_shape_passed"] is True

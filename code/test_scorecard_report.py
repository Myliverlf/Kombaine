"""Targeted regression tests for scorecard_report equity math.

Futures equity in the read-only report should be deposit + unrealized PnL on
open positions, not a mixture of closed PnL or notional values.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from scorecard_report import compute_equity_from_slots, slot_unrealized_pnl  # noqa: E402


def test_slot_unrealized_pnl_prefers_explicit_mtm_fields() -> None:
    open_slot = {
        "open_position": {"direction": "LONG", "qty": 1, "entry_price": 100.0},
        "unrealized_pnl_rub": 12.5,
        "pnl_rub": 99.0,
    }
    assert slot_unrealized_pnl(open_slot) == pytest.approx(12.5)


def test_slot_unrealized_pnl_ignores_closed_slot_pnl() -> None:
    closed_slot = {
        "open_position": None,
        "unrealized_pnl_rub": 33.0,
        "pnl_rub": 77.0,
    }
    assert slot_unrealized_pnl(closed_slot) == pytest.approx(0.0)


def test_compute_equity_is_deposit_plus_unrealized_pnl_only() -> None:
    slots = {
        "slot_open": {
            "open_position": {"direction": "SHORT", "qty": 1, "entry_price": 82.78},
            "unrealized_pnl_rub": 48.0,
            "pnl_rub": 1_000.0,
        },
        "slot_closed": {
            "open_position": None,
            "unrealized_pnl_rub": 500.0,
            "pnl_rub": 250.0,
        },
    }
    equity = compute_equity_from_slots(slots, 21_281.0)
    assert equity == pytest.approx(21_329.0)


def test_compute_equity_falls_back_to_open_slot_pnl_when_mtm_missing() -> None:
    slots = {
        "slot_open": {
            "open_position": {"direction": "LONG", "qty": 1, "entry_price": 100.0},
            "pnl_rub": -7.5,
        }
    }
    equity = compute_equity_from_slots(slots, 10_000.0)
    assert equity == pytest.approx(9_992.5)

"""Fixture-level regression checks for live_watchdog dry-run snapshots."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from live_watchdog import load_fixture_bundle  # noqa: E402

FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "dry-run" / "watchdog"
PASS_FIXTURE = FIXTURE_ROOT / "pass"
FAIL_FIXTURE = FIXTURE_ROOT / "orphan_scale_timer_inactive"


def test_pass_fixture_contains_consistent_snapshots() -> None:
    bundle = load_fixture_bundle(PASS_FIXTURE)
    assert bundle["portfolio"]["slots"]
    assert bundle["broker_positions"]
    assert bundle["analytics_open_trades"]
    assert all(item.get("active") == "active" for item in bundle["systemd_timers"].values())


def test_fail_fixture_contains_orphan_and_inactive_timer() -> None:
    bundle = load_fixture_bundle(FAIL_FIXTURE)
    slot_ids = set(bundle["portfolio"]["slots"])
    orphan_slots = [trade for trade in bundle["analytics_open_trades"] if trade.get("slot_id") not in slot_ids]
    assert orphan_slots
    assert bundle["systemd_timers"]["combine-supervisor.timer"]["active"] == "inactive"
    assert bundle["portfolio"]["slots"]["slot_GAZP_dup_a"]["open_position"]["entry_price"] == 8278.0
    assert bundle["portfolio"]["slots"]["slot_GAZP_dup_b"]["open_position"]["entry_price"] == 82.78

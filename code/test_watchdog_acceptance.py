"""Acceptance-level dry-run checks for live_watchdog PASS/FAIL behavior."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from live_watchdog import audit_watchdog, load_fixture_bundle  # noqa: E402

FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "dry-run" / "watchdog"
PASS_FIXTURE = FIXTURE_ROOT / "pass"
FAIL_FIXTURE = FIXTURE_ROOT / "orphan_scale_timer_inactive"


def test_watchdog_pass_fixture_is_clean() -> None:
    payload = audit_watchdog(load_fixture_bundle(PASS_FIXTURE), ROOT / "tmp-acceptance-report")
    assert payload["ok"] is True
    assert payload["issues"] == []
    assert payload["live_orders"] == 0


def test_watchdog_fail_fixture_exposes_regressions() -> None:
    payload = audit_watchdog(load_fixture_bundle(FAIL_FIXTURE), ROOT / "tmp-acceptance-report")
    joined = "\n".join(payload["issues"])
    assert "orphan_open_trades" in joined
    assert "timer_inactive:combine-supervisor.timer" in joined
    assert any("scale_price_mismatch" in item for item in payload["issues"])
    assert any("duplicate_ticker_slots" in item for item in payload["issues"])

"""Regression tests for live_watchdog dry-run fixtures.

Covers orphan trades, scale-price mismatch, inactive timers, duplicate ticker
slots, and stale empty slots without touching broker state.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from live_watchdog import audit_watchdog, load_fixture_bundle, main as watchdog_main  # noqa: E402

FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "dry-run" / "watchdog"
PASS_FIXTURE = FIXTURE_ROOT / "pass"
FAIL_FIXTURE = FIXTURE_ROOT / "orphan_scale_timer_inactive"


@pytest.mark.parametrize("fixture_dir,expected_ok", [(PASS_FIXTURE, True), (FAIL_FIXTURE, False)])
def test_watchdog_fixture_bundle_smoke(fixture_dir: Path, expected_ok: bool, tmp_path: Path) -> None:
    payload = audit_watchdog(load_fixture_bundle(fixture_dir), tmp_path / "report")
    assert payload["ok"] is expected_ok
    assert payload["live_orders"] == 0


def test_pass_fixture_has_no_issues(tmp_path: Path) -> None:
    payload = audit_watchdog(load_fixture_bundle(PASS_FIXTURE), tmp_path / "report")
    assert payload["ok"] is True
    assert payload["issues"] == []
    assert payload["checks"]["duplicate_ticker_slots"] == {}
    assert payload["checks"]["stale_empty_slots"] == []
    assert payload["checks"]["inactive_timers"] == []
    assert all(float(slot.get("go_rub") or 0) > 0 for slot in payload["portfolio_slots"].values())
    open_tickers = [slot["ticker"] for slot in payload["portfolio_slots"].values() if slot["open_position"]]
    assert len(open_tickers) == len(set(open_tickers))
    gazp_slot = next(slot for slot in payload["portfolio_slots"].values() if slot["ticker"] == "GAZP")
    assert gazp_slot["direction"] == "SHORT"
    assert gazp_slot["entry_price"] == pytest.approx(82.78)
    assert gazp_slot["sl_px"] > gazp_slot["entry_price"] > gazp_slot["tp_px"]
    assert payload["portfolio"]["deposit_rub"] == pytest.approx(21281.0)


def test_detects_orphan_scale_and_inactive_timer(tmp_path: Path) -> None:
    payload = audit_watchdog(load_fixture_bundle(FAIL_FIXTURE), tmp_path / "report")
    issue_blob = "\n".join(payload["issues"])
    assert "orphan_open_trades:1" in issue_blob
    assert any("timer_inactive:combine-supervisor.timer" in item for item in payload["issues"])
    assert any("duplicate_ticker_slots" in item for item in payload["issues"])
    assert payload["checks"]["inactive_timers"] == ["combine-supervisor.timer"]
    assert payload["checks"]["orphan_open_trades"]
    assert any("go_rub_non_positive" in item for item in payload["issues"]) is False
    assert any("long_sltp_invalid" in item for item in payload["issues"]) is False
    assert any("short_sltp_invalid" in item for item in payload["issues"]) is False
    assert any("normalized_price=82.78" in item for item in payload["warnings"])
    from live_watchdog import normalize_price
    assert normalize_price(8278.0, 82.78) == pytest.approx(82.78)


def test_detects_stale_empty_slot_warning(tmp_path: Path) -> None:
    payload = audit_watchdog(load_fixture_bundle(FAIL_FIXTURE), tmp_path / "report")
    assert any("stale_empty_slot" in warning for warning in payload["warnings"])


def test_cli_uses_fixture_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    out_dir = tmp_path / "report"
    monkeypatch.setattr(sys, "argv", ["live_watchdog.py", "--dry-run-dir", str(PASS_FIXTURE), "--report-dir", str(out_dir)])
    exit_code = watchdog_main()
    captured = capsys.readouterr().out.strip().splitlines()[-1]
    assert exit_code == 0
    payload = json.loads(captured)
    assert payload["ok"] is True
    assert payload["live_orders"] == 0
    assert (out_dir / "latest.json").exists()
    assert (out_dir / "latest.md").exists()

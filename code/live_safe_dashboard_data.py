#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_DIR = ROOT / "state"
DEFAULT_DRY_RUN_DIR = ROOT / "tests" / "fixtures" / "dry-run" / "watchdog" / "pass"

FIXTURE_FILES = {
    "portfolio": "portfolio.json",
    "broker_positions": "broker_positions.json",
    "analytics_open_trades": "analytics_open_trades.json",
    "systemd_timers": "systemd_timers.json",
}


def load_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def load_dashboard_bundle(dry_run_dir: Path | None = None, state_dir: Path | None = None) -> dict[str, Any]:
    """Load a read-only dashboard snapshot from local files.

    The fixture bundle drives portfolio/broker/timer data; registry state is read
    separately from the local state dir so the dashboard can show waitlist info
    without talking to a broker.
    """

    dry_run_dir = (dry_run_dir or DEFAULT_DRY_RUN_DIR).resolve()
    state_dir = (state_dir or DEFAULT_STATE_DIR).resolve()

    portfolio = load_json(dry_run_dir / FIXTURE_FILES["portfolio"], {"slots": {}})
    broker_positions = load_json(dry_run_dir / FIXTURE_FILES["broker_positions"], {})
    analytics_open_trades = load_json(dry_run_dir / FIXTURE_FILES["analytics_open_trades"], [])
    systemd_timers = load_json(dry_run_dir / FIXTURE_FILES["systemd_timers"], {})

    strategy_registry = load_json(state_dir / "strategy_registry.json", {"strategies": {}})
    dataset_registry = load_json(state_dir / "dataset_registry.json", {"datasets": {}})
    waitlist = load_json(state_dir / "waitlist.json", {"candidates": {}})
    signal_pool = load_json(state_dir / "signal_pool.json", {"strategies": {}})
    research_latest = load_json(ROOT / "reports" / "research_pipeline" / "latest.json", {})
    live_dashboard_latest = load_json(ROOT / "reports" / "live_dashboard" / "latest.json", {})

    return {
        "dry_run_dir": dry_run_dir,
        "state_dir": state_dir,
        "portfolio": portfolio,
        "broker_positions": broker_positions,
        "analytics_open_trades": analytics_open_trades,
        "systemd_timers": systemd_timers,
        "strategy_registry": strategy_registry,
        "dataset_registry": dataset_registry,
        "waitlist": waitlist,
        "signal_pool": signal_pool,
        "research_latest": research_latest,
        "live_dashboard_latest": live_dashboard_latest,
    }

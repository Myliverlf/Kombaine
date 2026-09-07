#!/usr/bin/env python3
"""Prepare strategy_combine for market-session pickup without placing orders.

What it does:
- refreshes canonical active_signal_pool records so the live supervisor can
  promote them into portfolio watch slots when the market session starts;
- exports derived legacy signal_pool/waitlist files;
- optionally runs read-only market_session_preflight;
- never imports broker execution modules and never sends orders.

Why: strategy generation runs every ~2h, while supervisor promotion filters
signal_pool by signal_max_age_minutes (currently 16m). Without this safe refresh,
valid active strategies can look stale exactly when the market opens.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = PROJECT_ROOT / "code"
REPORT_DIR = PROJECT_ROOT / "reports" / "strategy_architect"
sys.path.insert(0, str(CODE_DIR))

from strategy_registry import STATUS_ACTIVE_SIGNAL_POOL, StrategyRegistry  # type: ignore  # noqa:E402


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _active_limit() -> int:
    cfg = _load_json(PROJECT_ROOT / "config.json", {})
    return int(cfg.get("risk", {}).get("signal_pool_max", 10) or 10)


def refresh_active_pool(dry_run: bool = False) -> dict[str, Any]:
    reg = StrategyRegistry.load()
    limit = _active_limit()
    active = sorted(
        [r for r in reg.records() if r.status == STATUS_ACTIVE_SIGNAL_POOL],
        key=lambda r: float(r.active_rank or 0.0),
        reverse=True,
    )[:limit]
    now = time.time()
    refreshed: list[dict[str, Any]] = []

    if not dry_run:
        for slot, record in enumerate(active, 1):
            record.signal_pool_slot = slot
            record.quality_gate["session_ready_ts"] = now
            record.quality_gate["session_ready_utc"] = datetime.now(timezone.utc).isoformat()
            record.add_event(
                STATUS_ACTIVE_SIGNAL_POOL,
                reason="session_open_refresh",
                note="prepared for market-session pickup; no orders",
                payload={"slot": slot, "live_orders": 0},
            )
            reg._store_record(record)  # internal but canonical module API has no update_record helper
            refreshed.append({
                "strategy_id": record.strategy_id,
                "ticker": record.ticker,
                "strategy": record.strategy,
                "rank": float(record.active_rank or 0.0),
                "slot": slot,
            })
        reg.save()
        reg.export_legacy_state_files()
    else:
        for slot, record in enumerate(active, 1):
            refreshed.append({
                "strategy_id": record.strategy_id,
                "ticker": record.ticker,
                "strategy": record.strategy,
                "rank": float(record.active_rank or 0.0),
                "slot": slot,
            })

    payload = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "active_found": len(active),
        "refreshed": 0 if dry_run else len(refreshed),
        "ready_for_supervisor_promotion": len(active) > 0,
        "live_orders": 0,
        "top": refreshed[:10],
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "session_open_prepare_latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# Session Open Prepare — strategy_combine",
        "",
        f"- checked_at: {payload['checked_at']}",
        f"- dry_run: {payload['dry_run']}",
        f"- active_found: {payload['active_found']}",
        f"- refreshed: {payload['refreshed']}",
        f"- ready_for_supervisor_promotion: {payload['ready_for_supervisor_promotion']}",
        f"- live_orders: {payload['live_orders']}",
        "",
        "| slot | ticker | strategy | rank | id |",
        "|---:|---|---|---:|---|",
    ]
    for row in refreshed[:10]:
        lines.append(
            f"| {row['slot']} | {row['ticker']} | {row['strategy']} | {row['rank']:.2f} | {row['strategy_id']} |"
        )
    (REPORT_DIR / "session_open_prepare_latest.md").write_text("\n".join(lines), encoding="utf-8")
    return payload


def run_preflight() -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(CODE_DIR / "market_session_preflight.py")],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip()[-1000:],
        "stderr": proc.stderr.strip()[-1000:],
    }


def run_pickup_dryrun() -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(CODE_DIR / "supervisor_pickup_dryrun.py")],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip()[-1000:],
        "stderr": proc.stderr.strip()[-1000:],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-preflight", action="store_true")
    args = ap.parse_args()

    payload = refresh_active_pool(dry_run=args.dry_run)
    if not args.skip_preflight:
        payload["preflight"] = run_preflight()
        payload["pickup_dryrun"] = run_pickup_dryrun()
        (REPORT_DIR / "session_open_prepare_latest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps({
        "ready": payload["ready_for_supervisor_promotion"],
        "active_found": payload["active_found"],
        "refreshed": payload["refreshed"],
        "live_orders": 0,
        "preflight_rc": payload.get("preflight", {}).get("returncode"),
        "pickup_rc": payload.get("pickup_dryrun", {}).get("returncode"),
    }, ensure_ascii=False))
    return 0 if payload["ready_for_supervisor_promotion"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

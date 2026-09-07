#!/usr/bin/env python3
"""Local smoke for manual Strategy Architect dry-runs.

Checks only local fixture paths and report artifacts; no broker mutations.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = PROJECT_ROOT / "reports" / "strategy_architect"
DEFAULT_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "dry-run" / "watchdog_portfolio.json"


def load_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"failed to load JSON fixture {path}: {exc}") from exc


def validate_fixture(data: Dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise ValueError("fixture must be a JSON object")
    slots = data.get("slots")
    if not isinstance(slots, dict) or not slots:
        raise ValueError("fixture must contain non-empty slots")
    if data.get("halted") not in {True, False}:
        raise ValueError("fixture missing halted boolean")


def validate_reports() -> Dict[str, Any]:
    latest = REPORT_DIR / "latest.md"
    if not latest.exists():
        raise FileNotFoundError(f"missing report: {latest}")
    text = latest.read_text(encoding="utf-8")
    required = ["TimesFM source:", "live orders: 0", "Safety: paper/backtest only"]
    missing = [item for item in required if item not in text]
    if missing:
        raise AssertionError(f"report missing markers: {', '.join(missing)}")
    return {"latest_report": str(latest), "report_has_timesfm": True}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Manual Strategy Architect dry-run smoke")
    ap.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    ap.add_argument("--require-report", action="store_true", help="require reports/strategy_architect/latest.md to exist and contain safety markers")
    return ap


def main() -> int:
    args = build_parser().parse_args()
    fixture_path = Path(args.fixture)
    if not fixture_path.exists():
        raise FileNotFoundError(f"fixture not found: {fixture_path}")
    validate_fixture(load_json(fixture_path))
    smoke = {"fixture": str(fixture_path), "safe_local_only": True}
    if args.require_report:
        smoke.update(validate_reports())
    print(json.dumps(smoke, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

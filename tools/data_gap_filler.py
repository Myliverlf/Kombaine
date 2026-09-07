#!/usr/bin/env python3
"""Safe data gap filler for Strategy Architect universe.

Downloads missing 15m/1h continuous CSVs for a small number of roots per run.
Read-only market data only, no orders.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROP_ROOT = PROJECT_ROOT.parent
FUTURES_LAB = PROP_ROOT / "futures_lab"
DATA_ROOT = FUTURES_LAB / "artifacts" / "tinkoff_futures_data"
REPORT_DIR = PROJECT_ROOT / "reports" / "strategy_architect"
DISCOVERY_JSON = REPORT_DIR / "universe_discovery.json"


def load_plan() -> Dict[str, Any]:
    if not DISCOVERY_JSON.exists():
        subprocess.run(["python3", str(PROJECT_ROOT / "tools" / "universe_discovery.py")], check=True, cwd=str(PROJECT_ROOT))
    return json.loads(DISCOVERY_JSON.read_text(encoding="utf-8"))


def download_one(root: str, tf: str, days: int, dry_run: bool) -> Dict[str, Any]:
    out = DATA_ROOT / f"{root}_{days}d_{tf}_continuous.csv"
    cmd = [
        "python3", "futures_lab.py", "download",
        "--ticker", root,
        "--days", str(days),
        "--interval", tf,
        "--continuous",
        "--out", str(out),
    ]
    if dry_run:
        return {"root": root, "timeframe": tf, "status": "dry_run", "cmd": cmd, "path": str(out)}
    proc = subprocess.run(cmd, cwd=str(FUTURES_LAB), text=True, capture_output=True, timeout=900)
    rows = 0
    if out.exists():
        rows = max(0, sum(1 for _ in out.open("r", encoding="utf-8")) - 1)
    return {
        "root": root,
        "timeframe": tf,
        "status": "ok" if proc.returncode == 0 and rows >= 300 else "failed",
        "returncode": proc.returncode,
        "rows": rows,
        "path": str(out),
        "stdout_tail": proc.stdout[-1000:],
        "stderr_tail": proc.stderr[-1000:],
    }


def render_md(payload: Dict[str, Any]) -> str:
    lines = [
        "# Data gap filler — latest run",
        "",
        f"- dry_run: {payload['dry_run']}",
        f"- attempted_roots: {', '.join(payload['attempted_roots']) or 'none'}",
        f"- live_orders: {payload['live_orders']}",
        "",
        "| root | tf | status | rows | path |",
        "|---|---|---|---:|---|",
    ]
    for r in payload["results"]:
        lines.append(f"| {r['root']} | {r['timeframe']} | {r['status']} | {r.get('rows', 0)} | {r.get('path','')} |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-roots", type=int, default=1, help="Safety limit per run")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    plan = load_plan()
    results: List[Dict[str, Any]] = []
    attempted: List[str] = []
    for item in plan.get("download_plan", []):
        root = item["root"]
        if len(attempted) >= args.max_roots:
            break
        needed = item.get("needed") or []
        if not needed:
            continue
        attempted.append(root)
        for tf in needed:
            results.append(download_one(root, tf, args.days, args.dry_run))
    payload = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dry_run": args.dry_run,
        "attempted_roots": attempted,
        "results": results,
        "live_orders": 0,
    }
    (REPORT_DIR / "data_gap_filler_latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORT_DIR / "data_gap_filler_latest.md").write_text(render_md(payload), encoding="utf-8")
    print(json.dumps({"attempted_roots": attempted, "results": len(results), "dry_run": args.dry_run, "live_orders": 0}, ensure_ascii=False))
    print(REPORT_DIR / "data_gap_filler_latest.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

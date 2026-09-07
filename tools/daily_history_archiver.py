#!/usr/bin/env python3
"""Daily history archiver for Strategy Architect.

Goal:
- detect available futures roots from universe discovery
- ensure 60d files exist for readiness
- ensure 365d/1095d files exist for stable research on the strongest roots
- update discovery + artifact reports so every new daily run expands history

Read-only for broker state; market-data only.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROP_ROOT = PROJECT_ROOT.parent
FUTURES_LAB = PROP_ROOT / "futures_lab"
DATA_ROOT = FUTURES_LAB / "artifacts" / "tinkoff_futures_data"
REPORT_DIR = PROJECT_ROOT / "reports" / "strategy_architect"
DISCOVERY_JSON = REPORT_DIR / "universe_discovery.json"


def load_discovery() -> Dict[str, Any]:
    if DISCOVERY_JSON.exists():
        return json.loads(DISCOVERY_JSON.read_text(encoding="utf-8"))
    subprocess.run([sys.executable, str(PROJECT_ROOT / "tools" / "universe_discovery.py")], check=True, cwd=str(PROJECT_ROOT))
    return json.loads(DISCOVERY_JSON.read_text(encoding="utf-8"))


def ensure_file(root: str, tf: str, days: int) -> Dict[str, Any]:
    out = DATA_ROOT / f"{root}_{days}d_{tf}_continuous.csv"
    if out.exists():
        rows = max(0, sum(1 for _ in out.open("r", encoding="utf-8")) - 1)
        return {"root": root, "tf": tf, "days": days, "status": "exists", "rows": rows, "path": str(out)}
    cmd = [
        sys.executable,
        "futures_lab.py",
        "download",
        "--ticker",
        root,
        "--days",
        str(days),
        "--interval",
        tf,
        "--continuous",
        "--out",
        str(out),
    ]
    proc = subprocess.run(cmd, cwd=str(FUTURES_LAB), text=True, capture_output=True, timeout=1800)
    rows = 0
    if out.exists():
        rows = max(0, sum(1 for _ in out.open("r", encoding="utf-8")) - 1)
    return {
        "root": root,
        "tf": tf,
        "days": days,
        "status": "ok" if proc.returncode == 0 and rows >= 300 else "failed",
        "returncode": proc.returncode,
        "rows": rows,
        "path": str(out),
        "stdout_tail": proc.stdout[-1000:],
        "stderr_tail": proc.stderr[-1000:],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--roots", default="")
    ap.add_argument("--days", default="60,365,1095")
    ap.add_argument("--tfs", default="15m,1h")
    ap.add_argument("--max-roots", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    discovery = load_discovery()
    roots = [r.strip() for r in args.roots.split(",") if r.strip()] if args.roots else list(discovery.get("selected_roots", []))
    tfs = [t.strip() for t in args.tfs.split(",") if t.strip()]
    days_list = [int(x.strip()) for x in args.days.split(",") if x.strip()]

    results: List[Dict[str, Any]] = []
    processed: List[str] = []

    for root in roots[: args.max_roots]:
        processed.append(root)
        for tf in tfs:
            for days in days_list:
                if args.dry_run:
                    results.append({"root": root, "tf": tf, "days": days, "status": "dry_run"})
                else:
                    results.append(ensure_file(root, tf, days))

    payload = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "processed_roots": processed,
        "tfs": tfs,
        "days": days_list,
        "dry_run": args.dry_run,
        "results": results,
        "ok": sum(1 for r in results if r.get("status") in {"ok", "exists"}),
        "total": len(results),
    }
    (REPORT_DIR / "daily_history_archiver_latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORT_DIR / "daily_history_archiver_latest.md").write_text(
        "# Daily history archiver\n\n"
        f"- processed_roots: {', '.join(processed) or 'none'}\n"
        f"- days: {', '.join(map(str, days_list))}\n"
        f"- tfs: {', '.join(tfs)}\n"
        f"- dry_run: {args.dry_run}\n"
        f"- ok: {payload['ok']}/{payload['total']}\n",
        encoding="utf-8",
    )
    print(json.dumps({"processed_roots": processed, "ok": payload["ok"], "total": payload["total"], "dry_run": args.dry_run}, ensure_ascii=False))
    print(REPORT_DIR / "daily_history_archiver_latest.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

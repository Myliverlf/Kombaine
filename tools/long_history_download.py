#!/usr/bin/env python3
"""Long-history continuous download helper for Strategy Architect.

Builds 365d / 1095d continuous series for selected futures roots.
Read-only market data only.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List

DISCOVERY_JSON = Path(__file__).resolve().parents[1] / "reports" / "strategy_architect" / "universe_discovery.json"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROP_ROOT = PROJECT_ROOT.parent
FUTURES_LAB = PROP_ROOT / "futures_lab"
DATA_ROOT = FUTURES_LAB / "artifacts" / "tinkoff_futures_data"
REPORT_DIR = PROJECT_ROOT / "reports" / "strategy_architect"

# keep it conservative: only the roots we already know are relevant
DEFAULT_ROOTS = ["BR", "GAZP", "LKOH", "SBER", "Si", "USDRUB", "EURRUB", "CNY", "IMOEX", "NG"]
DEFAULT_TFS = ["15m", "1h"]
DEFAULT_DAYS = [365, 1095]


def run_download(root: str, tf: str, days: int) -> Dict[str, object]:
    out = DATA_ROOT / f"{root}_{days}d_{tf}_continuous.csv"
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
    attempts = []
    proc = None
    for attempt in range(1, 4):
        proc = subprocess.run(cmd, cwd=str(FUTURES_LAB), text=True, capture_output=True, timeout=1800)
        attempts.append({"attempt": attempt, "returncode": proc.returncode, "stderr_tail": proc.stderr[-300:]})
        if proc.returncode == 0:
            break
        stderr = (proc.stderr or "") + (proc.stdout or "")
        if "RESOURCE_EXHAUSTED" in stderr or "resource exhausted" in stderr.lower():
            time.sleep(20 * attempt)
            continue
        break
    rows = 0
    if out.exists():
        with out.open("r", encoding="utf-8") as fh:
            rows = max(0, sum(1 for _ in fh) - 1)
    return {
        "root": root,
        "tf": tf,
        "days": days,
        "path": str(out),
        "returncode": proc.returncode if proc else 1,
        "rows": rows,
        "ok": (proc.returncode == 0 if proc else False) and rows >= 300,
        "attempts": attempts,
        "stdout_tail": proc.stdout[-1000:] if proc else "",
        "stderr_tail": proc.stderr[-1000:] if proc else "",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--roots", default=",".join(DEFAULT_ROOTS))
    ap.add_argument("--tfs", default=",".join(DEFAULT_TFS))
    ap.add_argument("--days", default=",".join(map(str, DEFAULT_DAYS)))
    ap.add_argument("--limit", type=int, default=2, help="Max roots per run for safety")
    args = ap.parse_args()

    roots = [r.strip() for r in args.roots.split(",") if r.strip()]
    tfs = [t.strip() for t in args.tfs.split(",") if t.strip()]
    days_list = [int(x.strip()) for x in args.days.split(",") if x.strip()]

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    results: List[Dict[str, object]] = []
    processed = []
    for root in roots[: args.limit]:
        processed.append(root)
        for tf in tfs:
            for days in days_list:
                results.append(run_download(root, tf, days))

    payload = {
        "roots": processed,
        "tfs": tfs,
        "days": days_list,
        "results": results,
        "ok": sum(1 for r in results if r["ok"]),
        "total": len(results),
    }
    (REPORT_DIR / "long_history_download_latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

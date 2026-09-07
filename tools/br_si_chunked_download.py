#!/usr/bin/env python3
"""Chunked downloader for BR/Si long-history continuous series.

Purpose: avoid Tinkoff rate-limit exhaustion on one-shot 1095d 15m BR download
by splitting the horizon into smaller windows, downloading each window separately,
and stitching local CSVs deterministically.

Read-only market data only. No broker mutation.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROP_ROOT = PROJECT_ROOT.parent
FUTURES_LAB = PROP_ROOT / "futures_lab"
DATA_ROOT = FUTURES_LAB / "artifacts" / "tinkoff_futures_data"
WORK_DIR = PROJECT_ROOT / "state" / "br_si_chunked"
WORK_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_END = datetime.now(timezone.utc)
DEFAULT_CHUNK_DAYS = 180
DEFAULT_OVERLAP_DAYS = 3


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def windows(end: datetime, days: int, chunk_days: int, overlap_days: int) -> list[tuple[datetime, datetime]]:
    start = end - timedelta(days=days)
    out = []
    cursor = start
    while cursor < end:
        nxt = min(cursor + timedelta(days=chunk_days), end)
        out.append((cursor, nxt))
        if nxt >= end:
            break
        cursor = nxt - timedelta(days=overlap_days)
    return out


def run_download(root: str, tf: str, start: datetime, end: datetime, roll_days: int = 5) -> dict:
    # Use futures_lab.py download with explicit start/end, continuous stitching, and local output.
    out = WORK_DIR / f"{root}_{tf}_{start:%Y%m%d}_{end:%Y%m%d}.csv"
    cmd = [
        sys.executable,
        str(FUTURES_LAB / "futures_lab.py"),
        "download",
        "--ticker", root,
        "--start", iso(start),
        "--end", iso(end),
        "--interval", tf,
        "--continuous",
        "--roll-days", str(roll_days),
        "--out", str(out),
    ]
    proc = subprocess.run(cmd, cwd=str(FUTURES_LAB), capture_output=True, text=True)
    rows = 0
    if out.exists():
        rows = max(0, sum(1 for _ in out.open("r", encoding="utf-8")) - 1)
    return {
        "cmd": cmd,
        "returncode": proc.returncode,
        "stdout_tail": (proc.stdout or "")[-2000:],
        "stderr_tail": (proc.stderr or "")[-2000:],
        "path": str(out),
        "rows": rows,
        "ok": proc.returncode == 0 and rows > 0,
    }


def stitch_csvs(paths: Iterable[Path], out_path: Path) -> dict:
    import csv

    rows = []
    header = None
    seen = set()
    for p in sorted(paths):
        with p.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames if header is None else header
            for r in reader:
                t = r.get("time")
                if t in seen:
                    continue
                seen.add(t)
                rows.append(r)
    rows.sort(key=lambda r: r["time"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)
    return {"rows": len(rows), "path": str(out_path)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--roots", default="BR")
    ap.add_argument("--tfs", default="15m")
    ap.add_argument("--days", type=int, default=1095)
    ap.add_argument("--chunk-days", type=int, default=DEFAULT_CHUNK_DAYS)
    ap.add_argument("--overlap-days", type=int, default=DEFAULT_OVERLAP_DAYS)
    ap.add_argument("--roll-days", type=int, default=5)
    args = ap.parse_args()

    roots = [x.strip() for x in args.roots.split(",") if x.strip()]
    tfs = [x.strip() for x in args.tfs.split(",") if x.strip()]
    report = {"results": []}
    end = DEFAULT_END.replace(hour=0, minute=0, second=0, microsecond=0)

    for root in roots:
        for tf in tfs:
            chunk_reports = []
            files = []
            for idx, (start, stop) in enumerate(windows(end, args.days, args.chunk_days, args.overlap_days), 1):
                r = run_download(root, tf, start, stop, roll_days=args.roll_days)
                r["chunk_index"] = idx
                r["start"] = iso(start)
                r["end"] = iso(stop)
                chunk_reports.append(r)
                if r["ok"]:
                    files.append(Path(r["path"]))
                else:
                    # stop at first failed chunk: enough to understand rate-limit behavior
                    break
                time.sleep(1.0)
            out = DATA_ROOT / f"{root}_{args.days}d_{tf}_continuous.csv"
            stitched = stitch_csvs(files, out) if files else {"rows": 0, "path": str(out)}
            report["results"].append({
                "root": root,
                "tf": tf,
                "chunk_reports": chunk_reports,
                "stitched": stitched,
                "ok": stitched["rows"] > 0 and all(c.get("ok") for c in chunk_reports),
            })
    (WORK_DIR / "latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

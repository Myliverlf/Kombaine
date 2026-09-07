#!/usr/bin/env python3
"""Staged expansion report for 15-20 futures assets.

Aggregates four dry-run checks:
  - data availability / rows / timeframes
  - excluded RI policy
  - per-ticker smoke backtests
  - ready candidates vs blockers

This module is local-only and writes artifacts under reports/strategy_architect.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = PROJECT_ROOT / "reports" / "strategy_architect"

import sys
sys.path.insert(0, str(PROJECT_ROOT / "code"))

from strategy_combine_dryrun_env import default_safe_config, summarize_config, local_only_banner  # noqa:E402
from staged_universe_audit import build_universe_candidates, write_reports as write_universe_reports  # noqa:E402
from ticker_smoke_backtest import smoke_backtest_universe, write_reports as write_smoke_reports  # noqa:E402


def build_report(target_size: int = 20, min_rows: int = 300) -> Dict[str, Any]:
    """Build a staged expansion report from local dry-run artifacts."""
    config = default_safe_config()
    audit = build_universe_candidates(target_size=target_size, min_rows=min_rows)
    smoke = smoke_backtest_universe(target_size=target_size)
    ready = [item for item in audit["selected"] if item.get("ready") and not item.get("excluded")]
    blocked = [item for item in audit["selected"] if item.get("root") not in {r["root"] for r in ready}]
    candidates = []
    blockers: List[Dict[str, Any]] = []
    smoke_by_root = {item["root"]: item for item in smoke["candidates"] + smoke["blockers"]}
    for item in ready:
        smoke_item = smoke_by_root.get(item["root"], {})
        candidates.append({
            "root": item["root"],
            "ready": True,
            "smoke_ready": bool(smoke_item.get("ready", False)),
            "rows": item["timeframes"],
            "smoke_results": smoke_item.get("results", []),
        })
    for item in blocked:
        blockers.append({
            "root": item["root"],
            "ready": bool(item.get("ready", False)),
            "excluded": bool(item.get("excluded", False)),
            "missing_timeframes": item.get("missing_timeframes", []),
            "smoke_ready": bool(smoke_by_root.get(item["root"], {}).get("ready", False)),
            "rows": item["timeframes"],
        })
    blocker_summary = {
        "missing_data": sum(1 for item in blockers if item.get("missing_timeframes")),
        "excluded": sum(1 for item in blockers if item.get("excluded")),
        "smoke_failed": sum(1 for item in blockers if not item.get("smoke_ready")),
    }
    payload = {
        "banner": local_only_banner(),
        "config": summarize_config(config),
        "target_size": target_size,
        "min_rows": min_rows,
        "data_audit": audit,
        "smoke": smoke,
        "candidates": candidates,
        "blockers": blockers,
        "blocker_summary": blocker_summary,
        "ready_count": len(candidates),
        "blocker_count": len(blockers),
        "live_orders": 0,
    }
    return payload


def render_markdown(payload: Dict[str, Any]) -> str:
    lines = [
        "# Staged expansion report",
        "",
        f"- banner: {payload['banner']}",
        f"- target_size: {payload['target_size']}",
        f"- min_rows: {payload['min_rows']}",
        f"- ready_count: {payload['ready_count']}",
        f"- blocker_count: {payload['blocker_count']}",
        f"- live_orders: {payload['live_orders']}",
        "",
        "## Candidates",
        "| root | ready | smoke_ready | 15m rows | 1h rows |",
        "|---|---|---|---:|---:|",
    ]
    for item in payload["candidates"]:
        rows = item["rows"]
        lines.append(
            f"| {item['root']} | {item['ready']} | {item['smoke_ready']} | {rows['15m']['rows']} | {rows['1h']['rows']} |"
        )
    lines.extend([
        "",
        "## Blockers",
        "| root | excluded | missing_timeframes | smoke_ready |",
        "|---|---|---|---|",
    ])
    for item in payload["blockers"]:
        lines.append(
            f"| {item['root']} | {item['excluded']} | {', '.join(item['missing_timeframes']) or '-'} | {item['smoke_ready']} |"
        )
    lines.extend([
        "",
        "## Blocker summary",
        f"- missing_data: {payload['blocker_summary']['missing_data']}",
        f"- excluded: {payload['blocker_summary']['excluded']}",
        f"- smoke_failed: {payload['blocker_summary']['smoke_failed']}",
    ])
    return "\n".join(lines)


def write_reports(payload: Dict[str, Any]) -> Dict[str, str]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / "staged_expansion_report_latest.json"
    md_path = REPORT_DIR / "staged_expansion_report_latest.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build staged expansion report (dry-run only)")
    parser.add_argument("--target-size", type=int, default=20)
    parser.add_argument("--min-rows", type=int, default=300)
    parser.add_argument("--dry-run", action="store_true", help="kept for CLI compatibility; always dry-run")
    args = parser.parse_args()

    payload = build_report(target_size=args.target_size, min_rows=args.min_rows)
    paths = write_reports(payload)
    print(json.dumps({
        "ready_count": payload["ready_count"],
        "blocker_count": payload["blocker_count"],
        "candidates": [item["root"] for item in payload["candidates"]],
        "blockers": [item["root"] for item in payload["blockers"]],
        "live_orders": 0,
        "reports": paths,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

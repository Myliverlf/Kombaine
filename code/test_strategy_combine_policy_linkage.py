#!/usr/bin/env python3
"""Local regression checker for the strategy_combine linkage.

This is a standalone dry-run validation script, not a broker mutation or live order tool.
It checks that the latest local artifacts expose the policy linkage:
- equity_shape summary exists,
- equity_diversity family summary exists,
- indicator_rotation_policy consumes both,
- strategy_architect_autopilot surfaces policy boosts/penalties.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = PROJECT_ROOT / "reports" / "strategy_architect"


def load_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"failed to load JSON {path}: {exc}") from exc


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def main() -> int:
    shape_path = REPORT_DIR / "equity_shape_filter_latest.json"
    diversity_path = REPORT_DIR / "equity_diversity_filter_latest.json"
    policy_path = REPORT_DIR / "indicator_rotation_policy.json"
    cycle_paths = sorted(REPORT_DIR.glob("cycle_*.json"))
    latest_md = REPORT_DIR / "latest.md"

    require(shape_path.exists(), f"missing report: {shape_path}")
    require(diversity_path.exists(), f"missing report: {diversity_path}")
    require(policy_path.exists(), f"missing report: {policy_path}")
    require(cycle_paths, f"missing report: {REPORT_DIR / 'cycle_*.json'}")
    require(latest_md.exists(), f"missing report: {latest_md}")

    shape = load_json(shape_path)
    diversity = load_json(diversity_path)
    policy = load_json(policy_path)
    latest_cycle = load_json(cycle_paths[-1])
    latest_text = latest_md.read_text(encoding="utf-8")

    require(bool(shape.get("rows")), "equity_shape rows missing")
    require(bool(diversity.get("family_summary")), "equity_diversity family_summary missing")
    require(any(row.get("winner_boost", 0) > 0 for row in diversity.get("family_summary", [])), "no winner_boost rows in diversity summary")
    require(any(row.get("clone_penalty", 0) > 0 or row.get("sideways_penalty", 0) > 0 for row in diversity.get("family_summary", [])), "no sideways/clone penalties in diversity summary")
    require(bool(policy.get("equity_shape_family_scores")), "policy missing equity_shape_family_scores")
    require(bool(policy.get("equity_diversity_family_scores")), "policy missing equity_diversity_family_scores")
    require(bool(policy.get("family_rows")), "policy missing family_rows")
    require(any(row.get("policy_reason") == "winner_boost" for row in policy.get("family_rows", [])) or any(row.get("source") == "equity_shape" for row in policy.get("family_rows", [])), "policy missing winner boost linkage")
    require(any(row.get("policy_reason") == "clone_penalty" for row in policy.get("family_rows", [])) or any(row.get("policy_reason") == "sideways_penalty" for row in policy.get("family_rows", [])), "policy missing penalty linkage")
    require("policy_family_rows" in latest_cycle, "autopilot payload missing policy_family_rows")
    require(any("winner" in str(row.get("family_role", "")) for row in latest_cycle.get("policy_family_rows", [])) or any(float(row.get("winner_boost", 0) or 0) > 0 for row in latest_cycle.get("policy_family_rows", [])), "autopilot missing winner boost rows")
    require(any(float(row.get("sideways_penalty", 0) or 0) > 0 or float(row.get("clone_penalty", 0) or 0) > 0 for row in latest_cycle.get("policy_family_rows", [])), "autopilot missing penalty rows")
    require("live orders: 0" in latest_text, "latest report missing live orders marker")
    require("Safety: paper/backtest only" in latest_text, "latest report missing safety marker")

    print(json.dumps({
        "shape_rows": len(shape.get("rows", [])),
        "diversity_families": len(diversity.get("family_summary", [])),
        "policy_families": len(policy.get("family_rows", [])),
        "cycle_policy_rows": len(latest_cycle.get("policy_family_rows", [])),
        "status": "PASS",
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

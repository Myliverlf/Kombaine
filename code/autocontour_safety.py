"""Autocontour Safety — dry-run gate for the feedback/backtest loop.

Validates the acceptance constraints without touching live broker/orders:
  - RI excluded
  - max_slots <= 3
  - max_contracts_per_entry == 1
  - no live broker/order patterns in provided modules
  - backtest scorecard exists and supports PnL↑/risk↓ comparison
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

DEFAULT_FORBIDDEN_CALLS = {"post_order", "place_order", "send_order", "submit_order", "Client"}


def validate_limits(config: Dict[str, Any]) -> Dict[str, Any]:
    risk = config.get("risk", {}) if isinstance(config, dict) else {}
    excluded = config.get("excluded", []) if isinstance(config, dict) else []
    max_slots = int(risk.get("max_slots", 0))
    max_contracts = int(risk.get("max_contracts_per_entry", 0))
    return {
        "ri_excluded": "RI" in [str(item).upper() for item in excluded],
        "max_slots_ok": max_slots <= 3,
        "max_contracts_ok": max_contracts == 1,
        "max_slots": max_slots,
        "max_contracts_per_entry": max_contracts,
    }


def _find_forbidden_calls(path: Path, forbidden_calls: Iterable[str] = DEFAULT_FORBIDDEN_CALLS) -> List[str]:
    if not path.exists():
        return ["<missing file>"]
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:
        return ["<syntax error>"]

    forbidden = set(forbidden_calls)
    found: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = ""
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name in forbidden:
                found.append(name)
    return found


def validate_no_live_patterns(paths: Iterable[Path]) -> Dict[str, List[str]]:
    report: Dict[str, List[str]] = {}
    for path in paths:
        report[str(path)] = _find_forbidden_calls(path)
    return report


def validate_autocontour_artifact(
    config: Dict[str, Any],
    backtest_artifact: Dict[str, Any],
    source_paths: Optional[Iterable[Path]] = None,
) -> Dict[str, Any]:
    limits = validate_limits(config)
    scorecard = backtest_artifact.get("scorecard", {}) if isinstance(backtest_artifact, dict) else {}
    ab_compare = backtest_artifact.get("ab_compare", {}) if isinstance(backtest_artifact, dict) else {}
    target_compare = ab_compare.get("healthy_vs_mixed", {}) if isinstance(ab_compare, dict) else {}
    source_paths = list(source_paths or [])
    live_patterns = validate_no_live_patterns(source_paths) if source_paths else {}

    has_pnl_risk = bool(target_compare.get("pnl_up") is not None and target_compare.get("risk_down") is not None)
    passed = all([
        limits["ri_excluded"],
        limits["max_slots_ok"],
        limits["max_contracts_ok"],
        bool(scorecard),
        has_pnl_risk,
        all(not issues for issues in live_patterns.values()),
    ])

    return {
        "passed": passed,
        "limits": limits,
        "has_scorecard": bool(scorecard),
        "pnl_up": target_compare.get("pnl_up"),
        "risk_down": target_compare.get("risk_down"),
        "live_patterns": live_patterns,
        "source_count": len(source_paths),
    }


def main() -> int:
    example = validate_autocontour_artifact(
        {"excluded": ["RI"], "risk": {"max_slots": 3, "max_contracts_per_entry": 1}},
        {"scorecard": {"composite_score": 0.4}, "ab_compare": {"healthy_vs_mixed": {"pnl_up": True, "risk_down": True}}},
    )
    print(example)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

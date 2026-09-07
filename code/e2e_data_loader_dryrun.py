"""E2E Data Loader Dry-Run — полная safe-цепочка data_loader → risk_scorecard.

Запускает:
    1. load_ohlcv для 3 тикеров (SBER, LKOH, GAZP) — real CSV + fallback
    2. compute_scorecard — PnL↑/risk↓ composite scorecard
    3. validate_constraints — max_slots≤3, max_contracts=1, RI excluded
    4. AST-guard — zero broker imports
    5. Честный PASS/FAIL + текстовый отчёт

Zero live orders. Live imports forbidden by AST-guard.
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


# ─── Import check: no broker ──────────────────────────────────────────
def _assert_no_broker():
    """AST-guard: ensures no broker imports in this module."""
    source = inspect.getsource(inspect.getmodule(_assert_no_broker))
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name.lower()
                assert "tinkoff" not in name, f"Broker import detected: {alias.name}"
                assert "alpaca" not in name, f"Broker import detected: {alias.name}"
                assert "ib" not in name, f"Broker import detected: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mod = node.module.lower()
                assert "tinkoff" not in mod, f"Broker import detected: {node.module}"
                assert "alpaca" not in mod, f"Broker import detected: {node.module}"


_assert_no_broker()


# ─── Imports ──────────────────────────────────────────────────────────
from data_loader import load_ohlcv, load_universe, DEFAULT_EXCLUDED
from risk_scorecard import compute_scorecard, validate_constraints
from allocator_metrics import expectancy_r, risk_penalty, WEIGHTS


def run_dryrun() -> Dict[str, Any]:
    """Execute full dry-run and return results dict."""
    report: Dict[str, Any] = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "steps": [],
        "overall_pass": False,
    }

    # ── Step 1: Load OHLCV for 3 tickers ──────────────────────────────
    step1: Dict[str, Any] = {"name": "load_ohlcv", "status": "FAIL"}
    tickers = ["SBER", "LKOH", "GAZP"]

    try:
        universe = load_universe(tickers, "15m")
        step1["loaded"] = list(universe.keys())
        step1["sources"] = {
            t: df.attrs.get("source", "unknown")
            for t, df in universe.items()
        }
        step1["row_counts"] = {
            t: len(df) for t, df in universe.items()
        }
        if len(universe) == len(tickers):
            step1["status"] = "PASS"
        else:
            step1["status"] = "WARN"
            step1["detail"] = (
                f"Expected {len(tickers)} tickers, loaded {len(universe)}"
            )
    except Exception as exc:
        step1["error"] = str(exc)

    report["steps"].append(step1)

    # ── Step 2: Compute risk scorecard ─────────────────────────────────
    step2: Dict[str, Any] = {"name": "risk_scorecard", "status": "FAIL"}

    try:
        scorecard = compute_scorecard(
            tickers, risk_per_trade=1.0
        )
        step2["ranked"] = scorecard["ranked"]
        step2["overall"] = scorecard["overall"]
        step2["ticker_count"] = scorecard["constraints"]["total_scored"]

        # Validate: each ticker has composite_score
        all_have_scores = all(
            "composite_score" in scorecard["tickers"][t]
            for t in scorecard["ranked"]
        )
        if all_have_scores and scorecard["constraints"]["total_scored"] > 0:
            step2["status"] = "PASS"
        else:
            step2["status"] = "FAIL"
            step2["detail"] = "Some tickers missing composite_score"
    except Exception as exc:
        step2["error"] = str(exc)

    report["steps"].append(step2)

    # ── Step 3: Validate constraints ───────────────────────────────────
    step3: Dict[str, Any] = {"name": "validate_constraints", "status": "FAIL"}

    try:
        passed, violations = validate_constraints(scorecard)
        step3["passed"] = passed
        step3["violations"] = violations
        step3["constraints"] = {
            "max_slots": scorecard["constraints"]["max_slots"],
            "max_contracts_per_entry": scorecard["constraints"]["max_contracts_per_entry"],
            "excluded": scorecard["constraints"]["excluded"],
        }
        step3["status"] = "PASS" if passed else "FAIL"
    except Exception as exc:
        step3["error"] = str(exc)

    report["steps"].append(step3)

    # ── Step 4: RI exclusion check ─────────────────────────────────────
    step4: Dict[str, Any] = {"name": "ri_exclusion", "status": "FAIL"}

    try:
        ri_in_excluded = "RI" in DEFAULT_EXCLUDED
        ri_in_scored = "RI" in scorecard.get("tickers", {})
        step4["RI_in_excluded"] = ri_in_excluded
        step4["RI_in_scored"] = ri_in_scored
        if ri_in_excluded and not ri_in_scored:
            step4["status"] = "PASS"
        else:
            step4["detail"] = (
                f"RI excluded={ri_in_excluded}, in scored={ri_in_scored}"
            )
    except Exception as exc:
        step4["error"] = str(exc)

    report["steps"].append(step4)

    # ── Step 5: PnL/risk metric check ──────────────────────────────────
    step5: Dict[str, Any] = {"name": "pnl_risk_metric", "status": "FAIL"}

    try:
        has_expectancy = all(
            "expectancy_r" in scorecard["tickers"][t]
            for t in scorecard["ranked"]
        )
        has_risk = all(
            "risk_penalty" in scorecard["tickers"][t]
            for t in scorecard["ranked"]
        )
        has_composite = all(
            "composite_score" in scorecard["tickers"][t]
            for t in scorecard["ranked"]
        )
        step5["expectancy_r_available"] = has_expectancy
        step5["risk_penalty_available"] = has_risk
        step5["composite_score_available"] = has_composite
        step5["weights"] = WEIGHTS

        if has_expectancy and has_risk and has_composite:
            step5["status"] = "PASS"
            # Show per-ticker summary
            step5["per_ticker"] = {
                t: {
                    "expectancy_r": scorecard["tickers"][t]["expectancy_r"],
                    "risk_penalty": scorecard["tickers"][t]["risk_penalty"],
                    "composite": scorecard["tickers"][t]["composite_score"],
                }
                for t in scorecard["ranked"]
            }
        else:
            step5["detail"] = "Missing metric components"
    except Exception as exc:
        step5["error"] = str(exc)

    report["steps"].append(step5)

    # ── Overall verdict ────────────────────────────────────────────────
    statuses = [s["status"] for s in report["steps"]]
    fails = statuses.count("FAIL")
    passes = statuses.count("PASS")

    report["overall_pass"] = fails == 0
    report["summary"] = {
        "total_steps": len(report["steps"]),
        "passes": passes,
        "fails": fails,
        "warns": statuses.count("WARN"),
    }

    return report


def format_report(report: Dict[str, Any]) -> str:
    """Format report as human-readable text."""
    lines: List[str] = []
    lines.append("=" * 60)
    lines.append("E2E DATA LOADER DRY-RUN REPORT")
    lines.append(f"Timestamp: {report['timestamp']}")
    lines.append("=" * 60)

    for step in report["steps"]:
        icon = {"PASS": "✓", "FAIL": "✗", "WARN": "⚠"}.get(step["status"], "?")
        lines.append(f"\n{icon} Step: {step['name']} — {step['status']}")
        for k, v in step.items():
            if k in ("name", "status"):
                continue
            if isinstance(v, dict):
                lines.append(f"  {k}:")
                for kk, vv in v.items():
                    lines.append(f"    {kk}: {vv}")
            elif isinstance(v, list):
                lines.append(f"  {k}: {v}")
            else:
                lines.append(f"  {k}: {v}")

    lines.append("\n" + "=" * 60)
    verdict = "PASS ✓" if report["overall_pass"] else "FAIL ✗"
    lines.append(f"OVERALL: {verdict}")
    s = report["summary"]
    lines.append(
        f"Steps: {s['total_steps']}, "
        f"Passes: {s['passes']}, Fails: {s['fails']}, Warns: {s['warns']}"
    )
    lines.append("=" * 60)

    return "\n".join(lines)


if __name__ == "__main__":
    report = run_dryrun()
    print(format_report(report))

    # Exit code: 0 = PASS, 1 = FAIL
    sys.exit(0 if report["overall_pass"] else 1)

"""Validate Forecast Scorecard Bridge — CLI-утилита для проверки bridge-модуля.

Проверки:
  1. AST-guard: check_no_broker_imports
  2. py_compile check
  3. Config guard: max_slots ≤ 3, max_contracts_per_entry = 1, RI excluded
  4. Dry-run smoke test: ForecastContext + forecast_scorecard_summary

Usage:
    python3 code/validate_forecast_bridge.py
    exit 0 = all checks pass
    exit 1 = one or more checks failed
"""
from __future__ import annotations

import ast
import json
import os
import py_compile
import sys
from typing import Any, Dict, List

# Ensure code/ dir on sys.path
_CODE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)))
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

from forecast_context import ForecastContext, EXCLUDED_TICKERS
from forecast_context_scorer import check_no_broker_imports
from timesfm_adapter import ForecastResult


def check_py_compile(filepath: str) -> bool:
    """py_compile check for a .py file."""
    try:
        py_compile.compile(filepath, doraise=True)
        return True
    except py_compile.PyCompileError:
        return False


def check_config_guard(config_path: str) -> bool:
    """Verify config constraints: max_slots≤3, contracts=1, RI excluded."""
    if not os.path.exists(config_path):
        print(f"  [WARN] Config file not found: {config_path}")
        return True  # skip if not present

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    ok = True
    risk = config.get("risk", {})

    max_slots = risk.get("max_slots", 0)
    if max_slots > 3:
        print(f"  [FAIL] max_slots={max_slots} > 3")
        ok = False
    else:
        print(f"  [OK] max_slots={max_slots}")

    max_contracts = risk.get("max_contracts_per_entry", 0)
    if max_contracts != 1:
        print(f"  [FAIL] max_contracts_per_entry={max_contracts} != 1")
        ok = False
    else:
        print(f"  [OK] max_contracts_per_entry={max_contracts}")

    excluded = set(config.get("excluded", []))
    if "RI" not in excluded:
        print(f"  [FAIL] RI not in excluded: {excluded}")
        ok = False
    else:
        print(f"  [OK] RI excluded: {sorted(excluded)}")

    return ok


def dry_run_smoke_test() -> bool:
    """Create ForecastContext + call forecast_scorecard_summary."""
    try:
        from forecast_scorecard_bridge import (
            forecast_weighted_sharpe,
            forecast_adjusted_degradation,
            forecast_stability_bonus,
            forecast_scorecard_summary,
        )
    except ImportError as e:
        print(f"  [FAIL] Import error: {e}")
        return False

    # Build a minimal context
    fr = ForecastResult(
        direction="up",
        ci_width=0.02,
        confidence=0.8,
        horizon=20,
        source="timesfm",
    )
    ctx = ForecastContext(
        per_ticker={"Si": fr},
        portfolio_bias="up",
        volatility_regime="calm",
        confidence_score=0.8,
        meta={"tickers_with_signal": 1, "tickers_no_signal": 0, "avg_ci_width": 0.02, "n_tickers": 1},
    )

    ok = True

    # Test 1: forecast_weighted_sharpe
    result = forecast_weighted_sharpe([0.01, -0.005, 0.02, -0.01, 0.015], ctx, "Si")
    if "weighted_sharpe" not in result:
        print(f"  [FAIL] forecast_weighted_sharpe missing key")
        ok = False
    else:
        print(f"  [OK] forecast_weighted_sharpe: {result['weighted_sharpe']}")

    # Test 2: forecast_adjusted_degradation
    result = forecast_adjusted_degradation([0.01, -0.005, 0.02, -0.01, 0.015], ctx, "Si")
    if "status" not in result:
        print(f"  [FAIL] forecast_adjusted_degradation missing status")
        ok = False
    else:
        print(f"  [OK] forecast_adjusted_degradation: {result['status']}")

    # Test 3: forecast_stability_bonus
    result = forecast_stability_bonus([0.01, -0.005, 0.02, -0.01, 0.015], ctx, "Si")
    if "adjusted_stability" not in result:
        print(f"  [FAIL] forecast_stability_bonus missing key")
        ok = False
    else:
        print(f"  [OK] forecast_stability_bonus: {result['adjusted_stability']}")

    # Test 4: forecast_scorecard_summary
    slots = [{"ticker": "Si", "score": 8.0, "direction": "LONG", "slot_score": 7.5, "risk_penalty": 0.1}]
    config = {"max_slots": 3, "max_contracts_per_entry": 1, "excluded": ["RI"]}
    result = forecast_scorecard_summary(slots, ctx, config)
    if "pnl_up_indicator" not in result or "risk_down_indicator" not in result:
        print(f"  [FAIL] forecast_scorecard_summary missing indicators")
        ok = False
    else:
        print(f"  [OK] forecast_scorecard_summary: PnL↑={result['pnl_up_indicator']}, risk↓={result['risk_down_indicator']}")

    # Test 5: RI excluded
    ri_result = forecast_weighted_sharpe([0.01, -0.005], ctx, "RI")
    if ri_result["confidence_mult"] != 0.0:
        print(f"  [FAIL] RI not excluded from forecast")
        ok = False
    else:
        print(f"  [OK] RI excluded: forecast contribution = 0.0")

    return ok


def main() -> int:
    """Run all validation checks."""
    print("=" * 60)
    print("Forecast Scorecard Bridge — Validation")
    print("=" * 60)

    all_ok = True

    # 1. AST-guard
    print("\n[1/4] AST-guard: check_no_broker_imports")
    bridge_path = os.path.join(_CODE_DIR, "forecast_scorecard_bridge.py")
    if os.path.exists(bridge_path):
        result = check_no_broker_imports(bridge_path)
        status = "OK" if result else "FAIL"
        print(f"  [{status}] {bridge_path}")
        all_ok = all_ok and result
    else:
        print(f"  [FAIL] File not found: {bridge_path}")
        all_ok = False

    # 2. py_compile
    print("\n[2/4] py_compile check")
    result = check_py_compile(bridge_path)
    status = "OK" if result else "FAIL"
    print(f"  [{status}] {bridge_path}")
    all_ok = all_ok and result

    # 3. Config guard
    print("\n[3/4] Config guard")
    config_path = os.path.join(os.path.dirname(_CODE_DIR), "config.json")
    result = check_config_guard(config_path)
    all_ok = all_ok and result

    # 4. Dry-run smoke test
    print("\n[4/4] Dry-run smoke test")
    result = dry_run_smoke_test()
    all_ok = all_ok and result

    # Summary
    print("\n" + "=" * 60)
    if all_ok:
        print("RESULT: ALL CHECKS PASSED")
        return 0
    else:
        print("RESULT: SOME CHECKS FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())

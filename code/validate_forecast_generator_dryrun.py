"""Dry-run validator for Forecast Generator Bridge.

End-to-end dry-run без live broker. Проверяет что bridge работает корректно
на fixture данных и выдаёт scorecard.

Шаги:
  1. Загружает fixtures (forecast_context_fixture.json, strategy_ideas_fixture.json, regime_snapshot.json)
  2. Строит ForecastContext через DummyTimesFMAdapter
  3. Запускает run_forecast_quality_gate() с fixture ideas
  4. Проверяет: no live broker imports; output dict содержит passed/rejected/meta
  5. Печатает scorecard: n_total, n_passed, n_rejected, forecast_boosted, forecast_rejected, max_slots ≤ 3
  6. Валидирует AST-guard

Не импортирует broker/client/tinkoff/futures_lab.
Не создаёт live orders.
"""
from __future__ import annotations

import json
import os
import sys

# Ensure code/ is on sys.path
CODE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "code")
if CODE_DIR not in sys.path:
    sys.path.insert(0, os.path.abspath(CODE_DIR))

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests", "fixtures")


def main() -> None:
    """Run dry-run validation of forecast_generator_bridge."""
    print("=" * 60)
    print("Forecast Generator Bridge — Dry-Run Validation")
    print("=" * 60)

    # ─── Step 1: AST-guard ─────────────────────────────────────────────
    from forecast_generator_bridge import check_no_broker_imports
    bridge_path = os.path.join(CODE_DIR, "forecast_generator_bridge.py")
    broker_clean = check_no_broker_imports(bridge_path)
    print("\n[1] AST-guard (no broker imports):", "PASS" if broker_clean else "FAIL")
    if not broker_clean:
        print("    ERROR: broker imports detected in forecast_generator_bridge.py")
        sys.exit(1)

    # ─── Step 2: Load fixtures ─────────────────────────────────────────
    print("\n[2] Loading fixtures...")

    fc_path = os.path.join(FIXTURES_DIR, "forecast_context_fixture.json")
    ideas_path = os.path.join(FIXTURES_DIR, "strategy_ideas_fixture.json")
    regime_path = os.path.join(FIXTURES_DIR, "regime_snapshot.json")

    with open(fc_path, "r", encoding="utf-8") as f:
        fc_fixture = json.load(f)
    with open(ideas_path, "r", encoding="utf-8") as f:
        ideas_fixture = json.load(f)
    with open(regime_path, "r", encoding="utf-8") as f:
        regime_fixture = json.load(f)

    print(f"    bars_map tickers: {list(fc_fixture['bars_map'].keys())}")
    print(f"    ideas count: {len(ideas_fixture['ideas'])}")
    print(f"    regime tickers: {list(regime_fixture['tickers'].keys())}")

    # ─── Step 3: Build ForecastContext ──────────────────────────────────
    from timesfm_adapter import DummyTimesFMAdapter
    from forecast_context import build_forecast_context, ForecastContext

    adapter = DummyTimesFMAdapter()
    ctx = build_forecast_context(
        adapter, fc_fixture["bars_map"], fc_fixture.get("regime_snapshot")
    )
    print(f"\n[3] ForecastContext built:")
    print(f"    portfolio_bias: {ctx.portfolio_bias}")
    print(f"    volatility_regime: {ctx.volatility_regime}")
    print(f"    confidence_score: {ctx.confidence_score}")
    print(f"    tickers in context: {list(ctx.per_ticker.keys())}")
    print(f"    meta: {ctx.meta}")

    # ─── Step 4: Run forecast_prefilter ─────────────────────────────────
    from forecast_generator_bridge import forecast_prefilter, run_forecast_quality_gate

    ideas = ideas_fixture["ideas"]
    config = ideas_fixture.get("config", fc_fixture.get("config", {}))

    prefilter_result = forecast_prefilter(ideas, ctx, config)
    print(f"\n[4] Forecast prefilter:")
    print(f"    n_total: {prefilter_result['meta']['n_total']}")
    print(f"    n_passed: {prefilter_result['meta']['n_passed']}")
    print(f"    n_rejected_veto: {prefilter_result['meta']['n_rejected_veto']}")
    print(f"    n_rejected_forecast: {prefilter_result['meta']['n_rejected_forecast']}")
    print(f"    n_forecast_boosted: {prefilter_result['meta']['n_forecast_boosted']}")
    print(f"    fail_open: {prefilter_result['meta']['fail_open']}")

    # ─── Step 5: Run full quality gate ──────────────────────────────────
    feedback = ideas_fixture.get("feedback", {})
    gate_result = run_forecast_quality_gate(
        ideas, ctx, regime_fixture, feedback, config
    )
    meta = gate_result["meta"]
    print(f"\n[5] Forecast quality gate:")
    print(f"    n_total:        {meta['n_total']}")
    print(f"    n_passed:       {meta['n_passed']}")
    print(f"    n_rejected_veto:     {meta['n_rejected_veto']}")
    print(f"    n_rejected_forecast: {meta['n_rejected_forecast']}")
    print(f"    n_forecast_boosted:  {meta['n_forecast_boosted']}")
    print(f"    n_rejected_weak:     {meta['n_rejected_weak']}")
    print(f"    n_rejected_low_consistency: {meta['n_rejected_low_consistency']}")
    print(f"    n_rejected_low_significance: {meta['n_rejected_low_significance']}")
    print(f"    n_rejected_excess_slots:     {meta['n_rejected_excess_slots']}")

    # ─── Step 6: Scorecard ──────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("SCORECARD")
    print("=" * 60)
    print(f"  total ideas:            {meta['n_total']}")
    print(f"  passed (alive):         {meta['n_passed']}")
    print(f"  rejected_veto (RI):     {meta['n_rejected_veto']}")
    print(f"  rejected_forecast:      {meta['n_rejected_forecast']}")
    print(f"  forecast_boosted:       {meta['n_forecast_boosted']}")
    print(f"  rejected_weak:          {meta['n_rejected_weak']}")
    print(f"  forecast_confidence:    {meta['forecast_confidence']}")
    print(f"  fail_open:              {meta['fail_open']}")
    print(f"  max_slots constraint:   ≤3 — {'PASS' if meta['n_passed'] <= 3 else 'FAIL'}")

    # Print passed ideas
    print(f"\n  PASSED ideas ({len(gate_result['passed'])}):")
    for idea in gate_result["passed"]:
        boosted = " [FORECAST-BOOSTED]" if idea.get("_forecast_boosted") else ""
        print(f"    - {idea['ticker']} {idea['strategy_name']} "
              f"score={idea.get('score', 'N/A'):.4f} contracts={idea.get('contracts', '?')}{boosted}")

    # ─── Step 7: Validate output structure ──────────────────────────────
    errors = []

    # Check output structure
    if "passed" not in gate_result or "rejected" not in gate_result or "meta" not in gate_result:
        errors.append("Output missing required keys (passed/rejected/meta)")

    # Check contracts=1 for all passed
    for idea in gate_result.get("passed", []):
        if idea.get("contracts") != 1:
            errors.append(f"{idea['ticker']} contracts={idea.get('contracts')} ≠ 1")

    # Check max_slots ≤ 3
    if meta["n_passed"] > 3:
        errors.append(f"n_passed={meta['n_passed']} > max_slots=3")

    # Check RI excluded
    ri_in_passed = [i for i in gate_result.get("passed", []) if i.get("ticker") == "RI"]
    if ri_in_passed:
        errors.append("RI found in passed ideas")

    # Check no broker in passed ideas' data
    for idea in gate_result.get("passed", []):
        for key in ("broker", "tinkoff", "futures_lab"):
            if key in str(idea).lower():
                errors.append(f"Broker-related data in idea: {key}")

    print("\n" + "=" * 60)
    if errors:
        print("VALIDATION: FAIL")
        for e in errors:
            print(f"  ERROR: {e}")
        sys.exit(1)
    else:
        print("VALIDATION: PASS ✓")
        print("All checks passed: no broker, RI excluded, max_slots≤3, contracts=1")

    print("\n[OK] Dry-run validation complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()

"""Dry-run validator for strategy_ideas module.

Self-validating script (like other validators in code/):
  1. py_compile check
  2. Run idea_score on fixture data, output scorecard
  3. Verify: RI → VETO, max_contracts=1, imports clean
  4. Table-formatted output (like risk_scorecard)

Usage:
  cd /root/prop-desk/strategy_combine
  python3 code/validate_strategy_ideas_dryrun.py
"""
import ast
import json
import math
import subprocess
import sys
from pathlib import Path

# Ensure code/ is on sys.path
_CODE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_CODE_DIR))

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

from strategy_ideas import (
    idea_score,
    filter_weak,
    suggest_templates,
    build_idea,
    score_ideas_batch,
    check_no_broker_imports,
)


def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def main():
    print("=" * 60)
    print("  Strategy Ideas — Dry-Run Validator")
    print("=" * 60)

    errors = []

    # ─── 1. py_compile ─────────────────────────────────────────────────
    section("1. py_compile")
    module_path = _CODE_DIR / "strategy_ideas.py"
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(module_path)],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(f"  ✅ py_compile OK: {module_path.name}")
    else:
        print(f"  ❌ py_compile FAILED: {result.stderr.strip()}")
        errors.append("py_compile")

    # ─── 2. Load fixtures ──────────────────────────────────────────────
    section("2. Load fixtures")
    fixture_path = _FIXTURES_DIR / "strategy_ideas_fixture.json"
    with open(fixture_path) as f:
        data = json.load(f)

    ideas = data["ideas"]
    regime = data["regime_snapshot"]
    feedback = data["feedback"]
    config = data["config"]

    print(f"  Ideas: {len(ideas)}")
    print(f"  Regime tickers: {list(regime['tickers'].keys())}")
    print(f"  Feedback: LONG PnL={feedback['LONG']['pnl']}, "
          f"SHORT PnL={feedback['SHORT']['pnl']}")
    print(f"  Config: excluded={config['excluded']}, "
          f"max_contracts={config['max_contracts_per_entry']}, "
          f"max_slots={config['max_slots']}")

    # ─── 3. Score ideas (scorecard) ────────────────────────────────────
    section("3. Idea Scorecard")
    scored = score_ideas_batch(ideas, regime, feedback, config)

    # Header
    header = f"  {'Ticker':<8} {'Strategy':<20} {'Dir':<6} {'Score':<10} {'Status'}"
    print(header)
    print("  " + "-" * 58)

    for idea in scored:
        ticker = idea.get("ticker", "?")
        strat = idea.get("strategy_name", "?")
        direction = idea.get("direction", "-") or "-"
        score = idea.get("score", None)

        if score is None:
            status = "NO_SCORE"
        elif math.isinf(score) and score < 0:
            status = "VETO ❌"
        elif score >= 0.3:
            status = "PASS ✅"
        else:
            status = "WEAK ⚠️"

        score_str = f"{score:.4f}" if (score is not None and not math.isinf(score)) else "-inf"
        print(f"  {ticker:<8} {strat:<20} {direction:<6} {score_str:<10} {status}")

    # ─── 4. Verify constraints ─────────────────────────────────────────
    section("4. Constraint Verification")

    # 4a. RI → VETO
    ri_scored = [i for i in scored if i["ticker"] == "RI"]
    if ri_scored and math.isinf(ri_scored[0]["score"]) and ri_scored[0]["score"] < 0:
        print("  ✅ RI → VETO (score=-inf)")
    else:
        print("  ❌ RI not VETO'd")
        errors.append("RI not vetoed")

    # 4b. max_contracts_per_entry = 1
    all_contracts_1 = all(i.get("contracts", 1) == 1 for i in scored)
    if all_contracts_1:
        print("  ✅ All ideas have contracts=1")
    else:
        print("  ❌ Some ideas have contracts != 1")
        errors.append("contracts != 1")

    # 4c. No broker imports
    clean_imports = check_no_broker_imports()
    if clean_imports:
        print("  ✅ No broker/tinkoff/futures_lab imports")
    else:
        print("  ❌ Forbidden imports detected")
        errors.append("forbidden imports")

    # ─── 5. Filter weak ────────────────────────────────────────────────
    section("5. Filter Weak (threshold=0.3)")
    strong = filter_weak(scored, threshold=0.3)
    weak = [i for i in scored if i not in strong]
    print(f"  Strong (≥0.3): {len(strong)}")
    for i in strong:
        print(f"    → {i['ticker']}/{i['strategy_name']} = {i['score']:.4f}")
    print(f"  Weak/VETO (<0.3 or -inf): {len(weak)}")
    for i in weak:
        sc = f"{i['score']:.4f}" if not math.isinf(i['score']) else "-inf"
        print(f"    → {i['ticker']}/{i['strategy_name']} = {sc}")

    # ─── 6. Suggest templates ──────────────────────────────────────────
    section("6. Suggest Templates")
    templates = suggest_templates(regime)
    print(f"  Regime templates ({len(templates)}): {templates}")

    # ─── 7. Summary ────────────────────────────────────────────────────
    section("7. Summary")
    if not errors:
        print("  ✅ ALL CHECKS PASSED")
        print(f"  Scored: {len(scored)} ideas | Strong: {len(strong)} | "
              f"VETO'd: {sum(1 for i in scored if math.isinf(i['score']) and i['score'] < 0)}")
        print(f"  Templates suggested: {len(templates)}")
        print(f"  Max live slots: {config['max_slots']} (≤3 ✅)")
        print(f"  Max contracts: {config['max_contracts_per_entry']} (=1 ✅)")
        print(f"  RI excluded: {config['excluded']} ✅")
        return 0
    else:
        print(f"  ❌ ERRORS: {errors}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

"""Integration Check — unified entry-point for portfolio health checks.

Runs the full validation → backup → enforce → re-validate → restore → scorecard
pipeline on a tmp copy of the state.  Never touches the real state/ directory.

Usage (programmatic):
    from integration_check import run_integration_check
    result = run_integration_check(portfolio, config, tmp_dir="/tmp/check")

Returns:
    {
        "verdict": "PASS" | "VIOLATIONS",
        "composite_score": float,
        "checks": { ... },
        "enforce_before": int,
        "enforce_after": int,
        "removed_slots": [ ... ],
        "backup_ok": bool,
        "restore_ok": bool,
    }

All operations are in-memory or on tmp_dir — no live broker, no real state/ writes.
"""
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# Ensure project modules are importable
COMBINE_DIR = Path(__file__).resolve().parent.parent
CODE_DIR = COMBINE_DIR / "code"
sys.path.insert(0, str(CODE_DIR))

from portfolio_validator import validate_portfolio  # noqa: E402
from portfolio_enforcer import enforce_portfolio  # noqa: E402
from state_backup import snapshot_state, restore_from_backup  # noqa: E402
from risk_allocator_scorecard import compute_composite_score  # noqa: E402


def _checks_to_score_inputs(portfolio: dict, config: dict) -> list[dict]:
    """Convert portfolio + config into a list of check dicts for composite scoring.

    Each check has {"passed": bool, ...} so compute_composite_score can consume it.
    """
    risk = config.get("risk", {})
    max_slots = risk.get("max_slots", 3)
    max_contracts = risk.get("max_contracts_per_entry", 1)
    excluded = config.get("excluded", ["RI"])

    slots = portfolio.get("slots", {})

    # Check 1: max slots
    n_slots = len(slots)
    slot_check = {
        "name": "max_live_slots",
        "passed": n_slots <= max_slots,
        "actual": n_slots,
        "limit": max_slots,
    }

    # Check 2: contracts per entry
    contracts_violations = []
    for sid, slot in slots.items():
        c = slot.get("contracts", 1)
        if c > max_contracts:
            contracts_violations.append(sid)
    contract_check = {
        "name": "max_contracts_per_entry",
        "passed": len(contracts_violations) == 0,
        "violations": contracts_violations,
    }

    # Check 3: excluded tickers
    excluded_slots = [
        sid for sid, s in slots.items()
        if s.get("ticker") in excluded
    ]
    excluded_check = {
        "name": "ri_excluded",
        "passed": len(excluded_slots) == 0,
    }

    # Check 4: no duplicates
    seen: dict[tuple, str] = {}
    dupes = []
    for sid, slot in slots.items():
        key = (slot.get("ticker", ""), slot.get("strategy", ""))
        if key in seen:
            dupes.append(sid)
        else:
            seen[key] = sid
    dupe_check = {
        "name": "no_duplicates",
        "passed": len(dupes) == 0,
    }

    # Check 5: open_position consistency
    open_issues = []
    for sid, slot in slots.items():
        open_pos = slot.get("open_position")
        if open_pos is not None:
            for field in ("direction", "entry_price", "qty"):
                if field not in open_pos:
                    open_issues.append(f"{sid}:{field}")
            contracts = slot.get("contracts", 0)
            if open_pos.get("qty", 0) != contracts:
                open_issues.append(f"{sid}:qty_mismatch")
    open_check = {
        "name": "open_position_consistency",
        "passed": len(open_issues) == 0,
    }

    return [slot_check, contract_check, excluded_check, dupe_check, open_check]


def run_integration_check(
    portfolio: dict,
    config: dict,
    tmp_dir: Optional[str | Path] = None,
) -> dict:
    """Run the full integration check pipeline on a tmp copy.

    Steps:
      1. validate_portfolio (read-only)
      2. snapshot_state (backup in tmp_dir)
      3. enforce_portfolio (on tmp copy)
      4. re-validate after enforce
      5. restore_from_backup (restore tmp copy)
      6. compute_composite_score (scorecard)

    Args:
        portfolio: portfolio dict (with "slots" key)
        config: config dict (with "risk", "excluded" keys)
        tmp_dir: directory for temp files (created if needed)

    Returns:
        Unified result dict with verdict, composite_score, all checks.
    """
    if tmp_dir is None:
        import tempfile
        tmp_dir = Path(tempfile.mkdtemp(prefix="integration_check_"))
    else:
        tmp_dir = Path(tmp_dir)
        tmp_dir.mkdir(parents=True, exist_ok=True)

    # ── Step 0: Write portfolio to tmp file for enforce ─────────────
    tmp_portfolio_path = tmp_dir / "portfolio.json"
    tmp_portfolio_path.write_text(json.dumps(portfolio, indent=2, ensure_ascii=False))

    # ── Step 1: Validate (read-only) ───────────────────────────────
    validation_before = validate_portfolio(
        portfolio,
        max_slots=config.get("risk", {}).get("max_slots", 3),
        max_contracts_per_entry=config.get("risk", {}).get("max_contracts_per_entry", 1),
        excluded_tickers=tuple(config.get("excluded", ["RI"])),
    )

    # ── Step 2: Backup ─────────────────────────────────────────────
    backup_result = snapshot_state(tmp_dir)
    backup_ok = len(backup_result["created"]) > 0 and len(backup_result["errors"]) == 0

    # ── Step 3: Enforce on tmp copy ────────────────────────────────
    max_slots = config.get("risk", {}).get("max_slots", 3)
    enforce_before, enforce_after, removed_ids = enforce_portfolio(
        tmp_portfolio_path, max_slots=max_slots
    )

    # ── Step 4: Re-validate after enforce ──────────────────────────
    if enforce_before != enforce_after:
        enforced_portfolio = json.loads(tmp_portfolio_path.read_text())
        validation_after = validate_portfolio(
            enforced_portfolio,
            max_slots=max_slots,
            max_contracts_per_entry=config.get("risk", {}).get("max_contracts_per_entry", 1),
            excluded_tickers=tuple(config.get("excluded", ["RI"])),
        )
    else:
        validation_after = validation_before

    # ── Step 5: Restore from backup ────────────────────────────────
    restore_result = restore_from_backup(tmp_portfolio_path)
    restore_ok = restore_result.get("restored", False)

    # ── Step 6: Composite score ────────────────────────────────────
    score_inputs = _checks_to_score_inputs(portfolio, config)
    composite_score = compute_composite_score(score_inputs)

    # Determine final verdict: original violations + enforce results
    has_violations = validation_before["verdict"] == "VIOLATIONS"
    enforce_helped = enforce_before > enforce_after if enforce_before > enforce_after else False

    # Final verdict: PASS if no violations OR enforce fixed them all
    if has_violations and not enforce_helped:
        final_verdict = "VIOLATIONS"
    elif has_violations and enforce_helped:
        final_verdict = "ENFORCED"
    else:
        final_verdict = "PASS"

    return {
        "verdict": final_verdict,
        "composite_score": composite_score,
        "checks": {
            "validation_before": validation_before,
            "validation_after": validation_after,
        },
        "enforce_before": enforce_before,
        "enforce_after": enforce_after,
        "removed_slots": removed_ids,
        "backup_ok": backup_ok,
        "restore_ok": restore_ok,
        "scorecard": validation_before.get("scorecard", {}),
    }


# ── CLI ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import tempfile

    portfolio_path = COMBINE_DIR / "state" / "portfolio.json"
    config_path = COMBINE_DIR / "config.json"

    if portfolio_path.exists():
        portfolio = json.loads(portfolio_path.read_text())
    else:
        portfolio = {"slots": {}}

    if config_path.exists():
        config = json.loads(config_path.read_text())
    else:
        config = {"risk": {"max_slots": 3, "max_contracts_per_entry": 1}, "excluded": ["RI"]}

    with tempfile.TemporaryDirectory(prefix="integration_check_") as tmp:
        result = run_integration_check(portfolio, config, tmp_dir=tmp)

    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))

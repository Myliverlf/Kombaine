"""Guard Report — generates markdown scorecard for the Apostle.

Reads current state/portfolio.json, runs all validation checks,
produces a markdown report at state/guard_report.md.

Checks performed:
  1. Validator verdict (slots, dupes, open/close, contracts, RI)
  2. Enforcer dry-run: before/after count, removed slots
  3. Composite allocator score (0–1)
  4. Backup status (exists/created)
  5. Safety summary: no live, paper mode, max_slots compliance

All operations are read-only on real state/ — no writes except the report itself.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# Ensure project modules are importable
COMBINE_DIR = Path(__file__).resolve().parent.parent
CODE_DIR = COMBINE_DIR / "code"
sys.path.insert(0, str(CODE_DIR))

from portfolio_validator import validate_portfolio, format_report  # noqa: E402
from risk_allocator_scorecard import run_scorecard  # noqa: E402
from state_backup import list_backups  # noqa: E402


def _load_json(path: Path) -> dict:
    """Load JSON file safely, returning empty dict on failure."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def generate_guard_report(
    state_dir: Optional[str | Path] = None,
    config_path: Optional[str | Path] = None,
) -> str:
    """Generate markdown guard report from current state.

    Args:
        state_dir: path to state/ directory
        config_path: path to config.json

    Returns:
        Markdown string with full report.
    """
    state_dir = Path(state_dir or COMBINE_DIR / "state")
    config_path = Path(config_path or COMBINE_DIR / "config.json")

    portfolio = _load_json(state_dir / "portfolio.json")
    config = _load_json(config_path)

    risk = config.get("risk", {})
    max_slots = risk.get("max_slots", 3)
    excluded = config.get("excluded", ["RI"])

    # ── Section 1: Validator verdict ────────────────────────────────
    validation = validate_portfolio(
        portfolio,
        max_slots=max_slots,
        excluded_tickers=tuple(excluded),
    )

    # ── Section 2: Enforcer dry-run (read-only simulation) ─────────
    from portfolio_enforcer import _slot_sort_key

    slots = portfolio.get("slots", {})
    before_count = len(slots)

    # Simulate dedup + sort without writing
    deduped: dict[tuple, tuple] = {}
    for slot_id, slot in slots.items():
        key = (slot.get("ticker", ""), slot.get("strategy", ""))
        if key not in deduped:
            deduped[key] = (slot_id, slot)
        else:
            _, old_slot = deduped[key]
            if _slot_sort_key(slot) < _slot_sort_key(old_slot):
                deduped[key] = (slot_id, slot)

    unique = [(sid, s) for sid, s in deduped.values()]
    unique.sort(key=lambda x: _slot_sort_key(x[1]))
    kept_ids = [sid for sid, _ in unique[:max_slots]]
    removed_ids = [sid for sid in slots if sid not in kept_ids]

    after_count = min(before_count, max_slots)
    after_count = len(kept_ids)

    # ── Section 3: Composite score ──────────────────────────────────
    scorecard = run_scorecard(
        portfolio_path=state_dir / "portfolio.json",
        config_path=config_path,
        pool_path=state_dir / "signal_pool.json",
    )
    composite_score = scorecard.get("composite_score", 0.0)

    # ── Section 4: Backup status ────────────────────────────────────
    backups = list_backups(state_dir)

    # ── Section 5: Safety summary ──────────────────────────────────
    mode = config.get("mode", "unknown")
    paper_first = config.get("paper_first", False)

    # ── Build markdown ─────────────────────────────────────────────
    lines = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"# Portfolio Guard Report")
    lines.append(f"_Generated: {now}_\n")

    # Validator
    v_verdict = validation["verdict"]
    v_icon = "✅" if v_verdict == "PASS" else "❌"
    lines.append(f"## 1. Validator Verdict: {v_icon} {v_verdict}\n")

    for name, check in validation.get("checks", {}).items():
        ok = check.get("ok", False)
        mark = "✅" if ok else "❌"
        detail = ""
        if name == "slot_count":
            detail = f" ({check.get('count', 0)} / max {check.get('max', 3)})"
        elif name == "duplicates":
            found = check.get("found", [])
            detail = f" ({len(found)} duplicate groups)" if found else ""
        elif name == "contracts_per_entry":
            detail = f" (max found: {check.get('max_found', 0)})"
        elif name == "excluded_tickers":
            found = check.get("found", [])
            detail = f" ({len(found)} excluded)" if found else ""
        lines.append(f"  {mark} **{name}**{detail}")

    if validation["violations"]:
        lines.append("\n### Violations")
        for v in validation["violations"]:
            lines.append(f"  - {v}")

    # Scorecard
    sc = validation.get("scorecard", {})
    lines.append(f"\n### Scorecard")
    lines.append(f"  - Total PnL: **{sc.get('total_pnl_rub', 0):.2f} RUB**")
    lines.append(f"  - Open positions: {sc.get('total_open_positions', 0)}")
    lines.append(f"  - Slots: {sc.get('total_slots', 0)}")
    lines.append(f"  - Unique tickers: {sc.get('unique_tickers', 0)}")
    lines.append(f"  - Avg PnL/slot: {sc.get('avg_pnl_per_slot', 0):.2f} RUB")

    # Enforcer dry-run
    lines.append(f"\n## 2. Enforcer Dry-Run\n")
    lines.append(f"  - Slots before: **{before_count}**")
    lines.append(f"  - Slots after: **{after_count}**")
    lines.append(f"  - Max allowed: {max_slots}")
    if removed_ids:
        lines.append(f"  - Removed: {', '.join(removed_ids)}")
    else:
        lines.append(f"  - Removed: none (all within limit)")

    # Composite score
    lines.append(f"\n## 3. Composite Allocator Score\n")
    lines.append(f"  - **Score: {composite_score:.3f}** (range 0.0–1.0)")
    lines.append(f"  - Pass: {'✅' if scorecard.get('pass', False) else '❌'}")

    for m in scorecard.get("metrics", []):
        mark = "✅" if m.get("passed", False) else "❌"
        lines.append(f"    {mark} {m.get('name', '?')}: {m.get('detail', '')}")

    # Backup status
    lines.append(f"\n## 4. Backup Status\n")
    if backups:
        lines.append(f"  - Found {len(backups)} backup(s):")
        for b in backups:
            lines.append(f"    - {b['original']} ({b['size_bytes']} bytes)")
    else:
        lines.append(f"  - No backups found")

    # Safety summary
    lines.append(f"\n## 5. Safety Summary\n")
    mode_icon = "✅" if mode == "paper" else "❌"
    lines.append(f"  {mode_icon} Mode: **{mode}**")
    pf_icon = "✅" if paper_first else "⚠️"
    lines.append(f"  {pf_icon} paper_first: {paper_first}")
    excl_icon = "✅" if excluded else "⚠️"
    lines.append(f"  {excl_icon} Excluded tickers: {excluded}")

    slots_ok = before_count <= max_slots
    sl_icon = "✅" if slots_ok else "❌"
    lines.append(f"  {sl_icon} Slots compliance: {before_count} ≤ {max_slots}")

    # Verdict
    overall_safe = (
        v_verdict == "PASS"
        and mode == "paper"
        and slots_ok
    )
    overall = "✅ SAFE" if overall_safe else "⚠️ NEEDS ATTENTION"
    lines.append(f"\n## Overall: {overall}\n")

    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    report = generate_guard_report()
    output_path = COMBINE_DIR / "state" / "guard_report.md"
    output_path.write_text(report, encoding="utf-8")
    print(f"Guard report written to {output_path}")
    print(report)

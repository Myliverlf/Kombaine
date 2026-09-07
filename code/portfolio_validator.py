"""Portfolio Validator — read-only валидатор portfolio.json.

Проверяет:
  1. slots <= max_slots
  2. нет дублей (ticker, strategy)
  3. open_position consistency (open слоты не orphan, закрытые не имеют open_position)
  4. contracts <= max_contracts_per_entry
  5. RI excluded (ticker "RI" не должен быть в слотах)
  6. composite PnL/risk scorecard через allocator_metrics

Чистые функции: dict-in → dict-out. Не пишет в state/, не вызывает broker.

Источники:
  - analysis.md §2.3 (8 слотов, дубли GAZP×4)
  - plan.md Фича 1
  - config.json risk.max_slots=3, risk.max_contracts_per_entry=1, excluded=["RI"]
"""
from typing import Any, Dict, List, Optional, Tuple


# ── Defaults (mirror config.json) ──────────────────────────────────
DEFAULT_MAX_SLOTS = 3
DEFAULT_MAX_CONTRACTS_PER_ENTRY = 1
DEFAULT_EXCLUDED_TICKERS = ("RI",)


def validate_portfolio(
    portfolio: dict,
    max_slots: int = DEFAULT_MAX_SLOTS,
    max_contracts_per_entry: int = DEFAULT_MAX_CONTRACTS_PER_ENTRY,
    excluded_tickers: Tuple[str, ...] = DEFAULT_EXCLUDED_TICKERS,
) -> dict:
    """Validate portfolio dict. Returns structured verdict dict.

    Args:
        portfolio: dict with "slots" key (dict of slot_id -> slot_data)
        max_slots: maximum allowed slots
        max_contracts_per_entry: max contracts per slot entry
        excluded_tickers: tickers that must not appear in portfolio

    Returns:
        {
            "verdict": "PASS" | "VIOLATIONS",
            "violations": [...],
            "checks": {
                "slot_count": {"count": N, "max": M, "ok": bool},
                "duplicates": {"found": [...], "ok": bool},
                "open_position_consistency": {"issues": [...], "ok": bool},
                "contracts_per_entry": {"max_found": N, "ok": bool},
                "excluded_tickers": {"found": [...], "ok": bool},
            },
            "scorecard": {
                "total_pnl_rub": float,
                "total_open_positions": int,
                "total_slots": int,
                "unique_tickers": int,
                "unique_strategies": int,
                "slots_with_pnl": int,
                "avg_pnl_per_slot": float,
            },
        }
    """
    slots = portfolio.get("slots", {})
    if not isinstance(slots, dict):
        return {
            "verdict": "VIOLATIONS",
            "violations": ["slots key is missing or not a dict"],
            "checks": {},
            "scorecard": _empty_scorecard(),
        }

    violations: List[str] = []
    checks: Dict[str, Any] = {}

    # ── Check 1: slot count ────────────────────────────────────────
    slot_count = len(slots)
    count_ok = slot_count <= max_slots
    checks["slot_count"] = {
        "count": slot_count,
        "max": max_slots,
        "ok": count_ok,
    }
    if not count_ok:
        violations.append(
            f"slot_count={slot_count} exceeds max_slots={max_slots}"
        )

    # ── Check 2: duplicates (ticker, strategy) ─────────────────────
    seen: Dict[Tuple[str, str], List[str]] = {}
    for slot_id, slot in slots.items():
        key = (slot.get("ticker", ""), slot.get("strategy", ""))
        seen.setdefault(key, []).append(slot_id)
    dupes = {k: v for k, v in seen.items() if len(v) > 1}
    dupes_ok = len(dupes) == 0
    checks["duplicates"] = {
        "found": [
            {"ticker": k[0], "strategy": k[1], "slot_ids": v}
            for k, v in dupes.items()
        ],
        "ok": dupes_ok,
    }
    if not dupes_ok:
        for (ticker, strategy), ids in dupes.items():
            violations.append(
                f"duplicate ({ticker}, {strategy}): {len(ids)} slots {ids}"
            )

    # ── Check 3: open_position consistency ─────────────────────────
    open_issues: List[str] = []
    for slot_id, slot in slots.items():
        open_pos = slot.get("open_position")
        has_pnl = slot.get("pnl_rub", 0.0) != 0
        n_trades = slot.get("n_trades", 0)
        if open_pos is not None:
            # Open slot: should have direction, entry_price, qty
            missing_fields = []
            for field in ("direction", "entry_price", "qty"):
                if field not in open_pos:
                    missing_fields.append(field)
            if missing_fields:
                open_issues.append(
                    f"slot {slot_id} open_position missing: {missing_fields}"
                )
            # Verify qty matches contracts
            contracts = slot.get("contracts", 0)
            if open_pos.get("qty", 0) != contracts:
                open_issues.append(
                    f"slot {slot_id}: open_position.qty={open_pos.get('qty')} "
                    f"!= contracts={contracts}"
                )
        else:
            # Closed slot: n_trades can be 0 (never traded), that's fine
            # But if open_position is null and n_trades > 0, slot is "closed" — valid
            pass
    open_ok = len(open_issues) == 0
    checks["open_position_consistency"] = {
        "issues": open_issues,
        "ok": open_ok,
    }
    if not open_ok:
        violations.extend(open_issues)

    # ── Check 4: contracts per entry ───────────────────────────────
    max_contracts_found = 0
    contracts_violations: List[str] = []
    for slot_id, slot in slots.items():
        c = slot.get("contracts", 0)
        if c > max_contracts_found:
            max_contracts_found = c
        if c > max_contracts_per_entry:
            contracts_violations.append(
                f"slot {slot_id}: contracts={c} > max={max_contracts_per_entry}"
            )
    contracts_ok = len(contracts_violations) == 0
    checks["contracts_per_entry"] = {
        "max_found": max_contracts_found,
        "max_allowed": max_contracts_per_entry,
        "ok": contracts_ok,
    }
    if not contracts_ok:
        violations.extend(contracts_violations)

    # ── Check 5: excluded tickers ──────────────────────────────────
    excluded_found: List[str] = []
    for slot_id, slot in slots.items():
        ticker = slot.get("ticker", "")
        if ticker in excluded_tickers:
            excluded_found.append(f"slot {slot_id}: ticker={ticker}")
    excluded_ok = len(excluded_found) == 0
    checks["excluded_tickers"] = {
        "excluded_list": list(excluded_tickers),
        "found": excluded_found,
        "ok": excluded_ok,
    }
    if not excluded_ok:
        for issue in excluded_found:
            violations.append(f"excluded ticker found: {issue}")

    # ── Scorecard ──────────────────────────────────────────────────
    scorecard = _compute_scorecard(slots)

    verdict = "VIOLATIONS" if violations else "PASS"
    return {
        "verdict": verdict,
        "violations": violations,
        "checks": checks,
        "scorecard": scorecard,
    }


def _compute_scorecard(slots: dict) -> dict:
    """Compute PnL/risk composite scorecard from slots."""
    total_pnl = 0.0
    open_positions = 0
    slots_with_pnl = 0
    tickers = set()
    strategies = set()

    for slot in slots.values():
        tickers.add(slot.get("ticker", ""))
        strategies.add(slot.get("strategy", ""))
        pnl = slot.get("pnl_rub", 0.0)
        total_pnl += pnl
        if pnl != 0:
            slots_with_pnl += 1
        if slot.get("open_position"):
            open_positions += 1

    n = len(slots) if slots else 1
    return {
        "total_pnl_rub": round(total_pnl, 2),
        "total_open_positions": open_positions,
        "total_slots": len(slots),
        "unique_tickers": len(tickers),
        "unique_strategies": len(strategies),
        "slots_with_pnl": slots_with_pnl,
        "avg_pnl_per_slot": round(total_pnl / n, 2),
    }


def _empty_scorecard() -> dict:
    return {
        "total_pnl_rub": 0.0,
        "total_open_positions": 0,
        "total_slots": 0,
        "unique_tickers": 0,
        "unique_strategies": 0,
        "slots_with_pnl": 0,
        "avg_pnl_per_slot": 0.0,
    }


def format_report(result: dict) -> str:
    """Format validation result as human-readable string."""
    lines = []
    lines.append(f"=== Portfolio Validation: {result['verdict']} ===")

    checks = result.get("checks", {})
    for name, check in checks.items():
        ok_str = "OK" if check.get("ok") else "FAIL"
        lines.append(f"  [{ok_str}] {name}")

    if result["violations"]:
        lines.append(f"\nViolations ({len(result['violations'])}):")
        for v in result["violations"]:
            lines.append(f"  - {v}")

    sc = result.get("scorecard", {})
    lines.append(f"\nScorecard:")
    lines.append(f"  Total PnL: {sc.get('total_pnl_rub', 0):.2f} RUB")
    lines.append(f"  Open positions: {sc.get('total_open_positions', 0)}")
    lines.append(f"  Slots: {sc.get('total_slots', 0)}")
    lines.append(f"  Unique tickers: {sc.get('unique_tickers', 0)}")
    lines.append(f"  Unique strategies: {sc.get('unique_strategies', 0)}")
    lines.append(f"  Avg PnL/slot: {sc.get('avg_pnl_per_slot', 0):.2f} RUB")

    return "\n".join(lines)


# ── Self-test ──────────────────────────────────────────────────────
if __name__ == "__main__":
    import json
    from pathlib import Path

    portfolio_path = Path("/root/prop-desk/strategy_combine/state/portfolio.json")
    if portfolio_path.exists():
        portfolio = json.loads(portfolio_path.read_text())
        result = validate_portfolio(portfolio)
        print(format_report(result))
    else:
        print("No portfolio.json found — running with empty portfolio")
        result = validate_portfolio({"slots": {}})
        print(format_report(result))

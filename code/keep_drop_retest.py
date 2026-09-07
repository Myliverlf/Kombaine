"""Keep/Drop/Retest scorecard for combine dry-runs.

Consumes performance bundles from perf_metrics.py or strategy_sweep.py and
adds the acceptance rules requested by the task:
- RI excluded
- max live slots <= 3
- 1 contract max per entry

Returns a deterministic decision bundle without broker/live side effects.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from perf_metrics import compute_performance_metrics


DEFAULT_MAX_LIVE_SLOTS = 3
DEFAULT_MAX_CONTRACTS_PER_ENTRY = 1
DEFAULT_EXCLUDED = {"RI"}


@dataclass(frozen=True)
class DecisionRow:
    ticker: str
    timeframe: str
    strategy: str
    decision: str
    reason: str


def _upper(value: Any) -> str:
    return str(value or "").upper()


def validate_constraints(
    portfolio: Mapping[str, Any],
    *,
    max_live_slots: int = DEFAULT_MAX_LIVE_SLOTS,
    max_contracts_per_entry: int = DEFAULT_MAX_CONTRACTS_PER_ENTRY,
    excluded: Iterable[str] = DEFAULT_EXCLUDED,
) -> Dict[str, Any]:
    """Validate the acceptance constraints on a dry-run portfolio."""
    excluded_set = {_upper(x) for x in excluded}
    slots = dict(portfolio.get("slots", {}))
    n_slots = len(slots)
    slot_violations: List[str] = []
    ri_slots: List[str] = []
    contracts_violations: List[str] = []

    for slot_id, slot in slots.items():
        ticker = _upper(slot.get("ticker"))
        contracts = int(slot.get("contracts", 1) or 1)
        if ticker in excluded_set:
            ri_slots.append(slot_id)
        if contracts > max_contracts_per_entry:
            contracts_violations.append(slot_id)
    if n_slots > max_live_slots:
        slot_violations.append(f"slots:{n_slots}>{max_live_slots}")

    passed = not slot_violations and not ri_slots and not contracts_violations
    return {
        "passed": passed,
        "n_slots": n_slots,
        "max_live_slots": max_live_slots,
        "ri_excluded": not ri_slots,
        "ri_slots": ri_slots,
        "contracts_ok": not contracts_violations,
        "contracts_violations": contracts_violations,
        "slot_violations": slot_violations,
    }


def _decision(metrics: Mapping[str, Any], constraint_ok: bool) -> tuple[str, str]:
    pnl = float(metrics.get("PnL", 0.0))
    pf = float(metrics.get("PF", metrics.get("profit_factor", 0.0)))
    wr = float(metrics.get("WR", metrics.get("win_rate", 0.0)))
    dd = float(metrics.get("DD", metrics.get("dd", 0.0)))
    sharpe = float(metrics.get("sharpe", 0.0))

    if not constraint_ok:
        return "drop", "constraint_violation"
    if pnl > 0 and pf >= 1.2 and wr >= 0.5 and dd <= 0.15:
        return "keep", "profitable_and_safe"
    if pnl < 0 or dd >= 0.22:
        return "drop", "risk_or_pnl_failed"
    if sharpe >= 0.25:
        return "retest", "needs_more_sample"
    return "drop", "below_threshold"


def build_keep_drop_retest_scorecard(
    strategy_rows: Sequence[Mapping[str, Any]],
    portfolio: Mapping[str, Any],
    *,
    constraints: Mapping[str, Any] | None = None,
    start_equity: float = 100_000.0,
) -> Dict[str, Any]:
    """Build a deterministic scorecard for keep/drop/retest."""
    constraint_summary = validate_constraints(portfolio, **dict(constraints or {}))
    rows: List[Dict[str, Any]] = []
    for row in strategy_rows:
        ticker = _upper(row.get("ticker"))
        timeframe = str(row.get("timeframe", ""))
        strategy = str(row.get("strategy", ""))
        trades = list(row.get("trades", []))
        metrics = compute_performance_metrics(trades, start_equity=row.get("start_equity", start_equity))
        decision, reason = _decision(metrics, constraint_summary["passed"] and ticker not in DEFAULT_EXCLUDED)
        rows.append(
            {
                "ticker": ticker,
                "timeframe": timeframe,
                "strategy": strategy,
                "decision": decision,
                "reason": reason,
                "metrics": metrics,
            }
        )

    summary = {
        "keep": sum(1 for row in rows if row["decision"] == "keep"),
        "drop": sum(1 for row in rows if row["decision"] == "drop"),
        "retest": sum(1 for row in rows if row["decision"] == "retest"),
    }
    scorecard = {
        "pnl_up": sum(1 for row in rows if row["metrics"]["PnL"] > 0),
        "risk_down": sum(1 for row in rows if row["metrics"]["DD"] <= 0.15),
    }
    return {
        "decisions": rows,
        "summary": summary,
        "constraints": constraint_summary,
        "scorecard": scorecard,
    }


__all__ = ["build_keep_drop_retest_scorecard", "validate_constraints", "DecisionRow"]

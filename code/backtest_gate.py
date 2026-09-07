"""Backtest Gate — primary filter before live admission.

Pure dict-in → dict-out, 0 broker imports, stdlib-only.
Filters candidates by composite_score threshold, enforces MAX_SLOTS=3,
MAX_CONTRACTS=1 per entry, and RI exclusion.

Usage:
    from backtest_gate import apply_gate
    result = apply_gate(candidates, gate_config)
    # result["passed"]  — candidates that passed backtest threshold
    # result["rejected"] — candidates that failed, with reasons
    # result["gate_meta"] — summary stats
"""
from __future__ import annotations

from typing import Any, Dict, List

# ── Constants ──
MAX_SLOTS = 3
MAX_CONTRACTS_PER_ENTRY = 1
DEFAULT_MIN_COMPOSITE_SCORE = 50.0
DEFAULT_EXCLUDED: List[str] = ["RI"]


def apply_gate(
    gate_candidates: List[Dict[str, Any]],
    gate_config: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Filter candidates through backtest gate.

    Args:
        gate_candidates: list of candidate dicts, each must have
            ``ticker`` (str) and ``composite_score`` (float).
            Optional: ``backtest_meta`` (dict with extra info).
        gate_config: gate configuration dict:
            - ``min_composite_score`` (float, default 50.0)
            - ``max_slots`` (int, default 3)
            - ``max_contracts_per_entry`` (int, default 1)
            - ``excluded`` (list[str], default ["RI"])

    Returns:
        {
            "passed": list[dict],   — candidates that passed all filters
            "rejected": list[dict], — {candidate, reason} for each rejection
            "gate_meta": {
                "n_passed": int,
                "n_rejected": int,
                "threshold": float,
                "max_slots": int,
                "max_contracts_per_entry": int,
                "excluded": list[str],
            }
        }
    """
    if gate_config is None:
        gate_config = {}

    min_score = gate_config.get("min_composite_score", DEFAULT_MIN_COMPOSITE_SCORE)
    max_slots = gate_config.get("max_slots", MAX_SLOTS)
    max_contracts = gate_config.get("max_contracts_per_entry", MAX_CONTRACTS_PER_ENTRY)
    excluded = gate_config.get("excluded", list(DEFAULT_EXCLUDED))

    # Phase 1: Exclusion filter (tickers in excluded list)
    passed = []
    rejected = []

    for c in gate_candidates:
        ticker = c.get("ticker", "")
        if ticker in excluded:
            rejected.append({
                "candidate": c,
                "reason": f"excluded_ticker:{ticker}",
            })
            continue

        # Phase 2: Composite score threshold
        score = c.get("composite_score", 0.0)
        if not isinstance(score, (int, float)):
            rejected.append({
                "candidate": c,
                "reason": f"invalid_composite_score:{score}",
            })
            continue

        if score < min_score:
            rejected.append({
                "candidate": c,
                "reason": f"below_threshold:{score}<{min_score}",
            })
            continue

        # Phase 3: Contracts cap
        contracts = c.get("contracts", 1)
        if contracts > max_contracts:
            # Clamp contracts down, still pass the candidate
            c = dict(c)
            c["contracts"] = max_contracts
            c["_gate_clamped"] = True

        passed.append(c)

    # Phase 4: Slot cap — keep top-N by composite_score
    if len(passed) > max_slots:
        # Sort descending by composite_score
        passed.sort(key=lambda x: x.get("composite_score", 0.0), reverse=True)
        overflow = passed[max_slots:]
        passed = passed[:max_slots]
        for c in overflow:
            rejected.append({
                "candidate": c,
                "reason": f"slot_cap_overflow:{max_slots}",
            })

    gate_meta = {
        "n_passed": len(passed),
        "n_rejected": len(rejected),
        "threshold": min_score,
        "max_slots": max_slots,
        "max_contracts_per_entry": max_contracts,
        "excluded": list(excluded),
    }

    return {
        "passed": passed,
        "rejected": rejected,
        "gate_meta": gate_meta,
    }

"""Direction enforcement + metrics validation for strategy registry.

Ensures every strategy record has an explicit direction (LONG/SHORT) or
a documented reason for UNKNOWN.  Validates that metrics dicts contain
the fields required by allocator_metrics and risk_allocator_scorecard_v2.

All functions are pure: dict-in → dict/list-out.  No broker, no state, no network.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


# ─── Direction policy ────────────────────────────────────────────────

class DirectionPolicy(str, Enum):
    """Allowed direction values for strategy records."""
    LONG = "LONG"
    SHORT = "SHORT"
    UNKNOWN = "UNKNOWN"


VALID_DIRECTIONS = {d.value for d in DirectionPolicy}


# ─── Metrics schema ──────────────────────────────────────────────────

REQUIRED_SCORING_METRICS: set[str] = {
    "win_rate",
    "avg_win",
    "avg_loss",
    "trades",
    "trades_per_day",
    "drawdown_pct",
}

# Extended set used by risk_allocator_scorecard_v2.compute_composite
OPTIONAL_SCORING_METRICS: set[str] = {
    "pnl",
    "sharpe",
    "pf",
    "volatility",
}


# ─── 1. Single record validation ─────────────────────────────────────

def validate_record_direction(record: Dict[str, Any]) -> Tuple[bool, str]:
    """Check that a strategy record has a valid direction or explicit UNKNOWN reason.

    Args:
        record: strategy record dict (from StrategyRecord.to_dict() or equivalent).

    Returns:
        (is_valid, reason):
          - is_valid=True  → direction is LONG/SHORT, or UNKNOWN with non-empty reason.
          - is_valid=False → reason describes what is wrong.

    Examples:
        >>> validate_record_direction({"direction": "LONG"})
        (True, '')
        >>> validate_record_direction({"direction": "UNKNOWN", "direction_unknown_reason": "low win rate"})
        (True, '')
        >>> validate_record_direction({"direction": "UNKNOWN"})
        (False, 'direction is UNKNOWN but direction_unknown_reason is empty')
        >>> validate_record_direction({})
        (False, 'direction is missing — add direction or direction_unknown_reason')
    """
    direction = record.get("direction")

    if direction in (DirectionPolicy.LONG.value, DirectionPolicy.SHORT.value):
        return True, ""

    if direction == DirectionPolicy.UNKNOWN.value:
        reason = record.get("direction_unknown_reason", "")
        if reason and isinstance(reason, str) and reason.strip():
            return True, ""
        return False, "direction is UNKNOWN but direction_unknown_reason is empty"

    if direction is None or direction == "":
        return False, "direction is missing — add direction or direction_unknown_reason"

    return False, f"direction is invalid: {direction!r} (expected LONG/SHORT/UNKNOWN)"


# ─── 2. Registry batch validation ───────────────────────────────────

def validate_registry_directions(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Validate direction for all records in a registry list.

    Args:
        records: list of strategy record dicts.

    Returns:
        List of error dicts: [{"strategy_id": str, "error": str}, ...].
        Empty list means all records are valid.
    """
    errors: List[Dict[str, str]] = []
    for rec in records:
        sid = rec.get("strategy_id", "<unknown>")
        is_valid, reason = validate_record_direction(rec)
        if not is_valid:
            errors.append({"strategy_id": sid, "error": reason})
    return errors


# ─── 3. Metrics schema validation ───────────────────────────────────

def validate_metrics_schema(metrics: Dict[str, Any]) -> List[str]:
    """Check that a metrics dict contains the required fields for scoring.

    Args:
        metrics: strategy metrics dict.

    Returns:
        List of missing field names.  Empty list means all required fields present.

    Examples:
        >>> validate_metrics_schema({"win_rate": 0.5, "avg_win": 100, "avg_loss": 50, "trades": 30, "trades_per_day": 1.5, "drawdown_pct": 5.0})
        []
        >>> sorted(validate_metrics_schema({"pnl": 1000}))
        ['avg_loss', 'avg_win', 'drawdown_pct', 'trades', 'trades_per_day', 'win_rate']
    """
    missing: List[str] = []
    for key in sorted(REQUIRED_SCORING_METRICS):
        if key not in metrics:
            missing.append(key)
    return missing


# ─── 4. Direction inference from strategy name ──────────────────────

# Patterns: first match wins
_INFER_PATTERNS: List[Tuple[re.Pattern[str], str]] = [
    (re.compile(r"mean_?rev|meanrev|reversion|bollinger|vwap|bband", re.IGNORECASE), DirectionPolicy.SHORT.value),
    (re.compile(r"trend|follow|breakout|momentum|macd", re.IGNORECASE), DirectionPolicy.LONG.value),
]


def infer_direction_from_strategy_name(
    name: str,
    params: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """Heuristically infer direction from strategy name keywords.

    Args:
        name: strategy name or description (e.g. "LKOH_nfi_trend").
        params: optional params dict (currently unused, reserved for future heuristics).

    Returns:
        (direction, reason):
          - direction: one of "LONG", "SHORT", "UNKNOWN".
          - reason: short human-readable explanation.

    Examples:
        >>> infer_direction_from_strategy_name("GAZP_trend_follow")
        ('LONG', 'inferred from name: trend keyword')
        >>> infer_direction_from_strategy_name("LKOH_mean_reversion_bband")
        ('SHORT', 'inferred from name: mean_reversion keyword')
        >>> infer_direction_from_strategy_name("some_random_name")
        ('UNKNOWN', 'no direction keyword found in name')
    """
    for pattern, direction in _INFER_PATTERNS:
        m = pattern.search(name)
        if m:
            keyword = m.group(0).lower()
            return direction, f"inferred from name: {keyword} keyword"

    return DirectionPolicy.UNKNOWN.value, "no direction keyword found in name"


# ─── 5. Batch enforcement (migration) ──────────────────────────────

def enforce_directions(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Batch enrich records that are missing direction.

    For each record without direction:
      1. Try infer_direction_from_strategy_name.
      2. If inference yields UNKNOWN → set direction=UNKNOWN, reason from inference.

    Records that already have a valid direction are returned unchanged.

    Args:
        records: list of strategy record dicts (will NOT be mutated — new dicts returned).

    Returns:
        New list of enriched record dicts.
    """
    enriched: List[Dict[str, Any]] = []
    for rec in records:
        is_valid, _reason = validate_record_direction(rec)
        if is_valid:
            enriched.append(dict(rec))
            continue

        # Try inference
        name = f"{rec.get('ticker', '')}_{rec.get('strategy', '')}"
        direction, reason = infer_direction_from_strategy_name(name, rec.get("params"))

        new_rec = dict(rec)
        new_rec["direction"] = direction
        if direction == DirectionPolicy.UNKNOWN.value:
            new_rec["direction_unknown_reason"] = reason
        enriched.append(new_rec)

    return enriched

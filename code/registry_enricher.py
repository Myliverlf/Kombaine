"""Registry Enricher — direction enforcement + metrics validation for strategy registry.

Pure module: dict-in → dict-out.  No broker, no I/O, no network.

Functions:
  - enrich_registry(registry) → enriched registry with direction + metrics notes
  - validate_registry_readiness(registry) → {passed, errors, warnings}

Dependencies:
  - code/direction_enforcer.py (enforce_directions, validate_metrics_schema,
    validate_registry_directions, REQUIRED_SCORING_METRICS)
"""
from typing import Any, Dict, List


# ─── 1. Enrich registry ─────────────────────────────────────────────

def _extract_strategies_as_list(registry: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract strategies from registry dict as a flat list.

    Registry stores strategies as dict keyed by strategy_id.
    This converts to list for enforce_directions() compatibility.
    """
    strategies = registry.get("strategies", {})
    records: List[Dict[str, Any]] = []
    for sid, rec in strategies.items():
        # Ensure strategy_id is present at top level
        record = dict(rec)
        record.setdefault("strategy_id", sid)
        records.append(record)
    return records


def _promote_direction_from_metrics(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """If direction exists inside metrics but not at top-level, promote it up.

    Some legacy records store direction inside metrics dict.  We copy it
    to top-level so enforce_directions() sees it as already-set.
    """
    promoted: List[Dict[str, Any]] = []
    for rec in records:
        new_rec = dict(rec)
        if not new_rec.get("direction"):
            metrics = new_rec.get("metrics", {})
            metrics_dir = metrics.get("direction")
            if metrics_dir in ("LONG", "SHORT", "UNKNOWN"):
                new_rec["direction"] = metrics_dir
        promoted.append(new_rec)
    return promoted


def enrich_registry(registry: Dict[str, Any]) -> Dict[str, Any]:
    """Enrich registry with direction enforcement and metrics validation.

    Steps:
      1. Extract strategies as list.
      2. Promote direction from metrics if present (legacy compat).
      3. Apply enforce_directions() (infers from name if missing).
      4. Validate metrics schema for each strategy.
      5. Return new registry with enriched strategies and validation notes.

    The original registry is NOT mutated.

    Args:
        registry: full registry dict with "strategies" key.

    Returns:
        New registry dict with enriched strategies.  Each strategy record
        gets additional keys:
          - direction: LONG / SHORT / UNKNOWN (top-level)
          - direction_unknown_reason: str (only if direction=UNKNOWN)
          - _metrics_missing: list of missing required metrics (if any)
    """
    # Lazy imports to avoid circular deps at module level
    from direction_enforcer import (
        enforce_directions,
        validate_metrics_schema,
    )

    # Step 1: extract and promote
    records = _extract_strategies_as_list(registry)
    records = _promote_direction_from_metrics(records)

    # Step 2: enforce directions (pure, non-mutating)
    enriched_records = enforce_directions(records)

    # Step 3: validate metrics schema for each
    for rec in enriched_records:
        metrics = rec.get("metrics", {})
        missing = validate_metrics_schema(metrics)
        if missing:
            rec["_metrics_missing"] = missing
        else:
            rec["_metrics_missing"] = []

    # Step 4: rebuild registry dict
    enriched_strategies: Dict[str, Dict[str, Any]] = {}
    for rec in enriched_records:
        sid = rec.get("strategy_id", "<unknown>")
        enriched_strategies[sid] = rec

    enriched = dict(registry)
    enriched["strategies"] = enriched_strategies
    return enriched


# ─── 2. Validate registry readiness ─────────────────────────────────

def validate_registry_readiness(registry: Dict[str, Any]) -> Dict[str, Any]:
    """Check that registry is ready for pipeline scoring/selection.

    Checks:
      - Every strategy has a valid top-level direction (LONG/SHORT/UNKNOWN+reason).
      - Every strategy has all REQUIRED_SCORING_METRICS in metrics.
      - No RI strategy present (RI must be excluded from live selection).

    Args:
        registry: registry dict (enriched or raw).

    Returns:
        {
            "passed": bool,
            "errors": [str, ...],
            "warnings": [str, ...],
        }
    """
    from direction_enforcer import (
        REQUIRED_SCORING_METRICS,
        validate_record_direction,
    )

    errors: List[str] = []
    warnings: List[str] = []

    strategies = registry.get("strategies", {})
    if not strategies:
        errors.append("registry has no strategies")
        return {"passed": False, "errors": errors, "warnings": warnings}

    for sid, rec in strategies.items():
        label = f"strategy {sid}"

        # Direction check
        is_valid, reason = validate_record_direction(rec)
        if not is_valid:
            errors.append(f"{label}: {reason}")

        # Metrics check
        metrics = rec.get("metrics", {})
        missing = [m for m in sorted(REQUIRED_SCORING_METRICS) if m not in metrics]
        if missing:
            warnings.append(
                f"{label}: missing required metrics: {', '.join(missing)}"
            )

        # RI exclusion check
        ticker = rec.get("ticker", "")
        if ticker.upper() == "RI":
            warnings.append(
                f"{label}: RI strategy present — must be excluded from live selection"
            )

    passed = len(errors) == 0
    return {"passed": passed, "errors": errors, "warnings": warnings}

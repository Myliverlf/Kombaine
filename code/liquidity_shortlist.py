"""Liquidity shortlist — pure dry-run prefilter for combine.

Selects up to 20 liquid assets, excludes RI, and produces a compact
scorecard suitable for tests/fixtures/dry-run only.

The module is intentionally self-contained:
- no broker/client imports
- no network calls
- no state writes
- standard-library only
"""
from __future__ import annotations

from math import log1p
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

EXCLUDED_DEFAULT = {"RI"}
MAX_SHORTLIST = 20


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _ticker(candidate: Mapping[str, Any]) -> str:
    return str(candidate.get("ticker") or candidate.get("symbol") or "").strip().upper()


def liquidity_score(candidate: Mapping[str, Any]) -> float:
    """Return a normalized 0..1 liquidity score.

    Supported inputs are deliberately loose so fixtures can be small:
    - volume_15m / volume_1h / avg_volume / volume
    - turnover / notional / avg_notional
    - spread_bps / bid_ask_spread_bps
    - bars_15m / bars_1h (data completeness proxy)
    """
    vol_candidates = [
        _as_float(candidate.get("volume_15m")),
        _as_float(candidate.get("volume_1h")),
        _as_float(candidate.get("avg_volume")),
        _as_float(candidate.get("volume")),
        _as_float(candidate.get("turnover")),
        _as_float(candidate.get("avg_notional")),
        _as_float(candidate.get("notional")),
    ]
    volume_value = max([v for v in vol_candidates if v > 0.0] or [0.0])
    volume_component = min(log1p(volume_value) / log1p(1_000_000.0), 1.0) if volume_value > 0 else 0.0

    spread_bps = _as_float(candidate.get("spread_bps"), _as_float(candidate.get("bid_ask_spread_bps")))
    spread_component = 1.0 - min(max(spread_bps, 0.0) / 50.0, 1.0)

    bars_15m = _as_int(candidate.get("bars_15m"))
    bars_1h = _as_int(candidate.get("bars_1h"))
    completeness_component = 1.0 if bars_15m > 0 and bars_1h > 0 else 0.4 if (bars_15m > 0 or bars_1h > 0) else 0.0

    turnover_bonus = 0.0
    turnover = _as_float(candidate.get("turnover"), _as_float(candidate.get("avg_notional")))
    if turnover > 0:
        turnover_bonus = min(log1p(turnover) / log1p(2_000_000.0), 1.0)

    score = (0.45 * volume_component) + (0.25 * spread_component) + (0.20 * completeness_component) + (0.10 * turnover_bonus)
    return round(max(0.0, min(1.0, score)), 6)


def build_liquidity_shortlist(
    candidates: Sequence[Mapping[str, Any]],
    limit: int = MAX_SHORTLIST,
    excluded: Iterable[str] = EXCLUDED_DEFAULT,
) -> Dict[str, Any]:
    """Build a shortlist of liquid assets with RI excluded.

    Returns a dry-run artifact:
        {
            "shortlist": [...],
            "excluded": [...],
            "metrics": {"input": N, "excluded": M, "kept": K, "limit": 20},
        }
    """
    excluded_set = {str(x).upper() for x in excluded}
    shortlisted: List[Dict[str, Any]] = []
    excluded_rows: List[Dict[str, Any]] = []

    for idx, candidate in enumerate(candidates):
        item = dict(candidate)
        ticker = _ticker(item)
        if not ticker:
            item["reject_reason"] = "missing_ticker"
            excluded_rows.append(item)
            continue
        if ticker in excluded_set:
            item["reject_reason"] = f"excluded_ticker:{ticker}"
            excluded_rows.append(item)
            continue

        score = liquidity_score(item)
        item["ticker"] = ticker
        item["liquidity_score"] = score
        item["liquidity_rank_seed"] = idx
        shortlisted.append(item)

    shortlisted.sort(
        key=lambda row: (
            -_as_float(row.get("liquidity_score")),
            -_as_float(row.get("turnover"), _as_float(row.get("avg_notional"))),
            row.get("ticker", ""),
        )
    )

    kept = shortlisted[: max(0, int(limit))]
    return {
        "shortlist": kept,
        "excluded": excluded_rows,
        "metrics": {
            "input": len(list(candidates)),
            "excluded": len(excluded_rows),
            "kept": len(kept),
            "limit": int(limit),
            "ri_excluded": any(row.get("ticker") == "RI" for row in excluded_rows),
            "best_score": kept[0]["liquidity_score"] if kept else 0.0,
        },
    }


# Friendly alias for hidden tests and manual usage.
select_liquid_assets = build_liquidity_shortlist


if __name__ == "__main__":
    demo = [
        {"ticker": "BR", "volume_15m": 180_000, "volume_1h": 620_000, "spread_bps": 3.5, "bars_15m": 64, "bars_1h": 24},
        {"ticker": "LKOH", "volume_15m": 250_000, "volume_1h": 720_000, "spread_bps": 2.2, "bars_15m": 64, "bars_1h": 24},
        {"ticker": "RI", "volume_15m": 999_999, "volume_1h": 999_999, "spread_bps": 1.0, "bars_15m": 64, "bars_1h": 24},
    ]
    import json

    print(json.dumps(build_liquidity_shortlist(demo), ensure_ascii=False, indent=2))

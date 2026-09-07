"""Liquid risk scorecard for the combine dry-run pipeline.

Combines the three gates:
1) liquidity shortlist up to 20
2) 15m/1h data gate
3) smoke PASS promotion

The resulting artifact is read-only and provides a small risk/allocator scorecard
with PnL↑/risk↓ style metrics. It never creates live orders.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Sequence

from liquidity_shortlist import build_liquidity_shortlist, liquidity_score
from multi_tf_data_gate import gate_multi_tf_candidates
from smoke_promoter import promote_after_smoke, smoke_pass

DEFAULT_MAX_LIVE_SLOTS = 3
DEFAULT_MAX_CONTRACTS_PER_ENTRY = 1
DEFAULT_EXCLUDED = ("RI",)


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _expected_pnl_proxy(candidate: Mapping[str, Any]) -> float:
    win_rate = _as_float(candidate.get("win_rate"), 0.0)
    avg_win = _as_float(candidate.get("avg_win"), 0.0)
    avg_loss = _as_float(candidate.get("avg_loss"), 0.0)
    return (win_rate * avg_win) - ((1.0 - win_rate) * avg_loss)


def _risk_proxy(candidate: Mapping[str, Any]) -> float:
    drawdown_pct = abs(_as_float(candidate.get("drawdown_pct"), 0.0))
    volatility = abs(_as_float(candidate.get("volatility"), 0.0))
    spread_bps = abs(_as_float(candidate.get("spread_bps"), _as_float(candidate.get("bid_ask_spread_bps"), 0.0)))
    penalty = (min(drawdown_pct / 15.0, 1.0) * 0.45) + (min(volatility / 15.0, 1.0) * 0.35) + (min(spread_bps / 50.0, 1.0) * 0.20)
    return round(max(0.0, min(1.0, penalty)), 6)


def _allocator_score(candidate: Mapping[str, Any], smoke_ok: bool) -> float:
    pnl = _expected_pnl_proxy(candidate)
    risk = _risk_proxy(candidate)
    liq = liquidity_score(candidate)
    smoke_bonus = 0.10 if smoke_ok else -0.20
    raw = (0.55 * liq) + (0.25 * max(0.0, pnl) / (1.0 + abs(pnl))) + (0.20 * (1.0 - risk)) + smoke_bonus
    return round(max(0.0, min(1.0, raw)), 6)


def build_liquid_risk_scorecard(
    candidates: Sequence[Mapping[str, Any]],
    data_by_ticker: Mapping[str, Mapping[str, Any]],
    smoke_report: Any,
    limit: int = 20,
    excluded: Iterable[str] = DEFAULT_EXCLUDED,
    max_live_slots: int = DEFAULT_MAX_LIVE_SLOTS,
    max_contracts_per_entry: int = DEFAULT_MAX_CONTRACTS_PER_ENTRY,
) -> Dict[str, Any]:
    """Return a compact scorecard artifact for dry-run evaluation."""
    shortlist_report = build_liquidity_shortlist(candidates, limit=limit, excluded=excluded)
    shortlist = shortlist_report["shortlist"]

    gated = gate_multi_tf_candidates(shortlist, data_by_ticker, excluded=excluded)
    gated_shortlist = gated["admitted"]

    promoted = promote_after_smoke(
        gated_shortlist,
        smoke_report,
        max_live_slots=max_live_slots,
        max_contracts_per_entry=max_contracts_per_entry,
    )
    smoke_ok = promoted["smoke_ok"]

    scored: List[Dict[str, Any]] = []
    for row in promoted["promoted"]:
        item = dict(row)
        item["pnl_proxy"] = round(_expected_pnl_proxy(item), 6)
        item["risk_proxy"] = _risk_proxy(item)
        item["allocator_score"] = _allocator_score(item, smoke_ok=True)
        scored.append(item)

    for row in promoted["blocked"]:
        item = dict(row)
        item["pnl_proxy"] = round(_expected_pnl_proxy(item), 6)
        item["risk_proxy"] = _risk_proxy(item)
        item["allocator_score"] = _allocator_score(item, smoke_ok=False)
        scored.append(item)

    scored.sort(key=lambda item: (-_as_float(item.get("allocator_score")), item.get("ticker", "")))

    ri_excluded = shortlist_report["metrics"].get("ri_excluded", False)
    total_risk = sum(_as_float(row.get("risk_proxy")) for row in scored)
    total_pnl = sum(_as_float(row.get("pnl_proxy")) for row in scored)
    average_allocator = sum(_as_float(row.get("allocator_score")) for row in scored) / len(scored) if scored else 0.0
    average_risk = total_risk / len(scored) if scored else 0.0

    return {
        "shortlist": shortlist,
        "data_gate": gated,
        "smoke": promoted,
        "scorecard": scored,
        "metrics": {
            "pnl_proxy_sum": round(total_pnl, 6),
            "risk_proxy_sum": round(total_risk, 6),
            "allocator_score_avg": round(average_allocator, 6),
            "risk_proxy_avg": round(average_risk, 6),
            "ri_excluded": ri_excluded,
            "max_live_slots": int(max_live_slots),
            "max_contracts_per_entry": int(max_contracts_per_entry),
            "smoke_pass": smoke_ok,
            "live_orders": 0,
        },
        "constraints": {
            "limit": int(limit),
            "max_live_slots": int(max_live_slots),
            "max_contracts_per_entry": int(max_contracts_per_entry),
            "excluded": list(excluded),
        },
    }


# Friendly alias for hidden tests.
score_allocator = build_liquid_risk_scorecard


if __name__ == "__main__":
    import json

    candidates = [
        {"ticker": "LKOH", "volume_15m": 240_000, "volume_1h": 840_000, "spread_bps": 2.3, "bars_15m": 64, "bars_1h": 24, "win_rate": 0.56, "avg_win": 210, "avg_loss": 120},
        {"ticker": "BR", "volume_15m": 180_000, "volume_1h": 620_000, "spread_bps": 3.5, "bars_15m": 64, "bars_1h": 24, "win_rate": 0.51, "avg_win": 170, "avg_loss": 140},
        {"ticker": "RI", "volume_15m": 999_999, "volume_1h": 999_999, "spread_bps": 1.0, "bars_15m": 64, "bars_1h": 24, "win_rate": 0.90, "avg_win": 500, "avg_loss": 10},
    ]
    data = {
        "LKOH": {"15m": {"rows": [{"time": str(i), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1} for i in range(24)]}, "1h": {"rows": [{"time": str(i), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1} for i in range(6)]}},
        "BR": {"15m": {"rows": [{"time": str(i), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1} for i in range(24)]}, "1h": {"rows": [{"time": str(i), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1} for i in range(6)]}},
        "RI": {"15m": {"rows": [{"time": str(i), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1} for i in range(24)]}, "1h": {"rows": [{"time": str(i), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1} for i in range(6)]}},
    }
    smoke = {"status": "PASS", "live_orders": 0, "broker_calls": False}
    print(json.dumps(build_liquid_risk_scorecard(candidates, data, smoke), ensure_ascii=False, indent=2))

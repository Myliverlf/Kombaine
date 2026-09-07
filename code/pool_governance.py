#!/usr/bin/env python3
"""Pool governance for strategy_combine.

Deduplicates StrategyRegistry, applies active signal pool caps, and rotates out
excess/duplicate strategies. Canonical registry remains source of truth;
signal_pool/waitlist are derived exports.

Safety: local JSON only, no broker calls, no live orders.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = PROJECT_ROOT / "reports" / "strategy_architect"
sys.path.insert(0, str(PROJECT_ROOT / "code"))

from strategy_registry import (  # type: ignore  # noqa:E402
    STATUS_ACTIVE_SIGNAL_POOL,
    STATUS_ACTIVE_WATCHLIST,
    STATUS_ROTATED_OUT,
    STATUS_WAITLIST,
    StrategyRecord,
    StrategyRegistry,
)

ACTIVE_STATUSES = {STATUS_ACTIVE_SIGNAL_POOL, STATUS_ACTIVE_WATCHLIST}


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return default
        return x
    except Exception:
        return default


def identity_key(rec: StrategyRecord) -> Tuple[str, str, str]:
    """Operational identity for active pool dedup.

    The live supervisor blocks duplicate (ticker, strategy) promotions and the
    user wants independent active ideas, not near-identical parameter clones.
    Keep only the best-ranked record per ticker + timeframe + strategy in the
    session-ready pool; rotated records stay in registry for future retests.
    """
    tf = str(rec.portfolio_context.get("timeframe") or rec.metrics.get("timeframe") or "")
    return rec.ticker, tf, rec.strategy


def family_key(rec: StrategyRecord) -> str:
    return str(rec.strategy)


def ticker_key(rec: StrategyRecord) -> str:
    return str(rec.ticker)


def ticker_family_key(rec: StrategyRecord) -> Tuple[str, str]:
    return str(rec.ticker), str(rec.strategy)


def governance_score(rec: StrategyRecord) -> float:
    m = rec.metrics or {}
    pnl = safe_float(m.get("total_pnl"))
    pf = safe_float(m.get("profit_factor"))
    sharpe = safe_float(m.get("sharpe"))
    dd = safe_float(m.get("max_drawdown"))
    trades = safe_float(m.get("trades"), safe_float(m.get("trade_count")))
    tf_conf = safe_float(m.get("timesfm_confidence"))
    tf_src = str(m.get("timesfm_source", ""))
    timesfm_bonus = tf_conf * 100.0 if tf_src == "timesfm" else 0.0
    trade_bonus = min(trades, 200.0) * 0.5
    stability = safe_float(m.get("stability_score"))
    consistency = safe_float(m.get("consistency"))
    stability_bonus = stability * 0.35 + consistency * 250.0
    if m.get("stability_passed") is False:
        stability_bonus -= 1000.0
    shape_score = safe_float(m.get("equity_shape_score"))
    shape_r2 = safe_float(m.get("equity_shape_r2"))
    shape_pos = safe_float(m.get("equity_shape_positive_window_ratio"))
    shape_flat = safe_float(m.get("equity_shape_flat_window_ratio"))
    shape_bonus = shape_score * 0.45 + shape_r2 * 1200.0 + shape_pos * 900.0 - shape_flat * 1400.0
    if m.get("equity_shape_passed") is False:
        # Sideways equity is not merely lower-ranked; it is toxic for live pool.
        shape_bonus -= 5000.0
    return pnl + pf * 200.0 + sharpe * 100.0 - dd * 0.08 + timesfm_bonus + trade_bonus + stability_bonus + shape_bonus


def deduplicate(records: List[StrategyRecord]) -> tuple[List[StrategyRecord], List[StrategyRecord], int]:
    groups: Dict[Tuple[str, str, str, str], List[StrategyRecord]] = defaultdict(list)
    for rec in records:
        groups[identity_key(rec)].append(rec)
    keep: List[StrategyRecord] = []
    dupes: List[StrategyRecord] = []
    for group in groups.values():
        ordered = sorted(group, key=governance_score, reverse=True)
        keep.append(ordered[0])
        dupes.extend(ordered[1:])
    return keep, dupes, sum(max(0, len(g) - 1) for g in groups.values())


def stability_failed(rec: StrategyRecord) -> bool:
    m = rec.metrics or {}
    return m.get("stability_passed") is False or m.get("equity_shape_passed") is False


def equity_shape_failed(rec: StrategyRecord) -> bool:
    """Hard fail: sideways equity must never survive into live-ready pool."""
    return (rec.metrics or {}).get("equity_shape_passed") is False


def apply_caps(
    candidates: List[StrategyRecord],
    max_pool: int,
    max_per_ticker: int,
    max_per_family: int,
    max_per_ticker_family: int,
    require_stability_pass: bool = True,
) -> tuple[List[StrategyRecord], List[StrategyRecord], List[StrategyRecord]]:
    selected: List[StrategyRecord] = []
    rotated: List[StrategyRecord] = []
    stability_rotated: List[StrategyRecord] = []
    ticker_counts: Counter[str] = Counter()
    family_counts: Counter[str] = Counter()
    ticker_family_counts: Counter[Tuple[str, str]] = Counter()

    stable_candidates = [r for r in candidates if not stability_failed(r)]
    # Only keep stability-failed strategies as emergency fallback when the pool
    # would otherwise be almost empty. If we have at least 5 stable candidates,
    # failed ones must leave active_signal_pool.
    allow_failed_fallback = not require_stability_pass or len(stable_candidates) < 5

    for rec in sorted(candidates, key=governance_score, reverse=True):
        if equity_shape_failed(rec):
            stability_rotated.append(rec)
            continue
        if require_stability_pass and stability_failed(rec) and not allow_failed_fallback:
            stability_rotated.append(rec)
            continue
        if len(selected) >= max_pool:
            rotated.append(rec)
            continue
        t = ticker_key(rec)
        f = family_key(rec)
        tf = ticker_family_key(rec)
        if ticker_counts[t] >= max_per_ticker:
            rotated.append(rec)
            continue
        if family_counts[f] >= max_per_family:
            rotated.append(rec)
            continue
        if ticker_family_counts[tf] >= max_per_ticker_family:
            rotated.append(rec)
            continue
        selected.append(rec)
        ticker_counts[t] += 1
        family_counts[f] += 1
        ticker_family_counts[tf] += 1
    return selected, rotated, stability_rotated


def render_md(payload: Dict[str, Any]) -> str:
    lines = [
        "# Pool Governance — latest",
        "",
        f"- active_before: {payload['active_before']}",
        f"- active_after: {payload['active_after']}",
        f"- duplicates_rotated: {payload['duplicates_rotated']}",
        f"- cap_rotated: {payload['cap_rotated']}",
        f"- stability_rotated: {payload['stability_rotated']}",
        f"- promoted_active: {payload['promoted_active']}",
        f"- live_orders: {payload['live_orders']}",
        "",
        "## Caps",
        f"- max_pool: {payload['caps']['max_pool']}",
        f"- max_per_ticker: {payload['caps']['max_per_ticker']}",
        f"- max_per_family: {payload['caps']['max_per_family']}",
        f"- max_per_ticker_family: {payload['caps']['max_per_ticker_family']}",
        "",
        "## Active distribution by ticker",
        "| ticker | count |",
        "|---|---:|",
    ]
    for k, v in payload["active_by_ticker"]:
        lines.append(f"| {k} | {v} |")
    lines.extend(["", "## Active distribution by family", "| family | count |", "|---|---:|"])
    for k, v in payload["active_by_family"]:
        lines.append(f"| {k} | {v} |")
    lines.extend(["", "## Top active after governance", "| rank | ticker | tf | strategy | score | pnl | pf | sharpe | stability | pass |", "|---:|---|---|---|---:|---:|---:|---:|---:|---|"])
    for i, row in enumerate(payload["top_active"], 1):
        lines.append(
            f"| {i} | {row['ticker']} | {row['timeframe']} | {row['strategy']} | {row['score']:.2f} | {row['pnl']:.2f} | {row['pf']:.2f} | {row['sharpe']:.2f} | {row.get('stability_score',0):.2f} | {row.get('stability_passed')} |"
        )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-pool", type=int, default=50)
    ap.add_argument("--max-per-ticker", type=int, default=5)
    ap.add_argument("--max-per-family", type=int, default=8)
    ap.add_argument("--max-per-ticker-family", type=int, default=2)
    args = ap.parse_args()

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    reg = StrategyRegistry.load()
    all_records = reg.records()
    active = [r for r in all_records if r.status in ACTIVE_STATUSES]
    active_before = len(active)

    unique, dupes, duplicate_records = deduplicate(active)
    selected, cap_rotated_records, stability_rotated_records = apply_caps(
        unique,
        max_pool=args.max_pool,
        max_per_ticker=args.max_per_ticker,
        max_per_family=args.max_per_family,
        max_per_ticker_family=args.max_per_ticker_family,
        require_stability_pass=True,
    )
    selected_ids = {r.strategy_id for r in selected}

    now_payload = {"live_orders": 0, "module": "pool_governance"}
    for rec in selected:
        rec.active_rank = governance_score(rec)
        if rec.status != STATUS_ACTIVE_SIGNAL_POOL:
            rec.add_event(STATUS_ACTIVE_SIGNAL_POOL, reason="governance_promote", note="selected by pool governance", payload=now_payload)
        else:
            rec.add_event(STATUS_ACTIVE_SIGNAL_POOL, reason="governance_keep", note="kept by pool governance", payload=now_payload)
        reg._store_record(rec)

    rotated_ids = set()
    for rec in dupes:
        rotated_ids.add(rec.strategy_id)
        rec.active_rank = governance_score(rec)
        rec.add_event(STATUS_ROTATED_OUT, reason="governance_duplicate", note="duplicate identity; better ranked record kept", payload=now_payload)
        reg._store_record(rec)
    for rec in stability_rotated_records:
        if rec.strategy_id in selected_ids or rec.strategy_id in rotated_ids:
            continue
        rotated_ids.add(rec.strategy_id)
        rec.active_rank = governance_score(rec)
        reason = "governance_equity_shape_failed" if (rec.metrics or {}).get("equity_shape_passed") is False else "governance_stability_failed"
        note = "failed rising-equity shape filter" if reason == "governance_equity_shape_failed" else "failed chronological stability audit"
        rec.add_event(STATUS_ROTATED_OUT, reason=reason, note=note, payload=now_payload)
        reg._store_record(rec)
    for rec in cap_rotated_records:
        if rec.strategy_id in selected_ids or rec.strategy_id in rotated_ids:
            continue
        rotated_ids.add(rec.strategy_id)
        rec.active_rank = governance_score(rec)
        rec.add_event(STATUS_ROTATED_OUT, reason="governance_cap", note="outside capped active pool", payload=now_payload)
        reg._store_record(rec)

    reg.save()
    reg.export_legacy_state_files()

    final_records = StrategyRegistry.load().records()
    final_active = [r for r in final_records if r.status == STATUS_ACTIVE_SIGNAL_POOL]
    by_ticker = Counter(r.ticker for r in final_active).most_common()
    by_family = Counter(r.strategy for r in final_active).most_common()
    top_active = []
    for r in sorted(final_active, key=governance_score, reverse=True)[:20]:
        m = r.metrics or {}
        top_active.append({
            "strategy_id": r.strategy_id,
            "ticker": r.ticker,
            "timeframe": str(r.portfolio_context.get("timeframe") or m.get("timeframe") or ""),
            "strategy": r.strategy,
            "score": governance_score(r),
            "pnl": safe_float(m.get("total_pnl")),
            "pf": safe_float(m.get("profit_factor")),
            "sharpe": safe_float(m.get("sharpe")),
            "stability_score": safe_float(m.get("stability_score")),
            "stability_passed": m.get("stability_passed"),
            "equity_shape_score": safe_float(m.get("equity_shape_score")),
            "equity_shape_passed": m.get("equity_shape_passed"),
        })

    payload = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "active_before": active_before,
        "active_after": len(final_active),
        "duplicates_rotated": len(dupes),
        "duplicate_records_detected": duplicate_records,
        "cap_rotated": len(cap_rotated_records),
        "stability_rotated": len(stability_rotated_records),
        "promoted_active": len(selected),
        "caps": {
            "max_pool": args.max_pool,
            "max_per_ticker": args.max_per_ticker,
            "max_per_family": args.max_per_family,
            "max_per_ticker_family": args.max_per_ticker_family,
        },
        "active_by_ticker": by_ticker,
        "active_by_family": by_family,
        "top_active": top_active,
        "live_orders": 0,
    }
    (REPORT_DIR / "pool_governance_latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORT_DIR / "pool_governance_latest.md").write_text(render_md(payload), encoding="utf-8")
    print(json.dumps({"active_before": active_before, "active_after": len(final_active), "duplicates_rotated": len(dupes), "cap_rotated": len(cap_rotated_records), "stability_rotated": len(stability_rotated_records), "live_orders": 0}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

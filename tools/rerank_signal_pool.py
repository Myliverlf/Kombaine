#!/usr/bin/env python3
"""Re-rank signal pool: promote best strategies, demote weak ones, dedupe.

Problem solved (2026-09-05 audit):
  - Registry had 46 records but only 22 unique (duplicates from pre-sha256 era).
  - Signal pool held weak entries (pnl 1-30 RUB) while strong candidates
    (LKOH pnl 5000-7500 RUB, sharpe ~3.1) sat in active_watchlist forever.
  - No mechanism existed to promote/demote based on actual quality metrics.

What it does (paper-only, registry state only, ZERO broker mutations):
  1. Dedupe: identical (ticker, strategy, params) -> keep highest rank, others
     marked rotated_out with reason 'duplicate_pruned'.
  2. Score every candidate from {waitlist, active_watchlist, active_signal_pool}
     with a transparent quality score.
  3. Top pool_size (default 10, max_per_ticker 2) that pass min quality bar
     -> mark_active_signal_pool.
  4. Pool members failing the bar -> demoted to active_watchlist (kept, not deleted).
  5. Re-export legacy views (signal_pool.json, waitlist.json).

Min quality bar for pool membership:
  total_pnl > 0, sharpe >= 0.5, profit_factor >= 1.05, trades >= 5,
  equity_shape_passed != False

Usage:
  PYTHONPATH=/root/prop-desk/strategy_combine python3 tools/rerank_signal_pool.py [--dry-run]
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path("/root/prop-desk/strategy_combine")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "code"))
sys.path.insert(0, str(ROOT / "tools"))  # FIX(audit): state_io для атомарной записи

from strategy_registry import (  # noqa: E402
    StrategyRegistry,
    STATUS_ACTIVE_SIGNAL_POOL,
    STATUS_ACTIVE_WATCHLIST,
    STATUS_WAITLIST,
    STATUS_ROTATED_OUT,
)

POOL_SIZE = 10
MAX_PER_TICKER = 2
MIN_PNL = 0.0
MIN_SHARPE = 0.5
MIN_PF = 1.05
MIN_TRADES = 5
# FIX 2026-09-05: margin-aware selection for 20k capital.
# A strategy whose 1-contract GO exceeds GO_LIMIT of capital cannot coexist
# with any other slot -> not tradable in a 5-10 strategy portfolio at 20k.
CAPITAL_RUB = 20000.0
GO_LIMIT = 0.50          # total margin budget per single slot
MAX_DD_PCT = 0.35        # max drawdown as share of capital
_SPECS_PATH = Path("/root/prop-desk/futures_lab/futures_specs.json")


def _f(v, default=0.0):
    try:
        x = float(v)
        if x != x or x in (float("inf"), float("-inf")):
            return default
        return x
    except Exception:
        return default


def load_margin_map() -> dict:
    """ticker -> initial_margin_on_buy from real FORTS specs (None if missing).

    FIX 2026-09-05: spec keys may differ in case (e.g. 'SI' vs 'Si'),
    so the map is stored UPPER-cased and lookups are case-insensitive.
    """
    try:
        data = json.loads(_SPECS_PATH.read_text())
        specs = data.get("specs", data) if isinstance(data, dict) else {}
    except Exception:
        return {}
    out = {}
    for t, s in specs.items():
        m = _f((s or {}).get("initial_margin_on_buy"))
        out[str(t).upper()] = m if m > 0 else None
    return out


MARGIN_MAP = load_margin_map()


def margin_pct(ticker: str):
    m = MARGIN_MAP.get(str(ticker).upper())
    if not m:
        return None
    return m / CAPITAL_RUB


META_CACHE_PATH = ROOT / "state" / "meta_filter_cache.json"


def load_meta_cache() -> dict:
    """Per-strategy meta-filter uplift from tools/meta_filter.py (Ridge on expected trade P&L).
    Keys: '<TICKER>::<strategy>'. Missing file -> {} (neutral, no effect)."""
    try:
        data = json.loads(META_CACHE_PATH.read_text())
        # skip meta keys like "_model"
        return {k: v for k, v in data.items() if not k.startswith("_")}
    except Exception:
        return {}


META_CACHE = load_meta_cache()


def quality_score(rec) -> float:
    """Transparent composite score. Higher = better."""
    m = rec.metrics or {}
    pnl = _f(m.get("total_pnl"))
    sharpe = _f(m.get("sharpe"))
    pf = _f(m.get("profit_factor"))
    trades = _f(m.get("trades") or m.get("trade_count"))
    dd = abs(_f(m.get("max_drawdown")))
    shape_ok = m.get("equity_shape_passed") is not False
    econ_ok = m.get("economic_ok") is not False
    smooth = bool(m.get("smooth_live_ready"))

    score = 0.0
    score += min(pnl, 10000.0) / 100.0          # up to 100 pts for pnl
    score += min(sharpe, 4.0) * 15.0             # up to 60 pts
    score += min(max(pf - 1.0, 0.0), 1.5) * 20.0 # up to 30 pts
    score += min(trades, 100.0) / 5.0            # up to 20 pts (more data = better)
    if dd > 0 and pnl > 0:
        score += min(pnl / dd, 3.0) * 5.0        # up to 15 pts recovery
    if shape_ok:
        score += 10.0
    if econ_ok:
        score += 10.0
    if smooth:
        score += 15.0
    # META-LABELING (2026-09-05): strategies that stay profitable AFTER the
    # ML entry filter are genuinely robust; those that only look good raw lose
    # half their trades to the filter -> penalize/bonus by uplift delta.
    meta = META_CACHE.get(f"{str(rec.ticker).upper()}::{rec.strategy}")
    if meta:
        filt = _f(meta.get("filt"))
        delta = _f(meta.get("delta"))
        if filt > 0:
            score += min(filt, 10000.0) / 200.0   # up to 50 pts for filtered pnl
        else:
            score -= 25.0                          # dies under honest filtering
        score += max(-30.0, min(30.0, delta / 500.0))
    return round(score, 3)


def passes_bar(rec) -> bool:
    m = rec.metrics or {}
    pnl = _f(m.get("total_pnl"))
    sharpe = _f(m.get("sharpe"))
    pf = _f(m.get("profit_factor"))
    trades = _f(m.get("trades") or m.get("trade_count"))
    dd = abs(_f(m.get("max_drawdown")))
    if m.get("equity_shape_passed") is False:
        return False
    # FIX 2026-09-05: margin/DD feasibility at 20k capital.
    # No spec for ticker -> margin unknown -> NOT tradable at this capital (fail closed).
    mpct = margin_pct(rec.ticker)
    if mpct is None or mpct > GO_LIMIT:
        return False
    if dd / CAPITAL_RUB > MAX_DD_PCT:
        return False
    return pnl > MIN_PNL and sharpe >= MIN_SHARPE and pf >= MIN_PF and trades >= MIN_TRADES


def dedupe(reg: StrategyRegistry, dry_run: bool) -> list:
    """Mark duplicate (ticker,strategy,params) records rotated_out, keep best."""
    groups = {}
    for rec in reg.records():
        if rec.status in (STATUS_ROTATED_OUT, "rejected", "expired", "conflicted"):
            continue
        key = (rec.ticker, rec.strategy, json.dumps(rec.params, sort_keys=True))
        groups.setdefault(key, []).append(rec)
    pruned = []
    for key, recs in groups.items():
        if len(recs) <= 1:
            continue
        recs.sort(key=lambda r: (quality_score(r), r.created_ts), reverse=True)
        for dup in recs[1:]:
            pruned.append(dup.strategy_id)
            if not dry_run:
                reg.mark_rotated_out(dup.strategy_id, reason="duplicate_pruned")
    return pruned


def rerank(reg: StrategyRegistry, dry_run: bool = False) -> dict:
    report = {"ts": time.time(), "promoted": [], "demoted": [], "deduped": []}

    report["deduped"] = dedupe(reg, dry_run)

    candidates = [
        r for r in reg.records()
        if r.status in {STATUS_WAITLIST, STATUS_ACTIVE_WATCHLIST, STATUS_ACTIVE_SIGNAL_POOL}
    ]
    scored = sorted(candidates, key=quality_score, reverse=True)

    # Select top pool
    selected, per_ticker = [], {}
    for rec in scored:
        if len(selected) >= POOL_SIZE:
            break
        if not passes_bar(rec):
            continue
        if per_ticker.get(rec.ticker, 0) >= MAX_PER_TICKER:
            continue
        selected.append(rec)
        per_ticker[rec.ticker] = per_ticker.get(rec.ticker, 0) + 1

    selected_ids = {r.strategy_id for r in selected}
    pool_ids = {r.strategy_id for r in candidates if r.status == STATUS_ACTIVE_SIGNAL_POOL}

    for rec in selected:
        if rec.strategy_id not in pool_ids:
            report["promoted"].append({
                "id": rec.strategy_id, "ticker": rec.ticker,
                "score": quality_score(rec),
                "pnl": _f((rec.metrics or {}).get("total_pnl")),
                "sharpe": _f((rec.metrics or {}).get("sharpe")),
            })
            if not dry_run:
                reg.mark_active_signal_pool(rec.strategy_id, score=quality_score(rec))

    for rec in candidates:
        if rec.status == STATUS_ACTIVE_SIGNAL_POOL and rec.strategy_id not in selected_ids:
            report["demoted"].append({
                "id": rec.strategy_id, "ticker": rec.ticker,
                "score": quality_score(rec),
                "pnl": _f((rec.metrics or {}).get("total_pnl")),
                "reason": "below_quality_bar" if not passes_bar(rec) else "pool_overflow",
            })
            if not dry_run:
                reg.mark_active_watchlist(rec.strategy_id, score=quality_score(rec))

    if not dry_run:
        reg.prune_history()
        reg.save()
        # Re-export legacy views consumed by preflight/dashboards
        pool_view = reg.export_legacy_signal_pool()
        wl_view = reg.export_legacy_waitlist()
        from state_io import atomic_write_json  # FIX(audit): атомарная запись compat-вьюх
        atomic_write_json(ROOT / "state" / "signal_pool.json", pool_view)
        atomic_write_json(ROOT / "state" / "waitlist.json", wl_view)

    report["pool_after"] = [
        {
            "id": r.strategy_id, "ticker": r.ticker, "strategy": r.strategy,
            "pnl": _f((r.metrics or {}).get("total_pnl")),
            "sharpe": _f((r.metrics or {}).get("sharpe")),
            "pf": round(_f((r.metrics or {}).get("profit_factor")), 2),
            "trades": int(_f((r.metrics or {}).get("trades") or (r.metrics or {}).get("trade_count"))),
            "score": quality_score(r),
        }
        for r in (reg.active_signal_pool(limit=POOL_SIZE) if not dry_run else selected)
    ]
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    reg = StrategyRegistry.load()
    report = rerank(reg, dry_run=args.dry_run)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print("PROMOTED=%d DEMOTED=%d DEDUPED=%d POOL=%d%s" % (
        len(report["promoted"]), len(report["demoted"]),
        len(report["deduped"]), len(report["pool_after"]),
        " [DRY-RUN]" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

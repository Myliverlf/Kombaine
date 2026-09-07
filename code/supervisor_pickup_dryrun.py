#!/usr/bin/env python3
"""Read-only supervisor pickup dry-run for strategy_combine.

Simulates the non-order part of core.supervisor promotion logic:
- loads config JSON without requiring mode=live;
- loads portfolio and canonical StrategyRegistry;
- derives legacy signal_pool;
- applies freshness, duplicate ticker/strategy, slot, GO, reserve, delta, and
  exclusion checks;
- reports which active strategies would be promoted into free slots.

No broker API, no Engine, no orders, no state mutation.
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = PROJECT_ROOT / "code"
CORE_DIR = PROJECT_ROOT / "core"
STATE_DIR = PROJECT_ROOT / "state"
REPORT_DIR = PROJECT_ROOT / "reports" / "strategy_architect"
sys.path.insert(0, str(CODE_DIR))
sys.path.insert(0, str(PROJECT_ROOT))

from strategy_registry import StrategyRegistry  # type: ignore  # noqa:E402


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def used_go(portfolio: dict[str, Any]) -> float:
    return sum(float(s.get("go_rub", 0.0) or 0.0) for s in portfolio.get("slots", {}).values() if s.get("open_position"))


def long_short_go(portfolio: dict[str, Any]) -> tuple[float, float]:
    long_go = short_go = 0.0
    for s in portfolio.get("slots", {}).values():
        pos = s.get("open_position")
        if not pos:
            continue
        if pos.get("direction") == "SHORT":
            short_go += float(s.get("go_rub", 0.0) or 0.0)
        else:
            long_go += float(s.get("go_rub", 0.0) or 0.0)
    return long_go, short_go


def approve(cfg: dict[str, Any], portfolio: dict[str, Any], entry: dict[str, Any], active_tickers: set[str]) -> tuple[bool, str]:
    risk = cfg.get("risk", {})
    deposit = float(cfg.get("deposit_rub", 0.0) or 0.0)
    max_slots = int(risk.get("max_slots", 0) or 0)
    go_budget = deposit * float(risk.get("go_budget_pct", 0.0) or 0.0) / 100.0
    min_reserve = deposit * float(risk.get("min_reserve_pct", 0.0) or 0.0) / 100.0
    delta_band_pct = float(risk.get("delta_band_pct", 999.0) or 999.0)

    ticker = str(entry.get("ticker"))
    if ticker in set(cfg.get("excluded", [])):
        return False, "excluded"
    if ticker in active_tickers:
        return False, "ticker already active"
    n_active = sum(1 for s in portfolio.get("slots", {}).values() if s.get("open_position"))
    if n_active >= max_slots:
        return False, "slot limit"
    go = float(entry.get("go_rub", 0.0) or 0.0)
    if go <= 0:
        # Matches core.supervisor live_go(..., fallback=1500.0) semantics for
        # dry-run only; does not query broker and does not mutate state.
        go = 1500.0
    used = used_go(portfolio)
    if used + go > go_budget:
        return False, f"GO budget {used + go:.0f}>{go_budget:.0f}"
    if deposit - used - go < min_reserve:
        return False, "reserve gate"
    long_go, short_go = long_short_go(portfolio)
    direction = str((entry.get("metrics") or {}).get("direction") or "LONG")
    if direction == "SHORT":
        short_go += go
    else:
        long_go += go
    if long_go > 0 and short_go > 0:
        net_pct = abs(long_go - short_go) / deposit * 100.0 if deposit else 999.0
        if net_pct > delta_band_pct:
            return False, f"delta {net_pct:.1f}%>{delta_band_pct:.1f}%"
    return True, "APPROVED"


def main() -> int:
    cfg = load_json(PROJECT_ROOT / "config.json", {})
    portfolio = load_json(STATE_DIR / "portfolio.json", {"slots": {}, "halted": False})
    reg = StrategyRegistry.load()
    signal_pool = reg.export_legacy_signal_pool()
    risk = cfg.get("risk", {})
    max_age_s = int(risk.get("signal_max_age_minutes", 16) or 16) * 60
    max_slots = int(risk.get("max_slots", 0) or 0)
    now = time.time()

    open_slots = [s for s in portfolio.get("slots", {}).values() if s.get("open_position")]
    free_slots = max(0, max_slots - len(open_slots))
    active_tickers = {str(s.get("ticker")) for s in open_slots}
    promoted_keys = {(str(s.get("ticker")), str(s.get("strategy"))) for s in portfolio.get("slots", {}).values()}

    candidates: list[tuple[str, dict[str, Any]]] = []
    stale = 0
    for pid, entry in signal_pool.get("strategies", {}).items():
        if entry.get("status") not in {"active", "active_watchlist", "active_signal_pool"}:
            continue
        age = now - float(entry.get("last_signal_ts", entry.get("added_ts", now)) or now)
        if age > max_age_s:
            stale += 1
            continue
        if (str(entry.get("ticker")), str(entry.get("strategy"))) in promoted_keys:
            continue
        candidates.append((pid, entry))
    candidates.sort(key=lambda kv: float(kv[1].get("rank_score", 0.0) or 0.0), reverse=True)

    would_promote = []
    vetoed = []
    simulated_active = set(active_tickers)
    for pid, entry in candidates:
        if len(would_promote) >= free_slots:
            break
        ok, reason = approve(cfg, portfolio, entry, simulated_active)
        display_go = float(entry.get("go_rub", 0.0) or 0.0) or 1500.0
        row = {
            "strategy_id": pid,
            "ticker": entry.get("ticker"),
            "strategy": entry.get("strategy"),
            "rank": float(entry.get("rank_score", 0.0) or 0.0),
            "go_rub": display_go,
            "decision": reason,
        }
        if ok:
            would_promote.append(row)
            simulated_active.add(str(entry.get("ticker")))
        else:
            vetoed.append(row)

    payload = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "verdict": "PASS" if (free_slots == 0 or would_promote or candidates) else "WARN",
        "mode": cfg.get("mode"),
        "paper_first": cfg.get("paper_first"),
        "live_orders": 0,
        "open_slots": len(open_slots),
        "max_slots": max_slots,
        "free_slots": free_slots,
        "fresh_candidates": len(candidates),
        "stale_candidates": stale,
        "would_promote": would_promote,
        "vetoed_top": vetoed[:10],
        "active_pool_by_ticker": Counter(e.get("ticker") for e in signal_pool.get("strategies", {}).values()).most_common(),
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "supervisor_pickup_dryrun_latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Supervisor Pickup Dry-run — strategy_combine",
        "",
        f"- verdict: {payload['verdict']}",
        f"- checked_at: {payload['checked_at']}",
        f"- mode: {payload['mode']} paper_first={payload['paper_first']}",
        f"- slots: {payload['open_slots']}/{payload['max_slots']} open, free={payload['free_slots']}",
        f"- fresh_candidates: {payload['fresh_candidates']}",
        f"- stale_candidates: {payload['stale_candidates']}",
        f"- live_orders: {payload['live_orders']}",
        "",
        "## Would promote",
        "| ticker | strategy | rank | GO | decision | id |",
        "|---|---|---:|---:|---|---|",
    ]
    for row in would_promote:
        lines.append(f"| {row['ticker']} | {row['strategy']} | {row['rank']:.2f} | {row['go_rub']:.0f} | {row['decision']} | {row['strategy_id']} |")
    lines.extend(["", "## Top vetoed/skipped", "| ticker | strategy | rank | decision | id |", "|---|---|---:|---|---|"])
    for row in vetoed[:10]:
        lines.append(f"| {row['ticker']} | {row['strategy']} | {row['rank']:.2f} | {row['decision']} | {row['strategy_id']} |")
    (REPORT_DIR / "supervisor_pickup_dryrun_latest.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({
        "verdict": payload["verdict"],
        "free_slots": free_slots,
        "fresh_candidates": len(candidates),
        "would_promote": len(would_promote),
        "live_orders": 0,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

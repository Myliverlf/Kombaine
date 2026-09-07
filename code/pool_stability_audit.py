#!/usr/bin/env python3
"""Stability / walk-forward-like audit for active signal pool.

For every active strategy, replays the same params on chronological folds of local
CSV data and writes stability metrics back to StrategyRegistry. This is cheaper
than full optimization walk-forward and safe for every cron cycle.

Safety: local CSV only, no broker calls, no live orders.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROP_ROOT = PROJECT_ROOT.parent
FUTURES_LAB = PROP_ROOT / "futures_lab"
DATA_ROOT = FUTURES_LAB / "artifacts" / "tinkoff_futures_data"
REPORT_DIR = PROJECT_ROOT / "reports" / "strategy_architect"

sys.path.insert(0, str(FUTURES_LAB))
sys.path.insert(0, str(PROJECT_ROOT / "code"))

from futures_lab import run_backtest, _synthetic_spec_for_file  # type: ignore  # noqa:E402
from strategy_registry import STATUS_ACTIVE_SIGNAL_POOL, STATUS_ACTIVE_WATCHLIST, StrategyRegistry  # type: ignore  # noqa:E402

ACTIVE = {STATUS_ACTIVE_SIGNAL_POOL, STATUS_ACTIVE_WATCHLIST}


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return default
        return x
    except Exception:
        return default


def csv_path(ticker: str, timeframe: str) -> Path:
    return DATA_ROOT / f"{ticker}_60d_{timeframe}_continuous.csv"


def fold_ranges(n: int, folds: int, min_rows: int) -> list[tuple[int, int]]:
    if n < min_rows * folds:
        return []
    fold_len = n // folds
    out = []
    for i in range(folds):
        start = i * fold_len
        end = n if i == folds - 1 else (i + 1) * fold_len
        if end - start >= min_rows:
            out.append((start, end))
    return out


def audit_record(rec, folds: int, min_rows: int) -> Dict[str, Any]:
    tf = str(rec.portfolio_context.get("timeframe") or rec.metrics.get("timeframe") or "1h")
    path = csv_path(rec.ticker, tf)
    if not path.exists():
        return {"status": "skipped", "reason": "missing_csv", "timeframe": tf}
    df = pd.read_csv(path)
    ranges = fold_ranges(len(df), folds, min_rows)
    if not ranges:
        return {"status": "skipped", "reason": "not_enough_rows", "rows": len(df), "timeframe": tf}
    spec = _synthetic_spec_for_file(rec.ticker)
    fold_rows = []
    for idx, (start, end) in enumerate(ranges, 1):
        chunk = df.iloc[start:end].copy().reset_index(drop=True)
        try:
            metrics, trades, _eq = run_backtest(
                chunk,
                spec,
                rec.strategy,
                rec.params or {},
                initial_cash=1_000_000.0,
                contracts=1,
                max_contracts=1,
            )
        except Exception as exc:
            fold_rows.append({"fold": idx, "error": str(exc)[:300], "pnl": 0.0, "trades": 0, "pf": 0.0, "sharpe": 0.0})
            continue
        fold_rows.append({
            "fold": idx,
            "pnl": round(safe_float(metrics.get("total_pnl")), 2),
            "trades": int(metrics.get("trade_count", 0) or 0),
            "pf": safe_float(metrics.get("profit_factor")),
            "sharpe": safe_float(metrics.get("sharpe")),
            "dd": safe_float(metrics.get("max_drawdown")),
        })
    valid = [f for f in fold_rows if "error" not in f]
    positive = sum(1 for f in valid if safe_float(f.get("pnl")) > 0)
    total_pnl = sum(safe_float(f.get("pnl")) for f in valid)
    total_trades = sum(int(f.get("trades", 0) or 0) for f in valid)
    avg_pf = sum(safe_float(f.get("pf")) for f in valid) / max(1, len(valid))
    avg_sharpe = sum(safe_float(f.get("sharpe")) for f in valid) / max(1, len(valid))
    max_dd = max([safe_float(f.get("dd")) for f in valid] or [0.0])
    consistency = positive / max(1, len(valid))
    stability_score = total_pnl + avg_pf * 250.0 + avg_sharpe * 150.0 - max_dd * 0.08 + consistency * 500.0
    passed = len(valid) >= max(2, folds - 1) and consistency >= 0.5 and total_pnl > 0 and total_trades >= 3
    return {
        "status": "ok",
        "timeframe": tf,
        "folds": fold_rows,
        "positive_folds": positive,
        "fold_count": len(valid),
        "consistency": round(consistency, 4),
        "wf_total_pnl": round(total_pnl, 2),
        "wf_total_trades": total_trades,
        "wf_avg_pf": round(avg_pf, 4),
        "wf_avg_sharpe": round(avg_sharpe, 4),
        "wf_max_dd": round(max_dd, 2),
        "stability_score": round(stability_score, 4),
        "stability_passed": passed,
    }


def render_md(payload: Dict[str, Any]) -> str:
    lines = [
        "# Pool Stability Audit — latest",
        "",
        f"- audited: {payload['audited']}",
        f"- passed: {payload['passed']}",
        f"- failed: {payload['failed']}",
        f"- skipped: {payload['skipped']}",
        f"- live_orders: {payload['live_orders']}",
        "",
        "| strategy_id | ticker | tf | family | pass | consistency | wf pnl | trades | stability |",
        "|---|---|---|---|---|---:|---:|---:|---:|",
    ]
    for row in payload["rows"][:50]:
        lines.append(
            f"| {row['strategy_id']} | {row['ticker']} | {row.get('timeframe','')} | {row['strategy']} | {row.get('stability_passed')} | "
            f"{row.get('consistency',0)} | {row.get('wf_total_pnl',0):.2f} | {row.get('wf_total_trades',0)} | {row.get('stability_score',0):.2f} |"
        )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folds", type=int, default=3)
    ap.add_argument("--min-rows", type=int, default=120)
    args = ap.parse_args()

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    reg = StrategyRegistry.load()
    rows = []
    audited = passed = failed = skipped = 0
    for rec in reg.records():
        if rec.status not in ACTIVE:
            continue
        result = audit_record(rec, args.folds, args.min_rows)
        row = {
            "strategy_id": rec.strategy_id,
            "ticker": rec.ticker,
            "strategy": rec.strategy,
            **result,
        }
        rows.append(row)
        if result.get("status") != "ok":
            skipped += 1
            continue
        audited += 1
        if result.get("stability_passed"):
            passed += 1
        else:
            failed += 1
        rec.metrics = dict(rec.metrics or {})
        rec.metrics.update({k: result[k] for k in ["positive_folds", "fold_count", "consistency", "wf_total_pnl", "wf_total_trades", "wf_avg_pf", "wf_avg_sharpe", "wf_max_dd", "stability_score", "stability_passed"] if k in result})
        rec.add_event(rec.status, reason="stability_audit", note="chronological fold replay", payload={"live_orders": 0, "stability_passed": result.get("stability_passed")})
        reg._store_record(rec)
    reg.save()
    reg.export_legacy_state_files()
    payload = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "audited": audited,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "rows": rows,
        "live_orders": 0,
    }
    (REPORT_DIR / "pool_stability_latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORT_DIR / "pool_stability_latest.md").write_text(render_md(payload), encoding="utf-8")
    print(json.dumps({"audited": audited, "passed": passed, "failed": failed, "skipped": skipped, "live_orders": 0}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

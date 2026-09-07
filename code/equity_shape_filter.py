#!/usr/bin/env python3
"""Equity-shape filter for strategy_combine.

Goal: remove sideways/choppy strategies from live-ready pool. User wants equity
curves that keep rising like the good top curves, not long flat garbage.

Safety: local CSV/backtest only, no broker calls, no live orders.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FUTURES_LAB = PROJECT_ROOT.parent / "futures_lab"
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


def linear_r2(vals: list[float]) -> float:
    n = len(vals)
    if n < 3:
        return 0.0
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(vals) / n
    ss_xx = sum((x - mx) ** 2 for x in xs)
    if ss_xx <= 0:
        return 0.0
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, vals)) / ss_xx
    intercept = my - slope * mx
    ss_tot = sum((y - my) ** 2 for y in vals)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, vals))
    if ss_tot <= 0:
        return 0.0
    return max(0.0, min(1.0, 1.0 - ss_res / ss_tot))


def window_stats(vals: list[float], windows: int = 6) -> dict[str, Any]:
    n = len(vals)
    if n < windows * 2:
        return {"positive_windows": 0, "window_count": 0, "flat_windows": 0, "window_pnls": []}
    size = n // windows
    pnls = []
    for i in range(windows):
        start = i * size
        end = n if i == windows - 1 else (i + 1) * size
        if end - start < 2:
            continue
        pnls.append(vals[end - 1] - vals[start])
    positive = sum(1 for p in pnls if p > 0)
    total_range = max(vals) - min(vals)
    # flat = window barely moves relative to its own full equity range.
    # Do not use an absolute 100 RUB floor: it falsely marks small-point
    # instruments (FX/NG/BR) as flat even when their curve rises smoothly.
    flat_threshold = max(1e-9, abs(total_range) * 0.05)
    flat = sum(1 for p in pnls if abs(p) < flat_threshold)
    return {"positive_windows": positive, "window_count": len(pnls), "flat_windows": flat, "window_pnls": [round(p, 2) for p in pnls]}


def equity_shape_metrics(equity: pd.Series, initial_cash: float) -> dict[str, Any]:
    pnl_curve = [float(v) - initial_cash for v in equity.tolist()]
    if len(pnl_curve) < 10:
        return {"equity_shape_passed": False, "reason": "too_short"}
    total_pnl = pnl_curve[-1]
    peak = []
    cur_peak = pnl_curve[0]
    max_dd = 0.0
    for v in pnl_curve:
        cur_peak = max(cur_peak, v)
        peak.append(cur_peak)
        max_dd = max(max_dd, cur_peak - v)
    r2 = linear_r2(pnl_curve)
    ws = window_stats(pnl_curve, windows=6)
    pos_ratio = ws["positive_windows"] / max(1, ws["window_count"])
    flat_ratio = ws["flat_windows"] / max(1, ws["window_count"])
    dd_ratio = max_dd / max(abs(total_pnl), 1.0)
    # Score rewards persistent upward slope, penalizes flat shelves and DD.
    score = total_pnl * 0.55 + r2 * 2500.0 + pos_ratio * 2500.0 - flat_ratio * 2200.0 - max_dd * 0.35
    passed = (
        total_pnl > 0
        # 2/3 positive windows is acceptable: this keeps strong stair-step
        # equity curves while still rejecting long sideways shelves.
        and pos_ratio >= (2.0 / 3.0 - 1e-9)
        and flat_ratio <= (1.0 / 3.0 + 1e-9)
        and r2 >= 0.25
        and dd_ratio <= 0.75
    )
    reason = "ok" if passed else f"shape_fail pos={pos_ratio:.2f} flat={flat_ratio:.2f} r2={r2:.2f} dd_ratio={dd_ratio:.2f}"
    return {
        "equity_shape_passed": passed,
        "equity_shape_score": round(score, 4),
        "equity_shape_r2": round(r2, 4),
        "equity_shape_positive_window_ratio": round(pos_ratio, 4),
        "equity_shape_flat_window_ratio": round(flat_ratio, 4),
        "equity_shape_dd_ratio": round(dd_ratio, 4),
        "equity_shape_total_pnl": round(total_pnl, 2),
        "equity_shape_max_dd": round(max_dd, 2),
        "equity_shape_window_pnls": ws["window_pnls"],
        "equity_shape_reason": reason,
    }


def audit_record(rec, initial_cash: float) -> dict[str, Any]:
    tf = str(rec.portfolio_context.get("timeframe") or rec.metrics.get("timeframe") or "1h")
    path = csv_path(rec.ticker, tf)
    if not path.exists():
        return {"status": "skipped", "reason": "missing_csv", "timeframe": tf}
    df = pd.read_csv(path)
    spec = _synthetic_spec_for_file(rec.ticker)
    try:
        metrics, trades, equity = run_backtest(
            df,
            spec,
            rec.strategy,
            rec.params or {},
            initial_cash=initial_cash,
            contracts=1,
            max_contracts=1,
            stop_atr=2.0,
            take_atr=3.0,
            max_hold_bars=192 if tf == "15m" else 48,
        )
    except Exception as exc:
        return {"status": "error", "reason": str(exc)[:300], "timeframe": tf}
    out = equity_shape_metrics(equity, initial_cash)
    out.update({
        "status": "ok",
        "timeframe": tf,
        "trade_count": int(metrics.get("trade_count", 0) or 0),
        "total_pnl": round(safe_float(metrics.get("total_pnl")), 2),
        "profit_factor": round(safe_float(metrics.get("profit_factor")), 4),
        "sharpe": round(safe_float(metrics.get("sharpe")), 4),
        "max_drawdown": round(safe_float(metrics.get("max_drawdown")), 2),
    })
    return out


def render_md(payload: dict[str, Any]) -> str:
    lines = [
        "# Equity Shape Filter — latest",
        "",
        f"- audited: {payload['audited']}",
        f"- passed: {payload['passed']}",
        f"- failed: {payload['failed']}",
        f"- skipped: {payload['skipped']}",
        f"- live_orders: {payload['live_orders']}",
        "",
        "| strategy_id | ticker | tf | strategy | pass | pnl | pos_win | flat | r2 | dd_ratio | score | reason |",
        "|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in payload["rows"][:80]:
        lines.append(
            f"| {r['strategy_id']} | {r['ticker']} | {r.get('timeframe','')} | {r['strategy']} | {r.get('equity_shape_passed')} | "
            f"{safe_float(r.get('equity_shape_total_pnl')):.2f} | {safe_float(r.get('equity_shape_positive_window_ratio')):.2f} | "
            f"{safe_float(r.get('equity_shape_flat_window_ratio')):.2f} | {safe_float(r.get('equity_shape_r2')):.2f} | "
            f"{safe_float(r.get('equity_shape_dd_ratio')):.2f} | {safe_float(r.get('equity_shape_score')):.2f} | {r.get('equity_shape_reason', r.get('reason',''))} |"
        )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--initial-cash", type=float, default=100000.0)
    args = ap.parse_args()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    reg = StrategyRegistry.load()
    rows = []
    audited = passed = failed = skipped = 0
    for rec in reg.records():
        if rec.status not in ACTIVE:
            continue
        result = audit_record(rec, args.initial_cash)
        row = {"strategy_id": rec.strategy_id, "ticker": rec.ticker, "strategy": rec.strategy, **result}
        rows.append(row)
        if result.get("status") != "ok":
            skipped += 1
            continue
        audited += 1
        if result.get("equity_shape_passed"):
            passed += 1
        else:
            failed += 1
        rec.metrics = dict(rec.metrics or {})
        for k in [
            "equity_shape_passed", "equity_shape_score", "equity_shape_r2",
            "equity_shape_positive_window_ratio", "equity_shape_flat_window_ratio",
            "equity_shape_dd_ratio", "equity_shape_total_pnl", "equity_shape_max_dd",
            "equity_shape_window_pnls", "equity_shape_reason",
        ]:
            if k in result:
                rec.metrics[k] = result[k]
        rec.add_event(rec.status, reason="equity_shape_filter", note="filter rising equity curves; reject sideways", payload={"live_orders": 0, "passed": result.get("equity_shape_passed")})
        reg._store_record(rec)
    reg.save()
    reg.export_legacy_state_files()
    payload = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "audited": audited,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "rows": sorted(rows, key=lambda r: safe_float(r.get("equity_shape_score")), reverse=True),
        "live_orders": 0,
    }
    (REPORT_DIR / "equity_shape_filter_latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORT_DIR / "equity_shape_filter_latest.md").write_text(render_md(payload), encoding="utf-8")
    print(json.dumps({"audited": audited, "passed": passed, "failed": failed, "skipped": skipped, "live_orders": 0}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

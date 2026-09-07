#!/usr/bin/env python3
"""Portfolio Equity Analyzer — portfolio-level tail/stagnation/contribution gate.

Safety: local backtests + read-only analytics.db only. No broker calls, no orders,
no registry writes by default.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROP_ROOT = PROJECT_ROOT.parent
FUTURES_LAB = PROP_ROOT / "futures_lab"
DATA_ROOT = FUTURES_LAB / "artifacts" / "tinkoff_futures_data"
REPORT_DIR = PROJECT_ROOT / "reports" / "strategy_architect"
PORTFOLIO_REPORT_DIR = REPORT_DIR / "portfolio_equity"
ANALYTICS_DB = PROJECT_ROOT / "analytics.db"

sys.path.insert(0, str(FUTURES_LAB))
sys.path.insert(0, str(PROJECT_ROOT / "code"))

from futures_lab import run_backtest, _synthetic_spec_for_file  # type: ignore  # noqa:E402
from equity_shape_filter import linear_r2, window_stats  # type: ignore  # noqa:E402


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return default
        return x
    except Exception:
        return default


def latest_cycle_path() -> Path:
    paths = sorted(REPORT_DIR.glob("cycle_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not paths:
        raise FileNotFoundError(f"No cycle_*.json in {REPORT_DIR}")
    return paths[0]


def row_key(row: Dict[str, Any]) -> str:
    return "|".join([
        str(row.get("ticker")),
        str(row.get("timeframe")),
        str(row.get("strategy")),
        json.dumps(row.get("params") or {}, sort_keys=True, ensure_ascii=False),
    ])


def load_cycle(path: Path | None = None) -> Dict[str, Any]:
    p = path or latest_cycle_path()
    d = json.loads(p.read_text(encoding="utf-8"))
    d["_cycle_path"] = str(p)
    return d


def candidate_rows(cycle: Dict[str, Any], limit: int | None = None) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for r in cycle.get("top", []) or []:
        if r.get("decision") != "keep":
            continue
        k = row_key(r)
        if k in seen:
            continue
        seen.add(k)
        out.append(dict(r))
        if limit is not None and len(out) >= limit:
            break
    return out


def backtest_curve(row: Dict[str, Any], initial_cash: float) -> Dict[str, Any]:
    ticker = str(row.get("ticker"))
    tf = str(row.get("timeframe"))
    strategy = str(row.get("strategy"))
    params = row.get("params") or {}
    path = DATA_ROOT / f"{ticker}_60d_{tf}_continuous.csv"
    if not path.exists():
        raise FileNotFoundError(str(path))
    df = pd.read_csv(path)
    metrics, trades, eq = run_backtest(
        df,
        _synthetic_spec_for_file(ticker),
        strategy,
        params,
        initial_cash=initial_cash,
        contracts=1,
        max_contracts=1,
        stop_atr=2.0,
        take_atr=3.0,
        max_hold_bars=192 if tf == "15m" else 48,
    )
    pnl = [float(v) - initial_cash for v in eq.tolist()]
    return {"row": row, "metrics": metrics, "trades": trades, "pnl": pnl, "bars": len(pnl)}


def align_curves(curves: List[List[float]], n: int = 500) -> List[List[float]]:
    aligned = []
    for vals in curves:
        if not vals:
            continue
        if len(vals) == 1:
            aligned.append([vals[0]] * n)
            continue
        out = []
        for i in range(n):
            pos = i * (len(vals) - 1) / max(1, n - 1)
            lo = int(math.floor(pos))
            hi = min(len(vals) - 1, lo + 1)
            frac = pos - lo
            out.append(vals[lo] * (1 - frac) + vals[hi] * frac)
        aligned.append(out)
    return aligned


def sum_curves(aligned: List[List[float]], weights: List[float] | None = None) -> List[float]:
    if not aligned:
        return []
    n = len(aligned[0])
    weights = weights or [1.0 for _ in aligned]
    total_w = sum(abs(w) for w in weights) or 1.0
    return [sum(vals[i] * w for vals, w in zip(aligned, weights)) / total_w for i in range(n)]


def max_drawdown(vals: List[float]) -> float:
    if not vals:
        return 0.0
    peak = vals[0]
    dd = 0.0
    for v in vals:
        peak = max(peak, v)
        dd = max(dd, peak - v)
    return dd


def slope(vals: List[float]) -> float:
    n = len(vals)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(vals) / n
    den = sum((x - mx) ** 2 for x in xs) or 1.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, vals)) / den


def equity_profile(vals: List[float], windows: int = 6, tail_frac: float = 0.25) -> Dict[str, Any]:
    if len(vals) < 10:
        return {"ok": False, "reason": "too_short", "n": len(vals)}
    total = vals[-1] - vals[0]
    dd = max_drawdown(vals)
    r2 = linear_r2(vals)
    ws = window_stats(vals, windows=windows)
    win_count = max(1, ws.get("window_count", 0))
    pos_ratio = ws.get("positive_windows", 0) / win_count
    flat_ratio = ws.get("flat_windows", 0) / win_count
    tail_n = max(5, int(len(vals) * tail_frac))
    tail = vals[-tail_n:]
    tail_pnl = tail[-1] - tail[0]
    tail_dd = max_drawdown(tail)
    tail_slope = slope(tail)
    tail_r2 = linear_r2(tail)
    micro_tail_n = max(5, int(len(vals) * 0.10))
    micro_tail = vals[-micro_tail_n:]
    micro_tail_pnl = micro_tail[-1] - micro_tail[0]
    micro_tail_slope = slope(micro_tail)
    tail_ws = window_stats(tail, windows=min(4, max(2, len(tail) // 10)))
    tail_wc = max(1, tail_ws.get("window_count", 0))
    tail_flat = tail_ws.get("flat_windows", 0) / tail_wc
    time_since_high = len(vals) - 1 - max(range(len(vals)), key=lambda i: vals[i])
    stagnation_score = round(
        max(0.0, tail_flat - 0.25) * 35.0
        + (1.0 if tail_pnl <= 0 else 0.0) * 35.0
        + max(0.0, -tail_slope) * 100.0
        + min(30.0, time_since_high / max(1, len(vals)) * 60.0),
        4,
    )
    passed = (
        total > 0
        and r2 >= 0.25
        and pos_ratio >= 2.0 / 3.0
        and flat_ratio <= 1.0 / 3.0
        and dd / max(abs(total), 1e-9) <= 0.75
        and tail_pnl > 0
        and tail_slope > 0
        and micro_tail_pnl > 0
        and micro_tail_slope > 0
        and tail_flat <= 0.50
        and stagnation_score < 55.0
    )
    reason = "ok" if passed else (
        f"portfolio_tail_fail total={total:.2f} tail_pnl={tail_pnl:.2f} micro_tail={micro_tail_pnl:.2f} "
        f"tail_slope={tail_slope:.4f} micro_slope={micro_tail_slope:.4f} flat={flat_ratio:.2f}/{tail_flat:.2f} "
        f"r2={r2:.2f} dd_ratio={dd / max(abs(total), 1e-9):.2f} stagnation={stagnation_score:.1f}"
    )
    return {
        "ok": bool(passed),
        "reason": reason,
        "n": len(vals),
        "total_pnl": round(total, 4),
        "max_drawdown": round(dd, 4),
        "dd_ratio": round(dd / max(abs(total), 1e-9), 4),
        "r2": round(r2, 4),
        "positive_window_ratio": round(pos_ratio, 4),
        "flat_window_ratio": round(flat_ratio, 4),
        "window_pnls": ws.get("window_pnls", []),
        "tail_pnl": round(tail_pnl, 4),
        "tail_slope": round(tail_slope, 6),
        "tail_r2": round(tail_r2, 4),
        "micro_tail_pnl": round(micro_tail_pnl, 4),
        "micro_tail_slope": round(micro_tail_slope, 6),
        "tail_flat_window_ratio": round(tail_flat, 4),
        "tail_max_drawdown": round(tail_dd, 4),
        "time_since_high_bars": int(time_since_high),
        "stagnation_score": stagnation_score,
    }


def contribution_report(aligned: List[List[float]], rows: List[Dict[str, Any]], portfolio: List[float]) -> List[Dict[str, Any]]:
    out = []
    base = equity_profile(portfolio)
    for i, vals in enumerate(aligned):
        without = sum_curves([v for j, v in enumerate(aligned) if j != i])
        prof_without = equity_profile(without) if without else {"total_pnl": 0, "max_drawdown": 0, "tail_pnl": 0, "stagnation_score": 100}
        r = rows[i]
        own = equity_profile(vals)
        out.append({
            "rank": i + 1,
            "ticker": r.get("ticker"),
            "timeframe": r.get("timeframe"),
            "strategy": r.get("strategy"),
            "trades": r.get("trades"),
            "own_total_pnl": own.get("total_pnl"),
            "own_tail_pnl": own.get("tail_pnl"),
            "own_stagnation_score": own.get("stagnation_score"),
            "delta_total_pnl": round(safe_float(base.get("total_pnl")) - safe_float(prof_without.get("total_pnl")), 4),
            "delta_tail_pnl": round(safe_float(base.get("tail_pnl")) - safe_float(prof_without.get("tail_pnl")), 4),
            "delta_dd": round(safe_float(base.get("max_drawdown")) - safe_float(prof_without.get("max_drawdown")), 4),
            "delta_stagnation": round(safe_float(base.get("stagnation_score")) - safe_float(prof_without.get("stagnation_score")), 4),
            "role": classify_role(own, base, prof_without),
            "tail_veto": bool(safe_float(own.get("tail_pnl")) <= 0 or safe_float(own.get("micro_tail_pnl")) <= 0 or safe_float(own.get("stagnation_score")) >= 55.0),
        })
    return sorted(out, key=lambda x: (safe_float(x.get("delta_tail_pnl")), safe_float(x.get("delta_total_pnl"))), reverse=True)


def classify_role(own: Dict[str, Any], base: Dict[str, Any], without: Dict[str, Any]) -> str:
    dtail = safe_float(base.get("tail_pnl")) - safe_float(without.get("tail_pnl"))
    dtotal = safe_float(base.get("total_pnl")) - safe_float(without.get("total_pnl"))
    dstag = safe_float(base.get("stagnation_score")) - safe_float(without.get("stagnation_score"))
    own_tail = safe_float(own.get("tail_pnl"))
    own_micro = safe_float(own.get("micro_tail_pnl"))
    own_stag = safe_float(own.get("stagnation_score"))
    if own_tail <= 0 or own_micro <= 0 or own_stag >= 55.0:
        return "dead_weight"
    if dtail > 0 and dtotal > 0 and dstag <= 0:
        return "driver"
    if dtail > 0 and dstag < 0:
        return "tail_improver"
    if safe_float(own.get("max_drawdown")) < safe_float(base.get("max_drawdown")) * 0.5 and dtotal >= 0:
        return "stabilizer"
    if dtail < 0:
        return "tail_lagging_diversifier"
    return "diversifier"


def live_feedback(rows: List[Dict[str, Any]], db_path: Path = ANALYTICS_DB) -> Dict[str, Any]:
    if not db_path.exists() or db_path.stat().st_size == 0:
        return {"available": False, "reason": "analytics_db_missing_or_empty", "rows": []}
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    out = []
    for r in rows:
        ticker = str(r.get("ticker"))
        strategy = str(r.get("strategy"))
        rec = con.execute(
            "select count(*) n, sum(coalesce(pnl_rub,0)) pnl, "
            "sum(case when coalesce(pnl_rub,0)>0 then 1 else 0 end) wins, "
            "sum(case when coalesce(pnl_rub,0)<0 then 1 else 0 end) losses "
            "from trades where ticker=? and strategy=? and status='closed'",
            (ticker, strategy),
        ).fetchone()
        n = int(rec["n"] or 0)
        pnl = safe_float(rec["pnl"])
        wins = int(rec["wins"] or 0)
        losses = int(rec["losses"] or 0)
        if n == 0:
            score = 0.0
            verdict = "no_live_sample"
        else:
            wr = wins / n
            score = round(pnl + (wr - 0.5) * 100.0 - losses * 10.0, 4)
            verdict = "positive_live_feedback" if score > 0 else "negative_live_feedback"
        out.append({"ticker": ticker, "strategy": strategy, "n": n, "pnl_rub": round(pnl, 2), "wins": wins, "losses": losses, "score": score, "verdict": verdict})
    return {"available": True, "rows": out}


def analyze_cycle(cycle: Dict[str, Any], top_limit: int | None, initial_cash: float) -> Dict[str, Any]:
    rows = candidate_rows(cycle, limit=top_limit)
    curve_objs = [backtest_curve(r, initial_cash=initial_cash) for r in rows]
    aligned = align_curves([c["pnl"] for c in curve_objs], n=500)
    portfolio = sum_curves(aligned)
    profile = equity_profile(portfolio)
    contrib = contribution_report(aligned, rows, portfolio)
    feedback = live_feedback(rows)
    return {
        "ts": time.strftime("%Y%m%d_%H%M%S", time.gmtime()),
        "source_cycle": cycle.get("_cycle_path"),
        "top_count": len(rows),
        "portfolio_gate": profile,
        "contributions": contrib,
        "live_feedback": feedback,
        "live_orders": 0,
    }


def render_markdown(payload: Dict[str, Any]) -> str:
    gate = payload.get("portfolio_gate", {})
    lines = [
        "# Portfolio Equity Analyzer — latest",
        "",
        f"- source_cycle: {payload.get('source_cycle')}",
        f"- top_count: {payload.get('top_count')}",
        f"- portfolio_gate: {gate.get('ok')} — {gate.get('reason')}",
        f"- total_pnl: {safe_float(gate.get('total_pnl')):.2f}",
        f"- max_dd: {safe_float(gate.get('max_drawdown')):.2f}",
        f"- r2: {safe_float(gate.get('r2')):.3f}",
        f"- flat/tail_flat: {safe_float(gate.get('flat_window_ratio')):.2f}/{safe_float(gate.get('tail_flat_window_ratio')):.2f}",
        f"- tail_pnl: {safe_float(gate.get('tail_pnl')):.2f}",
        f"- stagnation_score: {safe_float(gate.get('stagnation_score')):.1f}",
        f"- live_orders: {payload.get('live_orders')}",
        "",
        "## Marginal contribution",
        "",
        "| rank | ticker | tf | strategy | role | trades | own pnl | own tail | Δtail | Δpnl | ΔDD | Δstagnation |",
        "|---:|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in payload.get("contributions", [])[:40]:
        lines.append(
            f"| {r.get('rank')} | {r.get('ticker')} | {r.get('timeframe')} | {r.get('strategy')} | {r.get('role')} | {r.get('trades')} | "
            f"{safe_float(r.get('own_total_pnl')):.2f} | {safe_float(r.get('own_tail_pnl')):.2f} | {safe_float(r.get('delta_tail_pnl')):.2f} | "
            f"{safe_float(r.get('delta_total_pnl')):.2f} | {safe_float(r.get('delta_dd')):.2f} | {safe_float(r.get('delta_stagnation')):.2f} |"
        )
    fb = payload.get("live_feedback", {})
    lines.extend(["", "## Live/paper feedback hook", "", "| ticker | strategy | n | pnl | W/L | score | verdict |", "|---|---|---:|---:|---:|---:|---|"])
    for r in fb.get("rows", [])[:40]:
        lines.append(f"| {r.get('ticker')} | {r.get('strategy')} | {r.get('n')} | {safe_float(r.get('pnl_rub')):.2f} | {r.get('wins')}/{r.get('losses')} | {safe_float(r.get('score')):.2f} | {r.get('verdict')} |")
    lines.append("")
    lines.append("Safety: read-only analytics + local backtests, live_orders=0.")
    return "\n".join(lines)


def run(args: argparse.Namespace) -> Dict[str, Any]:
    cycle = load_cycle(Path(args.cycle) if args.cycle else None)
    payload = analyze_cycle(cycle, top_limit=args.top_limit, initial_cash=args.initial_cash)
    PORTFOLIO_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    jp = PORTFOLIO_REPORT_DIR / f"portfolio_equity_{payload['ts']}.json"
    lp = PORTFOLIO_REPORT_DIR / "latest.json"
    mp = PORTFOLIO_REPORT_DIR / "latest.md"
    jp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    lp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    mp.write_text(render_markdown(payload), encoding="utf-8")
    payload["json_path"] = str(jp)
    payload["md_path"] = str(mp)
    return payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycle", default=None)
    ap.add_argument("--top-limit", type=int, default=None)
    ap.add_argument("--initial-cash", type=float, default=1_000_000.0)
    args = ap.parse_args()
    payload = run(args)
    g = payload["portfolio_gate"]
    print(json.dumps({
        "top_count": payload["top_count"],
        "portfolio_ok": g.get("ok"),
        "reason": g.get("reason"),
        "total_pnl": g.get("total_pnl"),
        "tail_pnl": g.get("tail_pnl"),
        "flat": g.get("flat_window_ratio"),
        "tail_flat": g.get("tail_flat_window_ratio"),
        "stagnation_score": g.get("stagnation_score"),
        "live_orders": payload["live_orders"],
        "json_path": payload["json_path"],
        "md_path": payload["md_path"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

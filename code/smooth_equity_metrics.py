#!/usr/bin/env python3
"""Smooth equity metrics for professional portfolio construction.

Goal: prefer equity curves that look like a smooth line from bottom-left to
upper-right: low volatility, fast recovery, low time under water, positive tail.
Safety: pure math, no broker calls.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return default
        return x
    except Exception:
        return default


def linear_r2(vals: List[float]) -> float:
    n = len(vals)
    if n < 3:
        return 0.0
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(vals) / n
    ss_xx = sum((x - mx) ** 2 for x in xs) or 1.0
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, vals)) / ss_xx
    intercept = my - slope * mx
    ss_tot = sum((y - my) ** 2 for y in vals)
    if ss_tot <= 0:
        return 0.0
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, vals))
    return max(0.0, min(1.0, 1.0 - ss_res / ss_tot))


def slope(vals: List[float]) -> float:
    n = len(vals)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(vals) / n
    den = sum((x - mx) ** 2 for x in xs) or 1.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, vals)) / den


def max_drawdown(vals: List[float]) -> float:
    if not vals:
        return 0.0
    peak = vals[0]
    dd = 0.0
    for v in vals:
        peak = max(peak, v)
        dd = max(dd, peak - v)
    return dd


def drawdown_series(vals: List[float]) -> List[float]:
    peak = vals[0] if vals else 0.0
    out = []
    for v in vals:
        peak = max(peak, v)
        out.append(max(0.0, peak - v))
    return out


def max_time_under_water(vals: List[float]) -> int:
    if not vals:
        return 0
    peak = vals[0]
    cur = 0
    best = 0
    for v in vals:
        if v >= peak:
            peak = v
            cur = 0
        else:
            cur += 1
            best = max(best, cur)
    return best


def drawdown_clusters(vals: List[float], threshold_ratio: float = 0.10) -> int:
    if not vals:
        return 0
    total = abs(vals[-1] - vals[0]) or max(abs(max(vals) - min(vals)), 1.0)
    thr = max(total * threshold_ratio, 1e-9)
    in_cluster = False
    clusters = 0
    for dd in drawdown_series(vals):
        if dd > thr and not in_cluster:
            clusters += 1
            in_cluster = True
        elif dd <= thr:
            in_cluster = False
    return clusters


def stdev(xs: List[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def returns(vals: List[float]) -> List[float]:
    return [b - a for a, b in zip(vals, vals[1:])]


def monotonicity_score(vals: List[float]) -> float:
    rs = returns(vals)
    if not rs:
        return 0.0
    positive = sum(1 for r in rs if r >= 0) / len(rs)
    total = vals[-1] - vals[0]
    gross = sum(abs(r) for r in rs) or 1.0
    efficiency = max(0.0, total) / gross
    return round(max(0.0, min(100.0, positive * 45.0 + efficiency * 55.0)), 4)


def ulcer_index(vals: List[float]) -> float:
    if not vals:
        return 0.0
    dds = drawdown_series(vals)
    scale = max(abs(vals[-1] - vals[0]), max(abs(v) for v in vals), 1.0)
    pct = [(dd / scale) * 100.0 for dd in dds]
    return math.sqrt(sum(x * x for x in pct) / len(pct))


def smooth_equity_metrics(vals: List[float], tail_frac: float = 0.25, micro_frac: float = 0.10) -> Dict[str, Any]:
    vals = [safe_float(v) for v in vals]
    if len(vals) < 10:
        return {"smooth_ok": False, "smooth_reason": "too_short", "n": len(vals)}
    total = vals[-1] - vals[0]
    dd = max_drawdown(vals)
    rs = returns(vals)
    eq_vol = stdev(rs)
    r2 = linear_r2(vals)
    sl = slope(vals)
    tuw = max_time_under_water(vals)
    clusters = drawdown_clusters(vals)
    ulcer = ulcer_index(vals)
    recovery = total / max(dd, 1e-9)
    pnl_dd = total / max(dd, 1e-9)
    tail_n = max(5, int(len(vals) * tail_frac))
    micro_n = max(5, int(len(vals) * micro_frac))
    tail = vals[-tail_n:]
    micro = vals[-micro_n:]
    tail_pnl = tail[-1] - tail[0]
    micro_pnl = micro[-1] - micro[0]
    tail_sl = slope(tail)
    micro_sl = slope(micro)
    mono = monotonicity_score(vals)
    smooth_score = round(
        r2 * 220.0
        + max(0.0, min(8.0, pnl_dd)) * 90.0
        + max(0.0, min(8.0, recovery)) * 40.0
        + mono * 3.0
        + max(0.0, tail_sl) * 25.0
        + max(0.0, micro_sl) * 25.0
        - ulcer * 22.0
        - eq_vol * 0.08
        - min(120.0, tuw / max(1, len(vals)) * 180.0)
        - clusters * 18.0,
        4,
    )
    dd_ratio = dd / max(abs(total), 1e-9)
    smooth_ok = (
        total > 0
        and sl > 0
        and tail_pnl > 0
        and micro_pnl > 0
        and tail_sl > 0
        and micro_sl > 0
        and r2 >= 0.55
        and dd_ratio <= 0.55
        and ulcer <= 8.0
        and tuw <= int(len(vals) * 0.22)
        and clusters <= 4
        and smooth_score > 350.0
    )
    reason = "ok" if smooth_ok else (
        f"smooth_fail total={total:.2f} r2={r2:.2f} dd_ratio={dd_ratio:.2f} "
        f"ulcer={ulcer:.2f} tuw={tuw} clusters={clusters} tail={tail_pnl:.2f} micro={micro_pnl:.2f} score={smooth_score:.1f}"
    )
    return {
        "smooth_ok": bool(smooth_ok),
        "smooth_reason": reason,
        "smooth_score": smooth_score,
        "n": len(vals),
        "total_pnl": round(total, 4),
        "max_drawdown": round(dd, 4),
        "dd_ratio": round(dd_ratio, 4),
        "equity_r2": round(r2, 4),
        "equity_slope": round(sl, 6),
        "equity_volatility": round(eq_vol, 6),
        "ulcer_index": round(ulcer, 4),
        "max_time_under_water": int(tuw),
        "drawdown_cluster_count": int(clusters),
        "recovery_factor": round(recovery, 4),
        "pnl_dd_ratio": round(pnl_dd, 4),
        "tail_pnl": round(tail_pnl, 4),
        "tail_slope": round(tail_sl, 6),
        "micro_tail_pnl": round(micro_pnl, 4),
        "micro_tail_slope": round(micro_sl, 6),
        "monotonicity_score": mono,
    }


def economic_value(metrics: Dict[str, Any], min_total_pnl: float = 1.0, min_avg_trade: float = 0.05,
                   capital_rub: float = 0.0, min_total_pnl_pct: float = 0.01, min_avg_trade_pct: float = 0.0001) -> Dict[str, Any]:
    pnl = safe_float(metrics.get("total_pnl"))
    avg = safe_float(metrics.get("avg_trade"))
    trades = safe_float(metrics.get("trade_count") or metrics.get("trades"))
    pf = safe_float(metrics.get("profit_factor"))
    # Audit P1 fix: thresholds must scale with real capital, otherwise noise
    # (pnl=1.16 RUB on a 20k deposit) passes as "economically viable".
    eff_min_pnl = min_total_pnl
    eff_min_avg = min_avg_trade
    if capital_rub > 0:
        eff_min_pnl = max(min_total_pnl, capital_rub * min_total_pnl_pct)
        eff_min_avg = max(min_avg_trade, capital_rub * min_avg_trade_pct)
    ok = pnl >= eff_min_pnl and avg >= eff_min_avg and trades >= 30 and pf >= 1.1
    score = round(pnl + avg * 25.0 + max(0.0, pf - 1.0) * 10.0 - max(0.0, 8.0 - trades) * 5.0, 4)
    reason = "ok" if ok else (
        f"economic_fail pnl={pnl:.2f} (min {eff_min_pnl:.2f}) avg={avg:.4f} (min {eff_min_avg:.2f}) "
        f"trades={trades:.0f} (min 30) pf={pf:.2f}"
    )
    return {
        "economic_ok": bool(ok),
        "economic_score": score,
        "economic_reason": reason,
    }

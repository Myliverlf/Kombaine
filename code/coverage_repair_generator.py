#!/usr/bin/env python3
"""Coverage Repair Generator — targeted repair loop for tickers missing from balanced top.

Reads latest Strategy Architect cycle, finds tickers absent from top because of
shape/quality failure, then runs a focused strategy-family grid for those tickers.
Outputs JSON/Markdown repair plan. Safety: local CSV/backtest only, no registry
writes, no broker calls, live_orders=0.
"""
from __future__ import annotations

import argparse
import json
import math
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
REPAIR_DIR = REPORT_DIR / "coverage_repair"

sys.path.insert(0, str(FUTURES_LAB))
sys.path.insert(0, str(PROJECT_ROOT / "code"))

from futures_lab import ZOO_PARAM_GRIDS, run_backtest, _synthetic_spec_for_file  # type: ignore  # noqa:E402
from equity_shape_filter import equity_shape_metrics  # type: ignore  # noqa:E402
from strategy_architect_autopilot import (  # type: ignore  # noqa:E402
    archetype_match_score,
    portfolio_score,
    safe_float,
    trade_bucket,
)

# Families chosen by prop-desk logic: trend/breakout for flat curves, reversion
# and channel families for overextended moves, plus a small exploration tail.
DEFAULT_REPAIR_FAMILIES = [
    "atr_breakout",
    "donchian_breakout",
    "opening_range_breakout",
    "intraday_momentum",
    "dual_ma_adx_filter",
    "supertrend_ema_combo",
    "ft_supertrend",
    "vwap_bands",
    "keltner_reversion",
    "cci_channel_breakout",
    "macd_histogram_reversal",
    "volatility_squeeze",
    "bollinger_reversion",
    "stochastic_cross",
]

FX_TICKERS = {"CNY", "EURRUB", "USDRUB"}
COMMODITY_TICKERS = {"BR", "NG"}
INDEX_TICKERS = {"IMOEX"}


def latest_cycle_path() -> Path:
    paths = sorted(REPORT_DIR.glob("cycle_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not paths:
        raise FileNotFoundError(f"No cycle_*.json in {REPORT_DIR}")
    return paths[0]


def load_latest_cycle(path: Path | None = None) -> Dict[str, Any]:
    p = path or latest_cycle_path()
    data = json.loads(p.read_text(encoding="utf-8"))
    data["_cycle_path"] = str(p)
    return data


def missing_repair_tickers(cycle: Dict[str, Any], include_selected: bool = False) -> List[Dict[str, Any]]:
    rows = []
    for r in cycle.get("coverage_reasons", []) or []:
        reason = str(r.get("reason") or "")
        if include_selected or not r.get("in_top"):
            if "shape_fail" in reason or "no_keep" in reason or not r.get("in_top"):
                rows.append(dict(r))
    return rows


def repair_families_for_ticker(ticker: str, reason: str, deep: bool = False) -> List[str]:
    if deep:
        return [k for k in ZOO_PARAM_GRIDS.keys() if k not in {"ft_low_bb"}]
    families = list(DEFAULT_REPAIR_FAMILIES)
    if ticker in FX_TICKERS:
        families = [
            "atr_breakout", "donchian_breakout", "opening_range_breakout",
            "dual_ma_adx_filter", "macd_histogram_reversal", "stochastic_cross",
            "bollinger_reversion", "keltner_reversion", "vwap_bands",
        ]
    elif ticker in COMMODITY_TICKERS:
        families = [
            "atr_breakout", "donchian_breakout", "intraday_momentum",
            "volatility_squeeze", "supertrend_ema_combo", "ft_supertrend",
            "keltner_reversion", "cci_channel_breakout", "vwap_bands",
        ]
    elif ticker in INDEX_TICKERS:
        families = [
            "opening_range_breakout", "intraday_momentum", "dual_ma_adx_filter",
            "atr_breakout", "donchian_breakout", "volatility_squeeze",
            "vwap_bands", "macd_histogram_reversal",
        ]
    return [f for f in families if f in ZOO_PARAM_GRIDS]


def csv_path(ticker: str, timeframe: str) -> Path:
    return DATA_ROOT / f"{ticker}_60d_{timeframe}_continuous.csv"


def candidate_params(strategy: str, max_params: int) -> List[Dict[str, Any]]:
    return list((ZOO_PARAM_GRIDS.get(strategy) or [])[:max_params])


def decision(metrics: Dict[str, Any], shape: Dict[str, Any], min_trades: int, min_pf: float) -> str:
    trades = int(metrics.get("trade_count", 0) or 0)
    pnl = safe_float(metrics.get("total_pnl"))
    pf = safe_float(metrics.get("profit_factor"))
    sharpe = safe_float(metrics.get("sharpe"))
    if shape.get("equity_shape_passed") and trades >= min_trades and pnl > 0 and pf >= min_pf and sharpe >= 0:
        return "repair_keep"
    if pnl > 0 and pf >= 1.0 and trades >= max(2, min_trades // 2):
        return "repair_near_miss"
    return "repair_drop"


def score_row(row: Dict[str, Any]) -> Dict[str, Any]:
    row["trade_bucket"] = trade_bucket(row.get("trades"))
    row["archetype_match_score"] = archetype_match_score(row)
    row["portfolio_score"] = portfolio_score(row)
    row["repair_score"] = round(
        safe_float(row.get("portfolio_score"))
        + safe_float(row.get("archetype_match_score")) * 120.0
        + max(0.0, safe_float(row.get("profit_factor")) - 1.0) * 800.0
        - safe_float(row.get("equity_shape_flat_window_ratio")) * 2500.0
        - max(0.0, 40.0 - safe_float(row.get("trades"))) * 35.0,
        4,
    )
    return row


def run_repair_for_ticker(
    ticker_info: Dict[str, Any],
    timeframes: Iterable[str],
    max_params: int,
    min_bars: int,
    min_trades: int,
    min_pf: float,
    initial_cash: float,
    deep: bool = False,
) -> Dict[str, Any]:
    ticker = str(ticker_info.get("ticker"))
    reason = str(ticker_info.get("reason") or "")
    families = repair_families_for_ticker(ticker, reason, deep=deep)
    rows: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    tested = 0
    for tf in timeframes:
        p = csv_path(ticker, tf)
        if not p.exists():
            errors.append({"timeframe": tf, "reason": "missing_csv", "path": str(p)})
            continue
        df = pd.read_csv(p)
        if len(df) < min_bars:
            errors.append({"timeframe": tf, "reason": "not_enough_bars", "bars": len(df), "min_bars": min_bars})
            continue
        spec = _synthetic_spec_for_file(ticker)
        for family in families:
            for params in candidate_params(family, max_params):
                tested += 1
                try:
                    metrics, trades, eq = run_backtest(
                        df,
                        spec,
                        family,
                        params,
                        initial_cash=initial_cash,
                        contracts=1,
                        max_contracts=1,
                    )
                except Exception as exc:
                    errors.append({"timeframe": tf, "strategy": family, "reason": str(exc)[:220]})
                    continue
                shape = equity_shape_metrics(eq, initial_cash)
                wins = sum(1 for t in trades if t.pnl > 0)
                losses = sum(1 for t in trades if t.pnl < 0)
                row = {
                    "ticker": ticker,
                    "timeframe": tf,
                    "strategy": family,
                    "params": params,
                    "decision": None,
                    "trades": int(metrics.get("trade_count", 0) or 0),
                    "wins": wins,
                    "losses": losses,
                    "win_rate": safe_float(metrics.get("win_rate_pct")),
                    "total_pnl": round(safe_float(metrics.get("total_pnl")), 2),
                    "profit_factor": safe_float(metrics.get("profit_factor")),
                    "max_drawdown": round(safe_float(metrics.get("max_drawdown")), 2),
                    "sharpe": round(safe_float(metrics.get("sharpe")), 4),
                    "repair_origin_reason": reason,
                    **shape,
                }
                row["decision"] = decision(metrics, shape, min_trades=min_trades, min_pf=min_pf)
                rows.append(score_row(row))
    rows.sort(key=lambda r: (r.get("decision") == "repair_keep", r.get("decision") == "repair_near_miss", safe_float(r.get("repair_score"))), reverse=True)
    keep = [r for r in rows if r.get("decision") == "repair_keep"]
    near = [r for r in rows if r.get("decision") == "repair_near_miss"]
    best = rows[0] if rows else None
    if keep:
        action = "promote_to_next_architect_policy_candidate"
    elif near:
        action = "expand_param_cloud_around_near_miss"
    else:
        action = "switch_family_or_data_regime; current families still shape_fail"
    return {
        "ticker": ticker,
        "original_reason": reason,
        "families": families,
        "tested": tested,
        "errors": errors[:20],
        "keep_count": len(keep),
        "near_miss_count": len(near),
        "best": best,
        "top_candidates": rows[:10],
        "action": action,
    }


def render_markdown(payload: Dict[str, Any]) -> str:
    lines = [
        "# Coverage Repair Generator — latest",
        "",
        f"- source_cycle: {payload.get('source_cycle')}",
        f"- tickers_repaired: {payload.get('tickers_repaired')}",
        f"- rows_tested: {payload.get('rows_tested')}",
        f"- repair_keep: {payload.get('repair_keep_total')}",
        f"- near_miss: {payload.get('near_miss_total')}",
        f"- live_orders: {payload.get('live_orders')}",
        "",
        "## Summary",
        "",
        "| ticker | tested | keep | near | action | best | tf | trades | pnl | PF | archetype | flat | reason |",
        "|---|---:|---:|---:|---|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for r in payload.get("repairs", []):
        b = r.get("best") or {}
        lines.append(
            f"| {r.get('ticker')} | {r.get('tested')} | {r.get('keep_count')} | {r.get('near_miss_count')} | {r.get('action')} | "
            f"{b.get('strategy')} | {b.get('timeframe')} | {b.get('trades', 0)} | {safe_float(b.get('total_pnl')):.2f} | "
            f"{safe_float(b.get('profit_factor')):.2f} | {safe_float(b.get('archetype_match_score')):.1f} | "
            f"{safe_float(b.get('equity_shape_flat_window_ratio')):.2f} | {b.get('equity_shape_reason', '')} |"
        )
    lines.extend(["", "## Best candidates", ""])
    for r in payload.get("repairs", []):
        lines.append(f"### {r.get('ticker')} — {r.get('action')}")
        lines.append("")
        lines.append("| decision | tf | strategy | bucket | trades | W/L | pnl | PF | DD | archetype | repair_score | shape_reason |")
        lines.append("|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|")
        for c in r.get("top_candidates", [])[:5]:
            lines.append(
                f"| {c.get('decision')} | {c.get('timeframe')} | {c.get('strategy')} | {c.get('trade_bucket')} | {c.get('trades')} | "
                f"{c.get('wins')}/{c.get('losses')} | {safe_float(c.get('total_pnl')):.2f} | {safe_float(c.get('profit_factor')):.2f} | "
                f"{safe_float(c.get('max_drawdown')):.2f} | {safe_float(c.get('archetype_match_score')):.1f} | {safe_float(c.get('repair_score')):.2f} | {c.get('equity_shape_reason')} |"
            )
        lines.append("")
    lines.append("Safety: local backtest only, no registry writes, live_orders=0.")
    return "\n".join(lines)


def run(args: argparse.Namespace) -> Dict[str, Any]:
    cycle = load_latest_cycle(Path(args.cycle) if args.cycle else None)
    tickers = missing_repair_tickers(cycle, include_selected=args.include_selected)
    if args.tickers:
        wanted = {t.strip() for t in args.tickers.split(",") if t.strip()}
        tickers = [r for r in tickers if str(r.get("ticker")) in wanted]
    repairs = [
        run_repair_for_ticker(
            t,
            timeframes=[x.strip() for x in args.timeframes.split(",") if x.strip()],
            max_params=args.max_params,
            min_bars=args.min_bars,
            min_trades=args.min_trades,
            min_pf=args.min_pf,
            initial_cash=args.initial_cash,
            deep=args.deep,
        )
        for t in tickers
    ]
    payload = {
        "ts": time.strftime("%Y%m%d_%H%M%S", time.gmtime()),
        "source_cycle": cycle.get("_cycle_path"),
        "tickers_repaired": len(repairs),
        "rows_tested": sum(int(r.get("tested", 0)) for r in repairs),
        "repair_keep_total": sum(int(r.get("keep_count", 0)) for r in repairs),
        "near_miss_total": sum(int(r.get("near_miss_count", 0)) for r in repairs),
        "repairs": repairs,
        "live_orders": 0,
    }
    REPAIR_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPAIR_DIR / f"repair_{payload['ts']}.json"
    md_path = REPAIR_DIR / "latest.md"
    latest_json = REPAIR_DIR / "latest.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    payload["json_path"] = str(json_path)
    payload["md_path"] = str(md_path)
    return payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycle", default=None)
    ap.add_argument("--tickers", default=None)
    ap.add_argument("--timeframes", default="15m,1h")
    ap.add_argument("--max-params", type=int, default=8)
    ap.add_argument("--min-bars", type=int, default=300)
    ap.add_argument("--min-trades", type=int, default=8)
    ap.add_argument("--min-pf", type=float, default=1.05)
    ap.add_argument("--initial-cash", type=float, default=1_000_000.0)
    ap.add_argument("--include-selected", action="store_true")
    ap.add_argument("--deep", action="store_true")
    args = ap.parse_args()
    payload = run(args)
    print(json.dumps({k: payload[k] for k in ["tickers_repaired", "rows_tested", "repair_keep_total", "near_miss_total", "live_orders", "json_path", "md_path"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Per-ticker smoke backtest for staged universe expansion.

The module is deliberately local-only: it uses futures_lab backtest helpers and
local CSV files, never broker APIs and never live orders.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROP_ROOT = PROJECT_ROOT.parent
FUTURES_LAB = PROP_ROOT / "futures_lab"
DATA_ROOT = FUTURES_LAB / "artifacts" / "tinkoff_futures_data"
REPORT_DIR = PROJECT_ROOT / "reports" / "strategy_architect"
DEFAULT_TIMEFRAMES = ("15m", "1h")
DEFAULT_EXCLUDED = {"RI"}

import sys
sys.path.insert(0, str(FUTURES_LAB))
sys.path.insert(0, str(PROJECT_ROOT / "code"))

from futures_lab import run_backtest, _synthetic_spec_for_file  # type: ignore  # noqa:E402
from staged_universe_audit import build_universe_candidates  # noqa:E402
from strategy_combine_dryrun_env import default_safe_config, merge_safe_config  # noqa:E402


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _load_df(path: Path):
    import pandas as pd
    return pd.read_csv(path)


def available_ticker_paths(root: str, data_root: Path = DATA_ROOT) -> Dict[str, Path]:
    return {
        tf: data_root / f"{root}_60d_{tf}_continuous.csv"
        for tf in DEFAULT_TIMEFRAMES
    }


def build_smoke_candidates(root: str, max_strategies: int = 4) -> List[Dict[str, Any]]:
    """Generate a small set of local-only smoke candidates for a ticker."""
    return [
        {"strategy": "sma_cross", "params": {"fast": 7, "slow": 21}},
        {"strategy": "rsi_reversal", "params": {"period": 14, "low": 35, "high": 65}},
        {"strategy": "bollinger_reversion", "params": {"period": 20, "std": 2.0}},
        {"strategy": "atr_breakout", "params": {"period": 14, "mult": 1.5}},
    ][:max_strategies]


def smoke_backtest_ticker(
    root: str,
    timeframes: Iterable[str] = DEFAULT_TIMEFRAMES,
    max_strategies: int = 4,
    initial_cash: float = 100000.0,
    max_contracts: int = 1,
) -> Dict[str, Any]:
    """Run smoke backtests for a single ticker across local CSV timeframes."""
    excluded = {x.upper() for x in DEFAULT_EXCLUDED}
    if root.upper() in excluded:
        return {
            "root": root,
            "excluded": True,
            "ready": False,
            "results": [],
            "reason": "excluded_ticker",
            "live_orders": 0,
        }

    config = merge_safe_config({"deposit_rub": initial_cash, "risk": {"max_contracts_per_entry": max_contracts}})
    spec_cache = _synthetic_spec_for_file(root)
    outcomes: List[Dict[str, Any]] = []
    for tf in timeframes:
        path = DATA_ROOT / f"{root}_60d_{tf}_continuous.csv"
        if not path.exists():
            outcomes.append({"timeframe": tf, "path": str(path), "status": "missing"})
            continue
        df = _load_df(path)
        if len(df) < 300:
            outcomes.append({"timeframe": tf, "path": str(path), "status": "too_short", "rows": len(df)})
            continue
        # futures_lab.run_backtest works with a file-derived spec and local data only.
        for candidate in build_smoke_candidates(root, max_strategies=max_strategies):
            try:
                metrics, trades, equity = run_backtest(
                    df,
                    spec_cache,
                    candidate["strategy"],
                    candidate["params"],
                    initial_cash=initial_cash,
                    contracts=1,
                    max_contracts=max_contracts,
                )
                pnl = _safe_float(metrics.get("total_pnl"))
                trades_n = _safe_int(metrics.get("trade_count"), len(trades))
                outcome = {
                    "timeframe": tf,
                    "path": str(path),
                    "status": "ok" if trades_n > 0 else "warn",
                    "strategy": candidate["strategy"],
                    "params": candidate["params"],
                    "trade_count": trades_n,
                    "total_pnl": round(pnl, 2),
                    "profit_factor": _safe_float(metrics.get("profit_factor")),
                    "max_drawdown": _safe_float(metrics.get("max_drawdown")),
                    "sharpe": _safe_float(metrics.get("sharpe")),
                    "equity_points": len(equity),
                    "live_orders": 0,
                }
                outcomes.append(outcome)
                break
            except Exception as exc:
                outcomes.append({
                    "timeframe": tf,
                    "path": str(path),
                    "strategy": candidate["strategy"],
                    "params": candidate["params"],
                    "status": "error",
                    "error": str(exc),
                    "live_orders": 0,
                })
    ready = any(item.get("status") == "ok" for item in outcomes)
    return {
        "root": root,
        "ready": ready,
        "excluded": False,
        "results": outcomes,
        "live_orders": 0,
    }


def smoke_backtest_universe(target_size: int = 20) -> Dict[str, Any]:
    """Run smoke backtests for every ready root in the staged universe."""
    audit = build_universe_candidates(target_size=target_size)
    roots = [item["root"] for item in audit["selected"] if item.get("ready") and not item.get("excluded")]
    results = [smoke_backtest_ticker(root) for root in roots]
    candidates = [item for item in results if item.get("ready")]
    blockers = [item for item in results if not item.get("ready")]
    return {
        "target_size": target_size,
        "roots": roots,
        "candidates": candidates,
        "blockers": blockers,
        "pass_count": len(candidates),
        "blocker_count": len(blockers),
        "live_orders": 0,
    }


def render_markdown(payload: Dict[str, Any]) -> str:
    lines = [
        "# Ticker smoke backtest",
        "",
        f"- target_size: {payload['target_size']}",
        f"- pass_count: {payload['pass_count']}",
        f"- blocker_count: {payload['blocker_count']}",
        f"- live_orders: {payload['live_orders']}",
        "",
        "## Candidates",
        "| root | status | first_ok_strategy | first_ok_tf |",
        "|---|---|---|---|",
    ]
    for item in payload["candidates"]:
        ok = next((r for r in item.get("results", []) if r.get("status") == "ok"), {})
        lines.append(f"| {item['root']} | ready | {ok.get('strategy','-')} | {ok.get('timeframe','-')} |")
    lines.extend(["", "## Blockers", "| root | reason |", "|---|---|"])
    for item in payload["blockers"]:
        reason = item.get("reason")
        if not reason:
            missing = [r for r in item.get("results", []) if r.get("status") in {"missing", "too_short", "error"}]
            reason = missing[0].get("status", "unknown") if missing else "unknown"
        lines.append(f"| {item['root']} | {reason} |")
    return "\n".join(lines)


def write_reports(payload: Dict[str, Any]) -> Dict[str, str]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / "ticker_smoke_backtest_latest.json"
    md_path = REPORT_DIR / "ticker_smoke_backtest_latest.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def main() -> int:
    payload = smoke_backtest_universe(target_size=20)
    paths = write_reports(payload)
    print(json.dumps({
        "pass_count": payload["pass_count"],
        "blocker_count": payload["blocker_count"],
        "roots": payload["roots"],
        "live_orders": 0,
        "reports": paths,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

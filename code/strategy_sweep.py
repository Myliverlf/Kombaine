"""Dry-run sweep across tickers and timeframes.

This module bridges the current combine pipeline in a non-invasive way:
- it reads strategy-like fixtures for (ticker, timeframe)
- never places orders
- produces one record per sweep row with metrics and keep/drop/retest output

The output is intentionally compact and test-friendly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from perf_metrics import compute_performance_metrics, summarize_metric_direction


DEFAULT_TIMEFRAMES = ("15m", "1h")
DEFAULT_KEEP_PNL_MIN = 0.0
DEFAULT_KEEP_PF_MIN = 1.1
DEFAULT_KEEP_WR_MIN = 0.5
DEFAULT_DROP_DD_MAX = 0.18
DEFAULT_RETEST_SHARPE_MIN = 0.25


@dataclass(frozen=True)
class SweepResult:
    ticker: str
    timeframe: str
    strategy: str
    decision: str
    metrics: Dict[str, Any]
    note: str = ""


def _row_to_trade(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "ticker": str(row.get("ticker", "")).upper(),
        "timeframe": str(row.get("timeframe", "")),
        "strategy": str(row.get("strategy", "")),
        "pnl": float(row.get("pnl", row.get("profit", 0.0))),
        "contracts": int(row.get("contracts", 1) or 1),
    }


def _decision_from_metrics(metrics: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    pnl = float(metrics.get("PnL", 0.0))
    pf = float(metrics.get("PF", metrics.get("profit_factor", 0.0)))
    wr = float(metrics.get("WR", metrics.get("win_rate", 0.0)))
    dd = float(metrics.get("DD", metrics.get("dd", 0.0)))
    sharpe = float(metrics.get("sharpe", 0.0))

    keep = (
        pnl >= float(config.get("keep_pnl_min", DEFAULT_KEEP_PNL_MIN))
        and pf >= float(config.get("keep_pf_min", DEFAULT_KEEP_PF_MIN))
        and wr >= float(config.get("keep_wr_min", DEFAULT_KEEP_WR_MIN))
        and dd <= float(config.get("keep_dd_max", DEFAULT_DROP_DD_MAX))
    )
    if keep:
        return "keep"

    drop = pnl < 0 and dd >= float(config.get("drop_dd_max", DEFAULT_DROP_DD_MAX))
    if drop:
        return "drop"

    if sharpe >= float(config.get("retest_sharpe_min", DEFAULT_RETEST_SHARPE_MIN)):
        return "retest"
    return "drop"


def sweep_strategy_universe(
    strategy_rows: Sequence[Mapping[str, Any]],
    *,
    timeframes: Sequence[str] = DEFAULT_TIMEFRAMES,
    config: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Run a fixture-backed dry-run sweep across ticker/timeframe pairs.

    Each input row represents a strategy fixture with a ticker and a set of
    trade PnLs. We expand it across the requested timeframes.
    """
    cfg = dict(config or {})
    results: List[Dict[str, Any]] = []

    for row in strategy_rows:
        ticker = str(row.get("ticker", "")).upper()
        strategy = str(row.get("strategy", ""))
        base_trades = list(row.get("trades", []))
        for timeframe in timeframes:
            trades = [dict(t, ticker=ticker, timeframe=timeframe, strategy=strategy) for t in base_trades]
            metrics = compute_performance_metrics(trades, start_equity=row.get("start_equity", 100_000.0))
            decision = _decision_from_metrics(metrics, cfg)
            results.append({
                "ticker": ticker,
                "timeframe": timeframe,
                "strategy": strategy,
                "metrics": metrics,
                "decision": decision,
                "sweep_key": f"{ticker}:{timeframe}:{strategy}",
                "holdout": bool(row.get("holdout", False)),
            })

    return {
        "results": results,
        "summary": {
            "n_rows": len(results),
            "keep": sum(1 for row in results if row["decision"] == "keep"),
            "drop": sum(1 for row in results if row["decision"] == "drop"),
            "retest": sum(1 for row in results if row["decision"] == "retest"),
            "timeframes": list(timeframes),
        },
    }


def build_strategy_scorecard(
    strategy_rows: Sequence[Mapping[str, Any]],
    *,
    timeframes: Sequence[str] = DEFAULT_TIMEFRAMES,
    config: Mapping[str, Any] | None = None,
    baseline: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Return a combined sweep + scorecard artifact."""
    sweep = sweep_strategy_universe(strategy_rows, timeframes=timeframes, config=config)
    baseline = baseline or {"PnL": 0.0, "DD": 1.0, "sharpe": 0.0}
    results = []
    for row in sweep["results"]:
        direction = summarize_metric_direction(row["metrics"], baseline)
        results.append({**row, "direction": direction})
    scorecard = {
        "pnl_up": sum(1 for row in results if row["direction"]["pnl_up"]),
        "risk_down": sum(1 for row in results if row["direction"]["risk_down"]),
        "sharpe_up": sum(1 for row in results if row["direction"]["sharpe_up"]),
    }
    return {"sweep": {**sweep, "results": results}, "scorecard": scorecard}


__all__ = ["sweep_strategy_universe", "build_strategy_scorecard", "SweepResult"]

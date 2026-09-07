"""Performance metrics for strategy_combine dry-runs.

Pure standard-library helpers for fixture-driven evaluation of strategy
results across tickers/timeframes.

Exports a compact scorecard with the requested fields:
trades, wins, losses, PnL, PF, WR, DD, sharpe.

No network, no broker, no live orders.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from statistics import fmean
from typing import Any, Dict, Iterable, List, Mapping, Sequence


Number = float | int


@dataclass(frozen=True)
class Trade:
    ticker: str
    timeframe: str
    strategy: str
    pnl: float
    contracts: int = 1


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        x = float(value)
        if x != x:  # NaN guard
            return default
        return x
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_trades(trades: Sequence[Mapping[str, Any]] | Sequence[Trade]) -> List[Dict[str, Any]]:
    """Normalize input trades to a list of dictionaries.

    Accepted keys include pnl/profit, ticker, timeframe, strategy, contracts.
    """
    normalized: List[Dict[str, Any]] = []
    for trade in trades:
        row = dict(trade)
        row["ticker"] = str(row.get("ticker", "")).upper()
        row["timeframe"] = str(row.get("timeframe", ""))
        row["strategy"] = str(row.get("strategy", ""))
        row["pnl"] = _to_float(row.get("pnl", row.get("profit", 0.0)))
        row["contracts"] = max(1, _to_int(row.get("contracts", 1), 1))
        normalized.append(row)
    return normalized


def trade_pnls(trades: Sequence[Mapping[str, Any]] | Sequence[Trade]) -> List[float]:
    return [row["pnl"] for row in normalize_trades(trades)]


def build_equity_curve(
    trades: Sequence[Mapping[str, Any]] | Sequence[Trade],
    start_equity: Number = 100_000.0,
) -> List[float]:
    """Build an equity curve from trade PnLs.

    The first point is the starting equity and every trade appends a new point.
    """
    equity = float(start_equity)
    curve = [equity]
    for pnl in trade_pnls(trades):
        equity += pnl
        curve.append(equity)
    return curve


def _returns_from_curve(curve: Sequence[Number]) -> List[float]:
    values = [float(v) for v in curve]
    if len(values) < 2:
        return []
    returns: List[float] = []
    for prev, curr in zip(values, values[1:]):
        if prev == 0:
            returns.append(0.0)
        else:
            returns.append((curr - prev) / abs(prev))
    return returns


def compute_sharpe(returns: Sequence[Number], periods_per_year: int = 252) -> float:
    """Annualized Sharpe on simple returns."""
    vals = [float(r) for r in returns]
    if len(vals) < 2:
        return 0.0
    mean_r = fmean(vals)
    variance = sum((r - mean_r) ** 2 for r in vals) / (len(vals) - 1)
    if variance <= 0:
        return 0.0
    std = sqrt(variance)
    return (mean_r / std) * sqrt(periods_per_year)


def compute_drawdown(equity_curve: Sequence[Number]) -> Dict[str, float]:
    """Return drawdown in fraction and percent."""
    values = [float(v) for v in equity_curve]
    if not values:
        return {"dd": 0.0, "dd_pct": 0.0, "peak": 0.0, "trough": 0.0}
    peak = values[0]
    trough = values[0]
    max_dd = 0.0
    peak_at_dd = values[0]
    trough_at_dd = values[0]
    for value in values:
        if value > peak:
            peak = value
        drawdown = 0.0 if peak == 0 else (peak - value) / abs(peak)
        if drawdown > max_dd:
            max_dd = drawdown
            peak_at_dd = peak
            trough_at_dd = value
            trough = value
    return {
        "dd": round(max_dd, 6),
        "dd_pct": round(max_dd * 100.0, 3),
        "peak": round(peak_at_dd, 6),
        "trough": round(trough_at_dd, 6),
    }


def compute_trade_metrics(trades: Sequence[Mapping[str, Any]] | Sequence[Trade]) -> Dict[str, Any]:
    """Compute the requested trade metrics from a trade ledger."""
    rows = normalize_trades(trades)
    pnls = [row["pnl"] for row in rows]
    total_trades = len(rows)
    wins = sum(1 for pnl in pnls if pnl > 0)
    losses = sum(1 for pnl in pnls if pnl < 0)
    breakeven = total_trades - wins - losses
    pnl_total = round(sum(pnls), 6)
    wins_pnl = sum(pnl for pnl in pnls if pnl > 0)
    losses_pnl = sum(pnl for pnl in pnls if pnl < 0)
    profit_factor = 0.0
    if losses_pnl < 0:
        profit_factor = wins_pnl / abs(losses_pnl) if wins_pnl > 0 else 0.0
    elif wins_pnl > 0:
        profit_factor = float("inf")
    win_rate = (wins / total_trades) if total_trades else 0.0
    loss_rate = (losses / total_trades) if total_trades else 0.0
    avg_win = (wins_pnl / wins) if wins else 0.0
    avg_loss = (abs(losses_pnl) / losses) if losses else 0.0
    avg_trade = (pnl_total / total_trades) if total_trades else 0.0

    return {
        "trades": total_trades,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "PnL": pnl_total,
        "pnl": pnl_total,
        "PF": round(profit_factor, 6) if profit_factor != float("inf") else float("inf"),
        "profit_factor": round(profit_factor, 6) if profit_factor != float("inf") else float("inf"),
        "WR": round(win_rate, 6),
        "win_rate": round(win_rate, 6),
        "loss_rate": round(loss_rate, 6),
        "avg_win": round(avg_win, 6),
        "avg_loss": round(avg_loss, 6),
        "avg_trade": round(avg_trade, 6),
    }


def compute_performance_metrics(
    trades: Sequence[Mapping[str, Any]] | Sequence[Trade],
    equity_curve: Sequence[Number] | None = None,
    start_equity: Number = 100_000.0,
) -> Dict[str, Any]:
    """Return the combined metrics bundle for sweep/scorecard use."""
    rows = normalize_trades(trades)
    trade_metrics = compute_trade_metrics(rows)
    curve = list(equity_curve) if equity_curve is not None else build_equity_curve(rows, start_equity=start_equity)
    dd = compute_drawdown(curve)
    returns = _returns_from_curve(curve)
    sharpe = compute_sharpe(returns) if returns else 0.0

    metrics = {
        **trade_metrics,
        "equity_curve": [round(float(x), 6) for x in curve],
        "returns": [round(float(x), 8) for x in returns],
        "DD": dd["dd"],
        "dd_pct": dd["dd_pct"],
        "max_drawdown": dd["dd"],
        "max_drawdown_pct": dd["dd_pct"],
        "sharpe": round(sharpe, 6),
    }
    metrics["PnL_per_trade"] = round(metrics["PnL"] / metrics["trades"], 6) if metrics["trades"] else 0.0
    metrics["profit_per_drawdown"] = (
        round(metrics["PnL"] / (1.0 + dd["dd_pct"]), 6) if metrics["PnL"] else 0.0
    )
    return metrics


def summarize_metric_direction(candidate: Mapping[str, Any], baseline: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    """Convenience helper to highlight PnL↑/risk↓ in a dry-run bundle."""
    if baseline is None:
        baseline = {}
    pnl_up = _to_float(candidate.get("PnL", candidate.get("pnl", 0.0))) >= _to_float(
        baseline.get("PnL", baseline.get("pnl", 0.0))
    )
    risk_down = _to_float(candidate.get("DD", candidate.get("dd", 0.0))) <= _to_float(
        baseline.get("DD", baseline.get("dd", 1.0))
    )
    sharpe_up = _to_float(candidate.get("sharpe", 0.0)) >= _to_float(baseline.get("sharpe", 0.0))
    return {
        "pnl_up": pnl_up,
        "risk_down": risk_down,
        "sharpe_up": sharpe_up,
    }


__all__ = [
    "Trade",
    "normalize_trades",
    "trade_pnls",
    "build_equity_curve",
    "compute_sharpe",
    "compute_drawdown",
    "compute_trade_metrics",
    "compute_performance_metrics",
    "summarize_metric_direction",
]

#!/usr/bin/env python3
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from perf_metrics import compute_performance_metrics

SAFE_ACTIVE_STATUSES = {"active_signal_pool", "active_watchlist", "registry/candidate", "waitlist"}
KEEP_STATUSES = {"active_signal_pool"}
DROP_HINTS = ("drop", "reject", "blocked", "failed", "veto")
RETEST_HINTS = ("watchlist", "retest", "waitlist", "candidate")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        x = float(value)
        if x != x:
            return default
        return x
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _iter_strategy_records(strategy_registry: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    for strategy_id, record in (strategy_registry.get("strategies") or {}).items():
        if isinstance(record, dict):
            yield strategy_id, record


def _decision_for_record(record: dict[str, Any]) -> str:
    status = str(record.get("status") or "").lower()
    note = " ".join(
        str(part).lower()
        for part in (
            record.get("reason"),
            record.get("note"),
            record.get("reject_reason"),
        )
        if part
    )
    quality_gate = record.get("quality_gate") or {}
    if isinstance(quality_gate, dict) and not quality_gate.get("passed", True):
        return "drop"
    if any(hint in status or hint in note for hint in DROP_HINTS):
        return "drop"
    if status in KEEP_STATUSES:
        return "keep"
    if any(hint in status or hint in note for hint in RETEST_HINTS):
        return "retest"
    if _safe_float(record.get("rank_score") or record.get("active_rank")) > 0:
        return "retest"
    return "retest"


def build_trade_rows(strategy_registry: dict[str, Any], portfolio: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Build a read-only synthetic ledger from local registry metrics.

    We intentionally stay away from broker/order APIs and only use local state.
    Each active strategy contributes one synthetic row with its total_pnl, so the
    dashboard can compute PF/DD from the same fixture/state payloads that drive
    the pool/risk flow.
    """

    portfolio = portfolio or {}
    rows: list[dict[str, Any]] = []
    for strategy_id, record in _iter_strategy_records(strategy_registry):
        status = str(record.get("status") or "")
        if status not in SAFE_ACTIVE_STATUSES:
            continue
        metrics = record.get("metrics") or {}
        ticker = str(record.get("ticker") or "").upper()
        strategy = str(record.get("strategy") or "")
        pnl = _safe_float(metrics.get("total_pnl", metrics.get("pnl", 0.0)))
        contracts = max(1, _safe_int(record.get("contracts") or 1, 1))
        rows.append(
            {
                "ticker": ticker,
                "strategy": strategy,
                "strategy_id": strategy_id,
                "pnl": pnl,
                "profit": pnl,
                "contracts": contracts,
                "status": status,
                "win_rate": _safe_float(metrics.get("win_rate"), 0.0),
                "wins": _safe_int(metrics.get("wins"), 0),
                "losses": _safe_int(metrics.get("losses"), 0),
                "trades": _safe_int(metrics.get("trades"), 0),
                "max_drawdown": _safe_float(metrics.get("max_drawdown"), 0.0),
                "profit_factor": _safe_float(metrics.get("profit_factor"), 0.0),
                "go_rub": _safe_float(record.get("go_rub"), 0.0),
            }
        )

    if not rows:
        for slot_id, slot in (portfolio.get("slots") or {}).items():
            open_position = slot.get("open_position") or {}
            ticker = str(slot.get("ticker") or "").upper()
            strategy = str(slot.get("strategy") or "")
            rows.append(
                {
                    "ticker": ticker,
                    "strategy": strategy,
                    "strategy_id": slot_id,
                    "pnl": _safe_float(slot.get("pnl_rub"), 0.0),
                    "profit": _safe_float(slot.get("pnl_rub"), 0.0),
                    "contracts": max(1, _safe_int(slot.get("contracts") or 1, 1)),
                    "status": "open" if open_position else "empty",
                    "win_rate": 0.0,
                    "wins": 0,
                    "losses": 0,
                    "trades": _safe_int(slot.get("n_trades"), 0),
                    "max_drawdown": 0.0,
                    "profit_factor": 0.0,
                    "go_rub": _safe_float(slot.get("go_rub"), 0.0),
                }
            )
    return rows


def build_metrics_summary(strategy_registry: dict[str, Any], portfolio: dict[str, Any] | None = None) -> dict[str, Any]:
    portfolio = portfolio or {}
    rows = build_trade_rows(strategy_registry, portfolio)
    perf = compute_performance_metrics(rows)

    strategy_names = set()
    assets = set()
    decision_counts: Counter[str] = Counter()
    decision_rows: list[dict[str, Any]] = []
    total_trades = 0
    total_wins = 0
    total_losses = 0
    total_pnl = 0.0
    total_go_rub = 0.0
    total_risk = 0.0
    total_drawdown = 0.0

    for strategy_id, record in _iter_strategy_records(strategy_registry):
        status = str(record.get("status") or "")
        metrics = record.get("metrics") or {}
        ticker = str(record.get("ticker") or "").upper()
        strategy = str(record.get("strategy") or "")
        strategy_names.add(strategy or strategy_id)
        if ticker:
            assets.add(ticker)
        decision = _decision_for_record(record)
        decision_counts[decision] += 1
        total_trades += _safe_int(metrics.get("trades"), 0)
        total_wins += _safe_int(metrics.get("wins"), 0)
        total_losses += _safe_int(metrics.get("losses"), 0)
        total_pnl += _safe_float(metrics.get("total_pnl", metrics.get("pnl", 0.0)))
        total_go_rub += _safe_float(record.get("go_rub"), 0.0)
        total_drawdown += _safe_float(metrics.get("max_drawdown"), 0.0)
        total_risk += _safe_float(metrics.get("equity_shape_dd_ratio"), 0.0)
        decision_rows.append(
            {
                "strategy_id": strategy_id,
                "ticker": ticker,
                "strategy": strategy,
                "status": status,
                "decision": decision,
                "rank_score": round(_safe_float(record.get("rank_score"), 0.0), 4),
                "trades": _safe_int(metrics.get("trades"), 0),
                "pnl": round(_safe_float(metrics.get("total_pnl", metrics.get("pnl", 0.0))), 4),
                "win_rate": round(_safe_float(metrics.get("win_rate"), 0.0), 4),
                "reason": record.get("reason") or record.get("note") or record.get("reject_reason") or "",
            }
        )

    decision_rows.sort(key=lambda row: (row["decision"], row["rank_score"], row["pnl"]), reverse=True)
    active_open_tickers = {
        str(slot.get("ticker") or "").upper()
        for slot in (portfolio.get("slots") or {}).values()
        if slot.get("ticker")
    }
    assets.update(active_open_tickers)
    open_positions = sum(1 for slot in (portfolio.get("slots") or {}).values() if slot.get("open_position"))
    active_slots = len(portfolio.get("slots") or {})
    risk_budget = round(total_go_rub, 2)
    risk_density = round(total_drawdown / max(len(rows), 1), 6)
    pnl_to_risk = round(perf["PnL"] / (1.0 + perf["DD"]), 6) if perf["PnL"] else 0.0

    return {
        "counts": {
            "assets": len(assets),
            "strategies": len(strategy_names),
            "trades": total_trades or perf["trades"],
            "wins": total_wins or perf["wins"],
            "losses": total_losses or perf["losses"],
            "open_positions": open_positions,
            "slots": active_slots,
        },
        "performance": {
            "PnL": round(perf["PnL"], 6),
            "PF": perf["PF"],
            "DD": perf["DD"],
            "WR": perf["WR"],
            "win_rate": perf["win_rate"],
            "loss_rate": perf["loss_rate"],
            "avg_win": perf["avg_win"],
            "avg_loss": perf["avg_loss"],
        },
        "risk": {
            "overall": "block" if (portfolio.get("halted") or perf["DD"] > 0.0) else "pass",
            "risk_budget": risk_budget,
            "risk_density": risk_density,
            "pnl_to_risk": pnl_to_risk,
            "go_rub_total": round(total_go_rub, 2),
            "mean_drawdown": round(total_drawdown / max(len(rows), 1), 6),
            "mean_risk_signal": round(total_risk / max(len(rows), 1), 6),
        },
        "decisions": {
            "keep": decision_counts.get("keep", 0),
            "drop": decision_counts.get("drop", 0),
            "retest": decision_counts.get("retest", 0),
            "items": decision_rows[:15],
        },
        "ledger": rows,
        "perf": perf,
    }


def build_allocator_scorecard(metrics: dict[str, Any], limits: dict[str, Any]) -> dict[str, Any]:
    max_slots = max(1, _safe_int(limits.get("max_live_slots"), 3))
    max_contracts = max(1, _safe_int(limits.get("max_contracts_per_entry"), 1))
    slots = _safe_int(metrics.get("counts", {}).get("slots"), 0)
    open_positions = _safe_int(metrics.get("counts", {}).get("open_positions"), 0)
    pnl = _safe_float(metrics.get("performance", {}).get("PnL"), 0.0)
    dd = _safe_float(metrics.get("performance", {}).get("DD"), 0.0)
    pf = _safe_float(metrics.get("performance", {}).get("PF"), 0.0)
    risk_density = _safe_float(metrics.get("risk", {}).get("risk_density"), 0.0)
    compliance = 1.0 if slots <= max_slots and max_contracts >= 1 else 0.0
    efficiency = 0.0
    if pnl or dd:
        efficiency = max(0.0, min(1.0, (pnl / (1.0 + abs(dd))) / 1000.0 + 0.5))
    risk_score = max(0.0, min(1.0, 1.0 - min(0.95, dd + risk_density)))
    allocator_score = round((compliance + efficiency + risk_score) / 3.0, 6)
    direction = {
        "pnl_up": pnl >= 0.0,
        "risk_down": dd <= max(0.0, 0.15),
        "pf_ok": pf >= 1.0,
    }
    return {
        "composite_score": allocator_score,
        "compliance_score": compliance,
        "efficiency_score": round(efficiency, 6),
        "risk_score": round(risk_score, 6),
        "direction": direction,
        "limits": {
            "max_live_slots": max_slots,
            "max_contracts_per_entry": max_contracts,
            "open_positions": open_positions,
            "slots": slots,
        },
    }

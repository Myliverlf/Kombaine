#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _human_age(ts: Any) -> str:
    try:
        age_hours = max((datetime.now(timezone.utc).timestamp() - float(ts)) / 3600.0, 0.0)
    except (TypeError, ValueError):
        return "unknown"
    if age_hours < 1:
        return f"{age_hours * 60:.0f} min"
    if age_hours < 48:
        return f"{age_hours:.1f} h"
    return f"{age_hours / 24.0:.1f} d"


def build_candidate_waitlist(
    strategy_registry: dict[str, Any],
    waitlist: dict[str, Any] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Build a human-readable waitlist snapshot.

    The canonical source is the registry; legacy waitlist.json is used as a
    fallback when the registry has not been migrated yet.
    """

    waitlist = waitlist or {"candidates": {}}
    records = strategy_registry.get("strategies") or {}
    raw_candidates = dict(waitlist.get("candidates") or {})

    for strategy_id, rec in records.items():
        if rec.get("status") not in {"registry/candidate", "waitlist"}:
            continue
        raw_candidates.setdefault(
            strategy_id,
            {
                "ticker": rec.get("ticker"),
                "strategy": rec.get("strategy"),
                "params": rec.get("params") or {},
                "metrics": rec.get("metrics") or {},
                "rank_score": _as_float(rec.get("active_rank") or rec.get("metrics", {}).get("rank_score")),
                "added_ts": rec.get("created_ts"),
                "retested_ts": rec.get("updated_ts"),
                "ttl_days": int((rec.get("quality_gate") or {}).get("ttl_days", 7)),
                "retests": int((rec.get("quality_gate") or {}).get("retests", 0)),
                "status": rec.get("status"),
            },
        )

    items: list[dict[str, Any]] = []
    for strategy_id, cand in raw_candidates.items():
        rank_score = _as_float(cand.get("rank_score"))
        metrics = cand.get("metrics") or {}
        item = {
            "strategy_id": strategy_id,
            "ticker": cand.get("ticker"),
            "strategy": cand.get("strategy"),
            "status": cand.get("status", "waitlist"),
            "rank_score": round(rank_score, 4),
            "added": _human_age(cand.get("added_ts")),
            "retested": _human_age(cand.get("retested_ts")),
            "ttl_days": int(cand.get("ttl_days") or 0),
            "retests": int(cand.get("retests") or 0),
            "reason": cand.get("reason") or cand.get("reject_reason") or cand.get("note") or "ожидает сигнала на продвижение",
            "go_rub": _as_float(cand.get("go_rub") or cand.get("metrics", {}).get("go_rub")),
            "metrics": {
                "win_rate": metrics.get("win_rate"),
                "sharpe": metrics.get("sharpe"),
                "profit_factor": metrics.get("profit_factor"),
                "max_drawdown": metrics.get("max_drawdown"),
                "trades": metrics.get("trades"),
            },
        }
        items.append(item)

    items.sort(key=lambda row: (row["rank_score"], row["retested"]), reverse=True)
    selected = items[:limit]
    return {
        "source": "state/strategy_registry.json + legacy waitlist.json",
        "count": len(items),
        "limit": limit,
        "items": selected,
        "empty": len(items) == 0,
        "summary": "Кандидатов нет" if not items else f"Кандидатов в ожидании: {len(items)}",
    }


def humanize_warning(item: str | dict[str, Any]) -> str:
    if isinstance(item, dict):
        code = str(item.get("code") or item.get("kind") or "warning")
        message = item.get("message") or item.get("text") or code
        details = item.get("details")
        if details:
            return f"{message}: {details}"
        return str(message)
    text = str(item)
    replacements = {
        "normalized_price": "Цена брокера приведена к шкале отчёта",
        "stale_empty_slot": "Пустой слот хранит устаревшие поля",
        "orphan_open_trades": "Есть открытая сделка без слота в портфеле",
        "inactive_timers": "Есть неактивный сервисный таймер",
        "peak_equity_below_deposit": "Пик equity ниже депозита",
    }
    for key, human in replacements.items():
        if key in text:
            return f"{human}: {text}"
    return text

"""Slot Scorecard — per-slot метрика PnL↑/risk↓.

Для одного selected-слота считает:
  - expectancy_r, risk_penalty, regime_bonus, composite allocator_score
  - lifecycle metrics (hit_rate, stability, decay) если returns переданы

Не импортирует broker/client, не пишет state/.
Чистый dict-in → dict-out.

Используется pipeline_ranker.py для per-slot scorecard в финальном отчёте.
"""
from typing import Any, Dict, List, Optional

from allocator_metrics import (
    expectancy_r as _expectancy_r,
    risk_penalty as _risk_penalty,
    regime_bonus as _regime_bonus,
    allocator_score as _allocator_score,
)
from lifecycle_metrics import hit_rate, expectancy, decay, stability


def slot_scorecard(
    slot: Dict[str, Any],
    regime_snapshot: Optional[Dict[str, Any]] = None,
    risk_per_trade: float = 0.0,
    returns: Optional[List[float]] = None,
    weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Компактный scorecard для одного слота.

    slot: selected-слот из candidate_allocator.select_live_slots:
        {ticker, direction, contracts, score, expectancy_r, risk_penalty, regime_bonus}
        Или raw candidate: {ticker, direction, win_rate, avg_win, avg_loss, ...}

    regime_snapshot: state/regime_snapshot.json или None.
    risk_per_trade: размер типичного риска в рублях.
    returns: временной ряд доходностей для lifecycle (optional).
    weights: override allocator weights (optional).

    Возвращает:
        {
            "ticker": str,
            "direction": str,
            "expectancy_r": float,
            "risk_penalty": float,
            "regime_bonus": float,
            "score": float,
            "lifecycle": {
                "hit_rate": float | None,
                "expectancy": float | None,
                "decay": float | None,
                "stability": float | None,
            } | None,
        }
    """
    if regime_snapshot is None:
        regime_snapshot = {}
    if returns is None:
        returns = []

    ticker = slot.get("ticker", "")
    direction = slot.get("direction")

    # Compute allocator metrics (use raw candidate fields if available)
    stats = {
        "win_rate": slot.get("win_rate", 0.0),
        "avg_win": slot.get("avg_win", 0.0),
        "avg_loss": slot.get("avg_loss", 0.0),
    }
    e_r = _expectancy_r(stats, risk_per_trade=risk_per_trade)
    r_pen = _risk_penalty(slot)
    reg_b = _regime_bonus(ticker, direction, regime_snapshot)

    # Composite score
    score = slot.get("score")
    if score is None:
        candidate = {
            "ticker": ticker,
            "direction": direction,
            "win_rate": stats["win_rate"],
            "avg_win": stats["avg_win"],
            "avg_loss": stats["avg_loss"],
            "drawdown_pct": slot.get("drawdown_pct"),
            "volatility": slot.get("volatility"),
        }
        score = _allocator_score(candidate, regime_snapshot,
                                 risk_per_trade=risk_per_trade, weights=weights)

    # Lifecycle (if returns provided)
    lifecycle = None
    if returns:
        lifecycle = {
            "hit_rate": hit_rate(returns),
            "expectancy": expectancy(returns),
            "decay": decay(returns) if len(returns) >= 2 else None,
            "stability": stability(returns) if len(returns) >= 2 else None,
        }

    return {
        "ticker": ticker,
        "direction": direction,
        "expectancy_r": round(e_r, 6),
        "risk_penalty": round(r_pen, 6),
        "regime_bonus": round(reg_b, 6),
        "score": round(score, 6) if isinstance(score, float) else score,
        "lifecycle": lifecycle,
    }

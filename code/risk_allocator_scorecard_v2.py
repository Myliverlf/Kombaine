"""Risk/Allocator Scorecard V2: расширенный composite scoring для кандидатов.

Связывает allocator_metrics (expectancy_r, risk_penalty, regime_bonus) с
checks из v1 (max_slots, contracts_per_entry, RI excluded) в единый composite.

Ключевые функции:
  - compute_composite(candidate, risk_per_trade, regime_snapshot) → composite dict
  - score_pool(pool, config, regime) → отсортированный ranking

Метрики:
  - PnL↑ через expectancy_r (expectancy в единицах R)
  - risk↓ через risk_penalty (штраф 0..1 за просадку/волатильность)
  - regime_bonus: совпадение с рыночным режимом

Источники:
  - code/allocator_metrics.py: expectancy_r, risk_penalty, regime_bonus, WEIGHTS
  - code/risk_allocator_scorecard.py: check_max_slots, check_contracts_per_entry, check_ri_excluded
  - plan.md Фича 2
"""
from typing import Any, Dict, List, Optional

# Импорт существующих метрик (чистые функции, без side-effects)
try:
    from allocator_metrics import (
        expectancy_r as _expectancy_r,
        risk_penalty as _risk_penalty,
        regime_bonus as _regime_bonus,
        WEIGHTS,
    )
except ImportError:
    from code.allocator_metrics import (
        expectancy_r as _expectancy_r,
        risk_penalty as _risk_penalty,
        regime_bonus as _regime_bonus,
        WEIGHTS,
    )


# ─── Composite scoring ───────────────────────────────────────────────

def compute_composite(
    candidate: Dict[str, Any],
    risk_per_trade: float = 0.0,
    regime_snapshot: Optional[Dict[str, Any]] = None,
    weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Композитный scoring для одного кандидата.

    Объединяет:
      - expectancy_r: нормированная expectancy (PnL↑)
      - risk_penalty: штраф за риск 0..1 (risk↓)
      - regime_bonus: бонус/штраф за regime (-1..+1)

    Args:
        candidate: {
            "ticker": str,
            "direction": "LONG"/"SHORT"/None,
            "win_rate": float (0..1),
            "avg_win": float (>0),
            "avg_loss": float (>=0),
            "drawdown_pct": float (опционально),
            "volatility": float (опционально),
            "risk_per_trade": float (опционально override)
        }
        risk_per_trade: размер риска на сделку (для нормировки expectancy)
        regime_snapshot: state/regime_snapshot.json format
        weights: override allocator_metrics.WEIGHTS

    Returns:
        Dict с компонентами и composite_score:
        {
            "ticker": str,
            "expectancy_r": float,
            "risk_penalty": float (0..1),
            "regime_bonus": float (-1..+1),
            "composite_score": float (нормализованный),
            "composite_raw": float (взвешенная сумма),
        }
    """
    w = weights or WEIGHTS
    regime = regime_snapshot or {"tickers": {}, "bias": "neutral"}

    ticker = candidate.get("ticker", "")
    direction = candidate.get("direction")

    # 1. Expectancy в R
    e_r = _expectancy_r(
        {
            "win_rate": candidate.get("win_rate", 0.0),
            "avg_win": candidate.get("avg_win", 0.0),
            "avg_loss": candidate.get("avg_loss", 0.0),
        },
        risk_per_trade=risk_per_trade or candidate.get("risk_per_trade", 0.0),
    )

    # 2. Risk penalty 0..1
    r_pen = _risk_penalty({
        "drawdown_pct": candidate.get("drawdown_pct"),
        "volatility": candidate.get("volatility"),
    })

    # 3. Regime bonus -1..+1
    reg = _regime_bonus(ticker, direction, regime)

    # 4. Composite: sigmoid-normalized expectancy + (1 - risk) + regime
    e_norm = e_r / (1.0 + abs(e_r)) if e_r != 0 else 0.0

    raw = (
        w["expectancy"] * e_norm
        + w["risk"] * (1.0 - r_pen)
        + w["regime"] * reg
    ) / 100.0

    # Нормализуем raw в [0, 1]:
    # raw ∈ [-(35+25)/100, (40+35+25)/100] = [-0.6, 1.0]
    # Сдвигаем: score = (raw + 0.6) / 1.6
    composite = max(0.0, min(1.0, (raw + 0.6) / 1.6))

    return {
        "ticker": ticker,
        "expectancy_r": round(e_r, 6),
        "risk_penalty": round(r_pen, 6),
        "regime_bonus": round(reg, 6),
        "composite_raw": round(raw, 6),
        "composite_score": round(composite, 6),
    }


# ─── Pool scoring ────────────────────────────────────────────────────

def score_pool(
    pool: Dict[str, Any],
    config: Dict[str, Any],
    regime_snapshot: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Считает composite для всех strategies в pool и возвращает ranking.

    Фильтры (из config):
      - excluded тикеры (напр. RI)
      - max_slots
      - min_rank (из signal_min_rank)

    Args:
        pool: signal_pool dict с strategies
        config: config dict
        regime_snapshot: regime snapshot

    Returns:
        List of scored candidates, отсортированных по composite_score DESC.
        Каждый элемент — merge из pool entry + compute_composite result.
    """
    excluded = set(config.get("excluded", []))
    risk = config.get("risk", {})
    max_slots = risk.get("max_slots", 3)
    min_rank = risk.get("signal_min_rank", 100.0)

    strategies = pool.get("strategies", {})
    scored: List[Dict[str, Any]] = []

    for pool_id, entry in strategies.items():
        # Только active стратегии
        if entry.get("status") != "active":
            continue

        ticker = entry.get("ticker", "")

        # Исключение тикеров (RI и др.)
        if ticker.upper() in excluded:
            continue

        # Минимальный rank_score
        if entry.get("rank_score", 0) < min_rank:
            continue

        # Кандидат для composite
        candidate = {
            "ticker": ticker,
            "direction": entry.get("direction", "LONG"),
            "win_rate": entry.get("metrics", {}).get("win_rate", 0.5),
            "avg_win": entry.get("metrics", {}).get("avg_win", 0.0),
            "avg_loss": entry.get("metrics", {}).get("avg_loss", 0.0),
            "drawdown_pct": entry.get("drawdown_pct"),
            "volatility": entry.get("volatility"),
        }

        result = compute_composite(
            candidate,
            regime_snapshot=regime_snapshot,
        )

        # Merge pool entry + composite
        scored_entry = {
            "pool_id": pool_id,
            "ticker": ticker,
            "strategy": entry.get("strategy", ""),
            "rank_score": entry.get("rank_score", 0.0),
            **result,
        }
        scored.append(scored_entry)

    # Сортируем по composite_score DESC
    scored.sort(key=lambda x: x["composite_score"], reverse=True)

    # Ограничиваем max_slots
    return scored[:max_slots]

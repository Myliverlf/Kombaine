"""Consistency Scorer — R² equity curve + significance gate.

Оценивает статистическую значимость и стабильность стратегии до бэктеста.
Все функции — чистые, stdlib only (math, statistics).

Функции:
  - equity_r2(returns: list[float]) → float   R² линейной регрессии equity curve
  - check_significance(n_trades: int, min_trades: int=30) → bool
  - consistency_score(returns=None, n_trades=0, ...) → float  (0..1, combines R² + significance)

Зависимости: stdlib only (math, statistics). Нет external зависимостей.
"""
from __future__ import annotations

import math
from typing import List, Optional


def equity_r2(returns: List[float]) -> float:
    """R² линейной регрессии equity curve.

    Строит equity curve из returns (кумулятивная сумма), затем считает R²
    линейной регрессии equity vs index.

    Возвращает 0..1:
      1.0 — идеально линейный equity curve (мечта)
      0.0 — нет корреляции с линейным ростом
      < 0 — equity curve anti-correlated (откат)

    edge cases:
      - пустой список → 0.0
      - один элемент → 0.0 (недостаточно данных)
      - все returns = 0 → 0.0 (constant equity)
    """
    if not returns or len(returns) < 2:
        return 0.0

    n = len(returns)

    # Build equity curve: cumulative sum
    equity: List[float] = []
    cum = 0.0
    for r in returns:
        cum += r
        equity.append(cum)

    # Linear regression: y = equity, x = index (0, 1, 2, ...)
    # R² = 1 - SS_res / SS_tot
    mean_y = sum(equity) / n
    mean_x = (n - 1) / 2.0  # mean of 0..n-1

    ss_tot = 0.0
    ss_res = 0.0
    ss_xy = 0.0
    ss_xx = 0.0

    for i in range(n):
        dx = i - mean_x
        dy = equity[i] - mean_y
        ss_xy += dx * dy
        ss_xx += dx * dx

    if ss_xx == 0.0:
        # All x values are the same (impossible with n>=2, but defensive)
        return 0.0

    slope = ss_xy / ss_xx
    intercept = mean_y - slope * mean_x

    for i in range(n):
        predicted = slope * i + intercept
        residual = equity[i] - predicted
        ss_res += residual * residual
        dy = equity[i] - mean_y
        ss_tot += dy * dy

    if ss_tot == 0.0:
        # Constant equity — no variance
        return 0.0

    r_squared = 1.0 - (ss_res / ss_tot)

    # Clamp to 0..1 (anti-correlated equity = 0, not negative)
    return max(0.0, min(1.0, r_squared))


def check_significance(n_trades: int, min_trades: int = 30) -> bool:
    """Проверка статистической значимости по количеству сделок.

    n_trades >= min_trades → True (достаточно данных для выводов)
    """
    return n_trades >= min_trades


def consistency_score(
    returns: Optional[List[float]] = None,
    n_trades: int = 0,
    r2_weight: float = 0.6,
    significance_weight: float = 0.4,
    min_trades: int = 30,
) -> float:
    """Комбинированный consistency score (0..1).

    Веса:
      - R² equity curve: r2_weight (default 0.6)
      - significance gate: significance_weight (default 0.4)

    Если returns=None → consistency_score = 0.0 (fail-open: нет данных = нет confidence).
    """
    if returns is None or len(returns) < 2:
        return 0.0

    r2 = equity_r2(returns)
    sig = 1.0 if check_significance(n_trades, min_trades) else 0.0

    total_weight = r2_weight + significance_weight
    if total_weight == 0.0:
        return 0.0

    score = (r2_weight * r2 + significance_weight * sig) / total_weight
    return round(min(1.0, max(0.0, score)), 6)

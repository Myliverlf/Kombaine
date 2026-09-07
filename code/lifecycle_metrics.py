"""Lifecycle Metrics — чистый stdlib-модуль метрик жизненного цикла стратегии.

Метрики:
  hit_rate(trades)         — доля выигрышных сделок (wins/total)
  expectancy(trades)       — среднее R на сделку (mean R-multiple)
  decay(returns)           — proxy-затухание: вторая половина окна vs первая
  stability(returns)       — доля rolling-окон с Sharpe > 0 (PSR-lite, Bailey/LdP 2014)

Все функции безопасны на empty/single/flat (0.0, без ZeroDivision).
Нет broker-импортов, нет сети, нет записи state/.

Связь с плацдармом:
  - allocator_metrics.expectancy_r — slot-level expectancy по stats dict.
    Этот модуль считает по交易ному ряду (list[float] R-multiples), дополняет, не заменяет.
  - scorecard_metrics.max_drawdown — time-series просадка; здесь нет дубля MDD,
    но есть decay/stability как ADC-аналоги.
"""
import math
from typing import List


# ─── 1. Hit Rate ───────────────────────────────────────────────────────

def hit_rate(trades: List[float]) -> float:
    """Доля выигрышных сделок (R > 0).

    trades: список R-множителей (R = profit/risk, 1.0 = прибыль 1R).
    Возвращает float 0.0..1.0.

    >>> hit_rate([1.0, -1.0, 1.0, -0.5, 2.0])
    0.6
    >>> hit_rate([])
    0.0
    >>> hit_rate([-1.0])
    0.0
    """
    if not trades:
        return 0.0

    wins = sum(1 for r in trades if r > 0)
    return wins / len(trades)


# ─── 2. Expectancy ─────────────────────────────────────────────────────

def expectancy(trades: List[float]) -> float:
    """Среднее R на сделку (mean R-multiple).

    Формула: mean(trades) = sum(R) / n.
    Аналог allocator_metrics.expectancy_r, но считает по交易ному ряду,
    а не по stats dict.

    trades: список R-множителей.
    Возвращает float: средний R.

    >>> expectancy([1.0, -1.0, 1.0, -0.5, 2.0])
    0.5
    >>> expectancy([])
    0.0
    >>> expectancy([-2.0, 1.0])
    -0.5
    """
    if not trades:
        return 0.0

    return sum(trades) / len(trades)


# ─── 3. Decay ──────────────────────────────────────────────────────────

def decay(returns: List[float]) -> float:
    """Proxy-затухание доходности: отношение второй половины окна к первой.

    ASSUMPTION: proxy-метод (первая vs вторая половина).
    Промышленный IC half-life (Bailey/LdP 2014) требует факторных данных,
    которые недоступны. Proxy помечен как предположение.

    Логика:
      - Делим returns пополам.
      - Считаем cumulative return для каждой половины.
      - decay = cum2 / cum1 (нормировано на длину).
      - Если нет данных → 0.0 (нейтрально).

    Интерпретация:
      - decay > 1.0 — доходность растёт (нет затухания).
      - decay = 1.0 — стабильна.
      - decay < 1.0 — затухает ( Strat теряет силу).

    >>> decay([0.01, 0.02, 0.03, 0.01, 0.02, 0.04])  # doctest: +ELLIPSIS
    1.168...
    >>> decay([0.02, 0.03, 0.04, 0.01, 0.005, 0.001])  # doctest: +ELLIPSIS
    0.173...
    """
    if not returns or len(returns) < 2:
        return 0.0

    mid = len(returns) // 2
    first_half = returns[:mid] if mid > 0 else returns[:1]
    second_half = returns[mid:]

    if not first_half or not second_half:
        return 0.0

    # Cumulative return для каждой половины: prod(1 + r) - 1
    cum1 = 1.0
    for r in first_half:
        cum1 *= (1.0 + r)
    cum1 -= 1.0

    cum2 = 1.0
    for r in second_half:
        cum2 *= (1.0 + r)
    cum2 -= 1.0

    # Нормируем на длину окна
    avg1 = cum1 / len(first_half) if first_half else 0.0
    avg2 = cum2 / len(second_half) if second_half else 0.0

    # Отношение: защита от деления на 0
    if abs(avg1) < 1e-12:
        if abs(avg2) < 1e-12:
            return 1.0  # оба нулевые — стабильно
        return 999.0  # первая нулевая, вторая нет — взрыв (не затухает)

    return avg2 / avg1


# ─── 4. Stability ──────────────────────────────────────────────────────

def _sharpe_simple(returns: List[float]) -> float:
    """Простой Sharpe ratio (без annualization) для stability."""
    n = len(returns)
    if n < 2:
        return 0.0
    mean_r = sum(returns) / n
    var_r = sum((r - mean_r) ** 2 for r in returns) / (n - 1)
    std_r = math.sqrt(var_r)
    if std_r == 0.0:
        return 0.0
    return mean_r / std_r


def stability(returns: List[float], n_windows: int = 4) -> float:
    """Доля rolling-окон с Sharpe > 0 (PSR-lite, Bailey/LdP 2014).

    Делим returns на n_windows скользящих окон (с перекрытием).
    Для каждого окна считаем Sharpe.
    stability = (число окон с Sharpe > 0) / n_windows.

    Возвращает float 0.0..1.0:
      0.0 — ни одно окно не положительно (нестабильна).
      1.0 — все окна положительны (стабильна).

    >>> stability([0.01, 0.005, 0.02, 0.008, 0.015, 0.002, 0.01, 0.003, 0.007, 0.001])
    1.0
    >>> stability([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    0.0
    """
    if not returns or n_windows < 1:
        return 0.0

    n = len(returns)
    min_window = 2  # минимум для Sharpe
    if n < min_window:
        return 0.0

    # Определяем размер окна и шаг
    window_size = max(min_window, n // n_windows)
    step = max(1, (n - window_size) // max(1, n_windows - 1)) if n_windows > 1 else 1

    positive_count = 0
    actual_windows = 0

    for i in range(n_windows):
        start = i * step
        end = start + window_size
        if end > n:
            break
        window = returns[start:end]
        if len(window) < 2:
            break
        actual_windows += 1
        if _sharpe_simple(window) > 0:
            positive_count += 1

    if actual_windows == 0:
        return 0.0

    return positive_count / actual_windows


# ─── No-broker guard ──────────────────────────────────────────────────
_FORBIDDEN_CALLS = {"post_order", "place_order", "send_order", "submit_order", "Client"}


def _validate_no_broker() -> None:
    """Runtime check: модуль не содержит broker-вызовов (AST-based)."""
    import ast as _ast
    import pathlib
    tree = _ast.parse(pathlib.Path(__file__).read_text())
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Call):
            func = node.func
            name = ""
            if isinstance(func, _ast.Name):
                name = func.id
            elif isinstance(func, _ast.Attribute):
                name = func.attr
            if name in _FORBIDDEN_CALLS:
                raise RuntimeError(
                    "lifecycle_metrics.py contains forbidden call: %s()" % name
                )


if __name__ == "__main__":
    _validate_no_broker()
    # Demo
    demo_trades = [1.5, -1.0, 2.0, -0.5, 1.0, 0.5, -1.5, 1.0]
    demo_returns = [0.01, 0.015, -0.005, 0.008, -0.002, 0.012, -0.003, 0.009]
    print("hit_rate:", round(hit_rate(demo_trades), 3))
    print("expectancy:", round(expectancy(demo_trades), 3))
    print("decay:", round(decay(demo_returns), 3))
    print("stability:", round(stability(demo_returns), 3))

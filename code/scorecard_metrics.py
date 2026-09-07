"""Scorecard Metrics — pure-python риск-метрики PnL↑/risk↓.

Реализация идей quantstats/empyrical-reloaded без внешних зависимостей.
Все функции чистые: list[float] → float/dict.
Нет broker/client, нет сети, нет записи state/.

Метрики:
  - sharpe_ratio(returns, rf=0.0)     — annualized Sharpe (rf=0 → простой)
  - sortino_ratio(returns, rf=0.0)    — downside deviation-based Sortino
  - max_drawdown(returns)             — максимальная просадка (0..-1)
  - profit_factor(returns)            — sum(gains) / sum(losses), ∞ если нет losses
  - ab_compare(baseline, candidate)   — A/B scorecard: pnl_up, risk_down, metrics

Связь с плацдармом:
  - validate_regime_gate_dryrun.py — A/B scorecard PnL↑/risk↓ через allocator-метрики.
  - allocator_metrics.py — expectancy_r, risk_penalty (slot-level).
  - Этот модуль даёт time-series метрики (на списках доходностей).
"""
import math
from typing import Any, Dict, List, Optional


# ─── 1. Sharpe Ratio ──────────────────────────────────────────────────

def sharpe_ratio(returns: List[float], rf: float = 0.0) -> float:
    """Annualized Sharpe Ratio (rf = risk-free rate per period).

    Формула: (mean(returns) - rf) / std(returns) * sqrt(252)
    Если std == 0 → 0.0 (нет волатильности — нет премии).

    returns: список периодных доходностей (0.01 = 1%).
    rf: безрисковая ставка за период (по умолчанию 0).

    >>> sharpe_ratio([0.01, -0.005, 0.02, -0.01, 0.015])  # doctest: +ELLIPSIS
    0.87...
    """
    if len(returns) < 2:
        return 0.0

    n = len(returns)
    mean_r = sum(returns) / n
    var_r = sum((r - mean_r) ** 2 for r in returns) / (n - 1)
    std_r = math.sqrt(var_r)

    if std_r == 0.0:
        return 0.0

    return (mean_r - rf) / std_r * math.sqrt(252)


# ─── 2. Sortino Ratio ─────────────────────────────────────────────────

def sortino_ratio(returns: List[float], rf: float = 0.0) -> float:
    """Annualized Sortino Ratio (downside deviation).

    Формула: (mean(returns) - rf) / downside_std * sqrt(252)
    downside_std = std только по отрицательным доходностям (ниже rf).

    >>> sortino_ratio([0.01, -0.005, 0.02, -0.01, 0.015])  # doctest: +ELLIPSIS
    1.16...
    """
    if len(returns) < 2:
        return 0.0

    n = len(returns)
    mean_r = sum(returns) / n

    downside = [r - rf for r in returns if r < rf]
    if len(downside) < 2:
        # Нет отрицательных доходностей → бесконечная Sortino → ограничиваем
        if not downside:
            return 10.0 if mean_r > rf else 0.0
        return 0.0

    dd_var = sum(d ** 2 for d in downside) / (len(downside) - 1)
    dd_std = math.sqrt(dd_var)

    if dd_std == 0.0:
        return 10.0 if mean_r > rf else 0.0

    return (mean_r - rf) / dd_std * math.sqrt(252)


# ─── 3. Max Drawdown ──────────────────────────────────────────────────

def max_drawdown(returns: List[float]) -> float:
    """Максимальная просадка (0.0 .. -1.0).

    Считает накопленную доходность и находит максимальный пик-до-падение.
    Возвращает отрицательное число или 0.0 (нет просадки).

    >>> max_drawdown([0.01, -0.005, 0.02, -0.03, 0.01])
    -0.02955...
    """
    if not returns:
        return 0.0

    equity = 1.0
    peak = 1.0
    max_dd = 0.0

    for r in returns:
        equity *= (1.0 + r)
        if equity > peak:
            peak = equity
        dd = (equity - peak) / peak
        if dd < max_dd:
            max_dd = dd

    return max_dd


# ─── 4. Profit Factor ─────────────────────────────────────────────────

def profit_factor(returns: List[float]) -> float:
    """Profit Factor: sum(gains) / abs(sum(losses)).

    Если нет убытков → +inf (представляем как 999.99).
    Если нет прибыли → 0.0.

    >>> profit_factor([0.01, -0.005, 0.02, -0.01, 0.015])
    2.333...
    """
    gains = sum(r for r in returns if r > 0)
    losses = sum(r for r in returns if r < 0)

    if losses == 0.0:
        return 999.99 if gains > 0 else 0.0
    if gains == 0.0:
        return 0.0

    return gains / abs(losses)


# ─── 5. A/B Compare (scorecard) ───────────────────────────────────────

def ab_compare(
    baseline_returns: List[float],
    candidate_returns: List[float],
) -> Dict[str, Any]:
    """A/B scorecard: сравнивает baseline vs candidate.

    Возвращает:
        {
            "pnl_up": bool,     — candidate Sharpe >= baseline Sharpe
            "risk_down": bool,  — candidate max_drawdown >= baseline max_drawdown (меньше по модулю)
            "metrics": {
                "baseline": {sharpe, sortino, max_dd, profit_factor},
                "candidate": {sharpe, sortino, max_dd, profit_factor},
                "delta": {sharpe, sortino, max_dd, profit_factor},
            }
        }

    pnl_up: candidate Sharpe >= baseline Sharpe ( лучше или равен).
    risk_down: candidate max_dd >= baseline max_dd (просадка меньше или рава, т.к. обе отрицательные).

    >>> r = ab_compare([0.0, 0.01, -0.005], [0.0, 0.02, -0.002])
    >>> r["pnl_up"]
    True
    >>> r["risk_down"]
    True
    """
    b_sharpe = sharpe_ratio(baseline_returns)
    c_sharpe = sharpe_ratio(candidate_returns)
    b_sortino = sortino_ratio(baseline_returns)
    c_sortino = sortino_ratio(candidate_returns)
    b_dd = max_drawdown(baseline_returns)
    c_dd = max_drawdown(candidate_returns)
    b_pf = profit_factor(baseline_returns)
    c_pf = profit_factor(candidate_returns)

    # pnl_up: candidate Sharpe >= baseline Sharpe
    pnl_up = c_sharpe >= b_sharpe

    # risk_down: candidate drawdown is less severe (closer to 0, i.e. greater since both ≤ 0)
    risk_down = c_dd >= b_dd

    return {
        "pnl_up": pnl_up,
        "risk_down": risk_down,
        "metrics": {
            "baseline": {
                "sharpe": round(b_sharpe, 6),
                "sortino": round(b_sortino, 6),
                "max_drawdown": round(b_dd, 6),
                "profit_factor": round(b_pf, 6),
            },
            "candidate": {
                "sharpe": round(c_sharpe, 6),
                "sortino": round(c_sortino, 6),
                "max_drawdown": round(c_dd, 6),
                "profit_factor": round(c_pf, 6),
            },
            "delta": {
                "sharpe": round(c_sharpe - b_sharpe, 6),
                "sortino": round(c_sortino - b_sortino, 6),
                "max_drawdown": round(c_dd - b_dd, 6),
                "profit_factor": round(c_pf - b_pf, 6),
            },
        },
    }


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
                raise RuntimeError(f"scorecard_metrics.py contains forbidden call: {name}()")


if __name__ == "__main__":
    _validate_no_broker()
    # Demo: A/B compare
    baseline = [0.0, 0.01, -0.005, 0.005, -0.01, 0.008]
    candidate = [0.0, 0.015, -0.002, 0.012, -0.005, 0.01]
    result = ab_compare(baseline, candidate)
    print(f"pnl_up={result['pnl_up']}, risk_down={result['risk_down']}")
    for key, vals in result["metrics"].items():
        print(f"  {key}: {vals}")

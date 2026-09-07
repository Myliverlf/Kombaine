"""Backtest Feedback Layer — unified feedback scorecard from backtest results.

Оркестратор: собирает FeedbackScorecard из equity curve + trades list.
REUSE: equity_r2 (consistency_scorer), cusum_detect (degradation_detector),
       stability (lifecycle_metrics), sharpe_ratio/sortino_ratio/max_drawdown
       (scorecard_metrics), expectancy (lifecycle_metrics).

Нет broker-импортов, нет сети, нет записи state/ (кроме read-only JSON output).
Все функции чистые: list/dict-in → dict-out. stdlib-only.

Связь с плацдармом:
  - allocator_metrics.expectancy_r — slot-level expectancy по stats dict.
    Этот модуль считает по equity curve + trades, дополняет, не заменяет.
  - generator_feedback.json — текущий {by_direction_regime, best_hours}.
    Расширяет секцией backtest_feedback (обратно совместимо).
  - degradation_detector — CUSUM/health → health feedback signals.
  - consistency_scorer — equity_r2 для линейности equity curve.

Константы: MAX_SLOTS=3, MAX_CONTRACTS=1 (из config.json limits).
"""
from __future__ import annotations

import math
import sys
import os
from typing import Any, Dict, List, Optional, Tuple

# Ensure code/ is on sys.path for sibling imports
_CODE_DIR = os.path.dirname(os.path.abspath(__file__))
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

from consistency_scorer import equity_r2
from degradation_detector import cusum_detect, health_score, degradation_alert
from lifecycle_metrics import stability, expectancy, hit_rate, decay
from scorecard_metrics import sharpe_ratio, sortino_ratio, max_drawdown, profit_factor

# ═══════════════════════════════════════════════════════════════════════
# Constants (from config.json limits)
# ═══════════════════════════════════════════════════════════════════════
MAX_SLOTS = 3
MAX_CONTRACTS = 1
EXCLUDED_TICKERS: List[str] = ["RI"]

# ═══════════════════════════════════════════════════════════════════════
# Weights for composite feedback score (PnL↑ / risk↓)
# ═══════════════════════════════════════════════════════════════════════
COMPOSITE_WEIGHTS = {
    "equity_r2": 20,       # linearity of equity curve
    "sharpe_norm": 20,     # normalized Sharpe (PnL proxy)
    "dd_severity": 15,     # drawdown severity (lower = better)
    "expectancy_trend": 15, # is expectancy growing or shrinking
    "stability": 15,       # PSR-lite stability score
    "degradation_penalty": 15,  # penalty for detected degradation
}

# ═══════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════

def _equity_slope(equity_curve: List[float]) -> float:
    """Linear regression slope of equity curve.

    Returns slope value: positive = equity growing, negative = declining.
    Normalized by initial equity to make it comparable across scales.
    """
    n = len(equity_curve)
    if n < 2:
        return 0.0

    mean_x = (n - 1) / 2.0
    mean_y = sum(equity_curve) / n

    ss_xy = 0.0
    ss_xx = 0.0
    for i in range(n):
        dx = i - mean_x
        dy = equity_curve[i] - mean_y
        ss_xy += dx * dy
        ss_xx += dx * dx

    if ss_xx == 0.0:
        return 0.0

    slope = ss_xy / ss_xx
    # Normalize by initial equity to make comparable
    initial = equity_curve[0] if equity_curve[0] != 0 else 1.0
    return slope / abs(initial)


def _compute_returns_from_equity(equity_curve: List[float]) -> List[float]:
    """Compute period returns from equity curve.

    equity_curve: [e0, e1, e2, ...] where e_t is equity at time t.
    returns: [(e1-e0)/e0, (e2-e1)/e1, ...]
    """
    if len(equity_curve) < 2:
        return []
    returns = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1]
        if prev == 0.0:
            returns.append(0.0)
        else:
            returns.append((equity_curve[i] - prev) / abs(prev))
    return returns


def _dd_trend(returns: List[float], window: int = 10) -> float:
    """Drawdown trend: compare recent DD vs earlier DD.

    Returns: -1..+1
      positive = drawdown improving (getting shallower)
      negative = drawdown worsening (getting deeper)
      0 = stable
    """
    n = len(returns)
    if n < window * 2:
        return 0.0

    # Compute rolling max drawdown in two halves
    def _max_dd_section(rets: List[float]) -> float:
        eq = 1.0
        peak = 1.0
        mdd = 0.0
        for r in rets:
            eq *= (1.0 + r)
            if eq > peak:
                peak = eq
            dd = (eq - peak) / peak
            if dd < mdd:
                mdd = dd
        return mdd

    first_half = returns[:n // 2]
    second_half = returns[n // 2:]

    dd_first = _max_dd_section(first_half)
    dd_second = _max_dd_section(second_half)

    # dd_first and dd_second are <= 0
    # improvement = second is closer to 0 (greater value)
    if abs(dd_first) < 1e-12 and abs(dd_second) < 1e-12:
        return 0.0
    # Normalize to -1..+1
    diff = dd_second - dd_first  # positive = improvement
    scale = max(abs(dd_first), abs(dd_second), 0.01)
    return max(-1.0, min(1.0, diff / scale))


def _expectancy_trend(trades: List[float], window: int = 5) -> float:
    """Trend of rolling expectancy: compare second half vs first half.

    Returns: -1..+1
      positive = expectancy improving
      negative = expectancy declining
      0 = stable
    """
    n = len(trades)
    if n < window * 2:
        return 0.0

    first_half = trades[:n // 2]
    second_half = trades[n // 2:]

    e_first = expectancy(first_half)
    e_second = expectancy(second_half)

    # Normalize to -1..+1
    scale = max(abs(e_first), abs(e_second), 0.01)
    diff = e_second - e_first
    return max(-1.0, min(1.0, diff / scale))


def _normalize_sharpe(s: float) -> float:
    """Normalize Sharpe to 0..1 range for composite score.

    Sharpe of 2.0 → 1.0 (excellent)
    Sharpe of -1.0 → 0.0 (terrible)
    """
    return max(0.0, min(1.0, (s + 1.0) / 3.0))


def _normalize_dd(dd: float) -> float:
    """Normalize max drawdown to 0..1 range (1=good, 0=bad).

    dd = 0.0 → 1.0 (no drawdown)
    dd = -0.25 → 0.0 (25% drawdown, very bad)
    """
    return max(0.0, min(1.0, 1.0 + dd / 0.25))


def _degradation_penalty_score(cusum_count: int, health: Dict[str, Any]) -> float:
    """Compute degradation penalty: 1.0 = no penalty, 0.0 = max penalty.

    Based on:
    - CUSUM changepoint count (more = worse)
    - Health status composite score
    """
    # CUSUM penalty: 0 alerts = no penalty, 3+ alerts = max penalty
    cusum_pen = max(0.0, 1.0 - cusum_count / 3.0)

    # Health penalty
    health_score_val = float(health.get("composite_score", 0.5))

    return (cusum_pen + health_score_val) / 2.0


def _generate_recommendations(
    composite_score: float,
    equity_r2_val: float,
    sharpe_val: float,
    max_dd_val: float,
    stability_val: float,
    cusum_count: int,
    exp_trend: float,
    dd_trend_val: float,
) -> List[str]:
    """Generate actionable recommendations for allocator/generator."""
    recs = []
    if composite_score >= 0.7:
        recs.append("Strategy is healthy — maintain allocation")
    elif composite_score >= 0.4:
        recs.append("Strategy shows moderate quality — monitor closely")
    else:
        recs.append("Strategy is weak — consider reducing allocation or stopping")

    if equity_r2_val < 0.3:
        recs.append("Equity curve is non-linear — check for regime-dependent performance")

    if max_dd_val < -0.15:
        recs.append("Drawdown exceeds 15% — review risk parameters")

    if cusum_count >= 2:
        recs.append(f"CUSUM detected {cusum_count} regime shifts — possible strategy exhaustion")

    if stability_val < 0.4:
        recs.append("Low stability (PSR-lite) — returns are inconsistent across windows")

    if exp_trend < -0.3:
        recs.append("Expectancy is declining — strategy may be losing edge")

    if dd_trend_val < -0.3:
        recs.append("Drawdown is deepening — active degradation signal")

    if sharpe_val < 0:
        recs.append("Negative Sharpe — strategy is risk-inefficient")

    return recs


# ═══════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════

def build_feedback_scorecard(
    equity_curve: List[float],
    trades: List[float],
    per_strategy_stats: Optional[Dict[str, Any]] = None,
    cusum_threshold: float = 4.0,
    cusum_drift: float = 0.5,
) -> Dict[str, Any]:
    """Build unified FeedbackScorecard from equity curve + trades.

    Args:
        equity_curve: equity values over time [e0, e1, e2, ...]
        trades: list of R-multiples per trade (1.0 = profit of 1R)
        per_strategy_stats: optional dict of per-strategy performance
            {strategy_name: {"equity_curve": [...], "trades": [...], ...}}
        cusum_threshold: CUSUM detection threshold (default 4.0)
        cusum_drift: CUSUM drift parameter (default 0.5)

    Returns:
        FeedbackScorecard dict with all metrics + composite_score + recommendations.

    Example:
        >>> eq = [10000, 10100, 10200, 10300, 10400, 10500]
        >>> tr = [1.0, 0.5, 1.5, -0.5, 1.0, 0.8]
        >>> sc = build_feedback_scorecard(eq, tr)
        >>> sc["composite_score"] > 0
        True
    """
    # Compute returns from equity curve
    returns = _compute_returns_from_equity(equity_curve)

    # === 1. Equity metrics ===
    eq_r2 = equity_r2(returns) if returns else 0.0
    eq_slope = _equity_slope(equity_curve)

    # === 2. PnL aggregation ===
    total_pnl = equity_curve[-1] - equity_curve[0] if equity_curve else 0.0
    total_pnl_pct = (total_pnl / abs(equity_curve[0]) * 100.0) if equity_curve and equity_curve[0] != 0 else 0.0

    # === 3. Drawdown metrics ===
    mdd = max_drawdown(returns) if returns else 0.0
    dd_trend_val = _dd_trend(returns) if returns else 0.0

    # === 4. Expectancy trend ===
    exp = expectancy(trades) if trades else 0.0
    exp_trend_val = _expectancy_trend(trades) if trades else 0.0

    # === 5. CUSUM degradation detection ===
    cusum_alerts = cusum_detect(returns, cusum_threshold, cusum_drift) if returns else []

    # === 6. Stability ===
    stab = stability(returns) if returns else 0.0

    # === 7. Health score ===
    s_val = sharpe_ratio(returns) if returns else 0.0
    hr = hit_rate(trades) if trades else 0.0
    health = health_score(
        rolling_sharpe=s_val,
        rolling_ir=0.0,  # not computed from equity alone
        max_dd=mdd,
        win_rate=hr,
    )

    # === 8. Degradation alert ===
    alert = degradation_alert(health, cusum_alerts)

    # === 9. Composite feedback score (PnL↑ / risk↓) ===
    norm_sharpe = _normalize_sharpe(s_val)
    norm_dd = _normalize_dd(mdd)
    exp_trend_norm = (exp_trend_val + 1.0) / 2.0  # map -1..1 to 0..1
    stab_norm = stab  # already 0..1
    r2_norm = max(0.0, min(1.0, eq_r2))
    deg_pen = _degradation_penalty_score(len(cusum_alerts), health)

    composite_score = (
        COMPOSITE_WEIGHTS["equity_r2"] * r2_norm
        + COMPOSITE_WEIGHTS["sharpe_norm"] * norm_sharpe
        + COMPOSITE_WEIGHTS["dd_severity"] * norm_dd
        + COMPOSITE_WEIGHTS["expectancy_trend"] * exp_trend_norm
        + COMPOSITE_WEIGHTS["stability"] * stab_norm
        + COMPOSITE_WEIGHTS["degradation_penalty"] * deg_pen
    ) / 100.0

    # === 10. Recommendations ===
    recs = _generate_recommendations(
        composite_score, eq_r2, s_val, mdd, stab,
        len(cusum_alerts), exp_trend_val, dd_trend_val,
    )

    # === 11. Per-strategy feedback (optional) ===
    per_strategy_feedback = {}
    if per_strategy_stats:
        for strat_name, strat_data in per_strategy_stats.items():
            strat_trades = strat_data.get("trades", [])
            strat_eq = strat_data.get("equity_curve", [])
            strat_returns = _compute_returns_from_equity(strat_eq) if strat_eq else []
            strat_eq_r2 = equity_r2(strat_returns) if strat_returns else 0.0
            strat_sharpe = sharpe_ratio(strat_returns) if strat_returns else 0.0
            strat_mdd = max_drawdown(strat_returns) if strat_returns else 0.0
            strat_stab = stability(strat_returns) if strat_returns else 0.0
            strat_exp = expectancy(strat_trades) if strat_trades else 0.0
            strat_cusum = cusum_detect(strat_returns, cusum_threshold, cusum_drift) if strat_returns else []

            strat_norm_sharpe = _normalize_sharpe(strat_sharpe)
            strat_norm_dd = _normalize_dd(strat_mdd)
            strat_r2_norm = max(0.0, min(1.0, strat_eq_r2))
            strat_exp_trend = _expectancy_trend(strat_trades) if strat_trades else 0.0
            strat_exp_norm = (strat_exp_trend + 1.0) / 2.0
            strat_health = health_score(strat_sharpe, 0.0, strat_mdd, hit_rate(strat_trades) if strat_trades else 0.0)
            strat_deg_pen = _degradation_penalty_score(len(strat_cusum), strat_health)

            strat_composite = (
                COMPOSITE_WEIGHTS["equity_r2"] * strat_r2_norm
                + COMPOSITE_WEIGHTS["sharpe_norm"] * strat_norm_sharpe
                + COMPOSITE_WEIGHTS["dd_severity"] * strat_norm_dd
                + COMPOSITE_WEIGHTS["expectancy_trend"] * strat_exp_norm
                + COMPOSITE_WEIGHTS["stability"] * strat_stab
                + COMPOSITE_WEIGHTS["degradation_penalty"] * strat_deg_pen
            ) / 100.0

            per_strategy_feedback[strat_name] = {
                "equity_r2": round(strat_eq_r2, 6),
                "sharpe": round(strat_sharpe, 6),
                "max_drawdown": round(strat_mdd, 6),
                "stability": round(strat_stab, 6),
                "expectancy": round(strat_exp, 6),
                "cusum_alerts": len(strat_cusum),
                "composite_score": round(strat_composite, 6),
            }

    # === 12. Assemble scorecard ===
    scorecard = {
        "version": "1.0",
        "equity_metrics": {
            "equity_r2": round(eq_r2, 6),
            "equity_slope": round(eq_slope, 8),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl_pct, 4),
            "data_points": len(equity_curve),
        },
        "drawdown_metrics": {
            "max_drawdown": round(mdd, 6),
            "dd_trend": round(dd_trend_val, 6),
        },
        "expectancy_metrics": {
            "expectancy_r": round(exp, 6),
            "expectancy_trend": round(exp_trend_val, 6),
            "hit_rate": round(hr, 6),
            "n_trades": len(trades),
        },
        "degradation": {
            "cusum_alerts": cusum_alerts,
            "cusum_count": len(cusum_alerts),
            "health_status": health.get("status", "unknown"),
            "health_composite": round(float(health.get("composite_score", 0.0)), 6),
            "alert": alert,
        },
        "stability_score": round(stab, 6),
        "sharpe": round(s_val, 6),
        "sortino": round(sortino_ratio(returns) if returns else 0.0, 6),
        "profit_factor": round(profit_factor(returns) if returns else 0.0, 6),
        "composite_score": round(composite_score, 6),
        "recommendations": recs,
        "guards": {
            "max_slots": MAX_SLOTS,
            "max_contracts": MAX_CONTRACTS,
            "excluded_tickers": EXCLUDED_TICKERS,
        },
    }

    if per_strategy_feedback:
        scorecard["per_strategy"] = per_strategy_feedback

    return scorecard


def extend_generator_feedback(
    existing_feedback: Dict[str, Any],
    scorecard: Dict[str, Any],
) -> Dict[str, Any]:
    """Extend existing generator_feedback.json with backtest_feedback section.

    Preserves existing fields (by_direction_regime, best_hours) and adds
    backtest_feedback section. Backward compatible.
    """
    result = dict(existing_feedback)
    result["backtest_feedback"] = {
        "composite_score": scorecard.get("composite_score", 0.0),
        "equity_r2": scorecard.get("equity_metrics", {}).get("equity_r2", 0.0),
        "max_drawdown": scorecard.get("drawdown_metrics", {}).get("max_drawdown", 0.0),
        "stability": scorecard.get("stability_score", 0.0),
        "recommendations": scorecard.get("recommendations", []),
        "health_status": scorecard.get("degradation", {}).get("health_status", "unknown"),
    }
    return result


# ═══════════════════════════════════════════════════════════════════════
# No-broker guard
# ═══════════════════════════════════════════════════════════════════════
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
                    "backtest_feedback.py contains forbidden call: %s()" % name
                )


if __name__ == "__main__":
    _validate_no_broker()
    # Demo with synthetic healthy strategy
    demo_equity = [10000.0]
    for i in range(20):
        demo_equity.append(demo_equity[-1] * (1.0 + 0.008))
    demo_trades = [1.5, -1.0, 2.0, -0.5, 1.0, 0.8, -0.3, 1.2, 1.8, -0.7]
    sc = build_feedback_scorecard(demo_equity, demo_trades)
    print("=== Backtest Feedback Scorecard ===")
    print(f"composite_score: {sc['composite_score']}")
    print(f"equity_r2: {sc['equity_metrics']['equity_r2']}")
    print(f"max_drawdown: {sc['drawdown_metrics']['max_drawdown']}")
    print(f"stability: {sc['stability_score']}")
    print(f"health_status: {sc['degradation']['health_status']}")
    print(f"recommendations: {sc['recommendations']}")

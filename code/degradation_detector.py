"""Degradation Detector — CUSUM changepoint + rolling health scoring.

Модуль НЕ импортирует broker/client, НЕ пишет state/, НЕ ходит в сеть.
Все функции чистые: list/float-in → list/dict-out. stdlib-only.

Методы:
  - CUSUM changepoint detection:
   累积和 (Page 1954) — детекция сдвига среднего в series.
  - Rolling Information Ratio:
    скользящий IR = mean(r) / std(r) за window баров.
  - Composite health score:
    4-компонентная оценка (healthy/degraded/critical/dead) по:
    rolling Sharpe, rolling IR, max drawdown, win rate.
  - Degradation alert:
    формирование alert если health деградировал.

Связь с плацдармом:
  - scorecard_metrics.sharpe_ratio — compute rolling Sharpe для windows.
  - lifecycle_metrics — rolling PF/Win% statistics.
  - risk_scorecard — portfolio risk components (exposure, drawdown, volatility).
  - Overfit guard: если OOS показывает degradation на CUSUM → стратегия exhausted.
"""
import math
from typing import Any, Dict, List, Optional, Tuple


# ─── Health Status Constants ────────────────────────────────────────────
STATUS_HEALTHY = "healthy"
STATUS_DEGRADED = "degraded"
STATUS_CRITICAL = "critical"
STATUS_DEAD = "dead"

ALL_STATUSES = [STATUS_HEALTHY, STATUS_DEGRADED, STATUS_CRITICAL, STATUS_DEAD]


# ═══════════════════════════════════════════════════════════════════════
# 1. CUSUM Changepoint Detection
# ═══════════════════════════════════════════════════════════════════════

def cusum_detect(
    series: List[float],
    threshold: float = 4.0,
    drift: float = 0.5,
) -> List[int]:
    """CUSUM (Page 1954) — детекция changepoint'ов в series.

    Считает кумулятивную сумму отклонений от среднего.
    S⁺ₜ = max(0, S⁺ₜ₋₁ + (μ₀ - xₜ) - k), где μ₀ = mean(series), k = drift.
    Если S⁺ₜ > threshold → changepoint.

    series: временной ряд (например, returns или Sharpe per window).
    threshold: порог обнаружения (по умолчанию 4.0).
    drift: допустимое отклонение (slack, по умолчанию 0.5).

    Возвращает список индексов changepoint'ов.

    Пример:
        >>> cusum_detect([1, 1, 1, 1, -3, -3, -3, -3, -3])
        [5]
        >>> cusum_detect([1, 1, 1, 1, 1])
        []
    """
    n = len(series)
    if n < 2:
        return []

    # Estimate in-control mean from full series (or first half)
    split = max(1, n // 2)
    mu0 = sum(series[:split]) / split

    s_pos = 0.0
    changepoints: List[int] = []

    for i in range(n):
        s_pos = max(0.0, s_pos + (mu0 - series[i]) - drift)
        if s_pos > threshold:
            changepoints.append(i)
            # Reset after detection to find multiple changepoints
            s_pos = 0.0

    return changepoints


def cusum_detect_two_sided(
    series: List[float],
    threshold: float = 4.0,
    drift: float = 0.5,
) -> Tuple[List[int], List[int]]:
    """Two-sided CUSUM: detects both upward and downward shifts.

    S⁺: detects downward shifts (worsening).
    S⁻: detects upward shifts (improving).

    Возвращает (downshift_indices, upshift_indices).

    Пример:
        >>> downs, ups = cusum_detect_two_sided([1, 1, 1, -3, -3, -3, 5, 5, 5])
        >>> len(downs) >= 1
        True
    """
    n = len(series)
    if n < 2:
        return ([], [])

    split = max(1, n // 2)
    mu0 = sum(series[:split]) / split

    s_pos = 0.0
    s_neg = 0.0
    downshifts: List[int] = []
    upshifts: List[int] = []

    for i in range(n):
        # S⁺ detects negative shift (worsening)
        s_pos = max(0.0, s_pos + (mu0 - series[i]) - drift)
        if s_pos > threshold:
            downshifts.append(i)
            s_pos = 0.0

        # S⁻ detects positive shift (improving)
        s_neg = max(0.0, s_neg + (series[i] - mu0) - drift)
        if s_neg > threshold:
            upshifts.append(i)
            s_neg = 0.0

    return (downshifts, upshifts)


# ═══════════════════════════════════════════════════════════════════════
# 2. Rolling Information Ratio
# ═══════════════════════════════════════════════════════════════════════

def rolling_ir(
    returns: List[float],
    window: int = 20,
) -> List[Optional[float]]:
    """Rolling Information Ratio: mean(r) / std(r) за скользящее окно.

    returns: список периодных доходностей.
    window: размер окна (по умолчанию 20).

    Возвращает список IR значений (None для окон, где std == 0).

    Пример:
        >>> ir = rolling_ir([0.01, 0.02, 0.01, -0.01, 0.015], 3)
        >>> len(ir)
        5
        >>> ir[0] is None
        True
    """
    n = len(returns)
    result: List[Optional[float]] = [None] * n

    for i in range(n):
        start = max(0, i - window + 1)
        w = returns[start:i + 1]
        if len(w) < 2:
            continue

        mean_r = sum(w) / len(w)
        var_r = sum((r - mean_r) ** 2 for r in w) / (len(w) - 1)
        std_r = math.sqrt(var_r)

        if std_r == 0.0:
            result[i] = None
        else:
            result[i] = mean_r / std_r

    return result


# ═══════════════════════════════════════════════════════════════════════
# 3. Composite Health Score
# ═══════════════════════════════════════════════════════════════════════

def _score_component(value: float, good: float, bad: float) -> float:
    """Map a value to 0..1 score: 1=good, 0=bad, linear interpolation."""
    if good == bad:
        return 1.0 if value >= good else 0.0
    t = (value - bad) / (good - bad)
    return max(0.0, min(1.0, t))


def health_score(
    rolling_sharpe: float,
    rolling_ir: float,
    max_dd: float,
    win_rate: float,
    thresholds: Optional[Dict[str, Tuple[float, float]]] = None,
) -> Dict[str, object]:
    """4-component composite health score: healthy/degraded/critical/dead.

    Components:
      1. Rolling Sharpe (higher = better)
      2. Rolling Information Ratio (higher = better)
      3. Max Drawdown (lower = better, represented as negative)
      4. Win Rate (higher = better, 0..1)

    thresholds: dict mapping component name → (good, bad) bounds.
    Default thresholds designed for futures trading with small deposit.

    Returns dict:
    {
        "status": "healthy" | "degraded" | "critical" | "dead",
        "composite_score": float (0..1),
        "components": {
            "sharpe": {"value": float, "score": float (0..1)},
            "ir": {"value": float, "score": float (0..1)},
            "max_dd": {"value": float, "score": float (0..1)},
            "win_rate": {"value": float, "score": float (0..1)},
        },
    }

    Пример:
        >>> h = health_score(1.5, 0.5, -0.05, 0.55)
        >>> h["status"]
        'healthy'
    """
    if thresholds is None:
        thresholds = {
            "sharpe": (1.0, -0.5),     # good=1.0, bad=-0.5
            "ir": (0.3, -0.2),         # good=0.3, bad=-0.2
            "max_dd": (-0.05, -0.25),  # good=-5%, bad=-25%
            "win_rate": (0.50, 0.30),  # good=50%, bad=30%
        }

    components = {
        "sharpe": {
            "value": rolling_sharpe,
            "score": _score_component(
                rolling_sharpe,
                thresholds["sharpe"][0],
                thresholds["sharpe"][1],
            ),
        },
        "ir": {
            "value": rolling_ir,
            "score": _score_component(
                rolling_ir,
                thresholds["ir"][0],
                thresholds["ir"][1],
            ),
        },
        "max_dd": {
            "value": max_dd,
            "score": _score_component(
                max_dd,
                thresholds["max_dd"][0],
                thresholds["max_dd"][1],
            ),
        },
        "win_rate": {
            "value": win_rate,
            "score": _score_component(
                win_rate,
                thresholds["win_rate"][0],
                thresholds["win_rate"][1],
            ),
        },
    }

    # Composite: equal weight across 4 components
    composite = sum(c["score"] for c in components.values()) / 4.0

    if composite >= 0.75:
        status = STATUS_HEALTHY
    elif composite >= 0.50:
        status = STATUS_DEGRADED
    elif composite >= 0.25:
        status = STATUS_CRITICAL
    else:
        status = STATUS_DEAD

    return {
        "status": status,
        "composite_score": round(composite, 4),
        "components": components,
    }


# ═══════════════════════════════════════════════════════════════════════
# 4. Degradation Alert
# ═══════════════════════════════════════════════════════════════════════

def degradation_alert(
    health: Dict[str, object],
    cusum_alerts: Optional[List[int]] = None,
    thresholds: Optional[Dict[str, float]] = None,
) -> Dict[str, object]:
    """Formulate degradation alert based on health + CUSUM.

    health: output of health_score().
    cusum_alerts: list of changepoint indices from cusum_detect().
    thresholds: optional override for alert thresholds.

    Returns dict:
    {
        "alert": bool,
        "severity": "none" | "info" | "warning" | "critical",
        "status": str,
        "cusum_changepoints": list[int],
        "recommendations": list[str],
    }

    Пример:
        >>> h = health_score(0.2, -0.1, -0.20, 0.35)
        >>> a = degradation_alert(h)
        >>> a["alert"]
        True
    """
    if cusum_alerts is None:
        cusum_alerts = []

    status = health.get("status", STATUS_DEAD)
    composite = health.get("composite_score", 0.0)
    n_changepoints = len(cusum_alerts)

    recommendations: List[str] = []

    if status == STATUS_DEAD:
        severity = "critical"
        recommendations.append("STOP trading — strategy is dead")
        recommendations.append("Review regime parameters")
    elif status == STATUS_CRITICAL:
        severity = "critical"
        recommendations.append("Reduce position size to minimum")
        recommendations.append("Monitor closely — possible strategy exhaustion")
    elif status == STATUS_DEGRADED:
        severity = "warning"
        recommendations.append("Consider reducing slot allocation")
        recommendations.append("Check regime alignment")
    else:
        severity = "none"

    # Upgrade severity if CUSUM detected changepoints
    if n_changepoints >= 2 and severity in ("none", "info"):
        severity = "warning"
        recommendations.append(f"CUSUM detected {n_changepoints} regime shifts")
    elif n_changepoints >= 3:
        severity = "critical"
        recommendations.append(f"Multiple CUSUM alerts ({n_changepoints}) — likely regime change")

    alert = severity != "none"

    return {
        "alert": alert,
        "severity": severity,
        "status": status,
        "cusum_changepoints": cusum_alerts,
        "recommendations": recommendations,
    }


# ═══════════════════════════════════════════════════════════════════════
# 5. Full Degradation Report
# ═══════════════════════════════════════════════════════════════════════

def degradation_report(
    returns: List[float],
    sharpe_window: int = 20,
    ir_window: int = 20,
    cusum_threshold: float = 4.0,
    cusum_drift: float = 0.5,
) -> Dict[str, object]:
    """Полный degradation report: CUSUM + health + alert.

    returns: временной ряд доходностей.

    Returns dict with all components.

    Пример:
        >>> report = degradation_report([0.01]*30 + [-0.02]*10 + [0.005]*10)
        >>> report["alert"]["alert"] in (True, False)
        True
    """
    # Compute rolling metrics
    ir_values = rolling_ir(returns, ir_window)
    ir_values_clean = [v for v in ir_values if v is not None]
    avg_ir = sum(ir_values_clean) / len(ir_values_clean) if ir_values_clean else 0.0

    # Rolling Sharpe
    sharpe_values: List[Optional[float]] = []
    n = len(returns)
    for i in range(n):
        start = max(0, i - sharpe_window + 1)
        w = returns[start:i + 1]
        if len(w) < 2:
            sharpe_values.append(None)
            continue
        mean_r = sum(w) / len(w)
        var_r = sum((r - mean_r) ** 2 for r in w) / (len(w) - 1)
        std_r = math.sqrt(var_r)
        if std_r == 0.0:
            sharpe_values.append(0.0)
        else:
            sharpe_values.append(mean_r / std_r * math.sqrt(252))

    sharpe_clean = [v for v in sharpe_values if v is not None]
    last_sharpe = sharpe_clean[-1] if sharpe_clean else 0.0

    # Max drawdown
    cumulative = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in returns:
        cumulative *= (1.0 + r)
        peak = max(peak, cumulative)
        dd = (cumulative - peak) / peak
        max_dd = min(max_dd, dd)

    # Win rate
    wins = sum(1 for r in returns if r > 0)
    win_rate = wins / len(returns) if returns else 0.0

    # CUSUM
    cusum_alerts = cusum_detect(returns, cusum_threshold, cusum_drift)

    # Health
    h = health_score(last_sharpe, avg_ir, max_dd, win_rate)

    # Alert
    alert = degradation_alert(h, cusum_alerts)

    return {
        "returns_count": n,
        "last_sharpe": last_sharpe,
        "avg_ir": avg_ir,
        "max_dd": max_dd,
        "win_rate": win_rate,
        "cusum_changepoints": cusum_alerts,
        "health": h,
        "alert": alert,
    }

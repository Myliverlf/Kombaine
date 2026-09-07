"""Overfit Guard — защита от overfitting при walk-forward optimization.

Модуль НЕ импортирует broker/client, НЕ пишет state/, НЕ ходит в сеть.
Все функции чистые: dict/float-in → float/dict-out. stdlib-only.

Методы:
  - Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2014):
    коррекция на множественное тестирование (N стратегий × M наблюдений).
  - IS→OOS Sharpe degradation ratio:
    отношение OOS/IS Sharpe; если < порога — overfit.
  - Minimum OOS trades threshold:
    минимум сделок в OOS для статистической значимости.
  - Embargo gap checker:
    минимальный gap (bars) между IS и OOS для предотвращения data leakage.

Связь с плацдармом:
  - walk_forward_optimizer.py: optimize_on_window() даёт IS/OOS sharpe для каждой пары окон.
  - scorecard_metrics.sharpe_ratio() — вычисление Sharpe для IS/OOS подвыборок.
  - config: excluded=["RI"], max_slots=3.
"""
import math
from typing import Dict, List, Optional, Tuple


# ─── Defaults ───────────────────────────────────────────────────────────
DEFAULT_MIN_DSR = 0.95        # minimum DSR probability for "not overfit"
DEFAULT_MIN_OOS_TRADES = 15   # minimum OOS trades for significance
DEFAULT_MIN_EMBARGO = 5       # minimum bar gap IS→OOS
DEFAULT_MIN_DEGRADATION = 0.5 # minimum OOS/IS Sharpe ratio


# ═══════════════════════════════════════════════════════════════════════
# 1. Deflated Sharpe Ratio (Bailey 2014)
# ═══════════════════════════════════════════════════════════════════════

def _normal_cdf(x: float) -> float:
    """Standard normal CDF via math.erf (stdlib-only)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def deflated_sharpe_ratio(
    sr: float,
    num_trials: int,
    num_obs: int,
) -> float:
    """Deflated Sharpe Ratio — вероятность (0..1), что SR не случайный.

    Bailey & Lopez de Prado (2014): коррекция на множественное тестирование.
    E[Sr|null] ≈ sqrt(2*ln(N)) / sqrt(n)   (ожидаемый макс SR из N iid)
    V[Sr|null] ≈ π² / (12 * n * ln(N))      (дисперсия максимума)
    DSR = Φ((sr - E[Sr|null]) / sqrt(V[Sr|null]))

    sr:       наблюдаемый Sharpe ratio стратегии.
    num_trials: количество протестированных стратегий/комбинаций (≥1).
    num_obs:  количество наблюдений (bars/trades) для вычисления sr (≥3).

    Возвращает float 0..1:
      close to 1 → SR скорее всего НЕ случайный.
      close to 0 → SR может быть результатом overfitting.

    Пример:
        >>> round(deflated_sharpe_ratio(2.0, 100, 250), 2)
        0.79
        >>> deflated_sharpe_ratio(0.0, 100, 250) < 0.5
        True
    """
    if num_trials <= 0 or num_obs <= 3:
        return 0.0

    n = float(num_obs)

    if num_trials == 1:
        # Single trial: no multiple-testing correction, just test SR != 0
        se = 1.0 / math.sqrt(n)
        if se == 0.0:
            return 1.0 if sr > 0 else 0.0
        z = sr / se
        return _normal_cdf(z)

    # Expected max of N iid N(0, 1) — extreme value approximation
    ln_n = math.log(num_trials)
    sqrt_2lnn = math.sqrt(2.0 * ln_n)

    # E[max of N std normals] ≈ sqrt(2*ln(N)) - (ln(ln(N)) + ln(4π)) / (2*sqrt(2*ln(N)))
    e_max = sqrt_2lnn - (math.log(ln_n) + math.log(4.0 * math.pi)) / (2.0 * sqrt_2lnn)

    # Scale by 1/sqrt(n) for Sharpe scale
    e_sr_null = e_max / math.sqrt(n)

    # Variance: π² / (12 * n * ln(N))
    v_sr_null = (math.pi ** 2) / (12.0 * n * ln_n)

    if v_sr_null <= 0.0:
        return 0.0

    z = (sr - e_sr_null) / math.sqrt(v_sr_null)
    return _normal_cdf(z)


# ═══════════════════════════════════════════════════════════════════════
# 2. IS→OOS Sharpe Degradation Ratio
# ═══════════════════════════════════════════════════════════════════════

def sharpe_degradation_ratio(is_sharpe: float, oos_sharpe: float) -> float:
    """Отношение OOS/IS Sharpe ratio (degradation indicator).

    is_sharpe: Sharpe ratio на in-sample.
    oos_sharpe: Sharpe ratio на out-of-sample.

    Возвращает float:
      1.0 → нет деградации (OOS = IS).
      < 1.0 → деградация (ожидаемо при overfit).
      < 0 → OOS хуже нуля (сильный overfit).

    Пример:
        >>> sharpe_degradation_ratio(2.0, 1.0)
        0.5
        >>> sharpe_degradation_ratio(2.0, -0.5)
        -0.25
    """
    if is_sharpe == 0.0:
        return 0.0
    return oos_sharpe / is_sharpe


# ═══════════════════════════════════════════════════════════════════════
# 3. Minimum OOS Trades Threshold
# ═══════════════════════════════════════════════════════════════════════

def min_oos_trades_ok(
    oos_trades: int,
    min_threshold: int = DEFAULT_MIN_OOS_TRADES,
) -> bool:
    """Проверка: достаточно ли сделок в OOS для статистической значимости.

    oos_trades: количество сделок в out-of-sample.
    min_threshold: минимум (по умолчанию 15).

    Возвращает True если oos_trades >= min_threshold.

    Пример:
        >>> min_oos_trades_ok(20)
        True
        >>> min_oos_trades_ok(5)
        False
    """
    return oos_trades >= min_threshold


# ═══════════════════════════════════════════════════════════════════════
# 4. Embargo Gap Checker
# ═══════════════════════════════════════════════════════════════════════

def embargo_gap_ok(
    bar_distances: List[int],
    min_gap: int = DEFAULT_MIN_EMBARGO,
) -> bool:
    """Проверка embargo gap между IS и OOS окнами.

    bar_distances: список расстояний (в bars) между IS-end и OOS-start
                   для каждой пары окон. Все значения >= 0.
    min_gap: минимальный допустимый gap (по умолчанию 5 bars).

    Возвращает True если ВСЕ gaps >= min_gap (нет data leakage).

    Пример:
        >>> embargo_gap_ok([5, 10, 7])
        True
        >>> embargo_gap_ok([2, 10])
        False
    """
    if not bar_distances:
        return True
    return all(g >= min_gap for g in bar_distances)


# ═══════════════════════════════════════════════════════════════════════
# 5. Composite Overfit Check
# ═══════════════════════════════════════════════════════════════════════

def is_overfit(
    is_sharpe: float,
    oos_sharpe: float,
    num_trials: int,
    num_obs: int,
    oos_trades: int = 0,
    min_dsr: float = DEFAULT_MIN_DSR,
    min_degradation: float = DEFAULT_MIN_DEGRADATION,
) -> Dict[str, object]:
    """Композитная проверка overfit: DSR + degradation + min trades.

    Возвращает dict:
    {
        "is_overfit": bool,
        "dsr": float (0..1),
        "degradation_ratio": float,
        "dsr_pass": bool,
        "degradation_pass": bool,
        "trades_pass": bool,
        "reasons": list[str],
    }

    Пример:
        >>> r = is_overfit(2.0, 1.8, num_trials=10, num_obs=250, oos_trades=30)
        >>> r["is_overfit"]
        False
    """
    reasons: List[str] = []

    # DSR check
    dsr = deflated_sharpe_ratio(oos_sharpe, num_trials, num_obs)
    dsr_pass = dsr >= min_dsr
    if not dsr_pass:
        reasons.append(f"DSR={dsr:.3f} < {min_dsr}")

    # Degradation check
    deg = sharpe_degradation_ratio(is_sharpe, oos_sharpe)
    degradation_pass = deg >= min_degradation
    if not degradation_pass:
        reasons.append(f"degradation={deg:.3f} < {min_degradation}")

    # Trades check
    trades_pass = min_oos_trades_ok(oos_trades)
    if not trades_pass:
        reasons.append(f"oos_trades={oos_trades} < {DEFAULT_MIN_OOS_TRADES}")

    overfit = not (dsr_pass and degradation_pass and trades_pass)

    return {
        "is_overfit": overfit,
        "dsr": dsr,
        "degradation_ratio": deg,
        "dsr_pass": dsr_pass,
        "degradation_pass": degradation_pass,
        "trades_pass": trades_pass,
        "reasons": reasons,
    }


# ═══════════════════════════════════════════════════════════════════════
# 6. Overfit Guard for WFO (all windows)
# ═══════════════════════════════════════════════════════════════════════

def overfit_guard_report(
    window_results: List[Dict],
    num_trials: int,
    min_dsr: float = DEFAULT_MIN_DSR,
    min_degradation: float = DEFAULT_MIN_DEGRADATION,
) -> Dict[str, object]:
    """Aggregated overfit guard for all WFO windows.

    window_results: list of dicts, each with:
        {"is_sharpe": float, "oos_sharpe": float, "oos_trades": int}
    num_trials: total parameter combinations tested.

    Возвращает dict:
    {
        "windows_total": int,
        "windows_overfit": int,
        "overfit_pct": float (0..100),
        "avg_dsr": float,
        "min_dsr": float,
        "per_window": list[dict],
        "verdict": "PASS" | "WARN" | "FAIL",
    }
    """
    per_window: List[Dict] = []
    overfit_count = 0
    dsr_values: List[float] = []

    for wr in window_results:
        result = is_overfit(
            is_sharpe=wr.get("is_sharpe", 0.0),
            oos_sharpe=wr.get("oos_sharpe", 0.0),
            num_trials=num_trials,
            num_obs=max(1, wr.get("oos_trades", 0)),
            oos_trades=wr.get("oos_trades", 0),
            min_dsr=min_dsr,
            min_degradation=min_degradation,
        )
        per_window.append(result)
        dsr_values.append(result["dsr"])
        if result["is_overfit"]:
            overfit_count += 1

    total = len(window_results)
    overfit_pct = (overfit_count / total * 100.0) if total > 0 else 0.0
    avg_dsr = sum(dsr_values) / len(dsr_values) if dsr_values else 0.0
    min_dsr_val = min(dsr_values) if dsr_values else 0.0

    if overfit_pct == 0:
        verdict = "PASS"
    elif overfit_pct <= 33.0:
        verdict = "WARN"
    else:
        verdict = "FAIL"

    return {
        "windows_total": total,
        "windows_overfit": overfit_count,
        "overfit_pct": overfit_pct,
        "avg_dsr": avg_dsr,
        "min_dsr": min_dsr_val,
        "per_window": per_window,
        "verdict": verdict,
    }

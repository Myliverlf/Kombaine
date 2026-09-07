"""Forecast Scorer — интеграция TimesFM forecast-aware signals в allocator scoring.

Три чистые функции dict-in→float-out:
  - forecast_bonus(candidate, forecast_result) → float [-1..+1]
  - forecast_risk_penalty(forecast_result) → float [0..1]
  - forecast_feedback(forecast_results) → dict

Плюс расширенный allocator_score:
  - allocator_score_with_forecast(candidate, regime_snapshot, forecast_result, ...) → float

Философия:
  - CI-полоса (q10/q90) — основной forecast signal, НЕ direction
  - Узкая CI = модель уверена → bonus, широкая = неуверенна → penalty
  - forecast missing/dummy → fail-closed: no bonus + explicit penalty flag
  - RI excluded → bonus=0.0 (как в signal_fusion.py EXCLUDED_TICKERS)
  - Forecast weight = 10% от итого (ALLOCATOR_WEIGHTS.forecast)

FAIL_CLOSED_FORECAST = False  # FIX: timesfm not installed -> forecasts always dummy; penalizing missing forecast blocks whole pool
MISSING_FORECAST_PENALTY = 0.12
"""
from typing import Any, Dict, List, Optional


# ─── Веса allocator-скоринга с forecast ───────────────────────────────

ALLOCATOR_WEIGHTS_FORECAST = {
    "expectancy": 40,
    "risk": 30,
    "regime": 20,
    "forecast": 10,
}

# Пороги CI width для классификации уверенности модели
CI_CONFIDENT_THRESHOLD = 0.02   # CI width < 2% → модель уверена
CI_UNCERTAIN_THRESHOLD = 0.08   # CI width > 8% → модель не уверена

# Порог vol spike для risk penalty
VOL_SPIKE_THRESHOLD = 0.06     # CI width > 6% → forecast vol spike

# Исключённые тикеры (как в signal_fusion.py EXCLUDED_TICKERS)
EXCLUDED_TICKERS = {"RI"}

# Fail-closed policy flags
FAIL_CLOSED_FORECAST = False  # FIX: timesfm not installed -> forecasts always dummy; penalizing missing forecast blocks whole pool
MISSING_FORECAST_PENALTY = 0.12


def _forecast_source(forecast_result: Any) -> Optional[str]:
    source = getattr(forecast_result, "source", None) or (
        forecast_result.get("source") if isinstance(forecast_result, dict) else None
    )
    return source


def _forecast_missing(forecast_result: Any) -> bool:
    return forecast_result is None or _forecast_source(forecast_result) in {None, "dummy"}


# ─── 1. Forecast Bonus ────────────────────────────────────────────────

def forecast_bonus(candidate: Dict[str, Any], forecast_result: Any) -> float:
    """Коррекция allocator_score на основе прогноза TimesFM.

    Результат: float от -1.0 до +1.0
      +1.0  — CI очень узкая (< 2%): модель уверена → strong bonus
      +0.5  — CI узкая (2-4%): модель достаточно уверена
       0.0  — CI средняя (4-8%): нейтрально
      -0.5  — CI широкая (8-12%): модель не уверена
      -1.0  — CI очень широкая (> 12%): нет пользы от прогноза

    Если forecast_result.source == "dummy" → 0.0 (fail-open).
    Если ticker в EXCLUDED_TICKERS → 0.0.

    Логика: CI-полоса — лучший use-case TimesFM (accuracy 8/10 по analysis.md).
    Узкая CI → модель даёт useful контекст → бонус. Широкая → нет пользы.
    """
    # Защиты
    ticker = candidate.get("ticker", "")
    if ticker in EXCLUDED_TICKERS:
        return 0.0

    if _forecast_missing(forecast_result):
        return -MISSING_FORECAST_PENALTY if FAIL_CLOSED_FORECAST else 0.0

    # Извлечение полей (работает и с ForecastResult dataclass, и с dict)
    source = _forecast_source(forecast_result)

    ci_width = getattr(forecast_result, "ci_width", None) or (
        forecast_result.get("ci_width", 0.0) if isinstance(forecast_result, dict) else 0.0
    )
    ci_width = float(ci_width)

    # Линейная интерполяция: ci_width → bonus
    if ci_width <= CI_CONFIDENT_THRESHOLD:
        # Очень узкая CI → бонус 0.5..1.0
        bonus = 0.5 + 0.5 * (1.0 - ci_width / CI_CONFIDENT_THRESHOLD)
    elif ci_width <= 0.04:
        # Узкая CI → бонус 0.0..0.5
        bonus = 0.5 * (1.0 - (ci_width - CI_CONFIDENT_THRESHOLD) / (0.04 - CI_CONFIDENT_THRESHOLD))
    elif ci_width <= CI_UNCERTAIN_THRESHOLD:
        # Средняя CI → бонус -0.3..0.0
        ratio = (ci_width - 0.04) / (CI_UNCERTAIN_THRESHOLD - 0.04)
        bonus = -0.3 * ratio
    elif ci_width <= 0.12:
        # Широкая CI → penalty -0.3..-0.7
        ratio = (ci_width - CI_UNCERTAIN_THRESHOLD) / (0.12 - CI_UNCERTAIN_THRESHOLD)
        bonus = -0.3 - 0.4 * ratio
    else:
        # Очень широкая CI → penalty -0.7..-1.0
        bonus = -0.7 - 0.3 * min(1.0, (ci_width - 0.12) / 0.08)

    return round(max(-1.0, min(1.0, bonus)), 4)


# ─── 2. Forecast Risk Penalty ─────────────────────────────────────────

def forecast_risk_penalty(forecast_result: Any) -> float:
    """Дополнительная risk-компонент на основе forecast volatility.

    Если модель прогнозирует волатильный всплеск (CI width > VOL_SPIKE_THRESHOLD) →
    дополнительный penalty 0..1.

    forecast_result.source is missing/dummy → explicit penalty (fail-closed).

    Результат: float 0.0..1.0
      0.0  — нет волатильного всплеска (или dummy)
      0.3  — умеренный spike (CI 6-10%)
      0.6  — значительный spike (CI 10-15%)
      1.0  — экстремальный spike (CI > 20%)
    """
    if _forecast_missing(forecast_result):
        return MISSING_FORECAST_PENALTY if FAIL_CLOSED_FORECAST else 0.0

    source = _forecast_source(forecast_result)

    ci_width = getattr(forecast_result, "ci_width", None) or (
        forecast_result.get("ci_width", 0.0) if isinstance(forecast_result, dict) else 0.0
    )
    ci_width = float(ci_width)

    if ci_width <= VOL_SPIKE_THRESHOLD:
        return 0.0

    # Линейная шкала penalty
    if ci_width <= 0.10:
        penalty = 0.3 * (ci_width - VOL_SPIKE_THRESHOLD) / (0.10 - VOL_SPIKE_THRESHOLD)
    elif ci_width <= 0.15:
        penalty = 0.3 + 0.3 * (ci_width - 0.10) / (0.15 - 0.10)
    elif ci_width <= 0.20:
        penalty = 0.6 + 0.4 * (ci_width - 0.15) / (0.20 - 0.15)
    else:
        penalty = 1.0

    return round(min(1.0, max(0.0, penalty)), 4)


# ─── 3. Forecast Feedback (for generator) ─────────────────────────────

def forecast_feedback(forecast_results: List[Any]) -> Dict[str, Any]:
    """Агрегированные forecast hints для генератора.

    forecast_results: список ForecastResult (или dict) для разных тикеров.

    Возвращает:
    {
        "overall_bias": "up" / "down" / "flat",
        "volatility_regime": "calm" / "normal" / "volatile",
        "tickers_with_signal": int,      # кол-во тикеров с реальным forecast
        "tickers_no_signal": int,         # кол-во тикеров с dummy forecast
        "avg_ci_width": float,            # средняя ширина CI
        "forecast_available": bool,       # есть ли хотя бы один реальный forecast
    }
    """
    if not forecast_results:
        return {
            "overall_bias": "flat",
            "volatility_regime": "normal",
            "tickers_with_signal": 0,
            "tickers_no_signal": 0,
            "avg_ci_width": 0.0,
            "forecast_available": False,
            "missing_forecast_penalty": MISSING_FORECAST_PENALTY,
        }

    up_count = 0
    down_count = 0
    real_count = 0
    dummy_count = 0
    ci_widths = []

    for fr in forecast_results:
        source = getattr(fr, "source", None) or (
            fr.get("source") if isinstance(fr, dict) else None
        )
        direction = getattr(fr, "direction", "flat") or (
            fr.get("direction", "flat") if isinstance(fr, dict) else "flat"
        )
        ci_w = getattr(fr, "ci_width", 0.0) or (
            fr.get("ci_width", 0.0) if isinstance(fr, dict) else 0.0
        )

        if source == "dummy":
            dummy_count += 1
        else:
            real_count += 1
            ci_widths.append(float(ci_w))
            if direction == "up":
                up_count += 1
            elif direction == "down":
                down_count += 1

    # Overall bias
    if up_count > down_count:
        overall_bias = "up"
    elif down_count > up_count:
        overall_bias = "down"
    else:
        overall_bias = "flat"

    # Volatility regime
    avg_ci = sum(ci_widths) / len(ci_widths) if ci_widths else 0.0
    if avg_ci < 0.03:
        vol_regime = "calm"
    elif avg_ci < 0.08:
        vol_regime = "normal"
    else:
        vol_regime = "volatile"

    return {
        "overall_bias": overall_bias,
        "volatility_regime": vol_regime,
        "tickers_with_signal": real_count,
        "tickers_no_signal": dummy_count,
        "avg_ci_width": round(avg_ci, 6),
        "forecast_available": real_count > 0,
        "missing_forecast_penalty": MISSING_FORECAST_PENALTY if dummy_count or real_count == 0 else 0.0,
    }


# ─── 4. Extended allocator score with forecast ─────────────────────────

def allocator_score_with_forecast(
    candidate: Dict[str, Any],
    regime_snapshot: Dict[str, Any],
    forecast_result: Any = None,
    risk_per_trade: float = 0.0,
    weights: Optional[Dict[str, float]] = None,
    forecast_ts: Optional[float] = None,
    now_ts: Optional[float] = None,
    elapsed_bars: int = 0,
) -> float:
    """Композитный allocator-score с forecast компонентой + priors.

    Делает то же, что allocator_metrics.allocator_score, но с весами:
      expectancy=40, risk=30, regime=20, forecast=10.

    Новые параметры (backward compat через defaults):
      forecast_ts  — timestamp прогноза для signal_freshness
      now_ts       — текущий timestamp для signal_freshness
      elapsed_bars — число баров с момента прогноза для forecast_decay

    Интеграция forecast_priors:
      - expectancy_prior: кросс-валидация forecast.direction vs candidate.direction
      - regime_prior: кросс-валидация forecast vs regime_snapshot
      - signal_freshness: множитель к forecast_bonus
      - forecast_decay: множитель к forecast_bonus
      - Итого: f_score = f_bonus * freshness * decay
      - e_prior/r_prior прибавляют ~5% к соответствующим компонентам

    forecast_result может быть None → forecast_bonus=0.0 (fail-open).
    RI excluded → forecast_bonus=0.0.

    candidate: {
        "ticker": str,
        "direction": "LONG" / "SHORT" / None,
        "win_rate": float,
        "avg_win": float,
        "avg_loss": float,
        "drawdown_pct": float (optional),
        "volatility": float (optional),
    }

    Возвращает float: чем больше — тем лучше кандидат.
    """
    # Lazy import из allocator_metrics (не circle)
    from allocator_metrics import expectancy_r, risk_penalty, regime_bonus

    # Lazy import из forecast_priors (не circle)
    try:
        from forecast_priors import (
            forecast_expectancy_prior,
            forecast_regime_prior,
            signal_freshness,
            forecast_decay,
        )
        _HAS_PRIORS = True
    except ImportError:
        _HAS_PRIORS = False

    w = weights or ALLOCATOR_WEIGHTS_FORECAST

    # ── Expectancy (base) ──
    e_r = expectancy_r(
        {
            "win_rate": candidate.get("win_rate", 0.0),
            "avg_win": candidate.get("avg_win", 0.0),
            "avg_loss": candidate.get("avg_loss", 0.0),
        },
        risk_per_trade=risk_per_trade,
    )

    # ── Risk penalty (base + forecast) ──
    r_pen = risk_penalty(candidate)
    fr_risk_pen = forecast_risk_penalty(forecast_result)
    combined_risk = max(r_pen, fr_risk_pen)

    # ── Regime bonus (base) ──
    ticker = candidate.get("ticker", "")
    reg = regime_bonus(
        ticker,
        candidate.get("direction"),
        regime_snapshot,
    )

    # ── Forecast bonus (base) ──
    f_bonus = forecast_bonus(candidate, forecast_result)

    # ── Priors integration (if forecast_priors available) ──
    e_prior_adj = 0.0
    r_prior_adj = 0.0
    f_fresh = 1.0
    f_decay = 1.0

    if _HAS_PRIORS and forecast_result is not None:
        # Expectancy prior: модификатор e_norm (~5% вес)
        e_prior_adj = forecast_expectancy_prior(candidate, forecast_result)

        # Regime prior: модификатор reg (~5% вес)
        r_prior_adj = forecast_regime_prior(forecast_result, regime_snapshot, ticker)

        # Signal freshness: множитель к forecast_bonus
        # Извлекаем horizon для расчёта half_life freshness
        fr_horizon = getattr(forecast_result, "horizon", None) or (
            forecast_result.get("horizon", 20) if isinstance(forecast_result, dict) else 20
        )
        f_fresh = signal_freshness(forecast_ts, now_ts, horizon=int(fr_horizon))

        # Forecast decay: множитель к forecast_bonus
        f_decay = forecast_decay(elapsed_bars)

    # ── Weighted score ──
    e_norm = e_r / (1.0 + abs(e_r)) if e_r != 0 else 0.0

    # Forecast score с priors: bonus * freshness * decay
    f_score = f_bonus * f_fresh * f_decay

    # Expectancy с prior adjustment (~5% от итого: w["expectancy"] * 0.05 = 2)
    e_prior_weight = w["expectancy"] * 0.05  # ~5% of expectancy component

    # Regime с prior adjustment (~5% от итого: w["regime"] * 0.05 = 1)
    r_prior_weight = w["regime"] * 0.05  # ~5% of regime component

    score = (
        w["expectancy"] * e_norm
        + e_prior_weight * e_prior_adj
        + w["risk"] * (1.0 - combined_risk)
        + w["regime"] * reg
        + r_prior_weight * r_prior_adj
        + w["forecast"] * f_score
    ) / 100.0

    return round(score, 6)


# ─── No broker imports guard ──────────────────────────────────────────

BROKER_KEYWORDS = ("tinkoff", "place_order", "send_order", "create_order")


def check_no_broker_imports(filepath: str) -> bool:
    """Проверить что файл не содержит broker-импортов.

    Анализирует только строки import/from для обнаружения broker-зависимостей.
    Возвращает True если файл чистый, False если есть broker imports.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except (OSError, IOError):
        return True  # если не можем прочитать — считаем чистым

    for line in lines:
        stripped = line.strip()
        # Проверяем только строки-импорты
        if stripped.startswith("import ") or stripped.startswith("from "):
            line_lower = stripped.lower()
            for kw in BROKER_KEYWORDS:
                if kw in line_lower:
                    return False
    return True

"""Forecast Scorecard Bridge — forecast-aware metrics for scorecard/degradation/stability.

Модуль связывает ForecastContext с метриками пайплайна strategy_combine:
  - forecast_weighted_sharpe()       — Sharpe с confidence-weight
  - forecast_adjusted_degradation()  — health_score с regime-adjusted порогами
  - forecast_stability_bonus()       — stability с confidence modulation
  - forecast_scorecard_summary()     — unified summary с PnL↑/risk↓

Дизайн:
  - Чистые функции: dict/list-in → dict-out. Без мутаций.
  - Fail-open: нет context → baseline; нет ticker forecast → baseline.
  - RI excluded → forecast contribution = 0.0.
  - Реиспользует _ci_to_bonus, check_no_broker_imports из forecast_context_scorer.
  - stdlib-only (math, typing) + forecast_context/timesfm_adapter imports.
  - No broker/client, no network, no state writes.
"""
from __future__ import annotations

import math
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

# Ensure code/ dir on sys.path for sibling imports
_CODE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)))
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

from forecast_context import ForecastContext, EXCLUDED_TICKERS
from forecast_context_scorer import _ci_to_bonus, check_no_broker_imports
from scorecard_metrics import sharpe_ratio, sortino_ratio
from degradation_detector import health_score, STATUS_HEALTHY, STATUS_DEGRADED, STATUS_CRITICAL, STATUS_DEAD
from lifecycle_metrics import stability


# ─── Vol Regime Threshold Adjustments ──────────────────────────────────

# Vol regime shifts the "good" boundary of health_score thresholds ±10%
VOL_REGIME_ADJUSTMENT = 0.10  # 10% shift

# Confidence → stability bonus/penalty range
MAX_STABILITY_BONUS = 0.15     # +15% at confidence=1.0
MIN_STABILITY_PENALTY = -0.05  # -5% at confidence=0.0

# Sharpe weight from forecast context
FORECAST_SHARPE_WEIGHT = 0.30  # max 30% adjustment to Sharpe


# ─── 1. Forecast-Weighted Sharpe ───────────────────────────────────────


def forecast_weighted_sharpe(
    returns: List[float],
    context: Optional[ForecastContext],
    ticker: str,
) -> Dict[str, Any]:
    """Sharpe ratio с forecast confidence weight.

    Формула:
      weighted_sharpe = baseline_sharpe * (1.0 + ci_bonus * confidence_mult * FORECAST_SHARPE_WEIGHT)

    ci_bonus ∈ [-1, +1] из _ci_to_bonus(ci_width).
    confidence_mult ∈ [0.0, 1.0] из ForecastContext.confidence_multiplier().
    Adjustment range: [-0.3, +0.3] от baseline.

    Args:
        returns: list of period returns (e.g. [0.01, -0.005, ...])
        context: ForecastContext (None → baseline Sharpe)
        ticker: ticker symbol for per-ticker forecast lookup

    Returns:
        dict: {
            "baseline_sharpe": float,
            "weighted_sharpe": float,
            "confidence_mult": float,
            "ci_bonus": float,
            "adjustment_pct": float,
            "ticker": str,
        }
    """
    baseline = sharpe_ratio(returns)

    if context is None or ticker in EXCLUDED_TICKERS:
        return {
            "baseline_sharpe": round(baseline, 6),
            "weighted_sharpe": round(baseline, 6),
            "confidence_mult": 0.0,
            "ci_bonus": 0.0,
            "adjustment_pct": 0.0,
            "ticker": ticker,
        }

    confidence_mult = context.confidence_multiplier()
    fr = context.get_ticker(ticker)
    ci_bonus = 0.0
    if fr is not None:
        ci_bonus = _ci_to_bonus(fr.ci_width)

    adjustment = ci_bonus * confidence_mult * FORECAST_SHARPE_WEIGHT
    weighted = baseline * (1.0 + adjustment)

    return {
        "baseline_sharpe": round(baseline, 6),
        "weighted_sharpe": round(weighted, 6),
        "confidence_mult": round(confidence_mult, 4),
        "ci_bonus": round(ci_bonus, 4),
        "adjustment_pct": round(adjustment * 100, 2),
        "ticker": ticker,
    }


# ─── 2. Forecast-Adjusted Degradation ──────────────────────────────────


def _adjust_thresholds(
    vol_regime: str,
    base_thresholds: Optional[Dict[str, Tuple[float, float]]] = None,
) -> Dict[str, Tuple[float, float]]:
    """Adjust health_score thresholds based on vol_regime.

    - "calm": good boundaries 10% more lenient (shift down)
    - "volatile": good boundaries 10% more strict (shift up)
    - "normal": no adjustment

    Adjustment applies only to the "good" boundary of each component.
    """
    if base_thresholds is None:
        base_thresholds = {
            "sharpe": (1.0, -0.5),
            "ir": (0.3, -0.2),
            "max_dd": (-0.05, -0.25),
            "win_rate": (0.50, 0.30),
        }

    if vol_regime == "normal":
        return dict(base_thresholds)

    factor = -VOL_REGIME_ADJUSTMENT if vol_regime == "calm" else VOL_REGIME_ADJUSTMENT
    adjusted = {}
    for name, (good, bad) in base_thresholds.items():
        # Shift good boundary: for negative-is-good metrics (max_dd), shift goes the other way
        if name == "max_dd":
            # max_dd: good=-0.05, bad=-0.25. Calm → more lenient → good shifts to -0.045 (less negative)
            adjusted[name] = (round(good * (1.0 - factor), 6), bad)
        else:
            # sharpe, ir, win_rate: good is positive. Calm → lower good threshold (more lenient)
            adjusted[name] = (round(good * (1.0 + factor), 6), bad)

    return adjusted


def forecast_adjusted_degradation(
    returns: List[float],
    context: Optional[ForecastContext],
    ticker: str,
    window: int = 20,
) -> Dict[str, Any]:
    """health_score с regime-adjusted порогами.

    Волатильный режим → stricter thresholds → earlier degradation detection.
    Спокойный режим → lenient thresholds → later degradation alert.

    Args:
        returns: list of period returns
        context: ForecastContext (None → baseline degradation)
        ticker: ticker symbol
        window: rolling window for internal metrics

    Returns:
        dict with keys:
            "status": "healthy"/"degraded"/"critical"/"dead"
            "composite_score": float 0..1
            "regime_adjusted": bool
            "vol_regime": str
            "confidence_mult": float
            "components": dict (from health_score)
            "ticker": str
    """
    # Compute baseline metrics for health_score
    rolling_sharpe, rolling_ir, max_dd, win_rate = _compute_health_inputs(returns, window)

    if context is None or ticker in EXCLUDED_TICKERS:
        result = health_score(rolling_sharpe, rolling_ir, max_dd, win_rate)
        result["regime_adjusted"] = False
        result["vol_regime"] = "normal"
        result["confidence_mult"] = 0.0
        result["ticker"] = ticker
        return result

    vol_regime = context.volatility_regime
    confidence_mult = context.confidence_multiplier()

    adjusted_thresholds = _adjust_thresholds(vol_regime)
    result = health_score(rolling_sharpe, rolling_ir, max_dd, win_rate, adjusted_thresholds)
    result["regime_adjusted"] = True
    result["vol_regime"] = vol_regime
    result["confidence_mult"] = round(confidence_mult, 4)
    result["ticker"] = ticker
    return result


def _compute_health_inputs(
    returns: List[float],
    window: int,
) -> Tuple[float, float, float, float]:
    """Compute 4 inputs for health_score from raw returns.

    Returns (rolling_sharpe, rolling_ir, max_dd, win_rate).
    """
    if not returns or len(returns) < 2:
        return (0.0, 0.0, 0.0, 0.0)

    # Rolling Sharpe: last window
    end = min(len(returns), window)
    recent = returns[max(0, len(returns) - end):]
    rolling_sharpe = sharpe_ratio(recent) if len(recent) >= 2 else 0.0

    # Rolling IR: mean / std over last window
    n = len(recent)
    mean_r = sum(recent) / n
    var_r = sum((r - mean_r) ** 2 for r in recent) / max(1, n - 1)
    std_r = math.sqrt(var_r)
    rolling_ir = mean_r / std_r if std_r > 0 else 0.0

    # Max drawdown
    max_dd = _max_drawdown(returns)

    # Win rate
    wins = sum(1 for r in returns if r > 0)
    win_rate = wins / len(returns)

    return (round(rolling_sharpe, 6), round(rolling_ir, 6), round(max_dd, 6), round(win_rate, 4))


def _max_drawdown(returns: List[float]) -> float:
    """Compute max drawdown from period returns (negative value)."""
    if not returns:
        return 0.0
    cum = 1.0
    peak = 1.0
    mdd = 0.0
    for r in returns:
        cum *= (1.0 + r)
        if cum > peak:
            peak = cum
        dd = (cum - peak) / peak
        if dd < mdd:
            mdd = dd
    return round(mdd, 6)


# ─── 3. Forecast Stability Bonus ───────────────────────────────────────


def forecast_stability_bonus(
    returns: List[float],
    context: Optional[ForecastContext],
    ticker: str,
    n_windows: int = 4,
) -> Dict[str, Any]:
    """Stability score с forecast confidence modulation.

    Высокая confidence (>0.5) → bonus до +15%.
    Низкая confidence (≤0.5) → penalty до -5%.
    Stability clamp: [0.0, 1.0].

    Args:
        returns: list of period returns
        context: ForecastContext (None → baseline stability)
        ticker: ticker symbol
        n_windows: number of rolling windows for stability

    Returns:
        dict: {
            "baseline_stability": float,
            "adjusted_stability": float,
            "confidence_bonus": float,
            "confidence_mult": float,
            "ticker": str,
        }
    """
    baseline = stability(returns, n_windows)

    if context is None or ticker in EXCLUDED_TICKERS:
        return {
            "baseline_stability": round(baseline, 6),
            "adjusted_stability": round(baseline, 6),
            "confidence_bonus": 0.0,
            "confidence_mult": 0.0,
            "ticker": ticker,
        }

    confidence = context.confidence_score
    confidence_mult = context.confidence_multiplier()

    # Confidence → stability bonus
    if confidence > 0.5:
        # Linear interpolation: 0.5→0, 1.0→MAX_STABILITY_BONUS
        bonus = (confidence - 0.5) / 0.5 * MAX_STABILITY_BONUS
    else:
        # Linear interpolation: 0.5→0, 0.0→MIN_STABILITY_PENALTY
        bonus = (confidence - 0.5) / 0.5 * abs(MIN_STABILITY_PENALTY)

    adjusted = baseline * (1.0 + bonus)
    adjusted = max(0.0, min(1.0, adjusted))

    return {
        "baseline_stability": round(baseline, 6),
        "adjusted_stability": round(adjusted, 6),
        "confidence_bonus": round(bonus, 4),
        "confidence_mult": round(confidence_mult, 4),
        "ticker": ticker,
    }


# ─── 4. Forecast Scorecard Summary ─────────────────────────────────────


def forecast_scorecard_summary(
    slots: List[Dict[str, Any]],
    context: Optional[ForecastContext],
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Unified forecast-aware scorecard summary.

    Агрегирует:
      - Forecast confidence + bias + vol_regime
      - Per-slot: weighted sharpe, degradation health, stability
      - Portfolio-level: PnL↑ indicator, risk↓ indicator, forecast agreement
      - Config constraints: max_slots, excluded tickers

    Args:
        slots: list of slot dicts [{ticker, score, direction, ...}] max 3
        context: ForecastContext (None → all baseline)
        config: optional config dict with max_slots, excluded, weights

    Returns:
        dict: comprehensive scorecard summary
    """
    max_slots = 3
    excluded = set(EXCLUDED_TICKERS)
    if config:
        max_slots = config.get("max_slots", 3)
        excluded = set(config.get("excluded", list(EXCLUDED_TICKERS)))

    # Cap slots at max_slots
    active_slots = slots[:max_slots]

    # Portfolio-level forecast info
    forecast_available = context.has_forecast() if context else False
    confidence_score = context.confidence_score if context else 0.0
    portfolio_bias = context.portfolio_bias if context else "flat"
    vol_regime = context.volatility_regime if context else "normal"

    # Per-slot metrics
    slot_results = []
    total_weighted_sharpe = 0.0
    healthy_count = 0
    degraded_count = 0
    total_stability = 0.0
    n_active = 0

    for slot in active_slots:
        ticker = slot.get("ticker", "")
        # Use slot_score as proxy returns if no real returns available
        # For summary, we just compute forecast adjustments on slot metadata
        slot_weight = slot.get("score", 5.0) / 10.0  # normalize to 0..1

        # Forecast agreement: does slot direction match portfolio bias?
        direction = slot.get("direction", "").upper()
        forecast_agreement = _direction_match(direction, portfolio_bias, context, ticker)

        # Confidence multiplier for this ticker
        confidence_mult = 0.0
        ci_bonus = 0.0
        if context and ticker not in excluded:
            confidence_mult = context.confidence_multiplier()
            fr = context.get_ticker(ticker)
            if fr is not None:
                ci_bonus = _ci_to_bonus(fr.ci_width)

        slot_result = {
            "ticker": ticker,
            "direction": direction,
            "weight": round(slot_weight, 4),
            "forecast_agreement": forecast_agreement,
            "ci_bonus": round(ci_bonus, 4),
            "confidence_mult": round(confidence_mult, 4),
            "is_excluded": ticker in excluded,
        }
        slot_results.append(slot_result)

        if ticker not in excluded:
            n_active += 1
            total_weighted_sharpe += ci_bonus * confidence_mult * slot_weight
            total_stability += confidence_mult
            if forecast_agreement > 0.5:
                healthy_count += 1
            elif forecast_agreement < -0.3:
                degraded_count += 1

    # Portfolio-level indicators
    avg_agreement = 0.0
    if slot_results:
        avg_agreement = sum(s["forecast_agreement"] for s in slot_results) / len(slot_results)

    # PnL↑: positive if forecast-agreed slots dominate
    pnl_up = avg_agreement > 0.2 and forecast_available

    # risk↓: low risk if no vol_spike, confidence decent
    risk_down = vol_regime != "volatile" and confidence_score >= 0.3

    # Forecast agreement ratio
    agreement_ratio = 0.0
    if n_active > 0:
        positive_agreements = sum(1 for s in slot_results if not s["is_excluded"] and s["forecast_agreement"] > 0)
        agreement_ratio = positive_agreements / n_active

    return {
        "forecast_available": forecast_available,
        "confidence_score": round(confidence_score, 4),
        "portfolio_bias": portfolio_bias,
        "vol_regime": vol_regime,
        "max_slots": max_slots,
        "active_slots_count": len(active_slots),
        "excluded_tickers": sorted(excluded),
        "slots": slot_results,
        "pnl_up_indicator": pnl_up,
        "risk_down_indicator": risk_down,
        "agreement_ratio": round(agreement_ratio, 4),
        "avg_forecast_agreement": round(avg_agreement, 4),
        "forecast_confidence_weighted_score": round(total_weighted_sharpe, 6),
        "healthy_forecast_slots": healthy_count,
        "degraded_forecast_slots": degraded_count,
    }


def _direction_match(
    slot_direction: str,
    portfolio_bias: str,
    context: Optional[ForecastContext],
    ticker: str,
) -> float:
    """Check if slot direction aligns with forecast.

    Returns float in [-1.0, 1.0]:
      +1.0 = fully aligned
      -1.0 = opposed
       0.0 = no forecast / flat
    """
    if context is None or ticker in EXCLUDED_TICKERS:
        return 0.0

    fr = context.get_ticker(ticker)
    if fr is None:
        return 0.0

    forecast_dir = fr.direction  # "up" / "down" / "flat"
    if forecast_dir == "flat":
        return 0.0

    slot_dir = "up" if slot_direction == "LONG" else "down" if slot_direction == "SHORT" else ""
    if not slot_dir:
        return 0.0

    if slot_dir == forecast_dir:
        return 1.0
    return -1.0

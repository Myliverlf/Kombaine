"""RiskTimesFM — ядро: risk-on/off, stale filter, conflict filter, regime-aware prior.

5 чистых функций (0 мутаций существующих файлов):
  1. risk_mode_from_forecast — portfolio-level risk mode
  2. forecast_stale_filter — forecast-aware stale detection
  3. forecast_conflict_filter — position vs forecast conflict
  4. regime_aware_risk_prior — adjusted risk thresholds
  5. adjust_scorecard_with_forecast — modified scorecard dict

Импорт check_no_broker_imports из forecast_context.py (НЕ копировать!).

Не ходит в сеть, не импортирует broker/client.
"""
from __future__ import annotations

import sys
import os
from typing import Any, Dict, List, Optional

# Ensure code/ is on sys.path for sibling imports
_code_dir = os.path.dirname(os.path.abspath(__file__))
if _code_dir not in sys.path:
    sys.path.insert(0, _code_dir)

from forecast_context import ForecastContext, check_no_broker_imports
from timesfm_adapter import ForecastResult
from risk_timesfm_config import RiskTimesFMConfig


# ─── Constants ────────────────────────────────────────────────────────

RI_EXCLUDED = {"RI"}


# ─── 1. Risk Mode ─────────────────────────────────────────────────────

def risk_mode_from_forecast(
    context: ForecastContext,
    config: Optional[RiskTimesFMConfig] = None,
) -> str:
    """Portfolio-level risk mode из forecast context.

    Определяет: "risk-on" / "risk-off" / "neutral" на основе:
      - confidence_score: high → risk-on, low → risk-off
      - vol_regime: volatile → risk-off, calm → bonus к risk-on
      - portfolio_bias: direction alignment подтверждает/ослабляет режим

    Args:
        context: ForecastContext с агрегированными прогнозами
        config: RiskTimesFMConfig с порогами (дефолты если None)

    Returns:
        "risk-on" / "risk-off" / "neutral"
    """
    if config is None:
        config = RiskTimesFMConfig.default()

    if not context.has_forecast():
        return "neutral"

    confidence = context.confidence_score
    vol_regime = context.volatility_regime
    portfolio_bias = context.portfolio_bias

    # Базовый режим по confidence
    base_mode = config.risk_mode_from_scores(confidence, portfolio_bias, vol_regime)

    # Коррекция: flat bias + high confidence → slightly more conservative
    if base_mode == "risk-on" and portfolio_bias == "flat":
        return "neutral"

    return base_mode


# ─── 2. Stale Signal Filter ───────────────────────────────────────────

def forecast_stale_filter(
    slot: Dict[str, Any],
    context: ForecastContext,
    config: Optional[RiskTimesFMConfig] = None,
) -> Dict[str, Any]:
    """Forecast-aware stale detection для слота.

    Слот "stale" если:
      - forecast старше stale_age_hours (по age_hours из context meta или ForecastResult)
      - forecast direction opposes position direction

    Args:
        slot: dict с ticker, direction ("LONG"/"SHORT"), optional age_hours
        context: ForecastContext
        config: RiskTimesFMConfig

    Returns:
        dict: {is_stale: bool, reason: str, confidence: float}
    """
    if config is None:
        config = RiskTimesFMConfig.default()

    ticker = slot.get("ticker", "")
    direction = slot.get("direction", "")
    slot_age_hours = slot.get("age_hours", 0.0)

    result = {
        "is_stale": False,
        "reason": "",
        "confidence": 0.0,
    }

    fr = context.get_ticker(ticker)

    # Нет forecast → не stale (fail-open)
    if fr is None:
        return result

    result["confidence"] = fr.confidence

    # Dummy forecast → не stale (fail-open)
    if getattr(fr, "source", "dummy") == "dummy":
        return result

    # Проверка 1: age > threshold
    if slot_age_hours > config.stale_age_hours:
        result["is_stale"] = True
        result["reason"] = f"forecast age {slot_age_hours:.1f}h > {config.stale_age_hours}h"
        return result

    # Проверка 2: direction mismatch
    forecast_dir = fr.direction  # "up" / "down" / "flat"
    position_to_forecast = _position_matches_forecast(direction, forecast_dir)

    if not position_to_forecast and fr.confidence >= config.conflict_direction_threshold:
        result["is_stale"] = True
        result["reason"] = (
            f"direction mismatch: position={direction}, "
            f"forecast={forecast_dir}, confidence={fr.confidence:.2f}"
        )
        return result

    return result


# ─── 3. Conflict Filter ───────────────────────────────────────────────

def forecast_conflict_filter(
    slot: Dict[str, Any],
    context: ForecastContext,
    config: Optional[RiskTimesFMConfig] = None,
) -> Dict[str, Any]:
    """Detect conflicts между позицией и forecast.

    Типы конфликтов:
      - direction_mismatch: LONG позиция + down forecast (или наоборот)
      - vol_spike: volatile forecast + позиция

    Args:
        slot: dict с ticker, direction, optional strategy
        context: ForecastContext
        config: RiskTimesFMConfig

    Returns:
        dict: {has_conflict: bool, conflict_type: str, severity: float}
    """
    if config is None:
        config = RiskTimesFMConfig.default()

    ticker = slot.get("ticker", "")
    direction = slot.get("direction", "")

    result = {
        "has_conflict": False,
        "conflict_type": "none",
        "severity": 0.0,
    }

    fr = context.get_ticker(ticker)

    # Нет forecast → нет конфликта (fail-open)
    if fr is None:
        return result

    # Dummy forecast → нет конфликта
    if getattr(fr, "source", "dummy") == "dummy":
        return result

    # Конфликт 1: direction mismatch
    forecast_dir = fr.direction
    position_matches = _position_matches_forecast(direction, forecast_dir)

    if not position_matches and fr.confidence >= config.conflict_direction_threshold:
        severity = fr.confidence  # чем выше confidence mismatch, тем серьёзнее
        return {
            "has_conflict": True,
            "conflict_type": "direction_mismatch",
            "severity": round(severity, 4),
        }

    # Конфликт 2: vol spike
    if context.volatility_regime == "volatile" and fr.ci_width > 0.06:
        severity = min(fr.ci_width * 5.0, 1.0)  # нормализуем к 0..1
        return {
            "has_conflict": True,
            "conflict_type": "vol_spike",
            "severity": round(severity, 4),
        }

    return result


# ─── 4. Regime-Aware Risk Prior ───────────────────────────────────────

def regime_aware_risk_prior(
    regime: Dict[str, Any],
    context: ForecastContext,
    config: Optional[RiskTimesFMConfig] = None,
    base_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Adjusted risk thresholds на основе regime + forecast.

    Risk-on + trend → more aggressive (higher PF threshold, wider delta)
    Risk-off + range → more conservative (lower PF threshold, tighter delta)

    Args:
        regime: regime snapshot dict {tickers, bias, trend_cnt, ...}
        context: ForecastContext
        config: RiskTimesFMConfig
        base_config: базовые пороги из config.json risk секции

    Returns:
        dict: {max_slots, eject_pf, delta_band_pct, silent_days, risk_mode}
    """
    if config is None:
        config = RiskTimesFMConfig.default()

    if base_config is None:
        base_config = {
            "max_slots": 3,
            "slot_eject_pf": 0.9,
            "delta_band_pct": 30,
            "slot_eject_silent_days": 5,
        }

    risk_mode = risk_mode_from_forecast(context, config)

    # Базовые значения
    max_slots = base_config.get("max_slots", 3)
    eject_pf = base_config.get("slot_eject_pf", 0.9)
    delta_band_pct = base_config.get("delta_band_pct", 30)
    silent_days = base_config.get("slot_eject_silent_days", 5)

    # Regime multipliers (выбираем по преобладающему regime)
    regime_bias = regime.get("bias", "neutral")
    trend_cnt = regime.get("trend_cnt", 0)
    total_tickers = len(regime.get("tickers", {}))

    if total_tickers > 0 and trend_cnt / total_tickers > 0.5:
        regime_key = "trend"
    else:
        regime_key = "range"

    regime_mult = config.regime_multipliers.get(regime_key, {})

    # Vol multipliers
    vol_key = context.volatility_regime if context.has_forecast() else "normal"
    vol_mult = config.vol_multipliers.get(vol_key, {})

    # Комбинированные множители (умножаем)
    eject_pf *= regime_mult.get("eject_pf_mult", 1.0)
    eject_pf *= vol_mult.get("eject_pf_mult", 1.0)

    max_slots = int(max_slots * regime_mult.get("max_slots_mult", 1.0))
    max_slots = int(max_slots * vol_mult.get("max_slots_mult", 1.0))
    max_slots = max(1, min(max_slots, base_config.get("max_slots", 3)))

    delta_band_pct *= regime_mult.get("delta_band_mult", 1.0)
    delta_band_pct *= vol_mult.get("delta_band_mult", 1.0)

    silent_days = int(silent_days * regime_mult.get("silent_days_mult", 1.0))
    silent_days = int(silent_days * vol_mult.get("silent_days_mult", 1.0))
    silent_days = max(1, silent_days)

    # Risk mode overrides
    if risk_mode == "risk-off" and config.risk_off_max_slots is not None:
        max_slots = min(max_slots, config.risk_off_max_slots)
    if risk_mode == "risk-on" and config.risk_on_max_slots is not None:
        max_slots = max(max_slots, config.risk_on_max_slots)

    return {
        "max_slots": max_slots,
        "eject_pf": round(eject_pf, 4),
        "delta_band_pct": round(delta_band_pct, 2),
        "silent_days": silent_days,
        "risk_mode": risk_mode,
    }


# ─── 5. Scorecard Adjustment ──────────────────────────────────────────

def adjust_scorecard_with_forecast(
    scorecard: Dict[str, Any],
    risk_mode: str,
    per_slot_stale: Optional[Dict[str, bool]] = None,
    per_slot_conflict: Optional[Dict[str, Dict[str, Any]]] = None,
    prior: Optional[Dict[str, Any]] = None,
    config: Optional[RiskTimesFMConfig] = None,
) -> Dict[str, Any]:
    """Modified scorecard с forecast-aware adjustments.

    Добавляет/корректирует компоненты scorecard на основе:
      - risk_mode: risk-off penalizes, risk-on bonuses
      - stale signals: penalty за stale слоты
      - conflicts: penalty за конфликтные слоты
      - prior: regime-adjusted thresholds

    Args:
        scorecard: текущий risk scorecard dict
        risk_mode: "risk-on" / "risk-off" / "neutral"
        per_slot_stale: {slot_id: is_stale}
        per_slot_conflict: {slot_id: conflict_dict}
        prior: output от regime_aware_risk_prior
        config: RiskTimesFMConfig

    Returns:
        dict: modified scorecard (НЕ мутирует входной)
    """
    if config is None:
        config = RiskTimesFMConfig.default()

    # Копируем чтобы не мутировать входной
    result = dict(scorecard)

    # Risk mode adjustment
    forecast_adjustment = {
        "risk_mode": risk_mode,
        "forecast_penalty": 0.0,
        "stale_count": 0,
        "conflict_count": 0,
        "prior_adjustments": {},
    }

    if risk_mode == "risk-off":
        forecast_adjustment["forecast_penalty"] = 10.0  # penalty points
    elif risk_mode == "risk-on":
        forecast_adjustment["forecast_penalty"] = -5.0   # bonus (reduces risk score)

    # Stale signals penalty
    if per_slot_stale:
        stale_count = sum(1 for v in per_slot_stale.values() if v)
        forecast_adjustment["stale_count"] = stale_count
        forecast_adjustment["forecast_penalty"] += stale_count * 5.0

    # Conflicts penalty
    if per_slot_conflict:
        conflict_count = sum(
            1 for v in per_slot_conflict.values()
            if v.get("has_conflict", False)
        )
        forecast_adjustment["conflict_count"] = conflict_count
        forecast_adjustment["forecast_penalty"] += conflict_count * 8.0

    # Prior adjustments
    if prior:
        forecast_adjustment["prior_adjustments"] = {
            "max_slots": prior.get("max_slots"),
            "eject_pf": prior.get("eject_pf"),
            "delta_band_pct": prior.get("delta_band_pct"),
            "silent_days": prior.get("silent_days"),
        }

    result["forecast_adjustment"] = forecast_adjustment
    return result


# ─── Helpers ───────────────────────────────────────────────────────────

def _position_matches_forecast(position_dir: str, forecast_dir: str) -> bool:
    """Проверить совпадение направления позиции и forecast.

    Args:
        position_dir: "LONG" / "SHORT"
        forecast_dir: "up" / "down" / "flat"

    Returns:
        True если совпадают (LONG+up, SHORT+down) или forecast flat
    """
    pos_upper = position_dir.upper()
    fc_lower = forecast_dir.lower()

    if fc_lower == "flat":
        return True  # flat forecast → не противоречит

    if pos_upper == "LONG" and fc_lower == "up":
        return True
    if pos_upper == "SHORT" and fc_lower == "down":
        return True

    return False

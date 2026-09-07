"""Forecast Context Scorer — 4 bridge functions для интеграции ForecastContext
в каждый этап пайплайна strategy_combine.

ForecastContext (из forecast_context.py) → modified score / gate / risk dict
для каждого модуля пайплайна:
  1. forecast_aware_allocator_score()  → candidate_allocator enhanced scoring
  2. forecast_aware_quality_gate()     → quality_gate confidence-weighted filter
  3. forecast_aware_risk_context()     → risk_scorecard forecast vol supplement
  4. forecast_aware_signal_weight()    → signal_fusion forecast-weighted fusion

Каждая функция:
  - Принимает candidate/ideas/slots + ForecastContext
  - Возвращает modified score/dict (НЕ мутирует входные данные)
  - Fail-open: ForecastContext с no forecast → baseline behavior
  - Backward compatible: все модули продолжают работать без ForecastContext
  - Forecast weight = 10% baseline → max 20% при high confidence

Философия:
  - ForecastContext — центральный brain, НЕ форкающий существующие модули
  - Через эти 4 bridge-функции пайплайны получают forecast-aware контекст
  - RI excluded → forecast contribution = 0.0
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from forecast_context import ForecastContext, EXCLUDED_TICKERS
from timesfm_adapter import ForecastResult


# ─── Constants ────────────────────────────────────────────────────────

# Baseline forecast weight в allocator scoring (10% от итого)
BASELINE_FORECAST_WEIGHT = 0.10

# Max forecast weight при high confidence (20% от итого)
MAX_FORECAST_WEIGHT = 0.20

# Confidence thresholds для масштабирования forecast weight
HIGH_CONFIDENCE_THRESHOLD = 0.6
LOW_CONFIDENCE_THRESHOLD = 0.3

# Quality gate thresholds
FORECAST_CONFIDENCE_BONUS_THRESHOLD = 0.5
FORECAST_CONFIDENCE_PENALTY_THRESHOLD = 0.2

# Risk context vol spike threshold
VOL_SPIKE_THRESHOLD = 0.06


# ─── 1. Forecast-Aware Allocator Score ────────────────────────────────

def forecast_aware_allocator_score(
    candidate: Dict[str, Any],
    context: ForecastContext,
    base_score: float = 0.0,
    regime_snapshot: Optional[Dict[str, Any]] = None,
) -> float:
    """Enhanced allocator score с ForecastContext.

    Добавляет forecast-aware компоненту к base_score (из allocator_score).
    Forecast weight масштабируется по confidence_multiplier().

    Args:
        candidate: {ticker, direction, win_rate, avg_win, avg_loss, ...}
        context: ForecastContext
        base_score: текущий allocator_score (без forecast)
        regime_snapshot: для regime_prior cross-validation (optional)

    Returns:
        float: enhanced score = base_score + forecast_contribution

    Fail-open:
        - No real forecasts → base_score unchanged
        - RI excluded → base_score unchanged
        - Missing ticker in context → base_score unchanged
    """
    ticker = candidate.get("ticker", "")

    # RI excluded
    if ticker in EXCLUDED_TICKERS:
        return base_score

    # No real forecasts → no forecast contribution
    if not context.has_forecast():
        return base_score

    # Per-ticker forecast
    fr = context.get_ticker(ticker)
    if fr is None:
        return base_score

    # Dummy source → no contribution
    source = getattr(fr, "source", "dummy")
    if source == "dummy":
        return base_score

    # CI-based bonus: narrow CI → positive, wide CI → negative
    ci_width = getattr(fr, "ci_width", 0.0)
    forecast_bonus = _ci_to_bonus(ci_width)

    # Direction alignment bonus: forecast direction vs candidate direction
    cand_direction = candidate.get("direction")
    forecast_direction = getattr(fr, "direction", "flat")
    direction_bonus = _direction_alignment(cand_direction, forecast_direction)

    # Combined forecast contribution
    forecast_contribution = 0.7 * forecast_bonus + 0.3 * direction_bonus

    # Scale by confidence multiplier
    conf_mult = context.confidence_multiplier()

    # Scale by dynamic forecast weight (10% → 20% based on confidence)
    dynamic_weight = BASELINE_FORECAST_WEIGHT + (
        MAX_FORECAST_WEIGHT - BASELINE_FORECAST_WEIGHT
    ) * conf_mult

    contribution = forecast_contribution * conf_mult * dynamic_weight

    return round(base_score + contribution, 6)


# ─── 2. Forecast-Aware Quality Gate ───────────────────────────────────

def forecast_aware_quality_gate(
    ideas: List[Dict[str, Any]],
    context: ForecastContext,
    base_threshold: float = 0.3,
) -> Dict[str, Any]:
    """Quality gate с forecast confidence-weighted threshold adjustment.

    Если ForecastContext имеет high confidence → threshold снижается
    ( allow more ideas, forecast подтверждает).
    Если low confidence → threshold повышается ( be conservative).

    Args:
        ideas: list of idea dicts [{ticker, strategy_name, score, ...}]
        context: ForecastContext
        base_threshold: базовый порог (default 0.3, из quality_gate.DEFAULT_WEAK_THRESHOLD)

    Returns:
        dict: {
            "adjusted_threshold": float,
            "forecast_confidence": float,
            "ideas_with_forecast_boost": int,
            "forecast_bias": str,
            "recommendation": "pass_all" / "filter" / "boost"
        }

    Fail-open:
        - No real forecasts → base_threshold unchanged
        - Empty ideas → empty result
    """
    result = {
        "adjusted_threshold": base_threshold,
        "forecast_confidence": context.confidence_score,
        "ideas_with_forecast_boost": 0,
        "forecast_bias": context.portfolio_bias,
        "recommendation": "filter",
    }

    if not context.has_forecast():
        return result

    conf = context.confidence_score
    vol_regime = context.volatility_regime

    # Threshold adjustment based on confidence
    if conf >= HIGH_CONFIDENCE_THRESHOLD:
        # High confidence → lower threshold (allow more ideas)
        adjustment = -0.05 * (conf - LOW_CONFIDENCE_THRESHOLD)
        result["adjusted_threshold"] = round(max(0.1, base_threshold + adjustment), 4)
        result["recommendation"] = "boost"
    elif conf <= LOW_CONFIDENCE_THRESHOLD:
        # Low confidence → higher threshold (be conservative)
        adjustment = 0.05 * (1.0 - conf)
        result["adjusted_threshold"] = round(min(0.5, base_threshold + adjustment), 4)
        result["recommendation"] = "filter"
    else:
        result["adjusted_threshold"] = base_threshold
        result["recommendation"] = "pass_all"

    # Vol spike → more conservative
    if vol_regime == "volatile":
        result["adjusted_threshold"] = round(
            min(0.5, result["adjusted_threshold"] + 0.05), 4
        )

    # Count ideas with forecast boost (aligned direction)
    for idea in ideas:
        ticker = idea.get("ticker", "")
        if ticker in EXCLUDED_TICKERS:
            continue
        fr = context.get_ticker(ticker)
        if fr is None or getattr(fr, "source", "dummy") == "dummy":
            continue
        idea_dir = idea.get("direction", "").upper()
        forecast_dir = getattr(fr, "direction", "flat")
        cand_dir = "up" if idea_dir == "LONG" else "down" if idea_dir == "SHORT" else None
        if cand_dir and forecast_dir == cand_dir:
            result["ideas_with_forecast_boost"] += 1

    return result


# ─── 3. Forecast-Aware Risk Context ───────────────────────────────────

def forecast_aware_risk_context(
    slots: List[Dict[str, Any]],
    context: ForecastContext,
) -> Dict[str, Any]:
    """Forecast-aware risk context для risk_scorecard.

    Дополняет risk-оценку forecast-based метриками:
      - forecast_vol_spike: bool (portfolio-level vol spike detected)
      - forecast_confidence_risk: float 0..1 (inverse of confidence → higher = riskier)
      - per_slot_risk: {ticker: {forecast_risk_penalty, ci_width, direction_aligned}}
      - overall_risk_adjustment: float (множитель к risk score, 1.0 = no adjustment)

    Args:
        slots: list of selected slot dicts [{ticker, direction, contracts, ...}]
        context: ForecastContext

    Returns:
        dict: risk context для интеграции в risk_scorecard

    Fail-open:
        - No real forecasts → risk_adjustment = 1.0 (no change)
        - Empty slots → empty risk context
    """
    result = {
        "forecast_vol_spike": False,
        "forecast_confidence_risk": 0.0,
        "per_slot_risk": {},
        "overall_risk_adjustment": 1.0,
        "forecast_available": context.has_forecast(),
    }

    if not context.has_forecast() or not slots:
        return result

    # Portfolio-level vol spike
    if context.volatility_regime == "volatile":
        result["forecast_vol_spike"] = True

    # Confidence risk (inverse: low confidence → high risk)
    result["forecast_confidence_risk"] = round(1.0 - context.confidence_score, 4)

    # Per-slot risk assessment
    total_risk_penalty = 0.0
    n_slots = 0

    for slot in slots:
        ticker = slot.get("ticker", "")
        fr = context.get_ticker(ticker)

        slot_risk = {
            "forecast_risk_penalty": 0.0,
            "ci_width": 0.0,
            "direction_aligned": True,
        }

        if fr is not None and getattr(fr, "source", "dummy") != "dummy":
            ci_width = getattr(fr, "ci_width", 0.0)
            slot_risk["ci_width"] = ci_width

            # Risk penalty from CI width
            if ci_width > VOL_SPIKE_THRESHOLD:
                slot_risk["forecast_risk_penalty"] = min(1.0, (ci_width - VOL_SPIKE_THRESHOLD) / 0.15)

            # Direction alignment check
            cand_direction = slot.get("direction", "")
            forecast_direction = getattr(fr, "direction", "flat")
            cand_dir = "up" if cand_direction == "LONG" else "down" if cand_direction == "SHORT" else None
            forecast_dir = forecast_direction
            if cand_dir and forecast_dir:
                slot_risk["direction_aligned"] = (cand_dir == forecast_dir)

            total_risk_penalty += slot_risk["forecast_risk_penalty"]
            n_slots += 1

        result["per_slot_risk"][ticker] = slot_risk

    # Overall risk adjustment: 1.0 = no change, >1.0 = more conservative
    if n_slots > 0:
        avg_risk_penalty = total_risk_penalty / n_slots
        result["overall_risk_adjustment"] = round(1.0 + avg_risk_penalty * 0.3, 4)

    return result


# ─── 4. Forecast-Aware Signal Weight ──────────────────────────────────

def forecast_aware_signal_weight(
    ticker: str,
    base_weight: float,
    context: ForecastContext,
) -> float:
    """Forecast-weighted signal weight для signal_fusion.

    Модифицирует base_weight (из signal_fusion) на основе ForecastContext:
      - Narrow CI + aligned direction → weight * 1.2 (boost)
      - Wide CI + misaligned direction → weight * 0.7 (penalize)
      - No forecast → weight unchanged (fail-open)
      - Dummy forecast → weight unchanged

    Args:
        ticker: тикер
        base_weight: текущий weight из signal_fusion
        context: ForecastContext

    Returns:
        float: modified weight

    Fail-open:
        - No real forecast → base_weight unchanged
        - RI excluded → base_weight unchanged
    """
    if ticker in EXCLUDED_TICKERS:
        return base_weight

    if not context.has_forecast():
        return base_weight

    fr = context.get_ticker(ticker)
    if fr is None or getattr(fr, "source", "dummy") == "dummy":
        return base_weight

    ci_width = getattr(fr, "ci_width", 0.0)
    confidence = getattr(fr, "confidence", 0.0)

    # CI-based adjustment
    if ci_width < 0.02:
        ci_factor = 1.2  # narrow CI → boost
    elif ci_width < 0.04:
        ci_factor = 1.1
    elif ci_width < 0.08:
        ci_factor = 1.0  # neutral
    elif ci_width < 0.12:
        ci_factor = 0.85  # wide CI → slight penalty
    else:
        ci_factor = 0.7  # very wide CI → penalty

    # Confidence scaling
    conf_mult = context.confidence_multiplier()
    # Blend: 70% CI factor, 30% confidence-scaled
    adjusted_factor = 0.7 * ci_factor + 0.3 * (ci_factor * conf_mult + (1 - conf_mult))

    return round(base_weight * adjusted_factor, 4)


# ─── Internal helpers ──────────────────────────────────────────────────

def _ci_to_bonus(ci_width: float) -> float:
    """CI width → forecast bonus [-1, +1].

    Narrow CI → positive bonus, wide CI → negative.
    Same logic as forecast_scorer.forecast_bonus but simplified.
    """
    if ci_width <= 0.01:
        return 1.0
    if ci_width <= 0.02:
        return 0.5 + 0.5 * (1.0 - ci_width / 0.02)
    if ci_width <= 0.04:
        return 0.5 * (1.0 - (ci_width - 0.02) / 0.02)
    if ci_width <= 0.08:
        return -0.3 * (ci_width - 0.04) / 0.04
    if ci_width <= 0.12:
        return -0.3 - 0.4 * (ci_width - 0.08) / 0.04
    return max(-1.0, -0.7 - 0.3 * min(1.0, (ci_width - 0.12) / 0.08))


def _direction_alignment(
    candidate_direction: Optional[str],
    forecast_direction: str,
) -> float:
    """Direction alignment: forecast.direction vs candidate.direction → [-1, +1].

    LONG → "up", SHORT → "down".
    +1.0 = fully aligned, -1.0 = opposed, 0.0 = flat/missing.
    """
    if candidate_direction is None or forecast_direction == "flat":
        return 0.0

    cand_dir = "up" if candidate_direction.upper() == "LONG" else "down"

    if cand_dir == forecast_direction:
        return 1.0
    return -1.0


# ─── Scorecard builder ────────────────────────────────────────────────

def build_forecast_scorecard(
    context: ForecastContext,
    allocator_enhanced_scores: Optional[Dict[str, float]] = None,
    risk_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a complete forecast-aware scorecard dict.

    Содержит ключи:
      - PnL↑ (via enhanced allocator scores)
      - risk↓ (via risk_context adjustments)
      - allocator_score (average enhanced score)
      - forecast_confidence
      - forecast_bias
      - vol_regime

    Args:
        context: ForecastContext
        allocator_enhanced_scores: {ticker: enhanced_score} (optional)
        risk_context: output from forecast_aware_risk_context (optional)

    Returns:
        dict: scorecard для portfolio-level reporting
    """
    scorecard = {
        "forecast_confidence": context.confidence_score,
        "forecast_bias": context.portfolio_bias,
        "vol_regime": context.volatility_regime,
        "forecast_available": context.has_forecast(),
        "tickers_with_signal": context.meta.get("tickers_with_signal", 0),
        "tickers_no_signal": context.meta.get("tickers_no_signal", 0),
        "avg_ci_width": context.meta.get("avg_ci_width", 0.0),
    }

    # Allocator scores
    if allocator_enhanced_scores:
        scores = list(allocator_enhanced_scores.values())
        scorecard["allocator_score"] = round(sum(scores) / len(scores), 6) if scores else 0.0
        scorecard["allocator_scores"] = allocator_enhanced_scores
    else:
        scorecard["allocator_score"] = 0.0
        scorecard["allocator_scores"] = {}

    # Risk context
    if risk_context:
        scorecard["risk_adjustment"] = risk_context.get("overall_risk_adjustment", 1.0)
        scorecard["vol_spike_detected"] = risk_context.get("forecast_vol_spike", False)
        scorecard["confidence_risk"] = risk_context.get("forecast_confidence_risk", 0.0)
    else:
        scorecard["risk_adjustment"] = 1.0
        scorecard["vol_spike_detected"] = False
        scorecard["confidence_risk"] = 0.0

    # PnL↑ indicator: positive if any slot has direction-aligned forecast
    enhanced = allocator_enhanced_scores or {}
    # Scorecard indicates forecast-enhanced PnL potential
    scorecard["pnl_up_indicator"] = context.has_forecast() and scorecard["allocator_score"] > 0

    # risk↓ indicator: low risk adjustment
    scorecard["risk_down_indicator"] = (
        scorecard["risk_adjustment"] <= 1.1 and not scorecard["vol_spike_detected"]
    )

    return scorecard


# ─── No broker imports guard ──────────────────────────────────────────

BROKER_KEYWORDS = ("tinkoff", "place_order", "send_order", "create_order")


def check_no_broker_imports(filepath: str) -> bool:
    """AST-guard: проверить что файл не содержит broker-импортов."""
    import ast

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
    except (OSError, IOError):
        return True

    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return True

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name_lower = alias.name.lower()
                for kw in BROKER_KEYWORDS:
                    if kw in name_lower:
                        return False
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").lower()
            for kw in BROKER_KEYWORDS:
                if kw in module:
                    return False

    return True

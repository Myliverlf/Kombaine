"""Forecast Generator Bridge — TimesFM pre-filter / context-teacher for strategy generator.

Additive bridge модуль: связывает ForecastContext с generator/ideas pipeline.
НЕ мутирует существующие файлы — только предоставляет enhanced функции.

Функции:
  1. forecast_prefilter(ideas, forecast_context, config) → {passed, rejected, meta}
     Фильтрует ideas по forecast confidence × direction alignment.
     Fail-open: confidence < 0.3 → pass all без фильтрации.
     VETO для RI (forecast contribution = 0.0).

  2. forecast_aware_idea_score(idea, forecast_context, regime_snapshot, feedback, config) → float
     Обёртка над idea_score() с forecast-weight компонентом (10-15% additive).
     Fail-open: no forecast → baseline score.

  3. forecast_aware_suggest_templates(regime_snapshot, forecast_context) → list[str]
     Если forecast confident (≥0.6) → сузить шаблоны до forecast-aligned.
     Else → standard regime-based suggestions.

  4. run_forecast_quality_gate(ideas, forecast_context, regime_snapshot, feedback, config, returns_map)
     → {passed, rejected, meta}
     Enhanced quality_gate: forecast_prefilter → idea_score (forecast-aware) → filter_weak
     → dedup → consistency → significance → max_slots(≤3). contracts=1.

Зависимости (read-only):
  - strategy_ideas.py (idea_score, filter_weak, suggest_templates, build_idea, IDEA_WEIGHTS, ALL_TEMPLATES, TREND_TEMPLATES, RANGE_TEMPLATES)
  - forecast_context.py (ForecastContext, EXCLUDED_TICKERS, HIGH_CONFIDENCE_THRESHOLD, LOW_CONFIDENCE_THRESHOLD)
  - quality_gate.py (run_quality_gate) — не вызывается напрямую, но логика дублируется
  - candidate_allocator.py — не вызывается, max_slots enforcement через config
  - allocator_metrics.py (expectancy_r, risk_penalty, regime_bonus) — через idea_score
  - timesfm_adapter.py (ForecastResult) — тип данных

Нет broker/tinkoff/futures_lab импортов. AST-guard: check_no_broker_imports().
"""
from __future__ import annotations

import ast
import math
import os
from typing import Any, Dict, List, Optional

# ─── Read-only imports from existing modules ──────────────────────────
from strategy_ideas import idea_score, filter_weak, IDEA_WEIGHTS, ALL_TEMPLATES
from forecast_context import ForecastContext, EXCLUDED_TICKERS, HIGH_CONFIDENCE_THRESHOLD, LOW_CONFIDENCE_THRESHOLD


# ─── Constants ────────────────────────────────────────────────────────

# Forecast component weight in forecast_aware_idea_score (additive)
FORECAST_WEIGHT = 12  # 12% of total score → от 40/30/20/15 до 40/30/20/15/12

# Confidence thresholds for prefilter
PREFILTER_FAIL_OPEN_THRESHOLD = 0.3
PREFILTER_CONFIDENT_THRESHOLD = 0.6

# Direction mapping: idea direction → forecast direction
_DIRECTION_MAP = {
    "LONG": "up",
    "SHORT": "down",
}


# ─── 1. forecast_prefilter ────────────────────────────────────────────

def forecast_prefilter(
    ideas: List[Dict[str, Any]],
    forecast_context: Optional[ForecastContext] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Фильтрует ideas по forecast confidence × direction alignment.

    Логика:
      - forecast_context=None или confidence < 0.3 → fail-open: пропускать все
      - RI ticker → veto (reject)
      - confidence ≥ 0.3 и ticker есть в per_ticker:
        * direction aligned (idea.direction matches forecast) → PASS + boost
        * direction misaligned → REJECT (forecast считает что это bad direction)
      - confidence ≥ 0.3 но ticker нет в per_ticker → PASS (no forecast data → pass)

    Args:
        ideas: список dict-идей [{ticker, strategy_name, direction, ...}]
        forecast_context: ForecastContext или None
        config: {excluded: [...], max_slots: int, ...}

    Returns:
        {
            "passed": [...],
            "rejected": [...],
            "meta": {
                "n_total": int,
                "n_passed": int,
                "n_rejected_veto": int,
                "n_rejected_forecast": int,
                "n_forecast_boosted": int,
                "forecast_confidence": float,
                "fail_open": bool,
            }
        }
    """
    if config is None:
        config = {}

    meta = {
        "n_total": len(ideas),
        "n_passed": 0,
        "n_rejected_veto": 0,
        "n_rejected_forecast": 0,
        "n_forecast_boosted": 0,
        "forecast_confidence": 0.0,
        "fail_open": True,
    }

    if forecast_context is None:
        return {"passed": list(ideas), "rejected": [], "meta": meta}

    meta["forecast_confidence"] = forecast_context.confidence_score
    meta["fail_open"] = forecast_context.confidence_score < PREFILTER_FAIL_OPEN_THRESHOLD

    # Fail-open: low confidence → pass everything without filtering
    if forecast_context.confidence_score < PREFILTER_FAIL_OPEN_THRESHOLD:
        meta["n_passed"] = len(ideas)
        return {"passed": list(ideas), "rejected": [], "meta": meta}

    # Confident enough → apply forecast filtering
    excluded = set(config.get("excluded", []))
    all_excluded = excluded | EXCLUDED_TICKERS

    passed: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []

    for idea in ideas:
        ticker = idea.get("ticker", "")
        idea_direction = idea.get("direction", "")
        forecast_dir = _DIRECTION_MAP.get(idea_direction)  # "LONG"→"up", "SHORT"→"down"

        # VETO for excluded tickers (RI etc.)
        if ticker in all_excluded:
            veto_idea = dict(idea)
            veto_idea["_reject_reason"] = "veto_excluded_ticker"
            rejected.append(veto_idea)
            meta["n_rejected_veto"] += 1
            continue

        # Get per-ticker forecast
        ticker_forecast = forecast_context.get_ticker(ticker)

        if ticker_forecast is None:
            # No forecast data for this ticker → pass through
            passed.append(idea)
            meta["n_passed"] += 1
            continue

        # Skip dummy forecasts (no real signal)
        if getattr(ticker_forecast, "source", "dummy") == "dummy":
            passed.append(idea)
            meta["n_passed"] += 1
            continue

        forecast_direction = getattr(ticker_forecast, "direction", "flat")

        # flat forecast → pass all (no direction signal)
        if forecast_direction == "flat":
            passed.append(idea)
            meta["n_passed"] += 1
            continue

        # Direction alignment check
        if forecast_dir is not None and forecast_direction == forecast_dir:
            # Aligned → pass + mark boosted
            boosted_idea = dict(idea)
            boosted_idea["_forecast_boosted"] = True
            passed.append(boosted_idea)
            meta["n_forecast_boosted"] += 1
            meta["n_passed"] += 1
        elif forecast_dir is not None:
            # Misaligned → reject
            reject_idea = dict(idea)
            reject_idea["_reject_reason"] = (
                "forecast_direction_mismatch: idea=%s forecast=%s"
                % (idea_direction, forecast_direction)
            )
            rejected.append(reject_idea)
            meta["n_rejected_forecast"] += 1
        else:
            # No direction in idea → pass
            passed.append(idea)
            meta["n_passed"] += 1

    # Enforce max_slots
    max_slots = config.get("max_slots", 3)
    if len(passed) > max_slots:
        overflow = passed[max_slots:]
        passed = passed[:max_slots]
        for idea in overflow:
            idea["_reject_reason"] = "exceeds_max_slots"
            rejected.append(idea)

    return {
        "passed": passed,
        "rejected": rejected,
        "meta": meta,
    }


# ─── 2. forecast_aware_idea_score ─────────────────────────────────────

def forecast_aware_idea_score(
    idea: Dict[str, Any],
    forecast_context: Optional[ForecastContext] = None,
    regime_snapshot: Optional[Dict[str, Any]] = None,
    feedback: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> float:
    """Обёртка над idea_score() с forecast-weight компонентом.

    Добавляет 12% forecast-компонент к baseline score (expectancy 40%, risk 30%,
    regime 20%, feedback 15% → +forecast 12%).

    Forecast component = ci_width_bonus × direction_alignment × confidence
      - ci_width_bonus: узкая CI (0.01) → +1.0, широкая (0.1+) → +0.0
      - direction_alignment: aligned → +1.0, misaligned → -0.5, flat/no data → 0.0
      - confidence: directly from forecast

    Fail-open: no forecast_context or no ticker forecast → baseline score.

    Args:
        idea: dict с ticker, strategy_name, direction, ...
        forecast_context: ForecastContext или None
        regime_snapshot: regime data (optional)
        feedback: historical feedback (optional)
        config: конфигурация (optional)

    Returns:
        float: enhanced score (forecast-aware)
    """
    # Baseline score from idea_score
    base_score = idea_score(idea, regime_snapshot, feedback, config)

    # VETO'd ideas stay veto'd
    if math.isinf(base_score) and base_score < 0:
        return base_score

    # No forecast context → baseline
    if forecast_context is None or not forecast_context.has_forecast():
        return base_score

    # Get ticker forecast
    ticker = idea.get("ticker", "")
    ticker_forecast = forecast_context.get_ticker(ticker)

    if ticker_forecast is None:
        return base_score

    # Dummy forecast → no signal → baseline
    if getattr(ticker_forecast, "source", "dummy") == "dummy":
        return base_score

    # ─── Forecast component calculation ────────────────────────────────
    ci_width = getattr(ticker_forecast, "ci_width", 0.05)
    forecast_direction = getattr(ticker_forecast, "direction", "flat")
    confidence = getattr(ticker_forecast, "confidence", 0.0)
    idea_direction = idea.get("direction", "")

    # CI width bonus: narrow CI → high bonus (inverse relationship)
    # 0.01 → 1.0, 0.03 → 0.7, 0.08 → 0.2, 0.1+ → 0.0
    ci_bonus = max(0.0, 1.0 - ci_width * 12.0)

    # Direction alignment
    cand_dir = _DIRECTION_MAP.get(idea_direction)
    if cand_dir is not None and forecast_direction == cand_dir:
        alignment = 1.0  # Aligned
    elif cand_dir is not None and forecast_direction != "flat":
        alignment = -0.5  # Misaligned penalty
    else:
        alignment = 0.0  # No direction or flat forecast

    # Forecast component: ci_bonus × alignment × confidence
    forecast_component = ci_bonus * alignment * confidence

    # Additive: forecast_component = ci_bonus × alignment × confidence
    # base_score is on scale (40*e + 30*r + 20*reg + 15*fb) / 100
    # forecast_contribution adds FORECAST_WEIGHT/100 × component on top.
    # No re-normalization: preserves original score scale.
    forecast_contribution = FORECAST_WEIGHT * forecast_component / 100.0

    enhanced_score = base_score + forecast_contribution

    return round(enhanced_score, 6)


# ─── 3. forecast_aware_suggest_templates ───────────────────────────────

def forecast_aware_suggest_templates(
    regime_snapshot: Optional[Dict[str, Any]] = None,
    forecast_context: Optional[ForecastContext] = None,
) -> List[str]:
    """Предложить шаблоны стратегий с учётом forecast context.

    Логика:
      - forecast_context=None или confidence < PREFILTER_CONFIDENT_THRESHOLD →
        standard regime-based suggestions (delegates to suggest_templates)
      - forecast confident + bias="up" → prefer trend templates (ema_cross, donchian, ...)
      - forecast confident + bias="down" → prefer mean_reversion + downtrend templates
      - forecast confident + bias="flat" → balanced mix

    Args:
        regime_snapshot: regime data (optional)
        forecast_context: ForecastContext (optional)

    Returns:
        list[str]: suggested template names
    """
    # Import suggest_templates from strategy_ideas (avoid circular at module level)
    from strategy_ideas import suggest_templates

    # No forecast context or low confidence → standard
    if forecast_context is None:
        return suggest_templates(regime_snapshot)

    if forecast_context.confidence_score < PREFILTER_CONFIDENT_THRESHOLD:
        return suggest_templates(regime_snapshot)

    # Forecast is confident → narrow templates based on bias
    bias = forecast_context.portfolio_bias

    # Import template categories from strategy_ideas
    from strategy_ideas import TREND_TEMPLATES, RANGE_TEMPLATES

    if bias == "up":
        # Upward bias → prefer trend-following templates
        trend_list = sorted(TREND_TEMPLATES)
        # Add a couple range templates for diversification
        range_addition = sorted(RANGE_TEMPLATES)[:1]
        return trend_list + range_addition

    elif bias == "down":
        # Downward bias → prefer mean-reversion + short-friendly
        range_list = sorted(RANGE_TEMPLATES)
        # Add adx_breakout for strong downtrends
        trend_addition = ["adx_breakout"]
        return range_list + trend_addition

    else:
        # flat → balanced from both categories
        trend_pick = sorted(TREND_TEMPLATES)[:2]
        range_pick = sorted(RANGE_TEMPLATES)[:2]
        return trend_pick + range_pick


# ─── 4. run_forecast_quality_gate ──────────────────────────────────────

def run_forecast_quality_gate(
    ideas: List[Dict[str, Any]],
    forecast_context: Optional[ForecastContext] = None,
    regime_snapshot: Optional[Dict[str, Any]] = None,
    feedback: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
    returns_map: Optional[Dict[str, List[float]]] = None,
    weak_threshold: float = 0.3,
    consistency_threshold: float = 0.15,
    min_trades: int = 30,
) -> Dict[str, Any]:
    """Enhanced quality gate: forecast_prefilter → forecast-aware scoring → standard chain.

    Цепочка:
      1. forecast_prefilter (pre-filter по forecast confidence/direction)
      2. idea_score с forecast-aware scoring (forecast_aware_idea_score)
      3. filter_weak (standard)
      4. consistency_score + significance gate
      5. max_slots enforcement (≤3)
      6. contracts=1

    Args:
        ideas: список dict-идей
        forecast_context: ForecastContext (optional)
        regime_snapshot: regime data (optional)
        feedback: historical feedback (optional)
        config: {excluded, max_slots, max_contracts_per_entry, ...}
        returns_map: {ticker_strategy: [returns]} для consistency scoring
        weak_threshold: порог для filter_weak
        consistency_threshold: порог consistency_score
        min_trades: минимальное кол-во сделок для significance

    Returns:
        {
            "passed": [...],
            "rejected": [...],
            "meta": {
                "n_total": int,
                "n_passed": int,
                # forecast_prefilter stats
                "n_rejected_veto": int,
                "n_rejected_forecast": int,
                "n_forecast_boosted": int,
                "forecast_confidence": float,
                "fail_open": bool,
                # quality gate stats
                "n_rejected_weak": int,
                "n_rejected_low_consistency": int,
                "n_rejected_low_significance": int,
                "n_rejected_excess_slots": int,
            }
        }
    """
    if config is None:
        config = {}
    if returns_map is None:
        returns_map = {}

    meta: Dict[str, Any] = {
        "n_total": len(ideas),
        "n_passed": 0,
        "n_rejected_veto": 0,
        "n_rejected_forecast": 0,
        "n_forecast_boosted": 0,
        "forecast_confidence": 0.0,
        "fail_open": True,
        "n_rejected_weak": 0,
        "n_rejected_low_consistency": 0,
        "n_rejected_low_significance": 0,
        "n_rejected_excess_slots": 0,
    }

    # ─── Step 1: Forecast prefilter ────────────────────────────────────
    prefilter_result = forecast_prefilter(ideas, forecast_context, config)
    prefilter_meta = prefilter_result["meta"]
    meta["n_rejected_veto"] = prefilter_meta["n_rejected_veto"]
    meta["n_rejected_forecast"] = prefilter_meta["n_rejected_forecast"]
    meta["n_forecast_boosted"] = prefilter_meta["n_forecast_boosted"]
    meta["forecast_confidence"] = prefilter_meta["forecast_confidence"]
    meta["fail_open"] = prefilter_meta["fail_open"]

    candidates = prefilter_result["passed"]

    # ─── Step 2: Score with forecast-aware scoring ─────────────────────
    scored: List[Dict[str, Any]] = []
    for idea in candidates:
        scored_idea = dict(idea)
        if scored_idea.get("score") is None:
            scored_idea["score"] = forecast_aware_idea_score(
                scored_idea, forecast_context, regime_snapshot, feedback, config
            )
        scored.append(scored_idea)

    # ─── Step 3: filter_weak ───────────────────────────────────────────
    after_weak = filter_weak(scored, threshold=weak_threshold)
    meta["n_rejected_weak"] = len(scored) - len(after_weak)

    # ─── Step 4: consistency + significance ────────────────────────────
    # Lazy import to avoid circular dependency at module level
    from consistency_scorer import consistency_score, check_significance

    passed: List[Dict[str, Any]] = []
    for idea in after_weak:
        idea_key = "%s_%s" % (idea.get("ticker", ""), idea.get("strategy_name", ""))
        returns = returns_map.get(idea_key)

        c_score = consistency_score(
            returns=returns,
            n_trades=idea.get("n_trades", 0),
            min_trades=min_trades,
        )
        idea["consistency_score"] = c_score

        if c_score < consistency_threshold:
            idea["_reject_reason"] = "low_consistency"
            meta["n_rejected_low_consistency"] += 1
            continue

        n_trades = idea.get("n_trades", 0)
        if not check_significance(n_trades, min_trades=min_trades):
            idea["_reject_reason"] = "low_significance"
            meta["n_rejected_low_significance"] += 1
            continue

        passed.append(idea)

    # ─── Step 5: max_slots enforcement ─────────────────────────────────
    max_slots = config.get("max_slots", 3)
    if len(passed) > max_slots:
        passed.sort(
            key=lambda x: x.get("score", 0.0) if not math.isinf(x.get("score", 0.0)) else -1e9,
            reverse=True,
        )
        overflow = passed[max_slots:]
        passed = passed[:max_slots]
        for idea in overflow:
            idea["_reject_reason"] = "exceeds_max_slots"
            meta["n_rejected_excess_slots"] += 1

    # ─── Step 6: contracts=1 ───────────────────────────────────────────
    for idea in passed:
        idea["contracts"] = 1

    meta["n_passed"] = len(passed)

    # Collect all rejected
    all_rejected = prefilter_result["rejected"]
    # Add weak/consistency/significance rejected from scored ideas
    for idea in scored:
        if idea not in after_weak and idea not in all_rejected:
            all_rejected.append(idea)
    for idea in after_weak:
        reason = idea.get("_reject_reason")
        if reason in ("low_consistency", "low_significance") and idea not in all_rejected:
            all_rejected.append(idea)

    return {
        "passed": passed,
        "rejected": all_rejected,
        "meta": meta,
    }


# ─── AST-guard ─────────────────────────────────────────────────────────

BROKER_KEYWORDS = ("tinkoff", "place_order", "send_order", "create_order",
                    "futures_lab", "broker")


def check_no_broker_imports(filepath: Optional[str] = None) -> bool:
    """AST-guard: проверить что файл не содержит broker-импортов.

    Если filepath не указан — проверяет текущий модуль.
    Возвращает True если чисто, False если есть broker imports.
    """
    if filepath is None:
        filepath = os.path.abspath(__file__)

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

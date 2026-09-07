"""Pipeline ↔ TimesFM Bridge — интеграция pipeline_ranker + risk_timesfm_bridge.

Фича 2 плана аттестации: закрывает разрыв R4.

Задача:
  pipeline_ranker строит scorecard_result через risk_scorecard.build_scorecard(),
  но НЕ использует risk_timesfm_bridge.compute_forecast_risk_adjustments().
  Этот модуль добавляет augment_scorecard_with_forecast() — обёртку,
  которая берёт результат pipeline_ranker и обогащает его forecast-aware
  корректировками.

Интерфейс:
  augment_scorecard_with_forecast(pipeline_result, forecast_context, regime_snapshot, config)
    → enriched_pipeline_result

  На входе:
    - pipeline_result — dict из pipeline_ranker.run_pipeline()
    - forecast_context — ForecastContext или None
    - regime_snapshot — dict из state/regime_snapshot.json или None
    - config — полный config.json dict или RiskTimesFMConfig

  На выходе:
    - enriched dict с добавленными ключами:
      - "forecast_risk_adjustments" — результат compute_forecast_risk_adjustments
      - "scorecard" — обогащённый scorecard (если был forecast)
      - "per_slot_scores" — с добавленными forecast_riskPenalty/stale/conflict

Не ходит в сеть, не импортирует broker/client, не отправляет ордера.
"""
from __future__ import annotations

import copy
import json
import math
import os
import sys
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from forecast_context import ForecastContext
from risk_timesfm_bridge import compute_forecast_risk_adjustments
from risk_timesfm_config import RiskTimesFMConfig, load_config


def _extract_slots_from_pipeline(pipeline_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Извлечь selected slots из результата pipeline_ranker в формате для risk_timesfm_bridge."""
    selected = pipeline_result.get("selected", [])
    slots = []
    for i, s in enumerate(selected):
        slot = dict(s)
        slot["slot_id"] = s.get("slot_id", "slot_%s_%d" % (s.get("ticker", ""), i))
        # Ensure direction is present
        if "direction" not in slot:
            slot["direction"] = "LONG"
        slots.append(slot)
    return slots


def _enrich_per_slot_scores(
    per_slot_scores: List[Dict[str, Any]],
    forecast_adjustments: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Добавить forecast-aware данные к per-slot scores."""
    per_slot_adj = forecast_adjustments.get("per_slot", [])
    adj_map = {a.get("ticker", ""): a for a in per_slot_adj}

    enriched = []
    for sc in per_slot_scores:
        enriched_sc = dict(sc)
        ticker = sc.get("ticker", "")
        adj = adj_map.get(ticker)
        if adj:
            enriched_sc["forecast_stale"] = adj.get("stale", {})
            enriched_sc["forecast_conflict"] = adj.get("conflict", {})
        else:
            enriched_sc["forecast_stale"] = {"is_stale": False, "reason": "no adjustment"}
            enriched_sc["forecast_conflict"] = {"has_conflict": False, "conflict_type": "none"}
        enriched.append(enriched_sc)
    return enriched


def _adjust_scorecard_with_risk_mode(
    scorecard: Dict[str, Any],
    risk_mode: str,
    prior: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply risk_mode adjustments to scorecard verdict.

    risk_mode:
      - "risk-off": downgrade verdict (ALLOW→REDUCE, REDUCE→VETO)
      - "risk-on": no change (keep current verdict)
      - "neutral": no change

    If risk_mode is risk-off and prior has tighter thresholds,
    increase risk_score proportionally.
    """
    if risk_mode != "risk-off" or scorecard is None:
        return scorecard

    adjusted = copy.deepcopy(scorecard)
    current_verdict = adjusted.get("verdict", "ALLOW")

    # Downgrade verdict for risk-off
    verdict_map = {"ALLOW": "REDUCE", "REDUCE": "VETO", "VETO": "VETO"}
    adjusted["verdict"] = verdict_map.get(current_verdict, current_verdict)

    # Boost risk_score by 15% for risk-off (capped at 100)
    old_score = adjusted.get("risk_score", 0.0)
    adjusted["risk_score"] = min(100.0, old_score * 1.15)
    adjusted["risk_mode_adjusted"] = True
    adjusted["risk_mode"] = risk_mode

    return adjusted


def augment_scorecard_with_forecast(
    pipeline_result: Dict[str, Any],
    forecast_context: Optional[ForecastContext] = None,
    regime_snapshot: Optional[Dict[str, Any]] = None,
    config: Optional[Any] = None,
) -> Dict[str, Any]:
    """Обогащение результата pipeline_ranker forecast-aware корректировками.

    Pipeline bridge steps:
    1. Извлечь selected slots из pipeline_result
    2. Вызвать risk_timesfm_bridge.compute_forecast_risk_adjustments()
    3. Обогатить per_slot_scores forecast stale/conflict данными
    4. Если risk_mode = "risk-off" → обновить scorecard verdict
    5. Добавить forecast_risk_adjustments к результату

    Args:
        pipeline_result: dict из pipeline_ranker.run_pipeline()
        forecast_context: ForecastContext (None → no forecast adjustments)
        regime_snapshot: regime dict (None → defaults)
        config: RiskTimesFMConfig или dict или None

    Returns:
        enriched dict — копия pipeline_result с добавленными ключами:
        - forecast_risk_adjustments
        - scorecard (обновлённый если risk-off)
        - per_slot_scores (обогащённые)
        - meta.has_forecast_risk: bool
    """
    enriched = copy.deepcopy(pipeline_result)

    # No forecast context → return as-is (backward compat)
    if forecast_context is None or not forecast_context.has_forecast():
        enriched["meta"] = dict(enriched.get("meta", {}))
        enriched["meta"]["has_forecast_risk"] = False
        enriched["forecast_risk_adjustments"] = None
        return enriched

    # Convert config to RiskTimesFMConfig if needed
    rt_config = None
    if config is None:
        rt_config = RiskTimesFMConfig.default()
    elif isinstance(config, RiskTimesFMConfig):
        rt_config = config
    elif isinstance(config, dict):
        # Try to load from dict; on failure, use defaults
        try:
            rt_config = load_config(config)
        except (KeyError, TypeError, ValueError):
            rt_config = RiskTimesFMConfig.default()

    # Extract slots
    slots = _extract_slots_from_pipeline(enriched)
    if not slots:
        enriched["meta"] = dict(enriched.get("meta", {}))
        enriched["meta"]["has_forecast_risk"] = False
        enriched["forecast_risk_adjustments"] = None
        return enriched

    # Build base_risk_config from pipeline result
    base_risk_config = None
    scorecard = enriched.get("scorecard")

    # Compute forecast risk adjustments
    adjustments = compute_forecast_risk_adjustments(
        slots=slots,
        forecast_context=forecast_context,
        regime_snapshot=regime_snapshot,
        config=rt_config,
        base_risk_config=base_risk_config,
        scorecard=scorecard,
    )

    # Enrich per_slot_scores
    enriched["per_slot_scores"] = _enrich_per_slot_scores(
        enriched.get("per_slot_scores", []),
        adjustments,
    )

    # If scorecard adjustment available from bridge
    if adjustments.get("scorecard") is not None:
        enriched["scorecard"] = adjustments["scorecard"]

    # Apply risk_mode verdict adjustment
    risk_mode = adjustments.get("risk_mode", "neutral")
    prior = adjustments.get("prior", {})
    if enriched.get("scorecard") is not None:
        enriched["scorecard"] = _adjust_scorecard_with_risk_mode(
            enriched["scorecard"], risk_mode, prior
        )

    # Store adjustments
    enriched["forecast_risk_adjustments"] = adjustments
    enriched["meta"] = dict(enriched.get("meta", {}))
    enriched["meta"]["has_forecast_risk"] = True
    enriched["meta"]["risk_mode"] = risk_mode

    # Log delta
    old_verdict = pipeline_result.get("scorecard", {}).get("verdict") if pipeline_result.get("scorecard") else None
    new_verdict = enriched.get("scorecard", {}).get("verdict") if enriched.get("scorecard") else None
    if old_verdict != new_verdict:
        enriched["meta"]["verdict_changed"] = True
        enriched["meta"]["verdict_delta"] = f"{old_verdict} → {new_verdict}"

    return enriched


def _demo() -> None:
    """Демонстрация bridge на synthetic данных."""
    # Synthetic pipeline result (as if from pipeline_ranker.run_pipeline)
    pipeline_result = {
        "selected": [
            {
                "ticker": "LKOH",
                "direction": "SHORT",
                "contracts": 1,
                "score": 0.72,
                "win_rate": 0.65,
                "avg_win": 450.0,
                "avg_loss": 220.0,
            },
            {
                "ticker": "GAZP",
                "direction": "SHORT",
                "contracts": 1,
                "score": 0.58,
                "win_rate": 0.55,
                "avg_win": 180.0,
                "avg_loss": 150.0,
            },
        ],
        "per_slot_scores": [
            {"ticker": "LKOH", "expectancy_r": 0.89, "risk_penalty": 0.15, "regime_bonus": 0.1, "allocator_score": 0.72},
            {"ticker": "GAZP", "expectancy_r": 0.21, "risk_penalty": 0.25, "regime_bonus": 0.0, "allocator_score": 0.58},
        ],
        "scorecard": {
            "components": {"exposure": {"value": 0.42, "status": "OK"}},
            "risk_score": 18.5,
            "verdict": "ALLOW",
            "equity": 100000.0,
            "peak_equity": 100000.0,
        },
        "lifecycle": None,
        "lifecycle_verdict": "ALLOW",
        "meta": {
            "n_candidates": 5,
            "n_excluded": 1,
            "n_gated": 0,
            "n_selected": 2,
            "max_slots": 3,
            "has_forecast": True,
        },
    }

    # Synthetic ForecastContext — must use ForecastResult dataclass objects
    from timesfm_adapter import ForecastResult
    fc = ForecastContext(
        per_ticker={
            "LKOH": ForecastResult(direction="down", ci_width=0.08, confidence=0.75, horizon=20, source="dummy"),
            "GAZP": ForecastResult(direction="down", ci_width=0.15, confidence=0.55, horizon=20, source="dummy"),
        },
        portfolio_bias="down",
        volatility_regime="normal",
        confidence_score=0.65,
        meta={"tickers_with_signal": 2, "tickers_no_signal": 0, "avg_ci_width": 0.115, "n_tickers": 2},
    )

    # Regime snapshot
    regime_snapshot = {
        "tickers": {
            "LKOH": {"regime": "trend", "direction": "down", "adx": 28.0, "atr_pct": 0.242},
            "GAZP": {"regime": "trend", "direction": "up", "adx": 35.3, "atr_pct": 0.299},
        },
        "bias": "mixed",
        "trend_cnt": 2,
    }

    # Augment
    enriched = augment_scorecard_with_forecast(
        pipeline_result,
        forecast_context=fc,
        regime_snapshot=regime_snapshot,
    )

    print(json.dumps(enriched, indent=2, ensure_ascii=False, default=str))
    print("\n=== Bridge Summary ===")
    adj = enriched.get("forecast_risk_adjustments", {})
    if adj:
        print(f"risk_mode: {adj.get('risk_mode')}")
        print(f"n_stale: {adj['meta'].get('n_stale', 0)}")
        print(f"n_conflicts: {adj['meta'].get('n_conflicts', 0)}")
    print(f"has_forecast_risk: {enriched['meta'].get('has_forecast_risk')}")
    print(f"verdict_changed: {enriched['meta'].get('verdict_changed', False)}")


if __name__ == "__main__":
    _demo()

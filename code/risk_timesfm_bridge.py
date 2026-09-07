"""RiskTimesFM Bridge — сквозная функция forecast→risk adjustments.

Мост между risk_timesfm.py и существующими risk_scorecard.py / core/risk.py.
Возвращает dict — НЕ мутирует входы.

Может вызываться опционально из pipeline (backward-compat):
  - если forecast_context=None → returns empty adjustments
  - если regime_snapshot=None → uses defaults

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

from forecast_context import ForecastContext
from risk_timesfm_config import RiskTimesFMConfig, load_config
from risk_timesfm import (
    risk_mode_from_forecast,
    forecast_stale_filter,
    forecast_conflict_filter,
    regime_aware_risk_prior,
    adjust_scorecard_with_forecast,
)


def compute_forecast_risk_adjustments(
    slots: List[Dict[str, Any]],
    forecast_context: Optional[ForecastContext] = None,
    regime_snapshot: Optional[Dict[str, Any]] = None,
    config: Optional[RiskTimesFMConfig] = None,
    base_risk_config: Optional[Dict[str, Any]] = None,
    scorecard: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Сквозная функция: forecast context → risk adjustments.

    Агрегирует:
      1. risk_mode_from_forecast → "risk-on" / "risk-off" / "neutral"
      2. forecast_stale_filter → per-slot stale detection
      3. forecast_conflict_filter → per-slot conflict detection
      4. regime_aware_risk_prior → adjusted risk thresholds
      5. adjust_scorecard_with_forecast → modified scorecard (optional)

    Args:
        slots: list of selected slot dicts [{ticker, direction, ...}]
        forecast_context: ForecastContext (None → empty adjustments)
        regime_snapshot: regime dict {tickers, bias, trend_cnt} (None → defaults)
        config: RiskTimesFMConfig (None → defaults)
        base_risk_config: base risk params from config.json (None → defaults)
        scorecard: current risk scorecard dict (None → no scorecard adjustment)

    Returns:
        dict: {
            risk_mode: str,
            per_slot: [{slot_id, ticker, stale, conflict}],
            prior: {max_slots, eject_pf, delta_band_pct, silent_days, risk_mode},
            scorecard: modified scorecard or None,
            meta: {n_slots, n_stale, n_conflicts, available}
        }
    """
    # Fail-open: no context → no adjustments
    if forecast_context is None or not forecast_context.has_forecast():
        return _empty_adjustments(slots)

    if config is None:
        config = RiskTimesFMConfig.default()

    if regime_snapshot is None:
        regime_snapshot = {"tickers": {}, "bias": "neutral", "trend_cnt": 0}

    # 1. Risk mode
    risk_mode = risk_mode_from_forecast(forecast_context, config)

    # 2-3. Per-slot analysis
    per_slot = []
    n_stale = 0
    n_conflicts = 0
    per_slot_stale = {}
    per_slot_conflict = {}

    for i, slot in enumerate(slots):
        slot_id = slot.get("slot_id", f"slot_{i}")
        ticker = slot.get("ticker", "")

        stale = forecast_stale_filter(slot, forecast_context, config)
        conflict = forecast_conflict_filter(slot, forecast_context, config)

        per_slot.append({
            "slot_id": slot_id,
            "ticker": ticker,
            "stale": stale,
            "conflict": conflict,
        })

        per_slot_stale[slot_id] = stale.get("is_stale", False)
        per_slot_conflict[slot_id] = conflict

        if stale.get("is_stale", False):
            n_stale += 1
        if conflict.get("has_conflict", False):
            n_conflicts += 1

    # 4. Regime-aware prior
    prior = regime_aware_risk_prior(
        regime_snapshot, forecast_context, config, base_risk_config
    )

    # 5. Scorecard adjustment (optional)
    modified_scorecard = None
    if scorecard is not None:
        modified_scorecard = adjust_scorecard_with_forecast(
            scorecard, risk_mode, per_slot_stale, per_slot_conflict, prior, config
        )

    return {
        "risk_mode": risk_mode,
        "per_slot": per_slot,
        "prior": prior,
        "scorecard": modified_scorecard,
        "meta": {
            "n_slots": len(slots),
            "n_stale": n_stale,
            "n_conflicts": n_conflicts,
            "available": True,
        },
    }


def _empty_adjustments(slots: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Пустые adjustments для backward-compat (no forecast context)."""
    return {
        "risk_mode": "neutral",
        "per_slot": [
            {
                "slot_id": slot.get("slot_id", f"slot_{i}"),
                "ticker": slot.get("ticker", ""),
                "stale": {"is_stale": False, "reason": "no forecast context", "confidence": 0.0},
                "conflict": {"has_conflict": False, "conflict_type": "none", "severity": 0.0},
            }
            for i, slot in enumerate(slots)
        ],
        "prior": {
            "max_slots": 3,
            "eject_pf": 0.9,
            "delta_band_pct": 30,
            "silent_days": 5,
            "risk_mode": "neutral",
        },
        "scorecard": None,
        "meta": {
            "n_slots": len(slots),
            "n_stale": 0,
            "n_conflicts": 0,
            "available": False,
        },
    }

"""Fusion Pipeline — интеграция fused signals в pipeline ranker.

Модуль не импортирует broker/client, не пишет state/, не ходит в сеть.
Все функции чистые: dict-in → dict-out.

Функции:
  - build_fusion_candidate: оборачивает fused signal в формат кандидата
    совместимый с pipeline_ranker.py (dict с ticker, strategy, signal,
    contracts=1, max_slots≤3).
  - select_top_fusions: выбирает ≤3 лучших без превышения слотов.

Контракты:
  - 1 contract per entry (config.max_contracts_per_entry)
  - max live slots ≤3 (config.risk.max_slots)
  - RI excluded — VETO
  - No broker imports

Источники:
  - pipeline_ranker.py:run_pipeline() — формат кандидата
  - candidate_allocator.py:select_live_slots() — slot selection
  - config.json — constraints
"""
import os
import sys
from typing import Any, Dict, List, Optional

import pandas as pd

# Ensure code/ dir on sys.path
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from signal_fusion import FusionRule, fuse_signals, check_composition_excluded, MAX_STRATEGIES_PER_FUSION
from fusion_scorecard import (
    FusionScorecard,
    score_fusion_with_agreement,
    compare_compositions,
    select_top_compositions,
)

# ─── Constants ─────────────────────────────────────────────────────────

DEFAULT_MAX_SLOTS = 3
DEFAULT_MAX_CONTRACTS = 1
EXCLUDED_TICKERS = {"RI"}


# ─── Build fusion candidate ──────────────────────────────────────────

def build_fusion_candidate(
    composition_label: str,
    fused_signal: pd.Series,
    strategies: List[str],
    method: str,
    ticker: str = "FUSED",
    direction: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Обернуть fused signal в формат кандидата для pipeline_ranker.

    Возвращает dict совместимый с pipeline_ranker.run_pipeline():
      {
          "ticker": str,
          "strategy": str,       # composition label
          "signal": Series,
          "contracts": 1,
          "direction": "LONG"/"SHORT"/None,
          "win_rate": float,
          "avg_win": float,
          "avg_loss": float,
          "fused_strategies": [str],
          "fusion_method": str,
      }
    """
    cfg = config or {}
    max_contracts = cfg.get("max_contracts_per_entry", DEFAULT_MAX_CONTRACTS)
    excluded = set(cfg.get("excluded", []))

    # VETO: excluded tickers
    if ticker in excluded:
        return {
            "ticker": ticker,
            "strategy": composition_label,
            "signal": fused_signal,
            "contracts": 0,
            "direction": direction,
            "win_rate": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "fused_strategies": strategies,
            "fusion_method": method,
            "veto": True,
            "veto_reason": f"excluded ticker {ticker}",
        }

    # VETO: excluded strategies in composition
    if check_composition_excluded(strategies, excluded):
        return {
            "ticker": ticker,
            "strategy": composition_label,
            "signal": fused_signal,
            "contracts": 0,
            "direction": direction,
            "win_rate": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "fused_strategies": strategies,
            "fusion_method": method,
            "veto": True,
            "veto_reason": f"excluded strategy in composition: {strategies}",
        }

    # Определяем direction из fused signal
    if direction is None and len(fused_signal) > 0:
        last_signal = fused_signal.iloc[-1]
        if last_signal > 0:
            direction = "LONG"
        elif last_signal < 0:
            direction = "SHORT"

    # contracts = max_contracts (1 per entry, config constraint)
    contracts = min(1, max_contracts)

    return {
        "ticker": ticker,
        "strategy": composition_label,
        "signal": fused_signal,
        "contracts": contracts,
        "direction": direction,
        "win_rate": 0.5,   # placeholder — заполняется из analytics
        "avg_win": 0.0,    # placeholder
        "avg_loss": 0.0,   # placeholder
        "fused_strategies": strategies,
        "fusion_method": method,
        "veto": False,
    }


# ─── Full pipeline: fuse → score → select ──────────────────────────────

def run_fusion_pipeline(
    signals: Dict[str, pd.Series],
    returns: pd.Series,
    rules: List[FusionRule],
    config: Optional[Dict[str, Any]] = None,
    risk_per_trade: float = 0.0,
) -> Dict[str, Any]:
    """Полный pipeline: генерация fused signals → scoring → выбор top compositions.

    signals: {strategy_name: pd.Series(-1,0,1)}
    returns: временной ряд доходностей (для scoring).
    rules: правила комбинирования.
    config: config.json dict.

    Возвращает:
      {
          "fused_signals": {label: Series},
          "scorecards": [FusionScorecard],
          "selected": [FusionScorecard],
          "meta": {n_compositions, max_slots, ...}
      }
    """
    cfg = config or {}
    max_slots = cfg.get("risk", {}).get("max_slots", DEFAULT_MAX_SLOTS)
    excluded = set(cfg.get("excluded", []))

    # Step 1: Generate fused signals
    fused_signals = fuse_signals(signals, rules)

    # Step 2: Score each fusion
    scorecards: List[FusionScorecard] = []
    for label, fused in fused_signals.items():
        # Extract strategies and method from label
        parts = label.rsplit("_", 1)
        if len(parts) == 2:
            strat_part, method = parts
            strategies = strat_part.split("+")
        else:
            strategies = label.split("+")
            method = "unknown"

        # VETO check
        if any(s in excluded for s in strategies):
            sc = FusionScorecard(
                composition_label=label,
                composite_score=-float("inf"),
                strategies=strategies,
                method=method,
                contracts=0,
            )
            scorecards.append(sc)
            continue

        # Build signals DataFrame for agreement ratio
        available_strats = [s for s in strategies if s in signals]
        if len(available_strats) < 2:
            continue
        signals_df = pd.DataFrame({s: signals[s] for s in available_strats})

        sc = score_fusion_with_agreement(
            composition_label=label,
            signals_df=signals_df,
            fused_signal=fused,
            returns=returns,
            strategies=strategies,
            method=method,
            risk_per_trade=risk_per_trade,
        )
        scorecards.append(sc)

    # Step 3: Select top
    selected = select_top_compositions(scorecards, max_slots=max_slots, excluded=excluded)

    return {
        "fused_signals": fused_signals,
        "scorecards": scorecards,
        "selected": selected,
        "meta": {
            "n_compositions": len(scorecards),
            "n_selected": len(selected),
            "max_slots": max_slots,
            "excluded": sorted(excluded),
        },
    }


# ─── Constraints validators ──────────────────────────────────────────

def validate_fusion_constraints(
    compositions: List[Dict[str, Any]],
    config: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Проверить constraints на список compositions.

    Возвращает список ошибок (пустой = OK).
    Проверки:
      - не более max_slots compositions
      - 1 contract per entry
      - RI excluded
      - max 3 стратегии на composition
    """
    cfg = config or {}
    max_slots = cfg.get("risk", {}).get("max_slots", DEFAULT_MAX_SLOTS)
    excluded = set(cfg.get("excluded", EXCLUDED_TICKERS))

    errors: List[str] = []

    if len(compositions) > max_slots:
        errors.append(
            f"Too many compositions: {len(compositions)} > max_slots={max_slots}"
        )

    for i, comp in enumerate(compositions):
        contracts = comp.get("contracts", 1)
        if contracts > 1:
            errors.append(
                f"Composition {i}: contracts={contracts} > 1 (max_contracts_per_entry)"
            )
        if contracts == 0 and not comp.get("veto", False):
            errors.append(f"Composition {i}: contracts=0 but not vetoed")

        ticker = comp.get("ticker", "")
        if ticker in excluded:
            errors.append(f"Composition {i}: excluded ticker '{ticker}'")

        strats = comp.get("fused_strategies", [])
        if any(s in excluded for s in strats):
            errors.append(f"Composition {i}: excluded strategy in {strats}")
        if len(strats) > MAX_STRATEGIES_PER_FUSION:
            errors.append(
                f"Composition {i}: {len(strats)} strategies > "
                f"max {MAX_STRATEGIES_PER_FUSION}"
            )

    return errors

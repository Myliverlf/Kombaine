"""Quality Gate — unified pre-backtest quality pipeline.

Единая точка входа «отсев слабых идей + дедупликация + consistency + significance».

Цепочка:
  1. idea_score() → score для каждой идеи
  2. filter_weak() → отбросить score < threshold
  3. deduplicate_ideas() → убрать близнецов
  4. consistency_score() filter → отбросить low consistency (< 0.15)
  5. significance gate → отбросить ideas с n_trades < min

Зависимости: strategy_ideas, idea_deduplicator, consistency_scorer (все из code/).

23H: DEFAULT_MIN_TRADES loaded from canonical policy.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from idea_deduplicator import deduplicate_ideas
from consistency_scorer import consistency_score, check_significance
from strategy_ideas import idea_score, filter_weak

# ─── Canonical policy (23H) ──────────────────────────────────────
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
    from canonical_policy_loader import get_threshold as _canonical_threshold
    _CANONICAL_AVAILABLE = True
except ImportError:
    _CANONICAL_AVAILABLE = False

def _canonical_min_trades() -> int:
    if _CANONICAL_AVAILABLE:
        try:
            return int(_canonical_threshold("min_trades"))
        except (ValueError, KeyError):
            pass
    return 8

# Default thresholds
DEFAULT_WEAK_THRESHOLD = 0.3
DEFAULT_CONSISTENCY_THRESHOLD = 0.15
DEFAULT_MIN_TRADES = max(30, _canonical_min_trades())  # Stricter than canonical


def _is_veto(idea: Dict[str, Any]) -> bool:
    """True если idea VETO'd (score = -inf)."""
    score = idea.get("score")
    if score is None:
        return False
    return math.isinf(score) and score < 0


def run_quality_gate(
    ideas: List[Dict[str, Any]],
    regime_snapshot: Optional[Dict[str, Any]] = None,
    feedback: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
    returns_map: Optional[Dict[str, List[float]]] = None,
    weak_threshold: float = DEFAULT_WEAK_THRESHOLD,
    consistency_threshold: float = DEFAULT_CONSISTENCY_THRESHOLD,
    min_trades: int = DEFAULT_MIN_TRADES,
) -> Dict[str, Any]:
    """Unified quality gate для стратегий-идей.

    Args:
        ideas: список dict-идей (уже могут иметь score=None)
        regime_snapshot: текущий regime (optional)
        feedback: historical feedback (optional)
        config: конфигурация {excluded, max_contracts_per_entry, ...}
        returns_map: {ticker+strategy_name: [returns]} для consistency scoring
        weak_threshold: порог для filter_weak (default 0.3)
        consistency_threshold: порог consistency_score (default 0.15)
        min_trades: минимальное кол-во сделок для significance (default 30)

    Returns:
        {
            "passed": [...],   # иdea прошли все gate
            "rejected": [...], # идеи не прошли + причина в _reject_reason
            "meta": {
                "n_total": int,
                "n_passed": int,
                "n_rejected_veto": int,
                "n_rejected_weak": int,
                "n_rejected_dups": int,
                "n_rejected_low_consistency": int,
                "n_rejected_low_significance": int,
            }
        }
    """
    if config is None:
        config = {}
    if returns_map is None:
        returns_map = {}

    meta = {
        "n_total": len(ideas),
        "n_passed": 0,
        "n_rejected_veto": 0,
        "n_rejected_weak": 0,
        "n_rejected_dups": 0,
        "n_rejected_low_consistency": 0,
        "n_rejected_low_significance": 0,
    }

    rejected: List[Dict[str, Any]] = []

    # ─── Step 1: Score all ideas ───────────────────────────────────────
    scored: List[Dict[str, Any]] = []
    for idea in ideas:
        scored_idea = dict(idea)
        if idea.get("score") is None:
            scored_idea["score"] = idea_score(idea, regime_snapshot, feedback, config)
        scored.append(scored_idea)

    # ─── Step 2: Separate VETO'd ───────────────────────────────────────
    clean: List[Dict[str, Any]] = []
    for idea in scored:
        if _is_veto(idea):
            idea["_reject_reason"] = "veto_excluded_ticker"
            rejected.append(idea)
            meta["n_rejected_veto"] += 1
        else:
            clean.append(idea)

    # ─── Step 3: filter_weak ───────────────────────────────────────────
    after_weak = filter_weak(clean, threshold=weak_threshold)
    weak_count = len(clean) - len(after_weak)
    for idea in clean:
        if idea not in after_weak:
            idea["_reject_reason"] = "weak_score"
            rejected.append(idea)
    meta["n_rejected_weak"] = weak_count

    # ─── Step 4: deduplicate ───────────────────────────────────────────
    deduped = deduplicate_ideas(after_weak)
    dup_count = len(after_weak) - len(deduped)
    meta["n_rejected_dups"] = dup_count

    # ─── Step 5: consistency + significance gate ───────────────────────
    passed: List[Dict[str, Any]] = []

    for idea in deduped:
        # Get returns for this idea
        idea_key = f"{idea.get('ticker', '')}_{idea.get('strategy_name', '')}"
        returns = returns_map.get(idea_key)

        # Consistency check
        c_score = consistency_score(
            returns=returns,
            n_trades=idea.get("n_trades", 0),
            min_trades=min_trades,
        )
        idea["consistency_score"] = c_score

        if c_score < consistency_threshold:
            idea["_reject_reason"] = "low_consistency"
            rejected.append(idea)
            meta["n_rejected_low_consistency"] += 1
            continue

        # Significance check
        n_trades = idea.get("n_trades", 0)
        if not check_significance(n_trades, min_trades=min_trades):
            idea["_reject_reason"] = "low_significance"
            rejected.append(idea)
            meta["n_rejected_low_significance"] += 1
            continue

        passed.append(idea)

    meta["n_passed"] = len(passed)

    # ─── Enforce max_slots constraint ──────────────────────────────────
    max_slots = config.get("max_slots", 3)
    if len(passed) > max_slots:
        # Keep top by score
        passed.sort(
            key=lambda x: x.get("score", 0.0) if not math.isinf(x.get("score", 0.0)) else -1e9,
            reverse=True,
        )
        overflow = passed[max_slots:]
        passed = passed[:max_slots]
        for idea in overflow:
            idea["_reject_reason"] = "exceeds_max_slots"
            rejected.append(idea)

    # Enforce contracts=1
    for idea in passed:
        idea["contracts"] = 1

    meta["n_passed"] = len(passed)

    return {
        "passed": passed,
        "rejected": rejected,
        "meta": meta,
    }

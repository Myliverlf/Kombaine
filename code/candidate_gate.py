"""Strict candidate gate for signal_pool.

Goal: keep signal_pool only for proven candidates, without weakening the
existing quality gate. RI is always excluded.

A candidate is considered proven only when it satisfies all of:
- quality_gate_passed == True
- ticker != RI
- consistency_score >= threshold
- n_trades >= minimum
- evidence_count >= minimum
- strategy_independent == True
- contracts_requested <= 1

The module is stdlib-only and side-effect free.

23H: DEFAULT_MIN_TRADES loaded from canonical policy (stricter gate
for signal_pool entry — minimum of canonical and local threshold).
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

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

EXCLUDED_TICKERS = {"RI"}
DEFAULT_MIN_CONSISTENCY = 0.15
DEFAULT_MIN_TRADES = max(30, _canonical_min_trades())  # Signal pool: stricter than canonical
DEFAULT_MIN_EVIDENCE = 2
DEFAULT_MAX_CONTRACTS = 1
DEFAULT_MIN_SCORE = 0.30


def _ticker(candidate: Dict[str, Any]) -> str:
    return str(candidate.get("ticker", "")).upper()


def _float(candidate: Dict[str, Any], key: str, default: float = 0.0) -> float:
    value = candidate.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(candidate: Dict[str, Any], key: str, default: int = 0) -> int:
    value = candidate.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def quality_gate_pass(candidate: Dict[str, Any], min_score: float = DEFAULT_MIN_SCORE) -> bool:
    """Strict quality gate: no weakening, no speculative pool entries."""
    if _ticker(candidate) in EXCLUDED_TICKERS:
        return False
    if not bool(candidate.get("quality_gate_passed", False)):
        return False
    if _float(candidate, "score", 0.0) < min_score:
        return False
    if _float(candidate, "consistency_score", 0.0) < DEFAULT_MIN_CONSISTENCY:
        return False
    if _int(candidate, "n_trades", 0) < DEFAULT_MIN_TRADES:
        return False
    if _int(candidate, "evidence_count", 0) < DEFAULT_MIN_EVIDENCE:
        return False
    if not bool(candidate.get("strategy_independent", False)):
        return False
    if _int(candidate, "contracts_requested", 1) > DEFAULT_MAX_CONTRACTS:
        return False
    return True


def proven_candidate(candidate: Dict[str, Any], min_score: float = DEFAULT_MIN_SCORE) -> Tuple[bool, List[str]]:
    """Return (is_proven, rejection_reasons)."""
    reasons: List[str] = []
    if _ticker(candidate) in EXCLUDED_TICKERS:
        reasons.append("excluded_ticker:RI")
    if not bool(candidate.get("quality_gate_passed", False)):
        reasons.append("quality_gate_failed")
    if _float(candidate, "score", 0.0) < min_score:
        reasons.append("low_score")
    if _float(candidate, "consistency_score", 0.0) < DEFAULT_MIN_CONSISTENCY:
        reasons.append("low_consistency")
    if _int(candidate, "n_trades", 0) < DEFAULT_MIN_TRADES:
        reasons.append("low_significance")
    if _int(candidate, "evidence_count", 0) < DEFAULT_MIN_EVIDENCE:
        reasons.append("insufficient_evidence")
    if not bool(candidate.get("strategy_independent", False)):
        reasons.append("not_independent")
    if _int(candidate, "contracts_requested", 1) > DEFAULT_MAX_CONTRACTS:
        reasons.append("contracts_gt_1")
    return (len(reasons) == 0, reasons)


def filter_proven_candidates(
    candidates: Iterable[Dict[str, Any]],
    min_score: float = DEFAULT_MIN_SCORE,
) -> List[Dict[str, Any]]:
    """Keep only proven candidates, preserving input order among survivors."""
    proven: List[Dict[str, Any]] = []
    for candidate in candidates:
        ok, _ = proven_candidate(candidate, min_score=min_score)
        if ok:
            proven.append(dict(candidate))
    return proven


def build_signal_pool(
    candidates: Iterable[Dict[str, Any]],
    min_score: float = DEFAULT_MIN_SCORE,
    max_pool: int = 20,
) -> Dict[str, Any]:
    """Build signal_pool from proven candidates only.

    The pool is a derived view; no live mutation is performed.
    """
    pool: Dict[str, Dict[str, Any]] = {}
    rejected: List[Dict[str, Any]] = []
    counts = Counter()
    for idx, candidate in enumerate(candidates):
        ok, reasons = proven_candidate(candidate, min_score=min_score)
        ticker = _ticker(candidate)
        counts[ticker] += 1
        if not ok:
            rejected.append({"candidate": dict(candidate), "reasons": reasons})
            continue
        if len(pool) >= max_pool:
            rejected.append({"candidate": dict(candidate), "reasons": ["signal_pool_full"]})
            continue
        key = candidate.get("candidate_id") or f"{ticker}__{candidate.get('strategy', 'strategy')}__{idx}"
        pool[str(key)] = {
            "ticker": ticker,
            "strategy": str(candidate.get("strategy", "")),
            "score": _float(candidate, "score", 0.0),
            "consistency_score": _float(candidate, "consistency_score", 0.0),
            "n_trades": _int(candidate, "n_trades", 0),
            "evidence_count": _int(candidate, "evidence_count", 0),
            "strategy_independent": bool(candidate.get("strategy_independent", False)),
            "contracts": 1,
            "status": "active_signal_pool",
            "proven": True,
        }
    return {
        "strategies": pool,
        "rejected": rejected,
        "meta": {
            "n_input": sum(counts.values()),
            "n_proven": len(pool),
            "n_rejected": len(rejected),
            "excluded_tickers": sorted(EXCLUDED_TICKERS),
            "max_pool": max_pool,
            "min_score": min_score,
        },
    }


def gate_summary(candidates: Iterable[Dict[str, Any]], min_score: float = DEFAULT_MIN_SCORE) -> Dict[str, Any]:
    """Return a compact quality-gate summary for tests/fixtures."""
    passed = []
    rejected = []
    for candidate in candidates:
        ok, reasons = proven_candidate(candidate, min_score=min_score)
        row = dict(candidate)
        row["reasons"] = reasons
        if ok:
            passed.append(row)
        else:
            rejected.append(row)
    return {
        "passed": passed,
        "rejected": rejected,
        "meta": {
            "n_passed": len(passed),
            "n_rejected": len(rejected),
            "ri_excluded": True,
            "quality_gate_strict": True,
        },
    }


__all__ = [
    "EXCLUDED_TICKERS",
    "DEFAULT_MIN_CONSISTENCY",
    "DEFAULT_MIN_TRADES",
    "DEFAULT_MIN_EVIDENCE",
    "DEFAULT_MAX_CONTRACTS",
    "DEFAULT_MIN_SCORE",
    "quality_gate_pass",
    "proven_candidate",
    "filter_proven_candidates",
    "build_signal_pool",
    "gate_summary",
]

"""20-asset × independent-strategy matrix for signal frequency uplift.

The matrix expands signal opportunities across 20 active assets and multiple
independent strategy families, but the output stays strict:
- RI excluded
- only proven candidates are allowed into the pool
- contracts are capped at 1
- live slots remain capped at 3 by the downstream risk layer

This is a dry-run planning artifact, not a trading engine.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from itertools import product
from typing import Any, Dict, Iterable, List, Sequence

from candidate_gate import proven_candidate, filter_proven_candidates

EXCLUDED_TICKERS = {"RI"}
UNIVERSE_20: List[str] = [
    "SBER", "GAZP", "LKOH", "NVTK", "GMKN",
    "T", "ROSN", "SNGS", "MAGN", "CHMF",
    "MOEX", "YNDX", "OZON", "AFKS", "MTSS",
    "PLZL", "TATN", "SMLT", "FIVE", "AFLT",
]

INDEPENDENT_STRATEGIES: List[Dict[str, Any]] = [
    {"strategy": "trend_breakout", "family": "trend", "signal_type": "momentum"},
    {"strategy": "mean_reversion", "family": "reversion", "signal_type": "countertrend"},
    {"strategy": "volatility_breakout", "family": "volatility", "signal_type": "expansion"},
    {"strategy": "pullback", "family": "trend", "signal_type": "retracement"},
    {"strategy": "session_momentum", "family": "intraday", "signal_type": "session"},
]


@dataclass(frozen=True)
class MatrixCell:
    ticker: str
    strategy: str
    family: str
    signal_type: str
    independent: bool
    requires_proven: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def build_strategy_matrix(
    universe: Sequence[str] | None = None,
    strategies: Sequence[Dict[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    """Return a full dry-run matrix of asset × independent-strategy opportunities."""
    universe = list(universe or UNIVERSE_20)
    strategies = list(strategies or INDEPENDENT_STRATEGIES)
    cells: List[Dict[str, Any]] = []
    for ticker, spec in product(universe, strategies):
        if ticker.upper() in EXCLUDED_TICKERS:
            continue
        cells.append(
            MatrixCell(
                ticker=ticker,
                strategy=str(spec.get("strategy", "")),
                family=str(spec.get("family", "")),
                signal_type=str(spec.get("signal_type", "")),
                independent=True,
                requires_proven=True,
            ).to_dict()
        )
    return cells


def signal_opportunity_count(universe: Sequence[str] | None = None, strategies: Sequence[Dict[str, Any]] | None = None) -> int:
    """Number of potential dry-run signal opportunities."""
    return len(build_strategy_matrix(universe=universe, strategies=strategies))


def matrix_summary() -> Dict[str, Any]:
    """Compact scorecard for frequency uplift and independence coverage."""
    matrix = build_strategy_matrix()
    families = sorted({row["family"] for row in matrix})
    strategy_names = sorted({row["strategy"] for row in matrix})
    return {
        "universe_size": len(UNIVERSE_20),
        "strategy_families": families,
        "strategies": strategy_names,
        "opportunities": len(matrix),
        "ri_excluded": True,
        "independent_strategies": True,
        "requires_proven": True,
        "per_asset_average": round(len(matrix) / len(UNIVERSE_20), 2) if UNIVERSE_20 else 0.0,
    }


def select_matrix_candidates(candidates: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep only candidates that match the matrix and pass the strict gate."""
    matrix_keys = {(row["ticker"], row["strategy"]) for row in build_strategy_matrix()}
    selected: List[Dict[str, Any]] = []
    for candidate in candidates:
        key = (str(candidate.get("ticker", "")).upper(), str(candidate.get("strategy", "")))
        if key not in matrix_keys:
            continue
        ok, _ = proven_candidate(candidate)
        if ok:
            selected.append(dict(candidate))
    return selected


def expand_proven_candidates(candidates: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Expand only proven candidates across the independent-strategy matrix."""
    proven = filter_proven_candidates(candidates)
    matrix = build_strategy_matrix()
    by_ticker = {row["ticker"] for row in matrix}
    entries: List[Dict[str, Any]] = []
    source_candidates = list(candidates)
    for candidate in proven:
        ticker = str(candidate.get("ticker", "")).upper()
        if ticker not in by_ticker:
            continue
        for row in matrix:
            if row["ticker"] != ticker:
                continue
            if row["strategy"] != str(candidate.get("strategy", "")):
                continue
            item = dict(candidate)
            item.update(row)
            item["matrix_proven"] = True
            entries.append(item)
    return {
        "expanded": entries,
        "summary": {
            "n_input": len(source_candidates),
            "n_proven": len(proven),
            "n_expanded": len(entries),
            "ri_excluded": True,
        },
    }


__all__ = [
    "EXCLUDED_TICKERS",
    "UNIVERSE_20",
    "INDEPENDENT_STRATEGIES",
    "MatrixCell",
    "build_strategy_matrix",
    "signal_opportunity_count",
    "matrix_summary",
    "select_matrix_candidates",
    "expand_proven_candidates",
]

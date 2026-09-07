"""Convergence Detector for the layered agent orchestration runtime.

Analyzes decisions and results from orchestration cycles to determine
whether the system is making progress, stuck in a loop, or has converged.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


CONVERGENCE_VERSION = "1.0.0"


class Status(Enum):
    """Possible convergence states."""
    CONVERGED = "CONVERGED"
    STUCK = "STUCK"
    PROGRESS = "PROGRESS"
    UNKNOWN = "UNKNOWN"


@dataclass
class ConvergenceReport:
    """Result of a convergence check."""
    status: Status
    reason: str
    details: Dict[str, Any] = field(default_factory=dict)
    version: str = CONVERGENCE_VERSION

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload


def _count_repeated(decisions: List[str], threshold: int = 3) -> Optional[Dict[str, Any]]:
    """Find the most common decision string and its count.

    Returns None if no decision repeats above threshold, otherwise returns
    a dict with the repeated text and its count.
    """
    if not decisions:
        return None
    counter = Counter(decisions)
    most_common_text, most_common_count = counter.most_common(1)[0]
    if most_common_count >= threshold:
        return {"text": most_common_text, "count": most_common_count}
    return None


def _has_new_results(results: List[Dict[str, Any]], min_results: int = 1) -> bool:
    """Check whether the results list contains at least min_results entries."""
    return len(results) >= min_results


def detect(
    decisions: List[str],
    results: List[Dict[str, Any]],
    *,
    stuck_threshold: int = 3,
    min_results_for_progress: int = 1,
) -> ConvergenceReport:
    """Detect convergence status from orchestration decisions and results.

    Logic:
    - STUCK: the same decision string appears >= stuck_threshold times.
    - CONVERGED: not stuck, and no new results needed (empty results, all done).
    - PROGRESS: not stuck, and results indicate ongoing work.
    - UNKNOWN: not enough data.

    Args:
        decisions: List of decision strings from orchestration history.
        results: List of result dicts from orchestration.
        stuck_threshold: How many identical decisions constitute a stuck state.
        min_results_for_progress: Minimum results to consider as progress.

    Returns:
        ConvergenceReport with status, reason, and details.
    """
    if not decisions and not results:
        return ConvergenceReport(
            status=Status.UNKNOWN,
            reason="No decisions or results to analyze.",
            details={"decisions_count": 0, "results_count": 0},
        )

    repeated = _count_repeated(decisions, threshold=stuck_threshold)
    if repeated is not None:
        return ConvergenceReport(
            status=Status.STUCK,
            reason=(
                f"Decision '{repeated['text']}' repeated {repeated['count']} times "
                f"(threshold={stuck_threshold}). System appears stuck."
            ),
            details={
                "repeated_decision": repeated["text"],
                "repetition_count": repeated["count"],
                "threshold": stuck_threshold,
                "total_decisions": len(decisions),
                "total_results": len(results),
            },
        )

    if _has_new_results(results, min_results=min_results_for_progress):
        return ConvergenceReport(
            status=Status.PROGRESS,
            reason=f"System is producing new results ({len(results)} results, {len(decisions)} decisions).",
            details={
                "total_decisions": len(decisions),
                "total_results": len(results),
            },
        )

    if len(decisions) > 0 and not results:
        # FIX(false-converged): decisions without any results is NOT convergence —
        # it means nothing has actually been produced yet. Only an explicit
        # completion marker may converge; otherwise keep probing.
        return ConvergenceReport(
            status=Status.UNKNOWN,
            reason="Decisions recorded but zero results; treating as no-evidence, not converged.",
            details={
                "total_decisions": len(decisions),
                "total_results": 0,
            },
        )

    return ConvergenceReport(
        status=Status.UNKNOWN,
        reason="Insufficient data for convergence determination.",
        details={
            "total_decisions": len(decisions),
            "total_results": len(results),
        },
    )

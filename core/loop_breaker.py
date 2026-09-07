"""Loop Breaker for the LayeredAgentRuntime.

Integrates convergence detection into the runtime spawn decision.
Before spawning agents, checks convergence. If STUCK — skips spawn and
returns a recommendation. If CONVERGED — signals episode completion.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from core.convergence import Status, ConvergenceReport, detect


LOOP_BREAKER_VERSION = "1.0.1"


@dataclass
class SpawnDecision:
    """Decision on whether to spawn the next agent cycle."""
    should_spawn: bool
    convergence_status: Status
    reason: str
    recommendation: str
    details: Dict[str, Any] = field(default_factory=dict)
    version: str = LOOP_BREAKER_VERSION

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["convergence_status"] = self.convergence_status.value
        return payload


def should_spawn(
    decisions: List[str],
    results: List[Dict[str, Any]],
    *,
    stuck_threshold: int = 3,
    min_results_for_progress: int = 1,
) -> SpawnDecision:
    """Determine whether to spawn the next agent cycle.

    Uses convergence detection to decide:
    - STUCK → do NOT spawn, recommend investigation or termination.
    - CONVERGED → do NOT spawn, recommend finalization.
    - PROGRESS → spawn, continue iteration.
    - UNKNOWN → spawn cautiously (first cycle).

    Args:
        decisions: Historical decision strings from prior cycles.
        results: Historical result dicts from prior cycles.
        stuck_threshold: Repetitions before declaring stuck.
        min_results_for_progress: Minimum results to consider as progress.

    Returns:
        SpawnDecision with should_spawn, convergence_status, and recommendation.
    """
    report = detect(
        decisions,
        results,
        stuck_threshold=stuck_threshold,
        min_results_for_progress=min_results_for_progress,
    )

    if report.status == Status.STUCK:
        return SpawnDecision(
            should_spawn=False,
            convergence_status=Status.STUCK,
            reason=report.reason,
            recommendation=(
                "Stop spawning. Review the repeated decision and adjust the goal, "
                "scope, or agent prompts. Consider terminating this episode."
            ),
            details=report.details,
        )

    if report.status == Status.CONVERGED:
        return SpawnDecision(
            should_spawn=False,
            convergence_status=Status.CONVERGED,
            reason=report.reason,
            recommendation=(
                "Episode appears converged. Collect final outputs, "
                "persist a summary, and close the episode."
            ),
            details=report.details,
        )

    if report.status == Status.PROGRESS:
        return SpawnDecision(
            should_spawn=True,
            convergence_status=Status.PROGRESS,
            reason=report.reason,
            recommendation="Continue iterating. Evidence is being produced.",
            details=report.details,
        )

    return SpawnDecision(
        should_spawn=True,
        convergence_status=Status.UNKNOWN,
        reason=report.reason,
        recommendation="First cycle or insufficient data; spawning cautiously.",
        details=report.details,
    )

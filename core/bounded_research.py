"""Bounded research semantics for Iteration 23J.

This module defines an explicit distinction between:
- bounded research that may run on certified 60/90/180/365 horizons;
- long-history qualification that still requires genuine certified 1095-day coverage;
- full qualification / execution eligibility.

It does NOT change canonical qualification thresholds, risk policy, stage logic,
or horizon semantics. It only classifies scope.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict


class ResearchScope(str, Enum):
    RESEARCH_ONLY = "RESEARCH_ONLY"
    BOUNDED_VALIDATED = "BOUNDED_VALIDATED"
    LONG_HISTORY_PENDING = "LONG_HISTORY_PENDING"
    FULLY_QUALIFIED = "FULLY_QUALIFIED"
    EXECUTION_ELIGIBLE = "EXECUTION_ELIGIBLE"


@dataclass(frozen=True)
class ScopeAssessment:
    scope: ResearchScope
    bounded_research_allowed: bool
    long_history_required: bool
    long_history_ready: bool
    full_qualification_ready: bool
    execution_eligible: bool
    reason: str


QUALIFICATION_MATRIX: Dict[str, str] = {
    ResearchScope.RESEARCH_ONLY.value: "Backtests/diagnostics only; no bounded validation yet.",
    ResearchScope.BOUNDED_VALIDATED.value: "Certified available horizons passed; long-history still pending.",
    ResearchScope.LONG_HISTORY_PENDING.value: "Bounded validation passed but genuine 1095d remains unavailable.",
    ResearchScope.FULLY_QUALIFIED.value: "All canonical qualification gates satisfied, including long-history.",
    ResearchScope.EXECUTION_ELIGIBLE.value: "Fully qualified plus independent execution eligibility satisfied.",
}


BOUNDABLE_HORIZONS = (60, 90, 180, 365)
LONG_HISTORY_HORIZON = 1095


def classify_scope(
    *,
    bounded_validated: bool,
    long_history_ready: bool,
    full_qualification_ready: bool = False,
    execution_eligible: bool = False,
) -> ScopeAssessment:
    """Classify a candidate's research scope without weakening canonical gates."""
    if execution_eligible:
        return ScopeAssessment(
            scope=ResearchScope.EXECUTION_ELIGIBLE,
            bounded_research_allowed=False,
            long_history_required=True,
            long_history_ready=long_history_ready,
            full_qualification_ready=full_qualification_ready,
            execution_eligible=True,
            reason="Execution eligibility is only possible after full qualification and independent execution checks.",
        )

    if full_qualification_ready:
        return ScopeAssessment(
            scope=ResearchScope.FULLY_QUALIFIED,
            bounded_research_allowed=False,
            long_history_required=True,
            long_history_ready=long_history_ready,
            full_qualification_ready=True,
            execution_eligible=False,
            reason="Full qualification ready; execution still requires separate eligibility.",
        )

    if bounded_validated and not long_history_ready:
        return ScopeAssessment(
            scope=ResearchScope.LONG_HISTORY_PENDING,
            bounded_research_allowed=True,
            long_history_required=True,
            long_history_ready=False,
            full_qualification_ready=False,
            execution_eligible=False,
            reason="Certified available horizons passed, but genuine 1095d remains unavailable and blocks full qualification.",
        )

    if bounded_validated and long_history_ready:
        return ScopeAssessment(
            scope=ResearchScope.BOUNDED_VALIDATED,
            bounded_research_allowed=True,
            long_history_required=True,
            long_history_ready=True,
            full_qualification_ready=False,
            execution_eligible=False,
            reason="Bounded validation passed; long-history is ready but full qualification has not yet been asserted.",
        )

    return ScopeAssessment(
        scope=ResearchScope.RESEARCH_ONLY,
        bounded_research_allowed=False,
        long_history_required=True,
        long_history_ready=long_history_ready,
        full_qualification_ready=False,
        execution_eligible=False,
        reason="Research evidence is not yet sufficient for bounded validation.",
    )


def bounded_research_allowed(*, bounded_validated: bool, long_history_ready: bool) -> bool:
    """Return True when bounded research may execute without implying full qualification."""
    return bool(bounded_validated and not long_history_ready)


def make_scope_summary(assessment: ScopeAssessment) -> Dict[str, object]:
    return {
        "scope": assessment.scope.value,
        "bounded_research_allowed": assessment.bounded_research_allowed,
        "long_history_required": assessment.long_history_required,
        "long_history_ready": assessment.long_history_ready,
        "full_qualification_ready": assessment.full_qualification_ready,
        "execution_eligible": assessment.execution_eligible,
        "reason": assessment.reason,
    }

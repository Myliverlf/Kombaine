from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "code"))

from bounded_research import (  # noqa: E402
    ResearchScope,
    bounded_research_allowed,
    classify_scope,
    make_scope_summary,
)
from horizon_resolution import InsufficientCoverageError, resolve_horizon  # noqa: E402

DATA_DIR = Path("/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data")


class TestBoundedResearchClassification:
    def test_research_only(self):
        assessment = classify_scope(
            bounded_validated=False,
            long_history_ready=False,
        )
        assert assessment.scope == ResearchScope.RESEARCH_ONLY
        assert assessment.bounded_research_allowed is False

    def test_bounded_validated(self):
        assessment = classify_scope(
            bounded_validated=True,
            long_history_ready=False,
        )
        assert assessment.scope == ResearchScope.LONG_HISTORY_PENDING
        assert assessment.bounded_research_allowed is True
        summary = make_scope_summary(assessment)
        assert summary["bounded_research_allowed"] is True
        assert "1095" in summary["reason"]

    def test_full_qualified_not_bypassed(self):
        assessment = classify_scope(
            bounded_validated=True,
            long_history_ready=False,
            full_qualification_ready=True,
        )
        assert assessment.scope == ResearchScope.FULLY_QUALIFIED
        assert assessment.execution_eligible is False

    def test_execution_eligible_explicit_only(self):
        assessment = classify_scope(
            bounded_validated=True,
            long_history_ready=False,
            full_qualification_ready=True,
            execution_eligible=True,
        )
        assert assessment.scope == ResearchScope.EXECUTION_ELIGIBLE
        assert assessment.execution_eligible is True


class TestBoundedResearchAllowed:
    def test_allowed_true_only_when_bounded_validated_and_no_long_history(self):
        assert bounded_research_allowed(bounded_validated=True, long_history_ready=False) is True

    def test_allowed_false_when_long_history_ready(self):
        assert bounded_research_allowed(bounded_validated=True, long_history_ready=True) is False


class TestHorizonFailClosed:
    def test_1095_is_fail_closed_on_current_futures_history(self):
        with pytest.raises(InsufficientCoverageError):
            resolve_horizon("GAZP", "15m", 1095, DATA_DIR)

    def test_365_and_shorter_remain_available(self):
        r365 = resolve_horizon("GAZP", "15m", 365, DATA_DIR)
        r90 = resolve_horizon("GAZP", "15m", 90, DATA_DIR)
        r180 = resolve_horizon("GAZP", "15m", 180, DATA_DIR)
        assert r365.actual_coverage_days >= 365
        assert r90.actual_coverage_days >= 365
        assert r180.actual_coverage_days >= 365


class TestCanonicalSeparation:
    def test_bounded_state_does_not_imply_full_qualification(self):
        assessment = classify_scope(bounded_validated=True, long_history_ready=False)
        assert assessment.scope != ResearchScope.FULLY_QUALIFIED
        assert assessment.scope != ResearchScope.EXECUTION_ELIGIBLE

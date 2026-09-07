"""Unit tests for core.convergence — convergence detection logic."""
from __future__ import annotations

import pytest
from core.convergence import (
    CONVERGENCE_VERSION,
    Status,
    ConvergenceReport,
    _count_repeated,
    _has_new_results,
    detect,
)


# ---------------------------------------------------------------------------
# _count_repeated
# ---------------------------------------------------------------------------
class TestCountRepeated:
    def test_empty_decisions_returns_none(self):
        assert _count_repeated([]) is None

    def test_single_decision_below_threshold(self):
        assert _count_repeated(["A"]) is None

    def test_multiple_distinct_below_threshold(self):
        assert _count_repeated(["A", "B", "C"], threshold=3) is None

    def test_exactly_at_threshold(self):
        result = _count_repeated(["A", "A", "A"], threshold=3)
        assert result is not None
        assert result["text"] == "A"
        assert result["count"] == 3

    def test_above_threshold(self):
        result = _count_repeated(["A"] * 5, threshold=3)
        assert result is not None
        assert result["count"] == 5

    def test_custom_threshold(self):
        assert _count_repeated(["X", "X"], threshold=3) is None
        result = _count_repeated(["X", "X"], threshold=2)
        assert result is not None
        assert result["text"] == "X"

    def test_picks_most_common(self):
        result = _count_repeated(["A", "A", "A", "B", "B"], threshold=3)
        assert result["text"] == "A"
        assert result["count"] == 3


# ---------------------------------------------------------------------------
# _has_new_results
# ---------------------------------------------------------------------------
class TestHasNewResults:
    def test_empty_returns_false(self):
        assert _has_new_results([]) is False

    def test_one_result_returns_true(self):
        assert _has_new_results([{"ok": True}]) is True

    def test_custom_min(self):
        assert _has_new_results([{"a": 1}], min_results=2) is False
        assert _has_new_results([{"a": 1}, {"b": 2}], min_results=2) is True


# ---------------------------------------------------------------------------
# detect()
# ---------------------------------------------------------------------------
class TestDetect:
    def test_empty_returns_unknown(self):
        report = detect([], [])
        assert report.status == Status.UNKNOWN
        assert "No decisions" in report.reason

    def test_stuck_detection(self):
        report = detect(
            ["spawned L1=n1 L2=n2 L3=n3"] * 4,
            [],
        )
        assert report.status == Status.STUCK
        assert "repeated 4 times" in report.reason

    def test_stuck_respects_threshold(self):
        report = detect(["A", "A"], [], stuck_threshold=3)
        # 2 decisions, not stuck (below threshold=3), no results → CONVERGED
        assert report.status == Status.CONVERGED

    def test_progress_with_results(self):
        report = detect(
            ["plan created", "plan refined"],
            [{"task": "t1", "status": "done"}],
        )
        assert report.status == Status.PROGRESS
        assert "1 results" in report.reason

    def test_converged_no_results(self):
        report = detect(
            ["plan created", "code written"],
            [],
        )
        assert report.status == Status.CONVERGED
        assert "convergence" in report.reason.lower()

    def test_unknown_only_one_decision_no_results(self):
        report = detect(["plan created"], [])
        assert report.status == Status.CONVERGED

    def test_details_dict_populated(self):
        report = detect(["A", "A", "A"], [{"x": 1}])
        assert "total_decisions" in report.details
        assert "total_results" in report.details
        assert report.details["total_decisions"] == 3

    def test_version_is_set(self):
        report = detect(["A"], [])
        assert report.version == CONVERGENCE_VERSION


# ---------------------------------------------------------------------------
# ConvergenceReport.to_dict
# ---------------------------------------------------------------------------
class TestConvergenceReportToDict:
    def test_to_dict_has_status_string(self):
        report = detect([], [])
        d = report.to_dict()
        assert isinstance(d["status"], str)
        assert d["status"] == "UNKNOWN"

    def test_to_dict_roundtrip(self):
        report = detect(["A", "A", "A"], [])
        d = report.to_dict()
        assert d["status"] == "STUCK"
        assert "reason" in d
        assert "details" in d

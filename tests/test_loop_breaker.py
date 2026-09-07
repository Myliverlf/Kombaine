"""Unit tests for core.loop_breaker — spawn decision logic."""
from __future__ import annotations

import pytest
from core.convergence import Status
from core.loop_breaker import LOOP_BREAKER_VERSION, SpawnDecision, should_spawn


# ---------------------------------------------------------------------------
# SpawnDecision dataclass
# ---------------------------------------------------------------------------
class TestSpawnDecision:
    def test_creation(self):
        sd = SpawnDecision(
            should_spawn=True,
            convergence_status=Status.PROGRESS,
            reason="making progress",
            recommendation="keep going",
        )
        assert sd.should_spawn is True
        assert sd.convergence_status == Status.PROGRESS
        assert sd.version == LOOP_BREAKER_VERSION

    def test_to_dict_converts_status_to_string(self):
        sd = SpawnDecision(
            should_spawn=False,
            convergence_status=Status.STUCK,
            reason="repeated",
            recommendation="stop",
        )
        d = sd.to_dict()
        assert isinstance(d["convergence_status"], str)
        assert d["convergence_status"] == "STUCK"
        assert d["should_spawn"] is False


# ---------------------------------------------------------------------------
# should_spawn()
# ---------------------------------------------------------------------------
class TestShouldSpawn:
    def test_first_cycle_unknown_spawns(self):
        """Empty history → UNKNOWN → should spawn (cautious first cycle)."""
        sd = should_spawn([], [])
        assert sd.should_spawn is True
        assert sd.convergence_status == Status.UNKNOWN
        assert "First cycle" in sd.recommendation

    def test_stuck_does_not_spawn(self):
        sd = should_spawn(
            ["spawned L1=n1 L2=n2 L3=n3"] * 4,
            [],
        )
        assert sd.should_spawn is False
        assert sd.convergence_status == Status.STUCK
        assert "Stop spawning" in sd.recommendation

    def test_converged_does_not_spawn(self):
        sd = should_spawn(
            ["plan created", "code written"],
            [],
        )
        # FIX(false-converged): decisions with zero results are UNKNOWN,
        # so the loop keeps probing cautiously instead of stopping.
        assert sd.should_spawn is True
        assert sd.convergence_status == Status.UNKNOWN

    def test_progress_spawns(self):
        sd = should_spawn(
            ["plan created"],
            [{"task": "t1", "status": "done"}],
        )
        assert sd.should_spawn is True
        assert sd.convergence_status == Status.PROGRESS
        assert "Continue" in sd.recommendation

    def test_stuck_below_threshold_spawns(self):
        """Two identical decisions (below default threshold=3) → not stuck → progress or unknown."""
        sd = should_spawn(
            ["same"] * 2,
            [{"task": "t1"}],
        )
        # Two decisions + one result → PROGRESS
        assert sd.should_spawn is True
        assert sd.convergence_status == Status.PROGRESS

    def test_custom_threshold(self):
        sd = should_spawn(
            ["X", "X"],
            [],
            stuck_threshold=2,
        )
        assert sd.should_spawn is False
        assert sd.convergence_status == Status.STUCK

    def test_details_forwarded(self):
        sd = should_spawn(["A", "A", "A"], [])
        assert "total_decisions" in sd.details

    def test_version_is_set(self):
        sd = should_spawn([], [])
        assert sd.version == LOOP_BREAKER_VERSION

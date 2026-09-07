"""Derived views completeness check — exhaustive validation of waitlist/signal_pool views.

Checks:
  1. waitlist excludes ALL non-candidate/non-waitlist statuses (active, rejected, etc.)
  2. signal_pool includes ONLY active_watchlist + active_signal_pool
  3. canonical_status maps to correct status for all 8 lifecycle statuses
  4. No dangling strategies exist without history events
  5. All 8 statuses are representable in ALL_STATUSES constant
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure project modules are importable
COMBINE_DIR = Path(__file__).resolve().parent.parent
CODE_DIR = COMBINE_DIR / "code"
sys.path.insert(0, str(CODE_DIR))

from strategy_registry import (  # noqa: E402
    ALL_STATUSES,
    STATUS_ACTIVE_SIGNAL_POOL,
    STATUS_ACTIVE_WATCHLIST,
    STATUS_CONFLICTED,
    STATUS_EXPIRED,
    STATUS_REJECTED,
    STATUS_REGISTRY_CANDIDATE,
    STATUS_ROTATED_OUT,
    STATUS_WAITLIST,
    StrategyRecord,
    StrategyRegistry,
)


# ─── Fixtures ───────────────────────────────────────────────────────

@pytest.fixture
def registry_all_statuses(tmp_path: Path) -> StrategyRegistry:
    """Registry with exactly one strategy per each of the 8 statuses."""
    registry_path = tmp_path / "views_all_statuses.json"
    registry = StrategyRegistry(path=registry_path)

    status_map = {
        "strat_candidate": STATUS_REGISTRY_CANDIDATE,
        "strat_waitlist": STATUS_WAITLIST,
        "strat_watchlist": STATUS_ACTIVE_WATCHLIST,
        "strat_pool": STATUS_ACTIVE_SIGNAL_POOL,
        "strat_rejected": STATUS_REJECTED,
        "strat_rotated": STATUS_ROTATED_OUT,
        "strat_conflicted": STATUS_CONFLICTED,
        "strat_expired": STATUS_EXPIRED,
    }

    for sid, status in status_map.items():
        registry.record_generation(
            strategy_id=sid,
            ticker="TEST",
            strategy="test_strat",
            source="generator",
            status=status,
            note=f"setup_{status}",
        )
        # Add a second event for history completeness
        if status != STATUS_REGISTRY_CANDIDATE:
            registry.transition(sid, status, reason="transition_test", note="history_entry")

    registry.save()
    return registry


@pytest.fixture
def registry_with_history(tmp_path: Path) -> StrategyRegistry:
    """Registry where every strategy has ≥1 history event."""
    registry_path = tmp_path / "history_registry.json"
    registry = StrategyRegistry(path=registry_path)

    # Strategy with long lifecycle
    registry.record_generation(
        strategy_id="S1_lifecycle",
        ticker="GAZP",
        strategy="mean_reversion",
        source="generator",
    )
    registry.transition("S1_lifecycle", STATUS_WAITLIST, reason="promoted")
    registry.transition("S1_lifecycle", STATUS_ACTIVE_WATCHLIST, reason="promoted_to_watchlist")
    registry.transition("S1_lifecycle", STATUS_REJECTED, reason="quality_gate")

    # Strategy with short lifecycle
    registry.record_generation(
        strategy_id="S2_short",
        ticker="SBER",
        strategy="bollinger",
        source="generator",
    )

    # Strategy rotated out
    registry.record_generation(
        strategy_id="S3_rotated",
        ticker="LKOH",
        strategy="vwap",
        source="generator",
    )
    registry.transition("S3_rotated", STATUS_ACTIVE_SIGNAL_POOL, reason="promoted")
    registry.transition("S3_rotated", STATUS_ROTATED_OUT, reason="replaced")

    registry.save()
    return registry


@pytest.fixture
def excluded_statuses() -> set[str]:
    """Statuses that should NOT appear in waitlist view."""
    return {STATUS_REJECTED, STATUS_ROTATED_OUT, STATUS_CONFLICTED, STATUS_EXPIRED,
            STATUS_ACTIVE_WATCHLIST, STATUS_ACTIVE_SIGNAL_POOL}


# ─── Tests ──────────────────────────────────────────────────────────

def test_waitlist_excludes_non_candidate_statuses(registry_all_statuses: StrategyRegistry) -> None:
    """export_legacy_waitlist() excludes active, rejected, rotated, conflicted, expired."""
    waitlist = registry_all_statuses.export_legacy_waitlist()
    candidates = waitlist.get("candidates", {})

    # Only registry/candidate and waitlist should appear
    expected_in_waitlist = {"strat_candidate", "strat_waitlist"}
    actual_ids = set(candidates.keys())
    assert actual_ids == expected_in_waitlist, (
        f"Waitlist contains unexpected IDs: {actual_ids - expected_in_waitlist}; "
        f"missing: {expected_in_waitlist - actual_ids}"
    )


def test_signal_pool_only_active_statuses(registry_all_statuses: StrategyRegistry) -> None:
    """export_legacy_signal_pool() includes ONLY active_watchlist + active_signal_pool."""
    pool = registry_all_statuses.export_legacy_signal_pool()
    strategies = pool.get("strategies", {})

    expected_in_pool = {"strat_watchlist", "strat_pool"}
    actual_ids = set(strategies.keys())
    assert actual_ids == expected_in_pool, (
        f"Signal pool contains unexpected IDs: {actual_ids - expected_in_pool}; "
        f"missing: {expected_in_pool - actual_ids}"
    )


def test_canonical_status_covers_all_8(registry_all_statuses: StrategyRegistry) -> None:
    """canonical_status() returns the correct status for every strategy."""
    expected = {
        "strat_candidate": STATUS_REGISTRY_CANDIDATE,
        "strat_waitlist": STATUS_WAITLIST,
        "strat_watchlist": STATUS_ACTIVE_WATCHLIST,
        "strat_pool": STATUS_ACTIVE_SIGNAL_POOL,
        "strat_rejected": STATUS_REJECTED,
        "strat_rotated": STATUS_ROTATED_OUT,
        "strat_conflicted": STATUS_CONFLICTED,
        "strat_expired": STATUS_EXPIRED,
    }

    for sid, expected_status in expected.items():
        actual = registry_all_statuses.canonical_status(sid)
        assert actual == expected_status, (
            f"canonical_status({sid})={actual}, expected {expected_status}"
        )

    # Unknown returns None
    assert registry_all_statuses.canonical_status("nonexistent") is None


def test_no_dangling_strategies_without_history(registry_with_history: StrategyRegistry) -> None:
    """Every strategy in the registry has at least one history event."""
    for record in registry_with_history.records():
        assert len(record.history) >= 1, (
            f"Strategy {record.strategy_id} has no history events "
            f"(status={record.status})"
        )


def test_all_statuses_in_constant() -> None:
    """ALL_STATUSES contains exactly 8 unique non-empty string statuses."""
    assert len(ALL_STATUSES) == 8
    for status in ALL_STATUSES:
        assert isinstance(status, str)
        assert len(status) > 0

    # Verify each expected status is present
    expected = {
        "registry/candidate", "waitlist", "active_watchlist",
        "active_signal_pool", "rejected", "rotated_out", "conflicted", "expired",
    }
    assert ALL_STATUSES == expected

"""Tests for StrategyRegistry status transitions and history append.

Covers gaps identified in analysis:
  - add_event() updates status and appends to history
  - updated_ts is monotonic (>= created_ts)
  - Key lifecycle transitions: candidate→waitlist→active_watchlist→active_signal_pool
  - Rejection/rotated_out transitions
  - Persist → load roundtrip preserves full history trail
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

# Ensure project modules are importable
COMBINE_DIR = Path(__file__).resolve().parent.parent
CODE_DIR = COMBINE_DIR / "code"
sys.path.insert(0, str(CODE_DIR))

from strategy_registry import (  # noqa: E402
    STATUS_ACTIVE_SIGNAL_POOL,
    STATUS_ACTIVE_WATCHLIST,
    STATUS_REJECTED,
    STATUS_REGISTRY_CANDIDATE,
    STATUS_ROTATED_OUT,
    STATUS_WAITLIST,
    StrategyRecord,
    StrategyRegistry,
)


@pytest.fixture
def fresh_record() -> StrategyRecord:
    """A brand-new record with no history, status=registry/candidate."""
    return StrategyRecord(
        strategy_id="TEST__fresh",
        ticker="TEST",
        strategy="fresh_strategy",
        params={"window": 10},
        metrics={"pnl": 0.0},
        status=STATUS_REGISTRY_CANDIDATE,
        created_ts=1700000000.0,
        updated_ts=1700000000.0,
    )


@pytest.fixture
def transition_registry(tmp_path: Path) -> StrategyRegistry:
    """Registry with one record ready for transitions, persisted on tmp_path."""
    registry_path = tmp_path / "strategy_registry.json"
    registry = StrategyRegistry(path=registry_path)
    registry.record_generation(
        strategy_id="TRANS__test",
        ticker="TEST",
        strategy="transition_test",
        params={"lookback": 5},
        metrics={"pnl": 100.0},
        source="generator",
    )
    registry.save()
    return registry


@pytest.fixture
def full_lifecycle_registry(tmp_path: Path) -> StrategyRegistry:
    """Registry to exercise the full lifecycle: candidate → waitlist → watchlist → pool."""
    registry_path = tmp_path / "strategy_registry.json"
    return StrategyRegistry(path=registry_path)


def test_add_event_updates_status_and_appends_history(fresh_record: StrategyRecord) -> None:
    """add_event() sets status and appends a new event to history."""
    assert fresh_record.status == STATUS_REGISTRY_CANDIDATE
    assert len(fresh_record.history) == 0

    # Transition to waitlist
    fresh_record.add_event(
        status=STATUS_WAITLIST,
        reason="promotion",
        note="moved to waitlist",
        payload={"rank": 0.7},
    )

    assert fresh_record.status == STATUS_WAITLIST
    assert len(fresh_record.history) == 1
    assert fresh_record.history[0].status == STATUS_WAITLIST
    assert fresh_record.history[0].reason == "promotion"
    assert fresh_record.history[0].note == "moved to waitlist"
    assert fresh_record.history[0].payload == {"rank": 0.7}


def test_updated_ts_monotonic_after_add_event(fresh_record: StrategyRecord) -> None:
    """updated_ts is always >= created_ts after each add_event()."""
    base_ts = fresh_record.created_ts

    for _ in range(5):
        time.sleep(0.01)
        fresh_record.add_event(status=STATUS_WAITLIST, reason="test")

    assert fresh_record.updated_ts >= base_ts
    # History timestamps should be non-decreasing
    for i in range(1, len(fresh_record.history)):
        assert fresh_record.history[i].ts >= fresh_record.history[i - 1].ts


def test_candidate_to_watchlist_transition(tmp_path: Path) -> None:
    """Registry.transition() follows candidate → waitlist → active_watchlist path."""
    registry_path = tmp_path / "registry.json"
    registry = StrategyRegistry(path=registry_path)

    registry.record_generation(
        strategy_id="CAND__1",
        ticker="GAZP",
        strategy="test_strat",
        source="generator",
    )

    # candidate → waitlist
    rec = registry.transition("CAND__1", STATUS_WAITLIST, reason="promoted")
    assert rec.status == STATUS_WAITLIST
    assert rec.candidate_stream is True  # waitlist stays in candidate_stream

    # waitlist → active_watchlist
    rec = registry.mark_active_watchlist("CAND__1", slot=0, score=0.8)
    assert rec.status == STATUS_ACTIVE_WATCHLIST
    assert rec.watchlist_slot == 0
    assert rec.active_rank == 0.8
    assert rec.candidate_stream is False  # active strategies leave candidate_stream


def test_watchlist_to_signal_pool_transition(tmp_path: Path) -> None:
    """Registry tracks active_watchlist → active_signal_pool promotion."""
    registry_path = tmp_path / "registry.json"
    registry = StrategyRegistry(path=registry_path)

    registry.record_generation(
        strategy_id="POOL__1",
        ticker="LKOH",
        strategy="vwap_reversion",
        source="generator",
    )
    registry.mark_active_watchlist("POOL__1", slot=2, score=0.75)

    rec = registry.mark_active_signal_pool("POOL__1", slot=1, score=0.92)
    assert rec.status == STATUS_ACTIVE_SIGNAL_POOL
    assert rec.signal_pool_slot == 1
    assert rec.active_rank == 0.92
    assert rec.candidate_stream is False


def test_watchlist_to_rejected_transition(tmp_path: Path) -> None:
    """Rejection from watchlist sets status and keeps candidate_stream True."""
    registry_path = tmp_path / "registry.json"
    registry = StrategyRegistry(path=registry_path)

    registry.record_generation(
        strategy_id="REJ__1",
        ticker="SBER",
        strategy="bollinger",
        source="generator",
    )
    registry.mark_active_watchlist("REJ__1", slot=0, score=0.6)
    rec = registry.mark_rejected("REJ__1", reason="quality_gate_fail")

    assert rec.status == STATUS_REJECTED
    assert rec.candidate_stream is True  # rejected returns to candidate_stream


def test_rotated_out_transition(tmp_path: Path) -> None:
    """Rotated-out transition sets status correctly."""
    registry_path = tmp_path / "registry.json"
    registry = StrategyRegistry(path=registry_path)

    registry.record_generation(
        strategy_id="ROT__1",
        ticker="LKOH",
        strategy="trend",
        source="generator",
    )
    registry.mark_active_signal_pool("ROT__1", slot=0, score=0.9)
    rec = registry.mark_rotated_out("ROT__1", reason="replaced_by_better")

    assert rec.status == STATUS_ROTATED_OUT
    assert rec.candidate_stream is True  # rotated_out returns to candidate_stream


def test_history_grows_through_lifecycle(full_lifecycle_registry: StrategyRegistry) -> None:
    """Full lifecycle: history accumulates at each state transition."""
    registry = full_lifecycle_registry

    registry.record_generation(
        strategy_id="LIFE__1",
        ticker="TEST",
        strategy="lifecycle_test",
        source="generator",
    )

    # Each step adds an event to history
    registry.transition("LIFE__1", STATUS_WAITLIST, reason="step1")
    registry.mark_active_watchlist("LIFE__1", slot=0, score=0.8)
    registry.mark_active_signal_pool("LIFE__1", slot=0, score=0.9)
    registry.mark_rejected("LIFE__1", reason="final_step")

    rec = registry.get("LIFE__1")
    assert rec is not None

    # history: record_generation event + 4 transitions = 5 events total
    assert len(rec.history) == 5

    # Verify status trail
    statuses_in_history = [e.status for e in rec.history]
    assert statuses_in_history == [
        STATUS_REGISTRY_CANDIDATE,  # from record_generation
        STATUS_WAITLIST,
        STATUS_ACTIVE_WATCHLIST,
        STATUS_ACTIVE_SIGNAL_POOL,
        STATUS_REJECTED,
    ]


def test_persist_load_preserves_history(tmp_path: Path) -> None:
    """Save → load roundtrip preserves the complete history trail."""
    registry_path = tmp_path / "registry.json"
    registry = StrategyRegistry(path=registry_path)

    registry.record_generation(
        strategy_id="PERSIST__1",
        ticker="TEST",
        strategy="persist_test",
        source="generator",
    )
    registry.mark_active_watchlist("PERSIST__1", slot=0, score=0.7)

    registry.save()

    # Reload from disk
    registry2 = StrategyRegistry(path=registry_path)
    rec = registry2.get("PERSIST__1")
    assert rec is not None
    assert rec.status == STATUS_ACTIVE_WATCHLIST
    assert len(rec.history) == 2
    assert rec.history[0].status == STATUS_REGISTRY_CANDIDATE
    assert rec.history[1].status == STATUS_ACTIVE_WATCHLIST

    # Record roundtrip
    data = rec.to_dict()
    restored = StrategyRecord.from_dict(data)
    assert len(restored.history) == 2
    assert restored.history[0].status == STATUS_REGISTRY_CANDIDATE
    assert restored.history[1].status == STATUS_ACTIVE_WATCHLIST


def test_transition_unknown_strategy_raises(tmp_path: Path) -> None:
    """Transition on unknown strategy_id raises KeyError."""
    registry = StrategyRegistry(path=tmp_path / "registry.json")
    with pytest.raises(KeyError, match="unknown strategy_id"):
        registry.transition("NONEXISTENT", STATUS_WAITLIST)

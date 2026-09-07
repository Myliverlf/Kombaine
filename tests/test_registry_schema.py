"""Tests for StrategyRegistry schema: roundtrip, defaults, status completeness.

Covers gaps identified in analysis:
  - StrategyRecord.from_dict(record.to_dict()) roundtrip
  - Default values for missing optional fields
  - ALL_STATUSES constant is complete (8 unique statuses)
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
    STATUS_REGISTRY_CANDIDATE,
    STATUS_REJECTED,
    StrategyEvent,
    StrategyRecord,
)


@pytest.fixture
def sample_record() -> StrategyRecord:
    """Create a fully-populated StrategyRecord with 3 history events."""
    record = StrategyRecord(
        strategy_id="GAZP__mean_reversion",
        ticker="GAZP",
        strategy="mean_reversion",
        params={"lookback": 20, "threshold": 0.5},
        metrics={"pnl": 1500.0, "sharpe": 0.72, "win_rate": 55.0},
        status=STATUS_ACTIVE_WATCHLIST,
        created_ts=1700000000.0,
        updated_ts=1700001000.0,
        source="generator",
        generation_batch_id="batch-42",
        portfolio_context={"go_rub": 5000.0},
        quality_gate={"ttl_days": 7, "retests": 0},
        candidate_stream=False,
        active_rank=0.85,
        watchlist_slot=2,
        signal_pool_slot=None,
        expires_ts=1700600000.0,
    )
    record.history = [
        StrategyEvent(ts=1700000000.0, status="registry/candidate", reason="generated", note="init"),
        StrategyEvent(ts=1700000500.0, status="waitlist", reason="promoted", note="waitlist"),
        StrategyEvent(ts=1700001000.0, status=STATUS_ACTIVE_WATCHLIST, reason="promoted_to_watchlist", note="active"),
    ]
    return record


@pytest.fixture
def minimal_record_dict() -> dict:
    """Dict with only the required fields — no optional fields present."""
    return {
        "strategy_id": "SBER__bollinger",
        "ticker": "SBER",
        "strategy": "bollinger",
    }


@pytest.fixture
def record_with_events(sample_record: StrategyRecord) -> list[dict]:
    """History events as dicts (for serialization tests)."""
    return [e.__dict__ for e in sample_record.history]


def test_record_roundtrip(sample_record: StrategyRecord) -> None:
    """to_dict → from_dict restores all fields including history."""
    data = sample_record.to_dict()

    # Verify structure before roundtrip
    assert data["strategy_id"] == "GAZP__mean_reversion"
    assert data["status"] == STATUS_ACTIVE_WATCHLIST
    assert len(data["history"]) == 3
    assert data["history"][0]["status"] == "registry/candidate"
    assert data["watchlist_slot"] == 2
    assert data["signal_pool_slot"] is None
    assert data["expires_ts"] == 1700600000.0
    assert data["params"] == {"lookback": 20, "threshold": 0.5}
    assert data["metrics"]["pnl"] == 1500.0

    # Roundtrip
    restored = StrategyRecord.from_dict(data)

    assert restored.strategy_id == sample_record.strategy_id
    assert restored.ticker == sample_record.ticker
    assert restored.strategy == sample_record.strategy
    assert restored.params == sample_record.params
    assert restored.metrics == sample_record.metrics
    assert restored.status == sample_record.status
    assert restored.created_ts == sample_record.created_ts
    assert restored.updated_ts == sample_record.updated_ts
    assert restored.source == sample_record.source
    assert restored.generation_batch_id == sample_record.generation_batch_id
    assert restored.portfolio_context == sample_record.portfolio_context
    assert restored.quality_gate == sample_record.quality_gate
    assert restored.candidate_stream == sample_record.candidate_stream
    assert restored.active_rank == sample_record.active_rank
    assert restored.watchlist_slot == sample_record.watchlist_slot
    assert restored.signal_pool_slot == sample_record.signal_pool_slot
    assert restored.expires_ts == sample_record.expires_ts

    # History roundtrip
    assert len(restored.history) == 3
    for orig, rest in zip(sample_record.history, restored.history):
        assert rest.ts == orig.ts
        assert rest.status == orig.status
        assert rest.reason == orig.reason
        assert rest.note == orig.note
        assert rest.payload == orig.payload


def test_record_from_dict_missing_optional(minimal_record_dict: dict) -> None:
    """from_dict handles missing optional fields with correct defaults."""
    record = StrategyRecord.from_dict(minimal_record_dict)

    assert record.strategy_id == "SBER__bollinger"
    assert record.ticker == "SBER"
    assert record.strategy == "bollinger"

    # Optional fields get sensible defaults
    assert record.params == {}
    assert record.metrics == {}
    assert record.portfolio_context == {}
    assert record.quality_gate == {}
    assert record.history == []
    assert record.candidate_stream is True
    assert record.active_rank == 0.0
    assert record.watchlist_slot is None
    assert record.signal_pool_slot is None
    assert record.expires_ts is None


def test_all_statuses_constant_is_complete() -> None:
    """ALL_STATUSES contains exactly 8 unique statuses covering the full lifecycle."""
    expected_statuses = {
        "registry/candidate",
        "waitlist",
        "active_watchlist",
        "active_signal_pool",
        "rejected",
        "rotated_out",
        "conflicted",
        "expired",
    }

    assert ALL_STATUSES == expected_statuses
    assert len(ALL_STATUSES) == 8

    # Each status is a non-empty string
    for status in ALL_STATUSES:
        assert isinstance(status, str)
        assert len(status) > 0

    # No duplicates (set guarantees this, but also check list form)
    status_list = list(ALL_STATUSES)
    assert len(status_list) == len(set(status_list))

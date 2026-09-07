"""Tests for StrategyRegistry derived views and canonical status.

Covers gaps identified in analysis:
  - export_legacy_waitlist() excludes rejected
  - export_legacy_signal_pool() includes only active statuses
  - canonical_status() returns authoritative status
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
    STATUS_ACTIVE_SIGNAL_POOL,
    STATUS_ACTIVE_WATCHLIST,
    STATUS_CONFLICTED,
    STATUS_EXPIRED,
    STATUS_REJECTED,
    STATUS_REGISTRY_CANDIDATE,
    STATUS_ROTATED_OUT,
    STATUS_WAITLIST,
    StrategyRegistry,
)


@pytest.fixture
def registry_with_statuses(tmp_path: Path) -> StrategyRegistry:
    """Registry with 4 strategies in different statuses for view testing."""
    registry_path = tmp_path / "strategy_registry.json"
    registry = StrategyRegistry(path=registry_path)

    # Strategy 1: active_watchlist
    r1 = registry.record_generation(
        strategy_id="GAZP__mean_rev",
        ticker="GAZP",
        strategy="mean_reversion",
        params={"lookback": 20},
        metrics={"pnl": 1500.0},
        source="generator",
    )
    registry.mark_active_watchlist("GAZP__mean_rev", slot=1, score=0.85)

    # Strategy 2: active_signal_pool
    r2 = registry.record_generation(
        strategy_id="LKOH__vwap",
        ticker="LKOH",
        strategy="vwap_reversion",
        params={"lookback": 30},
        metrics={"pnl": 3000.0},
        source="generator",
    )
    registry.mark_active_signal_pool("LKOH__vwap", slot=0, score=0.92)

    # Strategy 3: rejected
    r3 = registry.record_generation(
        strategy_id="SBER__bollinger",
        ticker="SBER",
        strategy="bollinger",
        params={"period": 14},
        metrics={"pnl": -500.0},
        source="generator",
    )
    registry.mark_rejected("SBER__bollinger", reason="quality_gate")

    # Strategy 4: registry/candidate (in candidate_stream)
    registry.record_generation(
        strategy_id="Si__trend",
        ticker="Si",
        strategy="trend_following",
        params={},
        metrics={},
        source="generator",
        status=STATUS_REGISTRY_CANDIDATE,
    )

    registry.save()
    return registry


@pytest.fixture
def registry_with_all_statuses(tmp_path: Path) -> StrategyRegistry:
    """Registry with one strategy per status for canonical_status exhaustiveness."""
    registry_path = tmp_path / "registry_all_statuses.json"
    registry = StrategyRegistry(path=registry_path)

    statuses_map = {
        "strat_candidate": STATUS_REGISTRY_CANDIDATE,
        "strat_waitlist": STATUS_WAITLIST,
        "strat_watchlist": STATUS_ACTIVE_WATCHLIST,
        "strat_pool": STATUS_ACTIVE_SIGNAL_POOL,
        "strat_rejected": STATUS_REJECTED,
        "strat_rotated": STATUS_ROTATED_OUT,
        "strat_conflicted": STATUS_CONFLICTED,
        "strat_expired": STATUS_EXPIRED,
    }

    for sid, status in statuses_map.items():
        registry.record_generation(
            strategy_id=sid,
            ticker="TEST",
            strategy="test_strat",
            source="generator",
            status=status,
            note=f"set_to_{status}",
        )

    registry.save()
    return registry


@pytest.fixture
def empty_registry(tmp_path: Path) -> StrategyRegistry:
    """Empty registry for negative tests."""
    return StrategyRegistry(path=tmp_path / "empty.json")


def test_waitlist_excludes_rejected(registry_with_statuses: StrategyRegistry) -> None:
    """export_legacy_waitlist() does not include rejected or active strategies."""
    waitlist = registry_with_statuses.export_legacy_waitlist()
    candidates = waitlist["candidates"]

    # rejected is excluded from waitlist
    assert "SBER__bollinger" not in candidates, "rejected should be excluded"

    # active statuses are NOT in candidate_stream → not in waitlist
    assert "LKOH__vwap" not in candidates, "active_signal_pool should not be in waitlist"
    assert "GAZP__mean_rev" not in candidates, "active_watchlist should not be in waitlist"

    # registry/candidate IS in waitlist
    assert "Si__trend" in candidates

    # Verify structure of a waitlist entry
    entry = candidates["Si__trend"]
    assert entry["ticker"] == "Si"
    assert entry["strategy"] == "trend_following"
    assert entry["status"] == STATUS_REGISTRY_CANDIDATE
    assert "rank_score" in entry
    assert "added_ts" in entry


def test_signal_pool_only_active_statuses(registry_with_statuses: StrategyRegistry) -> None:
    """export_legacy_signal_pool() includes only active_watchlist + active_signal_pool."""
    pool = registry_with_statuses.export_legacy_signal_pool()
    strategies = pool["strategies"]

    # Only active statuses present
    assert "GAZP__mean_rev" in strategies
    assert "LKOH__vwap" in strategies

    # Rejected, candidate, waitlist etc. excluded
    assert "SBER__bollinger" not in strategies
    assert "Si__trend" not in strategies

    # Verify structure
    entry = strategies["LKOH__vwap"]
    assert entry["ticker"] == "LKOH"
    assert entry["status"] == STATUS_ACTIVE_SIGNAL_POOL
    assert "rank_score" in entry
    assert "go_rub" in entry


def test_canonical_status_matches_record(registry_with_all_statuses: StrategyRegistry) -> None:
    """canonical_status() returns the same status as record.status for all statuses."""
    for record in registry_with_all_statuses.records():
        canonical = registry_with_all_statuses.canonical_status(record.strategy_id)
        assert canonical == record.status, (
            f"canonical_status({record.strategy_id})={canonical} "
            f"!= record.status={record.status}"
        )


def test_canonical_status_unknown_strategy(empty_registry: StrategyRegistry) -> None:
    """canonical_status() returns None for unknown strategy_id."""
    result = empty_registry.canonical_status("nonexistent_strategy")
    assert result is None

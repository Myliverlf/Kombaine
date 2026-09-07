"""Tests for StrategyRegistry export_legacy_state_files disk-write and derived views integrity.

Covers gaps identified in analysis:
  - export_legacy_state_files() physically writes files on disk
  - Files contain valid JSON with expected structure
  - Repeated export is bytes-identical (idempotent write)
  - Derived views (waitlist, signal_pool) are consistent with registry state
"""
from __future__ import annotations

import json
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
    STATUS_REJECTED,
    STATUS_REGISTRY_CANDIDATE,
    STATUS_ROTATED_OUT,
    StrategyRegistry,
)


@pytest.fixture
def export_dir(tmp_path: Path) -> Path:
    """Isolated directory for export tests."""
    d = tmp_path / "export_state"
    d.mkdir()
    return d


@pytest.fixture
def mixed_registry(export_dir: Path) -> StrategyRegistry:
    """Registry with strategies in various statuses for export validation."""
    registry_path = export_dir / "strategy_registry.json"
    registry = StrategyRegistry(path=registry_path)

    # active_watchlist strategy
    registry.record_generation(
        strategy_id="EXP__watchlist",
        ticker="GAZP",
        strategy="mean_reversion",
        params={"lookback": 20},
        metrics={"pnl": 1500.0},
        source="generator",
    )
    registry.mark_active_watchlist("EXP__watchlist", slot=0, score=0.85)

    # active_signal_pool strategy
    registry.record_generation(
        strategy_id="EXP__pool",
        ticker="LKOH",
        strategy="vwap_reversion",
        params={"lookback": 30},
        metrics={"pnl": 3000.0},
        source="generator",
    )
    registry.mark_active_signal_pool("EXP__pool", slot=1, score=0.92)

    # rejected strategy (should be excluded from waitlist)
    registry.record_generation(
        strategy_id="EXP__rejected",
        ticker="SBER",
        strategy="bollinger",
        params={"period": 14},
        metrics={"pnl": -500.0},
        source="generator",
    )
    registry.mark_rejected("EXP__rejected", reason="quality_gate")

    # candidate strategy (should appear in waitlist)
    registry.record_generation(
        strategy_id="EXP__candidate",
        ticker="Si",
        strategy="trend_follow",
        params={},
        metrics={},
        source="generator",
    )

    # rotated_out strategy (in candidate_stream, excluded from waitlist by non-active check)
    registry.record_generation(
        strategy_id="EXP__rotated",
        ticker="LKOH",
        strategy="old_trend",
        params={},
        metrics={"pnl": 500.0},
        source="generator",
    )
    registry.mark_active_watchlist("EXP__rotated", slot=2, score=0.6)
    registry.mark_rotated_out("EXP__rotated", reason="replaced")

    registry.save()
    return registry


@pytest.fixture
def empty_registry(export_dir: Path) -> StrategyRegistry:
    """Empty registry for minimal export tests."""
    registry_path = export_dir / "empty_registry.json"
    return StrategyRegistry(path=registry_path)


def test_export_creates_files_on_disk(export_dir: Path, mixed_registry: StrategyRegistry) -> None:
    """export_legacy_state_files() physically creates waitlist.json and signal_pool.json."""
    waitlist_path = export_dir / "waitlist.json"
    signal_pool_path = export_dir / "signal_pool.json"

    # Files should not exist yet
    assert not waitlist_path.exists()
    assert not signal_pool_path.exists()

    mixed_registry.export_legacy_state_files(waitlist_path, signal_pool_path)

    assert waitlist_path.exists(), "waitlist.json not created"
    assert signal_pool_path.exists(), "signal_pool.json not created"
    assert waitlist_path.stat().st_size > 0, "waitlist.json is empty"
    assert signal_pool_path.stat().st_size > 0, "signal_pool.json is empty"


def test_waitlist_file_structure(export_dir: Path, mixed_registry: StrategyRegistry) -> None:
    """waitlist.json contains 'candidates' key with valid entries."""
    waitlist_path = export_dir / "waitlist.json"
    signal_pool_path = export_dir / "signal_pool.json"

    mixed_registry.export_legacy_state_files(waitlist_path, signal_pool_path)

    waitlist = json.loads(waitlist_path.read_text())
    assert "candidates" in waitlist

    candidates = waitlist["candidates"]

    # Candidate strategy should be present
    assert "EXP__candidate" in candidates
    entry = candidates["EXP__candidate"]
    assert entry["ticker"] == "Si"
    assert entry["strategy"] == "trend_follow"
    assert "rank_score" in entry
    assert "added_ts" in entry
    assert "ttl_days" in entry

    # Rejected strategy excluded from waitlist
    assert "EXP__rejected" not in candidates

    # Active strategies are NOT in candidate_stream → not in waitlist
    assert "EXP__watchlist" not in candidates
    assert "EXP__pool" not in candidates


def test_signal_pool_file_structure(export_dir: Path, mixed_registry: StrategyRegistry) -> None:
    """signal_pool.json contains 'strategies' with only active statuses."""
    waitlist_path = export_dir / "waitlist.json"
    signal_pool_path = export_dir / "signal_pool.json"

    mixed_registry.export_legacy_state_files(waitlist_path, signal_pool_path)

    pool = json.loads(signal_pool_path.read_text())
    assert "strategies" in pool
    assert "last_rotation_ts" in pool

    strategies = pool["strategies"]

    # Only active_watchlist and active_signal_pool present
    assert "EXP__watchlist" in strategies
    assert "EXP__pool" in strategies

    # Rejected, candidate, rotated excluded
    assert "EXP__rejected" not in strategies
    assert "EXP__candidate" not in strategies
    assert "EXP__rotated" not in strategies

    # Verify structure
    entry = strategies["EXP__watchlist"]
    assert entry["ticker"] == "GAZP"
    assert entry["status"] == STATUS_ACTIVE_WATCHLIST
    assert "rank_score" in entry
    assert "go_rub" in entry
    assert "added_ts" in entry
    assert "last_signal_ts" in entry


def test_export_idempotent_bytes_equal(export_dir: Path, mixed_registry: StrategyRegistry) -> None:
    """Two consecutive exports produce byte-identical files."""
    waitlist_path = export_dir / "waitlist.json"
    signal_pool_path = export_dir / "signal_pool.json"

    mixed_registry.export_legacy_state_files(waitlist_path, signal_pool_path)
    first_wl = waitlist_path.read_bytes()
    first_sp = signal_pool_path.read_bytes()

    mixed_registry.export_legacy_state_files(waitlist_path, signal_pool_path)
    second_wl = waitlist_path.read_bytes()
    second_sp = signal_pool_path.read_bytes()

    assert first_wl == second_wl, "waitlist.json differs between calls"
    assert first_sp == second_sp, "signal_pool.json differs between calls"


def test_empty_export_produces_empty_views(empty_registry: StrategyRegistry, export_dir: Path) -> None:
    """Exporting empty registry produces empty candidates/strategies dicts."""
    waitlist_path = export_dir / "waitlist.json"
    signal_pool_path = export_dir / "signal_pool.json"

    empty_registry.export_legacy_state_files(waitlist_path, signal_pool_path)

    waitlist = json.loads(waitlist_path.read_text())
    pool = json.loads(signal_pool_path.read_text())

    assert waitlist == {"candidates": {}}
    assert pool["strategies"] == {}


def test_export_roundtrip_json_valid(export_dir: Path, mixed_registry: StrategyRegistry) -> None:
    """Both exported files are valid JSON parseable by json.loads()."""
    waitlist_path = export_dir / "waitlist.json"
    signal_pool_path = export_dir / "signal_pool.json"

    mixed_registry.export_legacy_state_files(waitlist_path, signal_pool_path)

    for path in [waitlist_path, signal_pool_path]:
        content = path.read_text()
        parsed = json.loads(content)
        assert isinstance(parsed, dict), f"{path.name} did not parse to dict"

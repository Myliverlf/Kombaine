"""Tests for init/export/sync idempotency of StrategyRegistry.

Covers gaps identified in analysis:
  - init_registry(force=False) does not overwrite existing registry
  - export_legacy_state_files() produces identical files on repeated calls
  - sync_from_legacy_files() does not duplicate records on repeated calls
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure project modules are importable
COMBINE_DIR = Path(__file__).resolve().parent.parent
CODE_DIR = COMBINE_DIR / "code"
sys.path.insert(0, str(CODE_DIR))

from strategy_registry import (  # noqa: E402
    StrategyRegistry,
)


@pytest.fixture
def isolated_state_dir(tmp_path: Path) -> Path:
    """Create an isolated state directory with a pre-populated registry."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    return state_dir


@pytest.fixture
def populated_registry(isolated_state_dir: Path) -> StrategyRegistry:
    """Registry with 2 strategies, saved to isolated state dir."""
    registry_path = isolated_state_dir / "strategy_registry.json"
    registry = StrategyRegistry(path=registry_path)

    registry.record_generation(
        strategy_id="GAZP__mean_rev",
        ticker="GAZP",
        strategy="mean_reversion",
        params={"lookback": 20},
        metrics={"pnl": 1500.0},
        source="generator",
        note="test_record_1",
    )
    registry.record_generation(
        strategy_id="LKOH__vwap",
        ticker="LKOH",
        strategy="vwap_reversion",
        params={"lookback": 30},
        metrics={"pnl": 3000.0},
        source="generator",
        note="test_record_2",
    )
    registry.save()
    return registry


@pytest.fixture
def legacy_files_dir(tmp_path: Path) -> Path:
    """Create legacy signal_pool and waitlist files for sync tests."""
    state_dir = tmp_path / "legacy_state"
    state_dir.mkdir()

    waitlist = {
        "candidates": {
            "legacy_wait_1": {
                "ticker": "SBER",
                "strategy": "bollinger",
                "params": {"period": 14},
                "metrics": {"pnl": 800.0},
                "rank_score": 1200.0,
                "ttl_days": 7,
                "retests": 0,
                "status": "waitlist",
            },
        }
    }
    signal_pool = {
        "strategies": {
            "legacy_pool_1": {
                "ticker": "GAZP",
                "strategy": "trend_follow",
                "params": {},
                "metrics": {"pnl": 2200.0},
                "rank_score": 2500.0,
                "status": "active",
            },
        },
        "last_rotation_ts": 0.0,
    }

    (state_dir / "waitlist.json").write_text(json.dumps(waitlist, indent=2))
    (state_dir / "signal_pool.json").write_text(json.dumps(signal_pool, indent=2))
    return state_dir


def test_init_no_overwrite(isolated_state_dir: Path) -> None:
    """init_registry(force=False) does not overwrite existing registry."""
    import importlib
    import init_strategy_registry

    registry_path = isolated_state_dir / "strategy_registry.json"
    registry = StrategyRegistry(path=registry_path)

    # Seed with 2 records
    registry.record_generation(
        strategy_id="existing_1", ticker="TEST", strategy="strat_a",
        source="test", note="existing",
    )
    registry.record_generation(
        strategy_id="existing_2", ticker="TEST", strategy="strat_b",
        source="test", note="existing",
    )
    registry.save()

    original_count = len(registry.records())

    # Monkeypatch module-level globals to use our isolated dir
    with patch.object(init_strategy_registry, "_REGISTRY_PATH", registry_path), \
         patch.object(init_strategy_registry, "_STATE_DIR", isolated_state_dir), \
         patch.object(init_strategy_registry, "_SIGNAL_POOL_PATH", isolated_state_dir / "signal_pool.json"), \
         patch.object(init_strategy_registry, "_PORTFOLIO_PATH", isolated_state_dir / "portfolio.json"):
        result = init_strategy_registry.init_registry(force=False)

    # Registry should NOT be overwritten — same count
    registry_after = StrategyRegistry(path=registry_path)
    assert len(registry_after.records()) == original_count

    # Result should reflect the existing data
    assert len(result.get("strategies", {})) == original_count


def test_export_idempotent(populated_registry: StrategyRegistry, isolated_state_dir: Path) -> None:
    """export_legacy_state_files() produces identical files on repeated calls."""
    waitlist_path = isolated_state_dir / "waitlist.json"
    signal_pool_path = isolated_state_dir / "signal_pool.json"

    # Export twice
    populated_registry.export_legacy_state_files(waitlist_path, signal_pool_path)
    first_waitlist = waitlist_path.read_bytes()
    first_pool = signal_pool_path.read_bytes()

    populated_registry.export_legacy_state_files(waitlist_path, signal_pool_path)
    second_waitlist = waitlist_path.read_bytes()
    second_pool = signal_pool_path.read_bytes()

    assert first_waitlist == second_waitlist, "waitlist files differ between calls"
    assert first_pool == second_pool, "signal_pool files differ between calls"

    # Verify content is valid JSON and non-empty
    wl = json.loads(first_waitlist)
    sp = json.loads(first_pool)
    assert "candidates" in wl
    assert "strategies" in sp


def test_sync_no_duplicates(legacy_files_dir: Path) -> None:
    """sync_from_legacy_files() does not duplicate records on repeated calls."""
    registry_path = legacy_files_dir / "strategy_registry.json"
    registry = StrategyRegistry(path=registry_path)

    waitlist_path = legacy_files_dir / "waitlist.json"
    signal_pool_path = legacy_files_dir / "signal_pool.json"

    # First sync
    imported_1 = registry.sync_from_legacy_files(waitlist_path, signal_pool_path)
    registry.save()
    count_after_first = len(registry.records())

    assert imported_1 >= 1, "first sync should import at least 1 record"

    # Second sync — should import 0 (already in registry)
    imported_2 = registry.sync_from_legacy_files(waitlist_path, signal_pool_path)
    count_after_second = len(registry.records())

    assert imported_2 == 0, f"second sync should import 0, got {imported_2}"
    assert count_after_second == count_after_first, (
        f"record count changed: {count_after_first} → {count_after_second}"
    )

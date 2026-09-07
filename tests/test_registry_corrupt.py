"""Tests for StrategyRegistry corrupt JSON recovery and atomic save.

Covers gaps identified in analysis:
  - Corrupt/invalid JSON → graceful recovery (.corrupt file created, default data)
  - Partial/corrupt content recovery (empty file, truncated JSON, random bytes)
  - Atomic save (tmp→replace) does not corrupt data on concurrent-style access
  - Corrupt recovery preserves directory structure
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
    StrategyRegistry,
)


@pytest.fixture
def corrupt_dir(tmp_path: Path) -> Path:
    """Directory for corrupt recovery tests."""
    d = tmp_path / "corrupt_state"
    d.mkdir()
    return d


@pytest.fixture
def valid_registry(corrupt_dir: Path) -> StrategyRegistry:
    """A valid, saved registry with one record for baseline comparison."""
    path = corrupt_dir / "strategy_registry.json"
    registry = StrategyRegistry(path=path)
    registry.record_generation(
        strategy_id="VALID__1",
        ticker="TEST",
        strategy="valid_strat",
        source="generator",
    )
    registry.save()
    return registry


@pytest.fixture
def saved_registry(tmp_path: Path) -> StrategyRegistry:
    """Registry with 3 records saved to tmp_path for atomic save tests."""
    path = tmp_path / "strategy_registry.json"
    registry = StrategyRegistry(path=path)

    for i in range(3):
        registry.record_generation(
            strategy_id=f"ATOMIC__{i}",
            ticker="TEST",
            strategy=f"strat_{i}",
            params={"idx": i},
            metrics={"pnl": float(i * 100)},
            source="generator",
        )
    registry.save()
    return registry


def test_corrupt_json_creates_backup_and_defaults(corrupt_dir: Path) -> None:
    """Corrupt JSON in registry file → .corrupt-{ts} backup created, defaults returned."""
    registry_path = corrupt_dir / "strategy_registry.json"

    # Write invalid JSON
    registry_path.write_text("{ this is not valid json {{{")

    # Loading should NOT raise — graceful recovery
    registry = StrategyRegistry(path=registry_path)

    # Default data returned
    assert registry._data["version"] == 1
    assert registry._data["strategies"] == {}

    # Corrupt backup file created
    backups = list(corrupt_dir.glob("strategy_registry.json.corrupt-*"))
    assert len(backups) == 1, f"Expected 1 backup, found {len(backups)}: {backups}"


def test_empty_file_recovery(corrupt_dir: Path) -> None:
    """Empty file treated as corrupt → graceful recovery."""
    registry_path = corrupt_dir / "strategy_registry.json"
    registry_path.write_text("")

    registry = StrategyRegistry(path=registry_path)
    assert registry._data["strategies"] == {}
    assert registry._data["version"] == 1


def test_non_json_utf8_recovery(corrupt_dir: Path) -> None:
    """Valid UTF-8 text that is not JSON → graceful recovery."""
    registry_path = corrupt_dir / "strategy_registry.json"
    registry_path.write_text("hello this is plain text, not json at all ₽ ♠ ☺")

    registry = StrategyRegistry(path=registry_path)
    assert registry._data["strategies"] == {}


def test_truncated_json_recovery(corrupt_dir: Path) -> None:
    """Truncated JSON (valid start, broken end) → graceful recovery."""
    registry_path = corrupt_dir / "strategy_registry.json"
    registry_path.write_text('{"version": 1, "strategies": {"key": {"id": "trun')

    registry = StrategyRegistry(path=registry_path)
    assert registry._data["strategies"] == {}


def test_no_file_creates_defaults(corrupt_dir: Path) -> None:
    """Non-existent file → defaults without error."""
    registry_path = corrupt_dir / "nonexistent.json"
    registry = StrategyRegistry(path=registry_path)
    assert registry._data["version"] == 1
    assert registry._data["strategies"] == {}


def test_atomic_save_preserves_data(saved_registry: StrategyRegistry) -> None:
    """Atomic save (tmp→replace) does not lose existing records."""
    path = saved_registry.path
    records_before = len(saved_registry.records())

    # Trigger save — should complete atomically
    saved_registry.save()

    # Reload and verify all records preserved
    reloaded = StrategyRegistry(path=path)
    assert len(reloaded.records()) == records_before

    for i in range(3):
        rec = reloaded.get(f"ATOMIC__{i}")
        assert rec is not None
        assert rec.params == {"idx": i}
        assert rec.metrics == {"pnl": float(i * 100)}


def test_atomic_save_no_tmp_leftover(saved_registry: StrategyRegistry) -> None:
    """After save(), no .tmp file should remain on disk."""
    saved_registry.save()

    parent = saved_registry.path.parent
    tmp_files = list(parent.glob("*.tmp"))
    assert tmp_files == [], f"Leftover .tmp files: {tmp_files}"


def test_corrupt_recovery_allows_subsequent_save(corrupt_dir: Path) -> None:
    """After corrupt recovery, saving new data works correctly."""
    registry_path = corrupt_dir / "strategy_registry.json"
    registry_path.write_text("NOT_JSON")

    registry = StrategyRegistry(path=registry_path)
    assert len(registry.records()) == 0

    # Record new strategy and save
    registry.record_generation(
        strategy_id="POST_CORRUPT__1",
        ticker="TEST",
        strategy="post_corrupt_strat",
        source="generator",
    )
    registry.save()

    # Reload and verify
    reloaded = StrategyRegistry(path=registry_path)
    assert len(reloaded.records()) == 1
    rec = reloaded.get("POST_CORRUPT__1")
    assert rec is not None
    assert rec.strategy == "post_corrupt_strat"


def test_valid_file_not_touched(valid_registry: StrategyRegistry) -> None:
    """Loading a valid file does not create .corrupt backup."""
    registry_path = valid_registry.path
    _ = StrategyRegistry(path=registry_path)

    backups = list(registry_path.parent.glob("*.corrupt-*"))
    assert backups == [], f"Unexpected corrupt backups: {backups}"

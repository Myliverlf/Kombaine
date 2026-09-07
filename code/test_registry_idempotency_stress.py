"""Idempotency stress — multi-iteration validation of init/export/sync stability.

Checks:
  1. export_legacy_state_files produces bytes-equal output across 10 iterations
  2. No temp file leftovers after export
  3. sync_from_legacy_files produces consistent results across repeated calls
  4. Registry save/load preserves data integrity across multiple cycles
  5. Concurrent-like rapid save/load does not corrupt the registry
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Ensure project modules are importable
COMBINE_DIR = Path(__file__).resolve().parent.parent
CODE_DIR = COMBINE_DIR / "code"
sys.path.insert(0, str(CODE_DIR))

from strategy_registry import (  # noqa: E402
    STATUS_REGISTRY_CANDIDATE,
    STATUS_WAITLIST,
    StrategyRegistry,
)


# ─── Fixtures ───────────────────────────────────────────────────────

@pytest.fixture
def stress_state_dir(tmp_path: Path) -> Path:
    """Isolated state directory for stress tests."""
    state_dir = tmp_path / "stress_state"
    state_dir.mkdir()
    return state_dir


@pytest.fixture
def stress_registry(stress_state_dir: Path) -> StrategyRegistry:
    """Registry with 5 strategies for stress testing."""
    registry_path = stress_state_dir / "strategy_registry.json"
    registry = StrategyRegistry(path=registry_path)

    strategies = [
        ("GAZP__mr", "GAZP", "mean_reversion", {"lookback": 20}, {"pnl": 1500.0}),
        ("LKOH__vwap", "LKOH", "vwap_reversion", {"lookback": 30}, {"pnl": 3000.0}),
        ("SBER__bol", "SBER", "bollinger", {"period": 14}, {"pnl": 800.0}),
        ("Si__trend", "Si", "trend_following", {}, {"pnl": 2200.0}),
        ("BR__ml", "BR", "ml_classifier", {"model": "rf"}, {"pnl": 500.0}),
    ]

    for sid, ticker, strat, params, metrics in strategies:
        registry.record_generation(
            strategy_id=sid,
            ticker=ticker,
            strategy=strat,
            params=params,
            metrics=metrics,
            source="generator",
        )

    # Move some to different statuses
    registry.transition("GAZP__mr", STATUS_WAITLIST, reason="stress_test")
    registry.transition("SBER__bol", STATUS_WAITLIST, reason="stress_test")

    registry.save()
    return registry


@pytest.fixture
def legacy_files_for_stress(stress_state_dir: Path) -> tuple[Path, Path]:
    """Create legacy files for sync stress tests."""
    waitlist = {
        "candidates": {
            "legacy_1": {
                "ticker": "GAZP",
                "strategy": "legacy_strat",
                "params": {},
                "metrics": {"pnl": 100.0},
                "rank_score": 200.0,
                "ttl_days": 7,
                "retests": 0,
                "status": "waitlist",
            },
        }
    }
    signal_pool = {
        "strategies": {
            "legacy_2": {
                "ticker": "SBER",
                "strategy": "legacy_pool",
                "params": {},
                "metrics": {"pnl": 500.0},
                "rank_score": 600.0,
                "status": "active",
            },
        },
        "last_rotation_ts": 0.0,
    }

    wl_path = stress_state_dir / "waitlist.json"
    sp_path = stress_state_dir / "signal_pool.json"
    wl_path.write_text(json.dumps(waitlist, indent=2, sort_keys=True))
    sp_path.write_text(json.dumps(signal_pool, indent=2, sort_keys=True))

    return wl_path, sp_path


# ─── Tests ──────────────────────────────────────────────────────────

def test_export_bytes_equal_across_iterations(stress_registry: StrategyRegistry, stress_state_dir: Path) -> None:
    """10 consecutive exports produce byte-identical waitlist and signal_pool files."""
    wl_path = stress_state_dir / "waitlist.json"
    sp_path = stress_state_dir / "signal_pool.json"

    wl_bytes_prev = None
    sp_bytes_prev = None

    for i in range(10):
        stress_registry.export_legacy_state_files(wl_path, sp_path)
        wl_current = wl_path.read_bytes()
        sp_current = sp_path.read_bytes()

        if wl_bytes_prev is not None:
            assert wl_current == wl_bytes_prev, (
                f"Iteration {i}: waitlist bytes differ from iteration {i - 1}"
            )
            assert sp_current == sp_bytes_prev, (
                f"Iteration {i}: signal_pool bytes differ from iteration {i - 1}"
            )

        wl_bytes_prev = wl_current
        sp_bytes_prev = sp_current

    # Both files should be valid JSON
    wl = json.loads(wl_bytes_prev)
    sp = json.loads(sp_bytes_prev)
    assert "candidates" in wl
    assert "strategies" in sp


def test_no_tmp_files_after_export(stress_state_dir: Path) -> None:
    """No .tmp or .corrupt files remain in state dir after export cycles."""
    registry_path = stress_state_dir / "strategy_registry.json"
    registry = StrategyRegistry(path=registry_path)
    registry.record_generation(
        strategy_id="tmp_test", ticker="TEST", strategy="s", source="test",
    )
    registry.save()

    wl_path = stress_state_dir / "waitlist.json"
    sp_path = stress_state_dir / "signal_pool.json"

    for _ in range(5):
        registry.export_legacy_state_files(wl_path, sp_path)

    # Check no leftover tmp files
    all_files = list(stress_state_dir.iterdir())
    tmp_files = [f for f in all_files if f.suffix == ".tmp" or ".tmp." in f.name or ".corrupt" in f.name]
    assert len(tmp_files) == 0, f"Leftover temp files found: {[f.name for f in tmp_files]}"


def test_sync_no_duplicates_across_repeated_calls(
    stress_state_dir: Path,
    legacy_files_for_stress: tuple[Path, Path],
) -> None:
    """Repeated sync_from_legacy_files does not create duplicate records."""
    wl_path, sp_path = legacy_files_for_stress
    registry_path = stress_state_dir / "sync_registry.json"
    registry = StrategyRegistry(path=registry_path)

    counts = []
    for _ in range(5):
        imported = registry.sync_from_legacy_files(wl_path, sp_path)
        counts.append(len(registry.records()))

    # All counts should be equal (no new records on repeated sync)
    assert all(c == counts[0] for c in counts), (
        f"Record count changed across sync iterations: {counts}"
    )


def test_save_load_preserves_data_integrity(stress_registry: StrategyRegistry, stress_state_dir: Path) -> None:
    """5 save/load cycles preserve all strategy data exactly."""
    registry_path = stress_state_dir / "strategy_registry.json"
    original_records = {r.strategy_id: r.to_dict() for r in stress_registry.records()}

    for _ in range(5):
        stress_registry.save()
        reloaded = StrategyRegistry(path=registry_path)
        reloaded_records = {r.strategy_id: r.to_dict() for r in reloaded.records()}

        assert reloaded_records == original_records, (
            "Data integrity lost after save/load cycle"
        )


def test_export_content_stable_after_registry_mutation(stress_state_dir: Path) -> None:
    """After mutating registry, export stabilizes within 1 iteration."""
    registry_path = stress_state_dir / "mutation_test.json"
    registry = StrategyRegistry(path=registry_path)

    # Phase 1: initial strategies
    registry.record_generation(
        strategy_id="A", ticker="GAZP", strategy="s1", source="test",
    )
    registry.record_generation(
        strategy_id="B", ticker="SBER", strategy="s2", source="test",
    )
    registry.save()

    wl_path = stress_state_dir / "waitlist_m.json"
    sp_path = stress_state_dir / "signal_pool_m.json"

    # Export in phase 1
    registry.export_legacy_state_files(wl_path, sp_path)
    phase1_wl = wl_path.read_bytes()

    # Phase 2: mutate — reject one, promote one
    registry.mark_rejected("A", reason="test_mutation")
    registry.mark_active_watchlist("B", slot=0, score=0.7)
    registry.save()

    # Export in phase 2
    registry.export_legacy_state_files(wl_path, sp_path)
    phase2_wl = wl_path.read_bytes()

    # Phase 3: re-export without changes — should match phase 2
    registry.export_legacy_state_files(wl_path, sp_path)
    phase3_wl = wl_path.read_bytes()

    assert phase2_wl == phase3_wl, "Re-export without changes should be identical"
    assert phase1_wl != phase2_wl, "After mutation, export should differ from phase 1"

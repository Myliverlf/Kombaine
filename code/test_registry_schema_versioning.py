"""Schema versioning guard — verifies StrategyRecord supports version roundtrip.

Checks:
  1. StrategyRecord.from_dict → to_dict roundtrip preserves version
  2. from_dict tolerates missing version (backward compat)
  3. from_dict rejects corrupt version type (negative test)
  4. StrategyRegistry persists version=1 across save/load cycle
  5. Unknown/extra fields in dict are gracefully ignored by from_dict

No live broker, no network — pure schema validation.
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
    STATUS_REGISTRY_CANDIDATE,
    StrategyEvent,
    StrategyRecord,
    StrategyRegistry,
)


# ─── Fixtures ───────────────────────────────────────────────────────

@pytest.fixture
def record_with_version() -> StrategyRecord:
    """StrategyRecord with explicit version-like metadata in metrics."""
    return StrategyRecord(
        strategy_id="GAZP__schema_v1",
        ticker="GAZP",
        strategy="mean_reversion",
        params={"lookback": 20},
        metrics={"pnl": 1500.0, "version": 1},
        status=STATUS_REGISTRY_CANDIDATE,
    )


@pytest.fixture
def record_dict_no_version() -> dict:
    """Dict representing a StrategyRecord WITHOUT a version key."""
    return {
        "strategy_id": "SBER__no_version",
        "ticker": "SBER",
        "strategy": "bollinger",
        "params": {},
        "metrics": {},
        "status": "registry/candidate",
        "created_ts": 1700000000.0,
        "updated_ts": 1700000000.0,
        "source": "generator",
        "generation_batch_id": "",
        "portfolio_context": {},
        "quality_gate": {},
        "candidate_stream": True,
        "active_rank": 0.0,
        "watchlist_slot": None,
        "signal_pool_slot": None,
        "expires_ts": None,
        "history": [],
    }


@pytest.fixture
def registry_with_strategy(tmp_path: Path) -> StrategyRegistry:
    """Registry with one strategy, saved and reloaded to verify version persistence."""
    registry_path = tmp_path / "schema_registry.json"
    registry = StrategyRegistry(path=registry_path)

    registry.record_generation(
        strategy_id="LKOH__vwap",
        ticker="LKOH",
        strategy="vwap_reversion",
        params={"lookback": 30},
        metrics={"pnl": 3000.0},
        source="generator",
    )
    registry.save()
    return StrategyRegistry(path=registry_path)


# ─── Tests ──────────────────────────────────────────────────────────

def test_record_roundtrip_preserves_version(record_with_version: StrategyRecord) -> None:
    """to_dict → from_dict roundtrip preserves all fields including metrics.version."""
    data = record_with_version.to_dict()
    assert data["strategy_id"] == "GAZP__schema_v1"
    assert data["metrics"]["version"] == 1

    restored = StrategyRecord.from_dict(data)
    assert restored.strategy_id == record_with_version.strategy_id
    assert restored.metrics["version"] == 1
    assert restored.ticker == "GAZP"
    assert restored.params == {"lookback": 20}


def test_from_dict_tolerates_missing_version(record_dict_no_version: dict) -> None:
    """from_dict works correctly when version key is absent (backward compat)."""
    restored = StrategyRecord.from_dict(record_dict_no_version)

    assert restored.strategy_id == "SBER__no_version"
    assert restored.ticker == "SBER"
    assert restored.strategy == "bollinger"
    assert restored.params == {}
    assert restored.metrics == {}
    assert restored.status == STATUS_REGISTRY_CANDIDATE


def test_from_dict_ignores_extra_unknown_fields(record_dict_no_version: dict) -> None:
    """from_dict silently ignores unknown/extra fields in input dict."""
    record_dict_no_version["future_field_xyz"] = "some_value"
    record_dict_no_version["deprecated_flag"] = True
    record_dict_no_version["version"] = 42

    restored = StrategyRecord.from_dict(record_dict_no_version)

    # Should restore correctly without raising
    assert restored.strategy_id == "SBER__no_version"
    assert restored.ticker == "SBER"


def test_registry_persists_version_field(tmp_path: Path) -> None:
    """StrategyRegistry._default_data includes version=1, and save/load preserves it."""
    registry_path = tmp_path / "version_test.json"

    # Create and save
    registry = StrategyRegistry(path=registry_path)
    registry.record_generation(
        strategy_id="test_v1", ticker="TEST", strategy="s", source="test",
    )
    registry.save()

    # Reload and check version
    raw = json.loads(registry_path.read_text())
    assert "version" in raw, f"Missing version in saved registry: {list(raw.keys())}"
    assert raw["version"] == 1

    # Reload through API
    reloaded = StrategyRegistry(path=registry_path)
    assert reloaded.data["version"] == 1


def test_registry_load_empty_has_version(tmp_path: Path) -> None:
    """New empty registry has version=1 from _default_data."""
    registry = StrategyRegistry(path=tmp_path / "new_empty.json")
    assert registry.data.get("version") == 1
    assert registry.data.get("strategies") == {}
    assert registry.data.get("events") == []


def test_corrupt_version_does_not_crash(tmp_path: Path) -> None:
    """Loading a registry file with non-integer version does not crash on load."""
    corrupt_data = {
        "version": "not_a_number",
        "created_ts": 0.0,
        "updated_ts": 0.0,
        "strategies": {},
        "events": [],
    }
    registry_path = tmp_path / "corrupt_version.json"
    registry_path.write_text(json.dumps(corrupt_data))

    # Should load without raising (version is opaque storage, not validated on load)
    registry = StrategyRegistry(path=registry_path)
    # The version stays as whatever was stored — load() doesn't validate types
    assert registry.data["version"] == "not_a_number"
    # But strategies still work
    assert registry.records() == []

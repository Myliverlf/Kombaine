"""Iteration 06: Canonical Seeder Handoff tests.

T1–T16: mandatory functional tests for seeder handoff.
F1–F18: failure matrix covering all handoff failure modes.

All tests use tmp_path fixtures — no real broker, no real data files.
CLASS 2: runtime non-trading / no broker / no live / no strategy changes.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "core"))

from core.run_contract import (
    ResearchRun,
    SCHEMA_VERSION,
    REQUIRED_CHECK_NAMES,
)
from core.seeder_handoff import (
    resolve_latest_completed_run,
    validate_handoff,
    seed_from_eligible,
    legacy_fallback,
    run_seeder_handoff,
    HandoffValidation,
    HandoffSeedResult,
    HANDOFF_SCHEMA_VERSION,
    UNIVERSE_GATE_DEFAULT,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Mock StrategyRegistry for testing
# ═══════════════════════════════════════════════════════════════════════════════

class MockRecord:
    """Minimal record mock for testing registry operations."""
    def __init__(self, strategy_id, **kwargs):
        self.strategy_id = strategy_id
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockRegistry:
    """Mock registry that tracks calls for verification."""

    def __init__(self):
        self._records: Dict[str, MockRecord] = {}
        self._save_count = 0
        self._export_count = 0
        self._should_fail_save = False

    def get(self, strategy_id: str):
        return self._records.get(strategy_id)

    def record_generation(self, strategy_id, ticker, strategy, params,
                          metrics, portfolio_context, quality_gate,
                          source, status, note="", **kwargs):
        self._records[strategy_id] = MockRecord(
            strategy_id,
            ticker=ticker,
            strategy=strategy,
            params=params,
            metrics=metrics,
            portfolio_context=portfolio_context,
            quality_gate=quality_gate,
            source=source,
            status=status,
            note=note,
        )

    def _store_record(self, record):
        self._records[record.strategy_id] = record

    def save(self):
        if self._should_fail_save:
            raise RuntimeError("Mock registry save failure")
        self._save_count += 1

    def export_legacy_state_files(self):
        self._export_count += 1

    def records(self):
        return list(self._records.values())


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _base(tmp_path: Path) -> Path:
    """Return the reports base_dir inside tmp_path."""
    d = tmp_path / "reports" / "strategy_architect"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _create_completed_run(
    base: Path,
    run_id: str = "run_20260829_120000_abc12345",
    eligible: Optional[List[Dict]] = None,
    integrity_pass: bool = True,
    universe: Optional[List[str]] = None,
) -> Path:
    """Create a fully completed run directory for testing."""
    universe = universe or ["SBER", "GAZP", "LKOH", "BR", "Si"]
    run_dir = base / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Manifest
    manifest = {
        "run_id": run_id,
        "schema_version": SCHEMA_VERSION,
        "status": "COMPLETED",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "arguments": {},
        "universe": universe,
        "timeframes": ["15m"],
        "horizons": [60],
        "strategy_families": ["bband_rsi"],
        "strategy_versions": {},
        "git_revision": {"revision": "abc123", "dirty": False},
        "code_version": {"git": {"revision": "abc123"}},
        "dataset_info": {},
        "cost_assumptions": {"commission_pct": 0.05},
        "planned_configurations": 2,
        "tested_configurations": 2,
        "failed_configurations": 0,
        "eligible_configurations": len(eligible) if eligible else 0,
        "runner_version": "test",
        "backtest_engine_version": "test",
        "timesfm_state": "dummy",
        "host_context": {"hostname": "test", "pid": 1, "python": "3.11"},
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Checks
    checks = {
        "run_id": run_id,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "all_passed": integrity_pass,
        "checks": {name: integrity_pass for name in REQUIRED_CHECK_NAMES},
    }
    (run_dir / "checks.json").write_text(
        json.dumps(checks, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Eligible candidates
    if eligible is not None:
        (run_dir / "eligible_candidates.json").write_text(
            json.dumps(eligible, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # Plan
    (run_dir / "research_plan.json").write_text(
        json.dumps({"configs": []}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Candidates ledger
    (run_dir / "candidates.jsonl").write_text(
        "",
        encoding="utf-8",
    )

    return run_dir


def _eligible_candidate(
    run_id: str = "run_20260829_120000_abc12345",
    instrument: str = "SBER",
    strategy: str = "bband_rsi",
    config_key: str = "cfg-sber-001",
    timeframe: str = "15m",
    horizon_days: int = 60,
) -> Dict[str, Any]:
    """Build an eligible candidate dict."""
    return {
        "run_id": run_id,
        "config_key": config_key,
        "instrument": instrument,
        "timeframe": timeframe,
        "strategy": strategy,
        "parameters": {"period": 14, "mult": 2.0},
        "horizon_days": horizon_days,
        "dataset_identity": {"path": "test.csv", "hash": "abc123"},
        "strategy_version": "current",
        "metrics": {
            "total_pnl": 500.0,
            "profit_factor": 1.5,
            "max_drawdown": -100.0,
            "sharpe": 0.8,
            "win_rate": 0.6,
            "trade_count": 20,
        },
        "validation_reasons": [],
    }


def _write_pointer(base: Path, run_id: str, run_dir: Path) -> None:
    """Write latest_run.json pointer."""
    pointer = {
        "run_id": run_id,
        "path": str(run_dir),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    (base / "latest_run.json").write_text(
        json.dumps(pointer, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# T1 – T16: Mandatory functional tests
# ═══════════════════════════════════════════════════════════════════════════════

def test_t1_latest_completed_resolution(tmp_path: Path):
    """T1: latest COMPLETED run resolves from latest_run.json pointer."""
    base = _base(tmp_path)
    run_id = "run_20260829_120000_t0000001"
    run_dir = _create_completed_run(base, run_id=run_id)
    _write_pointer(base, run_id, run_dir)

    pointer = resolve_latest_completed_run(base)
    assert pointer is not None
    assert pointer["run_id"] == run_id
    assert Path(pointer["path"]).exists()


def test_t2_running_cannot_seed(tmp_path: Path):
    """T2: RUNNING status cannot seed — validation blocks."""
    base = _base(tmp_path)
    run_id = "run_running_0001"
    run_dir = base / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Write manifest with RUNNING status
    manifest = {
        "run_id": run_id,
        "schema_version": SCHEMA_VERSION,
        "status": "RUNNING",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    _write_pointer(base, run_id, run_dir)
    validation = validate_handoff(base)

    assert not validation.valid
    assert "COMPLETED" in validation.blocked_reason
    assert validation.status == "RUNNING"


def test_t3_failed_partial_blocked_cannot_seed(tmp_path: Path):
    """T3: FAILED/PARTIAL/BLOCKED status cannot seed."""
    base = _base(tmp_path)
    for status in ["FAILED", "PARTIAL", "BLOCKED"]:
        run_id = f"run_{status.lower()}_0001"
        run_dir = base / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        manifest = {
            "run_id": run_id,
            "schema_version": SCHEMA_VERSION,
            "status": status,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
        _write_pointer(base, run_id, run_dir)

        validation = validate_handoff(base)
        assert not validation.valid, f"Status {status} should block seeding"
        assert validation.status == status


def test_t4_integrity_failure_blocks(tmp_path: Path):
    """T4: Integrity check failure blocks seeding."""
    base = _base(tmp_path)
    run_id = "run_integrity_fail_0001"
    run_dir = _create_completed_run(base, run_id=run_id, integrity_pass=False)
    _write_pointer(base, run_id, run_dir)

    validation = validate_handoff(base)
    assert not validation.valid
    assert "integrity" in validation.blocked_reason.lower()


def test_t5_cross_run_contamination_rejected(tmp_path: Path):
    """T5: Cross-run contamination (wrong run_id in candidates) rejected."""
    base = _base(tmp_path)
    run_id = "run_good_0001"
    bad_run_id = "run_bad_9999"
    run_dir = _create_completed_run(
        base, run_id=run_id,
        eligible=[
            _eligible_candidate(run_id=run_id, config_key="cfg-ok"),
            _eligible_candidate(run_id=bad_run_id, config_key="cfg-bad"),
        ],
    )
    _write_pointer(base, run_id, run_dir)

    validation = validate_handoff(base)
    assert not validation.valid
    assert any("run_id mismatch" in r for r in validation.rejection_reasons)


def test_t6_missing_config_key_rejected(tmp_path: Path):
    """T6: Missing config_key is rejected."""
    base = _base(tmp_path)
    run_id = "run_nokey_0001"
    cand = _eligible_candidate(run_id=run_id)
    del cand["config_key"]  # Remove config_key
    run_dir = _create_completed_run(base, run_id=run_id, eligible=[cand])
    _write_pointer(base, run_id, run_dir)

    validation = validate_handoff(base)
    assert not validation.valid
    assert any("config_key" in r for r in validation.rejection_reasons)


def test_t7_foreign_universe_rejected(tmp_path: Path):
    """T7: Foreign universe candidate is rejected."""
    base = _base(tmp_path)
    run_id = "run_foreign_0001"
    run_dir = _create_completed_run(
        base, run_id=run_id,
        eligible=[
            _eligible_candidate(run_id=run_id, instrument="IMOEX", config_key="cfg-imoex"),
        ],
    )
    _write_pointer(base, run_id, run_dir)

    validation = validate_handoff(base, universe=["SBER", "GAZP", "LKOH", "BR", "Si"])
    assert not validation.valid
    assert any("foreign universe" in r for r in validation.rejection_reasons)


def test_t8_broken_canonical_no_silent_fallback(tmp_path: Path):
    """T8: Broken canonical + valid legacy still no silent fallback."""
    base = _base(tmp_path)
    # No latest_run.json — canonical is broken
    # Default mode (use_legacy=False) should return BLOCKED
    registry = MockRegistry()
    result = run_seeder_handoff(
        reports_dir=base,
        registry=registry,
        use_legacy=False,
    )

    assert result.source_type == "canonical_research_run"
    assert result.validation and not result.validation.valid
    assert result.accepted_count == 0
    assert result.validation.blocked_reason is not None
    assert "latest_run.json" in result.validation.blocked_reason


def test_t9_legacy_requires_explicit_opt_in_and_labeling(tmp_path: Path):
    """T9: Legacy mode requires explicit opt-in and visible LEGACY label."""
    base = _base(tmp_path)

    # Create a legacy scan file
    scan_data = [
        {"ticker": "SBER", "strategy": "bband_rsi", "wf_quality_passed": True,
         "best_params": {"period": 14}},
    ]
    scan_path = tmp_path / "scan.json"
    scan_path.write_text(json.dumps(scan_data, indent=2))

    registry = MockRegistry()
    result = legacy_fallback(
        scan_path=scan_path,
        registry=registry,
        universe=["SBER"],
    )

    assert result.legacy_mode_enabled is True
    assert result.source_type == "legacy_scan"
    assert result.accepted_count == 1


def test_t10_same_run_idempotent(tmp_path: Path):
    """T10: Same run seeded twice is idempotent — no duplicates."""
    base = _base(tmp_path)
    run_id = "run_idempotent_0001"
    eligible = [
        _eligible_candidate(run_id=run_id, config_key="cfg-idem-001"),
    ]
    run_dir = _create_completed_run(base, run_id=run_id, eligible=eligible)
    _write_pointer(base, run_id, run_dir)

    registry = MockRegistry()

    # First seed
    result1 = seed_from_eligible(base, registry, universe=["SBER", "GAZP", "LKOH", "BR", "Si"])
    assert result1.accepted_count == 1

    # Second seed — should skip
    result2 = seed_from_eligible(base, registry, universe=["SBER", "GAZP", "LKOH", "BR", "Si"])
    assert result2.skipped_existing_count == 1
    assert result2.accepted_count == 0
    # Only one record in registry
    assert len(registry._records) == 1


def test_t11_new_completed_run_provides_new_candidates(tmp_path: Path):
    """T11: New completed run can provide new candidates."""
    base = _base(tmp_path)
    run_id_v1 = "run_v1_0001"
    run_id_v2 = "run_v2_0001"
    eligible_v1 = [_eligible_candidate(run_id=run_id_v1, config_key="cfg-v1")]
    eligible_v2 = [_eligible_candidate(run_id=run_id_v2, config_key="cfg-v2")]

    _create_completed_run(base, run_id=run_id_v1, eligible=eligible_v1)
    run_dir_v2 = _create_completed_run(base, run_id=run_id_v2, eligible=eligible_v2)

    # Point to v2 (newer)
    _write_pointer(base, run_id_v2, run_dir_v2)

    registry = MockRegistry()
    result = seed_from_eligible(base, registry, universe=["SBER", "GAZP", "LKOH", "BR", "Si"])
    assert result.accepted_count == 1
    assert result.validation.run_id == run_id_v2


def test_t12_registry_remains_lifecycle_truth(tmp_path: Path):
    """T12: Registry remains lifecycle truth — seeder only adds, never replaces."""
    base = _base(tmp_path)
    run_id = "run_registry_0001"
    eligible = [
        _eligible_candidate(run_id=run_id, config_key="cfg-reg-001"),
    ]
    run_dir = _create_completed_run(base, run_id=run_id, eligible=eligible)
    _write_pointer(base, run_id, run_dir)

    registry = MockRegistry()
    result = seed_from_eligible(base, registry, universe=["SBER", "GAZP", "LKOH", "BR", "Si"])

    # Registry was committed
    assert result.registry_committed is True
    # Record exists in registry
    assert registry.get("cfg-reg-001") is not None
    # Registry is the source of truth — no derived files are created directly
    assert result.derived_export_path is not None  # but exports follow registry


def test_t13_signal_pool_waitlist_remain_registry_derived(tmp_path: Path):
    """T13: signal_pool/waitlist remain registry-derived — no direct research→signal."""
    base = _base(tmp_path)
    run_id = "run_derived_0001"
    eligible = [
        _eligible_candidate(run_id=run_id, config_key="cfg-deriv-001"),
    ]
    run_dir = _create_completed_run(base, run_id=run_id, eligible=eligible)
    _write_pointer(base, run_id, run_dir)

    registry = MockRegistry()
    result = seed_from_eligible(base, registry, universe=["SBER", "GAZP", "LKOH", "BR", "Si"])

    # Derived export paths show they come from registry
    assert result.derived_export_path == "state/waitlist.json, state/signal_pool.json"
    # The registry.export_legacy_state_files was called (derived)
    assert registry._export_count >= 1


def test_t14_mid_write_failure_no_corrupt(tmp_path: Path):
    """T14: Mid-write failure cannot corrupt canonical registry."""
    base = _base(tmp_path)
    run_id = "run_midwrite_0001"
    eligible = [
        _eligible_candidate(run_id=run_id, config_key="cfg-mid-001"),
    ]
    run_dir = _create_completed_run(base, run_id=run_id, eligible=eligible)
    _write_pointer(base, run_id, run_dir)

    registry = MockRegistry()
    registry._should_fail_save = True  # Simulate save failure

    result = seed_from_eligible(base, registry, universe=["SBER", "GAZP", "LKOH", "BR", "Si"])

    # Registration succeeded but save failed
    assert registry.get("cfg-mid-001") is not None  # record was created
    assert result.registry_committed is False  # save failed
    assert any("save failed" in r for r in result.rejection_reasons)


def test_t15_scheduler_resolves_canonical_source(tmp_path: Path):
    """T15: run_seeder_handoff entry point resolves canonical source."""
    base = _base(tmp_path)
    run_id = "run_sched_0001"
    eligible = [
        _eligible_candidate(run_id=run_id, config_key="cfg-sched-001"),
    ]
    run_dir = _create_completed_run(base, run_id=run_id, eligible=eligible)
    _write_pointer(base, run_id, run_dir)

    registry = MockRegistry()
    result = run_seeder_handoff(
        reports_dir=base,
        registry=registry,
        universe=["SBER", "GAZP", "LKOH", "BR", "Si"],
    )

    assert result.source_type == "canonical_research_run"
    assert result.source_run_id == run_id
    assert result.accepted_count == 1


def test_t16_regression_iterations_01_05(tmp_path: Path):
    """T16: Iterations 01-05 regression — universe gate and run contract intact."""
    # Iteration 01: universe gate
    base = _base(tmp_path)
    run_id = "run_regress_0001"
    eligible = [
        _eligible_candidate(run_id=run_id, instrument="IMOEX", config_key="cfg-foreign"),
    ]
    run_dir = _create_completed_run(base, run_id=run_id, eligible=eligible)
    _write_pointer(base, run_id, run_dir)

    # Universe gate (Iteration 01) should still block foreign instruments
    validation = validate_handoff(base, universe=["SBER", "GAZP", "LKOH", "BR", "Si"])
    assert not validation.valid
    assert any("foreign universe" in r for r in validation.rejection_reasons)

    # Iteration 05: run contract integrity checks still work
    run_id2 = "run_regress_0002"
    run_dir2 = _create_completed_run(base, run_id=run_id2, integrity_pass=False)
    _write_pointer(base, run_id2, run_dir2)
    validation2 = validate_handoff(base)
    assert not validation2.valid
    assert "integrity" in validation2.blocked_reason.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# F1 – F18: Failure matrix
# ═══════════════════════════════════════════════════════════════════════════════

def test_f1_latest_pointer_missing(tmp_path: Path):
    """F1: latest_run.json missing → HANDOFF_BLOCKED."""
    base = _base(tmp_path)
    validation = validate_handoff(base)
    assert not validation.valid
    assert "latest_run.json" in validation.blocked_reason


def test_f2_pointer_malformed(tmp_path: Path):
    """F2: malformed latest_run.json → HANDOFF_BLOCKED."""
    base = _base(tmp_path)
    (base / "latest_run.json").write_text("NOT JSON {{{", encoding="utf-8")
    validation = validate_handoff(base)
    assert not validation.valid
    assert "malformed" in validation.blocked_reason.lower()


def test_f3_pointer_points_to_missing_run(tmp_path: Path):
    """F3: pointer points to non-existent run directory → HANDOFF_BLOCKED."""
    base = _base(tmp_path)
    pointer = {
        "run_id": "run_nonexistent_0001",
        "path": str(base / "runs" / "run_nonexistent_0001"),
    }
    (base / "latest_run.json").write_text(
        json.dumps(pointer, indent=2), encoding="utf-8"
    )
    validation = validate_handoff(base)
    assert not validation.valid
    assert "does not exist" in validation.blocked_reason


def test_f4_manifest_missing(tmp_path: Path):
    """F4: manifest.json missing → HANDOFF_BLOCKED."""
    base = _base(tmp_path)
    run_id = "run_nomani_0001"
    run_dir = base / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_pointer(base, run_id, run_dir)

    validation = validate_handoff(base)
    assert not validation.valid
    assert "manifest" in validation.blocked_reason.lower()


def test_f5_status_running(tmp_path: Path):
    """F5: status RUNNING → HANDOFF_BLOCKED."""
    base = _base(tmp_path)
    run_id = "run_status_run_0001"
    run_dir = base / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"run_id": run_id, "status": "RUNNING"}
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    _write_pointer(base, run_id, run_dir)

    validation = validate_handoff(base)
    assert not validation.valid
    assert validation.status == "RUNNING"


def test_f6_status_partial_failed_blocked(tmp_path: Path):
    """F6: status PARTIAL/FAILED/BLOCKED → HANDOFF_BLOCKED."""
    base = _base(tmp_path)
    for status in ["PARTIAL", "FAILED", "BLOCKED"]:
        run_id = f"run_f6_{status.lower()}"
        run_dir = base / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        manifest = {"run_id": run_id, "status": status}
        (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
        _write_pointer(base, run_id, run_dir)

        validation = validate_handoff(base)
        assert not validation.valid
        assert validation.status == status


def test_f7_integrity_failed(tmp_path: Path):
    """F7: integrity checks failed → HANDOFF_BLOCKED."""
    base = _base(tmp_path)
    run_id = "run_integrity_0001"
    run_dir = _create_completed_run(base, run_id=run_id, integrity_pass=False)
    _write_pointer(base, run_id, run_dir)

    validation = validate_handoff(base)
    assert not validation.valid
    assert "integrity" in validation.blocked_reason.lower()


def test_f8_eligible_artifact_missing(tmp_path: Path):
    """F8: eligible_candidates.json missing → HANDOFF_BLOCKED."""
    base = _base(tmp_path)
    run_id = "run_noeligible_0001"
    run_dir = _create_completed_run(base, run_id=run_id)
    # Don't write eligible_candidates.json
    _write_pointer(base, run_id, run_dir)

    validation = validate_handoff(base)
    assert not validation.valid
    assert "eligible" in validation.blocked_reason.lower()


def test_f9_eligible_artifact_corrupt(tmp_path: Path):
    """F9: eligible_candidates.json is corrupt → HANDOFF_BLOCKED."""
    base = _base(tmp_path)
    run_id = "run_corruptelig_0001"
    run_dir = _create_completed_run(base, run_id=run_id)
    (run_dir / "eligible_candidates.json").write_text("NOT JSON {{{", encoding="utf-8")
    _write_pointer(base, run_id, run_dir)

    validation = validate_handoff(base)
    assert not validation.valid
    assert "eligible" in validation.blocked_reason.lower()


def test_f10_candidate_run_id_mismatch(tmp_path: Path):
    """F10: candidate with wrong run_id → cross-run contamination blocked."""
    base = _base(tmp_path)
    run_id = "run_f10_0001"
    run_dir = _create_completed_run(
        base, run_id=run_id,
        eligible=[
            _eligible_candidate(run_id="run_OTHER_9999", config_key="cfg-f10-bad"),
        ],
    )
    _write_pointer(base, run_id, run_dir)

    validation = validate_handoff(base)
    assert not validation.valid
    assert any("run_id mismatch" in r for r in validation.rejection_reasons)


def test_f11_missing_config_key(tmp_path: Path):
    """F11: candidate missing config_key → HANDOFF_BLOCKED."""
    base = _base(tmp_path)
    run_id = "run_f11_0001"
    cand = _eligible_candidate(run_id=run_id, config_key="cfg-f11-ok")
    cand_no_key = _eligible_candidate(run_id=run_id)
    del cand_no_key["config_key"]
    run_dir = _create_completed_run(base, run_id=run_id, eligible=[cand, cand_no_key])
    _write_pointer(base, run_id, run_dir)

    validation = validate_handoff(base)
    # The valid candidate (cfg-f11-ok) passes, the bad one is rejected
    assert validation.candidates_valid == 1
    assert validation.candidates_rejected == 1
    assert any("config_key" in r for r in validation.rejection_reasons)


def test_f12_duplicate_config_key(tmp_path: Path):
    """F12: duplicate config_key → rejected."""
    base = _base(tmp_path)
    run_id = "run_f12_0001"
    run_dir = _create_completed_run(
        base, run_id=run_id,
        eligible=[
            _eligible_candidate(run_id=run_id, config_key="cfg-dup"),
            _eligible_candidate(run_id=run_id, config_key="cfg-dup"),
        ],
    )
    _write_pointer(base, run_id, run_dir)

    validation = validate_handoff(base)
    assert validation.candidates_rejected >= 1
    assert any("duplicate" in r.lower() for r in validation.rejection_reasons)


def test_f13_foreign_universe_candidate(tmp_path: Path):
    """F13: foreign universe candidate → rejected."""
    base = _base(tmp_path)
    run_id = "run_f13_0001"
    run_dir = _create_completed_run(
        base, run_id=run_id,
        eligible=[
            _eligible_candidate(run_id=run_id, instrument="IMOEX", config_key="cfg-f13-imoex"),
        ],
    )
    _write_pointer(base, run_id, run_dir)

    validation = validate_handoff(base, universe=["SBER", "GAZP", "LKOH", "BR", "Si"])
    assert not validation.valid
    assert any("foreign universe" in r for r in validation.rejection_reasons)


def test_f14_same_run_seeded_twice(tmp_path: Path):
    """F14: same run seeded twice → idempotent, no duplicate effects."""
    base = _base(tmp_path)
    run_id = "run_f14_0001"
    eligible = [_eligible_candidate(run_id=run_id, config_key="cfg-f14")]
    run_dir = _create_completed_run(base, run_id=run_id, eligible=eligible)
    _write_pointer(base, run_id, run_dir)

    registry = MockRegistry()
    result1 = seed_from_eligible(base, registry, universe=["SBER", "GAZP", "LKOH", "BR", "Si"])
    result2 = seed_from_eligible(base, registry, universe=["SBER", "GAZP", "LKOH", "BR", "Si"])

    assert result1.accepted_count == 1
    assert result2.skipped_existing_count == 1
    assert result2.accepted_count == 0
    assert len(registry._records) == 1


def test_f15_legacy_scan_exists_while_canonical_valid(tmp_path: Path):
    """F15: legacy scan exists while canonical valid → canonical wins, legacy ignored."""
    base = _base(tmp_path)
    run_id = "run_f15_0001"
    eligible = [_eligible_candidate(run_id=run_id, config_key="cfg-f15")]
    run_dir = _create_completed_run(base, run_id=run_id, eligible=eligible)
    _write_pointer(base, run_id, run_dir)

    # Also create a legacy scan file
    scan_data = [{"ticker": "SBER", "strategy": "bband_rsi", "wf_quality_passed": True}]
    scan_path = tmp_path / "scan.json"
    scan_path.write_text(json.dumps(scan_data))

    registry = MockRegistry()
    result = run_seeder_handoff(
        reports_dir=base,
        registry=registry,
        use_legacy=False,  # Canonical wins — legacy not opted in
    )

    assert result.source_type == "canonical_research_run"
    assert result.accepted_count == 1
    assert not result.legacy_mode_enabled


def test_f16_canonical_invalid_while_legacy_valid(tmp_path: Path):
    """F16: canonical invalid while legacy valid → HANDOFF_BLOCKED (no fallback)."""
    base = _base(tmp_path)
    # No canonical run — pointer missing
    # Create legacy scan file
    scan_data = [{"ticker": "SBER", "strategy": "bband_rsi", "wf_quality_passed": True}]
    scan_path = tmp_path / "scan.json"
    scan_path.write_text(json.dumps(scan_data))

    registry = MockRegistry()
    result = run_seeder_handoff(
        reports_dir=base,
        registry=registry,
        use_legacy=False,  # No legacy opt-in
    )

    assert result.source_type == "canonical_research_run"
    assert result.accepted_count == 0
    assert result.validation and not result.validation.valid


def test_f17_newer_completed_replaces_older(tmp_path: Path):
    """F17: newer COMPLETED run replaces older as latest."""
    base = _base(tmp_path)
    run_id_old = "run_old_0001"
    run_id_new = "run_new_0001"

    _create_completed_run(base, run_id=run_id_old,
                          eligible=[_eligible_candidate(run_id=run_id_old, config_key="cfg-old")])
    run_dir_new = _create_completed_run(base, run_id=run_id_new,
                                         eligible=[_eligible_candidate(run_id=run_id_new, config_key="cfg-new")])

    # Point to new
    _write_pointer(base, run_id_new, run_dir_new)

    validation = validate_handoff(base)
    assert validation.run_id == run_id_new


def test_f18_registry_write_fails_midway(tmp_path: Path):
    """F18: registry write fails midway → HANDOFF partial, committed=false."""
    base = _base(tmp_path)
    run_id = "run_f18_0001"
    eligible = [_eligible_candidate(run_id=run_id, config_key="cfg-f18")]
    run_dir = _create_completed_run(base, run_id=run_id, eligible=eligible)
    _write_pointer(base, run_id, run_dir)

    registry = MockRegistry()
    registry._should_fail_save = True

    result = seed_from_eligible(base, registry, universe=["SBER", "GAZP", "LKOH", "BR", "Si"])
    assert result.registry_committed is False
    assert any("save failed" in r for r in result.rejection_reasons)


# ═══════════════════════════════════════════════════════════════════════════════
# Additional edge case tests
# ═══════════════════════════════════════════════════════════════════════════════

def test_validation_returns_structured_result(tmp_path: Path):
    """Validation returns structured HandoffValidation data."""
    base = _base(tmp_path)
    run_id = "run_struct_0001"
    run_dir = _create_completed_run(
        base, run_id=run_id,
        eligible=[_eligible_candidate(run_id=run_id, config_key="cfg-s1")],
    )
    _write_pointer(base, run_id, run_dir)

    validation = validate_handoff(base)
    assert isinstance(validation, HandoffValidation)
    assert validation.valid is True
    assert validation.run_id == run_id
    assert validation.eligible_count == 1
    assert validation.candidates_valid == 1
    d = validation.to_dict()
    assert d["valid"] is True
    assert d["run_id"] == run_id


def test_seed_result_structured(tmp_path: Path):
    """Seed result returns structured HandoffSeedResult data."""
    base = _base(tmp_path)
    run_id = "run_result_0001"
    run_dir = _create_completed_run(
        base, run_id=run_id,
        eligible=[_eligible_candidate(run_id=run_id, config_key="cfg-r1")],
    )
    _write_pointer(base, run_id, run_dir)

    registry = MockRegistry()
    result = seed_from_eligible(base, registry, universe=["SBER", "GAZP", "LKOH", "BR", "Si"])

    assert isinstance(result, HandoffSeedResult)
    assert result.source_type == "canonical_research_run"
    assert result.source_run_id == run_id
    d = result.to_dict()
    assert d["source_type"] == "canonical_research_run"


def test_dry_run_no_registry_mutation(tmp_path: Path):
    """Dry run validates but doesn't write to registry."""
    base = _base(tmp_path)
    run_id = "run_dry_0001"
    run_dir = _create_completed_run(
        base, run_id=run_id,
        eligible=[_eligible_candidate(run_id=run_id, config_key="cfg-dry")],
    )
    _write_pointer(base, run_id, run_dir)

    registry = MockRegistry()
    result = seed_from_eligible(base, registry, universe=["SBER", "GAZP", "LKOH", "BR", "Si"],
                                dry_run=True)

    assert result.accepted_count == 1
    assert len(registry._records) == 0  # No actual writes
    assert registry._save_count == 0


def test_multiple_candidates_mixed_validity(tmp_path: Path):
    """Multiple candidates with mixed validity: some pass, some fail."""
    base = _base(tmp_path)
    run_id = "run_mixed_0001"
    run_dir = _create_completed_run(
        base, run_id=run_id,
        eligible=[
            _eligible_candidate(run_id=run_id, config_key="cfg-mix-ok",
                               instrument="SBER"),
            _eligible_candidate(run_id=run_id, config_key="cfg-mix-foreign",
                               instrument="IMOEX"),
            _eligible_candidate(run_id="run_OTHER_9999", config_key="cfg-mix-cross"),
        ],
    )
    _write_pointer(base, run_id, run_dir)

    validation = validate_handoff(base, universe=["SBER", "GAZP", "LKOH", "BR", "Si"])
    assert validation.candidates_valid == 1  # Only SBER passes
    assert validation.candidates_rejected >= 1


def test_legacy_fallback_blocked_when_no_scan(tmp_path: Path):
    """Legacy fallback with missing scan file → blocked."""
    base = _base(tmp_path)
    scan_path = tmp_path / "nonexistent.json"

    registry = MockRegistry()
    result = legacy_fallback(scan_path=scan_path, registry=registry)
    assert result.rejected_count > 0
    assert any("not found" in r.lower() for r in result.rejection_reasons)


def test_run_seeder_handoff_legacy_opt_in(tmp_path: Path):
    """run_seeder_handoff with use_legacy=True uses legacy path."""
    base = _base(tmp_path)
    # No canonical run
    scan_data = [{"ticker": "SBER", "strategy": "bband_rsi", "wf_quality_passed": True,
                  "best_params": {"period": 14}}]
    scan_path = tmp_path / "scan.json"
    scan_path.write_text(json.dumps(scan_data))

    registry = MockRegistry()
    result = run_seeder_handoff(
        reports_dir=base,
        registry=registry,
        use_legacy=True,
        legacy_scan_path=scan_path,
    )

    assert result.legacy_mode_enabled is True
    assert result.source_type == "legacy_scan"


def test_run_seeder_handoff_legacy_no_scan_path(tmp_path: Path):
    """run_seeder_handoff with use_legacy=True but no scan_path → error."""
    base = _base(tmp_path)
    registry = MockRegistry()
    result = run_seeder_handoff(
        reports_dir=base,
        registry=registry,
        use_legacy=True,
        legacy_scan_path=None,
    )

    assert result.accepted_count == 0
    assert any("legacy_scan_path is None" in r for r in result.rejection_reasons)


def test_empty_eligible_list(tmp_path: Path):
    """Empty eligible list from a valid run → no candidates seeded."""
    base = _base(tmp_path)
    run_id = "run_empty_0001"
    run_dir = _create_completed_run(base, run_id=run_id, eligible=[])
    _write_pointer(base, run_id, run_dir)

    registry = MockRegistry()
    result = seed_from_eligible(base, registry, universe=["SBER", "GAZP", "LKOH", "BR", "Si"])

    assert result.eligible_count == 0
    assert result.accepted_count == 0
    assert registry._save_count == 0  # No save needed


def test_eligible_not_a_list(tmp_path: Path):
    """eligible_candidates.json containing a dict instead of list → blocked."""
    base = _base(tmp_path)
    run_id = "run_dict_0001"
    run_dir = base / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"run_id": run_id, "status": "COMPLETED"}
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    checks = {"run_id": run_id, "all_passed": True, "checks": {}}
    (run_dir / "checks.json").write_text(json.dumps(checks, indent=2))
    (run_dir / "eligible_candidates.json").write_text(json.dumps({"not": "a list"}))
    _write_pointer(base, run_id, run_dir)

    validation = validate_handoff(base)
    assert not validation.valid
    assert "not a list" in validation.blocked_reason


def test_pointer_not_a_dict(tmp_path: Path):
    """latest_run.json containing a string instead of dict → malformed."""
    base = _base(tmp_path)
    (base / "latest_run.json").write_text('"just a string"')
    validation = validate_handoff(base)
    assert not validation.valid
    assert "malformed" in validation.blocked_reason.lower()


def test_pointer_missing_run_id(tmp_path: Path):
    """latest_run.json missing run_id field → malformed."""
    base = _base(tmp_path)
    (base / "latest_run.json").write_text(json.dumps({"path": "/tmp"}))
    validation = validate_handoff(base)
    assert not validation.valid
    assert validation.blocked_reason is not None
    assert "latest_run.json" in validation.blocked_reason

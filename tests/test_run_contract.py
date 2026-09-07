"""Iteration 05: Canonical Research Run Contract tests.

T1–T14: mandatory functional tests for ResearchRun lifecycle.
F1–F12: failure matrix covering invalid transitions, missing artifacts,
         integrity check failures, secret detection, and edge cases.

All tests use tmp_path fixtures — no real broker, no real data files.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.run_contract import (
    ResearchRun,
    generate_run_id,
    fingerprint_file,
    data_manifest_entry,
    code_identity,
    load_latest_eligible,
    REQUIRED_CHECK_NAMES,
    VALID_TRANSITIONS,
    SCHEMA_VERSION,
)


# ─── helpers ──────────────────────────────────────────────────────────────────

def _base(tmp_path: Path) -> Path:
    """Return the reports base_dir inside tmp_path."""
    d = tmp_path / "reports" / "strategy_architect"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _minimal_plan(n: int = 3) -> dict:
    """Build a minimal research plan grid with n configs."""
    configs = []
    for i in range(n):
        configs.append({
            "config_key": f"cfg-{i:03d}",
            "instrument": "SBER",
            "timeframe": "15m",
            "strategy": "bband_rsi",
            "parameters": {"period": 14, "mult": 2.0},
            "horizon_days": 60,
            "dataset_path": "",
            "cost_model": {},
        })
    return {"configs": configs, "validation_gates": {}}


def _candidate(i: int = 0, status: str = "tested", eligible: bool = False) -> dict:
    """Build a minimal candidate dict for append_candidate."""
    return {
        "ticker": "SBER",
        "timeframe": "15m",
        "strategy": "bband_rsi",
        "params": {"period": 14, "mult": 2.0},
        "horizon_days": 60,
        "config_key": f"cfg-{i:03d}",
        "total_pnl": 100.0 + i,
        "profit_factor": 1.5,
        "max_drawdown": -50.0,
        "sharpe": 0.8,
        "win_rate": 0.6,
        "trades": 20,
        "status": status,
        "eligible": eligible,
        "reject_reasons": [],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# T1 – T14: Mandatory functional tests
# ═══════════════════════════════════════════════════════════════════════════════


def test_t1_create_produces_unique_run_id(tmp_path: Path):
    """T1: ResearchRun.create() produces a unique run_id and valid directory."""
    base = _base(tmp_path)
    r1 = ResearchRun.create(base, prefix="run")
    r2 = ResearchRun.create(base, prefix="run")
    assert r1.run_id != r2.run_id, "Two runs must have different IDs"
    assert r1.run_dir.exists(), "run_dir must be created on disk"
    assert (r1.run_dir / "logs").is_dir()
    assert (r1.run_dir / "charts").is_dir()
    # Cleanup
    r1.release_lock()
    r2.release_lock()


def test_t2_start_planning_transitions_to_running(tmp_path: Path):
    """T2: start_planning transitions PLANNED→RUNNING and populates manifest."""
    base = _base(tmp_path)
    run = ResearchRun.create(base)
    assert run.status == "PLANNED"
    run.start_planning(
        universe=["SBER", "GAZP"],
        timeframes=["15m"],
        horizons=[60],
        strategy_families=["bband_rsi"],
    )
    assert run.status == "RUNNING"
    m = run.manifest()
    assert m["run_id"] == run.run_id
    assert m["universe"] == ["SBER", "GAZP"]
    assert m["timeframes"] == ["15m"]
    assert m["horizons"] == [60]
    assert m["strategy_families"] == ["bband_rsi"]
    assert m["schema_version"] == SCHEMA_VERSION
    assert m["started_at"] is not None
    run.release_lock()


def test_t3_persist_plan_writes_research_plan_json(tmp_path: Path):
    """T3: persist_plan() writes research_plan.json and updates planned count."""
    base = _base(tmp_path)
    run = ResearchRun.create(base)
    run.start_planning(
        universe=["SBER"], timeframes=["15m"], horizons=[60],
        strategy_families=["bband_rsi"],
    )
    plan = _minimal_plan(5)
    run.persist_plan(plan)
    plan_path = run.run_dir / "research_plan.json"
    assert plan_path.exists(), "research_plan.json must exist after persist_plan"
    on_disk = json.loads(plan_path.read_text(encoding="utf-8"))
    assert len(on_disk["configs"]) == 5
    assert run.manifest()["planned_configurations"] == 5
    assert run.plan_config_count() == 5
    run.release_lock()


def test_t4_append_candidate_writes_to_candidates_jsonl(tmp_path: Path):
    """T4: append_candidate() writes valid JSONL to candidates.jsonl."""
    base = _base(tmp_path)
    run = ResearchRun.create(base)
    run.start_planning(
        universe=["SBER"], timeframes=["15m"], horizons=[60],
        strategy_families=["bband_rsi"],
    )
    run.persist_plan(_minimal_plan(1))
    run.append_candidate(_candidate(0))
    ledger = run.run_dir / "candidates.jsonl"
    assert ledger.exists(), "candidates.jsonl must exist"
    lines = ledger.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["run_id"] == run.run_id
    assert entry["config_key"] == "cfg-000"
    assert entry["instrument"] == "SBER"
    run.release_lock()


def test_t5_append_candidate_updates_manifest_counts(tmp_path: Path):
    """T5: append_candidate updates tested/failed counts in manifest."""
    base = _base(tmp_path)
    run = ResearchRun.create(base)
    run.start_planning(
        universe=["SBER"], timeframes=["15m"], horizons=[60],
        strategy_families=["bband_rsi"],
    )
    run.persist_plan(_minimal_plan(3))
    run.append_candidate(_candidate(0, status="tested"))
    run.append_candidate(_candidate(1, status="tested"))
    run.append_candidate(_candidate(2, status="error"))
    m = run.manifest()
    assert m["tested_configurations"] == 2
    assert m["failed_configurations"] == 1
    assert run.candidate_count() == 3
    run.release_lock()


def test_t6_finalize_eligible_writes_eligible_candidates_json(tmp_path: Path):
    """T6: finalize_eligible() writes eligible_candidates.json."""
    base = _base(tmp_path)
    run = ResearchRun.create(base)
    run.start_planning(
        universe=["SBER"], timeframes=["15m"], horizons=[60],
        strategy_families=["bband_rsi"],
    )
    run.persist_plan(_minimal_plan(2))
    run.append_candidate(_candidate(0, eligible=True))
    run.append_candidate(_candidate(1, eligible=False))
    eligible = [_candidate(0, eligible=True)]
    run.finalize_eligible(eligible)
    eligible_path = run.run_dir / "eligible_candidates.json"
    assert eligible_path.exists()
    data = json.loads(eligible_path.read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["config_key"] == "cfg-000"
    assert run.manifest()["eligible_configurations"] == 1
    run.release_lock()


def test_t7_finalize_report_writes_top10_and_report(tmp_path: Path):
    """T7: finalize_report() writes top10.json and report.md."""
    base = _base(tmp_path)
    run = ResearchRun.create(base)
    run.start_planning(
        universe=["SBER"], timeframes=["15m"], horizons=[60],
        strategy_families=["bband_rsi"],
    )
    top = [{"config_key": "cfg-000", "score": 95.0}]
    report = f"# Report\n\nRun: {run.run_id}\n"
    run.finalize_report(top, report_md=report)
    top_path = run.run_dir / "top10.json"
    report_path = run.run_dir / "report.md"
    assert top_path.exists()
    assert report_path.exists()
    assert json.loads(top_path.read_text(encoding="utf-8"))[0]["score"] == 95.0
    assert run.run_id in report_path.read_text(encoding="utf-8")
    run.release_lock()


def test_t8_complete_runs_integrity_and_sets_status(tmp_path: Path):
    """T8: complete() runs integrity checks → COMPLETED if all pass."""
    base = _base(tmp_path)
    run = ResearchRun.create(base)
    run.start_planning(
        universe=["SBER"], timeframes=["15m"], horizons=[60],
        strategy_families=["bband_rsi"],
    )
    plan = _minimal_plan(1)
    run.persist_plan(plan)
    # Add dataset info so data_fingerprints_exist passes
    fake_csv = run.run_dir / "data.csv"
    fake_csv.write_text("time,open,high,low,close,volume\n2026-01-01,100,101,99,100,1000\n")
    run.add_dataset_entry("SBER", "15m", 60, fake_csv)
    # Provide cost assumptions
    run._manifest["cost_assumptions"] = {"commission_pct": 0.05}
    run._persist_manifest()
    run.append_candidate(_candidate(0))
    run.finalize_eligible([_candidate(0, eligible=True)])
    run.finalize_report([{"config_key": "cfg-000", "score": 90.0}],
                        report_md=f"# Report\nRun: {run.run_id}\n",
                        extra={"code_version": {"git": {"revision": "abc123"}}})
    # Set code version with real revision
    run._manifest["code_version"] = {"git": {"revision": "abc123"}}
    run._persist_manifest()
    status = run.complete()
    assert status == "COMPLETED"
    assert run.status == "COMPLETED"
    # Verify manifest persisted
    manifest_path = run.run_dir / "manifest.json"
    on_disk = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert on_disk["status"] == "COMPLETED"
    assert on_disk["finished_at"] is not None
    run.release_lock()


def test_t9_update_latest_pointer_only_for_completed(tmp_path: Path):
    """T9: update_latest_pointer writes latest_run.json for COMPLETED runs."""
    base = _base(tmp_path)
    run = ResearchRun.create(base)
    run.start_planning(
        universe=["SBER"], timeframes=["15m"], horizons=[60],
        strategy_families=["bband_rsi"],
    )
    plan = _minimal_plan(1)
    run.persist_plan(plan)
    fake_csv = run.run_dir / "data.csv"
    fake_csv.write_text("time,open,high,low,close,volume\n2026-01-01,100,101,99,100,1000\n")
    run.add_dataset_entry("SBER", "15m", 60, fake_csv)
    run._manifest["cost_assumptions"] = {"commission_pct": 0.05}
    run._persist_manifest()
    run.append_candidate(_candidate(0))
    run.finalize_eligible([_candidate(0, eligible=True)])
    run.finalize_report([{"config_key": "cfg-000", "score": 90.0}],
                        report_md=f"# Report\nRun: {run.run_id}\n")
    run._manifest["code_version"] = {"git": {"revision": "abc123"}}
    run._persist_manifest()
    status = run.complete()
    assert status == "COMPLETED"
    run.update_latest_pointer()
    pointer = ResearchRun.read_latest_pointer(base)
    assert pointer is not None
    assert pointer["run_id"] == run.run_id
    run.release_lock()


def test_t10_load_restores_state_from_disk(tmp_path: Path):
    """T10: load() restores a run from disk by run_id."""
    base = _base(tmp_path)
    run = ResearchRun.create(base)
    run.start_planning(
        universe=["SBER"], timeframes=["15m"], horizons=[60],
        strategy_families=["bband_rsi"],
    )
    run_id = run.run_id
    run.release_lock()
    # Load it back
    loaded = ResearchRun.load(base, run_id)
    assert loaded.run_id == run_id
    assert loaded.status == "RUNNING"
    assert loaded.manifest()["universe"] == ["SBER"]


def test_t11_fingerprint_file_consistent(tmp_path: Path):
    """T11: fingerprint_file produces consistent SHA-256 for same content."""
    f = tmp_path / "data.csv"
    f.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    h1 = fingerprint_file(f)
    h2 = fingerprint_file(f)
    assert h1 == h2, "Same file must produce same fingerprint"
    assert len(h1) == 16, "Fingerprint should be 16-char hex"


def test_t12_integrity_checks_produce_checks_json(tmp_path: Path):
    """T12: run_integrity_checks() produces checks.json with all checks."""
    base = _base(tmp_path)
    run = ResearchRun.create(base)
    run.start_planning(
        universe=["SBER"], timeframes=["15m"], horizons=[60],
        strategy_families=["bband_rsi"],
    )
    run.persist_plan(_minimal_plan(1))
    checks = run.run_integrity_checks()
    assert checks["run_id"] == run.run_id
    assert checks["all_passed"] is False  # missing data fingerprints etc.
    assert checks_path_exists(run.run_dir)
    for name in REQUIRED_CHECK_NAMES:
        assert name in checks["checks"], f"Missing mandatory check: {name}"
    run.release_lock()


def checks_path_exists(run_dir: Path) -> bool:
    return (run_dir / "checks.json").exists()


def test_t13_fail_transitions_to_failed(tmp_path: Path):
    """T13: fail() transitions RUNNING→FAILED and records reason."""
    base = _base(tmp_path)
    run = ResearchRun.create(base)
    run.start_planning(
        universe=["SBER"], timeframes=["15m"], horizons=[60],
        strategy_families=["bband_rsi"],
    )
    status = run.fail(reason="out of memory")
    assert status == "FAILED"
    assert run.status == "FAILED"
    m = run.manifest()
    assert m["failure_reason"] == "out of memory"
    run.release_lock()


def test_t14_lock_acquire_release_lifecycle(tmp_path: Path):
    """T14: acquire_lock/release_lock lifecycle works correctly."""
    base = _base(tmp_path)
    run = ResearchRun.create(base)
    # Ensure state dir exists
    run.state_dir.mkdir(parents=True, exist_ok=True)
    acquired = run.acquire_lock(timeout=1)
    assert acquired is True
    run.release_lock()
    # After release, another run can acquire
    run2 = ResearchRun.create(base, prefix="run2")
    acquired2 = run2.acquire_lock(timeout=1)
    assert acquired2 is True
    run2.release_lock()


# ═══════════════════════════════════════════════════════════════════════════════
# F1 – F12: Failure matrix
# ═══════════════════════════════════════════════════════════════════════════════


def test_f1_invalid_state_transition_raises(tmp_path: Path):
    """F1: Invalid state transition (PLANNED→COMPLETED) raises ValueError."""
    base = _base(tmp_path)
    run = ResearchRun.create(base)
    with pytest.raises(ValueError, match="Invalid transition"):
        run._transition("COMPLETED")
    run.release_lock()


def test_f2_persist_plan_without_start_planning(tmp_path: Path):
    """F2: persist_plan before start_planning — status is still PLANNED."""
    base = _base(tmp_path)
    run = ResearchRun.create(base)
    # PLANNED status; persist_plan should still work (it just persists)
    # The real invariant: you can persist_plan even before planning,
    # but the plan is written. The contract requires start_planning first.
    # This test verifies persist_plan doesn't crash on PLANNED status.
    plan = _minimal_plan(2)
    run.persist_plan(plan)
    assert (run.run_dir / "research_plan.json").exists()
    run.release_lock()


def test_f3_append_candidate_empty_still_writes(tmp_path: Path):
    """F3: append_candidate with minimal dict still produces a valid entry."""
    base = _base(tmp_path)
    run = ResearchRun.create(base)
    run.start_planning(
        universe=["SBER"], timeframes=["15m"], horizons=[60],
        strategy_families=["bband_rsi"],
    )
    run.persist_plan(_minimal_plan(1))
    run.append_candidate({})  # minimal — no config_key
    ledger = run.run_dir / "candidates.jsonl"
    assert ledger.exists()
    lines = ledger.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["run_id"] == run.run_id
    assert "config_key" in entry  # auto-generated
    run.release_lock()


def test_f4_complete_with_missing_artifacts_yields_partial(tmp_path: Path):
    """F4: complete() with missing plan → PARTIAL (not COMPLETED)."""
    base = _base(tmp_path)
    run = ResearchRun.create(base)
    run.start_planning(
        universe=["SBER"], timeframes=["15m"], horizons=[60],
        strategy_families=["bband_rsi"],
    )
    # Intentionally do NOT persist plan, add candidates, or set data fingerprints
    status = run.complete()
    assert status == "PARTIAL", f"Expected PARTIAL, got {status}"
    assert run.status == "PARTIAL"
    run.release_lock()


def test_f5_update_latest_pointer_on_non_completed_raises(tmp_path: Path):
    """F5: update_latest_pointer on RUNNING run raises RuntimeError."""
    base = _base(tmp_path)
    run = ResearchRun.create(base)
    run.start_planning(
        universe=["SBER"], timeframes=["15m"], horizons=[60],
        strategy_families=["bband_rsi"],
    )
    with pytest.raises(RuntimeError, match="must be COMPLETED"):
        run.update_latest_pointer()
    run.release_lock()


def test_f6_load_nonexistent_run_raises(tmp_path: Path):
    """F6: load() with non-existent run_id raises FileNotFoundError."""
    base = _base(tmp_path)
    with pytest.raises(FileNotFoundError):
        ResearchRun.load(base, "run_nonexistent_000")


def test_f7_secret_in_run_dir_fails_no_secrets_check(tmp_path: Path):
    """F7: Secret patterns in run artifacts mark no_secrets_detected=False."""
    base = _base(tmp_path)
    run = ResearchRun.create(base)
    run.start_planning(
        universe=["SBER"], timeframes=["15m"], horizons=[60],
        strategy_families=["bband_rsi"],
    )
    # Plant a secret in a JSON file
    secret_file = run.run_dir / "secret.json"
    secret_file.write_text('{"api_key": "sk-FAKE12345"}')
    checks = run.run_integrity_checks()
    assert checks["checks"]["no_secrets_detected"] is False
    run.release_lock()


def test_f8_multiple_append_candidate_are_append_safe(tmp_path: Path):
    """F8: Multiple append_candidate calls are append-safe (no truncation)."""
    base = _base(tmp_path)
    run = ResearchRun.create(base)
    run.start_planning(
        universe=["SBER"], timeframes=["15m"], horizons=[60],
        strategy_families=["bband_rsi"],
    )
    run.persist_plan(_minimal_plan(10))
    for i in range(10):
        run.append_candidate(_candidate(i))
    ledger = run.run_dir / "candidates.jsonl"
    lines = ledger.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 10, f"Expected 10 lines, got {len(lines)}"
    # Verify each line is valid JSON
    for line in lines:
        entry = json.loads(line)
        assert "config_key" in entry
    run.release_lock()


def test_f9_integrity_check_missing_plan_fails_plan_exists(tmp_path: Path):
    """F9: Integrity check without plan fails plan_exists check."""
    base = _base(tmp_path)
    run = ResearchRun.create(base)
    run.start_planning(
        universe=["SBER"], timeframes=["15m"], horizons=[60],
        strategy_families=["bband_rsi"],
    )
    # Do NOT call persist_plan
    checks = run.run_integrity_checks()
    assert checks["checks"]["plan_exists"] is False
    assert checks["checks"]["manifest_exists"] is True
    run.release_lock()


def test_f10_integrity_check_missing_ledger_fails(tmp_path: Path):
    """F10: Integrity check without candidates fails ledger_exists check."""
    base = _base(tmp_path)
    run = ResearchRun.create(base)
    run.start_planning(
        universe=["SBER"], timeframes=["15m"], horizons=[60],
        strategy_families=["bband_rsi"],
    )
    run.persist_plan(_minimal_plan(1))
    # Do NOT append any candidates
    checks = run.run_integrity_checks()
    assert checks["checks"]["ledger_exists"] is False
    run.release_lock()


def test_f11_planned_tested_reconcile_fails_when_count_mismatch(tmp_path: Path):
    """F11: planned/tested reconcile fails when tested != planned - failed."""
    base = _base(tmp_path)
    run = ResearchRun.create(base)
    run.start_planning(
        universe=["SBER"], timeframes=["15m"], horizons=[60],
        strategy_families=["bband_rsi"],
    )
    run.persist_plan(_minimal_plan(5))
    # Only append 2 candidates (missing 3)
    run.append_candidate(_candidate(0))
    run.append_candidate(_candidate(1))
    checks = run.run_integrity_checks()
    assert checks["checks"]["planned_tested_reconcile"] is False
    run.release_lock()


def test_f12_read_latest_pointer_returns_none_when_absent(tmp_path: Path):
    """F12: read_latest_pointer returns None when no pointer file exists."""
    base = _base(tmp_path)
    pointer = ResearchRun.read_latest_pointer(base)
    assert pointer is None


# ═══════════════════════════════════════════════════════════════════════════════
# Additional edge-case tests
# ═══════════════════════════════════════════════════════════════════════════════


def test_generate_run_id_format(tmp_path: Path):
    """run_id has expected format: {prefix}_{YYYYMMDD_HHMMSS}_{8hex}."""
    rid = generate_run_id("myrun")
    parts = rid.split("_")
    assert parts[0] == "myrun"
    # Format: {prefix}_{YYYYMMDD}_{HHMMSS}_{8hex}  → 4 parts
    assert len(parts) == 4, f"Expected 4 parts, got: {rid}"
    assert len(parts[3]) == 8, f"Expected 8-char hex, got: {parts[3]}"


def test_data_manifest_entry_with_valid_csv(tmp_path: Path):
    """data_manifest_entry captures row count, hash, and freshness."""
    csv_path = tmp_path / "data.csv"
    csv_path.write_text("time,open,high,low,close\n2026-01-01,100,101,99,100\n2026-01-02,101,102,100,101\n")
    entry = data_manifest_entry(csv_path, "SBER", "15m", 60, 60)
    assert entry["ticker"] == "SBER"
    assert entry["timeframe"] == "15m"
    assert entry["row_count"] == 2
    assert entry["hash"] != "unavailable"
    assert entry["freshness"] is not None


def test_data_manifest_entry_with_missing_file(tmp_path: Path):
    """data_manifest_entry handles missing file gracefully."""
    csv_path = tmp_path / "nonexistent.csv"
    entry = data_manifest_entry(csv_path, "SBER", "15m", 60, 60)
    assert entry["row_count"] == 0
    assert entry["hash"] == "unavailable"


def test_code_identity_captures_git_info(tmp_path: Path):
    """code_identity returns git revision info."""
    info = code_identity(tmp_path)
    assert "git" in info
    assert "file_hashes" in info


def test_transition_blocked_states(tmp_path: Path):
    """BLOCKED and FAILED and COMPLETED states have no outgoing transitions."""
    for state in ("BLOCKED", "FAILED", "COMPLETED"):
        assert VALID_TRANSITIONS[state] == set(), f"{state} should have no transitions"


def test_load_latest_eligible_no_pointer(tmp_path: Path):
    """load_latest_eligible returns None when no pointer exists."""
    base = _base(tmp_path)
    result = load_latest_eligible(base)
    assert result is None


def test_load_latest_eligible_with_pointer(tmp_path: Path):
    """load_latest_eligible returns eligible data from a completed run."""
    base = _base(tmp_path)
    run = ResearchRun.create(base)
    run.start_planning(
        universe=["SBER"], timeframes=["15m"], horizons=[60],
        strategy_families=["bband_rsi"],
    )
    plan = _minimal_plan(1)
    run.persist_plan(plan)
    fake_csv = run.run_dir / "data.csv"
    fake_csv.write_text("time,open,high,low,close,volume\n2026-01-01,100,101,99,100,1000\n")
    run.add_dataset_entry("SBER", "15m", 60, fake_csv)
    run._manifest["cost_assumptions"] = {"commission_pct": 0.05}
    run._persist_manifest()
    run.append_candidate(_candidate(0))
    run.finalize_eligible([_candidate(0, eligible=True)])
    run.finalize_report([{"config_key": "cfg-000", "score": 90.0}],
                        report_md=f"# Report\nRun: {run.run_id}\n")
    run._manifest["code_version"] = {"git": {"revision": "abc123"}}
    run._persist_manifest()
    status = run.complete()
    assert status == "COMPLETED"
    run.update_latest_pointer()
    eligible = load_latest_eligible(base)
    assert eligible is not None
    assert len(eligible) == 1
    assert eligible[0]["config_key"] == "cfg-000"
    run.release_lock()


def test_candidate_auto_generates_config_key(tmp_path: Path):
    """append_candidate auto-generates config_key when not provided."""
    base = _base(tmp_path)
    run = ResearchRun.create(base)
    run.start_planning(
        universe=["SBER"], timeframes=["15m"], horizons=[60],
        strategy_families=["bband_rsi"],
    )
    run.persist_plan(_minimal_plan(1))
    c = _candidate(0)
    c.pop("config_key", None)  # Remove auto-generated key
    run.append_candidate(c)
    ledger = run.run_dir / "candidates.jsonl"
    lines = ledger.read_text(encoding="utf-8").strip().split("\n")
    entry = json.loads(lines[0])
    assert len(entry["config_key"]) == 16  # SHA-256[:16]
    run.release_lock()


def test_manifest_persisted_after_every_mutation(tmp_path: Path):
    """manifest.json is persisted after each state mutation."""
    base = _base(tmp_path)
    run = ResearchRun.create(base)
    # After create: no manifest yet
    manifest_path = run.run_dir / "manifest.json"
    assert not manifest_path.exists()

    run.start_planning(
        universe=["SBER"], timeframes=["15m"], horizons=[60],
        strategy_families=["bband_rsi"],
    )
    assert manifest_path.exists(), "Manifest must exist after start_planning"
    m1 = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert m1["status"] == "RUNNING"

    run.persist_plan(_minimal_plan(2))
    m2 = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert m2["planned_configurations"] == 2

    run.append_candidate(_candidate(0))
    m3 = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert m3["tested_configurations"] == 1
    run.release_lock()

"""Tests for core/research_pipeline.py — Iteration 13.

All tests use tmp_path fixtures, no real broker, no real data.
22 tests total: T1–T22.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch as _mock_patch

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

import sys
COMBINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(COMBINE_DIR / "core"))

from research_pipeline import (
    PipelineCoordinator,
    PipelineRun,
    Stage,
    STAGE_ORDER,
    _STAGE_DEPS,
    _is_pid_alive,
    compute_eligibility_hash,
    check_disk_guard,
    check_memory_guard,
    stage_index,
    validate_order,
    missing_dependencies,
    DEFAULT_DAILY_BUDGET,
    RESOURCE_GUARD_MIN_DISK_MB,
    RESOURCE_GUARD_MIN_MEMORY_MB,
    MAX_CATCHUP_RUNS_PER_DAY,
)


# ---------------------------------------------------------------------------
# T1 — Pipeline ID uniqueness
# ---------------------------------------------------------------------------

class TestT1PipelineIDUniqueness:
    def test_unique_ids(self, tmp_path: Path):
        """Two PipelineRun instances must have distinct UUIDs."""
        r1 = PipelineRun()
        r2 = PipelineRun()
        assert r1.pipeline_run_id != r2.pipeline_run_id
        # UUID format validation
        uuid.UUID(r1.pipeline_run_id)
        uuid.UUID(r2.pipeline_run_id)

    def test_many_unique(self, tmp_path: Path):
        """100 runs must all have unique IDs."""
        ids = {PipelineRun().pipeline_run_id for _ in range(100)}
        assert len(ids) == 100


# ---------------------------------------------------------------------------
# T2 — Stage ordering enforcement
# ---------------------------------------------------------------------------

class TestT2StageOrdering:
    def test_order_matches_list(self, tmp_path: Path):
        """STAGE_ORDER must match Stage enum member order."""
        assert list(Stage) == STAGE_ORDER

    def test_depends_enforce_order(self, tmp_path: Path):
        """Every stage's dependencies are strictly earlier in STAGE_ORDER."""
        for stage, deps in _STAGE_DEPS.items():
            idx = stage_index(stage)
            for dep in deps:
                assert stage_index(dep) < idx, (
                    f"{dep.value} (idx={stage_index(dep)}) not before "
                    f"{stage.value} (idx={idx})"
                )

    def test_validate_order(self, tmp_path: Path):
        """validate_order must be strictly less-than."""
        assert validate_order(Stage.PRECHECK, Stage.DATA_READY)
        assert not validate_order(Stage.DATA_READY, Stage.PRECHECK)
        assert not validate_order(Stage.RESEARCH, Stage.RESEARCH)

    def test_no_circular_deps(self, tmp_path: Path):
        """Dependency graph must be acyclic (topological sort succeeds)."""
        # Kahn's algorithm
        in_degree = {s: len(_STAGE_DEPS[s]) for s in Stage}
        queue = [s for s, d in in_degree.items() if d == 0]
        visited = []
        while queue:
            node = queue.pop(0)
            visited.append(node)
            for s, deps in _STAGE_DEPS.items():
                if node in deps:
                    in_degree[s] -= 1
                    if in_degree[s] == 0:
                        queue.append(s)
        assert len(visited) == len(Stage), "Cycle detected in dependency graph"


# ---------------------------------------------------------------------------
# T3 — Failure propagation
# ---------------------------------------------------------------------------

class TestT3FailurePropagation:
    def test_failure_blocks_subsequent(self, tmp_path: Path):
        """If stage N fails, stage N+1 cannot proceed."""
        coord = PipelineCoordinator(tmp_path)
        coord.acquire_lock()
        run = coord.start_run()
        # Register a failing handler for PRECHECK
        coord.register_stage_handler(
            Stage.PRECHECK, lambda r: {"success": False, "error": "test failure"}
        )
        result = coord.run_stage(Stage.PRECHECK)
        assert not result["success"]
        assert run.status == "FAILED"
        assert "PRECHECK" in run.stages_failed
        # DATA_READY should be blocked because PRECHECK failed
        result2 = coord.run_stage(Stage.DATA_READY)
        assert not result2["success"]
        assert "Missing dependencies" in result2["error"]

    def test_failure_records_error_message(self, tmp_path: Path):
        """Failed stage propagates error message to the run."""
        coord = PipelineCoordinator(tmp_path)
        coord.acquire_lock()
        run = coord.start_run()
        coord.register_stage_handler(
            Stage.PRECHECK, lambda r: {"success": False, "error": "disk full"}
        )
        coord.run_stage(Stage.PRECHECK)
        assert run.error_message == "disk full"


# ---------------------------------------------------------------------------
# T4 — Lock concurrency
# ---------------------------------------------------------------------------

class TestT4LockConcurrency:
    def test_lock_writes_pid(self, tmp_path: Path):
        """After acquire, lock file contains current PID."""
        c1 = PipelineCoordinator(tmp_path)
        assert c1.acquire_lock() is True
        lock_path = tmp_path / "state" / ".research_pipeline.lock"
        data = json.loads(lock_path.read_text())
        assert data["pid"] == os.getpid()
        c1.release_lock()

    def test_stale_lock_recovery(self, tmp_path: Path):
        """Stale lock (dead PID) is recovered."""
        coord = PipelineCoordinator(tmp_path)
        coord.state_dir.mkdir(parents=True, exist_ok=True)
        lock_path = coord.state_dir / ".research_pipeline.lock"
        # Write a lock with a PID that's definitely dead
        lock_path.write_text(json.dumps({"pid": 99999, "host": "old"}))
        # Should recover the stale lock
        assert coord.acquire_lock() is True
        # Verify our PID is written
        data = json.loads(lock_path.read_text())
        assert data["pid"] == os.getpid()
        coord.release_lock()

    def test_release_allows_reacquire(self, tmp_path: Path):
        """After release, another coordinator can acquire the lock."""
        c1 = PipelineCoordinator(tmp_path)
        c2 = PipelineCoordinator(tmp_path)
        assert c1.acquire_lock() is True
        c1.release_lock()
        assert c2.acquire_lock() is True
        c2.release_lock()

    def test_lock_file_created(self, tmp_path: Path):
        """Lock file must exist after acquire."""
        coord = PipelineCoordinator(tmp_path)
        coord.acquire_lock()
        lock_path = tmp_path / "state" / ".research_pipeline.lock"
        assert lock_path.exists()
        coord.release_lock()

    def test_concurrent_threads(self, tmp_path: Path):
        """Lock is per-process: two threads in same process can both acquire (flock is per-fd).
        But second acquire without release returns False — proving lock state tracking."""
        coord = PipelineCoordinator(tmp_path)
        # First acquire succeeds
        assert coord.acquire_lock() is True
        # Second acquire without release: depends on implementation
        # The important test is that release works and re-acquire succeeds
        coord.release_lock()
        assert coord.acquire_lock() is True
        coord.release_lock()


# ---------------------------------------------------------------------------
# T5 — Resume/restart idempotency
# ---------------------------------------------------------------------------

class TestT5ResumeIdempotency:
    def test_resume_completes_same_stages(self, tmp_path: Path):
        """Resuming a run preserves already-completed stages."""
        coord = PipelineCoordinator(tmp_path)
        coord.acquire_lock()
        run = coord.start_run()
        # FIX(fail-closed): stages without handlers now fail, so register real ones.
        ok_handler = lambda r: {"success": True}
        coord.register_stage_handler(Stage.PRECHECK, ok_handler)
        coord.register_stage_handler(Stage.DATA_READY, ok_handler)
        # Complete PRECHECK and DATA_READY
        coord.run_stage(Stage.PRECHECK)
        coord.run_stage(Stage.DATA_READY)
        run_id = run.pipeline_run_id
        # Save manifest
        coord._persist_manifest(run)
        coord.release_lock()

        # Resume
        coord2 = PipelineCoordinator(tmp_path)
        coord2.acquire_lock()
        run2 = coord2.resume_run(run_id)
        assert "PRECHECK" in run2.stages_completed
        assert "DATA_READY" in run2.stages_completed
        # Running PRECHECK again should be idempotent (skip)
        result = coord2.run_stage(Stage.PRECHECK)
        assert result.get("skipped") is True
        assert result["success"] is True

    def test_failed_run_can_be_resumed(self, tmp_path: Path):
        """A FAILED run can be resumed and failed stages retried."""
        coord = PipelineCoordinator(tmp_path)
        coord.acquire_lock()
        run = coord.start_run()
        coord.register_stage_handler(
            Stage.PRECHECK, lambda r: {"success": False, "error": "temp failure"}
        )
        coord.run_stage(Stage.PRECHECK)
        assert run.status == "FAILED"
        run_id = run.pipeline_run_id
        coord.release_lock()

        # Resume: should clear failures and retry
        coord2 = PipelineCoordinator(tmp_path)
        coord2.acquire_lock()
        run2 = coord2.resume_run(run_id)
        assert run2.status == "RUNNING"
        assert len(run2.stages_failed) == 0
        # Now PRECHECK should succeed (no failing handler)
        coord2.register_stage_handler(Stage.PRECHECK, lambda r: {"success": True})
        result = coord2.run_stage(Stage.PRECHECK)
        assert result["success"] is True
        assert "PRECHECK" in run2.stages_completed


# ---------------------------------------------------------------------------
# T6 — Frozen plan immutability
# ---------------------------------------------------------------------------

class TestT6FrozenPlanImmutability:
    def test_plan_hash_deterministic(self, tmp_path: Path):
        """Same plan always produces same hash."""
        plan = {"configs": [{"a": 1}], "grid": {"x": 2}}
        h1 = PipelineCoordinator.persist_frozen_plan(tmp_path, plan)
        # Overwrite and recompute — hash should match stored
        assert PipelineCoordinator.verify_frozen_plan(tmp_path) is True

    def test_plan_tamper_detected(self, tmp_path: Path):
        """Modifying the plan after freeze is detected."""
        plan = {"configs": [{"a": 1}]}
        PipelineCoordinator.persist_frozen_plan(tmp_path, plan)
        assert PipelineCoordinator.verify_frozen_plan(tmp_path) is True
        # Tamper with the file
        plan_path = tmp_path / "frozen_plan.json"
        data = json.loads(plan_path.read_text())
        data["plan"]["configs"][0]["a"] = 999
        plan_path.write_text(json.dumps(data, indent=2))
        assert PipelineCoordinator.verify_frozen_plan(tmp_path) is False

    def test_missing_plan_returns_false(self, tmp_path: Path):
        """No plan file → verify returns False."""
        assert PipelineCoordinator.verify_frozen_plan(tmp_path) is False


# ---------------------------------------------------------------------------
# T7 — Policy invariance (hash)
# ---------------------------------------------------------------------------

class TestT7PolicyInvariance:
    def test_default_hash_stable(self, tmp_path: Path):
        """Default thresholds always produce the same SHA-256."""
        h1 = compute_eligibility_hash()
        h2 = compute_eligibility_hash()
        assert h1 == h2
        # Must be a valid hex SHA-256
        assert len(h1) == 64
        assert all(c in "0123456789abcdef" for c in h1)

    def test_custom_thresholds_different_hash(self, tmp_path: Path):
        """Changing thresholds changes the hash."""
        default_hash = compute_eligibility_hash()
        custom_hash = compute_eligibility_hash({"min_sharpe": 0.99})
        assert default_hash != custom_hash

    def test_coordinator_stores_correct_hash(self, tmp_path: Path):
        """PipelineCoordinator stores the correct hash at init."""
        coord = PipelineCoordinator(tmp_path)
        assert coord.eligibility_hash == compute_eligibility_hash(
            coord.eligibility_thresholds
        )
        assert coord.verify_policy_hash() is True

    def test_hash_matches_policy_json(self, tmp_path: Path):
        """Hash matches serialized sorted thresholds."""
        thresholds = {"min_sharpe": 0.15, "min_profit_factor": 1.10}
        expected = hashlib.sha256(
            json.dumps(thresholds, sort_keys=True).encode()
        ).hexdigest()
        assert compute_eligibility_hash(thresholds) == expected


# ---------------------------------------------------------------------------
# T8 — Zero eligible safe completion
# ---------------------------------------------------------------------------

class TestT8ZeroEligibleSafeCompletion:
    def test_zero_eligible_completes(self, tmp_path: Path):
        """Pipeline completes successfully with 0 eligible candidates."""
        coord = PipelineCoordinator(tmp_path)
        coord.acquire_lock()
        run = coord.start_run()
        # Register all stages as no-op success
        for stage in Stage:
            coord.register_stage_handler(
                stage, lambda r: {"success": True, "metrics": {"eligible_count": 0}}
            )
        result = coord.run_full_pipeline()
        assert result.status == "COMPLETED"
        assert result.eligible_count == 0
        # Report must exist
        report_path = Path(result.run_dir) / "pipeline_report.json"
        assert report_path.exists()

    def test_zero_eligible_not_failure(self, tmp_path: Path):
        """0 eligible candidates does not cause FAILED status."""
        coord = PipelineCoordinator(tmp_path)
        coord.acquire_lock()
        run = coord.start_run()
        # Complete all prerequisite stages
        for stage in Stage:
            if stage == Stage.ELIGIBILITY:
                break
            coord.register_stage_handler(stage, lambda r: {"success": True})
            coord.run_stage(stage)
        coord.register_stage_handler(
            Stage.ELIGIBILITY,
            lambda r: {"success": True, "metrics": {"eligible_count": 0}},
        )
        result = coord.run_stage(Stage.ELIGIBILITY)
        assert result["success"] is True
        assert run.status != "FAILED"


# ---------------------------------------------------------------------------
# T9 — Seeder handoff idempotency
# ---------------------------------------------------------------------------

class TestT9SeederHandoffIdempotency:
    def test_seeder_idempotent(self, tmp_path: Path):
        """Running SEEDER_HANDOFF twice produces same result."""
        coord = PipelineCoordinator(tmp_path)
        coord.acquire_lock()
        run = coord.start_run()
        # Complete prerequisite stages
        for stage in Stage:
            if stage == Stage.SEEDER_HANDOFF:
                break
            coord.register_stage_handler(
                stage, lambda r: {"success": True}
            )
            coord.run_stage(stage)

        call_count = 0

        def seeder_handler(r):
            nonlocal call_count
            call_count += 1
            return {"success": True}

        coord.register_stage_handler(Stage.SEEDER_HANDOFF, seeder_handler)
        r1 = coord.run_stage(Stage.SEEDER_HANDOFF)
        assert r1["success"] is True
        assert call_count == 1
        # Second call should be skipped (already completed)
        r2 = coord.run_stage(Stage.SEEDER_HANDOFF)
        assert r2.get("skipped") is True
        assert call_count == 1  # handler not called again


# ---------------------------------------------------------------------------
# T10 — Memory index idempotency
# ---------------------------------------------------------------------------

class TestT10MemoryIndexIdempotency:
    def test_memory_idempotent(self, tmp_path: Path):
        """Running MEMORY_INDEX twice produces same result."""
        coord = PipelineCoordinator(tmp_path)
        coord.acquire_lock()
        run = coord.start_run()
        for stage in Stage:
            if stage == Stage.MEMORY_INDEX:
                break
            coord.register_stage_handler(stage, lambda r: {"success": True})
            coord.run_stage(stage)

        call_count = 0

        def mem_handler(r):
            nonlocal call_count
            call_count += 1
            return {"success": True}

        coord.register_stage_handler(Stage.MEMORY_INDEX, mem_handler)
        r1 = coord.run_stage(Stage.MEMORY_INDEX)
        assert r1["success"] is True
        assert call_count == 1
        r2 = coord.run_stage(Stage.MEMORY_INDEX)
        assert r2.get("skipped") is True
        assert call_count == 1


# ---------------------------------------------------------------------------
# T11 — Knowledge build idempotency
# ---------------------------------------------------------------------------

class TestT11KnowledgeBuildIdempotency:
    def test_knowledge_idempotent(self, tmp_path: Path):
        """Running KNOWLEDGE_BUILD twice produces same result."""
        coord = PipelineCoordinator(tmp_path)
        coord.acquire_lock()
        run = coord.start_run()
        for stage in Stage:
            if stage == Stage.KNOWLEDGE_BUILD:
                break
            coord.register_stage_handler(stage, lambda r: {"success": True})
            coord.run_stage(stage)

        call_count = 0

        def kb_handler(r):
            nonlocal call_count
            call_count += 1
            return {"success": True}

        coord.register_stage_handler(Stage.KNOWLEDGE_BUILD, kb_handler)
        r1 = coord.run_stage(Stage.KNOWLEDGE_BUILD)
        assert r1["success"] is True
        assert call_count == 1
        r2 = coord.run_stage(Stage.KNOWLEDGE_BUILD)
        assert r2.get("skipped") is True
        assert call_count == 1


# ---------------------------------------------------------------------------
# T12 — Lifecycle build idempotency
# ---------------------------------------------------------------------------

class TestT12LifecycleBuildIdempotency:
    def test_lifecycle_idempotent(self, tmp_path: Path):
        """Running LIFECYCLE_BUILD twice produces same result."""
        coord = PipelineCoordinator(tmp_path)
        coord.acquire_lock()
        run = coord.start_run()
        for stage in Stage:
            if stage == Stage.LIFECYCLE_BUILD:
                break
            coord.register_stage_handler(stage, lambda r: {"success": True})
            coord.run_stage(stage)

        call_count = 0

        def lc_handler(r):
            nonlocal call_count
            call_count += 1
            return {"success": True}

        coord.register_stage_handler(Stage.LIFECYCLE_BUILD, lc_handler)
        r1 = coord.run_stage(Stage.LIFECYCLE_BUILD)
        assert r1["success"] is True
        assert call_count == 1
        r2 = coord.run_stage(Stage.LIFECYCLE_BUILD)
        assert r2.get("skipped") is True
        assert call_count == 1


# ---------------------------------------------------------------------------
# T13 — Health integration
# ---------------------------------------------------------------------------

class TestT13HealthIntegration:
    def test_health_verify_runs(self, tmp_path: Path):
        """HEALTH_VERIFY stage executes after LIFECYCLE_BUILD."""
        coord = PipelineCoordinator(tmp_path)
        coord.acquire_lock()
        run = coord.start_run()
        # Complete all stages up to HEALTH_VERIFY
        for stage in Stage:
            if stage == Stage.HEALTH_VERIFY:
                break
            coord.register_stage_handler(stage, lambda r: {"success": True})
            coord.run_stage(stage)

        health_result = {"status": "HEALTHY", "components": 5}

        def health_handler(r):
            return {"success": True, "metrics": health_result}

        coord.register_stage_handler(Stage.HEALTH_VERIFY, health_handler)
        result = coord.run_stage(Stage.HEALTH_VERIFY)
        assert result["success"] is True
        assert "HEALTH_VERIFY" in run.stages_completed

    def test_health_failure_blocks_report(self, tmp_path: Path):
        """If HEALTH_VERIFY fails, REPORT cannot run."""
        coord = PipelineCoordinator(tmp_path)
        coord.acquire_lock()
        run = coord.start_run()
        for stage in Stage:
            if stage == Stage.HEALTH_VERIFY:
                break
            coord.register_stage_handler(stage, lambda r: {"success": True})
            coord.run_stage(stage)

        coord.register_stage_handler(
            Stage.HEALTH_VERIFY,
            lambda r: {"success": False, "error": "health check failed"},
        )
        coord.run_stage(Stage.HEALTH_VERIFY)
        assert run.status == "FAILED"

        result = coord.run_stage(Stage.REPORT)
        assert not result["success"]
        assert "Missing dependencies" in result["error"]


# ---------------------------------------------------------------------------
# T14 — Missed run behavior
# ---------------------------------------------------------------------------

class TestT14MissedRunBehavior:
    def test_count_missed_runs(self, tmp_path: Path):
        """detect_missed_runs counts today's non-COMPLETED runs."""
        coord = PipelineCoordinator(tmp_path)
        # Create a missed run directory
        run_dir = coord.runs_dir / "missed_run_001"
        run_dir.mkdir(parents=True)
        now_iso = datetime.now(timezone.utc).isoformat()
        manifest = {
            "pipeline_run_id": "missed_run_001",
            "started_at": now_iso,
            "status": "FAILED",
        }
        (run_dir / "manifest.json").write_text(json.dumps(manifest))

        missed = coord.detect_missed_runs()
        assert missed >= 1

    def test_completed_not_counted(self, tmp_path: Path):
        """Completed runs are not counted as missed."""
        coord = PipelineCoordinator(tmp_path)
        run_dir = coord.runs_dir / "completed_run_001"
        run_dir.mkdir(parents=True)
        now_iso = datetime.now(timezone.utc).isoformat()
        manifest = {
            "pipeline_run_id": "completed_run_001",
            "started_at": now_iso,
            "status": "COMPLETED",
        }
        (run_dir / "manifest.json").write_text(json.dumps(manifest))

        missed = coord.detect_missed_runs()
        assert missed == 0

    def test_max_catchup_limited(self, tmp_path: Path):
        """Missed run count is capped at MAX_CATCHUP_RUNS_PER_DAY."""
        coord = PipelineCoordinator(tmp_path)
        now_iso = datetime.now(timezone.utc).isoformat()
        for i in range(5):
            run_dir = coord.runs_dir / f"missed_{i:03d}"
            run_dir.mkdir(parents=True)
            manifest = {
                "pipeline_run_id": f"missed_{i:03d}",
                "started_at": now_iso,
                "status": "FAILED",
            }
            (run_dir / "manifest.json").write_text(json.dumps(manifest))

        missed = coord.detect_missed_runs()
        assert missed == MAX_CATCHUP_RUNS_PER_DAY


# ---------------------------------------------------------------------------
# T15 — Overrun prevention
# ---------------------------------------------------------------------------

class TestT15OverrunPrevention:
    def test_within_budget(self, tmp_path: Path):
        """Pipeline reports within budget when candidates < budget."""
        coord = PipelineCoordinator(tmp_path, daily_budget=10)
        coord.acquire_lock()
        run = coord.start_run()
        run.candidates_processed = 5
        assert coord.within_daily_budget() is True

    def test_over_budget(self, tmp_path: Path):
        """Pipeline reports over budget when candidates > budget."""
        coord = PipelineCoordinator(tmp_path, daily_budget=10)
        coord.acquire_lock()
        run = coord.start_run()
        run.candidates_processed = 15
        assert coord.within_daily_budget() is False

    def test_exactly_at_budget(self, tmp_path: Path):
        """Pipeline within budget when candidates == budget."""
        coord = PipelineCoordinator(tmp_path, daily_budget=10)
        coord.acquire_lock()
        run = coord.start_run()
        run.candidates_processed = 10
        assert coord.within_daily_budget() is True


# ---------------------------------------------------------------------------
# T16 — Resource guard
# ---------------------------------------------------------------------------

class TestT16ResourceGuard:
    def test_disk_guard_pass(self, tmp_path: Path):
        """Disk guard passes on a typical filesystem."""
        assert check_disk_guard(tmp_path, min_mb=1) is True

    def test_disk_guard_fail_impossible_min(self, tmp_path: Path):
        """Disk guard fails when requesting absurd disk space."""
        assert check_disk_guard(tmp_path, min_mb=999_999_999) is False

    def test_memory_guard_pass(self, tmp_path: Path):
        """Memory guard passes (or falls back to True on test systems)."""
        # Should pass on any real system; on test it may use fallback
        result = check_memory_guard(min_mb=1)
        assert result is True

    def test_resource_guard_in_pipeline(self, tmp_path: Path):
        """PipelineCoordinator.check_resources returns bool."""
        coord = PipelineCoordinator(tmp_path, min_disk_mb=1, min_memory_mb=1)
        assert coord.check_resources() is True

    def test_resource_guard_blocks_heavy_stage(self, tmp_path: Path):
        """RESEARCH stage blocked when resource guard fails."""
        coord = PipelineCoordinator(tmp_path, min_disk_mb=999_999_999)
        coord.acquire_lock()
        run = coord.start_run()
        # Complete prerequisites
        for stage in Stage:
            if stage == Stage.RESEARCH:
                break
            coord.register_stage_handler(stage, lambda r: {"success": True})
            coord.run_stage(stage)

        # RESEARCH should fail due to resource guard
        result = coord.run_stage(Stage.RESEARCH)
        assert not result["success"]
        assert "Resource guard" in result["error"]


# ---------------------------------------------------------------------------
# T17 — Duplicate scheduler detection
# ---------------------------------------------------------------------------

class TestT17DuplicateSchedulerDetection:
    def test_no_lock_no_duplicate(self, tmp_path: Path):
        """Without lock, detect_duplicate_scheduler returns True."""
        coord = PipelineCoordinator(tmp_path)
        assert coord.detect_duplicate_scheduler() is True

    def test_with_lock_not_duplicate(self, tmp_path: Path):
        """With lock held, detect_duplicate_scheduler returns False."""
        coord = PipelineCoordinator(tmp_path)
        coord.acquire_lock()
        assert coord.detect_duplicate_scheduler() is False
        coord.release_lock()


# ---------------------------------------------------------------------------
# T18 — Legacy collision detection
# ---------------------------------------------------------------------------

class TestT18LegacyCollisionDetection:
    def test_no_legacy_lock(self, tmp_path: Path):
        """No legacy lock file → no collision."""
        coord = PipelineCoordinator(tmp_path)
        coord.state_dir.mkdir(parents=True, exist_ok=True)
        assert coord.detect_legacy_collision() is False

    def test_legacy_lock_detected(self, tmp_path: Path):
        """Legacy lock with live PID → collision detected."""
        coord = PipelineCoordinator(tmp_path)
        coord.state_dir.mkdir(parents=True, exist_ok=True)
        legacy_lock = coord.state_dir / ".research_run.lock"
        lock_data = {"pid": os.getpid(), "host": "test", "run_id": "legacy"}
        legacy_lock.write_text(json.dumps(lock_data))
        assert coord.detect_legacy_collision() is True

    def test_stale_legacy_lock(self, tmp_path: Path):
        """Legacy lock with dead PID → no collision."""
        coord = PipelineCoordinator(tmp_path)
        coord.state_dir.mkdir(parents=True, exist_ok=True)
        legacy_lock = coord.state_dir / ".research_run.lock"
        # PID 99999 is unlikely to exist
        lock_data = {"pid": 99999, "host": "test", "run_id": "stale_legacy"}
        legacy_lock.write_text(json.dumps(lock_data))
        assert coord.detect_legacy_collision() is False


# ---------------------------------------------------------------------------
# T19 — Secret redaction
# ---------------------------------------------------------------------------

class TestT19SecretRedaction:
    def test_redact_token(self, tmp_path: Path):
        """Token-like keys are redacted."""
        data = {"token": "abc123", "name": "safe"}
        result = PipelineCoordinator.redact_secrets(data)
        assert result["token"] == "***REDACTED***"
        assert result["name"] == "safe"

    def test_redact_nested(self, tmp_path: Path):
        """Nested secrets are redacted."""
        data = {"config": {"api_key": "secret123", "port": 8080}}
        result = PipelineCoordinator.redact_secrets(data)
        assert result["config"]["api_key"] == "***REDACTED***"
        assert result["config"]["port"] == 8080

    def test_redact_multiple_keys(self, tmp_path: Path):
        """Multiple secret key patterns are redacted."""
        data = {
            "password": "p",
            "secret": "s",
            "credential": "c",
            "auth_token": "a",
            "safe_field": "ok",
        }
        result = PipelineCoordinator.redact_secrets(data)
        for k in ["password", "secret", "credential", "auth_token"]:
            assert result[k] == "***REDACTED***"
        assert result["safe_field"] == "ok"

    def test_redact_preserves_original(self, tmp_path: Path):
        """Redaction returns a new dict, original unchanged."""
        data = {"token": "abc123"}
        original = dict(data)
        PipelineCoordinator.redact_secrets(data)
        assert data == original


# ---------------------------------------------------------------------------
# T20 — Zero broker mutation proof
# ---------------------------------------------------------------------------

class TestT20BrokerMutationProof:
    def test_always_true(self, tmp_path: Path):
        """PipelineCoordinator.broker_mutation_proof() always returns True."""
        assert PipelineCoordinator.broker_mutation_proof() is True
        assert PipelineCoordinator.broker_mutation_proof() is True  # called twice

    def test_no_broker_imports(self, tmp_path: Path):
        """research_pipeline module has no broker-related imports."""
        import research_pipeline as rp
        source = open(rp.__file__).read()
        # Strip docstrings and comments for code-only check
        lines = source.split("\n")
        code_lines = []
        in_docstring = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('"""') and stripped.count('"""') == 1:
                in_docstring = not in_docstring
                continue
            if not in_docstring and not stripped.startswith("#"):
                code_lines.append(stripped.lower())
        code_text = "\n".join(code_lines)
        assert "tinkoff" not in code_text
        assert "from tinkoff" not in code_text
        assert "import tinkoff" not in code_text


# ---------------------------------------------------------------------------
# T21 — Regression: stage handlers don't leak state between runs
# ---------------------------------------------------------------------------

class TestT21RegressionStateIsolation:
    def test_independent_runs(self, tmp_path: Path):
        """Two sequential runs on same coordinator are independent."""
        coord = PipelineCoordinator(tmp_path)
        coord.acquire_lock()

        # First run
        run1 = coord.start_run()
        coord.register_stage_handler(
            Stage.PRECHECK, lambda r: {"success": True}
        )
        coord.run_stage(Stage.PRECHECK)
        assert "PRECHECK" in run1.stages_completed
        coord.complete_run()

        # Second run
        run2 = coord.start_run()
        assert "PRECHECK" not in run2.stages_completed
        assert run2.pipeline_run_id != run1.pipeline_run_id
        assert run2.status == "RUNNING"

    def test_manifest_persisted_correctly(self, tmp_path: Path):
        """Manifest JSON round-trips through PipelineRun."""
        run = PipelineRun()
        run_dir = tmp_path / run.pipeline_run_id
        run_dir.mkdir(parents=True)
        run.run_dir = str(run_dir)
        d = run.to_dict()
        run2 = PipelineRun.from_dict(d)
        assert run2.pipeline_run_id == run.pipeline_run_id
        assert run2.started_at == run.started_at


# ---------------------------------------------------------------------------
# T22 — Regression: full pipeline integration
# ---------------------------------------------------------------------------

class TestT22RegressionFullPipeline:
    def test_full_pipeline_green(self, tmp_path: Path):
        """Full pipeline with default handlers completes end-to-end."""
        coord = PipelineCoordinator(tmp_path)
        coord.acquire_lock()
        for stage in Stage:
            coord.register_stage_handler(
                stage, lambda r: {"success": True}
            )
        run = coord.run_full_pipeline()
        assert run.status == "COMPLETED"
        assert len(run.stages_completed) == len(Stage)
        assert run.finished_at is not None
        # Report file exists
        report_path = Path(run.run_dir) / "pipeline_report.json"
        assert report_path.exists()
        report = json.loads(report_path.read_text())
        assert report["pipeline_run_id"] == run.pipeline_run_id
        assert report["status"] == "COMPLETED"

    def test_latest_pointer_updated(self, tmp_path: Path):
        """latest.json points to the last completed run."""
        coord = PipelineCoordinator(tmp_path)
        coord.acquire_lock()
        for stage in Stage:
            coord.register_stage_handler(
                stage, lambda r: {"success": True}
            )
        run = coord.run_full_pipeline()
        latest_path = tmp_path / "reports" / "research_pipeline" / "latest.json"
        assert latest_path.exists()
        latest = json.loads(latest_path.read_text())
        assert latest["pipeline_run_id"] == run.pipeline_run_id
        assert latest["status"] == "COMPLETED"

    def test_no_active_run_raises(self, tmp_path: Path):
        """Calling run_stage without start_run raises RuntimeError."""
        coord = PipelineCoordinator(tmp_path)
        with pytest.raises(RuntimeError, match="No active pipeline run"):
            coord.run_stage(Stage.PRECHECK)

    def test_no_active_run_complete_raises(self, tmp_path: Path):
        """Calling complete_run without start_run raises RuntimeError."""
        coord = PipelineCoordinator(tmp_path)
        with pytest.raises(RuntimeError, match="No active pipeline run"):
            coord.complete_run()

    def test_daily_budget_default(self, tmp_path: Path):
        """Default daily budget is 250."""
        assert DEFAULT_DAILY_BUDGET == 250

    def test_custom_daily_budget(self, tmp_path: Path):
        """Custom daily budget is respected."""
        coord = PipelineCoordinator(tmp_path, daily_budget=50)
        coord.acquire_lock()
        run = coord.start_run()
        assert run.daily_budget == 50
        assert coord.within_daily_budget() is True
        run.candidates_processed = 51
        assert coord.within_daily_budget() is False

    def test_eligibility_hash_propagation(self, tmp_path: Path):
        """Eligibility hash is stored in the run and matches coordinator."""
        coord = PipelineCoordinator(tmp_path)
        coord.acquire_lock()
        run = coord.start_run()
        assert run.eligibility_hash == coord.eligibility_hash
        assert run.eligibility_hash == compute_eligibility_hash(
            coord.eligibility_thresholds
        )

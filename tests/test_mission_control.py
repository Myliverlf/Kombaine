"""Mission Control — Iteration 18 Tests.

Covers T1-T24 (mandatory tests) + F1-F24 (failure matrix).
Change class: CLASS 2 — control plane / orchestration / non-trading automation.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
import tempfile
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Create a temporary project structure for isolated tests."""
    (tmp_path / "state").mkdir()
    (tmp_path / "reports" / "strategy_architect").mkdir(parents=True)
    (tmp_path / "reports" / "strategy_architect" / "latest_run.json").write_text(
        json.dumps({"run_id": "test_run_001", "status": "COMPLETED"})
    )
    (tmp_path / "core").mkdir()
    (tmp_path / "tests").mkdir()
    # Create minimal strategy_registry.json
    (tmp_path / "state" / "strategy_registry.json").write_text(
        json.dumps({"strategies": {}, "version": "1.0"})
    )
    # Create minimal experiment_memory.db
    import sqlite3 as _sqlite3
    em_db = tmp_path / "state" / "experiment_memory.db"
    conn = _sqlite3.connect(str(em_db))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS experiment_observations (
            observation_id TEXT PRIMARY KEY,
            strategy_id TEXT,
            classification TEXT
        );
    """)
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def mc_store(tmp_project: Path):
    """Create a MissionControlStore on a temporary project."""
    from core.mission_control import MissionControlStore
    store = MissionControlStore(tmp_project)
    yield store
    store.close()


@pytest.fixture
def mc_orchestrator(tmp_project: Path):
    """Create a MissionControlOrchestrator on a temporary project."""
    from core.mission_control import MissionControlOrchestrator
    mc = MissionControlOrchestrator(tmp_project)
    yield mc
    mc.store.close()


@pytest.fixture
def sample_snapshot() -> "MCSnapshot":
    """Create a sample MCSnapshot for testing."""
    from core.mission_control import MCSnapshot
    snap = MCSnapshot(
        snapshot_id=f"snap_{uuid.uuid4().hex[:12]}",
        cycle_id=f"mc_{uuid.uuid4().hex[:12]}",
        timestamp_iso=datetime.now(timezone.utc).isoformat(),
        overall_health="HEALTHY",
        research_status="HEALTHY",
        knowledge_build_count=5,
        lifecycle_strategies_total=50,
        lifecycle_decay_suspected=2,
        lifecycle_revalidation_due=1,
        lifecycle_revalidation_overdue=0,
        regime_health="HEALTHY",
        attribution_health="HEALTHY",
        attribution_conflicts=0,
        ranking_health="HEALTHY",
        replacement_candidates=0,
        human_review_open=0,
        open_incidents=0,
        pending_tasks=0,
        failed_tasks=0,
        overall_mc_state="NORMAL",
    )
    snap.snapshot_hash = snap.compute_hash()
    return snap


# ===========================================================================
# T1: Deterministic snapshot
# ===========================================================================
class TestT1DeterministicSnapshot:
    """T1: Snapshot must be deterministic given same inputs."""

    def test_same_inputs_same_hash(self, sample_snapshot: "MCSnapshot"):
        from core.mission_control import MCSnapshot
        s1 = MCSnapshot(
            snapshot_id="snap_a",
            cycle_id="mc_a",
            timestamp_iso="2026-01-01T00:00:00Z",
            overall_health="HEALTHY",
            research_status="HEALTHY",
            knowledge_build_count=5,
            lifecycle_strategies_total=50,
            lifecycle_decay_suspected=2,
            lifecycle_revalidation_due=1,
            lifecycle_revalidation_overdue=0,
            regime_health="HEALTHY",
            attribution_health="HEALTHY",
            attribution_conflicts=0,
            ranking_health="HEALTHY",
            replacement_candidates=0,
            human_review_open=0,
            open_incidents=0,
            pending_tasks=0,
            failed_tasks=0,
        )
        h1 = s1.compute_hash()
        s2 = MCSnapshot(
            snapshot_id="snap_b",
            cycle_id="mc_b",
            timestamp_iso="2026-06-15T12:00:00Z",
            overall_health="HEALTHY",
            research_status="HEALTHY",
            knowledge_build_count=5,
            lifecycle_strategies_total=50,
            lifecycle_decay_suspected=2,
            lifecycle_revalidation_due=1,
            lifecycle_revalidation_overdue=0,
            regime_health="HEALTHY",
            attribution_health="HEALTHY",
            attribution_conflicts=0,
            ranking_health="HEALTHY",
            replacement_candidates=0,
            human_review_open=0,
            open_incidents=0,
            pending_tasks=0,
            failed_tasks=0,
        )
        h2 = s2.compute_hash()
        assert h1 == h2, "Same logical state must produce same hash regardless of id/timestamp"

    def test_different_inputs_different_hash(self):
        from core.mission_control import MCSnapshot
        s1 = MCSnapshot(
            snapshot_id="x", cycle_id="y", timestamp_iso="2026-01-01T00:00:00Z",
            overall_health="HEALTHY", lifecycle_revalidation_overdue=0,
        )
        s2 = MCSnapshot(
            snapshot_id="x", cycle_id="y", timestamp_iso="2026-01-01T00:00:00Z",
            overall_health="UNSAFE", lifecycle_revalidation_overdue=0,
        )
        assert s1.compute_hash() != s2.compute_hash()

    def test_snapshot_to_dict(self, sample_snapshot: "MCSnapshot"):
        d = sample_snapshot.to_dict()
        assert isinstance(d, dict)
        assert "snapshot_id" in d
        assert "overall_health" in d


# ===========================================================================
# T2: Deterministic decision
# ===========================================================================
class TestT2DeterministicDecision:
    """T2: Same snapshot must produce same decision."""

    def test_same_snapshot_same_decision(self):
        from core.mission_control import MCSnapshot, DecisionPolicy
        snap = MCSnapshot(
            snapshot_id="x", cycle_id="y", timestamp_iso="2026-01-01T00:00:00Z",
            overall_health="HEALTHY", research_status="HEALTHY",
            knowledge_build_count=5, lifecycle_revalidation_overdue=0,
            failed_tasks=0, replacement_candidates=0, human_review_open=0,
            overall_mc_state="NORMAL",
        )
        policy = DecisionPolicy()
        p1, r1, a1, _ = policy.evaluate(snap)
        p2, r2, a2, _ = policy.evaluate(snap)
        assert p1 == p2
        assert r1 == r2
        assert a1 == a2

    def test_health_unsafe_gives_p0(self):
        from core.mission_control import MCSnapshot, DecisionPolicy, Priority
        snap = MCSnapshot(
            snapshot_id="x", cycle_id="y", timestamp_iso="2026-01-01T00:00:00Z",
            overall_health="UNSAFE", overall_mc_state="SAFETY_STOP",
        )
        policy = DecisionPolicy()
        priority, reasons, action, _ = policy.evaluate(snap)
        assert priority == Priority.P0_SAFETY

    def test_no_action_when_healthy(self):
        from core.mission_control import MCSnapshot, DecisionPolicy, Priority, MCAction
        snap = MCSnapshot(
            snapshot_id="x", cycle_id="y", timestamp_iso="2026-01-01T00:00:00Z",
            overall_health="HEALTHY", research_status="HEALTHY",
            knowledge_build_count=5, lifecycle_revalidation_overdue=0,
            failed_tasks=0, replacement_candidates=0, human_review_open=0,
            overall_mc_state="NORMAL",
        )
        policy = DecisionPolicy()
        priority, reasons, action, _ = policy.evaluate(snap)
        assert priority == Priority.P7_NO_ACTION
        assert action == MCAction.NO_ACTION


# ===========================================================================
# T3: Priority ordering
# ===========================================================================
class TestT3PriorityOrdering:
    """T3: Priority enum values must be ordered correctly."""

    def test_priority_order(self):
        from core.mission_control import Priority
        order = [
            Priority.P0_SAFETY,
            Priority.P1_DATA_FAILURE,
            Priority.P2_FAILED_TASK,
            Priority.P3_REQUIRED_REVALIDATION,
            Priority.P4_RESEARCH_NEED,
            Priority.P5_HUMAN_REVIEW,
            Priority.P6_WAIT_FOR_EVIDENCE,
            Priority.P7_NO_ACTION,
        ]
        for i in range(len(order) - 1):
            assert order[i].value < order[i+1].value, f"{order[i]} should be < {order[i+1]}"


# ===========================================================================
# T4: One primary action
# ===========================================================================
class TestT4OnePrimaryAction:
    """T4: Each decision must have exactly one primary action."""

    def test_one_action_per_decision(self):
        from core.mission_control import MCSnapshot, DecisionPolicy, MCAction
        snap = MCSnapshot(
            snapshot_id="x", cycle_id="y", timestamp_iso="2026-01-01T00:00:00Z",
            overall_health="HEALTHY", research_status="STALE",
            knowledge_build_count=5, lifecycle_revalidation_overdue=0,
            failed_tasks=0, replacement_candidates=0, human_review_open=0,
        )
        policy = DecisionPolicy()
        _, _, action, _ = policy.evaluate(snap)
        assert isinstance(action, MCAction)
        assert action in list(MCAction)


# ===========================================================================
# T5: Task dedupe
# ===========================================================================
class TestT5TaskDedupe:
    """T5: Duplicate tasks with same payload_hash must not be created."""

    def test_task_dedupe(self, mc_store):
        from core.mission_control import MCTask, TaskState
        task = MCTask(
            task_id="task_1",
            task_type="CANONICAL_RESEARCH",
            source_cycle="mc_1",
            priority="P4",
            payload_hash="abc123",
            state=TaskState.PENDING.value,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        mc_store.store_task(task)
        assert mc_store.task_exists_by_hash("abc123", TaskState.PENDING.value)
        assert not mc_store.task_exists_by_hash("abc123", TaskState.COMPLETED.value)
        assert not mc_store.task_exists_by_hash("xyz789", TaskState.PENDING.value)


# ===========================================================================
# T6: Restart/resume
# ===========================================================================
class TestT6RestartResume:
    """T6: After restart, RUNNING tasks can be resumed."""

    def test_running_task_resume(self, mc_store):
        from core.mission_control import MCTask, TaskState
        task = MCTask(
            task_id="task_resume",
            task_type="REVALIDATION",
            source_cycle="mc_1",
            priority="P3",
            payload_hash="def456",
            state=TaskState.RUNNING.value,
            created_at=datetime.now(timezone.utc).isoformat(),
            started_at=datetime.now(timezone.utc).isoformat(),
            attempts=1,
        )
        mc_store.store_task(task)
        pending = mc_store.get_pending_tasks()
        assert len(pending) == 1
        assert pending[0]["task_id"] == "task_resume"


# ===========================================================================
# T7: Revalidation creation
# ===========================================================================
class TestT7RevalidationCreation:
    """T7: RevalidationEngine creates bounded requests."""

    def test_create_revalidation(self, mc_store):
        from core.mission_control import RevalidationEngine, RevalidationState
        engine = RevalidationEngine(mc_store)
        req = engine.create_request(
            strategy_identity="strat_A",
            reason="DECAY_SUSPECTED",
            evidence_type="BACKTEST_REVALIDATION",
            source_trigger="LIFECYCLE",
        )
        assert req.state == RevalidationState.PENDING.value
        assert req.strategy_identity == "strat_A"
        assert req.requested_evidence_type == "BACKTEST_REVALIDATION"

    def test_plan_experiment(self, mc_store):
        from core.mission_control import RevalidationEngine, RevalidationState
        engine = RevalidationEngine(mc_store)
        req = engine.create_request(
            strategy_identity="strat_B",
            reason="STALE_EVIDENCE",
            evidence_type="DATA_REFRESH",
            source_trigger="MC_DECISION",
        )
        req = engine.plan_experiment(req)
        assert req.state == RevalidationState.PLANNED.value
        assert req.experiment_plan is not None
        assert req.completion_criteria is not None


# ===========================================================================
# T8: Revalidation novelty
# ===========================================================================
class TestT8RevalidationNovelty:
    """T8: Revalidation should check for duplicates."""

    def test_dedupe_check(self, mc_store):
        from core.mission_control import RevalidationEngine
        engine = RevalidationEngine(mc_store)
        engine.create_request(
            strategy_identity="strat_C",
            reason="TEST",
            evidence_type="BACKTEST_REVALIDATION",
            source_trigger="LIFECYCLE",
        )
        assert engine.dedupe_check("strat_C", "BACKTEST_REVALIDATION", "LIFECYCLE") is True
        assert engine.dedupe_check("strat_C", "DATA_REFRESH", "LIFECYCLE") is False
        assert engine.dedupe_check("strat_D", "BACKTEST_REVALIDATION", "LIFECYCLE") is False


# ===========================================================================
# T9: Revalidation loop guard
# ===========================================================================
class TestT9RevalidationLoopGuard:
    """T9: Loop protection prevents infinite revalidation."""

    def test_loop_guard_blocks(self, mc_store):
        from core.mission_control import (
            RevalidationEngine, RevalidationState,
            MAX_AUTO_REVALIDATIONS_PER_STRATEGY_PER_PERIOD,
        )
        engine = RevalidationEngine(mc_store)
        # Create max allowed revalidations
        for i in range(MAX_AUTO_REVALIDATIONS_PER_STRATEGY_PER_PERIOD):
            req = engine.create_request(
                strategy_identity="strat_loop",
                reason=f"reason_{i}",
                evidence_type="BACKTEST_REVALIDATION",
                source_trigger="LIFECYCLE",
            )
            assert req.state == RevalidationState.PENDING.value
        # Next one should be blocked
        req = engine.create_request(
            strategy_identity="strat_loop",
            reason="reason_extra",
            evidence_type="BACKTEST_REVALIDATION",
            source_trigger="LIFECYCLE",
        )
        assert req.state == RevalidationState.BLOCKED.value


# ===========================================================================
# T10: Incident dedupe
# ===========================================================================
class TestT10IncidentDedupe:
    """T10: Same fingerprint must update, not duplicate."""

    def test_incident_dedupe(self, mc_store):
        from core.mission_control import IncidentManager
        mgr = IncidentManager(mc_store)
        inc1 = mgr.create("comp_A", "FAIL_1", "WARNING", "first occurrence")
        inc2 = mgr.create("comp_A", "FAIL_1", "WARNING", "second occurrence")
        assert inc1.incident_id == inc2.incident_id
        assert inc2.occurrence_count == 2

    def test_different_fingerprint_different_incident(self, mc_store):
        from core.mission_control import IncidentManager
        mgr = IncidentManager(mc_store)
        inc1 = mgr.create("comp_A", "FAIL_1", "WARNING")
        inc2 = mgr.create("comp_A", "FAIL_2", "WARNING")
        assert inc1.incident_id != inc2.incident_id


# ===========================================================================
# T11: Recovery allowlist
# ===========================================================================
class TestT11RecoveryAllowlist:
    """T11: Only allowlisted recovery actions are permitted."""

    def test_allowlisted_recovery(self, mc_store):
        from core.mission_control import IncidentManager
        mgr = IncidentManager(mc_store)
        assert mgr.recovery_allowlist_check("RESEARCH_PIPELINE_FAILURE") is True
        assert mgr.recovery_allowlist_check("LOCK_STALE") is True

    def test_non_allowlisted_recovery(self, mc_store):
        from core.mission_control import IncidentManager
        mgr = IncidentManager(mc_store)
        assert mgr.recovery_allowlist_check("ARBITRARY_SHELL_COMMAND") is False
        assert mgr.recovery_allowlist_check("MODE_CHANGE") is False
        assert mgr.recovery_allowlist_check("REGISTRY_MUTATION") is False


# ===========================================================================
# T12: Recovery verification
# ===========================================================================
class TestT12RecoveryVerification:
    """T12: Recovery must be verified before marking RESOLVED."""

    def test_recovery_verify_success(self, mc_store):
        from core.mission_control import IncidentManager, IncidentState
        mgr = IncidentManager(mc_store)
        inc = mgr.create("comp_B", "LOCK_STALE", "WARNING")
        success, msg = mgr.attempt_recovery(inc.incident_id)
        assert success is True
        mgr.verify_recovery(inc.incident_id, True, "Lock recovered")
        open_incidents = mc_store.get_open_incidents()
        assert all(i["incident_id"] != inc.incident_id for i in open_incidents)

    def test_recovery_verify_failure(self, mc_store):
        from core.mission_control import IncidentManager, IncidentState
        mgr = IncidentManager(mc_store)
        inc = mgr.create("comp_C", "LOCK_STALE", "WARNING")
        success, msg = mgr.attempt_recovery(inc.incident_id)
        assert success is True
        mgr.verify_recovery(inc.incident_id, False, "Still broken")
        open_incidents = mc_store.get_open_incidents()
        escalated = [i for i in open_incidents if i["state"] == "ESCALATED"]
        assert len(escalated) == 1


# ===========================================================================
# T13: Recovery escalation
# ===========================================================================
class TestT13RecoveryEscalation:
    """T13: Failed recovery must escalate to operator."""

    def test_escalation_package(self, mc_store):
        from core.mission_control import IncidentManager, IncidentState
        mgr = IncidentManager(mc_store)
        inc = mgr.create("comp_D", "RECOVERY_FAILED", "BLOCKING")
        package = json.dumps({"what_failed": ["comp_D"], "severity": "BLOCKING"})
        mgr.escalate(inc.incident_id, package)
        open_incidents = mc_store.get_open_incidents()
        escalated = [i for i in open_incidents if i["state"] == "ESCALATED"]
        assert len(escalated) == 1
        assert escalated[0]["escalation_package"] is not None


# ===========================================================================
# T14: Alert dedupe
# ===========================================================================
class TestT14AlertDedupe:
    """T14: Repeated alerts must be rate-limited."""

    def test_alert_rate_limit(self, mc_store):
        from core.mission_control import ObservabilityLayer, MAX_ALERTS_PER_CYCLE
        obs = ObservabilityLayer(mc_store)
        for i in range(MAX_ALERTS_PER_CYCLE + 5):
            obs.emit_event("TEST_EVENT", "INFO", f"message {i}", "comp_X")
        # Check that some are suppressed
        conn = mc_store._connect()
        total = conn.execute("SELECT COUNT(*) FROM mc_alert_events").fetchone()[0]
        suppressed = conn.execute("SELECT COUNT(*) FROM mc_alert_events WHERE suppressed=1").fetchone()[0]
        assert suppressed > 0


# ===========================================================================
# T15: Secret redaction
# ===========================================================================
class TestT15SecretRedaction:
    """T15: Secrets must be redacted from alerts and reports."""

    def test_token_redaction(self):
        from core.mission_control import ObservabilityLayer
        text = "token=abc123secret456 and api_key=xyz789apikey123"
        redacted = ObservabilityLayer.redact_secrets(text)
        assert "abc123secret456" not in redacted
        assert "xyz789apikey123" not in redacted
        assert "REDACTED" in redacted

    def test_long_string_redaction(self):
        from core.mission_control import ObservabilityLayer
        text = "Using key: ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        redacted = ObservabilityLayer.redact_secrets(text)
        assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in redacted


# ===========================================================================
# T16: Human-review trigger
# ===========================================================================
class TestT16HumanReviewTrigger:
    """T16: MC can trigger human review via canonical interface."""

    def test_human_review_creation(self, mc_store, tmp_project):
        from core.mission_control import MissionControlOrchestrator, MCSnapshot, MCDecision, MCAction
        mc = MissionControlOrchestrator(tmp_project)
        snap = MCSnapshot(
            snapshot_id="snap_hr", cycle_id="mc_hr",
            timestamp_iso=datetime.now(timezone.utc).isoformat(),
            overall_health="HEALTHY", research_status="HEALTHY",
            knowledge_build_count=5, replacement_candidates=2,
            human_review_open=0, overall_mc_state="ACTION_REQUIRED",
        )
        snap.snapshot_hash = snap.compute_hash()
        dec = MCDecision(
            decision_id="dec_hr", cycle_id="mc_hr",
            snapshot_id="snap_hr", snapshot_hash=snap.snapshot_hash,
            primary_action=MCAction.REQUEST_HUMAN_REVIEW.value,
            priority="P5_HUMAN_REVIEW",
            reason_codes=["REPLACEMENT_CANDIDATES=2"],
            overall_state="ACTION_REQUIRED",
            timestamp_iso=datetime.now(timezone.utc).isoformat(),
        )
        dec = mc.execute_bounded_action(dec, snap)
        # Should either succeed or fail gracefully
        assert dec.executed is True
        mc.store.close()


# ===========================================================================
# T17: Agent cannot approve
# ===========================================================================
class TestT17AgentCannotApprove:
    """T17: Mission Control CANNOT approve/reject human review cases."""

    def test_no_approval_action(self):
        from core.mission_control import MCAction
        # MCAction must not contain APPROVE, REJECT, or DEFER
        for action in MCAction:
            assert "APPROVE" not in action.value.upper()
            assert "REJECT" not in action.value.upper()
            assert "DEFER" not in action.value.upper()


# ===========================================================================
# T18: Registry safety
# ===========================================================================
class TestT18RegistrySafety:
    """T18: Mission Control must never mutate the strategy registry."""

    def test_audit_registry_safety(self, mc_orchestrator, sample_snapshot):
        from core.mission_control import MCDecision, MCAction
        dec = MCDecision(
            decision_id="dec_test", cycle_id="mc_test",
            snapshot_id="snap_test", snapshot_hash="abc",
            primary_action=MCAction.NO_ACTION.value,
            priority="P7_NO_ACTION",
            timestamp_iso=datetime.now(timezone.utc).isoformat(),
        )
        audit = mc_orchestrator.audit("mc_test", sample_snapshot, dec)
        assert audit["safety_checks"]["no_registry_mutation"] is True
        assert audit["action_is_non_trading"] is True


# ===========================================================================
# T19: Swap safety
# ===========================================================================
class TestT19SwapSafety:
    """T19: Mission Control must never mutate swap state."""

    def test_audit_swap_safety(self, mc_orchestrator, sample_snapshot):
        from core.mission_control import MCDecision, MCAction
        dec = MCDecision(
            decision_id="dec_swap", cycle_id="mc_swap",
            snapshot_id="snap_swap", snapshot_hash="def",
            primary_action=MCAction.NO_ACTION.value,
            priority="P7_NO_ACTION",
            timestamp_iso=datetime.now(timezone.utc).isoformat(),
        )
        audit = mc_orchestrator.audit("mc_swap", sample_snapshot, dec)
        assert audit["safety_checks"]["no_swap_mutation"] is True


# ===========================================================================
# T20: Broker safety
# ===========================================================================
class TestT20BrokerSafety:
    """T20: Mission Control must never mutate broker state."""

    def test_audit_broker_safety(self, mc_orchestrator, sample_snapshot):
        from core.mission_control import MCDecision, MCAction
        dec = MCDecision(
            decision_id="dec_broker", cycle_id="mc_broker",
            snapshot_id="snap_broker", snapshot_hash="ghi",
            primary_action=MCAction.NO_ACTION.value,
            priority="P7_NO_ACTION",
            timestamp_iso=datetime.now(timezone.utc).isoformat(),
        )
        audit = mc_orchestrator.audit("mc_broker", sample_snapshot, dec)
        assert audit["safety_checks"]["no_broker_mutation"] is True


# ===========================================================================
# T21: Execution safety
# ===========================================================================
class TestT21ExecutionSafety:
    """T21: Mission Control must never mutate execution state."""

    def test_audit_execution_safety(self, mc_orchestrator, sample_snapshot):
        from core.mission_control import MCDecision, MCAction
        dec = MCDecision(
            decision_id="dec_exec", cycle_id="mc_exec",
            snapshot_id="snap_exec", snapshot_hash="jkl",
            primary_action=MCAction.NO_ACTION.value,
            priority="P7_NO_ACTION",
            timestamp_iso=datetime.now(timezone.utc).isoformat(),
        )
        audit = mc_orchestrator.audit("mc_exec", sample_snapshot, dec)
        assert audit["safety_checks"]["no_execution_mutation"] is True
        assert audit["safety_checks"]["no_signal_mutation"] is True


# ===========================================================================
# T22: Lock/concurrency
# ===========================================================================
class TestT22LockConcurrency:
    """T22: Lock must be acquired, second attempt must fail, stale recovery works."""

    def test_first_acquire_succeeds(self, mc_orchestrator):
        assert mc_orchestrator._acquire_lock() is True
        mc_orchestrator._release_lock()

    def test_second_acquire_fails(self, mc_orchestrator):
        assert mc_orchestrator._acquire_lock() is True
        # Second acquire should fail (non-blocking)
        mc2 = mc_orchestrator.__class__(mc_orchestrator.project_root)
        assert mc2._acquire_lock() is False
        mc_orchestrator._release_lock()
        mc2._release_lock()

    def test_stale_lock_recovery(self, mc_orchestrator):
        # Simulate stale lock
        mc_orchestrator.lock_path.parent.mkdir(parents=True, exist_ok=True)
        stale_time = (datetime.now(timezone.utc) - __import__('datetime').timedelta(minutes=35)).isoformat()
        mc_orchestrator.lock_path.write_text(json.dumps({
            "pid": 99999,
            "acquired_at": stale_time,
        }))
        assert mc_orchestrator._check_stale_lock() is True
        assert not mc_orchestrator.lock_path.exists()

    def test_lock_release(self, mc_orchestrator):
        mc_orchestrator._acquire_lock()
        mc_orchestrator._release_lock()
        # After release, lock should be acquirable
        assert mc_orchestrator._acquire_lock() is True
        mc_orchestrator._release_lock()


# ===========================================================================
# T23: System Health visibility
# ===========================================================================
class TestT23SystemHealthVisibility:
    """T23: Mission Control status must be visible in System Health."""

    def test_mc_state_in_snapshot(self, sample_snapshot):
        from core.mission_control import OverallMCState
        assert sample_snapshot.overall_mc_state in [s.value for s in OverallMCState]


# ===========================================================================
# T24: Regression accounting
# ===========================================================================
class TestT24RegressionAccounting:
    """T24: All tests must be accounted for with PASS/FAIL/ERROR/SKIP."""

    def test_all_enums_valid(self):
        from core.mission_control import (
            MCAction, Priority, IncidentSeverity, TaskState, IncidentState,
            RevalidationState, RevalidationEvidenceType, OverallMCState,
            MCHealthStatus, AlertEventType,
        )
        # Verify all enums have values
        assert len(MCAction) == 8
        assert len(Priority) == 8
        assert len(IncidentSeverity) == 5
        assert len(TaskState) == 7
        assert len(IncidentState) == 6
        assert len(RevalidationState) == 8
        assert len(RevalidationEvidenceType) == 7
        assert len(OverallMCState) == 6
        assert len(MCHealthStatus) == 6
        assert len(AlertEventType) == 12


# ===========================================================================
# F1: mission_control.db unavailable
# ===========================================================================
class TestF1DBUnavailable:
    """F1: Mission Control must handle missing/unavailable DB gracefully."""

    def test_store_creates_db(self, tmp_path):
        from core.mission_control import MissionControlStore
        store = MissionControlStore(tmp_path)
        assert (tmp_path / "state" / "mission_control.db").exists()
        store.close()

    def test_store_handles_corrupt_db(self, tmp_path):
        from core.mission_control import MissionControlStore
        db_path = tmp_path / "state" / "mission_control.db"
        db_path.parent.mkdir(exist_ok=True)
        db_path.write_text("corrupt data")
        # Should be able to handle gracefully
        store = MissionControlStore(tmp_path)
        # Even if the DB is corrupt, the store should initialize
        store.close()


# ===========================================================================
# F2: Source health unavailable
# ===========================================================================
class TestF2SourceHealthUnavailable:
    """F2: MC must handle missing system health data."""

    def test_snapshot_with_missing_health(self, mc_orchestrator):
        # snapshot() should not crash even if health data is unavailable
        snap = mc_orchestrator.snapshot()
        assert snap is not None
        assert snap.snapshot_hash != ""


# ===========================================================================
# F3: Research status missing
# ===========================================================================
class TestF3ResearchStatusMissing:
    """F3: MC must handle missing research status."""

    def test_snapshot_with_missing_research(self, mc_orchestrator, tmp_project):
        # Remove latest_run.json
        lr = tmp_project / "reports" / "strategy_architect" / "latest_run.json"
        if lr.exists():
            lr.unlink()
        snap = mc_orchestrator.snapshot()
        assert snap.research_status in ("UNKNOWN", "STALE", "FAILED")


# ===========================================================================
# F4: Lifecycle missing
# ===========================================================================
class TestF4LifecycleMissing:
    """F4: MC must handle missing lifecycle data."""

    def test_snapshot_with_missing_lifecycle(self, mc_orchestrator, tmp_project):
        # Remove lifecycle DB if exists
        ldb = tmp_project / "state" / "strategy_lifecycle.db"
        if ldb.exists():
            ldb.unlink()
        snap = mc_orchestrator.snapshot()
        assert snap is not None


# ===========================================================================
# F5: Ranking missing
# ===========================================================================
class TestF5RankingMissing:
    """F5: MC must handle missing ranking data."""

    def test_snapshot_with_missing_ranking(self, mc_orchestrator, tmp_project):
        rdb = tmp_project / "state" / "replacement_ranking.db"
        if rdb.exists():
            rdb.unlink()
        snap = mc_orchestrator.snapshot()
        assert snap is not None


# ===========================================================================
# F6: Human review unavailable
# ===========================================================================
class TestF6HumanReviewUnavailable:
    """F6: MC must handle missing human review data."""

    def test_snapshot_with_missing_human_review(self, mc_orchestrator, tmp_project):
        hrdb = tmp_project / "state" / "human_review.db"
        if hrdb.exists():
            hrdb.unlink()
        snap = mc_orchestrator.snapshot()
        assert snap is not None


# ===========================================================================
# F7: Duplicate cycle
# ===========================================================================
class TestF7DuplicateCycle:
    """F7: Duplicate MC cycles must be prevented by lock."""

    def test_lock_prevents_duplicate(self, mc_orchestrator):
        assert mc_orchestrator._acquire_lock() is True
        mc2 = mc_orchestrator.__class__(mc_orchestrator.project_root)
        assert mc2._acquire_lock() is False
        mc_orchestrator._release_lock()
        mc2._release_lock()


# ===========================================================================
# F8: Stale lock
# ===========================================================================
class TestF8StaleLock:
    """F8: Stale lock must be recovered."""

    def test_stale_lock_detected(self, mc_orchestrator):
        mc_orchestrator.lock_path.parent.mkdir(parents=True, exist_ok=True)
        stale_time = (datetime.now(timezone.utc) - __import__('datetime').timedelta(minutes=35)).isoformat()
        mc_orchestrator.lock_path.write_text(json.dumps({
            "pid": 99999,
            "acquired_at": stale_time,
        }))
        assert mc_orchestrator._check_stale_lock() is True


# ===========================================================================
# F9: Duplicate task
# ===========================================================================
class TestF9DuplicateTask:
    """F9: Duplicate tasks must be detected by payload hash."""

    def test_duplicate_detection(self, mc_store):
        from core.mission_control import MCTask, TaskState
        t = MCTask(
            task_id="t1", task_type="REVALIDATION", source_cycle="mc_1",
            priority="P3", payload_hash="hash_dup",
            state=TaskState.PENDING.value,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        mc_store.store_task(t)
        assert mc_store.task_exists_by_hash("hash_dup", TaskState.PENDING.value)


# ===========================================================================
# F10: Task crash
# ===========================================================================
class TestF10TaskCrash:
    """F10: Crashed tasks must be detectable."""

    def test_failed_task_detection(self, mc_store):
        from core.mission_control import MCTask, TaskState
        t = MCTask(
            task_id="t_crash", task_type="REVALIDATION", source_cycle="mc_1",
            priority="P3", payload_hash="hash_crash",
            state=TaskState.FAILED.value,
            created_at=datetime.now(timezone.utc).isoformat(),
            attempts=1,
        )
        mc_store.store_task(t)
        failed = mc_store.get_failed_tasks()
        assert len(failed) >= 1


# ===========================================================================
# F11: Task stuck RUNNING
# ===========================================================================
class TestF11TaskStuckRunning:
    """F11: Tasks stuck in RUNNING must be visible."""

    def test_stuck_running_visible(self, mc_store):
        from core.mission_control import MCTask, TaskState
        t = MCTask(
            task_id="t_stuck", task_type="REVALIDATION", source_cycle="mc_1",
            priority="P3", payload_hash="hash_stuck",
            state=TaskState.RUNNING.value,
            created_at=datetime.now(timezone.utc).isoformat(),
            started_at=datetime.now(timezone.utc).isoformat(),
            attempts=1,
        )
        mc_store.store_task(t)
        pending = mc_store.get_pending_tasks()
        assert any(p["task_id"] == "t_stuck" for p in pending)


# ===========================================================================
# F12: Revalidation duplicate
# ===========================================================================
class TestF12RevalidationDuplicate:
    """F12: Duplicate revalidation requests must be detected."""

    def test_revalidation_dedupe(self, mc_store):
        from core.mission_control import RevalidationEngine
        engine = RevalidationEngine(mc_store)
        engine.create_request(
            strategy_identity="strat_dup", reason="TEST",
            evidence_type="BACKTEST_REVALIDATION", source_trigger="LIFECYCLE",
        )
        assert engine.dedupe_check("strat_dup", "BACKTEST_REVALIDATION", "LIFECYCLE") is True


# ===========================================================================
# F13: Revalidation unsupported
# ===========================================================================
class TestF13RevalidationUnsupported:
    """F13: Unsupported evidence types must be handled."""

    def test_unsupported_evidence_type(self, mc_store):
        from core.mission_control import RevalidationEngine
        engine = RevalidationEngine(mc_store)
        # Should still create the request (validation is advisory)
        req = engine.create_request(
            strategy_identity="strat_unsup", reason="TEST",
            evidence_type="UNSUPPORTED_TYPE", source_trigger="TEST",
        )
        assert req.requested_evidence_type == "UNSUPPORTED_TYPE"


# ===========================================================================
# F14: Revalidation recursion
# ===========================================================================
class TestF14RevalidationRecursion:
    """F14: Recursive revalidation must be prevented."""

    def test_loop_guard_prevents_recursion(self, mc_store):
        from core.mission_control import (
            RevalidationEngine, RevalidationState,
            MAX_AUTO_REVALIDATIONS_PER_STRATEGY_PER_PERIOD,
        )
        engine = RevalidationEngine(mc_store)
        for i in range(MAX_AUTO_REVALIDATIONS_PER_STRATEGY_PER_PERIOD + 1):
            req = engine.create_request(
                strategy_identity="strat_recursion",
                reason=f"reason_{i}",
                evidence_type="BACKTEST_REVALIDATION",
                source_trigger="REVALIDATION",
            )
        # Last one should be blocked
        assert req.state == RevalidationState.BLOCKED.value


# ===========================================================================
# F15: Incident duplicate
# ===========================================================================
class TestF15IncidentDuplicate:
    """F15: Duplicate incidents must be merged."""

    def test_incident_merge(self, mc_store):
        from core.mission_control import IncidentManager
        mgr = IncidentManager(mc_store)
        inc1 = mgr.create("comp_merge", "MERGE_TEST", "INFO")
        inc2 = mgr.create("comp_merge", "MERGE_TEST", "INFO")
        assert inc1.incident_id == inc2.incident_id
        assert inc2.occurrence_count == 2


# ===========================================================================
# F16: Recovery attempt fails
# ===========================================================================
class TestF16RecoveryAttemptFails:
    """F16: Failed recovery must be recorded."""

    def test_recovery_failure_recorded(self, mc_store):
        from core.mission_control import IncidentManager
        mgr = IncidentManager(mc_store)
        inc = mgr.create("comp_fail", "RECOVERY_FAILED", "WARNING")
        success, msg = mgr.attempt_recovery(inc.incident_id)
        mgr.verify_recovery(inc.incident_id, False, "Recovery failed")
        # Check escalation
        open_inc = mc_store.get_open_incidents()
        escalated = [i for i in open_inc if i["state"] == "ESCALATED"]
        assert len(escalated) >= 1


# ===========================================================================
# F17: Recovery exceeds max attempts
# ===========================================================================
class TestF17RecoveryMaxAttempts:
    """F17: Recovery must stop after max attempts."""

    def test_max_attempts_exceeded(self, mc_store):
        from core.mission_control import IncidentManager
        mgr = IncidentManager(mc_store)
        inc = mgr.create("comp_max", "LOCK_STALE", "WARNING")
        # Exhaust attempts
        for _ in range(3):
            mgr.attempt_recovery(inc.incident_id)
        # Next attempt should fail
        success, msg = mgr.attempt_recovery(inc.incident_id)
        assert success is False
        assert "Max recovery attempts" in msg


# ===========================================================================
# F18: Notifier unavailable
# ===========================================================================
class TestF18NotifierUnavailable:
    """F18: MC must handle missing notifier gracefully."""

    def test_alert_without_notifier(self, mc_store):
        from core.mission_control import ObservabilityLayer
        obs = ObservabilityLayer(mc_store)
        # Should not crash even without Telegram
        alert = obs.emit_event("TEST", "INFO", "test message")
        assert alert.event_id is not None


# ===========================================================================
# F19: Notifier spam loop
# ===========================================================================
class TestF19NotifierSpamLoop:
    """F19: Alert spam must be rate-limited."""

    def test_spam_prevention(self, mc_store):
        from core.mission_control import ObservabilityLayer, MAX_ALERTS_PER_CYCLE
        obs = ObservabilityLayer(mc_store)
        alerts = []
        for i in range(20):
            a = obs.emit_event("SPAM_TEST", "INFO", f"msg {i}", "spam_comp")
            alerts.append(a)
        suppressed = sum(1 for a in alerts if a.suppressed)
        assert suppressed > 0


# ===========================================================================
# F20: Secret in alert
# ===========================================================================
class TestF20SecretInAlert:
    """F20: Secrets must not leak into alerts."""

    def test_secret_not_in_stored_alert(self, mc_store):
        from core.mission_control import ObservabilityLayer
        obs = ObservabilityLayer(mc_store)
        alert = obs.emit_event(
            "SECRET_TEST", "INFO",
            "token=supersecret123apikey and key=anothersecret456",
            "secret_comp",
        )
        assert "supersecret123apikey" not in alert.message
        assert "anothersecret456" not in alert.message


# ===========================================================================
# F21: Agent attempts human approval
# ===========================================================================
class TestF21AgentCannotApprove:
    """F21: MC must not have approval actions."""

    def test_no_approval_in_actions(self):
        from core.mission_control import MCAction
        for action in MCAction:
            val = action.value.upper()
            assert "APPROVE" not in val
            assert "REJECT" not in val
            assert "DEFER" not in val


# ===========================================================================
# F22: Control plane attempts registry mutation
# ===========================================================================
class TestF22NoRegistryMutation:
    """F22: Control plane must not attempt registry mutation."""

    def test_audit_no_registry_mutation(self, mc_orchestrator, sample_snapshot):
        from core.mission_control import MCDecision, MCAction
        for action in MCAction:
            dec = MCDecision(
                decision_id=f"dec_{action.value}", cycle_id="mc_test",
                snapshot_id="snap_test", snapshot_hash="test",
                primary_action=action.value,
                priority="P7_NO_ACTION",
                timestamp_iso=datetime.now(timezone.utc).isoformat(),
            )
            audit = mc_orchestrator.audit("mc_test", sample_snapshot, dec)
            assert audit["safety_checks"]["no_registry_mutation"] is True


# ===========================================================================
# F23: Control plane attempts broker mutation
# ===========================================================================
class TestF23NoBrokerMutation:
    """F23: Control plane must not attempt broker mutation."""

    def test_audit_no_broker_mutation(self, mc_orchestrator, sample_snapshot):
        from core.mission_control import MCDecision, MCAction
        for action in MCAction:
            dec = MCDecision(
                decision_id=f"dec_broker_{action.value}", cycle_id="mc_broker_test",
                snapshot_id="snap_broker_test", snapshot_hash="broker_test",
                primary_action=action.value,
                priority="P7_NO_ACTION",
                timestamp_iso=datetime.now(timezone.utc).isoformat(),
            )
            audit = mc_orchestrator.audit("mc_broker_test", sample_snapshot, dec)
            assert audit["safety_checks"]["no_broker_mutation"] is True


# ===========================================================================
# F24: Fixture data leaks into production
# ===========================================================================
class TestF24NoFixtureLeakage:
    """F24: Test fixture data must not leak into production state."""

    def test_tmp_project_isolated(self, tmp_project):
        from core.mission_control import MissionControlStore
        store = MissionControlStore(tmp_project)
        # Create a cycle in tmp
        store.create_cycle("test_cycle_leak")
        # Verify it's in tmp, not production
        prod_db = Path("/root/prop-desk/strategy_combine/state/mission_control.db")
        if prod_db.exists():
            conn = sqlite3.connect(str(prod_db))
            row = conn.execute(
                "SELECT 1 FROM mc_cycles WHERE cycle_id='test_cycle_leak'"
            ).fetchone()
            conn.close()
            assert row is None, "Fixture data leaked into production!"
        store.close()


# ===========================================================================
# Extra: Full MC cycle integration test
# ===========================================================================
class TestMCFullCycle:
    """Integration test: run one complete MC cycle."""

    def test_run_cycle(self, mc_orchestrator):
        result = mc_orchestrator.run_cycle()
        assert "cycle_id" in result
        assert result["cycle_id"] is not None
        assert "overall_state" in result
        assert "primary_action" in result
        assert "reason_codes" in result
        assert "snapshot_hash" in result
        assert "audit" in result
        assert result["audit"]["safety_checks"]["no_broker_mutation"] is True
        assert result["audit"]["safety_checks"]["no_registry_mutation"] is True
        assert result["audit"]["safety_checks"]["no_swap_mutation"] is True
        assert result["audit"]["safety_checks"]["no_execution_mutation"] is True

    def test_cycle_stores_decision(self, mc_orchestrator):
        result = mc_orchestrator.run_cycle()
        dec = mc_orchestrator.store.get_decision(result["decision_id"])
        assert dec is not None
        assert dec["primary_action"] == result["primary_action"]

    def test_cycle_stores_snapshot(self, mc_orchestrator):
        result = mc_orchestrator.run_cycle()
        latest = mc_orchestrator.store.get_latest_snapshot()
        assert latest is not None
        assert latest["snapshot_id"] == result["snapshot_id"]


# ===========================================================================
# Extra: Dataclass round-trip tests
# ===========================================================================
class TestDataclassRoundTrip:
    """Verify all dataclasses serialize/deserialize cleanly."""

    def test_mc_decision_to_dict(self):
        from core.mission_control import MCDecision
        dec = MCDecision(
            decision_id="d1", cycle_id="c1", snapshot_id="s1",
            snapshot_hash="h1", primary_action="NO_ACTION",
            priority="P7_NO_ACTION", timestamp_iso="2026-01-01T00:00:00Z",
        )
        d = dec.to_dict()
        assert d["decision_id"] == "d1"
        assert d["primary_action"] == "NO_ACTION"

    def test_mc_task_to_dict(self):
        from core.mission_control import MCTask
        t = MCTask(
            task_id="t1", task_type="REVALIDATION", source_cycle="mc_1",
            priority="P3", payload_hash="abc",
            created_at="2026-01-01T00:00:00Z",
        )
        d = t.to_dict()
        assert d["task_id"] == "t1"
        assert d["task_type"] == "REVALIDATION"

    def test_mc_incident_to_dict(self):
        from core.mission_control import MCIncident
        inc = MCIncident(
            incident_id="i1", fingerprint="fp1", severity="WARNING",
            source_component="comp", reason_code="FAIL",
            first_seen="2026-01-01T00:00:00Z",
            last_seen="2026-01-01T00:00:00Z",
        )
        d = inc.to_dict()
        assert d["incident_id"] == "i1"
        assert d["severity"] == "WARNING"

    def test_mc_revalidation_to_dict(self):
        from core.mission_control import MCRevalidationRequest
        req = MCRevalidationRequest(
            revalidation_id="r1", strategy_identity="strat",
            reason="TEST", requested_evidence_type="BACKTEST",
            source_trigger="TEST",
            created_at="2026-01-01T00:00:00Z",
        )
        d = req.to_dict()
        assert d["revalidation_id"] == "r1"


# ===========================================================================
# Extra: Policy version tests
# ===========================================================================
class TestPolicyVersion:
    """Verify policy versioning works."""

    def test_policy_version_constant(self):
        from core.mission_control import MC_POLICY_VERSION, DecisionPolicy
        assert DecisionPolicy.VERSION == MC_POLICY_VERSION

    def test_all_actions_have_outcomes(self):
        from core.mission_control import MissionControlOrchestrator, MCAction
        mc = MissionControlOrchestrator.__new__(MissionControlOrchestrator)
        for action in MCAction:
            outcome = mc._expected_outcome(action)
            assert len(outcome) > 0

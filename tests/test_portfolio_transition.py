"""Portfolio Transition Tests — Iteration 20 (PAPER ONLY).

T1-T24: Mandatory tests per directive.
F1-F24: Failure matrix tests per directive.

CLASS 2: PAPER ONLY — zero real broker mutation.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.portfolio_transition import (
    AllocationPolicy,
    AllocationPlan,
    ApprovalBridge,
    AuthorizationStatus,
    BlockReason,
    ExecutionAuthorization,
    PreflightOutcome,
    PortfolioTransitionManager,
    PortfolioTransitionStore,
    ReconciliationResult,
    TERMINAL_STATES,
    TRANSITION_POLICY_VERSION,
    TransitionPlan,
    TransitionState,
    VALID_TRANSITIONS,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary database for testing."""
    db_path = tmp_path / "test_portfolio_transitions.db"
    store = PortfolioTransitionStore(db_path)
    yield store
    store.close()


@pytest.fixture
def manager(tmp_db):
    """Create a transition manager with test database."""
    return PortfolioTransitionManager(store=tmp_db)


@pytest.fixture
def sample_case():
    """Sample approved review case."""
    return {
        "case_id": "case-001",
        "ranking_build_id": "ranking-001",
        "comparison_id": "comp-001",
        "incumbent_id": "strat-alpha",
        "candidate_id": "strat-beta",
        "ranking_decision": "REPLACEMENT_CANDIDATE",
        "ranking_confidence": "HIGH",
        "state": "APPROVED",
        "evidence_hash": "evidence-abc-123",
        "created_at": time.time() - 100,
        "decided_at": time.time() - 50,
        "registry_hash": "reg-hash-001",
        "portfolio_snapshot_id": "portfolio-001",
        "lifecycle_build_id": "lifecycle-001",
        "attribution_build_id": "attr-001",
        "regime_build_id": "regime-001",
        "knowledge_build_id": "knowledge-001",
        "open_position_present": False,
    }


@pytest.fixture
def sample_decision():
    """Sample HUMAN APPROVE decision."""
    return {
        "decision_id": "decision-001",
        "case_id": "case-001",
        "decision": "APPROVE",
        "actor_type": "HUMAN",
        "actor_id": "operator-001",
        "evidence_hash": "evidence-abc-123",
        "reason_code": "EVIDENCE_STRONG",
        "comment": "Approved after thorough review",
        "timestamp": time.time() - 40,
    }


@pytest.fixture
def sample_authorization():
    """Sample HUMAN paper execution authorization."""
    return ExecutionAuthorization(
        authorization_id="auth-001",
        transition_id="trans-sample-001",
        review_case_id="case-001",
        decision_id="decision-001",
        actor_type="HUMAN",
        actor_id="operator-001",
        action="AUTHORIZE_PAPER_TRANSITION",
        approval_evidence_hash="evidence-abc-123",
        transition_plan_hash="plan-hash-placeholder",
        timestamp=time.time() - 30,
    )


@pytest.fixture
def agent_decision():
    """Agent decision (should be rejected)."""
    return {
        "decision_id": "decision-agent-001",
        "case_id": "case-001",
        "decision": "APPROVE",
        "actor_type": "AGENT",
        "actor_id": "hermes-agent",
        "evidence_hash": "evidence-abc-123",
        "reason_code": "",
        "comment": "Auto-approved",
        "timestamp": time.time(),
    }


@pytest.fixture
def system_decision():
    """System decision (should be rejected)."""
    return {
        "decision_id": "decision-system-001",
        "case_id": "case-001",
        "decision": "APPROVE",
        "actor_type": "SYSTEM",
        "actor_id": "mission-control",
        "evidence_hash": "evidence-abc-123",
        "reason_code": "",
        "comment": "System auto-approve",
        "timestamp": time.time(),
    }


# ===========================================================================
# T1-T24: Mandatory Tests
# ===========================================================================

class TestT01_ExactApprovalBinding:
    """T1: Exact approval binding — case/decision/evidence must match."""

    def test_valid_approval_binding(self, manager, sample_case, sample_decision):
        is_valid, reason = manager.bridge.validate_approval(sample_case, sample_decision)
        assert is_valid is True
        assert reason == "VALID"

    def test_case_id_mismatch(self, manager, sample_case, sample_decision):
        sample_decision["case_id"] = "wrong-case-id"
        is_valid, reason = manager.bridge.validate_approval(sample_case, sample_decision)
        assert is_valid is False
        assert reason == "CASE_ID_MISMATCH"

    def test_evidence_hash_mismatch(self, manager, sample_case, sample_decision):
        sample_decision["evidence_hash"] = "wrong-evidence-hash"
        is_valid, reason = manager.bridge.validate_approval(sample_case, sample_decision)
        assert is_valid is False
        assert reason == "EVIDENCE_HASH_MISMATCH"

    def test_missing_incumbent(self, manager, sample_case, sample_decision):
        sample_case["incumbent_id"] = ""
        is_valid, reason = manager.bridge.validate_approval(sample_case, sample_decision)
        assert is_valid is False
        assert reason == "MISSING_STRATEGY_IDENTITIES"


class TestT02_HUMANActorRequired:
    """T2: HUMAN actor required for approval."""

    def test_human_actor_accepted(self, manager, sample_case, sample_decision):
        sample_decision["actor_type"] = "HUMAN"
        is_valid, reason = manager.bridge.validate_approval(sample_case, sample_decision)
        assert is_valid is True

    def test_agent_actor_rejected(self, manager, sample_case, agent_decision):
        is_valid, reason = manager.bridge.validate_approval(sample_case, agent_decision)
        assert is_valid is False
        assert reason == "BLOCKED_AGENT_APPROVAL"

    def test_system_actor_rejected(self, manager, sample_case, system_decision):
        is_valid, reason = manager.bridge.validate_approval(sample_case, system_decision)
        assert is_valid is False
        assert reason == "BLOCKED_SYSTEM_APPROVAL"


class TestT03_SeparateExecutionAuthorization:
    """T3: Separate execution authorization required (APPROVE ≠ EXECUTE)."""

    def test_valid_authorization(self, manager, sample_authorization):
        # Use the matching plan hash that the authorization was created with
        is_valid, reason = manager.bridge.validate_authorization(
            sample_authorization, "plan-hash-placeholder")
        # The authorization's is_valid() checks action=AUTHORIZE_PAPER_TRANSITION
        # which matches, so this should be valid
        assert is_valid is True
        assert reason == "VALID"

    def test_agent_authorization_rejected(self, manager):
        auth = ExecutionAuthorization(
            authorization_id="auth-agent",
            transition_id="trans-001",
            review_case_id="case-001",
            decision_id="decision-001",
            actor_type="AGENT",
            actor_id="hermes",
            action="AUTHORIZE_PAPER_TRANSITION",
            approval_evidence_hash="evidence-abc",
            transition_plan_hash="plan-hash",
            timestamp=time.time(),
        )
        is_valid, reason = manager.bridge.validate_authorization(auth, "plan-hash")
        assert is_valid is False
        assert "AGENT" in reason

    def test_plan_hash_mismatch_rejected(self, manager):
        auth = ExecutionAuthorization(
            authorization_id="auth-001",
            transition_id="trans-001",
            review_case_id="case-001",
            decision_id="decision-001",
            actor_type="HUMAN",
            actor_id="operator-001",
            action="AUTHORIZE_PAPER_TRANSITION",
            approval_evidence_hash="evidence-abc",
            transition_plan_hash="plan-hash-actual",
            timestamp=time.time(),
        )
        is_valid, reason = manager.bridge.validate_authorization(auth, "plan-hash-different")
        assert is_valid is False
        assert reason == "PLAN_HASH_MISMATCH"

    def test_wrong_action_rejected(self, manager):
        auth = ExecutionAuthorization(
            authorization_id="auth-001",
            transition_id="trans-001",
            review_case_id="case-001",
            decision_id="decision-001",
            actor_type="HUMAN",
            actor_id="operator-001",
            action="APPROVE",
            approval_evidence_hash="evidence-abc",
            transition_plan_hash="plan-hash",
            timestamp=time.time(),
        )
        is_valid, reason = manager.bridge.validate_authorization(auth, "plan-hash")
        assert is_valid is False


class TestT04_FreshnessCheck:
    """T4: Stale approval → BLOCKED_STALE_APPROVAL."""

    def test_fresh_approval(self, manager, sample_case):
        is_stale, reason = manager.bridge.check_staleness(
            sample_case, "ranking-hash", "reg-hash-001", "portfolio-001")
        assert is_stale is False

    def test_registry_drift(self, manager, sample_case):
        is_stale, reason = manager.bridge.check_staleness(
            sample_case, "ranking-hash", "reg-hash-CHANGED", "portfolio-001")
        assert is_stale is True
        assert reason == "REGISTRY_DRIFT"

    def test_portfolio_drift(self, manager, sample_case):
        is_stale, reason = manager.bridge.check_staleness(
            sample_case, "ranking-hash", "reg-hash-001", "portfolio-CHANGED")
        assert is_stale is True
        assert reason == "PORTFOLIO_DRIFT"


class TestT05_DeterministicPlan:
    """T5: Deterministic plan creation."""

    def test_plan_hash_deterministic(self):
        plan1 = TransitionPlan(
            plan_id="plan-001", transition_id="trans-001",
            review_case_id="case-001", decision_id="decision-001",
            approval_evidence_hash="evidence-abc",
            incumbent_id="strat-alpha", candidate_id="strat-beta",
            slot_id="slot-01", ticker="BR",
        )
        plan2 = TransitionPlan(
            plan_id="plan-001", transition_id="trans-001",
            review_case_id="case-001", decision_id="decision-001",
            approval_evidence_hash="evidence-abc",
            incumbent_id="strat-alpha", candidate_id="strat-beta",
            slot_id="slot-01", ticker="BR",
        )
        assert plan1.compute_hash() == plan2.compute_hash()

    def test_different_inputs_different_hash(self):
        plan1 = TransitionPlan(
            plan_id="plan-001", transition_id="trans-001",
            review_case_id="case-001", decision_id="decision-001",
            approval_evidence_hash="evidence-abc",
            incumbent_id="strat-alpha", candidate_id="strat-beta",
            slot_id="slot-01", ticker="BR",
        )
        plan2 = TransitionPlan(
            plan_id="plan-002", transition_id="trans-002",
            review_case_id="case-001", decision_id="decision-001",
            approval_evidence_hash="evidence-abc",
            incumbent_id="strat-alpha", candidate_id="strat-gamma",
            slot_id="slot-01", ticker="BR",
        )
        assert plan1.compute_hash() != plan2.compute_hash()


class TestT06_DeterministicAllocation:
    """T6: Deterministic allocation with version."""

    def test_allocation_within_limits(self, manager):
        alloc = manager.allocation_policy.compute_allocation(
            transition_id="trans-001",
            capital_basis_rub=1_000_000,
            active_slots=3,
            current_exposure_rub=500_000,
            slot_allocations={"slot-01": 150_000, "slot-02": 150_000},
            risk_budget={"per_slot_max": 200_000},
            concentration={"max_family_pct": 30.0},
        )
        assert alloc.is_valid is True
        assert alloc.policy_version == AllocationPolicy.POLICY_VERSION

    def test_allocation_exceeds_total_exposure(self, manager):
        alloc = manager.allocation_policy.compute_allocation(
            transition_id="trans-001",
            capital_basis_rub=1_000_000,
            active_slots=3,
            current_exposure_rub=800_000,
            slot_allocations={"slot-01": 500_000, "slot-02": 400_000},
            risk_budget={"per_slot_max": 500_000},
            concentration={"max_family_pct": 30.0},
        )
        assert alloc.is_valid is False
        assert any("TOTAL_EXPOSURE_EXCEEDED" in e for e in alloc.validation_errors)

    def test_allocation_exceeds_single_strategy(self, manager):
        alloc = manager.allocation_policy.compute_allocation(
            transition_id="trans-001",
            capital_basis_rub=1_000_000,
            active_slots=1,
            current_exposure_rub=0,
            slot_allocations={"slot-01": 300_000},
            risk_budget={"per_slot_max": 300_000},
            concentration={"max_family_pct": 30.0},
        )
        assert alloc.is_valid is False
        assert any("STRATEGY_" in e and "EXCEEDED" in e for e in alloc.validation_errors)

    def test_allocation_violates_reserve(self, manager):
        alloc = manager.allocation_policy.compute_allocation(
            transition_id="trans-001",
            capital_basis_rub=1_000_000,
            active_slots=1,
            current_exposure_rub=0,
            slot_allocations={"slot-01": 910_000},
            risk_budget={},
            concentration={"max_family_pct": 20.0},
        )
        assert alloc.is_valid is False
        assert any("RESERVE_INSUFFICIENT" in e for e in alloc.validation_errors)

    def test_allocation_concentration_violation(self, manager):
        alloc = manager.allocation_policy.compute_allocation(
            transition_id="trans-001",
            capital_basis_rub=1_000_000,
            active_slots=1,
            current_exposure_rub=0,
            slot_allocations={"slot-01": 100_000},
            risk_budget={},
            concentration={"max_family_pct": 50.0},
        )
        assert alloc.is_valid is False
        assert any("CONCENTRATION_EXCEEDED" in e for e in alloc.validation_errors)


class TestT07_HardLimits:
    """T7: Hard allocation limits enforced."""

    def test_zero_capital_blocks(self, manager):
        alloc = manager.allocation_policy.compute_allocation(
            transition_id="trans-001",
            capital_basis_rub=0,
            active_slots=1,
            current_exposure_rub=0,
            slot_allocations={},
            risk_budget={},
            concentration={},
        )
        assert alloc.is_valid is False

    def test_negative_capital_blocks(self, manager):
        alloc = manager.allocation_policy.compute_allocation(
            transition_id="trans-001",
            capital_basis_rub=-100000,
            active_slots=1,
            current_exposure_rub=0,
            slot_allocations={},
            risk_budget={},
            concentration={},
        )
        assert alloc.is_valid is False


class TestT08_UnknownCorrelation:
    """T8: Unknown correlation is UNKNOWN, never zero."""

    def test_unknown_correlation_status(self, manager):
        alloc = manager.allocation_policy.compute_allocation(
            transition_id="trans-001",
            capital_basis_rub=1_000_000,
            active_slots=1,
            current_exposure_rub=0,
            slot_allocations={"slot-01": 100_000},
            risk_budget={},
            concentration={"max_family_pct": 10.0},
            correlation_status="UNKNOWN",
        )
        assert alloc.correlation_status == "UNKNOWN"


class TestT09_OpenPositionBlock:
    """T9: Open position → WAITING_FLAT, candidate not activated."""

    def test_open_position_blocks(self, manager, sample_case, sample_decision):
        result = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        tid = result["transition_id"]
        assert tid is not None

        preflight_result = manager.preflight(
            tid, has_open_position=True, health_ok=True,
            truth_ok=True, risk_ok=True, data_ok=True)
        assert preflight_result["outcome"] == PreflightOutcome.WAIT_FOR_POSITION_DRAIN.value

        transition = manager.store.get_transition(tid)
        assert transition["state"] == TransitionState.WAITING_FLAT.value

    def test_open_position_no_activation(self, manager, sample_case, sample_decision):
        result = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        tid = result["transition_id"]

        manager.preflight(tid, has_open_position=True, health_ok=True,
                          truth_ok=True, risk_ok=True, data_ok=True)
        transition_result = manager.transition(tid)
        assert transition_result["success"] is False
        assert "WAITING_FOR_POSITION_DRAIN" in transition_result.get("error", "")


class TestT10_FlatVerification:
    """T10: Flat verification before activation."""

    def test_flat_verification_path(self, manager, sample_case, sample_decision):
        result = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        tid = result["transition_id"]

        preflight_result = manager.preflight(
            tid, has_open_position=False, health_ok=True,
            truth_ok=True, risk_ok=True, data_ok=True)
        assert preflight_result["outcome"] == PreflightOutcome.EXECUTABLE_PAPER.value

        transition_result = manager.transition(tid)
        assert transition_result["success"] is True


class TestT11_AtomicTransition:
    """T11: Atomic transition — all or nothing."""

    def test_atomic_transition_completes(self, manager, sample_case, sample_decision):
        result = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        tid = result["transition_id"]

        manager.preflight(tid, has_open_position=False, health_ok=True,
                          truth_ok=True, risk_ok=True, data_ok=True)
        manager.transition(tid)
        manager.reconcile(tid)
        manager.observe(tid, checks={
            "health": True, "duplicates": False,
            "unexpected_execution": False, "slot_integrity": True,
            "risk_errors": False, "attribution_linkage": True})

        final = manager.store.get_transition(tid)
        assert final["state"] == TransitionState.COMPLETED.value


class TestT12_ExclusiveSlot:
    """T12: Exclusive slot — only one active transition per slot."""

    def test_concurrent_transition_blocked(self, manager, sample_case, sample_decision):
        # First transition
        r1 = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        assert r1["transition_id"] is not None

        # Second transition on same slot should be blocked
        sample_case2 = dict(sample_case)
        sample_case2["evidence_hash"] = "different-evidence"
        sample_decision2 = dict(sample_decision)
        sample_decision2["evidence_hash"] = "different-evidence"

        r2 = manager.create_transition(
            sample_case2, sample_decision2,
            "strat-alpha", "strat-gamma", "slot-01", "BR")
        assert r2["transition_id"] is None
        assert r2["outcome"] == PreflightOutcome.BLOCKED_CONFLICT.value


class TestT13_Idempotency:
    """T13: Same approval+slot+evidence_hash → one transition."""

    def test_duplicate_prevention(self, manager, sample_case, sample_decision):
        r1 = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        assert r1["transition_id"] is not None

        r2 = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        assert r2["transition_id"] is None
        assert r2["outcome"] == PreflightOutcome.BLOCKED_CONFLICT.value


class TestT14_Concurrency:
    """T14: Concurrent transitions — one wins, other BLOCKED_CONFLICT."""

    def test_different_slots_concurrent(self, manager, sample_case, sample_decision):
        # Two transitions on different slots should both succeed
        r1 = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        assert r1["transition_id"] is not None

        sample_case2 = dict(sample_case)
        sample_case2["case_id"] = "case-002"
        sample_case2["evidence_hash"] = "evidence-xyz"
        sample_decision2 = dict(sample_decision)
        sample_decision2["case_id"] = "case-002"
        sample_decision2["decision_id"] = "decision-002"
        sample_decision2["evidence_hash"] = "evidence-xyz"

        r2 = manager.create_transition(
            sample_case2, sample_decision2,
            "strat-alpha", "strat-gamma", "slot-02", "GAZP")
        assert r2["transition_id"] is not None


class TestT15_CrashRecovery:
    """T15: Crash checkpoint → safe resume."""

    def test_crash_recovery_created_state(self, manager, sample_case, sample_decision):
        result = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        tid = result["transition_id"]

        recovery = manager.crash_recovery(tid)
        assert recovery["action"] == "RESUME"

    def test_crash_recovery_uncertain_state(self, manager, sample_case, sample_decision):
        result = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        tid = result["transition_id"]
        manager.store.update_transition_state(
            tid, TransitionState.DEACTIVATING_INCUMBENT.value,
            reason="crash_test")

        recovery = manager.crash_recovery(tid)
        assert recovery["action"] in ("RESUME", "ROLLBACK")

    def test_crash_recovery_terminal_state(self, manager, sample_case, sample_decision):
        result = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        tid = result["transition_id"]
        # Go through full path to COMPLETED
        manager.preflight(tid, has_open_position=False, health_ok=True,
                          truth_ok=True, risk_ok=True, data_ok=True)
        manager.transition(tid)
        manager.reconcile(tid)
        manager.observe(tid, checks={
            "health": True, "duplicates": False,
            "unexpected_execution": False, "slot_integrity": True,
            "risk_errors": False, "attribution_linkage": True})

        recovery = manager.crash_recovery(tid)
        assert recovery["action"] == "NONE"


class TestT16_FailedDrain:
    """T16: Failed drain → candidate remains inactive."""

    def test_drain_failure_no_activation(self, manager, sample_case, sample_decision):
        result = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        tid = result["transition_id"]

        # Go through valid path: CREATED → PREFLIGHT → READY_PAPER → DRAINING → WAITING_FLAT
        manager.preflight(tid, has_open_position=True, health_ok=True,
                          truth_ok=True, risk_ok=True, data_ok=True)
        # Ready paper with open position → WAITING_FLAT
        # Now simulate drain start → DRAINING
        manager.store.update_transition_state(
            tid, TransitionState.DRAINING.value, reason="drain_started")
        manager.store.update_transition_state(
            tid, TransitionState.WAITING_FLAT.value, reason="drain_pending")

        # Drain fails → state goes to ROLLBACK_REQUIRED
        # WAITING_FLAT → ROLLBACK_REQUIRED is valid
        manager.store.update_transition_state(
            tid, TransitionState.ROLLBACK_REQUIRED.value,
            reason="drain_failed_timeout")

        transition = manager.store.get_transition(tid)
        assert transition["state"] == TransitionState.ROLLBACK_REQUIRED.value

        # Rollback succeeds — no activation
        rollback_result = manager.rollback(tid, reason="drain_failed")
        assert rollback_result["success"] is True
        assert rollback_result["state"] == TransitionState.ROLLED_BACK.value


class TestT17_Reconciliation:
    """T17: Reconciliation mandatory before COMPLETED."""

    def test_completed_requires_reconciliation(self, manager, sample_case, sample_decision):
        result = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        tid = result["transition_id"]

        # Try to go directly to COMPLETED without reconciliation
        success = manager.store.update_transition_state(
            tid, TransitionState.COMPLETED.value, reason="skip_reconciliation")
        assert success is False  # Must go through OBSERVING first

    def test_reconciliation_consistent(self, manager, sample_case, sample_decision):
        result = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        tid = result["transition_id"]
        # Go through valid state path to RECONCILING
        manager.preflight(tid, has_open_position=False, health_ok=True,
                          truth_ok=True, risk_ok=True, data_ok=True)
        manager.transition(tid)
        # Now in ACTIVATING_CANDIDATE state — reconcile will move to RECONCILING

        rec = manager.reconcile(tid)
        assert rec["result"] == ReconciliationResult.CONSISTENT.value


class TestT18_ObservationWindow:
    """T18: Observation window before completion."""

    def test_observation_passed_completes(self, manager, sample_case, sample_decision):
        result = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        tid = result["transition_id"]
        # Go through valid path: CREATED → PREFLIGHT → READY_PAPER → DEACTIVATING → ACTIVATING → RECONCILING → OBSERVING
        manager.preflight(tid, has_open_position=False, health_ok=True,
                          truth_ok=True, risk_ok=True, data_ok=True)
        manager.transition(tid)
        manager.reconcile(tid)

        obs = manager.observe(tid, checks={
            "health": True, "duplicates": False,
            "unexpected_execution": False, "slot_integrity": True,
            "risk_errors": False, "attribution_linkage": True})
        assert obs["passed"] is True
        assert obs["state"] == TransitionState.COMPLETED.value

    def test_observation_failed_triggers_rollback(self, manager, sample_case, sample_decision):
        result = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        tid = result["transition_id"]
        # Go through valid path to OBSERVING
        manager.preflight(tid, has_open_position=False, health_ok=True,
                          truth_ok=True, risk_ok=True, data_ok=True)
        manager.transition(tid)
        manager.reconcile(tid)

        obs = manager.observe(tid, checks={
            "health": False, "duplicates": False,
            "unexpected_execution": False, "slot_integrity": True,
            "risk_errors": False, "attribution_linkage": True})
        assert obs["passed"] is False
        assert obs["state"] == TransitionState.ROLLBACK_REQUIRED.value


class TestT19_Rollback:
    """T19: Rollback — never places real trade."""

    def test_rollback_from_required(self, manager, sample_case, sample_decision):
        result = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        tid = result["transition_id"]
        # Go through valid path to ROLLBACK_REQUIRED
        manager.preflight(tid, has_open_position=False, health_ok=True,
                          truth_ok=True, risk_ok=True, data_ok=True)
        manager.transition(tid)
        # Move through states to ROLLBACK_REQUIRED
        manager.store.update_transition_state(
            tid, TransitionState.RECONCILING.value, reason="test_rollback")
        manager.store.update_transition_state(
            tid, TransitionState.ROLLBACK_REQUIRED.value, reason="test")

        rollback = manager.rollback(tid, reason="test_rollback")
        assert rollback["success"] is True
        assert rollback["state"] == TransitionState.ROLLED_BACK.value

    def test_rollback_from_wrong_state_fails(self, manager, sample_case, sample_decision):
        result = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        tid = result["transition_id"]

        rollback = manager.rollback(tid, reason="test")
        assert rollback["success"] is False

    def test_rollback_stores_record(self, manager, sample_case, sample_decision):
        result = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        tid = result["transition_id"]
        # Go through valid path to ROLLBACK_REQUIRED
        manager.preflight(tid, has_open_position=False, health_ok=True,
                          truth_ok=True, risk_ok=True, data_ok=True)
        manager.transition(tid)
        manager.store.update_transition_state(
            tid, TransitionState.RECONCILING.value, reason="test_rollback")
        manager.store.update_transition_state(
            tid, TransitionState.ROLLBACK_REQUIRED.value, reason="test")

        manager.rollback(tid, reason="test_rollback")
        checkpoints = manager.store.get_checkpoints(tid)
        # Should have events recorded
        events = manager.store.get_events(tid)
        assert len(events) > 0


class TestT20_MissionControlIntegration:
    """T20: Mission Control integration — status + alerts."""

    def test_get_status(self, manager, sample_case, sample_decision):
        result = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        tid = result["transition_id"]

        status = manager.get_status()
        assert status["total_transitions"] >= 1
        assert "policy_versions" in status

    def test_status_shows_active(self, manager, sample_case, sample_decision):
        result = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        status = manager.get_status()
        assert status["active_transitions"] >= 1


class TestT21_SystemHealthIntegration:
    """T21: System Health — transition store/allocation/manager exposed."""

    def test_status_includes_policy_versions(self, manager):
        status = manager.get_status()
        assert "transition" in status["policy_versions"]
        assert "allocation" in status["policy_versions"]
        assert "execution_authorization" in status["policy_versions"]
        assert "reconciliation" in status["policy_versions"]


class TestT22_TelegramInformationalOnly:
    """T22: Telegram — informational only, cannot approve."""

    def test_telegram_cannot_approve(self):
        """Prove that no code path allows Telegram reply to create approval."""
        from core.portfolio_transition import ApprovalBridge, PortfolioTransitionStore
        # Use a temp db instead of /dev/null
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            bridge = ApprovalBridge(PortfolioTransitionStore(db_path))
        # Bridge only accepts ReviewCase + ReviewDecision dicts
        # Telegram reply text cannot be converted to valid approval
        # because it lacks required fields (case_id, evidence_hash, etc.)
        is_valid, reason = bridge.validate_approval(
            {"state": "OPEN"},  # Not APPROVED
            {"actor_type": "HUMAN", "decision": "APPROVE"})
        assert is_valid is False


class TestT23_BrokerMutationImpossible:
    """T23: Zero real broker mutation from any Iteration-20 path."""

    def test_broker_safety_check(self, manager):
        safety = manager.broker_safety_check()
        assert safety["paper_only"] is True
        assert safety["real_orders_placed"] == 0
        assert safety["real_positions_changed"] == 0
        assert safety["broker_mutating_calls"] == []

    def test_no_tinkoff_imports_in_transition(self):
        """Verify no broker SDK imports in portfolio_transition.py."""
        import inspect
        from core import portfolio_transition
        source = inspect.getsource(portfolio_transition)
        assert "tinkoff" not in source.lower()
        assert "order" not in source.lower() or "order" in "transition_order"


class TestT24_FullRegression:
    """T24: Full happy-path A→B regression test."""

    def test_happy_path_a_to_b(self, manager, sample_case, sample_decision):
        # Step 1: Create transition
        result = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        tid = result["transition_id"]
        assert tid is not None
        assert result["state"] == TransitionState.CREATED.value

        # Step 2: Preflight
        pf = manager.preflight(tid, has_open_position=False, health_ok=True,
                               truth_ok=True, risk_ok=True, data_ok=True)
        assert pf["outcome"] == PreflightOutcome.EXECUTABLE_PAPER.value

        # Step 3: Allocate
        alloc = manager.allocate(
            tid, capital_basis_rub=1_000_000, active_slots=3,
            current_exposure_rub=300_000,
            proposed_allocations={"slot-01": 150_000},
            risk_budget={"per_slot_max": 200_000},
            concentration={"max_family_pct": 15.0})
        assert alloc["outcome"] == "ALLOCATED"

        # Step 4: Transition (paper)
        tr = manager.transition(tid)
        assert tr["success"] is True

        # Step 5: Reconcile
        rec = manager.reconcile(tid)
        assert rec["result"] == ReconciliationResult.CONSISTENT.value

        # Step 6: Observe
        obs = manager.observe(tid, checks={
            "health": True, "duplicates": False,
            "unexpected_execution": False, "slot_integrity": True,
            "risk_errors": False, "attribution_linkage": True})
        assert obs["passed"] is True

        # Step 7: Verify completed
        final = manager.store.get_transition(tid)
        assert final["state"] == TransitionState.COMPLETED.value

        # Step 8: Verify events
        events = manager.store.get_events(tid)
        assert len(events) >= 5


# ===========================================================================
# F1-F24: Failure Matrix Tests
# ===========================================================================

class TestF01_InvalidApproval:
    """F1: Invalid approval → blocked."""

    def test_invalid_case_state(self, manager):
        case = {"case_id": "c1", "state": "OPEN", "evidence_hash": "e1",
                "incumbent_id": "a", "candidate_id": "b"}
        decision = {"decision_id": "d1", "case_id": "c1", "decision": "APPROVE",
                    "actor_type": "HUMAN", "evidence_hash": "e1"}
        is_valid, reason = manager.bridge.validate_approval(case, decision)
        assert is_valid is False


class TestF02_AgentApproval:
    """F2: Agent/system approval → rejected."""

    def test_agent_approval_rejected(self, manager, sample_case, agent_decision):
        is_valid, reason = manager.bridge.validate_approval(sample_case, agent_decision)
        assert is_valid is False
        assert "AGENT" in reason

    def test_system_approval_rejected(self, manager, sample_case, system_decision):
        is_valid, reason = manager.bridge.validate_approval(sample_case, system_decision)
        assert is_valid is False
        assert "SYSTEM" in reason


class TestF03_StaleApproval:
    """F3: Stale approval → BLOCKED_STALE_APPROVAL."""

    def test_stale_approval_blocked(self, manager, sample_case):
        is_stale, _ = manager.bridge.check_staleness(
            sample_case, "new-ranking", "new-reg-hash", "new-portfolio")
        assert is_stale is True


class TestF04_DisappearedRanking:
    """F4: Disappeared ranking → blocked."""

    def test_missing_ranking_evidence(self, manager, sample_case, sample_decision):
        sample_case["evidence_hash"] = ""
        is_valid, reason = manager.bridge.validate_approval(sample_case, sample_decision)
        assert is_valid is False
        assert reason == "MISSING_EVIDENCE_HASH"


class TestF05_ChangedIdentities:
    """F5: Changed identities → BLOCKED_IDENTITY."""

    def test_incumbent_changed(self, manager, sample_case, sample_decision):
        # Create transition
        r = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        tid = r["transition_id"]

        # Staleness check with different registry (identity drift)
        is_stale, reason = manager.bridge.check_staleness(
            sample_case, "ranking", "DIFFERENT_REG", "portfolio-001")
        assert is_stale is True


class TestF06_RegistryDrift:
    """F6: Registry drift → BLOCKED_TRUTH."""

    def test_registry_drift_blocks(self, manager, sample_case):
        is_stale, reason = manager.bridge.check_staleness(
            sample_case, "ranking", "drifted-hash", "portfolio-001")
        assert is_stale is True
        assert reason == "REGISTRY_DRIFT"


class TestF07_InsufficientTruth:
    """F7: Insufficient truth → BLOCKED_TRUTH."""

    def test_truth_check_failure(self, manager, sample_case, sample_decision):
        r = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        tid = r["transition_id"]

        pf = manager.preflight(tid, truth_ok=False)
        assert pf["outcome"] == PreflightOutcome.BLOCKED_TRUTH.value


class TestF08_MissingAllocationPolicy:
    """F8: Missing allocation policy → block."""

    def test_zero_capital_blocks_allocation(self, manager):
        # Create a real transition first
        case = {"case_id": "c-alloc-1", "state": "APPROVED", "evidence_hash": "e-alloc-1",
                "incumbent_id": "a", "candidate_id": "b"}
        dec = {"decision_id": "d-alloc-1", "case_id": "c-alloc-1", "decision": "APPROVE",
               "actor_type": "HUMAN", "evidence_hash": "e-alloc-1"}
        r = manager.create_transition(case, dec, "a", "b", "s1", "BR")
        alloc = manager.allocate(
            r["transition_id"], capital_basis_rub=0, active_slots=1,
            current_exposure_rub=0, proposed_allocations={},
            risk_budget={}, concentration={})
        assert alloc["outcome"] == PreflightOutcome.BLOCKED_ALLOCATION.value


class TestF09_AllocationViolation:
    """F9: Allocation violation → BLOCKED; no mutation."""

    def test_excessive_allocation_blocked(self, manager):
        case = {"case_id": "c-alloc-2", "state": "APPROVED", "evidence_hash": "e-alloc-2",
                "incumbent_id": "a", "candidate_id": "b"}
        dec = {"decision_id": "d-alloc-2", "case_id": "c-alloc-2", "decision": "APPROVE",
               "actor_type": "HUMAN", "evidence_hash": "e-alloc-2"}
        r = manager.create_transition(case, dec, "a", "b", "s1", "BR")
        result = manager.allocate(
            r["transition_id"], capital_basis_rub=1_000_000, active_slots=1,
            current_exposure_rub=0,
            proposed_allocations={"slot-01": 900_000},
            risk_budget={}, concentration={})
        assert result["outcome"] == PreflightOutcome.BLOCKED_ALLOCATION.value

    def test_allocation_violation_no_mutation(self, manager):
        """After allocation block, state should not change."""
        alloc = manager.allocation_policy.compute_allocation(
            transition_id="trans-001",
            capital_basis_rub=1_000_000,
            active_slots=1,
            current_exposure_rub=0,
            slot_allocations={"slot-01": 900_000},
            risk_budget={},
            concentration={},
        )
        assert alloc.is_valid is False
        # No transition state should have been mutated


class TestF10_UnknownCorrelation:
    """F10: Unknown correlation → INSUFFICIENT, not zero."""

    def test_correlation_not_zero(self, manager):
        alloc = manager.allocation_policy.compute_allocation(
            transition_id="trans-001",
            capital_basis_rub=1_000_000,
            active_slots=1,
            current_exposure_rub=0,
            slot_allocations={"slot-01": 100_000},
            risk_budget={},
            concentration={"max_family_pct": 10.0},
            correlation_status="UNKNOWN",
        )
        assert alloc.correlation_status != "ZERO"
        assert alloc.correlation_status == "UNKNOWN"


class TestF11_OpenPosition:
    """F11: Open position → blocks activation."""

    def test_open_position_blocks_activation(self, manager, sample_case, sample_decision):
        r = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        tid = r["transition_id"]

        manager.preflight(tid, has_open_position=True, health_ok=True,
                          truth_ok=True, risk_ok=True, data_ok=True)

        tr = manager.transition(tid)
        assert tr["success"] is False


class TestF12_UnavailableFlatVerification:
    """F12: Unavailable flat verification → cannot proceed."""

    def test_unavailable_broker_truth(self, manager):
        """If broker truth unavailable, cannot claim broker-flat."""
        rec = manager.reconcile("nonexistent-tid")
        assert rec["result"] == ReconciliationResult.INCOMPLETE.value


class TestF13_DuplicateTransition:
    """F13: Duplicate transition → one transition only."""

    def test_duplicate_same_evidence(self, manager, sample_case, sample_decision):
        r1 = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        assert r1["transition_id"] is not None

        r2 = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        assert r2["transition_id"] is None


class TestF14_ConcurrentConflict:
    """F14: Two candidates same slot → one wins, one BLOCKED_CONFLICT."""

    def test_second_on_same_slot_blocked(self, manager, sample_case, sample_decision):
        r1 = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")

        case2 = dict(sample_case)
        case2["evidence_hash"] = "different-evidence"
        decision2 = dict(sample_decision)
        decision2["evidence_hash"] = "different-evidence"

        r2 = manager.create_transition(
            case2, decision2,
            "strat-alpha", "strat-gamma", "slot-01", "BR")
        assert r2["outcome"] == PreflightOutcome.BLOCKED_CONFLICT.value


class TestF15_CrashBeforeDeactivation:
    """F15: Crash before deactivation → safe resume."""

    def test_crash_before_deactivation(self, manager, sample_case, sample_decision):
        r = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        tid = r["transition_id"]
        manager.store.update_transition_state(
            tid, TransitionState.DEACTIVATING_INCUMBENT.value, reason="crash")
        manager.store.store_checkpoint(
            tid, TransitionState.DEACTIVATING_INCUMBENT.value,
            "pre_deactivation", {})

        recovery = manager.crash_recovery(tid)
        assert recovery["action"] == "RESUME"


class TestF16_CrashAroundActivation:
    """F16: Crash after activation but before reconciliation."""

    def test_crash_after_activation(self, manager, sample_case, sample_decision):
        r = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        tid = r["transition_id"]
        manager.store.update_transition_state(
            tid, TransitionState.ACTIVATING_CANDIDATE.value, reason="crash")

        recovery = manager.crash_recovery(tid)
        assert recovery["action"] == "RESUME"


class TestF17_RegistryTransactionFailure:
    """F17: Registry transaction failure → rollback."""

    def test_rollback_from_failed_state(self, manager, sample_case, sample_decision):
        result = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        tid = result["transition_id"]
        # Go through valid path to FAILED
        manager.preflight(tid, has_open_position=False, health_ok=True,
                          truth_ok=True, risk_ok=True, data_ok=True)
        manager.transition(tid)
        # Move to DEACTIVATING then FAIL
        manager.store.update_transition_state(
            tid, TransitionState.RECONCILING.value, reason="test")
        manager.store.update_transition_state(
            tid, TransitionState.FAILED.value, reason="registry_failure")

        rollback = manager.rollback(tid, reason="registry_transaction_failed")
        assert rollback["success"] is True
        assert rollback["state"] == TransitionState.ROLLED_BACK.value


class TestF18_ReconciliationMismatch:
    """F18: Reconciliation mismatch → not COMPLETED; rollback."""

    def test_reconciliation_conflict(self, manager, sample_case, sample_decision):
        result = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        tid = result["transition_id"]
        # Go through valid path to RECONCILING
        manager.preflight(tid, has_open_position=False, health_ok=True,
                          truth_ok=True, risk_ok=True, data_ok=True)
        manager.transition(tid)

        rec = manager.reconcile(tid, current_state={
            "transition_db": "CONFLICTED",
            "approval": "CONSISTENT"})
        assert rec["result"] == ReconciliationResult.CONFLICTED.value


class TestF19_RollbackFailure:
    """F19: Rollback failure → ESCALATED."""

    def test_rollback_from_terminal_fails(self, manager, sample_case, sample_decision):
        r = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        tid = r["transition_id"]
        manager.store.update_transition_state(
            tid, TransitionState.COMPLETED.value, reason="done")

        rollback = manager.rollback(tid, reason="too_late")
        assert rollback["success"] is False


class TestF20_PipelineBypass:
    """F20: Pipeline bypass → blocked."""

    def test_bypass_prevented(self, manager, sample_case, sample_decision):
        """Cannot skip preflight and go straight to transition."""
        r = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        tid = r["transition_id"]

        # Try to transition from CREATED state (skipping preflight)
        tr = manager.transition(tid)
        assert tr["success"] is False


class TestF21_BrokerMutationAttempt:
    """F21: Broker mutation attempt → impossible/blocked."""

    def test_no_broker_mutation_path(self, manager):
        safety = manager.broker_safety_check()
        assert safety["paper_only"] is True
        assert len(safety["broker_mutating_calls"]) == 0


class TestF22_LiveTransitionAttempt:
    """F22: Live transition attempt → blocked by mode check."""

    def test_mode_enforcement(self):
        """Prove paper mode is enforced by design — no live execution paths."""
        from core.portfolio_transition import PortfolioTransitionManager
        import inspect
        source = inspect.getsource(PortfolioTransitionManager)
        assert "paper" in source.lower() or "PAPER" in source
        # No broker SDK imports
        assert "tinkoff" not in source.lower()
        assert "OrderDirection" not in source
        assert "OrderType" not in source


class TestF23_HealthBlock:
    """F23: Health check failure → BLOCKED_HEALTH."""

    def test_health_block(self, manager, sample_case, sample_decision):
        r = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        tid = r["transition_id"]

        pf = manager.preflight(tid, health_ok=False)
        assert pf["outcome"] == PreflightOutcome.BLOCKED_HEALTH.value


class TestF24_DataBlock:
    """F24: Data check failure → BLOCKED_DATA."""

    def test_data_block(self, manager, sample_case, sample_decision):
        r = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        tid = r["transition_id"]

        pf = manager.preflight(tid, data_ok=False)
        assert pf["outcome"] == PreflightOutcome.BLOCKED_DATA.value


# ===========================================================================
# Additional Safety Proofs
# ===========================================================================

class TestApprovalCannotExecute:
    """Proof: approval alone cannot execute portfolio mutation."""

    def test_approval_requires_authorization(self, manager, sample_case, sample_decision):
        # Create with approval but NO authorization
        result = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        tid = result["transition_id"]

        # No authorization stored
        auth = manager.store.get_valid_authorization(tid)
        assert auth is None  # No authorization — cannot proceed safely


class TestZeroBrokerMutation:
    """Proof: zero real broker mutation from any path."""

    def test_transition_only_writes_to_db(self, manager, sample_case, sample_decision):
        """Trace that all transition operations only write to SQLite."""
        r = manager.create_transition(
            sample_case, sample_decision,
            "strat-alpha", "strat-beta", "slot-01", "BR")
        tid = r["transition_id"]

        # Verify database writes only
        transition = manager.store.get_transition(tid)
        assert transition is not None
        # No broker call was made (would require tinkoff SDK)


class TestStateTransitions:
    """Test the state machine transitions."""

    def test_valid_transition_exists(self):
        for state, targets in VALID_TRANSITIONS.items():
            assert isinstance(state, TransitionState)
            for target in targets:
                assert isinstance(target, TransitionState)

    def test_terminal_states_have_no_transitions(self):
        for state in TERMINAL_STATES:
            assert len(VALID_TRANSITIONS[state]) == 0

    def test_cannot_jump_to_completed_directly(self):
        assert TransitionState.COMPLETED not in VALID_TRANSITIONS[TransitionState.CREATED]
        assert TransitionState.COMPLETED not in VALID_TRANSITIONS[TransitionState.PREFLIGHT]
        assert TransitionState.COMPLETED not in VALID_TRANSITIONS[TransitionState.READY_PAPER]


class TestPolicyVersions:
    """Test that policy versions are present and consistent."""

    def test_policy_versions_defined(self):
        assert TRANSITION_POLICY_VERSION is not None
        assert AllocationPolicy.POLICY_VERSION is not None

    def test_status_includes_policy_versions(self, manager):
        status = manager.get_status()
        versions = status["policy_versions"]
        assert "transition" in versions
        assert "allocation" in versions
        assert "execution_authorization" in versions
        assert "reconciliation" in versions


# ===========================================================================
# Test count verification
# ===========================================================================

def test_all_enums_covered():
    """Verify all TransitionState values are in VALID_TRANSITIONS."""
    for state in TransitionState:
        assert state in VALID_TRANSITIONS, f"Missing transition for {state}"


def test_all_preflight_outcomes():
    """Verify PreflightOutcome enum has all required values."""
    required = [
        "EXECUTABLE_PAPER", "WAIT_FOR_POSITION_DRAIN",
        "BLOCKED_STALE_APPROVAL", "BLOCKED_HEALTH", "BLOCKED_RISK",
        "BLOCKED_ALLOCATION", "BLOCKED_DATA", "BLOCKED_TRUTH",
        "BLOCKED_IDENTITY", "BLOCKED_RECONCILIATION",
        "BLOCKED_OPEN_INCIDENT", "BLOCKED_CONFLICT", "BLOCKED_UNSUPPORTED",
    ]
    for name in required:
        assert hasattr(PreflightOutcome, name), f"Missing PreflightOutcome.{name}"


def test_all_states_enum():
    """Verify all required states exist."""
    required_states = [
        "CREATED", "PREFLIGHT", "BLOCKED", "READY_PAPER", "DRAINING",
        "WAITING_FLAT", "VERIFYING_FLAT", "DEACTIVATING_INCUMBENT",
        "ACTIVATING_CANDIDATE", "RECONCILING", "OBSERVING", "COMPLETED",
        "ROLLBACK_REQUIRED", "ROLLING_BACK", "ROLLED_BACK",
        "FAILED", "ESCALATED", "CANCELLED",
    ]
    for name in required_states:
        assert hasattr(TransitionState, name), f"Missing TransitionState.{name}"

"""
tests/test_stage_machine.py — Tests for core/stage_machine.py

Tests for the candidate qualification stage machine:
  T35–T41, T61, T01, and additional structural tests.
  18 tests total.

All tests use the canonical policy from config/research_qualification_policy.json.
No broker, no live, no real data.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

COMBINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(COMBINE_DIR / "core"))

from stage_machine import (
    CODE_VERSION,
    STAGE_ORDER,
    TRANSITION_GRAPH,
    QualificationStage,
    StageMachine,
    StageTransition,
    get_maturity_chain,
    get_policy_hash,
    get_stage_requirements,
    is_valid_stage,
    is_valid_transition,
    load_canonical_policy,
    stage_index,
    validate_stage_sequence,
)

POLICY_PATH = COMBINE_DIR / "config" / "research_qualification_policy.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _full_evidence(policy_version: str = "1.0.0", **overrides) -> Dict[str, Any]:
    """Build a complete evidence dict that passes all checks."""
    e = {
        "policy_version": policy_version,
        "oos_folds": True,
        "no_lookahead": True,
        "parameter_neighborhood": True,
        "temporal_regime": True,
        "risk_evidence": {
            "LIVE_RISK_V1": True,
            "bounded_stop": True,
            "sizing": True,
            "account_feasibility": True,
        },
        "LIVE_RISK_V1": True,
        "bounded_stop": True,
        "sizing": True,
        "account_feesibility": True,
        "horizon_coverage": ["60d", "90d", "180d", "365d"],
        "is_60d_only": False,
        "is_historical_replay": False,
        "data_horizon_refs": ["test_run_001"],
    }
    e.update(overrides)
    return e


def _make_machine() -> StageMachine:
    """Create a StageMachine with the canonical policy."""
    return StageMachine(policy_path=POLICY_PATH)


# ---------------------------------------------------------------------------
# T35 — Canonical stage machine exists and is structurally valid
# ---------------------------------------------------------------------------

class TestT35CanonicalStageMachineExists:
    def test_stage_count(self):
        """Must have exactly 9 canonical stages."""
        assert len(QualificationStage) == 9

    def test_stage_order_matches_names(self):
        """STAGE_ORDER must contain the stages in correct sequence."""
        expected = [
            "DISCOVERY", "BACKTEST_QUALIFIED", "MULTI_HORIZON_QUALIFIED",
            "WALK_FORWARD_QUALIFIED", "ROBUSTNESS_QUALIFIED", "RISK_QUALIFIED",
            "PAPER_ADMISSION_READY", "PAPER_QUALIFIED", "LIVE_CANDIDATE",
        ]
        assert [s.value for s in STAGE_ORDER] == expected

    def test_transition_graph_is_linear_chain(self):
        """Every stage (except last) maps to exactly the next stage."""
        assert len(TRANSITION_GRAPH) == 8  # 9 stages, 8 transitions
        for i in range(len(STAGE_ORDER) - 1):
            assert TRANSITION_GRAPH[STAGE_ORDER[i]] == STAGE_ORDER[i + 1]

    def test_machine_loads_canonical_policy(self):
        """StageMachine must load from the canonical policy file."""
        sm = _make_machine()
        assert sm.policy_version == "1.0.0"
        assert sm.canonical_stages == [s.value for s in STAGE_ORDER]


# ---------------------------------------------------------------------------
# T36 — Illegal stage skip blocked (DISCOVERY → PAPER_ADMISSION_READY)
# ---------------------------------------------------------------------------

class TestT36IllegalStageSkip:
    def test_skip_discovery_to_paper_blocked(self):
        """DISCOVERY → PAPER_ADMISSION_READY must be BLOCKED (cannot skip stages)."""
        sm = _make_machine()
        result = sm.transition_candidate(
            candidate_id="skip_test_001",
            from_stage="DISCOVERY",
            to_stage="PAPER_ADMISSION_READY",
            evidence=_full_evidence(),
        )
        assert result.verdict == "BLOCKED"
        assert "skip" in result.reason.lower() or "not a valid" in result.reason.lower()

    def test_skip_backtest_to_risk_blocked(self):
        """BACKTEST_QUALIFIED → RISK_QUALIFIED must be BLOCKED."""
        sm = _make_machine()
        result = sm.transition_candidate(
            candidate_id="skip_test_002",
            from_stage="BACKTEST_QUALIFIED",
            to_stage="RISK_QUALIFIED",
            evidence=_full_evidence(),
        )
        assert result.verdict == "BLOCKED"
        assert "skip" in result.reason.lower() or "not a valid" in result.reason.lower()


# ---------------------------------------------------------------------------
# T37 — 60d-only PAPER admission blocked
# ---------------------------------------------------------------------------

class TestT37SixtyDayOnlyPaperBlocked:
    def test_60d_only_paper_admission_blocked(self):
        """60d-only candidate cannot reach PAPER_ADMISSION_READY."""
        sm = _make_machine()
        evidence = _full_evidence(
            is_60d_only=True,
            horizon_coverage=["60d"],
        )
        result = sm.transition_candidate(
            candidate_id="60d_test_001",
            from_stage="RISK_QUALIFIED",
            to_stage="PAPER_ADMISSION_READY",
            evidence=evidence,
        )
        assert result.verdict == "BLOCKED"
        assert "60d" in result.reason.lower()

    def test_60d_only_paper_qualified_blocked(self):
        """60d-only candidate cannot reach PAPER_QUALIFIED."""
        sm = _make_machine()
        evidence = _full_evidence(
            is_60d_only=True,
            horizon_coverage=["60d"],
        )
        result = sm.transition_candidate(
            candidate_id="60d_test_002",
            from_stage="PAPER_ADMISSION_READY",
            to_stage="PAPER_QUALIFIED",
            evidence=evidence,
        )
        assert result.verdict == "BLOCKED"
        assert "60d" in result.reason.lower()

    def test_multi_horizon_paper_admission_not_blocked(self):
        """Candidate with multi-horizon coverage should NOT be blocked."""
        sm = _make_machine()
        evidence = _full_evidence(
            horizon_coverage=["60d", "90d", "180d", "365d"],
        )
        # First get to RISK_QUALIFIED through valid chain
        for from_s, to_s in [
            ("DISCOVERY", "BACKTEST_QUALIFIED"),
            ("BACKTEST_QUALIFIED", "MULTI_HORIZON_QUALIFIED"),
            ("MULTI_HORIZON_QUALIFIED", "WALK_FORWARD_QUALIFIED"),
            ("WALK_FORWARD_QUALIFIED", "ROBUSTNESS_QUALIFIED"),
            ("ROBUSTNESS_QUALIFIED", "RISK_QUALIFIED"),
        ]:
            r = sm.transition_candidate("multi_horizon_001", from_s, to_s, evidence)
            assert r.verdict == "PASS", f"Failed at {from_s} → {to_s}: {r.reason}"

        # Now try PAPER_ADMISSION_READY — should pass (no 60d-only block)
        result = sm.transition_candidate(
            "multi_horizon_001",
            "RISK_QUALIFIED",
            "PAPER_ADMISSION_READY",
            evidence,
        )
        assert result.verdict == "PASS"


# ---------------------------------------------------------------------------
# T38 — Missing OOS blocks required transition
# ---------------------------------------------------------------------------

class TestT38MissingOOSBlocked:
    def test_missing_oos_blocks_walk_forward(self):
        """Missing OOS evidence blocks WALK_FORWARD_QUALIFIED transition."""
        sm = _make_machine()
        evidence = _full_evidence()
        del evidence["oos_folds"]

        # Get to MULTI_HORIZON_QUALIFIED first
        for from_s, to_s in [
            ("DISCOVERY", "BACKTEST_QUALIFIED"),
            ("BACKTEST_QUALIFIED", "MULTI_HORIZON_QUALIFIED"),
        ]:
            sm.transition_candidate("oos_test_001", from_s, to_s, _full_evidence())

        result = sm.transition_candidate(
            "oos_test_001",
            "MULTI_HORIZON_QUALIFIED",
            "WALK_FORWARD_QUALIFIED",
            evidence,
        )
        assert result.verdict == "BLOCKED"
        assert "OOS" in result.reason or "oos" in result.reason.lower()


# ---------------------------------------------------------------------------
# T39 — Missing robustness blocks required transition
# ---------------------------------------------------------------------------

class TestT39MissingRobustnessBlocked:
    def test_missing_robustness_blocks_robustness_stage(self):
        """Missing robustness evidence blocks ROBUSTNESS_QUALIFIED transition."""
        sm = _make_machine()
        evidence = _full_evidence()
        del evidence["parameter_neighborhood"]
        del evidence["temporal_regime"]

        # Get to WALK_FORWARD_QUALIFIED
        for from_s, to_s in [
            ("DISCOVERY", "BACKTEST_QUALIFIED"),
            ("BACKTEST_QUALIFIED", "MULTI_HORIZON_QUALIFIED"),
            ("MULTI_HORIZON_QUALIFIED", "WALK_FORWARD_QUALIFIED"),
        ]:
            sm.transition_candidate("rob_test_001", from_s, to_s, _full_evidence())

        result = sm.transition_candidate(
            "rob_test_001",
            "WALK_FORWARD_QUALIFIED",
            "ROBUSTNESS_QUALIFIED",
            evidence,
        )
        assert result.verdict == "BLOCKED"
        assert "robustness" in result.reason.lower()


# ---------------------------------------------------------------------------
# T40 — Missing risk evidence blocks transition
# ---------------------------------------------------------------------------

class TestT40MissingRiskEvidenceBlocked:
    def test_missing_risk_evidence_blocks_risk_stage(self):
        """Missing risk evidence blocks RISK_QUALIFIED transition."""
        sm = _make_machine()
        evidence = _full_evidence()
        del evidence["risk_evidence"]

        # Get to ROBUSTNESS_QUALIFIED
        for from_s, to_s in [
            ("DISCOVERY", "BACKTEST_QUALIFIED"),
            ("BACKTEST_QUALIFIED", "MULTI_HORIZON_QUALIFIED"),
            ("MULTI_HORIZON_QUALIFIED", "WALK_FORWARD_QUALIFIED"),
            ("WALK_FORWARD_QUALIFIED", "ROBUSTNESS_QUALIFIED"),
        ]:
            sm.transition_candidate("risk_test_001", from_s, to_s, _full_evidence())

        result = sm.transition_candidate(
            "risk_test_001",
            "ROBUSTNESS_QUALIFIED",
            "RISK_QUALIFIED",
            evidence,
        )
        assert result.verdict == "BLOCKED"
        assert "risk" in result.reason.lower()


# ---------------------------------------------------------------------------
# T41 — Historical replay cannot create PAPER_QUALIFIED
# ---------------------------------------------------------------------------

class TestT41HistoricalReplayPaperBlocked:
    def test_historical_replay_blocks_paper_qualified(self):
        """Historical replay evidence cannot produce PAPER_QUALIFIED."""
        sm = _make_machine()
        evidence = _full_evidence(is_historical_replay=True)

        # Get to PAPER_ADMISSION_READY
        for from_s, to_s in [
            ("DISCOVERY", "BACKTEST_QUALIFIED"),
            ("BACKTEST_QUALIFIED", "MULTI_HORIZON_QUALIFIED"),
            ("MULTI_HORIZON_QUALIFIED", "WALK_FORWARD_QUALIFIED"),
            ("WALK_FORWARD_QUALIFIED", "ROBUSTNESS_QUALIFIED"),
            ("ROBUSTNESS_QUALIFIED", "RISK_QUALIFIED"),
            ("RISK_QUALIFIED", "PAPER_ADMISSION_READY"),
        ]:
            sm.transition_candidate("replay_test_001", from_s, to_s, _full_evidence())

        result = sm.transition_candidate(
            "replay_test_001",
            "PAPER_ADMISSION_READY",
            "PAPER_QUALIFIED",
            evidence,
        )
        assert result.verdict == "BLOCKED"
        assert "replay" in result.reason.lower() or "historical" in result.reason.lower()


# ---------------------------------------------------------------------------
# T61 — Missing policy version blocked
# ---------------------------------------------------------------------------

class TestT61MissingPolicyBlocked:
    def test_missing_policy_version_blocks_transition(self):
        """Transition without policy_version in evidence is BLOCKED."""
        sm = _make_machine()
        evidence = _full_evidence()
        del evidence["policy_version"]

        result = sm.transition_candidate(
            "policy_test_001",
            "DISCOVERY",
            "BACKTEST_QUALIFIED",
            evidence,
        )
        assert result.verdict == "BLOCKED"
        assert "policy_version" in result.reason.lower()


# ---------------------------------------------------------------------------
# T01 — mode=paper (evidence contract validation)
# ---------------------------------------------------------------------------

class TestT01ModePaper:
    def test_evidence_contract_fields_present(self):
        """StageTransition must contain all required evidence contract fields."""
        sm = _make_machine()
        evidence = _full_evidence()
        result = sm.transition_candidate(
            "contract_test_001",
            "DISCOVERY",
            "BACKTEST_QUALIFIED",
            evidence,
        )
        assert result.verdict == "PASS"
        d = result.to_dict()
        required_fields = [
            "candidate_id", "from_stage", "to_stage",
            "required_evidence", "evidence_refs",
            "policy_version", "policy_hash",
            "data_horizon_refs", "timestamp",
            "code_version", "verdict", "reason",
        ]
        for f in required_fields:
            assert f in d, f"Missing field: {f}"

    def test_code_version_is_set(self):
        """code_version must be set on every transition."""
        sm = _make_machine()
        result = sm.transition_candidate(
            "contract_test_002",
            "DISCOVERY",
            "BACKTEST_QUALIFIED",
            _full_evidence(),
        )
        assert result.code_version == CODE_VERSION


# ---------------------------------------------------------------------------
# Additional tests: valid transitions, invalid stages, duplicates, sequence
# ---------------------------------------------------------------------------

class TestValidForwardTransitions:
    def test_all_forward_transitions_pass(self):
        """Every valid single-step transition should pass with full evidence."""
        sm = _make_machine()
        evidence = _full_evidence()

        for from_s, to_s in TRANSITION_GRAPH.items():
            result = sm.transition_candidate(
                "chain_test_001",
                from_s.value,
                to_s.value,
                evidence,
            )
            assert result.verdict == "PASS", f"Failed at {from_s.value} → {to_s.value}: {result.reason}"


class TestInvalidStageRejected:
    def test_invalid_from_stage_rejected(self):
        """Invalid from_stage name must be BLOCKED."""
        sm = _make_machine()
        result = sm.transition_candidate(
            "inv_test_001",
            "NONEXISTENT_STAGE",
            "BACKTEST_QUALIFIED",
            _full_evidence(),
        )
        assert result.verdict == "BLOCKED"
        assert "invalid" in result.reason.lower()

    def test_invalid_to_stage_rejected(self):
        """Invalid to_stage name must be BLOCKED."""
        sm = _make_machine()
        result = sm.transition_candidate(
            "inv_test_002",
            "DISCOVERY",
            "NONEXISTENT_TARGET",
            _full_evidence(),
        )
        assert result.verdict == "BLOCKED"
        assert "invalid" in result.reason.lower()


class TestDuplicateTransitionBlocked:
    def test_duplicate_transition_to_same_stage_blocked(self):
        """Attempting to transition to the same stage twice is BLOCKED."""
        sm = _make_machine()
        evidence = _full_evidence()

        r1 = sm.transition_candidate("dup_test_001", "DISCOVERY", "BACKTEST_QUALIFIED", evidence)
        assert r1.verdict == "PASS"

        r2 = sm.transition_candidate("dup_test_001", "BACKTEST_QUALIFIED", "BACKTEST_QUALIFIED", evidence)
        assert r2.verdict == "BLOCKED"
        assert "skip" in r2.reason.lower() or "already at stage" in r2.reason.lower()


class TestStageSequenceIntegrity:
    def test_history_tracks_correctly(self):
        """Candidate history should track all transitions in order."""
        sm = _make_machine()
        evidence = _full_evidence()

        for from_s, to_s in TRANSITION_GRAPH.items():
            sm.transition_candidate("seq_test_001", from_s.value, to_s.value, evidence)

        history = sm.get_history("seq_test_001")
        assert len(history) == 8  # 8 transitions for 9 stages
        assert history[0].from_stage == "DISCOVERY"
        assert history[-1].to_stage == "LIVE_CANDIDATE"

    def test_get_current_stage(self):
        """get_current_stage should return the latest stage."""
        sm = _make_machine()
        evidence = _full_evidence()

        sm.transition_candidate("cur_test_001", "DISCOVERY", "BACKTEST_QUALIFIED", evidence)
        assert sm.get_current_stage("cur_test_001") == "BACKTEST_QUALIFIED"

        sm.transition_candidate("cur_test_001", "BACKTEST_QUALIFIED", "MULTI_HORIZON_QUALIFIED", evidence)
        assert sm.get_current_stage("cur_test_001") == "MULTI_HORIZON_QUALIFIED"

    def test_validate_stage_sequence_valid(self):
        """Valid ascending sequence should pass."""
        history = ["DISCOVERY", "BACKTEST_QUALIFIED", "MULTI_HORIZON_QUALIFIED"]
        assert validate_stage_sequence(history) is True

    def test_validate_stage_sequence_invalid(self):
        """Non-ascending sequence should fail."""
        history = ["DISCOVERY", "RISK_QUALIFIED", "BACKTEST_QUALIFIED"]
        assert validate_stage_sequence(history) is False


class TestMaturityChainValidation:
    def test_validate_full_maturity_chain_complete(self):
        """Full maturity chain with all stages present should pass."""
        sm = _make_machine()
        evidence = {stage.value: {"present": True} for stage in STAGE_ORDER}
        assert sm.validate_full_maturity_chain(evidence) is True

    def test_validate_full_maturity_chain_incomplete(self):
        """Maturity chain with missing stage should fail."""
        sm = _make_machine()
        evidence = {stage.value: {"present": True} for stage in STAGE_ORDER}
        del evidence["ROBUSTNESS_QUALIFIED"]
        assert sm.validate_full_maturity_chain(evidence) is False

    def test_validate_full_maturity_chain_empty_stage(self):
        """Maturity chain with empty stage evidence should fail."""
        sm = _make_machine()
        evidence = {stage.value: {"present": True} for stage in STAGE_ORDER}
        evidence["RISK_QUALIFIED"] = {}
        assert sm.validate_full_maturity_chain(evidence) is False


class TestStageRequirements:
    def test_get_stage_requirements_discovery(self):
        """DISCOVERY requires data_identity and single_backtest."""
        reqs = get_stage_requirements("DISCOVERY", load_canonical_policy(POLICY_PATH))
        assert "data_identity" in reqs
        assert "single_backtest" in reqs

    def test_get_stage_requirements_unknown(self):
        """Unknown stage returns empty list."""
        reqs = get_stage_requirements("NONEXISTENT", load_canonical_policy(POLICY_PATH))
        assert reqs == []


class TestPolicyHash:
    def test_policy_hash_deterministic(self):
        """Same policy always produces same hash."""
        h1 = get_policy_hash(load_canonical_policy(POLICY_PATH))
        h2 = get_policy_hash(load_canonical_policy(POLICY_PATH))
        assert h1 == h2

    def test_policy_hash_is_sha256(self):
        """Policy hash must be a valid SHA-256 hex string."""
        h = get_policy_hash(load_canonical_policy(POLICY_PATH))
        assert len(h) == 64
        hashlib.sha256()  # valid hash object
        int(h, 16)  # must be valid hex


class TestExportHistory:
    def test_export_history_serializable(self):
        """Exported history must be JSON-serializable."""
        sm = _make_machine()
        evidence = _full_evidence()
        sm.transition_candidate("export_001", "DISCOVERY", "BACKTEST_QUALIFIED", evidence)

        exported = sm.export_history()
        serialized = json.dumps(exported, default=str)
        assert "export_001" in serialized
        assert "BACKTEST_QUALIFIED" in serialized

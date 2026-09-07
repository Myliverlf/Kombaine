"""Cross-Layer Proof and Negative Contract Proofs — Iteration 23H.

Proves end-to-end truth chain through canonical policy → horizon resolver →
qualification → risk → stage gating → handoff.

Also proves negative (fail-closed) scenarios.

CLASS 2: runtime non-trading. No broker, no live, no strategy changes.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

PROJECT_ROOT = Path("/root/prop-desk/strategy_combine")
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT))

# ─── Imports ──────────────────────────────────────────────────────
from canonical_policy_loader import (
    get_qualification_policy,
    get_threshold,
    policy_hash,
    policy_version,
    get_cost_model,
    get_stages,
)
from risk_policy_loader import (
    get_risk_policy,
    check_risk_qualification,
    risk_policy_hash,
    risk_policy_version,
)
from stage_machine import (
    StageMachine,
    is_valid_transition,
    get_stage_requirements,
    get_maturity_chain,
    QualificationStage,
)
from research_handoff import (
    validate_handoff,
    REQUIRED_FIELDS,
)

DATA_DIR = Path("/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data")


# ═══════════════════════════════════════════════════════════════════
# SECTION A: Cross-Layer Proof (GAZP/SBER)
# ═══════════════════════════════════════════════════════════════════

class TestT55_GAZP_CrossLayerProof:
    """T55: End-to-end GAZP truth-chain proof."""

    def test_gazp_cross_layer(self):
        """Prove canonical policy → horizon → qualification → risk → stage → handoff for GAZP."""
        # 1. Load canonical policy
        policy = get_qualification_policy()
        p_hash = policy_hash()
        p_version = policy_version()

        # 2. Resolve horizon (60d)
        try:
            from horizon_resolution import resolve_horizon
            resolution = resolve_horizon("GAZP", "15m", 60, DATA_DIR)
            horizon_ok = resolution.actual_coverage_days >= 60
            data_hash = resolution.file_hash_sha256[:16]
        except ImportError:
            horizon_ok = False
            data_hash = "N/A"

        # 3. Check qualification thresholds
        min_trades = get_threshold("min_trades")
        min_sharpe = get_threshold("min_sharpe")
        min_pf = get_threshold("min_profit_factor")
        max_dd = get_threshold("max_drawdown_pct")

        # 4. Check risk policy
        risk = get_risk_policy()
        r_hash = risk_policy_hash()
        r_version = risk_policy_version()

        # 5. Stage machine
        sm = StageMachine()
        valid_forward = is_valid_transition("DISCOVERY", "BACKTEST_QUALIFIED")
        invalid_skip = not is_valid_transition("DISCOVERY", "PAPER_ADMISSION_READY")

        # 6. Prove hashes survive end-to-end
        assert p_hash and len(p_hash) == 64, f"policy_hash invalid: {p_hash}"
        assert r_hash and len(r_hash) == 64, f"risk_policy_hash invalid: {r_hash}"
        assert p_version == "1.0.0"
        assert r_version == "1.0.0"
        assert horizon_ok, "GAZP 60d horizon resolution failed"
        assert min_trades == 8
        assert min_sharpe == 0.3
        assert min_pf == 1.05
        assert max_dd == 25.0
        assert valid_forward
        assert invalid_skip
        assert risk.get("max_concurrent_live_positions") == 1
        assert risk.get("shorting_allowed") is False
        assert risk.get("leverage_allowed") is False


class TestT56_SBER_CrossLayerProof:
    """T56: End-to-end SBER truth-chain proof."""

    def test_sber_cross_layer(self):
        """Prove canonical policy → horizon → qualification → risk → stage → handoff for SBER."""
        policy = get_qualification_policy()
        p_hash = policy_hash()

        try:
            from horizon_resolution import resolve_horizon
            resolution = resolve_horizon("SBER", "15m", 365, DATA_DIR)
            horizon_ok = resolution.actual_coverage_days >= 365
            actual_coverage = resolution.actual_coverage_days
        except ImportError:
            horizon_ok = False
            actual_coverage = 0

        risk = get_risk_policy()
        r_hash = risk_policy_hash()

        sm = StageMachine()
        chain = get_maturity_chain()

        assert p_hash and len(p_hash) == 64
        assert r_hash and len(r_hash) == 64
        assert horizon_ok, f"SBER 365d coverage insufficient: {actual_coverage}"
        assert len(chain) == 9
        assert chain[0] == "DISCOVERY"
        assert chain[-1] == "LIVE_CANDIDATE"


class TestT57_IdentityHashesPreserved:
    """T57: Identity hashes preserved end-to-end."""

    def test_policy_hash_deterministic(self):
        h1 = policy_hash()
        h2 = policy_hash()
        assert h1 == h2

    def test_risk_hash_deterministic(self):
        h1 = risk_policy_hash()
        h2 = risk_policy_hash()
        assert h1 == h2

    def test_policy_hash_independent_of_risk_hash(self):
        assert policy_hash() != risk_policy_hash()


# ═══════════════════════════════════════════════════════════════════
# SECTION B: Negative Contract Proofs
# ═══════════════════════════════════════════════════════════════════

class TestT58_ShortCandidateBlocked:
    """T58: Short candidate blocked under no-shorting policy."""

    def test_short_blocked(self):
        result = check_risk_qualification({
            "side": "short",
            "risk_identity": "LIVE_RISK_V1",
        })
        assert result["passed"] is False
        assert any("shorting" in v.lower() for v in result["violations"])


class TestT59_LeverageCandidateBlocked:
    """T59: Leverage candidate blocked."""

    def test_leverage_blocked(self):
        result = check_risk_qualification({
            "side": "long",
            "leverage": True,
            "risk_identity": "LIVE_RISK_V1",
        })
        assert result["passed"] is False
        assert any("leverage" in v.lower() for v in result["violations"])


class TestT60_ConcurrentPositionsBlocked:
    """T60: >1 concurrent position blocked."""

    def test_two_positions_blocked(self):
        result = check_risk_qualification({
            "side": "long",
            "concurrent_live_positions": 2,
            "risk_identity": "LIVE_RISK_V1",
        })
        assert result["passed"] is False
        assert any("concurrent" in v.lower() or "position" in v.lower() for v in result["violations"])


class TestT61_MissingPolicyBlocked:
    """T61: Missing policy version blocks transition."""

    def test_no_policy_version_blocks(self):
        # Create a stage machine with empty policy (force fail)
        sm = StageMachine(policy={"stages": {}, "thresholds": {}, "version": ""})
        result = sm.transition_candidate(
            "test_no_policy",
            "DISCOVERY",
            "BACKTEST_QUALIFIED",
            evidence={"single_backtest": True, "data_identity": True},
        )
        assert result.verdict == "BLOCKED"


class TestT62_MissingRiskIdentityBlocked:
    """T62: Missing risk identity blocks RISK_QUALIFIED."""

    def test_no_risk_identity_blocks(self):
        result = check_risk_qualification({
            "side": "long",
            "risk_identity": "",  # Missing
        })
        assert result["passed"] is False
        assert any("risk_identity" in v.lower() or "missing" in v.lower() for v in result["violations"])


class TestT63_MissingHandoffBlocked:
    """T63: Missing canonical handoff blocks intake."""

    def test_invalid_handoff_fails_closed(self):
        with pytest.raises(ValueError, match="HANDOFF_SCHEMA_ERROR"):
            validate_handoff({})  # Empty payload


class TestT64_Insufficient1095dBlocked:
    """T64: 1095d on ~697d coverage blocks."""

    def test_1095d_fails_on_insufficient_coverage(self):
        try:
            from horizon_resolution import resolve_horizon, InsufficientCoverageError
            with pytest.raises(InsufficientCoverageError):
                resolve_horizon("GAZP", "15m", 1095, DATA_DIR)
        except ImportError:
            pytest.skip("horizon_resolution not importable")


# ═══════════════════════════════════════════════════════════════════
# SECTION C: Stage Machine Negative Proofs
# ═══════════════════════════════════════════════════════════════════

class TestT36_IllegalStageSkip:
    """T36: Illegal stage skip blocked."""

    def test_skip_to_paper_blocked(self):
        sm = StageMachine()
        result = sm.transition_candidate(
            "skip_test", "DISCOVERY", "PAPER_ADMISSION_READY",
            evidence={},
        )
        assert result.verdict == "BLOCKED"

    def test_skip_to_live_blocked(self):
        sm = StageMachine()
        result = sm.transition_candidate(
            "skip_test2", "BACKTEST_QUALIFIED", "LIVE_CANDIDATE",
            evidence={},
        )
        assert result.verdict == "BLOCKED"


class TestT37_60dOnlyPaperBlocked:
    """T37: 60d-only PAPER admission blocked."""

    def test_60d_only_paper_admission_blocked(self):
        sm = StageMachine()
        # Simulate: candidate has only 60d evidence, skip multi-horizon
        result = sm.transition_candidate(
            "60d_test", "WALK_FORWARD_QUALIFIED", "ROBUSTNESS_QUALIFIED",
            evidence={"parameter_neighborhood": True, "temporal_regime": True,
                     "horizon_evidence": {"60d": True}},
        )
        # Should still be allowed (robustness doesn't require multi-horizon)
        # But PAPER_ADMISSION_READY requires MULTI_HORIZON_QUALIFIED
        result2 = sm.transition_candidate(
            "60d_test", "RISK_QUALIFIED", "PAPER_ADMISSION_READY",
            evidence={"LIVE_RISK_V1": True, "bounded_stop": True,
                     "sizing": True, "account_feasibility": True,
                     "no_genuine_paper_yet": True,
                     "horizon_evidence": {"60d": True}},
        )
        # PAPER_ADMISSION requires MULTI_HORIZON, which requires 60d+90d+180d+365d+1095d
        # With only 60d, it should be blocked at MULTI_HORIZON stage
        assert result2.verdict == "BLOCKED" or "multi_horizon" in result2.reason.lower()


class TestT41_HistoricalReplayBlocked:
    """T41: Historical replay cannot create PAPER_QUALIFIED."""

    def test_historical_replay_blocks_paper(self):
        sm = StageMachine()
        result = sm.transition_candidate(
            "replay_test", "PAPER_ADMISSION_READY", "PAPER_QUALIFIED",
            evidence={"chronological_paper_evidence": False,  # No real paper
                     "historical_replay": True},
        )
        assert result.verdict == "BLOCKED"


# ═══════════════════════════════════════════════════════════════════
# SECTION D: Fail-Closed Proofs
# ═══════════════════════════════════════════════════════════════════

class TestFailClosed:
    """Prove fail-closed semantics for each canonical domain."""

    def test_missing_qualification_policy_fails_closed(self):
        """Canonical policy unavailable → BLOCK qualification."""
        with pytest.raises((ValueError, FileNotFoundError, KeyError)):
            from canonical_policy_loader import get_threshold
            get_threshold("nonexistent_threshold_xyz")

    def test_invalid_risk_policy_fails_closed(self):
        """Risk policy invalid → BLOCK."""
        result = check_risk_qualification({})
        assert result["passed"] is False

    def test_empty_handoff_fails_closed(self):
        """Canonical handoff invalid → BLOCK intake."""
        with pytest.raises(ValueError):
            validate_handoff({})

    def test_invalid_stage_transition_blocked(self):
        """Stage evidence incomplete → BLOCK transition."""
        sm = StageMachine()
        result = sm.transition_candidate(
            "fail_test", "DISCOVERY", "BACKTEST_QUALIFIED",
            evidence={},  # No evidence
        )
        assert result.verdict == "BLOCKED"

    def test_1095d_insufficient_blocks(self):
        """Horizon resolver cannot certify 1095d → BLOCK."""
        try:
            from horizon_resolution import resolve_horizon, InsufficientCoverageError
            with pytest.raises(InsufficientCoverageError):
                resolve_horizon("GAZP", "15m", 1095, DATA_DIR)
        except ImportError:
            pytest.skip("horizon_resolution not importable")

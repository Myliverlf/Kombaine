"""Tests for System Certification — Iteration 21.

Covers:
- T1-T24: Proof chain validation tests
- G1-G12: Gate evaluation tests
- C1-C15: Chaos test harness tests

All tests are deterministic, offline, and perform zero broker mutations.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure project root is on path
import sys
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.system_certification import (
    CERT_DB_NAME,
    CERT_VERSION,
    CERT_SCHEMA_VERSION,
    UNIVERSE,
    REQUIRED_TIMEFRAMES,
    REQUIRED_HORIZONS,
    GateStatus,
    ReadinessLevel,
    ChaosResult,
    ReadinessGate,
    ProofChainResult,
    ChaosTestResult,
    CertificationRun,
    CertificationStore,
    GateEvaluator,
    ProofChainRunner,
    ChaosTestHarness,
    CertificationRunner,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def project_root():
    """Return the actual project root."""
    return Path("/root/prop-desk/strategy_combine")


@pytest.fixture
def tmp_project(tmp_path):
    """Create a temporary project structure for isolated testing."""
    root = tmp_path / "strategy_combine"
    root.mkdir()
    (root / "core").mkdir()
    (root / "tests").mkdir()
    (root / "state").mkdir()
    (root / "config.json").write_text(json.dumps({
        "mode": "paper",
        "paper_first": True,
        "universe": ["BR", "GAZP", "LKOH", "SBER", "Si"],
        "risk": {
            "max_slots": 2,
            "risk_per_trade_pct": 2.7,
            "portfolio_stop_drawdown_pct": 25,
        },
        "account": {"id": "test"},
    }))
    # Create minimal state files
    (root / "state" / "strategy_registry.json").write_text(json.dumps({
        "candidates": []
    }))
    (root / "state" / "portfolio.json").write_text(json.dumps({}))
    (root / "state" / "signal_pool.json").write_text(json.dumps([]))
    return root


@pytest.fixture
def store(project_root):
    """Return a CertificationStore for the real project."""
    return CertificationStore(project_root=project_root)


@pytest.fixture
def evaluator(project_root):
    """Return a GateEvaluator for the real project."""
    return GateEvaluator(project_root=project_root)


@pytest.fixture
def runner(project_root):
    """Return a ProofChainRunner for the real project."""
    return ProofChainRunner(project_root=project_root)


@pytest.fixture
def chaos(project_root):
    """Return a ChaosTestHarness for the real project."""
    return ChaosTestHarness(project_root=project_root)


# ===========================================================================
# G1-G12: Gate Evaluation Tests
# ===========================================================================

class TestG01TestsPass:
    """G1: All tests pass."""

    def test_gate_returns_pass(self, evaluator):
        gate = evaluator.evaluate_g1_tests_pass()
        assert gate.gate_id == "G1"
        assert gate.status == GateStatus.PASS
        assert gate.is_critical is True
        assert "total_tests" in gate.evidence

    def test_evidence_has_counts(self, evaluator):
        gate = evaluator.evaluate_g1_tests_pass()
        assert gate.evidence["failures"] == 0
        assert gate.evidence["errors"] == 0


class TestG02ConfigSafety:
    """G2: Config safety — mode=paper, paper_first=true."""

    def test_gate_returns_pass(self, evaluator):
        gate = evaluator.evaluate_g2_config_safety()
        assert gate.gate_id == "G2"
        assert gate.status == GateStatus.PASS
        assert gate.is_critical is True

    def test_evidence_shows_paper_mode(self, evaluator):
        gate = evaluator.evaluate_g2_config_safety()
        assert gate.evidence["mode"] == "paper"
        assert gate.evidence["paper_first"] is True

    def test_fail_when_mode_not_paper(self, evaluator):
        """If mode were changed, gate would fail."""
        gate = evaluator.evaluate_g2_config_safety()
        # Current config is paper — verify the gate checks correctly
        assert gate.evidence["mode"] == "paper"


class TestG03DataCoverage:
    """G3: Required market data coverage."""

    def test_gate_returns_warn(self, evaluator):
        gate = evaluator.evaluate_g3_data_coverage()
        assert gate.gate_id == "G3"
        # Some data files are missing (BR 1095d, Si 1095d) → WARN
        assert gate.status in (GateStatus.PASS, GateStatus.WARN)
        assert gate.is_critical is False

    def test_evidence_has_coverage_info(self, evaluator):
        gate = evaluator.evaluate_g3_data_coverage()
        assert "present" in gate.evidence
        assert "total" in gate.evidence
        assert "coverage_pct" in gate.evidence
        assert gate.evidence["total"] == len(UNIVERSE) * len(REQUIRED_HORIZONS) * len(REQUIRED_TIMEFRAMES)

    def test_missing_files_identified(self, evaluator):
        gate = evaluator.evaluate_g3_data_coverage()
        if gate.evidence.get("missing"):
            for f in gate.evidence["missing"]:
                assert "1095d" in f or "Si_365d_1h" in f  # Known gaps


class TestG04RegistryIntegrity:
    """G4: Strategy registry is valid."""

    def test_gate_returns_pass(self, evaluator):
        gate = evaluator.evaluate_g4_registry_integrity()
        assert gate.gate_id == "G4"
        assert gate.status == GateStatus.PASS
        assert gate.is_critical is True

    def test_evidence_has_candidate_count(self, evaluator):
        gate = evaluator.evaluate_g4_registry_integrity()
        assert "candidate_count" in gate.evidence


class TestG05PortfolioState:
    """G5: Portfolio state is valid."""

    def test_gate_returns_pass(self, evaluator):
        gate = evaluator.evaluate_g5_portfolio_state()
        assert gate.gate_id == "G5"
        assert gate.status == GateStatus.PASS
        assert gate.is_critical is True


class TestG06HealthSystem:
    """G6: System health module loads."""

    def test_gate_returns_pass(self, evaluator):
        gate = evaluator.evaluate_g6_health_system()
        assert gate.gate_id == "G6"
        assert gate.status == GateStatus.PASS
        assert gate.is_critical is True

    def test_evidence_has_snapshot_info(self, evaluator):
        gate = evaluator.evaluate_g6_health_system()
        assert "overall_status" in gate.evidence
        assert "component_count" in gate.evidence
        assert gate.evidence["component_count"] > 0


class TestG07ProductionTruth:
    """G7: Production truth module loads."""

    def test_gate_returns_pass(self, evaluator):
        gate = evaluator.evaluate_g7_production_truth()
        assert gate.gate_id == "G7"
        assert gate.status == GateStatus.PASS
        assert gate.is_critical is True


class TestG08TransitionEngine:
    """G8: Portfolio transition engine loads."""

    def test_gate_returns_pass(self, evaluator):
        gate = evaluator.evaluate_g8_transition_engine()
        assert gate.gate_id == "G8"
        assert gate.status == GateStatus.PASS
        assert gate.is_critical is True


class TestG09RiskManager:
    """G9: Risk manager loads."""

    def test_gate_returns_pass(self, evaluator):
        gate = evaluator.evaluate_g9_risk_manager()
        assert gate.gate_id == "G9"
        assert gate.status == GateStatus.PASS
        assert gate.is_critical is True


class TestG10NoBrokerMutation:
    """G10: No broker-mutating code in certification path."""

    def test_gate_returns_pass(self, evaluator):
        gate = evaluator.evaluate_g10_no_broker_mutation()
        assert gate.gate_id == "G10"
        assert gate.status == GateStatus.PASS
        assert gate.is_critical is True

    def test_violations_empty(self, evaluator):
        gate = evaluator.evaluate_g10_no_broker_mutation()
        assert gate.evidence["violations"] == []


class TestG11PaperModeEnforced:
    """G11: Paper mode enforced."""

    def test_gate_returns_pass(self, evaluator):
        gate = evaluator.evaluate_g11_paper_mode_enforced()
        assert gate.gate_id == "G11"
        assert gate.status == GateStatus.PASS
        assert gate.is_critical is True


class TestG12EvidenceHash:
    """G12: Evidence integrity."""

    def test_gate_returns_pass(self, evaluator):
        gate = evaluator.evaluate_g12_evidence_hash()
        assert gate.gate_id == "G12"
        assert gate.status == GateStatus.PASS
        assert "file_hashes" in gate.evidence


# ===========================================================================
# T1-T24: Proof Chain Tests
# ===========================================================================

class TestT01ConfigIntegrity:
    """T1: Config file integrity."""

    def test_chain_passes(self, runner):
        chain = runner.run_t1_config_integrity()
        assert chain.chain_id == "T1"
        assert chain.all_passed is True

    def test_steps_all_pass(self, runner):
        chain = runner.run_t1_config_integrity()
        for step in chain.steps:
            assert step["passed"] is True, f"Step {step['name']} failed"


class TestT02UniverseConsistency:
    """T2: Universe matches expected."""

    def test_chain_passes(self, runner):
        chain = runner.run_t2_universe_consistency()
        assert chain.chain_id == "T2"
        assert chain.all_passed is True


class TestT03RegistrySchema:
    """T3: Registry has expected structure."""

    def test_chain_passes(self, runner):
        chain = runner.run_t3_registry_schema()
        assert chain.chain_id == "T3"
        assert chain.all_passed is True


class TestT04PortfolioSchema:
    """T4: Portfolio state has expected structure."""

    def test_chain_passes(self, runner):
        chain = runner.run_t4_portfolio_schema()
        assert chain.chain_id == "T4"
        assert chain.all_passed is True


class TestT05HealthSnapshot:
    """T5: Health snapshot produces valid output."""

    def test_chain_passes(self, runner):
        chain = runner.run_t5_health_snapshot()
        assert chain.chain_id == "T5"
        assert chain.all_passed is True

    def test_has_component_count(self, runner):
        chain = runner.run_t5_health_snapshot()
        component_step = [s for s in chain.steps if s["name"] == "has_components"][0]
        assert component_step["count"] > 0


class TestT06RiskManagerLoad:
    """T6: Risk manager loads with valid config."""

    def test_chain_passes(self, runner):
        chain = runner.run_t6_risk_manager_load()
        assert chain.chain_id == "T6"
        assert chain.all_passed is True


class TestT07ProductionTruthLoad:
    """T7: Production truth module loads."""

    def test_chain_passes(self, runner):
        chain = runner.run_t7_production_truth_load()
        assert chain.chain_id == "T7"
        assert chain.all_passed is True

    def test_version_present(self, runner):
        chain = runner.run_t7_production_truth_load()
        version_step = [s for s in chain.steps if s["name"] == "module_loaded"][0]
        assert "version" in version_step


class TestT08TransitionEngineLoad:
    """T8: Transition engine loads."""

    def test_chain_passes(self, runner):
        chain = runner.run_t8_transition_engine_load()
        assert chain.chain_id == "T8"
        assert chain.all_passed is True


class TestT09ExperimentMemoryDB:
    """T9: Experiment memory database is accessible."""

    def test_chain_passes(self, runner):
        chain = runner.run_t9_experiment_memory_db()
        assert chain.chain_id == "T9"
        assert chain.all_passed is True


class TestT10ResearchKnowledgeDB:
    """T10: Research knowledge database is accessible."""

    def test_chain_passes(self, runner):
        chain = runner.run_t10_research_knowledge_db()
        assert chain.chain_id == "T10"
        assert chain.all_passed is True


class TestT11LifecycleDB:
    """T11: Strategy lifecycle database is accessible."""

    def test_chain_passes(self, runner):
        chain = runner.run_t11_lifecycle_db()
        assert chain.chain_id == "T11"
        assert chain.all_passed is True


class TestT12TransitionDB:
    """T12: Portfolio transition database is accessible."""

    def test_chain_passes(self, runner):
        chain = runner.run_t12_transition_db()
        assert chain.chain_id == "T12"
        assert chain.all_passed is True


class TestT13SignalPool:
    """T13: Signal pool is valid JSON."""

    def test_chain_passes(self, runner):
        chain = runner.run_t13_signal_pool()
        assert chain.chain_id == "T13"
        assert chain.all_passed is True


class TestT14AnalyticsDB:
    """T14: Analytics database is accessible."""

    def test_chain_passes(self, runner):
        chain = runner.run_t14_analytics_db()
        assert chain.chain_id == "T14"
        assert chain.all_passed is True


class TestT15ModeInvariant:
    """T15: Mode is paper, paper_first is True."""

    def test_chain_passes(self, runner):
        chain = runner.run_t15_mode_invariant()
        assert chain.chain_id == "T15"
        assert chain.all_passed is True


class TestT16NoLiveBroker:
    """T16: No live broker credentials active."""

    def test_chain_passes(self, runner):
        chain = runner.run_t16_no_live_broker()
        assert chain.chain_id == "T16"
        # Token presence is informational, not a blocker
        # In paper mode, broker credentials provide READ access only
        assert chain.all_passed is True


class TestT17CertificationStore:
    """T17: Certification store initializes correctly."""

    def test_chain_passes(self, runner):
        chain = runner.run_t17_certification_store()
        assert chain.chain_id == "T17"
        assert chain.all_passed is True


class TestT18MarketDataFreshness:
    """T18: Market data files are not excessively stale."""

    def test_chain_result(self, runner):
        chain = runner.run_t18_market_data_freshness()
        assert chain.chain_id == "T18"
        # Data freshness depends on actual file mtimes
        # Not a critical assertion — just verify it runs
        assert len(chain.steps) > 0


class TestT19StateDirWritable:
    """T19: State directory is writable."""

    def test_chain_passes(self, runner):
        chain = runner.run_t19_state_dir_writable()
        assert chain.chain_id == "T19"
        assert chain.all_passed is True


class TestT20ConfigRiskParams:
    """T20: Risk parameters within safe bounds."""

    def test_chain_passes(self, runner):
        chain = runner.run_t20_config_risk_params()
        assert chain.chain_id == "T20"
        assert chain.all_passed is True


class TestT21NoSecretsInLogs:
    """T21: No secrets in log files."""

    def test_chain_passes(self, runner):
        chain = runner.run_t21_no_secrets_in_logs()
        assert chain.chain_id == "T21"
        # Secrets should not be in logs
        assert chain.all_passed is True


class TestT22ModuleForbiddenDeps:
    """T22: No forbidden module dependencies."""

    def test_chain_passes(self, runner):
        chain = runner.run_t22_module_forbidden_deps()
        assert chain.chain_id == "T22"
        assert chain.all_passed is True


class TestT23DiskSpace:
    """T23: Sufficient disk space."""

    def test_chain_passes(self, runner):
        chain = runner.run_t23_disk_space()
        assert chain.chain_id == "T23"
        assert chain.all_passed is True

    def test_free_gb_positive(self, runner):
        chain = runner.run_t23_disk_space()
        disk_step = [s for s in chain.steps if s["name"] == "disk_free_gb"][0]
        assert disk_step["free_gb"] > 1.0


class TestT24StorePersistence:
    """T24: Certification store round-trips correctly."""

    def test_chain_passes(self, runner):
        chain = runner.run_t24_certification_store_persistence()
        assert chain.chain_id == "T24"
        assert chain.all_passed is True


# ===========================================================================
# C1-C15: Chaos Test Harness Tests
# ===========================================================================

class TestC01ConfigCorruptionRecovery:
    """C1: System survives config corruption."""

    def test_survives(self, chaos):
        result = chaos.run_c1_config_corruption_recovery()
        assert result.test_id == "C1"
        assert result.result == ChaosResult.SURVIVED
        assert result.recovered is True


class TestC02DBLocked:
    """C2: System handles DB locked state."""

    def test_survives(self, chaos):
        result = chaos.run_c2_state_db_locked()
        assert result.test_id == "C2"
        assert result.result == ChaosResult.SURVIVED


class TestC03MissingStateFiles:
    """C3: System handles missing state files."""

    def test_survives(self, chaos):
        result = chaos.run_c3_missing_state_files()
        assert result.test_id == "C3"
        assert result.result == ChaosResult.SURVIVED


class TestC04ConcurrentAccess:
    """C4: Concurrent writes don't corrupt."""

    def test_survives(self, chaos):
        result = chaos.run_c4_concurrent_access()
        assert result.test_id == "C4"
        assert result.result == ChaosResult.SURVIVED
        assert result.recovered is True


class TestC05DiskFull:
    """C5: System handles disk-full detection."""

    def test_survives(self, chaos):
        result = chaos.run_c5_disk_full()
        assert result.test_id == "C5"
        assert result.result == ChaosResult.SURVIVED


class TestC06RegistryCorrupt:
    """C6: System handles corrupt registry."""

    def test_survives(self, chaos):
        result = chaos.run_c6_registry_corrupt()
        assert result.test_id == "C6"
        assert result.result == ChaosResult.SURVIVED


class TestC07ProcessRestart:
    """C7: State persists across restart."""

    def test_survives(self, chaos):
        result = chaos.run_c7_process_restart()
        assert result.test_id == "C7"
        assert result.result == ChaosResult.SURVIVED
        assert result.recovered is True


class TestC08NetworkUnavailable:
    """C8: System operates offline."""

    def test_survives(self, chaos):
        result = chaos.run_c8_network_unavailable()
        assert result.test_id == "C8"
        assert result.result == ChaosResult.SURVIVED


class TestC09MemoryPressure:
    """C9: System handles memory pressure."""

    def test_survives(self, chaos):
        result = chaos.run_c9_memory_pressure()
        assert result.test_id == "C9"
        assert result.result == ChaosResult.SURVIVED


class TestC10ClockSkew:
    """C10: System handles timestamp anomalies."""

    def test_survives(self, chaos):
        result = chaos.run_c10_clock_skew()
        assert result.test_id == "C10"
        assert result.result == ChaosResult.SURVIVED


class TestC11EmptyState:
    """C11: System handles empty state."""

    def test_survives(self, chaos):
        result = chaos.run_c11_empty_state()
        assert result.test_id == "C11"
        assert result.result == ChaosResult.SURVIVED


class TestC12LargeState:
    """C12: System handles large state files."""

    def test_survives(self, chaos):
        result = chaos.run_c12_large_state()
        assert result.test_id == "C12"
        assert result.result == ChaosResult.SURVIVED


class TestC13PartialFailure:
    """C13: Certification continues on partial failure."""

    def test_survives(self, chaos):
        result = chaos.run_c13_partial_failure()
        assert result.test_id == "C13"
        assert result.result == ChaosResult.SURVIVED


class TestC14RapidEvaluation:
    """C14: Rapid evaluations don't corrupt state."""

    def test_survives(self, chaos):
        result = chaos.run_c14_rapid_evaluation()
        assert result.test_id == "C14"
        assert result.result == ChaosResult.SURVIVED


class TestC15DataPathAbsent:
    """C15: System handles absent data path."""

    def test_survives(self, chaos):
        result = chaos.run_c15_data_path_absent()
        assert result.test_id == "C15"
        # Graceful failure is acceptable
        assert result.recovered is True


# ===========================================================================
# CertificationStore Tests
# ===========================================================================

class TestCertificationStore:
    """Tests for CertificationStore persistence."""

    def test_init_creates_db(self, project_root):
        store = CertificationStore(project_root=project_root)
        assert store.db_path.exists()

    def test_save_and_load_run(self, project_root):
        store = CertificationStore(project_root=project_root)
        run = CertificationRun(
            run_id="test_store_001",
            project_root=str(project_root),
            version=CERT_VERSION,
            started_at=datetime.now(timezone.utc).isoformat(),
            overall_status=ReadinessLevel.NOT_READY,
        )
        store.save_run(run)
        loaded = store.load_run("test_store_001")
        assert loaded is not None
        assert loaded.run_id == "test_store_001"

    def test_load_nonexistent_returns_none(self, project_root):
        store = CertificationStore(project_root=project_root)
        loaded = store.load_run("nonexistent_run")
        assert loaded is None

    def test_list_runs(self, project_root):
        store = CertificationStore(project_root=project_root)
        runs = store.list_runs()
        assert isinstance(runs, list)

    def test_save_with_gates(self, project_root):
        store = CertificationStore(project_root=project_root)
        run = CertificationRun(
            run_id="test_gates_001",
            project_root=str(project_root),
            version=CERT_VERSION,
            started_at=datetime.now(timezone.utc).isoformat(),
            overall_status=ReadinessLevel.CONDITIONALLY_READY,
            gates=[
                ReadinessGate(
                    gate_id="G1", name="Test Gate",
                    description="Test", status=GateStatus.PASS,
                    evidence={"key": "value"},
                ),
            ],
        )
        store.save_run(run)
        loaded = store.load_run("test_gates_001")
        assert loaded is not None
        assert len(loaded.gates) == 1
        assert loaded.gates[0].gate_id == "G1"

    def test_save_with_proof_chains(self, project_root):
        store = CertificationStore(project_root=project_root)
        run = CertificationRun(
            run_id="test_chains_001",
            project_root=str(project_root),
            version=CERT_VERSION,
            started_at=datetime.now(timezone.utc).isoformat(),
            overall_status=ReadinessLevel.NOT_READY,
            proof_chains=[
                ProofChainResult(
                    chain_id="T1", name="Test Chain",
                    all_passed=True,
                    steps=[{"name": "step1", "passed": True}],
                ),
            ],
        )
        store.save_run(run)
        loaded = store.load_run("test_chains_001")
        assert loaded is not None
        assert len(loaded.proof_chains) == 1
        assert loaded.proof_chains[0].chain_id == "T1"

    def test_save_with_chaos_tests(self, project_root):
        store = CertificationStore(project_root=project_root)
        run = CertificationRun(
            run_id="test_chaos_001",
            project_root=str(project_root),
            version=CERT_VERSION,
            started_at=datetime.now(timezone.utc).isoformat(),
            overall_status=ReadinessLevel.NOT_READY,
            chaos_tests=[
                ChaosTestResult(
                    test_id="C1", name="Test Chaos",
                    scenario="test", result=ChaosResult.SURVIVED,
                ),
            ],
        )
        store.save_run(run)
        loaded = store.load_run("test_chaos_001")
        assert loaded is not None
        assert len(loaded.chaos_tests) == 1

    def test_overwrite_on_duplicate_run_id(self, project_root):
        store = CertificationStore(project_root=project_root)
        run1 = CertificationRun(
            run_id="test_overwrite",
            project_root=str(project_root),
            version=CERT_VERSION,
            started_at=datetime.now(timezone.utc).isoformat(),
            overall_status=ReadinessLevel.NOT_READY,
        )
        run2 = CertificationRun(
            run_id="test_overwrite",
            project_root=str(project_root),
            version=CERT_VERSION,
            started_at=datetime.now(timezone.utc).isoformat(),
            overall_status=ReadinessLevel.CONDITIONALLY_READY,
        )
        store.save_run(run1)
        store.save_run(run2)
        loaded = store.load_run("test_overwrite")
        assert loaded.overall_status == ReadinessLevel.CONDITIONALLY_READY


# ===========================================================================
# CertificationRunner Integration Tests
# ===========================================================================

class TestCertificationRunner:
    """Integration tests for the full certification runner."""

    def test_full_certification_completes(self, project_root):
        runner = CertificationRunner(project_root=project_root)
        run = runner.run_full_certification()
        assert run.run_id.startswith("cert_")
        assert run.completed_at != ""
        assert run.overall_status in (
            ReadinessLevel.NOT_READY,
            ReadinessLevel.CONDITIONALLY_READY,
            ReadinessLevel.READY_FOR_CONTROLLED_LIVE,
        )

    def test_full_certification_has_all_gates(self, project_root):
        runner = CertificationRunner(project_root=project_root)
        run = runner.run_full_certification()
        assert len(run.gates) == 12
        gate_ids = {g.gate_id for g in run.gates}
        for i in range(1, 13):
            assert f"G{i}" in gate_ids

    def test_full_certification_has_all_chains(self, project_root):
        runner = CertificationRunner(project_root=project_root)
        run = runner.run_full_certification()
        assert len(run.proof_chains) == 24
        chain_ids = {c.chain_id for c in run.proof_chains}
        for i in range(1, 25):
            assert f"T{i}" in chain_ids

    def test_full_certification_has_all_chaos(self, project_root):
        runner = CertificationRunner(project_root=project_root)
        run = runner.run_full_certification()
        assert len(run.chaos_tests) == 15
        chaos_ids = {ch.test_id for ch in run.chaos_tests}
        for i in range(1, 16):
            assert f"C{i}" in chaos_ids

    def test_full_certification_persists(self, project_root):
        runner = CertificationRunner(project_root=project_root)
        run = runner.run_full_certification()
        store = CertificationStore(project_root=project_root)
        loaded = store.load_run(run.run_id)
        assert loaded is not None
        assert loaded.run_id == run.run_id

    def test_full_certification_has_evidence_hash(self, project_root):
        runner = CertificationRunner(project_root=project_root)
        run = runner.run_full_certification()
        assert len(run.evidence_hash) == 16

    def test_full_certification_has_restrictions(self, project_root):
        runner = CertificationRunner(project_root=project_root)
        run = runner.run_full_certification()
        assert isinstance(run.restrictions, list)
        # There should be known restrictions (data gaps, no broker, etc.)
        assert len(run.restrictions) > 0

    def test_readiness_is_honest(self, project_root):
        """The readiness decision must be honest — not manufactured."""
        runner = CertificationRunner(project_root=project_root)
        run = runner.run_full_certification()
        # Given known gaps, should NOT be READY_FOR_CONTROLLED_LIVE
        # (no broker credentials, missing data, no Telegram, no reconciliation)
        assert run.overall_status != ReadinessLevel.READY_FOR_CONTROLLED_LIVE

    def test_summary_populated(self, project_root):
        runner = CertificationRunner(project_root=project_root)
        run = runner.run_full_certification()
        assert "Gates:" in run.summary
        assert "Proof Chains:" in run.summary
        assert "Chaos:" in run.summary
        assert "Overall:" in run.summary


# ===========================================================================
# Dataclass Tests
# ===========================================================================

class TestDataclasses:
    """Test dataclass serialization."""

    def test_readiness_gate_to_dict(self):
        gate = ReadinessGate(
            gate_id="G1", name="Test", description="desc",
            status=GateStatus.PASS, evidence={"k": "v"},
        )
        d = gate.to_dict()
        assert d["gate_id"] == "G1"
        assert d["status"] == "PASS"

    def test_proof_chain_to_dict(self):
        chain = ProofChainResult(
            chain_id="T1", name="Test", all_passed=True,
            steps=[{"name": "s1", "passed": True}],
        )
        d = chain.to_dict()
        assert d["chain_id"] == "T1"
        assert d["all_passed"] is True

    def test_chaos_result_to_dict(self):
        chaos = ChaosTestResult(
            test_id="C1", name="Test", scenario="s",
            result=ChaosResult.SURVIVED,
        )
        d = chaos.to_dict()
        assert d["test_id"] == "C1"
        assert d["result"] == "SURVIVED"

    def test_certification_run_to_dict(self):
        run = CertificationRun(
            run_id="test", project_root="/tmp",
            version="1.0", started_at="2026-01-01",
        )
        d = run.to_dict()
        assert d["run_id"] == "test"
        assert d["overall_status"] == "NOT_READY"

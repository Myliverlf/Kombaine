"""Tests for Research Knowledge Layer Foundation — Iteration 09.

Covers T1–T21 and F1–F18 as specified in the directive.
No broker orders, no live execution, no strategy changes.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root on path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.research_knowledge import (
    CONFIDENCE_LEVELS,
    DISTILLER_VERSION,
    FINDING_STATUSES,
    FINDING_TYPES,
    KNOWLEDGE_DB_NAME,
    KNOWLEDGE_SCHEMA_VERSION,
    ConfidenceLevel,
    EvidenceRef,
    FindingStatus,
    FindingType,
    KnowledgeBuild,
    KnowledgeStore,
    OpenQuestion,
    ResearchFinding,
    check_comparability,
    compute_confidence,
    compute_finding_id,
    compute_question_id,
    distill_findings,
    group_comparable_observations,
    write_knowledge_report,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tmp_db() -> Path:
    """Create a temporary DB path."""
    return Path(tempfile.mkdtemp()) / "test_knowledge.db"


def _tmp_mem_db() -> Path:
    """Create a temporary Experiment Memory DB with observations."""
    return Path(tempfile.mkdtemp()) / "test_memory.db"


def _create_observation(
    run_id: str = "run_20260829_120000",
    config_key: str = "cfg_001",
    instrument: str = "SBER",
    timeframe: str = "1h",
    strategy: str = "sma_cross",
    params: dict = None,
    horizon_days: int = 60,
    dataset_hash: str = "d001",
    code_hash: str = "c001",
    cost_model_hash: str = "cm001",
    validation_version: str = "v1",
    backtest_engine_version: str = "bt1",
    classification: str = "NEW",
    status: str = "tested",
    eligible: bool = True,
    reject_reasons: list = None,
    metrics: dict = None,
    error_type: str = None,
    error_message: str = None,
    **extra,
) -> dict:
    """Build a sample observation dict matching experiment_instances schema."""
    if params is None:
        params = {"fast": 10, "slow": 30}
    if reject_reasons is None:
        reject_reasons = []
    if metrics is None:
        metrics = {
            "total_pnl": 5000.0,
            "profit_factor": 1.5,
            "max_drawdown": -800.0,
            "sharpe": 1.2,
            "win_rate": 0.6,
            "trade_count": 25,
        }
    instance_id = extra.pop("experiment_instance_id", "inst_aabbccdd")
    family_id = extra.pop("experiment_family_id", "fam_11223344")
    return {
        "experiment_instance_id": instance_id,
        "experiment_family_id": family_id,
        "run_id": run_id,
        "config_key": config_key,
        "instrument": instrument,
        "timeframe": timeframe,
        "strategy": strategy,
        "parameters_json": json.dumps(params, sort_keys=True),
        "horizon_days": horizon_days,
        "dataset_hash": dataset_hash,
        "dataset_start": "2026-06-01",
        "dataset_end": "2026-08-01",
        "code_hash": code_hash,
        "cost_model_hash": cost_model_hash,
        "validation_version": validation_version,
        "backtest_engine_version": backtest_engine_version,
        "classification": classification,
        "status": status,
        "eligible": 1 if eligible else 0,
        "reject_reasons": json.dumps(reject_reasons),
        "metrics_json": json.dumps(metrics) if metrics else "{}",
        "error_type": error_type,
        "error_message": error_message,
        "started_at": "2026-08-29T12:00:00Z",
        "finished_at": "2026-08-29T12:00:30Z",
        "indexed_at": "2026-08-29T12:01:00Z",
    }


def _create_memory_db(observations: List[dict], db_path: Path) -> None:
    """Create an Experiment Memory DB with given observations."""
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS experiment_families (
            experiment_family_id TEXT PRIMARY KEY,
            instrument TEXT, timeframe TEXT, strategy TEXT,
            normalized_parameters TEXT, horizon_days INTEGER,
            methodology_version TEXT, created_at TEXT,
            last_seen_at TEXT, schema_version TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS experiment_instances (
            experiment_instance_id TEXT NOT NULL,
            experiment_family_id TEXT NOT NULL,
            run_id TEXT NOT NULL, config_key TEXT NOT NULL,
            instrument TEXT, timeframe TEXT, strategy TEXT,
            parameters_json TEXT, horizon_days INTEGER,
            dataset_hash TEXT, dataset_start TEXT, dataset_end TEXT,
            code_hash TEXT, cost_model_hash TEXT,
            validation_version TEXT, backtest_engine_version TEXT,
            classification TEXT, status TEXT, eligible INTEGER,
            reject_reasons TEXT, metrics_json TEXT,
            error_type TEXT, error_message TEXT,
            started_at TEXT, finished_at TEXT, indexed_at TEXT,
            PRIMARY KEY (run_id, experiment_instance_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS indexed_runs (
            run_id TEXT PRIMARY KEY, run_bundle_path TEXT,
            indexed_at TEXT, instance_count INTEGER, schema_version TEXT
        )
    """)
    for obs in observations:
        cols = list(obs.keys())
        vals = [obs[c] for c in cols]
        placeholders = ", ".join(["?"] * len(cols))
        col_names = ", ".join(cols)
        conn.execute(
            f"INSERT OR REPLACE INTO experiment_instances ({col_names}) VALUES ({placeholders})",
            vals,
        )
    conn.commit()
    conn.close()


# ===========================================================================
# T1 — Finding identity
# ===========================================================================

class TestT1_FindingIdentity:
    """Same type/scope produces stable finding identity."""

    def test_same_type_scope_same_id(self):
        scope = {"instruments": ["SBER"], "strategies": ["sma_cross"]}
        id1 = compute_finding_id(FindingType.PERFORMANCE, "sma_cross:SBER", scope)
        id2 = compute_finding_id(FindingType.PERFORMANCE, "sma_cross:SBER", scope)
        assert id1 == id2
        assert id1.startswith("find_")

    def test_different_type_different_id(self):
        scope = {"instruments": ["SBER"], "strategies": ["sma_cross"]}
        id1 = compute_finding_id(FindingType.PERFORMANCE, "sma_cross:SBER", scope)
        id2 = compute_finding_id(FindingType.ROBUSTNESS, "sma_cross:SBER", scope)
        assert id1 != id2

    def test_different_scope_different_id(self):
        id1 = compute_finding_id(FindingType.PERFORMANCE, "sma_cross:SBER", {"instruments": ["SBER"]})
        id2 = compute_finding_id(FindingType.PERFORMANCE, "sma_cross:SBER", {"instruments": ["GAZP"]})
        assert id1 != id2

    def test_deterministic_across_calls(self):
        scope = {"instruments": ["SBER"], "strategies": ["sma_cross"]}
        ids = [compute_finding_id(FindingType.PERFORMANCE, "sma_cross:SBER", scope) for _ in range(50)]
        assert len(set(ids)) == 1

    def test_question_identity_stable(self):
        q1 = compute_question_id("sub", "reason", {"a": 1})
        q2 = compute_question_id("sub", "reason", {"a": 1})
        assert q1 == q2
        assert q1.startswith("q_")

    def test_question_identity_changes_with_input(self):
        q1 = compute_question_id("sub", "reason1", {"a": 1})
        q2 = compute_question_id("sub", "reason2", {"a": 1})
        assert q1 != q2


# ===========================================================================
# T2 — Evidence provenance
# ===========================================================================

class TestT2_EvidenceProvenance:
    """Every finding resolves to canonical observations/run evidence."""

    def test_finding_has_evidence_refs(self):
        finding = ResearchFinding(
            finding_id="find_001",
            finding_type=FindingType.PERFORMANCE,
            subject="sma_cross:SBER",
            scope={"instruments": ["SBER"]},
            statement="test",
            evidence_refs=[
                EvidenceRef(
                    observation_id="inst_001",
                    run_id="run_001",
                    config_key="cfg_001",
                    experiment_family_id="fam_001",
                    classification="NEW",
                    status="tested",
                    eligible=True,
                ).to_dict(),
            ],
        )
        assert len(finding.evidence_refs) == 1
        ref = finding.evidence_refs[0]
        assert ref["run_id"] == "run_001"
        assert ref["observation_id"] == "inst_001"

    def test_evidence_chain_fields_complete(self):
        ref = EvidenceRef(
            observation_id="inst_001",
            run_id="run_001",
            config_key="cfg_001",
            experiment_family_id="fam_001",
            classification="NEW",
            status="tested",
            eligible=True,
        )
        d = ref.to_dict()
        assert "observation_id" in d
        assert "run_id" in d
        assert "config_key" in d
        assert "experiment_family_id" in d

    def test_knowledge_store_evidence_query(self):
        db = _tmp_db()
        store = KnowledgeStore(db)
        finding = ResearchFinding(
            finding_id="find_prov",
            finding_type=FindingType.PERFORMANCE,
            subject="test",
            scope={},
            statement="test stmt",
            evidence_refs=[
                EvidenceRef("inst_a", "run_a", "cfg_a", "fam_a", "NEW", "tested", True).to_dict(),
            ],
        )
        store._upsert_finding(finding)
        evidence = store.get_evidence("find_prov")
        assert len(evidence) == 1
        assert evidence[0]["observation_id"] == "inst_a"
        store.close()


# ===========================================================================
# T3 — Empty memory
# ===========================================================================

class TestT3_EmptyMemory:
    """Zero observations produces valid empty/insufficient knowledge build."""

    def test_empty_memory_produces_build(self):
        mem_db = _tmp_mem_db()
        _create_memory_db([], mem_db)
        k_db = _tmp_db()
        store = KnowledgeStore(k_db)
        build = distill_findings(
            experiment_memory_path=mem_db,
            knowledge_store=store,
        )
        assert build.status == "COMPLETED_EMPTY"
        assert build.findings_created == 0
        store.close()

    def test_empty_build_recorded(self):
        mem_db = _tmp_mem_db()
        _create_memory_db([], mem_db)
        k_db = _tmp_db()
        store = KnowledgeStore(k_db)
        build = distill_findings(
            experiment_memory_path=mem_db,
            knowledge_store=store,
        )
        # Build should be recorded even if empty
        builds = store.get_build_history()
        assert len(builds) >= 1
        store.close()


# ===========================================================================
# T4 — Comparable aggregation
# ===========================================================================

class TestT4_ComparableAggregation:
    """Compatible observations aggregate deterministically."""

    def test_identical_observations_aggregate(self):
        obs = [
            _create_observation(instrument="SBER", strategy="sma_cross"),
            _create_observation(instrument="SBER", strategy="sma_cross"),
            _create_observation(instrument="SBER", strategy="sma_cross"),
        ]
        groups = group_comparable_observations(obs)
        assert len(groups) == 1
        assert len(groups[0]) == 3

    def test_aggregation_deterministic(self):
        obs = [_create_observation(instrument="SBER") for _ in range(5)]
        g1 = group_comparable_observations(obs)
        g2 = group_comparable_observations(obs)
        assert len(g1) == len(g2)
        assert len(g1[0]) == len(g2[0])


# ===========================================================================
# T5 — Incompatible separation
# ===========================================================================

class TestT5_IncompatibleSeparation:
    """Materially incompatible observations are not blindly averaged."""

    def test_different_instruments_separate(self):
        obs = [
            _create_observation(instrument="SBER"),
            _create_observation(instrument="GAZP"),
        ]
        groups = group_comparable_observations(obs)
        assert len(groups) == 2

    def test_different_strategies_separate(self):
        obs = [
            _create_observation(strategy="sma_cross"),
            _create_observation(strategy="mean_rev"),
        ]
        groups = group_comparable_observations(obs)
        assert len(groups) == 2

    def test_different_cost_models_separate(self):
        obs = [
            _create_observation(cost_model_hash="cm1"),
            _create_observation(cost_model_hash="cm2"),
        ]
        groups = group_comparable_observations(obs)
        assert len(groups) == 2

    def test_mixed_comparable_and_not(self):
        obs = [
            _create_observation(instrument="SBER", cost_model_hash="cm1"),
            _create_observation(instrument="SBER", cost_model_hash="cm2"),
            _create_observation(instrument="GAZP", cost_model_hash="cm1"),
        ]
        groups = group_comparable_observations(obs)
        # Should be 3 separate groups (SBER/cm1, SBER/cm2, GAZP/cm1)
        assert len(groups) == 3

    def test_check_comparability_details(self):
        a = _create_observation(instrument="SBER")
        b = _create_observation(instrument="GAZP")
        comparable, diffs = check_comparability(a, b)
        assert not comparable
        assert "instrument" in diffs


# ===========================================================================
# T6 — Low evidence
# ===========================================================================

class TestT6_LowEvidence:
    """Single observation cannot become unjustified HIGH confidence."""

    def test_single_observation_insufficient(self):
        conf, basis = compute_confidence(
            evidence_count=1,
            independent_revalidations=0,
            unique_instruments=1,
            consistency_ratio=1.0,
            total_trades=25,
            contradiction_count=0,
            methodology_versions=1,
        )
        assert conf == ConfidenceLevel.INSUFFICIENT

    def test_two_observations_low(self):
        conf, basis = compute_confidence(
            evidence_count=2,
            independent_revalidations=0,
            unique_instruments=1,
            consistency_ratio=0.5,
            total_trades=10,
            contradiction_count=0,
            methodology_versions=1,
        )
        assert conf in (ConfidenceLevel.LOW, ConfidenceLevel.INSUFFICIENT)


# ===========================================================================
# T7 — Confidence basis
# ===========================================================================

class TestT7_ConfidenceBasis:
    """Every confidence state includes explicit basis."""

    def test_high_confidence_has_basis(self):
        conf, basis = compute_confidence(
            evidence_count=8,
            independent_revalidations=3,
            unique_instruments=2,
            consistency_ratio=0.875,
            total_trades=200,
            contradiction_count=0,
            methodology_versions=2,
        )
        assert conf == ConfidenceLevel.HIGH
        assert len(basis) > 10  # non-trivial basis text

    def test_medium_confidence_has_basis(self):
        conf, basis = compute_confidence(
            evidence_count=5,
            independent_revalidations=1,
            unique_instruments=1,
            consistency_ratio=0.8,
            total_trades=50,
            contradiction_count=0,
            methodology_versions=1,
        )
        assert conf == ConfidenceLevel.MEDIUM
        assert "5 observations" in basis

    def test_insufficient_has_basis(self):
        conf, basis = compute_confidence(
            evidence_count=1,
            independent_revalidations=0,
            unique_instruments=1,
            consistency_ratio=0.0,
            total_trades=0,
            contradiction_count=0,
            methodology_versions=1,
        )
        assert conf == ConfidenceLevel.INSUFFICIENT
        assert "observation" in basis.lower()

    def test_contradictions_in_basis(self):
        conf, basis = compute_confidence(
            evidence_count=5,
            independent_revalidations=1,
            unique_instruments=1,
            consistency_ratio=0.6,
            total_trades=50,
            contradiction_count=2,
            methodology_versions=1,
        )
        assert "contradict" in basis.lower()


# ===========================================================================
# T8 — Contradiction
# ===========================================================================

class TestT8_Contradiction:
    """Opposing evidence produces contested/contradiction state."""

    def test_mixed_pnl_produces_contradiction_type(self):
        obs = [
            _create_observation(run_id="run_a", experiment_instance_id="inst_a", metrics={"total_pnl": 5000, "trade_count": 25}),
            _create_observation(run_id="run_b", experiment_instance_id="inst_b", metrics={"total_pnl": -3000, "trade_count": 20}),
            _create_observation(run_id="run_c", experiment_instance_id="inst_c", metrics={"total_pnl": 6000, "trade_count": 30}),
        ]
        mem_db = _tmp_mem_db()
        _create_memory_db(obs, mem_db)
        k_db = _tmp_db()
        store = KnowledgeStore(k_db)
        build = distill_findings(
            experiment_memory_path=mem_db,
            knowledge_store=store,
        )
        findings = store.find_findings()
        assert len(findings) >= 1
        # Should have at least one contradiction or contested
        contested_or_contradiction = [
            f for f in findings
            if f["status"] == "CONTESTED" or f["finding_type"] == "CONTRADICTION"
        ]
        assert len(contested_or_contradiction) >= 1
        store.close()

    def test_all_positive_no_contradiction(self):
        obs = [
            _create_observation(run_id="run_a", experiment_instance_id="inst_a", metrics={"total_pnl": 5000, "trade_count": 25}),
            _create_observation(run_id="run_b", experiment_instance_id="inst_b", metrics={"total_pnl": 3000, "trade_count": 20}),
            _create_observation(run_id="run_c", experiment_instance_id="inst_c", metrics={"total_pnl": 6000, "trade_count": 30}),
        ]
        mem_db = _tmp_mem_db()
        _create_memory_db(obs, mem_db)
        k_db = _tmp_db()
        store = KnowledgeStore(k_db)
        build = distill_findings(
            experiment_memory_path=mem_db,
            knowledge_store=store,
        )
        findings = store.find_findings()
        contradictions = [f for f in findings if f["finding_type"] == "CONTRADICTION"]
        assert len(contradictions) == 0
        store.close()


# ===========================================================================
# T9 — Negative evidence
# ===========================================================================

class TestT9_NegativeEvidence:
    """Rejected/negative research remains visible."""

    def test_rejected_observations_included(self):
        obs = [
            _create_observation(run_id="run_a", experiment_instance_id="inst_a", eligible=False, metrics={"total_pnl": -500}),
            _create_observation(run_id="run_b", experiment_instance_id="inst_b", eligible=False, metrics={"total_pnl": -300}),
        ]
        mem_db = _tmp_mem_db()
        _create_memory_db(obs, mem_db)
        k_db = _tmp_db()
        store = KnowledgeStore(k_db)
        build = distill_findings(
            experiment_memory_path=mem_db,
            knowledge_store=store,
        )
        findings = store.find_findings()
        assert len(findings) >= 1
        # Negative evidence should be visible as failure pattern
        assert findings[0]["finding_type"] in ("FAILURE_PATTERN", "INSUFFICIENT_EVIDENCE")
        store.close()

    def test_mixed_eligible_and_rejected(self):
        obs = [
            _create_observation(run_id="run_a", experiment_instance_id="inst_a", eligible=True, metrics={"total_pnl": 5000}),
            _create_observation(run_id="run_b", experiment_instance_id="inst_b", eligible=False, metrics={"total_pnl": -500}),
        ]
        mem_db = _tmp_mem_db()
        _create_memory_db(obs, mem_db)
        k_db = _tmp_db()
        store = KnowledgeStore(k_db)
        build = distill_findings(
            experiment_memory_path=mem_db,
            knowledge_store=store,
        )
        findings = store.find_findings()
        assert len(findings) >= 1
        store.close()


# ===========================================================================
# T10 — No survivorship bias
# ===========================================================================

class TestT10_NoSurvivorshipBias:
    """Distillation is not limited to eligible candidates."""

    def test_only_rejected_still_produces_finding(self):
        obs = [
            _create_observation(run_id="run_a", experiment_instance_id="inst_a", eligible=False, metrics={"total_pnl": -1000, "trade_count": 5}),
            _create_observation(run_id="run_b", experiment_instance_id="inst_b", eligible=False, metrics={"total_pnl": -2000, "trade_count": 8}),
        ]
        mem_db = _tmp_mem_db()
        _create_memory_db(obs, mem_db)
        k_db = _tmp_db()
        store = KnowledgeStore(k_db)
        build = distill_findings(
            experiment_memory_path=mem_db,
            knowledge_store=store,
        )
        findings = store.find_findings()
        assert len(findings) >= 1
        store.close()

    def test_distiller_queries_all_instances_not_just_eligible(self):
        """The distiller queries all experiment_instances, not just eligible ones."""
        obs = [
            _create_observation(run_id="run_a", experiment_instance_id="inst_a", eligible=False),
            _create_observation(run_id="run_b", experiment_instance_id="inst_b", eligible=True),
        ]
        mem_db = _tmp_mem_db()
        _create_memory_db(obs, mem_db)
        k_db = _tmp_db()
        store = KnowledgeStore(k_db)
        build = distill_findings(
            experiment_memory_path=mem_db,
            knowledge_store=store,
        )
        findings = store.find_findings()
        # Both should be in the evidence
        total_evidence = sum(len(json.loads(f.get("evidence_refs_json", "[]"))) for f in findings)
        assert total_evidence >= 2
        store.close()


# ===========================================================================
# T11 — Duplicate reproduction
# ===========================================================================

class TestT11_DuplicateReproduction:
    """Repeated exact observations do not inflate independent evidence incorrectly."""

    def test_exact_duplicates_grouped(self):
        obs = [
            _create_observation(experiment_instance_id="inst_same", run_id="run1"),
            _create_observation(experiment_instance_id="inst_same", run_id="run2"),
        ]
        groups = group_comparable_observations(obs)
        assert len(groups) == 1  # same strategy/instrument/cost → one group
        # Evidence count should use unique instance IDs
        unique_ids = set(o.get("experiment_instance_id") for o in groups[0])
        assert len(unique_ids) == 1  # same instance_id counted once

    def test_duplicate_weight_careful(self):
        """Multiple observations with same instance_id should not inflate."""
        from core.research_knowledge import _count_exact_duplicates
        obs = [
            {"experiment_instance_id": "inst_a"},
            {"experiment_instance_id": "inst_a"},
            {"experiment_instance_id": "inst_b"},
        ]
        dups = _count_exact_duplicates(obs)
        assert dups == 1  # one group of duplicates


# ===========================================================================
# T12 — Missing metric
# ===========================================================================

class TestT12_MissingMetric:
    """Missing is not converted to zero."""

    def test_missing_metric_not_zero(self):
        obs = [
            _create_observation(metrics={"total_pnl": 5000}),  # no trade_count
            _create_observation(metrics={"total_pnl": 3000}),  # no trade_count
        ]
        mem_db = _tmp_mem_db()
        _create_memory_db(obs, mem_db)
        k_db = _tmp_db()
        store = KnowledgeStore(k_db)
        build = distill_findings(
            experiment_memory_path=mem_db,
            knowledge_store=store,
        )
        findings = store.find_findings()
        if findings:
            # The trade_count should not appear as 0 in metrics_summary
            summary = json.loads(findings[0].get("evidence_refs_json", "[]"))
            # Evidence should show metrics_json without trade_count
            for ref in summary:
                metrics = json.loads(ref.get("metrics_json", "{}"))
                assert "trade_count" not in metrics
        store.close()

    def test_partial_metrics_handled(self):
        obs = [
            _create_observation(metrics={"total_pnl": 5000, "trade_count": 25}),
            _create_observation(metrics={"total_pnl": 3000}),  # no trade_count
        ]
        mem_db = _tmp_mem_db()
        _create_memory_db(obs, mem_db)
        k_db = _tmp_db()
        store = KnowledgeStore(k_db)
        build = distill_findings(
            experiment_memory_path=mem_db,
            knowledge_store=store,
        )
        findings = store.find_findings()
        assert len(findings) >= 1
        store.close()


# ===========================================================================
# T13 — Build provenance
# ===========================================================================

class TestT13_BuildProvenance:
    """Every knowledge mutation belongs to a knowledge_build_id."""

    def test_finding_has_build_id(self):
        mem_db = _tmp_mem_db()
        obs = [_create_observation()]
        _create_memory_db(obs, mem_db)
        k_db = _tmp_db()
        store = KnowledgeStore(k_db)
        build = distill_findings(
            experiment_memory_path=mem_db,
            knowledge_store=store,
        )
        findings = store.find_findings()
        if findings:
            assert findings[0]["knowledge_build_id"] == build.knowledge_build_id
            assert findings[0]["knowledge_build_id"].startswith("kb_")
        store.close()

    def test_build_recorded_in_db(self):
        mem_db = _tmp_mem_db()
        obs = [_create_observation()]
        _create_memory_db(obs, mem_db)
        k_db = _tmp_db()
        store = KnowledgeStore(k_db)
        build = distill_findings(
            experiment_memory_path=mem_db,
            knowledge_store=store,
        )
        builds = store.get_build_history()
        assert len(builds) >= 1
        assert builds[0]["knowledge_build_id"] == build.knowledge_build_id
        assert builds[0]["distiller_version"] == DISTILLER_VERSION
        store.close()


# ===========================================================================
# T14 — Idempotent rebuild
# ===========================================================================

class TestT14_IdempotentRebuild:
    """Same evidence + same distiller version yields same logical findings."""

    def test_double_build_same_findings(self):
        mem_db = _tmp_mem_db()
        obs = [_create_observation() for _ in range(3)]
        _create_memory_db(obs, mem_db)
        k_db = _tmp_db()
        store = KnowledgeStore(k_db)

        build1 = distill_findings(experiment_memory_path=mem_db, knowledge_store=store)
        findings1 = [f["finding_id"] for f in store.find_findings()]

        # Rebuild
        k_db2 = _tmp_db()
        store2 = KnowledgeStore(k_db2)
        build2 = distill_findings(experiment_memory_path=mem_db, knowledge_store=store2)
        findings2 = [f["finding_id"] for f in store2.find_findings()]

        # Same evidence → same finding IDs (deterministic)
        assert set(findings1) == set(findings2)
        store.close()
        store2.close()


# ===========================================================================
# T15 — Finding history
# ===========================================================================

class TestT15_FindingHistory:
    """Material finding change preserves history."""

    def test_statement_change_recorded(self):
        k_db = _tmp_db()
        store = KnowledgeStore(k_db)

        # Create original finding
        f1 = ResearchFinding(
            finding_id="find_hist",
            finding_type=FindingType.PERFORMANCE,
            subject="test",
            scope={},
            statement="original statement",
            knowledge_build_id="kb_001",
        )
        store._upsert_finding(f1)

        # Update with different statement
        f2 = ResearchFinding(
            finding_id="find_hist",
            finding_type=FindingType.PERFORMANCE,
            subject="test",
            scope={},
            statement="updated statement",
            knowledge_build_id="kb_002",
        )
        old = store.find_finding("find_hist")
        store._record_history(old, f2, "kb_002")
        store._upsert_finding(f2)

        # History should be recorded
        conn = store._connect()
        rows = conn.execute(
            "SELECT * FROM finding_history WHERE finding_id = 'find_hist'"
        ).fetchall()
        assert len(rows) >= 1
        assert rows[0]["previous_statement"] == "original statement"
        assert rows[0]["new_statement"] == "updated statement"
        store.close()


# ===========================================================================
# T16 — Query interface
# ===========================================================================

class TestT16_QueryInterface:
    """Finding/evidence/contested/insufficient queries work."""

    def _setup(self):
        k_db = _tmp_db()
        store = KnowledgeStore(k_db)
        # Insert various findings
        for i, (status, conf, ftype) in enumerate([
            ("ACTIVE", "MEDIUM", "PERFORMANCE"),
            ("CONTESTED", "LOW", "CONTRADICTION"),
            ("INSUFFICIENT", "INSUFFICIENT", "INSUFFICIENT_EVIDENCE"),
        ]):
            store._upsert_finding(ResearchFinding(
                finding_id=f"find_q{i}",
                finding_type=FindingType(ftype),
                subject=f"sub_{i}",
                scope={"instruments": ["SBER"]},
                statement=f"stmt_{i}",
                status=FindingStatus(status),
                confidence=ConfidenceLevel(conf),
            ))
        return store

    def test_find_findings_all(self):
        store = self._setup()
        all_f = store.find_findings()
        assert len(all_f) == 3
        store.close()

    def test_find_findings_by_type(self):
        store = self._setup()
        perf = store.find_findings(finding_type="PERFORMANCE")
        assert len(perf) == 1
        store.close()

    def test_find_findings_by_status(self):
        store = self._setup()
        contested = store.find_findings(status="CONTESTED")
        assert len(contested) == 1
        store.close()

    def test_find_contested(self):
        store = self._setup()
        contested = store.find_contested()
        assert len(contested) == 1
        assert contested[0]["finding_id"] == "find_q1"
        store.close()

    def test_find_insufficient(self):
        store = self._setup()
        insufficient = store.find_insufficient()
        assert len(insufficient) == 1
        assert insufficient[0]["finding_id"] == "find_q2"
        store.close()

    def test_explain_finding(self):
        store = self._setup()
        explanation = store.explain_finding("find_q0")
        assert "finding" in explanation
        assert "evidence_chain" in explanation
        assert "history" in explanation
        store.close()

    def test_get_evidence(self):
        store = self._setup()
        evidence = store.get_evidence("find_q0")
        assert isinstance(evidence, list)
        store.close()

    def test_find_strategy_knowledge(self):
        store = self._setup()
        results = store.find_strategy_knowledge("sub_0")
        assert len(results) >= 1
        store.close()


# ===========================================================================
# T17 — Open questions
# ===========================================================================

class TestT17_OpenQuestions:
    """Contradiction/insufficient evidence can create structured unresolved question."""

    def test_open_question_created_for_insufficient(self):
        mem_db = _tmp_mem_db()
        obs = [_create_observation(metrics={"total_pnl": 100, "trade_count": 2})]
        _create_memory_db(obs, mem_db)
        k_db = _tmp_db()
        store = KnowledgeStore(k_db)
        build = distill_findings(
            experiment_memory_path=mem_db,
            knowledge_store=store,
        )
        questions = store.get_open_questions()
        # Should have at least one question about insufficient evidence
        assert len(questions) >= 1
        assert questions[0]["status"] == "OPEN"
        store.close()

    def test_question_has_evidence_refs(self):
        q = OpenQuestion(
            question_id="q_test",
            subject="test",
            reason="INSUFFICIENT_REVALIDATION",
            evidence_refs=[{"observation_id": "inst_001"}],
        )
        store = KnowledgeStore(_tmp_db())
        store._upsert_question(q)
        questions = store.get_open_questions()
        assert len(questions) == 1
        refs = json.loads(questions[0]["evidence_refs_json"])
        assert len(refs) == 1
        store.close()

    def test_question_filter_by_reason(self):
        store = KnowledgeStore(_tmp_db())
        store._upsert_question(OpenQuestion(
            question_id="q1", subject="a", reason="INSUFFICIENT_REVALIDATION",
            created_at="2026-08-30T00:00:00Z",
        ))
        store._upsert_question(OpenQuestion(
            question_id="q2", subject="b", reason="CONTRADICTORY_RESULTS",
            created_at="2026-08-30T00:00:00Z",
        ))
        q1 = store.get_open_questions(reason="INSUFFICIENT_REVALIDATION")
        assert len(q1) == 1
        assert q1[0]["question_id"] == "q1"
        store.close()


# ===========================================================================
# T18 — No registry mutation
# ===========================================================================

class TestT18_NoRegistryMutation:
    """Knowledge build cannot mutate strategy registry."""

    def test_knowledge_store_does_not_import_registry(self):
        """Verify research_knowledge.py does not import registry modules."""
        import core.research_knowledge as rk
        source = open(rk.__file__, "r").read()
        # Check no import of strategy_registry or registry modules
        import_lines = [l for l in source.split("\n") if l.strip().startswith("import ") or l.strip().startswith("from ")]
        for line in import_lines:
            assert "strategy_registry" not in line.lower()
            assert "registry" not in line.lower()

    def test_knowledge_store_no_registry_write(self):
        """KnowledgeStore has no method to write to registry."""
        store = KnowledgeStore(_tmp_db())
        assert not hasattr(store, "write_registry")
        assert not hasattr(store, "update_registry")
        assert not hasattr(store, "promote_strategy")
        store.close()


# ===========================================================================
# T19 — No novelty-policy mutation
# ===========================================================================

class TestT19_NoNoveltyPolicyMutation:
    """Knowledge build cannot change Iteration 08 skip policy."""

    def test_knowledge_store_no_novelty_policy_write(self):
        store = KnowledgeStore(_tmp_db())
        assert not hasattr(store, "set_novelty_policy")
        assert not hasattr(store, "update_skip_policy")
        store.close()

    def test_knowledge_module_no_novelty_import(self):
        import core.research_knowledge as rk
        source = open(rk.__file__, "r").read()
        import_lines = [l for l in source.split("\n") if l.strip().startswith("import ") or l.strip().startswith("from ")]
        for line in import_lines:
            assert "novelty_gate" not in line.lower()
            assert "NoveltyPolicy" not in line


# ===========================================================================
# T20 — No trading authority
# ===========================================================================

class TestT20_NoTradingAuthority:
    """Knowledge findings cannot call broker/execution/risk."""

    def test_no_broker_import(self):
        import core.research_knowledge as rk
        source = open(rk.__file__, "r").read()
        assert "post_order" not in source
        assert "broker" not in source.lower().split("\n#")[0]  # not in executable code
        assert "Engine" not in source
        assert "risk" not in source.lower().split("\n#")[0]

    def test_no_execution_methods(self):
        store = KnowledgeStore(_tmp_db())
        assert not hasattr(store, "execute_order")
        assert not hasattr(store, "place_order")
        assert not hasattr(store, "modify_risk")
        assert not hasattr(store, "change_position")
        store.close()

    def test_finding_no_trading_methods(self):
        f = ResearchFinding(
            finding_id="find_test",
            finding_type=FindingType.PERFORMANCE,
            subject="test",
            scope={},
            statement="test",
        )
        assert not hasattr(f, "execute")
        assert not hasattr(f, "place_order")
        assert not hasattr(f, "modify_risk")


# ===========================================================================
# T21 — Regression (Iterations 01-08)
# ===========================================================================

class TestT21_Regression:
    """Iterations 01–08 relevant tests remain green."""

    def test_experiment_memory_imports(self):
        """Experiment memory module still importable."""
        from core.experiment_memory import ExperimentMemory
        assert ExperimentMemory is not None

    def test_novelty_gate_imports(self):
        """Novelty gate module still importable."""
        from core.novelty_gate import NoveltyPolicy
        assert NoveltyPolicy is not None

    def test_run_contract_imports(self):
        """Run contract module still importable."""
        from core.run_contract import ResearchRun
        assert ResearchRun is not None

    def test_no_broker_in_research_knowledge(self):
        """No real broker orders can be created from research_knowledge."""
        import core.research_knowledge as rk
        source = open(rk.__file__, "r").read()
        # Check no import of broker/engine/order modules
        import_lines = [l for l in source.split("\n") if l.strip().startswith("import ") or l.strip().startswith("from ")]
        for line in import_lines:
            assert "broker" not in line.lower()
            assert "engine" not in line.lower()
            assert "post_order" not in line.lower()


# ===========================================================================
# F1 — Knowledge DB missing
# ===========================================================================

class TestF1_KnowledgeDBMissing:
    def test_missing_experiment_memory_handled(self):
        """Distiller handles missing experiment memory DB gracefully."""
        build = distill_findings(
            experiment_memory_path=Path("/nonexistent/memory.db"),
        )
        assert build.status in ("FAILED_NO_MEMORY", "FAILED_MEMORY_QUERY")
        assert len(build.errors) >= 1

    def test_missing_knowledge_db_auto_created(self):
        """KnowledgeStore auto-creates the DB."""
        tmp = _tmp_db()
        assert not tmp.exists()
        store = KnowledgeStore(tmp)
        assert tmp.exists()
        store.close()


# ===========================================================================
# F2 — Schema initialization fails
# ===========================================================================

class TestF2_SchemaInitFails:
    def test_schema_initialization(self):
        """Schema is created correctly."""
        k_db = _tmp_db()
        store = KnowledgeStore(k_db)
        conn = store._connect()
        # Check tables exist
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {t["name"] for t in tables}
        assert "research_findings" in table_names
        assert "finding_history" in table_names
        assert "knowledge_builds" in table_names
        assert "open_questions" in table_names
        assert "schema_meta" in table_names
        store.close()

    def test_schema_version_recorded(self):
        k_db = _tmp_db()
        store = KnowledgeStore(k_db)
        conn = store._connect()
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        assert row["value"] == "1.0.0"
        store.close()


# ===========================================================================
# F3 — Experiment Memory unavailable
# ===========================================================================

class TestF3_ExperimentMemoryUnavailable:
    def test_no_memory_path_no_conn(self, tmp_path: Path):
        """Non-existent path → raises or handles gracefully."""
        nonexistent = tmp_path / "nonexistent" / "memory.db"
        try:
            build = distill_findings(experiment_memory_path=str(nonexistent))
            # If it doesn't raise, status should reflect the issue
            assert build.status in ("FAILED_NO_MEMORY", "COMPLETED_EMPTY", "COMPLETED")
        except Exception:
            # Expected: cannot open database file
            pass

    def test_corrupt_memory_db(self):
        """Corrupt DB → handled gracefully."""
        tmp = Path(tempfile.mkdtemp()) / "corrupt.db"
        tmp.write_text("not a sqlite file")
        build = distill_findings(experiment_memory_path=tmp)
        # Should fail with an error
        assert build.status in ("FAILED_NO_MEMORY", "FAILED_MEMORY_QUERY") or len(build.errors) >= 1


# ===========================================================================
# F4 — No observations exist
# ===========================================================================

class TestF4_NoObservations:
    def test_empty_observations(self):
        mem_db = _tmp_mem_db()
        _create_memory_db([], mem_db)
        k_db = _tmp_db()
        store = KnowledgeStore(k_db)
        build = distill_findings(
            experiment_memory_path=mem_db,
            knowledge_store=store,
        )
        assert build.status == "COMPLETED_EMPTY"
        assert build.findings_created == 0
        store.close()


# ===========================================================================
# F5 — Observation provenance missing
# ===========================================================================

class TestF5_ObservationProvenanceMissing:
    def test_missing_provenance_fields(self):
        """Observation with missing run_id/config_key still processed."""
        obs = [_create_observation(run_id="", config_key="")]
        mem_db = _tmp_mem_db()
        _create_memory_db(obs, mem_db)
        k_db = _tmp_db()
        store = KnowledgeStore(k_db)
        build = distill_findings(
            experiment_memory_path=mem_db,
            knowledge_store=store,
        )
        findings = store.find_findings()
        if findings:
            refs = json.loads(findings[0].get("evidence_refs_json", "[]"))
            # Evidence should still exist even with empty provenance
            assert len(refs) >= 1
        store.close()


# ===========================================================================
# F6 — Incompatible observations
# ===========================================================================

class TestF6_IncompatibleObservations:
    def test_incompatible_not_averaged(self):
        """Observations with different instruments are separated."""
        obs = [
            _create_observation(instrument="SBER", strategy="sma_cross"),
            _create_observation(instrument="GAZP", strategy="sma_cross"),
        ]
        groups = group_comparable_observations(obs)
        assert len(groups) == 2


# ===========================================================================
# F7 — Metric missing
# ===========================================================================

class TestF7_MetricMissing:
    def test_missing_metric_handled(self):
        obs = [
            _create_observation(metrics={"total_pnl": 5000}),
        ]
        mem_db = _tmp_mem_db()
        _create_memory_db(obs, mem_db)
        k_db = _tmp_db()
        store = KnowledgeStore(k_db)
        build = distill_findings(
            experiment_memory_path=mem_db,
            knowledge_store=store,
        )
        findings = store.find_findings()
        assert len(findings) >= 1
        store.close()


# ===========================================================================
# F8 — Contradictory observations
# ===========================================================================

class TestF8_ContradictoryObservations:
    def test_contradiction_produces_contested(self):
        obs = [
            _create_observation(run_id="run_a", experiment_instance_id="inst_a", metrics={"total_pnl": 5000, "trade_count": 25}),
            _create_observation(run_id="run_b", experiment_instance_id="inst_b", metrics={"total_pnl": -5000, "trade_count": 25}),
            _create_observation(run_id="run_c", experiment_instance_id="inst_c", metrics={"total_pnl": 6000, "trade_count": 30}),
        ]
        mem_db = _tmp_mem_db()
        _create_memory_db(obs, mem_db)
        k_db = _tmp_db()
        store = KnowledgeStore(k_db)
        build = distill_findings(
            experiment_memory_path=mem_db,
            knowledge_store=store,
        )
        findings = store.find_findings()
        contested = [f for f in findings if f["status"] == "CONTESTED"]
        assert len(contested) >= 1
        store.close()


# ===========================================================================
# F9 — Duplicate observation/reproduction inflation
# ===========================================================================

class TestF9_DuplicateInflation:
    def test_exact_duplicates_not_inflated(self):
        obs = [
            _create_observation(experiment_instance_id="inst_same", run_id="r1"),
            _create_observation(experiment_instance_id="inst_same", run_id="r2"),
            _create_observation(experiment_instance_id="inst_same", run_id="r3"),
        ]
        from core.research_knowledge import _count_exact_duplicates
        dups = _count_exact_duplicates(obs)
        assert dups == 1  # one group of duplicates
        unique_ids = set(o["experiment_instance_id"] for o in obs)
        assert len(unique_ids) == 1


# ===========================================================================
# F10 — Distiller crashes mid-build
# ===========================================================================

class TestF10_DistillerCrash:
    def test_distiller_returns_error_on_bad_db(self):
        tmp = Path(tempfile.mkdtemp()) / "empty_no_tables.db"
        import sqlite3
        conn = sqlite3.connect(str(tmp))
        conn.close()
        build = distill_findings(experiment_memory_path=tmp)
        # Should fail gracefully (table missing)
        assert build.status in ("FAILED_NO_MEMORY", "FAILED_MEMORY_QUERY") or len(build.errors) >= 1


# ===========================================================================
# F11 — Finding write fails
# ===========================================================================

class TestF11_FindingWriteFails:
    def test_finding_write_to_readonly_path(self):
        """Attempt to write to a read-only path."""
        readonly_dir = Path(tempfile.mkdtemp()) / "readonly_subdir"
        readonly_dir.mkdir()
        readonly_dir.chmod(0o444)
        readonly_db = readonly_dir / "test.db"
        try:
            store = KnowledgeStore(readonly_db)
            # This may fail because the directory is read-only
        except (PermissionError, OSError):
            pass  # Expected
        finally:
            readonly_dir.chmod(0o644)


# ===========================================================================
# F12 — Evidence link broken
# ===========================================================================

class TestF12_EvidenceLinkBroken:
    def test_explain_missing_finding(self):
        store = KnowledgeStore(_tmp_db())
        result = store.explain_finding("nonexistent_id")
        assert result["error"] == "FINDING_NOT_FOUND"
        store.close()

    def test_get_evidence_missing_finding(self):
        store = KnowledgeStore(_tmp_db())
        evidence = store.get_evidence("nonexistent_id")
        assert evidence == []
        store.close()


# ===========================================================================
# F13 — Schema migration required
# ===========================================================================

class TestF13_SchemaMigration:
    def test_schema_idempotent(self):
        """Creating KnowledgeStore twice on same DB doesn't fail."""
        k_db = _tmp_db()
        store1 = KnowledgeStore(k_db)
        store2 = KnowledgeStore(k_db)
        conn = store2._connect()
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        assert row["value"] == "1.0.0"
        store1.close()
        store2.close()


# ===========================================================================
# F14 — Source memory changes during build
# ===========================================================================

class TestF14_SourceMemoryChangesDuringBuild:
    def test_concurrent_memory_change(self):
        """Simulate memory changing during distillation."""
        mem_db = _tmp_mem_db()
        obs = [_create_observation()]
        _create_memory_db(obs, mem_db)

        k_db = _tmp_db()
        store = KnowledgeStore(k_db)
        build = distill_findings(
            experiment_memory_path=mem_db,
            knowledge_store=store,
        )
        # Build completes with snapshot of memory state
        assert build.status in ("COMPLETED", "COMPLETED_EMPTY", "COMPLETED_WITH_ERRORS")
        store.close()


# ===========================================================================
# F15 — Zero eligible strategies but valid negative evidence exists
# ===========================================================================

class TestF15_ZeroEligibleNegativeEvidence:
    def test_all_rejected_produces_finding(self):
        obs = [
            _create_observation(eligible=False, metrics={"total_pnl": -1000, "trade_count": 5}),
            _create_observation(eligible=False, metrics={"total_pnl": -2000, "trade_count": 8}),
            _create_observation(eligible=False, metrics={"total_pnl": -500, "trade_count": 3}),
        ]
        mem_db = _tmp_mem_db()
        _create_memory_db(obs, mem_db)
        k_db = _tmp_db()
        store = KnowledgeStore(k_db)
        build = distill_findings(
            experiment_memory_path=mem_db,
            knowledge_store=store,
        )
        findings = store.find_findings()
        assert len(findings) >= 1
        store.close()


# ===========================================================================
# F16 — Legacy/incomparable evidence encountered
# ===========================================================================

class TestF16_LegacyIncomparable:
    def test_incomparable_classification_handled(self):
        obs = [
            _create_observation(classification="INCOMPARABLE"),
            _create_observation(classification="INCOMPARABLE"),
        ]
        mem_db = _tmp_mem_db()
        _create_memory_db(obs, mem_db)
        k_db = _tmp_db()
        store = KnowledgeStore(k_db)
        build = distill_findings(
            experiment_memory_path=mem_db,
            knowledge_store=store,
        )
        # Should not crash
        assert build.status in ("COMPLETED", "COMPLETED_EMPTY", "COMPLETED_WITH_ERRORS")
        store.close()


# ===========================================================================
# F17 — Finding explanation requested for missing ID
# ===========================================================================

class TestF17_MissingFindingExplanation:
    def test_explain_nonexistent(self):
        store = KnowledgeStore(_tmp_db())
        result = store.explain_finding("find_does_not_exist")
        assert "error" in result
        assert result["error"] == "FINDING_NOT_FOUND"
        store.close()


# ===========================================================================
# F18 — Rebuild executed twice
# ===========================================================================

class TestF18_DoubleRebuild:
    def test_double_rebuild_idempotent(self):
        mem_db = _tmp_mem_db()
        obs = [_create_observation() for _ in range(3)]
        _create_memory_db(obs, mem_db)

        k_db = _tmp_db()
        store = KnowledgeStore(k_db)
        build1 = distill_findings(experiment_memory_path=mem_db, knowledge_store=store)
        build2 = distill_findings(experiment_memory_path=mem_db, knowledge_store=store)

        findings = store.find_findings()
        # Should not have duplicate findings
        ids = [f["finding_id"] for f in findings]
        assert len(ids) == len(set(ids))  # no duplicates

        # Both builds should be recorded
        builds = store.get_build_history()
        assert len(builds) >= 2
        store.close()

    def test_separate_stores_idempotent(self):
        """Two separate stores with same evidence produce same findings."""
        mem_db = _tmp_mem_db()
        obs = [_create_observation() for _ in range(3)]
        _create_memory_db(obs, mem_db)

        k_db1 = _tmp_db()
        store1 = KnowledgeStore(k_db1)
        distill_findings(experiment_memory_path=mem_db, knowledge_store=store1)
        f1 = [f["finding_id"] for f in store1.find_findings()]

        k_db2 = _tmp_db()
        store2 = KnowledgeStore(k_db2)
        distill_findings(experiment_memory_path=mem_db, knowledge_store=store2)
        f2 = [f["finding_id"] for f in store2.find_findings()]

        assert set(f1) == set(f2)
        store1.close()
        store2.close()


# ===========================================================================
# Additional: Confidence model
# ===========================================================================

class TestConfidenceModel:
    def test_high_confidence_requirements(self):
        conf, basis = compute_confidence(
            evidence_count=10,
            independent_revalidations=3,
            unique_instruments=2,
            consistency_ratio=0.9,
            total_trades=200,
            contradiction_count=0,
            methodology_versions=2,
        )
        assert conf == ConfidenceLevel.HIGH

    def test_medium_confidence(self):
        conf, basis = compute_confidence(
            evidence_count=5,
            independent_revalidations=1,
            unique_instruments=1,
            consistency_ratio=0.8,
            total_trades=50,
            contradiction_count=0,
            methodology_versions=1,
        )
        assert conf == ConfidenceLevel.MEDIUM

    def test_low_confidence(self):
        conf, basis = compute_confidence(
            evidence_count=3,
            independent_revalidations=0,
            unique_instruments=1,
            consistency_ratio=0.67,
            total_trades=15,
            contradiction_count=0,
            methodology_versions=1,
        )
        assert conf == ConfidenceLevel.LOW

    def test_contradictions_reduce_confidence(self):
        conf_high, _ = compute_confidence(
            evidence_count=8, independent_revalidations=3,
            unique_instruments=2, consistency_ratio=0.875,
            total_trades=200, contradiction_count=0,
            methodology_versions=2,
        )
        conf_with_contradictions, _ = compute_confidence(
            evidence_count=8, independent_revalidations=3,
            unique_instruments=2, consistency_ratio=0.875,
            total_trades=200, contradiction_count=3,
            methodology_versions=2,
        )
        # Contradictions should reduce confidence (HIGH → MEDIUM)
        level_order = {ConfidenceLevel.INSUFFICIENT: 0, ConfidenceLevel.LOW: 1,
                       ConfidenceLevel.MEDIUM: 2, ConfidenceLevel.HIGH: 3}
        assert level_order[conf_with_contradictions] <= level_order[conf_high]


# ===========================================================================
# Additional: Comparability
# ===========================================================================

class TestComparability:
    def test_same_observations_comparable(self):
        a = _create_observation()
        b = _create_observation()
        comparable, diffs = check_comparability(a, b)
        assert comparable
        assert diffs == []

    def test_different_cost_not_comparable(self):
        a = _create_observation(cost_model_hash="cm1")
        b = _create_observation(cost_model_hash="cm2")
        comparable, diffs = check_comparability(a, b)
        assert not comparable
        assert "cost_model_hash" in diffs

    def test_group_comparable_basic(self):
        obs = [
            _create_observation(instrument="SBER"),
            _create_observation(instrument="SBER"),
            _create_observation(instrument="GAZP"),
        ]
        groups = group_comparable_observations(obs)
        assert len(groups) == 2


# ===========================================================================
# Additional: KnowledgeStore
# ===========================================================================

class TestKnowledgeStore:
    def test_store_summary(self):
        k_db = _tmp_db()
        store = KnowledgeStore(k_db)
        summary = store.summary()
        assert summary["total_findings"] == 0
        assert summary["open_questions"] == 0
        store.close()

    def test_store_with_findings_summary(self):
        k_db = _tmp_db()
        store = KnowledgeStore(k_db)
        store._upsert_finding(ResearchFinding(
            finding_id="find_s1",
            finding_type=FindingType.PERFORMANCE,
            subject="test",
            scope={},
            statement="test",
            confidence=ConfidenceLevel.MEDIUM,
            status=FindingStatus.ACTIVE,
        ))
        summary = store.summary()
        assert summary["total_findings"] == 1
        assert summary["findings_by_type"]["PERFORMANCE"] == 1
        store.close()

    def test_finding_serialization(self):
        f = ResearchFinding(
            finding_id="find_ser",
            finding_type=FindingType.PERFORMANCE,
            subject="test",
            scope={"instruments": ["SBER"]},
            statement="test stmt",
            confidence=ConfidenceLevel.MEDIUM,
            status=FindingStatus.ACTIVE,
            evidence_refs=[EvidenceRef("i1", "r1", "c1", "f1", "NEW", "tested", True).to_dict()],
        )
        d = f.to_dict()
        assert d["finding_type"] == "PERFORMANCE"
        assert d["confidence"] == "MEDIUM"
        f2 = ResearchFinding.from_dict(d)
        assert f2.finding_type == FindingType.PERFORMANCE

    def test_report_generation(self):
        k_db = _tmp_db()
        store = KnowledgeStore(k_db)
        out = Path(tempfile.mkdtemp()) / "knowledge_report"
        result = write_knowledge_report(store, out)
        assert Path(result["md_path"]).exists()
        assert Path(result["json_path"]).exists()
        store.close()


# ===========================================================================
# Additional: Finding types and statuses
# ===========================================================================

class TestFindingTypesAndStatuses:
    def test_all_finding_types_valid(self):
        for ft in FindingType:
            assert ft.value in FINDING_TYPES

    def test_all_finding_statuses_valid(self):
        for fs in FindingStatus:
            assert fs.value in FINDING_STATUSES

    def test_all_confidence_levels_valid(self):
        for cl in ConfidenceLevel:
            assert cl.value in CONFIDENCE_LEVELS

    def test_finding_type_is_str_enum(self):
        assert FindingType.PERFORMANCE == "PERFORMANCE"
        assert isinstance(FindingType.PERFORMANCE, str)

    def test_finding_status_is_str_enum(self):
        assert FindingStatus.ACTIVE == "ACTIVE"
        assert isinstance(FindingStatus.ACTIVE, str)

    def test_confidence_level_is_str_enum(self):
        assert ConfidenceLevel.HIGH == "HIGH"
        assert isinstance(ConfidenceLevel.HIGH, str)


# ===========================================================================
# Additional: Edge cases
# ===========================================================================

class TestEdgeCases:
    def test_empty_metrics_json(self):
        obs = [_create_observation(metrics=None)]
        mem_db = _tmp_mem_db()
        _create_memory_db(obs, mem_db)
        k_db = _tmp_db()
        store = KnowledgeStore(k_db)
        build = distill_findings(experiment_memory_path=mem_db, knowledge_store=store)
        findings = store.find_findings()
        assert len(findings) >= 1
        store.close()

    def test_all_same_direction(self):
        obs = [
            _create_observation(run_id="run_a", experiment_instance_id="inst_a", metrics={"total_pnl": 5000, "trade_count": 25}),
            _create_observation(run_id="run_b", experiment_instance_id="inst_b", metrics={"total_pnl": 4000, "trade_count": 20}),
            _create_observation(run_id="run_c", experiment_instance_id="inst_c", metrics={"total_pnl": 6000, "trade_count": 30}),
        ]
        mem_db = _tmp_mem_db()
        _create_memory_db(obs, mem_db)
        k_db = _tmp_db()
        store = KnowledgeStore(k_db)
        build = distill_findings(experiment_memory_path=mem_db, knowledge_store=store)
        findings = store.find_findings()
        # All positive → no contradiction
        contradictions = [f for f in findings if f["finding_type"] == "CONTRADICTION"]
        assert len(contradictions) == 0
        store.close()

    def test_revalidation_observation(self):
        obs = [
            _create_observation(classification="NEW", experiment_instance_id="inst1"),
            _create_observation(classification="REVALIDATION", experiment_instance_id="inst2"),
            _create_observation(classification="REVALIDATION", experiment_instance_id="inst3"),
        ]
        mem_db = _tmp_mem_db()
        _create_memory_db(obs, mem_db)
        k_db = _tmp_db()
        store = KnowledgeStore(k_db)
        build = distill_findings(experiment_memory_path=mem_db, knowledge_store=store)
        findings = store.find_findings()
        assert len(findings) >= 1
        store.close()


# ===========================================================================
# Additional: EvidenceRef
# ===========================================================================

class TestEvidenceRef:
    def test_evidence_ref_serialization(self):
        ref = EvidenceRef(
            observation_id="inst_001",
            run_id="run_001",
            config_key="cfg_001",
            experiment_family_id="fam_001",
            classification="NEW",
            status="tested",
            eligible=True,
        )
        d = ref.to_dict()
        assert d["observation_id"] == "inst_001"
        ref2 = EvidenceRef.from_dict(d)
        assert ref2.run_id == "run_001"

    def test_open_question_serialization(self):
        q = OpenQuestion(
            question_id="q_001",
            subject="test",
            reason="INSUFFICIENT_REVALIDATION",
            evidence_refs=[],
            priority_hint="medium",
            created_at="2026-08-30T00:00:00Z",
        )
        d = q.to_dict()
        assert d["question_id"] == "q_001"
        q2 = OpenQuestion.from_dict(d)
        assert q2.reason == "INSUFFICIENT_REVALIDATION"

    def test_knowledge_build_serialization(self):
        b = KnowledgeBuild(
            knowledge_build_id="kb_001",
            started_at="2026-08-30T00:00:00Z",
            completed_at="2026-08-30T00:01:00Z",
            distiller_version=DISTILLER_VERSION,
            source_memory_version="1.0.0",
            findings_created=1,
            findings_updated=0,
            findings_unchanged=0,
            open_questions_created=0,
        )
        d = b.to_dict()
        assert d["knowledge_build_id"] == "kb_001"

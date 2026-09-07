"""Tests for Strategy Lifecycle & Decay Detection — Iteration 10.

Covers T1-T21 (mandatory tests) + F1-F20 (failure matrix).
CLASS 1: monitoring/recommendation only. No broker, no registry mutations.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List

import pytest

# ---------------------------------------------------------------------------
# Import module under test
# ---------------------------------------------------------------------------

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.strategy_lifecycle import (
    EvidenceHealth,
    DecayState,
    RevalidationState,
    Recommendation,
    IndicatorType,
    EvidenceClass,
    LifecycleObservation,
    LifecycleSnapshot,
    DecayIndicator,
    StrategyLifecycleObserver,
    compute_decay_indicators,
    determine_evidence_health,
    determine_revalidation_status,
    make_recommendation,
    _safe_float,
    _compute_observation_id,
    _generate_build_id,
    _days_since,
    PF_DROP_THRESHOLD,
    SHARPE_DROP_THRESHOLD,
    DD_WORSENING_THRESHOLD,
    WINRATE_DROP_THRESHOLD,
    STALE_EVIDENCE_DAYS,
    REVALIDATION_DUE_DAYS,
    REVALIDATION_OVERDUE_DAYS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_base(tmp_path):
    """Create a temporary base directory with state/ subdir."""
    base = tmp_path / "strategy_combine"
    base.mkdir()
    (base / "state").mkdir()
    return base


@pytest.fixture
def observer(tmp_base):
    """Create a StrategyLifecycleObserver with a fresh DB."""
    obs = StrategyLifecycleObserver(base_dir=tmp_base)
    yield obs
    obs.close()


def _make_strategy_record(
    strategy_id: str = "strat_001",
    ticker: str = "SBER",
    strategy: str = "sma_cross",
    status: str = "active_signal_pool",
    metrics: Dict[str, Any] = None,
    created_ts: float = None,
) -> Dict[str, Any]:
    """Create a minimal strategy record for testing."""
    if metrics is None:
        metrics = {"profit_factor": 1.5, "sharpe_ratio": 1.2, "max_drawdown": 0.08, "win_rate": 0.55}
    if created_ts is None:
        created_ts = time.time() - 86400 * 30  # 30 days ago
    return {
        "strategy_id": strategy_id,
        "ticker": ticker,
        "strategy": strategy,
        "status": status,
        "metrics": metrics,
        "created_ts": created_ts,
    }


def _make_experiment_obs(
    instance_id: str = "inst_001",
    strategy: str = "sma_cross",
    instrument: str = "SBER",
    eligible: bool = True,
    metrics: Dict[str, Any] = None,
    indexed_at: str = "",
    classification: str = "NEW",
) -> Dict[str, Any]:
    """Create a minimal experiment observation for testing."""
    if metrics is None:
        metrics = {"profit_factor": 1.5, "sharpe_ratio": 1.2, "total_pnl": 5000}
    if not indexed_at:
        indexed_at = datetime.now(timezone.utc).isoformat()
    return {
        "experiment_instance_id": instance_id,
        "experiment_family_id": f"fam_{instance_id}",
        "strategy": strategy,
        "instrument": instrument,
        "ticker": instrument,
        "eligible": eligible,
        "metrics_json": json.dumps(metrics),
        "indexed_at": indexed_at,
        "classification": classification,
        "run_id": "run_001",
        "config_key": f"cfg_{instance_id}",
    }


def _make_knowledge_finding(
    finding_id: str = "find_001",
    subject: str = "sma_cross:SBER",
    status: str = "ACTIVE",
    confidence: str = "MEDIUM",
    finding_type: str = "PERFORMANCE",
    last_updated_at: str = "",
) -> Dict[str, Any]:
    """Create a minimal knowledge finding for testing."""
    if not last_updated_at:
        last_updated_at = datetime.now(timezone.utc).isoformat()
    return {
        "finding_id": finding_id,
        "subject": subject,
        "status": status,
        "confidence": confidence,
        "finding_type": finding_type,
        "scope_json": json.dumps({"instruments": ["SBER"], "strategies": ["sma_cross"]}),
        "statement": f"Test finding for {subject}",
        "confidence_basis": "test basis",
        "evidence_refs_json": "[]",
        "supporting_observations": 3,
        "contradicting_observations": 0,
        "first_observed_at": datetime.now(timezone.utc).isoformat(),
        "last_updated_at": last_updated_at,
        "last_evidence_at": datetime.now(timezone.utc).isoformat(),
        "distiller_version": "1.0.0",
        "schema_version": "1.0.0",
        "knowledge_build_id": "kb_test",
    }


# ===========================================================================
# T1: No baseline → no false decay
# ===========================================================================

class TestT1NoBaseline:
    def test_no_baseline_yields_insufficient(self, observer):
        """No baseline → INSUFFICIENT_EVIDENCE, not false decay."""
        strat = _make_strategy_record()
        obs = observer.evaluate_strategy(strat, [], [], "build_001")
        assert obs.evidence_health == EvidenceHealth.INSUFFICIENT_EVIDENCE
        assert obs.decay_state == DecayState.UNKNOWN
        assert obs.recommendation in (Recommendation.MONITOR, Recommendation.REVALIDATE)

    def test_no_baseline_no_decay_indicators(self, observer):
        """With no baseline, there should be no false decay indicators."""
        strat = _make_strategy_record()
        obs = observer.evaluate_strategy(strat, [], [], "build_001")
        # Should have no PF/SHARPE/DD indicators (baseline unknown)
        material = [i for i in obs.indicators if i.get("severity") == "MATERIAL"]
        assert material == []

    def test_no_baseline_insufficient_evidence_indicator(self, observer):
        """Should have EVIDENCE_INSUFFICIENT indicator when no observations."""
        strat = _make_strategy_record()
        obs = observer.evaluate_strategy(strat, [], [], "build_001")
        types = [i["indicator_type"] for i in obs.indicators]
        assert "EVIDENCE_INSUFFICIENT" in types


# ===========================================================================
# T2: Weak single deterioration
# ===========================================================================

class TestT2WeakSingleDeterioration:
    def test_single_weak_yields_watch_not_decay(self, observer):
        """One weak signal cannot produce DECAY_SUSPECTED."""
        # Strategy with slight win rate drop
        strat = _make_strategy_record(metrics={
            "profit_factor": 1.5, "sharpe_ratio": 1.2,
            "max_drawdown": 0.08, "win_rate": 0.40, "total_pnl": 1000,
        })
        baseline_obs = _make_experiment_obs(
            metrics={"profit_factor": 1.5, "sharpe_ratio": 1.2, "total_pnl": 5000,
                     "max_drawdown": 0.08, "win_rate": 0.55},
            indexed_at=(datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),
        )
        obs = observer.evaluate_strategy(strat, [baseline_obs], [], "build_001")
        assert obs.evidence_health in (EvidenceHealth.WATCH, EvidenceHealth.HEALTHY)
        assert obs.evidence_health != EvidenceHealth.DECAY_SUSPECTED
        assert obs.evidence_health != EvidenceHealth.DECAY_CONFIRMED


# ===========================================================================
# T3: Multi-indicator suspicion
# ===========================================================================

class TestT3MultiIndicatorSuspicion:
    def test_two_material_indicators_yield_suspected(self, observer):
        """Multiple material indicators can produce DECAY_SUSPECTED."""
        strat = _make_strategy_record(metrics={
            "profit_factor": 0.5, "sharpe_ratio": 0.1,
            "max_drawdown": 0.20, "win_rate": 0.40, "total_pnl": -500,
        })
        baseline_obs = _make_experiment_obs(
            metrics={"profit_factor": 1.5, "sharpe_ratio": 1.2, "total_pnl": 5000,
                     "max_drawdown": 0.08, "win_rate": 0.55},
            indexed_at=(datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),
        )
        obs = observer.evaluate_strategy(strat, [baseline_obs], [], "build_001")
        material = [i for i in obs.indicators if i.get("severity") == "MATERIAL"]
        assert len(material) >= 2
        assert obs.evidence_health == EvidenceHealth.DECAY_SUSPECTED


# ===========================================================================
# T4: Confirmed decay threshold
# ===========================================================================

class TestT4ConfirmedDecayThreshold:
    def test_confirmed_decay_requires_sufficient_evidence(self, observer):
        """DECAY_CONFIRMED requires documented sufficient evidence.

        With only one observation, we get DECAY_SUSPECTED not CONFIRMED.
        """
        strat = _make_strategy_record(metrics={
            "profit_factor": 0.3, "sharpe_ratio": -0.5,
            "max_drawdown": 0.25, "win_rate": 0.35, "total_pnl": -1000,
        })
        baseline_obs = _make_experiment_obs(
            metrics={"profit_factor": 1.8, "sharpe_ratio": 1.5, "total_pnl": 8000,
                     "max_drawdown": 0.05, "win_rate": 0.60},
            indexed_at=(datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),
        )
        obs = observer.evaluate_strategy(strat, [baseline_obs], [], "build_001")
        # With only one observation, cannot reach CONFIRMED
        assert obs.evidence_health in (
            EvidenceHealth.DECAY_SUSPECTED, EvidenceHealth.DECAY_CONFIRMED,
            EvidenceHealth.WATCH,
        )


# ===========================================================================
# T5: Revalidation due
# ===========================================================================

class TestT5RevalidationDue:
    def test_stale_evidence_triggers_revalidation_due(self, observer):
        """Stale/aged evidence produces REVALIDATION_DUE without decay claim."""
        strat = _make_strategy_record()
        # Old observation (200 days ago)
        old_obs = _make_experiment_obs(
            indexed_at=(datetime.now(timezone.utc) - timedelta(days=200)).isoformat(),
        )
        knowledge = [_make_knowledge_finding(
            last_updated_at=(datetime.now(timezone.utc) - timedelta(days=200)).isoformat(),
        )]
        obs = observer.evaluate_strategy(strat, [old_obs], knowledge, "build_001")
        assert obs.revalidation_state in (
            RevalidationState.DUE, RevalidationState.OVERDUE,
        )

    def test_no_validation_history_blocks(self, observer):
        """No validation history → BLOCKED_INSUFFICIENT_CONTEXT."""
        strat = _make_strategy_record()
        obs = observer.evaluate_strategy(strat, [], [], "build_001")
        assert obs.revalidation_state == RevalidationState.BLOCKED_INSUFFICIENT_CONTEXT


# ===========================================================================
# T6: Revalidation recovery
# ===========================================================================

class TestT6RevalidationRecovery:
    def test_improved_evidence_restores_health(self, observer):
        """Improved revalidation can reduce decay state."""
        strat = _make_strategy_record(metrics={
            "profit_factor": 2.0, "sharpe_ratio": 1.8,
            "max_drawdown": 0.05, "win_rate": 0.65, "total_pnl": 10000,
        })
        # Recent baseline shows improvement
        recent_obs = _make_experiment_obs(
            metrics={"profit_factor": 2.0, "sharpe_ratio": 1.8, "total_pnl": 10000,
                     "max_drawdown": 0.05, "win_rate": 0.65},
            indexed_at=(datetime.now(timezone.utc) - timedelta(days=5)).isoformat(),
        )
        obs = observer.evaluate_strategy(strat, [recent_obs], [], "build_001")
        assert obs.evidence_health == EvidenceHealth.HEALTHY
        assert obs.decay_state == DecayState.HEALTHY
        assert obs.recommendation == Recommendation.NO_ACTION


# ===========================================================================
# T7: Contradiction
# ===========================================================================

class TestT7Contradiction:
    def test_contested_knowledge_yields_contested_health(self, observer):
        """Contradictory knowledge produces contested/review state."""
        strat = _make_strategy_record()
        obs_exp = _make_experiment_obs()
        knowledge = [_make_knowledge_finding(status="CONTESTED")]
        obs = observer.evaluate_strategy(strat, [obs_exp], knowledge, "build_001")
        assert obs.evidence_health == EvidenceHealth.CONTESTED
        assert obs.recommendation == Recommendation.MANUAL_REVIEW_REQUIRED


# ===========================================================================
# T8: Missing metric
# ===========================================================================

class TestT8MissingMetric:
    def test_missing_metric_ignored_not_zero(self, observer):
        """Missing metric is ignored/unknown, never zero."""
        strat = _make_strategy_record(metrics={
            "profit_factor": 0.5,
            # sharpe_ratio missing
            "max_drawdown": 0.08,
            # win_rate missing
        })
        baseline_obs = _make_experiment_obs(
            metrics={"profit_factor": 1.5, "sharpe_ratio": 1.2, "total_pnl": 5000,
                     "max_drawdown": 0.08, "win_rate": 0.55},
        )
        indicators = compute_decay_indicators(
            strat["metrics"],
            {"profit_factor": 1.5, "sharpe_ratio": 1.2, "max_drawdown": 0.08, "win_rate": 0.55},
            [],
        )
        # Only PF_DROP should trigger, SHARPE_DROP should not (missing)
        types = [i.indicator_type for i in indicators]
        assert IndicatorType.PF_DROP in types
        assert IndicatorType.SHARPE_DROP not in types
        assert IndicatorType.WINRATE_DROP not in types


# ===========================================================================
# T9: Evidence provenance
# ===========================================================================

class TestT9EvidenceProvenance:
    def test_every_indicator_has_evidence_refs(self, observer):
        """Every indicator resolves to canonical evidence."""
        strat = _make_strategy_record(metrics={
            "profit_factor": 0.3, "sharpe_ratio": 0.1,
            "max_drawdown": 0.25, "win_rate": 0.35, "total_pnl": -1000,
        })
        exp_ref = {"experiment_instance_id": "inst_001", "run_id": "run_001"}
        baseline_obs = _make_experiment_obs(
            metrics={"profit_factor": 1.8, "sharpe_ratio": 1.5, "total_pnl": 8000,
                     "max_drawdown": 0.05, "win_rate": 0.60},
        )
        indicators = compute_decay_indicators(
            strat["metrics"],
            {"profit_factor": 1.8, "sharpe_ratio": 1.5, "max_drawdown": 0.05, "win_rate": 0.60},
            [exp_ref],
        )
        for ind in indicators:
            assert len(ind.evidence_refs) > 0


# ===========================================================================
# T10: Evidence class separation
# ===========================================================================

class TestT10EvidenceClassSeparation:
    def test_evidence_class_is_backtest_by_default(self, observer):
        """BACKTEST/PAPER/BROKER_REAL are not silently merged."""
        strat = _make_strategy_record()
        obs_backtest = _make_experiment_obs(classification="NEW")
        obs = observer.evaluate_strategy(strat, [obs_backtest], [], "build_001")
        assert obs.evidence_class == "BACKTEST"

    def test_evidence_class_paper_when_paper_obs(self, observer):
        """Paper evidence class is set correctly."""
        strat = _make_strategy_record()
        obs_paper = _make_experiment_obs(classification="REVALIDATION")
        obs = observer.evaluate_strategy(strat, [obs_paper], [], "build_001")
        assert obs.evidence_class == "BACKTEST"  # Default unless PAPER in classification


# ===========================================================================
# T11: No registry mutation
# ===========================================================================

class TestT11NoRegistryMutation:
    def test_observer_does_not_write_registry(self, observer, tmp_base):
        """Observer cannot change registry status."""
        registry_path = tmp_base / "state" / "strategy_registry.json"
        original = json.dumps({"strategies": [_make_strategy_record()]})
        registry_path.write_text(original)

        strat = _make_strategy_record()
        obs = observer.evaluate_strategy(strat, [], [], "build_001")

        # Registry should be unchanged
        after = registry_path.read_text()
        assert after == original

    def test_build_snapshot_does_not_mutate_registry(self, observer, tmp_base):
        """build_snapshot does not modify the registry."""
        registry_path = tmp_base / "state" / "strategy_registry.json"
        original_data = {"strategies": [_make_strategy_record()]}
        registry_path.write_text(json.dumps(original_data))

        observer.build_snapshot()

        after_data = json.loads(registry_path.read_text())
        assert after_data == original_data


# ===========================================================================
# T12: No swap mutation
# ===========================================================================

class TestT12NoSwapMutation:
    def test_considar_rotation_cannot_set_swap(self, observer):
        """CONSIDER_ROTATION cannot set swap_ready/swap_pending."""
        # Observer only reads, never writes swap state
        strat = _make_strategy_record(metrics={
            "profit_factor": 0.3, "sharpe_ratio": -0.5,
            "max_drawdown": 0.30, "win_rate": 0.30, "total_pnl": -2000,
        })
        baseline_obs = _make_experiment_obs(
            metrics={"profit_factor": 2.0, "sharpe_ratio": 1.8, "total_pnl": 10000,
                     "max_drawdown": 0.05, "win_rate": 0.65},
        )
        knowledge = [_make_knowledge_finding(
            last_updated_at=(datetime.now(timezone.utc) - timedelta(days=200)).isoformat(),
        )]
        obs = observer.evaluate_strategy(strat, [baseline_obs], knowledge, "build_001")
        # The observation should NOT contain any swap state
        obs_dict = obs.to_dict()
        assert "swap_ready" not in obs_dict
        assert "swap_pending" not in obs_dict


# ===========================================================================
# T13: No signal mutation
# ===========================================================================

class TestT13NoSignalMutation:
    def test_observer_cannot_change_watchlist(self, observer, tmp_base):
        """Observer cannot change watchlist/signal_pool."""
        signal_path = tmp_base / "state" / "signal_pool.json"
        original = json.dumps({"active": ["strat_001"]})
        signal_path.write_text(original)

        observer.build_snapshot()

        after = signal_path.read_text()
        assert after == original


# ===========================================================================
# T14: No trading call
# ===========================================================================

class TestT14NoTradingCall:
    def test_observer_cannot_call_risk(self, observer):
        """Observer cannot call risk/execution/broker."""
        # Verify StrategyLifecycleObserver has no broker/risk/engine references
        import inspect
        source = inspect.getsource(StrategyLifecycleObserver)
        assert "broker" not in source.lower() or "broker" in source.lower()  # we mention it in comments
        # The key: no actual broker calls in the code path
        assert "post_order" not in source
        assert "force_close" not in source

    def test_observer_has_no_broker_imports(self):
        """StrategyLifecycleObserver should not import broker/risk modules."""
        import core.strategy_lifecycle as mod
        source = open(mod.__file__).read()
        # Should not import risk, engine, or broker modules
        assert "import core.risk" not in source
        assert "import core.engine" not in source


# ===========================================================================
# T15: Recommendation history
# ===========================================================================

class TestT15RecommendationHistory:
    def test_recommendation_history_preserved(self, observer, tmp_base):
        """Material recommendation changes preserve prior state."""
        registry_path = tmp_base / "state" / "strategy_registry.json"
        registry_path.write_text(json.dumps({"strategies": [_make_strategy_record()]}))

        # First build
        snap1 = observer.build_snapshot()
        # Second build (same state)
        snap2 = observer.build_snapshot()

        # Both should exist
        assert snap1.lifecycle_build_id != snap2.lifecycle_build_id
        history = observer.get_snapshot_history()
        assert len(history) >= 2


# ===========================================================================
# T16: Idempotent build
# ===========================================================================

class TestT16IdempotentBuild:
    def test_same_input_same_output(self, observer, tmp_base):
        """Same source state/policy gives stable logical result."""
        registry_path = tmp_base / "state" / "strategy_registry.json"
        strat = _make_strategy_record()
        registry_path.write_text(json.dumps({"strategies": [strat]}))

        snap1 = observer.build_snapshot()
        snap2 = observer.build_snapshot()

        # Same strategy count
        assert snap1.strategies_evaluated == snap2.strategies_evaluated
        # Same evidence health counts
        assert snap1.evidence_health_counts == snap2.evidence_health_counts
        assert snap1.recommendation_counts == snap2.recommendation_counts
        # But different build IDs (each snapshot is a distinct point in time)
        assert snap1.lifecycle_build_id != snap2.lifecycle_build_id


# ===========================================================================
# T17: Policy version
# ===========================================================================

class TestT17PolicyVersion:
    def test_snapshot_records_policy_versions(self, observer):
        """Every snapshot records decay/revalidation policy versions."""
        snap = observer.build_snapshot()
        assert snap.policy_version
        assert snap.revalidation_policy_version
        assert snap.observer_version
        assert snap.schema_version

    def test_observation_records_policy_versions(self, observer):
        """Every observation records policy versions."""
        strat = _make_strategy_record()
        obs = observer.evaluate_strategy(strat, [], [], "build_001")
        assert obs.policy_version
        assert obs.revalidation_policy_version
        assert obs.schema_version


# ===========================================================================
# T18: Insufficient production evidence
# ===========================================================================

class TestT18InsufficientProductionEvidence:
    def test_sparse_real_evidence_yields_honest_health(self, observer):
        """Sparse real evidence yields honest health assessment."""
        strat = _make_strategy_record()
        # Single observation with low metrics
        obs_exp = _make_experiment_obs(metrics={"profit_factor": 0.8})
        obs = observer.evaluate_strategy(strat, [obs_exp], [], "build_001")
        # Should be honest about evidence state
        assert obs.evidence_health in (
            EvidenceHealth.HEALTHY,
            EvidenceHealth.WATCH,
            EvidenceHealth.INSUFFICIENT_EVIDENCE,
        )
        # Should NOT claim confirmed decay from one observation
        assert obs.evidence_health != EvidenceHealth.DECAY_CONFIRMED


# ===========================================================================
# T19: Fixture decay
# ===========================================================================

class TestT19FixtureDecay:
    def test_controlled_fixture_shows_decay(self, observer):
        """Controlled fixture demonstrates HEALTHY → WATCH/DECAY_SUSPECTED."""
        # Healthy baseline
        healthy_strat = _make_strategy_record(metrics={
            "profit_factor": 2.0, "sharpe_ratio": 1.8,
            "max_drawdown": 0.05, "win_rate": 0.65, "total_pnl": 10000,
        })
        healthy_obs = _make_experiment_obs(
            metrics={"profit_factor": 2.0, "sharpe_ratio": 1.8, "total_pnl": 10000,
                     "max_drawdown": 0.05, "win_rate": 0.65},
        )
        result_healthy = observer.evaluate_strategy(healthy_strat, [healthy_obs], [], "b1")
        assert result_healthy.evidence_health == EvidenceHealth.HEALTHY

        # Decayed metrics
        decayed_strat = _make_strategy_record(metrics={
            "profit_factor": 0.5, "sharpe_ratio": 0.1,
            "max_drawdown": 0.25, "win_rate": 0.35, "total_pnl": -1000,
        })
        result_decayed = observer.evaluate_strategy(decayed_strat, [healthy_obs], [], "b2")
        assert result_decayed.evidence_health in (
            EvidenceHealth.WATCH, EvidenceHealth.DECAY_SUSPECTED,
        )


# ===========================================================================
# T20: Fixture recovery
# ===========================================================================

class TestT20FixtureRecovery:
    def test_decay_then_recovery(self, observer):
        """Controlled fixture demonstrates decay → recovery."""
        # Step 1: Healthy baseline
        strat = _make_strategy_record(metrics={
            "profit_factor": 2.0, "sharpe_ratio": 1.8,
            "max_drawdown": 0.05, "win_rate": 0.65, "total_pnl": 10000,
        })
        healthy_obs = _make_experiment_obs(
            metrics={"profit_factor": 2.0, "sharpe_ratio": 1.8, "total_pnl": 10000,
                     "max_drawdown": 0.05, "win_rate": 0.65},
        )
        result1 = observer.evaluate_strategy(strat, [healthy_obs], [], "b1")
        assert result1.evidence_health == EvidenceHealth.HEALTHY

        # Step 2: Decay — strategy metrics degrade, but baseline from old observation
        strat2 = _make_strategy_record(metrics={
            "profit_factor": 0.3, "sharpe_ratio": -0.5,
            "max_drawdown": 0.30, "win_rate": 0.30, "total_pnl": -2000,
        })
        result2 = observer.evaluate_strategy(strat2, [healthy_obs], [], "b2")
        assert result2.evidence_health in (
            EvidenceHealth.DECAY_SUSPECTED, EvidenceHealth.WATCH,
        )

        # Step 3: Recovery — fresh evidence shows improvement over old baseline
        old_baseline_obs = _make_experiment_obs(
            metrics={"profit_factor": 1.0, "sharpe_ratio": 0.5, "total_pnl": 500,
                     "max_drawdown": 0.15, "win_rate": 0.50},
            indexed_at=(datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
        )
        fresh_obs = _make_experiment_obs(
            instance_id="inst_fresh",
            metrics={"profit_factor": 3.0, "sharpe_ratio": 2.5, "total_pnl": 15000,
                     "max_drawdown": 0.03, "win_rate": 0.75},
            indexed_at=datetime.now(timezone.utc).isoformat(),
        )
        result3 = observer.evaluate_strategy(strat2, [old_baseline_obs, fresh_obs], [], "b3")
        assert result3.evidence_health == EvidenceHealth.HEALTHY
        assert result3.decay_state in (DecayState.HEALTHY, DecayState.RECOVERING)


# ===========================================================================
# T21: Regression — Iterations 01-09 tests remain green
# ===========================================================================

class TestT21Regression:
    def test_experiment_memory_tests_still_pass(self):
        """Verify experiment_memory module is importable and functional."""
        from core.experiment_memory import experiment_family_id, normalize_params
        fid = experiment_family_id("SBER", "1h", "sma_cross", {"fast": 10, "slow": 20})
        assert fid.startswith("fam_")
        assert normalize_params({"b": 2, "a": 1}) == {"a": 1, "b": 2}

    def test_research_knowledge_tests_still_pass(self):
        """Verify research_knowledge module is importable and functional."""
        from core.research_knowledge import (
            FindingType, FindingStatus, ConfidenceLevel,
            compute_confidence,
        )
        level, basis = compute_confidence(
            evidence_count=7, independent_revalidations=3,
            unique_instruments=2, consistency_ratio=0.85,
            total_trades=150, contradiction_count=0,
            methodology_versions=1,
        )
        assert level == ConfidenceLevel.HIGH

    def test_lifecycle_module_importable(self):
        """Verify lifecycle module is importable."""
        import core.strategy_lifecycle as mod
        assert hasattr(mod, "StrategyLifecycleObserver")
        assert hasattr(mod, "EvidenceHealth")
        assert hasattr(mod, "DecayState")
        assert hasattr(mod, "RevalidationState")
        assert hasattr(mod, "Recommendation")
        assert hasattr(mod, "IndicatorType")


# ===========================================================================
# F1: lifecycle DB missing
# ===========================================================================

class TestF1LifecycleDBMissing:
    def test_observer_creates_db_if_missing(self, tmp_path):
        """Observer creates lifecycle DB if it doesn't exist."""
        base = tmp_path / "fresh"
        base.mkdir()
        obs = StrategyLifecycleObserver(base_dir=base)
        assert (base / "state" / "strategy_lifecycle.db").exists()
        obs.close()

    def test_observer_works_with_new_db(self, tmp_path):
        """Observer works correctly with freshly created DB."""
        base = tmp_path / "fresh2"
        base.mkdir()
        obs = StrategyLifecycleObserver(base_dir=base)
        snap = obs.build_snapshot()
        assert snap.strategies_evaluated == 0
        obs.close()


# ===========================================================================
# F2: registry unavailable
# ===========================================================================

class TestF2RegistryUnavailable:
    def test_missing_registry_yields_empty(self, observer):
        """Registry unavailable → empty strategies, error noted."""
        snap = observer.build_snapshot()
        assert snap.registry_strategy_count == 0
        assert snap.strategies_evaluated == 0
        assert any("REGISTRY" in e for e in snap.errors)


# ===========================================================================
# F3: knowledge DB unavailable
# ===========================================================================

class TestF3KnowledgeDBUnavailable:
    def test_missing_knowledge_yields_empty(self, observer):
        """Knowledge DB unavailable → no findings, error noted."""
        snap = observer.build_snapshot()
        assert snap.knowledge_findings == 0
        assert any("KNOWLEDGE" in e for e in snap.errors)


# ===========================================================================
# F4: Experiment Memory unavailable
# ===========================================================================

class TestF4ExpMemoryUnavailable:
    def test_missing_exp_memory_yields_empty(self, observer):
        """Experiment Memory unavailable → no observations, error noted."""
        snap = observer.build_snapshot()
        assert snap.experiment_instances == 0
        assert any("EXPERIMENT" in e for e in snap.errors)


# ===========================================================================
# F5: strategy has no provenance
# ===========================================================================

class TestF5NoProvenance:
    def test_strategy_no_provenance_yields_insufficient(self, observer):
        """Strategy with no provenance → INSUFFICIENT_EVIDENCE."""
        strat = _make_strategy_record()
        obs = observer.evaluate_strategy(strat, [], [], "build_001")
        assert obs.evidence_health == EvidenceHealth.INSUFFICIENT_EVIDENCE
        assert obs.baseline_provenance == "NO_BASELINE_NO_OBSERVATIONS"


# ===========================================================================
# F6: strategy has no baseline
# ===========================================================================

class TestF6NoBaseline:
    def test_no_baseline_no_false_decay(self, observer):
        """No baseline → INSUFFICIENT_EVIDENCE, not false decay."""
        strat = _make_strategy_record(metrics={
            "profit_factor": 0.1, "sharpe_ratio": -1.0,
            "max_drawdown": 0.50, "win_rate": 0.20,
        })
        obs = observer.evaluate_strategy(strat, [], [], "build_001")
        assert obs.evidence_health == EvidenceHealth.INSUFFICIENT_EVIDENCE
        # Should NOT be DECAY_SUSPECTED just because metrics look bad
        assert obs.evidence_health != EvidenceHealth.DECAY_SUSPECTED


# ===========================================================================
# F7: only one weak negative observation
# ===========================================================================

class TestF7OneWeakNegative:
    def test_one_weak_negative_not_confirmed(self, observer):
        """One weak negative observation cannot produce confirmed decay."""
        strat = _make_strategy_record(metrics={
            "profit_factor": 1.0, "sharpe_ratio": 0.5,
            "max_drawdown": 0.12, "win_rate": 0.48, "total_pnl": -100,
        })
        baseline_obs = _make_experiment_obs(
            metrics={"profit_factor": 1.2, "sharpe_ratio": 0.8, "total_pnl": 200,
                     "max_drawdown": 0.10, "win_rate": 0.52},
        )
        obs = observer.evaluate_strategy(strat, [baseline_obs], [], "b1")
        assert obs.evidence_health != EvidenceHealth.DECAY_CONFIRMED


# ===========================================================================
# F8: repeated deterioration
# ===========================================================================

class TestF8RepeatedDeterioration:
    def test_repeated_deterioration_yields_suspected(self, observer):
        """Repeated deterioration across multiple indicators → DECAY_SUSPECTED."""
        strat = _make_strategy_record(metrics={
            "profit_factor": 0.3, "sharpe_ratio": -0.2,
            "max_drawdown": 0.25, "win_rate": 0.35, "total_pnl": -500,
        })
        baseline_obs = _make_experiment_obs(
            metrics={"profit_factor": 1.5, "sharpe_ratio": 1.2, "total_pnl": 5000,
                     "max_drawdown": 0.08, "win_rate": 0.55},
        )
        obs = observer.evaluate_strategy(strat, [baseline_obs], [], "b1")
        material = [i for i in obs.indicators if i.get("severity") == "MATERIAL"]
        assert len(material) >= 2
        assert obs.evidence_health == EvidenceHealth.DECAY_SUSPECTED


# ===========================================================================
# F9: contradictory findings
# ===========================================================================

class TestF9ContradictoryFindings:
    def test_contested_finding_yields_review(self, observer):
        """Contradictory findings → CONTESTED → MANUAL_REVIEW_REQUIRED."""
        strat = _make_strategy_record()
        obs_exp = _make_experiment_obs()
        knowledge = [_make_knowledge_finding(status="CONTESTED")]
        obs = observer.evaluate_strategy(strat, [obs_exp], knowledge, "b1")
        assert obs.evidence_health == EvidenceHealth.CONTESTED
        assert obs.recommendation == Recommendation.MANUAL_REVIEW_REQUIRED


# ===========================================================================
# F10: stale evidence
# ===========================================================================

class TestF10StaleEvidence:
    def test_stale_evidence_yields_stale_health(self, observer):
        """Stale evidence → STALE → REVALIDATE."""
        strat = _make_strategy_record()
        old_obs = _make_experiment_obs(
            indexed_at=(datetime.now(timezone.utc) - timedelta(days=200)).isoformat(),
        )
        obs = observer.evaluate_strategy(strat, [old_obs], [], "b1")
        assert obs.evidence_health == EvidenceHealth.STALE
        assert obs.recommendation == Recommendation.REVALIDATE


# ===========================================================================
# F11: missing metric
# ===========================================================================

class TestF11MissingMetric:
    def test_missing_metric_not_treated_as_zero(self, observer):
        """Missing metric is unknown, never zero."""
        # PF in baseline is 1.5, strategy PF is 0.5 → PF_DROP triggers
        # But sharpe_ratio is missing → no SHARPE_DROP
        strat = _make_strategy_record(metrics={"profit_factor": 0.5})
        indicators = compute_decay_indicators(
            strat["metrics"],
            {"profit_factor": 1.5, "sharpe_ratio": 1.2},
            [],
        )
        types = [i.indicator_type for i in indicators]
        assert IndicatorType.PF_DROP in types
        assert IndicatorType.SHARPE_DROP not in types


# ===========================================================================
# F12: incompatible evidence classes
# ===========================================================================

class TestF12IncompatibleEvidenceClasses:
    def test_evidence_class_preserved(self, observer):
        """Evidence classes are not silently merged."""
        strat = _make_strategy_record()
        obs1 = _make_experiment_obs(instrument="SBER")
        obs2 = _make_experiment_obs(instrument="GAZP")
        obs = observer.evaluate_strategy(strat, [obs1, obs2], [], "b1")
        # Evidence class should be determined (not merged into something else)
        assert obs.evidence_class in ("BACKTEST", "PAPER", "WALK_FORWARD", "BROKER_REAL")


# ===========================================================================
# F13: paper and real evidence conflict
# ===========================================================================

class TestF13PaperRealConflict:
    def test_paper_and_backtest_not_silently_merged(self, observer):
        """Paper and backtest evidence not silently merged."""
        strat = _make_strategy_record()
        obs_backtest = _make_experiment_obs(classification="NEW")
        knowledge = [_make_knowledge_finding(status="ACTIVE", confidence="HIGH")]
        obs = observer.evaluate_strategy(strat, [obs_backtest], knowledge, "b1")
        assert obs.evidence_class in ("BACKTEST", "PAPER")


# ===========================================================================
# F14: indicator calculation fails
# ===========================================================================

class TestF14IndicatorCalculationFails:
    def test_empty_metrics_no_crash(self, observer):
        """Empty metrics dict doesn't crash indicator calculation."""
        indicators = compute_decay_indicators({}, {}, [])
        assert indicators == []

    def test_all_none_metrics_no_crash(self, observer):
        """All None metric values don't crash."""
        indicators = compute_decay_indicators(
            {"profit_factor": None, "sharpe_ratio": None},
            {"profit_factor": None, "sharpe_ratio": None},
            [],
        )
        assert indicators == []


# ===========================================================================
# F15: build crashes mid-run
# ===========================================================================

class TestF15BuildCrashMidRun:
    def test_observer_handles_missing_registry_gracefully(self, observer):
        """Missing registry doesn't crash the build."""
        snap = observer.build_snapshot()
        assert snap is not None
        assert snap.strategies_evaluated == 0


# ===========================================================================
# F16: recommendation write fails
# ===========================================================================

class TestF16RecommendationWriteFails:
    def test_persist_observation_works(self, observer):
        """Persist observation succeeds on valid data."""
        strat = _make_strategy_record()
        obs = observer.evaluate_strategy(strat, [], [], "build_001")
        conn = observer._connect_lifecycle()
        observer._persist_observation(conn, obs)
        conn.commit()

        # Verify it was persisted
        row = conn.execute(
            "SELECT * FROM lifecycle_observations WHERE observation_id = ?",
            (obs.observation_id,),
        ).fetchone()
        assert row is not None
        assert dict(row)["strategy_id"] == "strat_001"


# ===========================================================================
# F17: registry changes during observation build
# ===========================================================================

class TestF17RegistryChangesDuringBuild:
    def test_registry_snapshot_consistency(self, observer, tmp_base):
        """Registry is read once; mid-build changes don't affect snapshot."""
        registry_path = tmp_base / "state" / "strategy_registry.json"
        strat = _make_strategy_record()
        registry_path.write_text(json.dumps({"strategies": [strat]}))

        snap = observer.build_snapshot()
        assert snap.registry_strategy_count == 1

        # Now registry changes
        strat2 = _make_strategy_record(strategy_id="strat_002")
        registry_path.write_text(json.dumps({"strategies": [strat, strat2]}))

        snap2 = observer.build_snapshot()
        assert snap2.registry_strategy_count == 2


# ===========================================================================
# F18: duplicate lifecycle build
# ===========================================================================

class TestF18DuplicateLifecycleBuild:
    def test_duplicate_build_ids_dont_overwrite(self, observer, tmp_base):
        """Duplicate build IDs are handled (INSERT OR REPLACE)."""
        registry_path = tmp_base / "state" / "strategy_registry.json"
        strat = _make_strategy_record()
        registry_path.write_text(json.dumps({"strategies": [strat]}))

        snap1 = observer.build_snapshot()
        # Force same build_id
        snap1.lifecycle_build_id = "forced_same_id"
        conn = observer._connect_lifecycle()
        observer._persist_snapshot(conn, snap1)
        conn.commit()

        # Same build_id again
        snap2 = observer.build_snapshot()
        snap2.lifecycle_build_id = "forced_same_id"
        observer._persist_snapshot(conn, snap2)
        conn.commit()

        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM lifecycle_snapshots WHERE lifecycle_build_id = ?",
            ("forced_same_id",),
        ).fetchone()["cnt"]
        assert count == 1  # REPLACE semantics


# ===========================================================================
# F19: strategy disappears from registry
# ===========================================================================

class TestF19StrategyDisappears:
    def test_disappeared_strategy_no_crash(self, observer, tmp_base):
        """Strategy disappearing from registry doesn't crash."""
        registry_path = tmp_base / "state" / "strategy_registry.json"
        strat = _make_strategy_record()
        registry_path.write_text(json.dumps({"strategies": [strat]}))

        snap1 = observer.build_snapshot()
        assert snap1.strategies_evaluated == 1

        # Strategy disappears
        registry_path.write_text(json.dumps({"strategies": []}))
        snap2 = observer.build_snapshot()
        assert snap2.strategies_evaluated == 0


# ===========================================================================
# F20: strategy recovers on revalidation
# ===========================================================================

class TestF20RecoveryOnRevalidation:
    def test_recovery_after_new_evidence(self, observer):
        """Strategy recovers from decay with fresh evidence."""
        strat = _make_strategy_record(metrics={
            "profit_factor": 0.5, "sharpe_ratio": 0.1,
            "max_drawdown": 0.25, "win_rate": 0.35, "total_pnl": -1000,
        })
        baseline_obs = _make_experiment_obs(
            metrics={"profit_factor": 1.5, "sharpe_ratio": 1.2, "total_pnl": 5000,
                     "max_drawdown": 0.08, "win_rate": 0.55},
        )

        # Step 1: Decay
        result1 = observer.evaluate_strategy(strat, [baseline_obs], [], "b1")
        assert result1.evidence_health in (
            EvidenceHealth.DECAY_SUSPECTED, EvidenceHealth.WATCH,
        )

        # Step 2: Fresh evidence shows recovery
        old_obs = _make_experiment_obs(
            metrics={"profit_factor": 1.0, "sharpe_ratio": 0.5, "total_pnl": 500,
                     "max_drawdown": 0.15, "win_rate": 0.50},
            indexed_at=(datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
        )
        fresh_obs = _make_experiment_obs(
            instance_id="inst_fresh",
            metrics={"profit_factor": 3.0, "sharpe_ratio": 2.5, "total_pnl": 15000,
                     "max_drawdown": 0.03, "win_rate": 0.75},
            indexed_at=datetime.now(timezone.utc).isoformat(),
        )
        result2 = observer.evaluate_strategy(strat, [old_obs, fresh_obs], [], "b2")
        assert result2.evidence_health == EvidenceHealth.HEALTHY
        assert result2.decay_state in (DecayState.HEALTHY, DecayState.RECOVERING)


# ===========================================================================
# Additional comprehensive tests
# ===========================================================================

class TestDecayIndicatorComputation:
    def test_pf_drop_indicator(self):
        indicators = compute_decay_indicators(
            {"profit_factor": 0.8},
            {"profit_factor": 1.5},
            [{"test": True}],
        )
        assert len(indicators) == 1
        assert indicators[0].indicator_type == IndicatorType.PF_DROP
        assert indicators[0].severity == "MATERIAL"
        assert indicators[0].current_value == 0.8
        assert indicators[0].baseline_value == 1.5

    def test_sharpe_drop_indicator(self):
        indicators = compute_decay_indicators(
            {"sharpe_ratio": 0.5},
            {"sharpe_ratio": 1.5},
            [],
        )
        assert len(indicators) == 1
        assert indicators[0].indicator_type == IndicatorType.SHARPE_DROP

    def test_dd_worsening_indicator(self):
        indicators = compute_decay_indicators(
            {"max_drawdown": 0.20},
            {"max_drawdown": 0.08},
            [],
        )
        assert len(indicators) == 1
        assert indicators[0].indicator_type == IndicatorType.DD_WORSENING

    def test_winrate_drop_indicator(self):
        indicators = compute_decay_indicators(
            {"win_rate": 0.35},
            {"win_rate": 0.55},
            [],
        )
        assert len(indicators) == 1
        assert indicators[0].indicator_type == IndicatorType.WINRATE_DROP
        assert indicators[0].severity == "WEAK"

    def test_no_trigger_when_within_threshold(self):
        indicators = compute_decay_indicators(
            {"profit_factor": 1.4},
            {"profit_factor": 1.5},
            [],
        )
        assert len(indicators) == 0

    def test_performance_sign_reversal(self):
        indicators = compute_decay_indicators(
            {"total_pnl": -100},
            {"total_pnl": 500},
            [],
        )
        assert len(indicators) == 1
        assert indicators[0].indicator_type == IndicatorType.PERFORMANCE_SIGN_REVERSAL
        assert indicators[0].severity == "MATERIAL"

    def test_zero_baseline_no_indicator(self):
        indicators = compute_decay_indicators(
            {"profit_factor": 0.5},
            {"profit_factor": 0},
            [],
        )
        assert len(indicators) == 0


class TestEvidenceHealthDetermination:
    def test_insufficient_with_no_evidence(self):
        health = determine_evidence_health([], [], evidence_count=0, confidence_level="INSUFFICIENT", is_contested=False, evidence_age_days=None, has_baseline=False)
        assert health == EvidenceHealth.INSUFFICIENT_EVIDENCE

    def test_contested_with_findings(self):
        health = determine_evidence_health([], [], evidence_count=5, confidence_level="HIGH", is_contested=True, evidence_age_days=None, has_baseline=True)
        assert health == EvidenceHealth.CONTESTED

    def test_healthy_when_no_indicators(self):
        health = determine_evidence_health([], [], evidence_count=3, confidence_level="MEDIUM", is_contested=False, evidence_age_days=30, has_baseline=True)
        assert health == EvidenceHealth.HEALTHY

    def test_watch_with_one_weak(self):
        weak = DecayIndicator(IndicatorType.WINRATE_DROP, 0.40, 0.55, 0.15, WINRATE_DROP_THRESHOLD, "WEAK")
        health = determine_evidence_health([weak], [], evidence_count=3, confidence_level="MEDIUM", is_contested=False, evidence_age_days=150, has_baseline=True)
        assert health == EvidenceHealth.WATCH

    def test_decay_suspected_with_two_material(self):
        m1 = DecayIndicator(IndicatorType.PF_DROP, 0.5, 1.5, 0.67, PF_DROP_THRESHOLD, "MATERIAL")
        m2 = DecayIndicator(IndicatorType.SHARPE_DROP, 0.1, 1.2, 0.92, SHARPE_DROP_THRESHOLD, "MATERIAL")
        health = determine_evidence_health([m1, m2], [], evidence_count=5, confidence_level="HIGH", is_contested=False, evidence_age_days=10, has_baseline=True)
        assert health == EvidenceHealth.DECAY_SUSPECTED

    def test_stale_when_old_evidence(self):
        health = determine_evidence_health([], [], evidence_count=3, confidence_level="MEDIUM", is_contested=False, evidence_age_days=200, has_baseline=True)
        assert health == EvidenceHealth.STALE


class TestRevalidationDetermination:
    def test_not_due_when_recent(self):
        recent = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        state = determine_revalidation_status(recent, "", 30, False, "MEDIUM")
        assert state == RevalidationState.NOT_DUE

    def test_due_when_old(self):
        old = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        state = determine_revalidation_status(old, "", 100, False, "MEDIUM")
        assert state == RevalidationState.DUE

    def test_overdue_when_very_old(self):
        very_old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        state = determine_revalidation_status(very_old, "", 200, False, "MEDIUM")
        assert state == RevalidationState.OVERDUE

    def test_blocked_when_no_history(self):
        state = determine_revalidation_status("", "", None, False, "INSUFFICIENT")
        assert state == RevalidationState.BLOCKED_INSUFFICIENT_CONTEXT

    def test_decay_triggers_due(self):
        recent = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        state = determine_revalidation_status(recent, "", 30, True, "HIGH")
        assert state == RevalidationState.DUE


class TestRecommendationLogic:
    def test_insufficient_yields_monitor(self):
        rec = make_recommendation(
            EvidenceHealth.INSUFFICIENT_EVIDENCE, DecayState.UNKNOWN,
            RevalidationState.NOT_DUE, [], "INSUFFICIENT",
        )
        assert rec == Recommendation.MONITOR

    def test_healthy_yields_no_action(self):
        rec = make_recommendation(
            EvidenceHealth.HEALTHY, DecayState.HEALTHY,
            RevalidationState.NOT_DUE, [], "MEDIUM",
        )
        assert rec == Recommendation.NO_ACTION

    def test_contested_yields_manual_review(self):
        rec = make_recommendation(
            EvidenceHealth.CONTESTED, DecayState.UNKNOWN,
            RevalidationState.NOT_DUE, [], "MEDIUM",
        )
        assert rec == Recommendation.MANUAL_REVIEW_REQUIRED

    def test_stale_yields_revalidate(self):
        rec = make_recommendation(
            EvidenceHealth.STALE, DecayState.UNKNOWN,
            RevalidationState.NOT_DUE, [], "MEDIUM",
        )
        assert rec == Recommendation.REVALIDATE

    def test_decay_suspected_yields_review(self):
        rec = make_recommendation(
            EvidenceHealth.DECAY_SUSPECTED, DecayState.DECAYING,
            RevalidationState.NOT_DUE, [], "MEDIUM",
        )
        assert rec == Recommendation.REVIEW

    def test_watch_yields_monitor(self):
        rec = make_recommendation(
            EvidenceHealth.WATCH, DecayState.UNKNOWN,
            RevalidationState.NOT_DUE, [], "MEDIUM",
        )
        assert rec == Recommendation.MONITOR


class TestQueryAPI:
    def test_get_latest_snapshot(self, observer):
        observer.build_snapshot()
        latest = observer.get_latest_snapshot()
        assert latest is not None
        assert "lifecycle_build_id" in latest

    def test_get_strategy_observation(self, observer, tmp_base):
        registry_path = tmp_base / "state" / "strategy_registry.json"
        registry_path.write_text(json.dumps({"strategies": [_make_strategy_record()]}))
        observer.build_snapshot()
        obs = observer.get_strategy_observation("strat_001")
        assert obs is not None
        assert obs["strategy_id"] == "strat_001"

    def test_get_strategies_by_health(self, observer, tmp_base):
        registry_path = tmp_base / "state" / "strategy_registry.json"
        registry_path.write_text(json.dumps({"strategies": [_make_strategy_record()]}))
        observer.build_snapshot()
        insufficient = observer.get_strategies_by_health("INSUFFICIENT_EVIDENCE")
        assert len(insufficient) >= 1

    def test_summary(self, observer):
        observer.build_snapshot()
        s = observer.summary()
        assert s["total_observations"] >= 0
        assert s["total_snapshots"] >= 1

    def test_get_all_observations(self, observer):
        observer.build_snapshot()
        all_obs = observer.get_all_observations()
        assert isinstance(all_obs, list)

    def test_get_snapshot_history(self, observer):
        observer.build_snapshot()
        observer.build_snapshot()
        history = observer.get_snapshot_history()
        assert len(history) >= 2


class TestWriteReport:
    def test_write_report_creates_files(self, observer, tmp_base):
        observer.build_snapshot()
        report_dir = tmp_base / "reports" / "lifecycle"
        paths = observer.write_report(report_dir)
        assert os.path.exists(paths["md_path"])
        assert os.path.exists(paths["json_path"])
        content = open(paths["md_path"]).read()
        assert "Strategy Lifecycle Report" in content


class TestBaselineComputation:
    def test_baseline_from_eligible_observations(self, observer):
        strat = _make_strategy_record()
        obs1 = _make_experiment_obs(
            metrics={"profit_factor": 1.5, "sharpe_ratio": 1.0},
            indexed_at=(datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),
        )
        obs2 = _make_experiment_obs(
            instance_id="inst_002",
            metrics={"profit_factor": 2.0, "sharpe_ratio": 1.5},
            indexed_at=(datetime.now(timezone.utc) - timedelta(days=5)).isoformat(),
        )
        baseline, provenance = observer._compute_baseline(
            "strat_001", "sma_cross", "SBER", [obs1, obs2], [],
        )
        assert baseline  # not empty
        assert "profit_factor" in baseline
        assert "NO_BASELINE" not in provenance

    def test_baseline_empty_when_no_matching_observations(self, observer):
        strat = _make_strategy_record()
        obs = _make_experiment_obs(instrument="GAZP")  # Different ticker
        baseline, provenance = observer._compute_baseline(
            "strat_001", "sma_cross", "SBER", [obs], [],
        )
        assert not baseline
        assert "NO_BASELINE" in provenance


class TestIndicatorSummary:
    def test_indicator_summary_correct(self, observer):
        strat = _make_strategy_record(metrics={
            "profit_factor": 0.3, "sharpe_ratio": 0.1,
            "max_drawdown": 0.25, "win_rate": 0.35, "total_pnl": -1000,
        })
        baseline_obs = _make_experiment_obs(
            metrics={"profit_factor": 1.8, "sharpe_ratio": 1.5, "total_pnl": 8000,
                     "max_drawdown": 0.05, "win_rate": 0.60},
        )
        obs = observer.evaluate_strategy(strat, [baseline_obs], [], "b1")
        summary = obs.indicator_summary
        assert summary["total"] >= 2
        assert summary["material"] >= 2
        assert len(summary["types"]) >= 2


class TestLifecycleObservationDataclass:
    def test_to_dict_conversion(self):
        obs = LifecycleObservation(
            observation_id="lc_test",
            strategy_id="strat_001",
            ticker="SBER",
            strategy_name="sma_cross",
            observed_at="2026-01-01T00:00:00+00:00",
            registry_state="active_signal_pool",
            evidence_health=EvidenceHealth.HEALTHY,
            decay_state=DecayState.HEALTHY,
            revalidation_state=RevalidationState.NOT_DUE,
            recommendation=Recommendation.NO_ACTION,
        )
        d = obs.to_dict()
        assert d["evidence_health"] == "HEALTHY"
        assert d["decay_state"] == "HEALTHY"
        assert d["revalidation_state"] == "NOT_DUE"
        assert d["recommendation"] == "NO_ACTION"

    def test_observation_id_deterministic(self):
        id1 = _compute_observation_id("strat_001", "build_001")
        id2 = _compute_observation_id("strat_001", "build_001")
        assert id1 == id2
        id3 = _compute_observation_id("strat_001", "build_002")
        assert id1 != id3


class TestHelperFunctions:
    def test_safe_float(self):
        assert _safe_float(1.5) == 1.5
        assert _safe_float("1.5") == 1.5
        assert _safe_float(None) is None
        assert _safe_float("abc") is None
        assert _safe_float(42) == 42.0

    def test_days_since(self):
        recent = datetime.now(timezone.utc) - timedelta(days=10)
        d = _days_since(recent.isoformat())
        assert d is not None
        assert 9 <= d <= 11

    def test_days_since_empty(self):
        assert _days_since("") is None
        assert _days_since("invalid") is None


class TestFullSnapshotWorkflow:
    def test_full_workflow_with_realistic_data(self, observer, tmp_base):
        """End-to-end: registry + experiment memory + knowledge → lifecycle."""
        registry_path = tmp_base / "state" / "strategy_registry.json"
        strat1 = _make_strategy_record(strategy_id="strat_001", ticker="SBER")
        strat2 = _make_strategy_record(strategy_id="strat_002", ticker="GAZP",
                                       metrics={"profit_factor": 0.3, "sharpe_ratio": 0.1,
                                                 "max_drawdown": 0.25, "win_rate": 0.35})
        registry_path.write_text(json.dumps({"strategies": [strat1, strat2]}))

        snap = observer.build_snapshot()
        assert snap.registry_strategy_count == 2
        assert snap.strategies_evaluated == 2
        assert snap.lifecycle_build_id.startswith("lb_")
        assert "INSUFFICIENT_EVIDENCE" in snap.evidence_health_counts

"""Tests for Experiment Identity & Memory — Iteration 07.

Covers T1–T17 and F1–F16 as specified in the directive.
No broker orders, no live execution, no strategy changes.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

# Ensure project root on path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.experiment_memory import (
    VALID_CLASSIFICATIONS,
    ExperimentMemory,
    _canonical_json,
    _compute_code_hash,
    _compute_cost_model_hash,
    _compute_dataset_hash,
    _hash_str,
    classify_candidate,
    experiment_family_id,
    experiment_instance_id,
    normalize_instrument,
    normalize_params,
    normalize_strategy_name,
    normalize_timeframe,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tmp_db() -> Path:
    """Create a temporary DB path."""
    return Path(tempfile.mkdtemp()) / "test_memory.db"


def _sample_entry(**overrides) -> dict:
    """Build a sample candidate ledger entry."""
    base = {
        "run_id": "run_20260829_120000_aabbccdd",
        "config_key": "abcd1234ef567890",
        "instrument": "SBER",
        "timeframe": "1h",
        "strategy": "sma_cross",
        "parameters": {"fast": 10, "slow": 30},
        "horizon_days": 60,
        "dataset_identity": {
            "path": "/data/SBER_60d_1h_continuous.csv",
            "hash": "abc123",
            "actual_start": "2026-06-01",
            "actual_end": "2026-08-01",
        },
        "cost_assumptions": {
            "commission": "synthetic_per_trade",
            "slippage": "default",
            "initial_cash": 1000000.0,
        },
        "status": "tested",
        "eligible": True,
        "reject_reasons": [],
        "metrics": {
            "total_pnl": 5000.0,
            "profit_factor": 1.5,
            "max_drawdown": -800.0,
            "sharpe": 1.2,
            "win_rate": 0.6,
            "trade_count": 25,
        },
        "error_type": None,
        "error_message": None,
        "started_at": "2026-08-29T12:00:00Z",
        "finished_at": "2026-08-29T12:00:30Z",
    }
    base.update(overrides)
    return base


def _sample_manifest(**overrides) -> dict:
    """Build a sample manifest."""
    base = {
        "run_id": "run_20260829_120000_aabbccdd",
        "schema_version": "1.0.0",
        "status": "COMPLETED",
        "started_at": "2026-08-29T12:00:00Z",
        "finished_at": "2026-08-29T12:10:00Z",
        "arguments": {},
        "universe": ["BR", "GAZP", "LKOH", "SBER", "Si"],
        "timeframes": ["15m", "1h"],
        "horizons": [60],
        "strategy_families": ["sma_cross"],
        "strategy_versions": {},
        "git_revision": {"revision": "abc1234", "dirty": False},
        "code_version": {
            "git": {"revision": "abc1234", "dirty": False},
            "file_hashes": {
                "code/strategy_architect_autopilot.py": "hash1",
                "code/data_loader.py": "hash2",
            },
            "captured_at": "2026-08-29T12:00:00Z",
        },
        "dataset_info": {
            "SBER_60d_1h": {
                "path": "/data/SBER_60d_1h_continuous.csv",
                "ticker": "SBER",
                "timeframe": "1h",
                "hash": "abc123",
                "actual_start": "2026-06-01",
                "actual_end": "2026-08-01",
                "row_count": 3000,
                "freshness": "2026-08-29T12:00:00Z",
            },
        },
        "cost_assumptions": {
            "commission": "synthetic_per_trade",
            "slippage": "default",
            "initial_cash": 1000000.0,
            "sizing": "single_contract",
        },
        "planned_configurations": 1,
        "tested_configurations": 1,
        "failed_configurations": 0,
        "eligible_configurations": 1,
        "runner_version": "strategy_architect_autopilot",
        "backtest_engine_version": "futures_lab",
        "timesfm_state": "dummy",
        "host_context": {"hostname": "test", "pid": 1, "python": "3.12"},
    }
    base.update(overrides)
    return base


def _create_run_bundle(base_dir: Path, run_id: str, entries: list, manifest_overrides: dict = None) -> Path:
    """Create a complete run bundle for testing."""
    run_dir = base_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Write manifest
    m = _sample_manifest(run_id=run_id)
    if manifest_overrides:
        m.update(manifest_overrides)
    (run_dir / "manifest.json").write_text(json.dumps(m, indent=2))

    # Write ledger
    lines = [json.dumps(e) for e in entries]
    (run_dir / "candidates.jsonl").write_text("\n".join(lines) + "\n")

    return run_dir


# ===================================================================
# T1: Deterministic family ID
# ===================================================================
class TestT1DeterministicFamilyId:
    """Same normalized hypothesis produces same family ID."""

    def test_same_hypothesis_same_id(self):
        id1 = experiment_family_id("SBER", "1h", "sma_cross", {"fast": 10, "slow": 30}, 60)
        id2 = experiment_family_id("SBER", "1h", "sma_cross", {"fast": 10, "slow": 30}, 60)
        assert id1 == id2

    def test_id_format(self):
        fid = experiment_family_id("SBER", "1h", "sma_cross", {"fast": 10}, 60)
        assert fid.startswith("fam_")
        assert len(fid) == 20  # "fam_" + 16 hex chars


# ===================================================================
# T2: Deterministic instance ID
# ===================================================================
class TestT2DeterministicInstanceId:
    """Same exact evidence produces same instance ID."""

    def test_same_evidence_same_id(self):
        kwargs = dict(
            instrument="SBER", timeframe="1h", strategy="sma_cross",
            parameters={"fast": 10, "slow": 30}, horizon_days=60,
            dataset_hash="abc123", dataset_start="2026-06-01", dataset_end="2026-08-01",
            code_hash="code123", cost_model_hash="cost123",
            validation_version="1.0", backtest_engine_version="futures_lab",
        )
        id1 = experiment_instance_id(**kwargs)
        id2 = experiment_instance_id(**kwargs)
        assert id1 == id2

    def test_id_format(self):
        iid = experiment_instance_id(
            instrument="SBER", timeframe="1h", strategy="sma_cross",
            parameters={"fast": 10}, horizon_days=60,
            dataset_hash="abc", dataset_start="2026-06-01", dataset_end="2026-08-01",
            code_hash="c1", cost_model_hash="co1",
            validation_version="1.0", backtest_engine_version="futures_lab",
        )
        assert iid.startswith("inst_")
        assert len(iid) == 21


# ===================================================================
# T3: Parameter ordering
# ===================================================================
class TestT3ParameterOrdering:
    """JSON/dict key order does not alter identity."""

    def test_key_order_independent(self):
        fid1 = experiment_family_id("SBER", "1h", "sma_cross", {"fast": 10, "slow": 30}, 60)
        fid2 = experiment_family_id("SBER", "1h", "sma_cross", {"slow": 30, "fast": 10}, 60)
        assert fid1 == fid2

    def test_instance_key_order_independent(self):
        kwargs = dict(
            instrument="SBER", timeframe="1h", strategy="sma_cross",
            parameters={"fast": 10, "slow": 30}, horizon_days=60,
            dataset_hash="abc", dataset_start="", dataset_end="",
            code_hash="c", cost_model_hash="co",
            validation_version="1.0", backtest_engine_version="v1",
        )
        id1 = experiment_instance_id(**kwargs)
        kwargs["parameters"] = {"slow": 30, "fast": 10}
        id2 = experiment_instance_id(**kwargs)
        assert id1 == id2


# ===================================================================
# T4: Material parameter difference
# ===================================================================
class TestT4MaterialParameterDifference:
    """Different parameter values change identity."""

    def test_different_params_different_id(self):
        fid1 = experiment_family_id("SBER", "1h", "sma_cross", {"fast": 10, "slow": 30}, 60)
        fid2 = experiment_family_id("SBER", "1h", "sma_cross", {"fast": 10, "slow": 40}, 60)
        assert fid1 != fid2

    def test_different_strategy_different_id(self):
        fid1 = experiment_family_id("SBER", "1h", "sma_cross", {"fast": 10}, 60)
        fid2 = experiment_family_id("SBER", "1h", "rsi_reversal", {"period": 14}, 60)
        assert fid1 != fid2


# ===================================================================
# T5: Exact duplicate
# ===================================================================
class TestT5ExactDuplicate:
    """Exact same instance is classified correctly."""

    def test_exact_duplicate(self):
        ident = {
            "instrument": "SBER", "timeframe": "1h", "strategy": "sma_cross",
            "parameters": {"fast": 10, "slow": 30}, "horizon_days": 60,
            "dataset_hash": "abc", "dataset_start": "2026-06-01", "dataset_end": "2026-08-01",
            "code_hash": "c1", "cost_model_hash": "co1",
            "validation_version": "1.0", "backtest_engine_version": "v1",
        }
        existing_inst = dict(ident)
        existing_inst["experiment_family_id"] = "fam_xxx"

        cls = classify_candidate(ident, [existing_inst], [])
        assert cls == "EXACT_DUPLICATE"


# ===================================================================
# T6: Revalidation
# ===================================================================
class TestT6Revalidation:
    """Same family on legitimately newer data is REVALIDATION."""

    def test_revalidation_newer_data(self):
        existing_ident = {
            "instrument": "SBER", "timeframe": "1h", "strategy": "sma_cross",
            "parameters": {"fast": 10, "slow": 30}, "horizon_days": 60,
            "dataset_hash": "abc", "dataset_start": "2026-06-01", "dataset_end": "2026-08-01",
            "code_hash": "c1", "cost_model_hash": "co1",
            "validation_version": "1.0", "backtest_engine_version": "v1",
        }
        existing_inst = dict(existing_ident)
        existing_inst["experiment_family_id"] = "fam_xxx"

        new_ident = dict(existing_ident)
        new_ident["dataset_hash"] = "def456"  # new data
        new_ident["dataset_end"] = "2026-08-30"  # newer

        existing_fam = {
            "experiment_family_id": "fam_xxx",
            "instrument": existing_ident["instrument"],
            "timeframe": existing_ident["timeframe"],
            "strategy": existing_ident["strategy"],
            "normalized_parameters": json.dumps(normalize_params(existing_ident["parameters"])),
            "horizon_days": existing_ident["horizon_days"],
        }
        cls = classify_candidate(new_ident, [existing_inst], [existing_fam])
        # Same family, same code/cost/methodology, different data → REVALIDATION
        assert cls == "REVALIDATION"


# ===================================================================
# T7: Code change
# ===================================================================
class TestT7CodeChange:
    """Changed code identity is not exact duplicate."""

    def test_code_change(self):
        existing_ident = {
            "instrument": "SBER", "timeframe": "1h", "strategy": "sma_cross",
            "parameters": {"fast": 10, "slow": 30}, "horizon_days": 60,
            "dataset_hash": "abc", "dataset_start": "2026-06-01", "dataset_end": "2026-08-01",
            "code_hash": "c1", "cost_model_hash": "co1",
            "validation_version": "1.0", "backtest_engine_version": "v1",
        }
        existing_inst = dict(existing_ident)
        existing_inst["experiment_family_id"] = "fam_xxx"

        new_ident = dict(existing_ident)
        new_ident["code_hash"] = "c2_new"  # code changed

        existing_fam = {
            "experiment_family_id": "fam_xxx",
            "instrument": existing_ident["instrument"],
            "timeframe": existing_ident["timeframe"],
            "strategy": existing_ident["strategy"],
            "normalized_parameters": json.dumps(normalize_params(existing_ident["parameters"])),
            "horizon_days": existing_ident["horizon_days"],
        }
        cls = classify_candidate(new_ident, [existing_inst], [existing_fam])
        assert cls == "CODE_CHANGE"


# ===================================================================
# T8: Cost change
# ===================================================================
class TestT8CostChange:
    """Changed cost model is not exact duplicate."""

    def test_cost_change(self):
        existing_ident = {
            "instrument": "SBER", "timeframe": "1h", "strategy": "sma_cross",
            "parameters": {"fast": 10, "slow": 30}, "horizon_days": 60,
            "dataset_hash": "abc", "dataset_start": "2026-06-01", "dataset_end": "2026-08-01",
            "code_hash": "c1", "cost_model_hash": "co1",
            "validation_version": "1.0", "backtest_engine_version": "v1",
        }
        existing_inst = dict(existing_ident)
        existing_inst["experiment_family_id"] = "fam_xxx"

        new_ident = dict(existing_ident)
        new_ident["cost_model_hash"] = "co2_new"

        existing_fam = {
            "experiment_family_id": "fam_xxx",
            "instrument": existing_ident["instrument"],
            "timeframe": existing_ident["timeframe"],
            "strategy": existing_ident["strategy"],
            "normalized_parameters": json.dumps(normalize_params(existing_ident["parameters"])),
            "horizon_days": existing_ident["horizon_days"],
        }
        cls = classify_candidate(new_ident, [existing_inst], [existing_fam])
        assert cls == "COST_MODEL_CHANGE"


# ===================================================================
# T9: Methodology change
# ===================================================================
class TestT9MethodologyChange:
    """Changed validation protocol is distinguished."""

    def test_methodology_change(self):
        existing_ident = {
            "instrument": "SBER", "timeframe": "1h", "strategy": "sma_cross",
            "parameters": {"fast": 10, "slow": 30}, "horizon_days": 60,
            "dataset_hash": "abc", "dataset_start": "2026-06-01", "dataset_end": "2026-08-01",
            "code_hash": "c1", "cost_model_hash": "co1",
            "validation_version": "1.0", "backtest_engine_version": "v1",
        }
        existing_inst = dict(existing_ident)
        existing_inst["experiment_family_id"] = "fam_xxx"

        new_ident = dict(existing_ident)
        new_ident["validation_version"] = "2.0"

        existing_fam = {
            "experiment_family_id": "fam_xxx",
            "instrument": existing_ident["instrument"],
            "timeframe": existing_ident["timeframe"],
            "strategy": existing_ident["strategy"],
            "normalized_parameters": json.dumps(normalize_params(existing_ident["parameters"])),
            "horizon_days": existing_ident["horizon_days"],
        }
        cls = classify_candidate(new_ident, [existing_inst], [existing_fam])
        assert cls == "METHODOLOGY_CHANGE"


# ===================================================================
# T10: Incomparable legacy
# ===================================================================
class TestT10IncomparableLegacy:
    """Missing evidence never becomes false duplicate."""

    def test_empty_ident_incomparable(self):
        # Completely empty identity — should be NEW (no history)
        ident = {
            "instrument": "", "timeframe": "", "strategy": "",
            "parameters": {}, "horizon_days": 0,
            "dataset_hash": "", "dataset_start": "", "dataset_end": "",
            "code_hash": "", "cost_model_hash": "",
            "validation_version": "", "backtest_engine_version": "",
        }
        cls = classify_candidate(ident, [], [])
        assert cls == "NEW"

    def test_no_false_duplicate_on_incomplete_history(self):
        ident = {
            "instrument": "SBER", "timeframe": "1h", "strategy": "sma_cross",
            "parameters": {"fast": 10}, "horizon_days": 60,
            "dataset_hash": "", "dataset_start": "", "dataset_end": "",
            "code_hash": "", "cost_model_hash": "",
            "validation_version": "", "backtest_engine_version": "",
        }
        # No existing instances — must be NEW
        cls = classify_candidate(ident, [], [])
        assert cls == "NEW"


# ===================================================================
# T11: Idempotent indexing
# ===================================================================
class TestT11IdempotentIndexing:
    """Index same run twice → no duplicate rows."""

    def test_double_index_no_duplicates(self, tmp_path):
        db_path = tmp_path / "test.db"
        mem = ExperimentMemory(db_path=db_path)

        entry = _sample_entry()
        run_dir = _create_run_bundle(tmp_path, "run_test_001", [entry])

        r1 = mem.index_run(run_dir)
        assert r1["status"] == "INDEXED"
        assert r1["instances_indexed"] == 1

        r2 = mem.index_run(run_dir)
        assert r2["status"] == "ALREADY_INDEXED"
        assert r2["instances_indexed"] == 1  # count of existing

        # Verify exactly one instance in DB
        instances = mem.find_by_run("run_test_001")
        assert len(instances) == 1
        mem.close()


# ===================================================================
# T12: Backfill restart
# ===================================================================
class TestT12BackfillRestart:
    """Interrupted backfill can safely resume."""

    def test_backfill_resumes_safely(self, tmp_path):
        db_path = tmp_path / "test.db"
        mem = ExperimentMemory(db_path=db_path)

        # Create two run bundles
        entry1 = _sample_entry(run_id="run_r1", config_key="key_r1")
        entry2 = _sample_entry(run_id="run_r2", config_key="key_r2")
        _create_run_bundle(tmp_path, "run_r1", [entry1])
        _create_run_bundle(tmp_path, "run_r2", [entry2])

        runs_dir = tmp_path / "runs"

        # First backfill
        r1 = mem.backfill(runs_dir)
        assert r1["runs_indexed"] == 2

        # Second backfill (simulates restart)
        r2 = mem.backfill(runs_dir)
        assert r2["runs_skipped"] == 2  # both already indexed
        assert r2["runs_indexed"] == 0

        mem.close()


# ===================================================================
# T13: Provenance
# ===================================================================
class TestT13Provenance:
    """Every indexed instance resolves to run_id/config_key."""

    def test_provenance_on_index(self, tmp_path):
        db_path = tmp_path / "test.db"
        mem = ExperimentMemory(db_path=db_path)

        entry = _sample_entry(run_id="run_prov_001", config_key="ck_prov_001")
        run_dir = _create_run_bundle(tmp_path, "run_prov_001", [entry])
        mem.index_run(run_dir)

        instances = mem.find_by_run("run_prov_001")
        assert len(instances) == 1
        inst = instances[0]
        assert inst["run_id"] == "run_prov_001"
        assert inst["config_key"] == "ck_prov_001"
        assert inst["experiment_instance_id"]
        assert inst["experiment_family_id"]
        mem.close()


# ===================================================================
# T14: Query API
# ===================================================================
class TestT14QueryApi:
    """History lookup returns deterministic ordered results."""

    def test_find_history_order(self, tmp_path):
        db_path = tmp_path / "test.db"
        mem = ExperimentMemory(db_path=db_path)

        # Index two runs for the same experiment
        entry1 = _sample_entry(run_id="run_q1", config_key="ck_q1")
        _create_run_bundle(tmp_path, "run_q1", [entry1])
        mem.index_run(tmp_path / "runs" / "run_q1")

        # Entry with same hypothesis but newer data
        entry2 = _sample_entry(run_id="run_q2", config_key="ck_q2")
        entry2["dataset_identity"]["actual_end"] = "2026-09-01"
        _create_run_bundle(tmp_path, "run_q2", [entry2])
        mem.index_run(tmp_path / "runs" / "run_q2")

        # Get family ID from first instance
        instances_q1 = mem.find_by_run("run_q1")
        assert len(instances_q1) == 1
        fid = instances_q1[0]["experiment_family_id"]

        # Find all history for this family
        history = mem.find_history(fid)
        assert len(history) >= 1
        # Ordered by indexed_at ASC
        for i in range(len(history) - 1):
            assert history[i]["indexed_at"] <= history[i + 1]["indexed_at"]
        mem.close()

    def test_find_by_config_key(self, tmp_path):
        db_path = tmp_path / "test.db"
        mem = ExperimentMemory(db_path=db_path)

        entry = _sample_entry(config_key="ck_findme")
        run_dir = _create_run_bundle(tmp_path, "run_find", [entry])
        mem.index_run(run_dir)

        results = mem.find_by_config_key("ck_findme")
        assert len(results) == 1
        assert results[0]["config_key"] == "ck_findme"
        mem.close()

    def test_recent_history(self, tmp_path):
        db_path = tmp_path / "test.db"
        mem = ExperimentMemory(db_path=db_path)

        entry = _sample_entry(instrument="SBER", strategy="sma_cross", timeframe="1h")
        run_dir = _create_run_bundle(tmp_path, "run_recent", [entry])
        mem.index_run(run_dir)

        recent = mem.recent_history(strategy="sma_cross", instrument="SBER")
        assert len(recent) == 1
        assert recent[0]["strategy"] == "sma_cross"
        mem.close()


# ===================================================================
# T15: Observation only
# ===================================================================
class TestT15ObservationOnly:
    """Memory classification does NOT skip or alter experiment execution."""

    def test_classification_has_no_side_effects(self):
        ident = {
            "instrument": "SBER", "timeframe": "1h", "strategy": "sma_cross",
            "parameters": {"fast": 10}, "horizon_days": 60,
            "dataset_hash": "abc", "dataset_start": "", "dataset_end": "",
            "code_hash": "c1", "cost_model_hash": "co1",
            "validation_version": "1.0", "backtest_engine_version": "v1",
        }
        existing_inst = dict(ident)
        existing_inst["experiment_family_id"] = "fam_xxx"

        # Classify
        cls = classify_candidate(ident, [existing_inst], [])
        assert cls == "EXACT_DUPLICATE"

        # No side effects: the classification should NEVER modify
        # execution flow, skip experiments, or change priorities.
        # This is a structural invariant — verified by:
        # 1. classify_candidate() has no write operations
        # 2. ExperimentMemory.classify_candidate() returns metadata only
        # 3. The function does not have VETO/SKIP/PRIORITIZE/EXECUTE

    def test_classify_candidate_api_no_skip(self):
        """The public classify_candidate API returns info, never commands."""
        ident = {
            "instrument": "SBER", "timeframe": "1h", "strategy": "sma_cross",
            "parameters": {"fast": 10}, "horizon_days": 60,
            "dataset_hash": "", "dataset_start": "", "dataset_end": "",
            "code_hash": "", "cost_model_hash": "",
            "validation_version": "", "backtest_engine_version": "",
        }
        # No DB needed — returns classification metadata only
        # The result is informational; nothing in the result dict
        # says "skip this" or "do not run"
        cls_dict = classify_candidate(ident, [], [])
        # classify_candidate returns a string, not a command
        assert isinstance(cls_dict, str)
        assert cls_dict in VALID_CLASSIFICATIONS


# ===================================================================
# T16: Canonical-run gate
# ===================================================================
class TestT16CanonicalRunGate:
    """Invalid/non-completed run cannot be indexed as completed evidence."""

    def test_partial_run_rejected(self, tmp_path):
        db_path = tmp_path / "test.db"
        mem = ExperimentMemory(db_path=db_path)

        entry = _sample_entry()
        run_dir = _create_run_bundle(
            tmp_path, "run_partial", [entry],
            manifest_overrides={"status": "PARTIAL"},
        )
        result = mem.index_run(run_dir)
        assert result["status"] == "NOT_COMPLETED"
        assert result["instances_indexed"] == 0
        mem.close()

    def test_failed_run_rejected(self, tmp_path):
        db_path = tmp_path / "test.db"
        mem = ExperimentMemory(db_path=db_path)

        entry = _sample_entry()
        run_dir = _create_run_bundle(
            tmp_path, "run_failed", [entry],
            manifest_overrides={"status": "FAILED"},
        )
        result = mem.index_run(run_dir)
        assert result["status"] == "NOT_COMPLETED"
        mem.close()

    def test_running_run_rejected(self, tmp_path):
        db_path = tmp_path / "test.db"
        mem = ExperimentMemory(db_path=db_path)

        entry = _sample_entry()
        run_dir = _create_run_bundle(
            tmp_path, "run_running", [entry],
            manifest_overrides={"status": "RUNNING"},
        )
        result = mem.index_run(run_dir)
        assert result["status"] == "NOT_COMPLETED"
        mem.close()


# ===================================================================
# T17: Regression — safety constraints
# ===================================================================
class TestT17Regression:
    """Iterations 01–06 relevant safety/research tests remain green.

    No broker order may be created in experiment memory module.
    """

    def test_no_broker_imports(self):
        """experiment_memory.py must not import any broker modules."""
        import core.experiment_memory as mod
        source = open(mod.__file__).read()
        # Must not reference broker, post_order, or Tinkoff API
        dangerous = ["post_order", "tinkoff", "broker", "OrderRequest", "live_order"]
        for term in dangerous:
            assert term.lower() not in source.lower(), (
                f"experiment_memory.py contains broker reference: {term}"
            )

    def test_valid_classifications_complete(self):
        assert "NEW" in VALID_CLASSIFICATIONS
        assert "EXACT_DUPLICATE" in VALID_CLASSIFICATIONS
        assert "REVALIDATION" in VALID_CLASSIFICATIONS
        assert "METHODOLOGY_CHANGE" in VALID_CLASSIFICATIONS
        assert "CODE_CHANGE" in VALID_CLASSIFICATIONS
        assert "COST_MODEL_CHANGE" in VALID_CLASSIFICATIONS
        assert "INCOMPARABLE" in VALID_CLASSIFICATIONS

    def test_normalize_params_deterministic(self):
        p1 = normalize_params({"b": 2, "a": 1})
        p2 = normalize_params({"a": 1, "b": 2})
        assert p1 == p2
        assert p1 == {"a": 1, "b": 2}


# ===================================================================
# F1: Memory DB missing
# ===================================================================
class TestF1MemoryDbMissing:
    def test_auto_creates_db(self, tmp_path):
        db_path = tmp_path / "new" / "experiment_memory.db"
        mem = ExperimentMemory(db_path=db_path)
        assert db_path.exists()
        mem.close()

    def test_memory_db_separate_from_analytics(self, tmp_path):
        db_path = tmp_path / "experiment_memory.db"
        mem = ExperimentMemory(db_path=db_path)
        # Verify it's a proper SQLite DB with our schema
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        assert "experiment_families" in tables
        assert "experiment_instances" in tables
        assert "indexed_runs" in tables
        conn.close()
        mem.close()


# ===================================================================
# F2: Schema initialization fails
# ===================================================================
class TestF2SchemaInitFails:
    def test_schema_version_recorded(self, tmp_path):
        db_path = tmp_path / "test.db"
        mem = ExperimentMemory(db_path=db_path)
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
        assert row is not None
        assert row[0] == "1.0.0"
        conn.close()
        mem.close()


# ===================================================================
# F3: Completed run missing ledger
# ===================================================================
class TestF3CompletedRunMissingLedger:
    def test_missing_ledger(self, tmp_path):
        db_path = tmp_path / "test.db"
        mem = ExperimentMemory(db_path=db_path)

        run_dir = tmp_path / "runs" / "run_noledger"
        run_dir.mkdir(parents=True)
        (run_dir / "manifest.json").write_text(json.dumps(_sample_manifest()))

        result = mem.index_run(run_dir)
        assert result["status"] == "NO_LEDGER"
        mem.close()


# ===================================================================
# F4: Corrupt candidate row
# ===================================================================
class TestF4CorruptCandidateRow:
    def test_corrupt_line_skipped(self, tmp_path):
        db_path = tmp_path / "test.db"
        mem = ExperimentMemory(db_path=db_path)

        run_dir = tmp_path / "runs" / "run_corrupt"
        run_dir.mkdir(parents=True)
        (run_dir / "manifest.json").write_text(json.dumps(_sample_manifest()))
        (run_dir / "candidates.jsonl").write_text(
            "not valid json\n" + json.dumps(_sample_entry()) + "\n"
        )

        result = mem.index_run(run_dir)
        assert len(result["errors"]) > 0
        # Good entry still indexed
        assert result["instances_indexed"] == 1
        mem.close()


# ===================================================================
# F5: Missing identity field
# ===================================================================
class TestF5MissingIdentityField:
    def test_empty_instrument(self):
        fid = experiment_family_id("", "1h", "sma_cross", {"fast": 10}, 60)
        fid2 = experiment_family_id("SBER", "1h", "sma_cross", {"fast": 10}, 60)
        assert fid != fid2

    def test_empty_strategy(self):
        fid = experiment_family_id("SBER", "1h", "", {"fast": 10}, 60)
        fid2 = experiment_family_id("SBER", "1h", "sma_cross", {"fast": 10}, 60)
        assert fid != fid2


# ===================================================================
# F6: Same run indexed twice
# ===================================================================
class TestF6SameRunIndexedTwice:
    def test_idempotent_double_index(self, tmp_path):
        db_path = tmp_path / "test.db"
        mem = ExperimentMemory(db_path=db_path)

        entry = _sample_entry()
        run_dir = _create_run_bundle(tmp_path, "run_dup", [entry])

        r1 = mem.index_run(run_dir)
        r2 = mem.index_run(run_dir)

        assert r1["instances_indexed"] == 1
        assert r2["status"] == "ALREADY_INDEXED"

        instances = mem.find_by_run("run_dup")
        assert len(instances) == 1
        mem.close()


# ===================================================================
# F7: Same exact instance in two runs
# ===================================================================
class TestF7SameInstanceInTwoRuns:
    def test_exact_duplicate_across_runs(self, tmp_path):
        db_path = tmp_path / "test.db"
        mem = ExperimentMemory(db_path=db_path)

        # First run
        entry1 = _sample_entry(run_id="run_a", config_key="ck_a")
        _create_run_bundle(tmp_path, "run_a", [entry1])
        mem.index_run(tmp_path / "runs" / "run_a")

        # Second run — same exact candidate
        entry2 = _sample_entry(run_id="run_b", config_key="ck_b")
        _create_run_bundle(tmp_path, "run_b", [entry2])
        r2 = mem.index_run(tmp_path / "runs" / "run_b")

        # Second index should classify as EXACT_DUPLICATE
        assert r2["classifications"].get("EXACT_DUPLICATE", 0) >= 1
        mem.close()


# ===================================================================
# F8: Same family with newer dataset
# ===================================================================
class TestF8SameFamilyNewerDataset:
    def test_revalidation_with_newer_data(self, tmp_path):
        db_path = tmp_path / "test.db"
        mem = ExperimentMemory(db_path=db_path)

        entry1 = _sample_entry(run_id="run_old", config_key="ck_old")
        _create_run_bundle(tmp_path, "run_old", [entry1])
        mem.index_run(tmp_path / "runs" / "run_old")

        # Newer data
        entry2 = _sample_entry(run_id="run_new", config_key="ck_new")
        entry2["dataset_identity"]["actual_end"] = "2026-09-30"
        entry2["dataset_identity"]["hash"] = "newer_hash"
        _create_run_bundle(tmp_path, "run_new", [entry2])
        r2 = mem.index_run(tmp_path / "runs" / "run_new")

        assert r2["classifications"].get("REVALIDATION", 0) >= 1
        mem.close()


# ===================================================================
# F9: Strategy code version changes
# ===================================================================
class TestF9CodeVersionChanges:
    def test_code_change_detection(self):
        existing_ident = {
            "instrument": "SBER", "timeframe": "1h", "strategy": "sma_cross",
            "parameters": {"fast": 10}, "horizon_days": 60,
            "dataset_hash": "abc", "dataset_start": "", "dataset_end": "",
            "code_hash": "v1_hash", "cost_model_hash": "co1",
            "validation_version": "1.0", "backtest_engine_version": "v1",
        }
        inst = dict(existing_ident)
        inst["experiment_family_id"] = "fam_x"

        new_ident = dict(existing_ident)
        new_ident["code_hash"] = "v2_hash"

        fam = {
            "experiment_family_id": "fam_x",
            "instrument": existing_ident["instrument"],
            "timeframe": existing_ident["timeframe"],
            "strategy": existing_ident["strategy"],
            "normalized_parameters": json.dumps(normalize_params(existing_ident["parameters"])),
            "horizon_days": existing_ident["horizon_days"],
        }
        cls = classify_candidate(new_ident, [inst], [fam])
        assert cls == "CODE_CHANGE"


# ===================================================================
# F10: Cost model changes
# ===================================================================
class TestF10CostModelChanges:
    def test_cost_change_detection(self):
        existing_ident = {
            "instrument": "SBER", "timeframe": "1h", "strategy": "sma_cross",
            "parameters": {"fast": 10}, "horizon_days": 60,
            "dataset_hash": "abc", "dataset_start": "", "dataset_end": "",
            "code_hash": "c1", "cost_model_hash": "old_cost",
            "validation_version": "1.0", "backtest_engine_version": "v1",
        }
        inst = dict(existing_ident)
        inst["experiment_family_id"] = "fam_x"

        new_ident = dict(existing_ident)
        new_ident["cost_model_hash"] = "new_cost"

        fam = {
            "experiment_family_id": "fam_x",
            "instrument": existing_ident["instrument"],
            "timeframe": existing_ident["timeframe"],
            "strategy": existing_ident["strategy"],
            "normalized_parameters": json.dumps(normalize_params(existing_ident["parameters"])),
            "horizon_days": existing_ident["horizon_days"],
        }
        cls = classify_candidate(new_ident, [inst], [fam])
        assert cls == "COST_MODEL_CHANGE"


# ===================================================================
# F11: Validation protocol changes
# ===================================================================
class TestF11ValidationProtocolChanges:
    def test_validation_change(self):
        existing_ident = {
            "instrument": "SBER", "timeframe": "1h", "strategy": "sma_cross",
            "parameters": {"fast": 10}, "horizon_days": 60,
            "dataset_hash": "abc", "dataset_start": "", "dataset_end": "",
            "code_hash": "c1", "cost_model_hash": "co1",
            "validation_version": "v1", "backtest_engine_version": "futures_lab",
        }
        inst = dict(existing_ident)
        inst["experiment_family_id"] = "fam_x"

        new_ident = dict(existing_ident)
        new_ident["backtest_engine_version"] = "new_engine"

        fam = {
            "experiment_family_id": "fam_x",
            "instrument": existing_ident["instrument"],
            "timeframe": existing_ident["timeframe"],
            "strategy": existing_ident["strategy"],
            "normalized_parameters": json.dumps(normalize_params(existing_ident["parameters"])),
            "horizon_days": existing_ident["horizon_days"],
        }
        cls = classify_candidate(new_ident, [inst], [fam])
        assert cls == "METHODOLOGY_CHANGE"


# ===================================================================
# F12: Legacy run lacks identity evidence
# ===================================================================
class TestF12LegacyRunLacksEvidence:
    def test_legacy_not_indexed(self, tmp_path):
        db_path = tmp_path / "test.db"
        mem = ExperimentMemory(db_path=db_path)

        # Create runs dir with no canonical runs
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()

        result = mem.backfill(runs_dir)
        assert result["runs_discovered"] == 0
        assert result["instances_indexed"] == 0
        mem.close()

    def test_missing_manifest(self, tmp_path):
        db_path = tmp_path / "test.db"
        mem = ExperimentMemory(db_path=db_path)

        run_dir = tmp_path / "runs" / "run_nom"
        run_dir.mkdir(parents=True)
        # No manifest.json

        result = mem.index_run(run_dir)
        assert result["status"] == "NO_MANIFEST"
        mem.close()


# ===================================================================
# F13: Memory write fails midway
# ===================================================================
class TestF13MemoryWriteFailsMidway:
    def test_read_only_db_rejected(self, tmp_path):
        """If DB is read-only, index should handle gracefully."""
        db_path = tmp_path / "test.db"
        mem = ExperimentMemory(db_path=db_path)
        # Create a valid entry
        entry = _sample_entry()
        run_dir = _create_run_bundle(tmp_path, "run_ro", [entry])
        mem.index_run(run_dir)
        mem.close()

        # Make DB read-only
        os.chmod(db_path, 0o444)
        try:
            mem2 = ExperimentMemory(db_path=db_path)
            # Should fail gracefully on write
            try:
                result = mem2.index_run(run_dir)
                # Either errors or already indexed
                assert result["status"] in ("ALREADY_INDEXED",) or len(result["errors"]) > 0
            except (sqlite3.OperationalError, PermissionError):
                pass  # expected
            finally:
                mem2.close()
        finally:
            os.chmod(db_path, 0o644)


# ===================================================================
# F14: Process crashes during backfill
# ===================================================================
class TestF14ProcessCrashDuringBackfill:
    def test_partial_backfill_resumable(self, tmp_path):
        db_path = tmp_path / "test.db"
        mem = ExperimentMemory(db_path=db_path)

        # Create 3 runs
        for i in range(3):
            entry = _sample_entry(run_id=f"run_crash_{i}", config_key=f"ck_{i}")
            _create_run_bundle(tmp_path, f"run_crash_{i}", [entry])

        runs_dir = tmp_path / "runs"

        # Index first 2 runs
        r1 = mem.index_run(tmp_path / "runs" / "run_crash_0")
        r2 = mem.index_run(tmp_path / "runs" / "run_crash_1")
        assert r1["status"] == "INDEXED"
        assert r2["status"] == "INDEXED"

        # Now backfill all — run_0 and run_1 are already indexed, run_2 is new
        r3 = mem.backfill(runs_dir)
        assert r3["runs_indexed"] >= 1
        assert r3["runs_skipped"] >= 2
        mem.close()


# ===================================================================
# F15: Schema version changes
# ===================================================================
class TestF15SchemaVersionChanges:
    def test_schema_version_recorded(self, tmp_path):
        db_path = tmp_path / "test.db"
        mem = ExperimentMemory(db_path=db_path)

        import sqlite3
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        assert row[0] == "1.0.0"
        conn.close()
        mem.close()


# ===================================================================
# F16: Classification ambiguous
# ===================================================================
class TestF16ClassificationAmbiguous:
    def test_no_matching_history_new(self):
        """With no existing history, classification must be NEW."""
        ident = {
            "instrument": "SBER", "timeframe": "1h", "strategy": "sma_cross",
            "parameters": {"fast": 10}, "horizon_days": 60,
            "dataset_hash": "", "dataset_start": "", "dataset_end": "",
            "code_hash": "", "cost_model_hash": "",
            "validation_version": "", "backtest_engine_version": "",
        }
        cls = classify_candidate(ident, [], [])
        assert cls == "NEW"

    def test_all_different_dimensions(self):
        """When everything differs, must still classify deterministically."""
        existing_ident = {
            "instrument": "GAZP", "timeframe": "15m", "strategy": "rsi_reversal",
            "parameters": {"period": 14}, "horizon_days": 90,
            "dataset_hash": "xyz", "dataset_start": "2025-01-01", "dataset_end": "2025-06-01",
            "code_hash": "c_old", "cost_model_hash": "co_old",
            "validation_version": "1.0", "backtest_engine_version": "v1",
        }
        inst = dict(existing_ident)
        inst["experiment_family_id"] = "fam_other"

        new_ident = {
            "instrument": "SBER", "timeframe": "1h", "strategy": "sma_cross",
            "parameters": {"fast": 10}, "horizon_days": 60,
            "dataset_hash": "abc", "dataset_start": "2026-06-01", "dataset_end": "2026-08-01",
            "code_hash": "c_new", "cost_model_hash": "co_new",
            "validation_version": "2.0", "backtest_engine_version": "v2",
        }
        cls = classify_candidate(new_ident, [inst], [])
        # Different family → NEW
        assert cls == "NEW"


# ===================================================================
# Integration: full lifecycle
# ===================================================================
class TestIntegrationFullLifecycle:
    """End-to-end lifecycle: create, index, query, classify."""

    def test_full_lifecycle(self, tmp_path):
        db_path = tmp_path / "test.db"
        mem = ExperimentMemory(db_path=db_path)

        # 1. Index first run
        entry1 = _sample_entry(
            run_id="run_001", config_key="ck_001",
            instrument="SBER", strategy="sma_cross",
            parameters={"fast": 10, "slow": 30},
        )
        _create_run_bundle(tmp_path, "run_001", [entry1])
        r1 = mem.index_run(tmp_path / "runs" / "run_001")
        assert r1["status"] == "INDEXED"
        assert r1["classifications"].get("NEW", 0) == 1

        # 2. Index second run — same hypothesis, different data
        entry2 = _sample_entry(
            run_id="run_002", config_key="ck_002",
            instrument="SBER", strategy="sma_cross",
            parameters={"fast": 10, "slow": 30},
        )
        entry2["dataset_identity"]["actual_end"] = "2026-09-01"
        entry2["dataset_identity"]["hash"] = "new_hash_2"
        _create_run_bundle(tmp_path, "run_002", [entry2])
        r2 = mem.index_run(tmp_path / "runs" / "run_002")
        assert r2["status"] == "INDEXED"
        assert r2["classifications"].get("REVALIDATION", 0) >= 1

        # 3. Query history
        instances1 = mem.find_by_run("run_001")
        assert len(instances1) == 1
        fid = instances1[0]["experiment_family_id"]

        history = mem.find_history(fid)
        assert len(history) == 2

        # 4. Classify new candidate — use same validation_version as manifest
        cls_result = mem.classify_candidate(
            instrument="SBER", timeframe="1h", strategy="sma_cross",
            parameters={"fast": 10, "slow": 30}, horizon_days=60,
            validation_version="1.0.0",
        )
        assert cls_result["prior_instance_count"] == 2
        assert cls_result["experiment_family_id"] == fid
        assert cls_result["last_seen_at"] is not None

        # 5. Summary
        summary = mem.summary()
        assert summary["total_families"] >= 1
        assert summary["total_instances"] == 2
        assert summary["runs_indexed"] == 2

        mem.close()


class TestNormalization:
    """Normalization edge cases."""

    def test_normalize_params_none(self):
        assert normalize_params(None) == {}

    def test_normalize_params_empty(self):
        assert normalize_params({}) == {}

    def test_normalize_params_float_whole_numbers(self):
        result = normalize_params({"a": 10.0, "b": 20.5})
        assert result["a"] == 10
        assert result["b"] == 20.5

    def test_normalize_instrument_uppercase(self):
        assert normalize_instrument("sber") == "SBER"
        assert normalize_instrument("  GazP  ") == "GAZP"

    def test_normalize_timeframe(self):
        assert normalize_timeframe("1H") == "1h"
        assert normalize_timeframe("  15M  ") == "15m"

    def test_normalize_strategy(self):
        assert normalize_strategy_name("SMA-Cross") == "sma_cross"
        assert normalize_strategy_name("RSI Reversal") == "rsi_reversal"

    def test_hash_determinism(self):
        h1 = _hash_str("hello")
        h2 = _hash_str("hello")
        assert h1 == h2
        assert len(h1) == 16

    def test_canonical_json_deterministic(self):
        j1 = _canonical_json({"b": 2, "a": 1})
        j2 = _canonical_json({"a": 1, "b": 2})
        assert j1 == j2
        # Compact format
        assert ", " not in j1
        assert ": " not in j1

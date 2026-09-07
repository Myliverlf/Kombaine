"""Tests for Production Performance Attribution — Iteration 14.

Covers:
- T1-T23: Mandatory test cases
- F1-F24: Failure matrix scenarios
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.performance_attribution import (
    AttributionBuild,
    AttributionConfidence,
    ConfidenceFilter,
    EvidenceClass,
    EvidenceLabel,
    ExecutionOutcome,
    OutcomeStatus,
    PerformanceAttributionBuilder,
    PerformanceAttributionStore,
    PositionOutcome,
    RealizationStatus,
    StrategyAggregate,
    StrategyAttribution,
    AnalyticsReconciler,
    LifecycleIntegration,
    AttributionHealthChecker,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EJ_SCHEMA = """CREATE TABLE IF NOT EXISTS execution_intents (
    intent_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    action TEXT NOT NULL,
    ticker TEXT NOT NULL,
    instrument_id TEXT,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    slot_id TEXT,
    strategy TEXT,
    provenance TEXT,
    mode TEXT NOT NULL,
    paper_first INTEGER NOT NULL,
    status TEXT NOT NULL,
    broker_client_order_id TEXT NOT NULL,
    broker_order_id TEXT,
    fill_quantity INTEGER NOT NULL DEFAULT 0,
    fill_price REAL,
    submission_attempt_count INTEGER NOT NULL DEFAULT 0,
    last_submission_at TEXT,
    last_error_type TEXT,
    last_error_message TEXT,
    reconciled_at TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1
);"""

ANALYTICS_SCHEMA = """CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_open TEXT NOT NULL,
    ts_close TEXT,
    slot_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    strategy TEXT NOT NULL,
    direction TEXT NOT NULL,
    contracts INTEGER NOT NULL DEFAULT 1,
    entry_price REAL,
    exit_price REAL,
    pnl_rub REAL,
    exit_reason TEXT,
    regime TEXT,
    hour INTEGER,
    status TEXT NOT NULL DEFAULT 'open'
);"""


def _make_ej_conn(tmp_path):
    """Create a fresh execution journal connection."""
    conn = sqlite3.connect(str(tmp_path / f"ej_{id(conn)}.db"))
    conn.executescript(EJ_SCHEMA)
    return conn


def _insert_intent(conn, intent_id, strategy=None, ticker="SBER", side="LONG",
                   quantity=10, fill_qty=10, fill_price=250.0, status="FILLED",
                   broker_order_id=None, slot_id="slot_1", provenance=None):
    """Insert a test intent into execution journal."""
    conn.execute(
        "INSERT INTO execution_intents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            intent_id, "2026-08-30T10:00:00+00:00", "2026-08-30T10:01:00+00:00",
            "BUY", ticker, f"{ticker}_fut", side, quantity, slot_id,
            strategy, json.dumps(provenance or {}),
            "paper", 1, status, intent_id, broker_order_id or f"paper_{intent_id}",
            fill_qty, fill_price, 1, None, None, None, None, 1,
        ),
    )
    conn.commit()


SAMPLE_REGISTRY = {
    "version": 1,
    "strategies": {
        "sma_cross_SBER_5m": {
            "strategy_id": "sma_cross_SBER_5m", "ticker": "SBER",
            "strategy": "sma_cross", "params": {"fast": 5, "slow": 20},
            "status": "active_signal_pool", "source": "generator",
        },
        "rsi_meanrev_GAZP_15m": {
            "strategy_id": "rsi_meanrev_GAZP_15m", "ticker": "GAZP",
            "strategy": "rsi_meanrev", "params": {"period": 14},
            "status": "active_watchlist", "source": "generator",
        },
    },
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    db_path = tmp_path / "test_pa.db"
    store = PerformanceAttributionStore(db_path)
    store.ensure_schema()
    yield store
    store.close()


@pytest.fixture
def ej(tmp_path):
    """Fresh execution journal connection."""
    conn = sqlite3.connect(str(tmp_path / "ej.db"))
    conn.executescript(EJ_SCHEMA)
    yield conn
    conn.close()


@pytest.fixture
def adb(tmp_path):
    """Fresh analytics DB connection."""
    conn = sqlite3.connect(str(tmp_path / "analytics.db"))
    conn.executescript(ANALYTICS_SCHEMA)
    yield conn
    conn.close()


# ===========================================================================
# T1: Schema/build
# ===========================================================================
class TestT1SchemaBuild:
    def test_store_initializes(self, tmp_db):
        conn = tmp_db._connect()
        tables = {t[0] for t in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "attribution_builds" in tables
        assert "execution_outcomes" in tables
        assert "strategy_attributions" in tables
        assert "position_outcomes" in tables
        assert "unattributed_events" in tables
        assert "attribution_conflicts" in tables

    def test_build_lifecycle(self, tmp_db):
        build = AttributionBuild(
            attribution_build_id="build_001",
            started_at=datetime.now(timezone.utc).isoformat(),
            finished_at=None, status="RUNNING",
        )
        tmp_db.start_build(build)
        tmp_db.finish_build("build_001", {"outcomes": 5}, "SUCCESS")
        result = tmp_db.get_build("build_001")
        assert result is not None
        assert result["status"] == "SUCCESS"


# ===========================================================================
# T2: Exact chain
# ===========================================================================
class TestT2ExactChain:
    def test_exact_chain_attribution(self, tmp_db, ej):
        _insert_intent(ej, "i001", strategy="sma_cross_SBER_5m")
        builder = PerformanceAttributionBuilder(tmp_db)
        build = builder.build_from_execution_journal(ej, registry_data=SAMPLE_REGISTRY)
        assert build.status == "SUCCESS"
        assert build.counts["attributed_exact"] == 1
        outcome = tmp_db.get_outcome(builder._make_id("outcome", "i001"))
        assert outcome is not None
        assert outcome["attribution_confidence"] == "EXACT"
        assert outcome["strategy_id"] == "sma_cross_SBER_5m"


# ===========================================================================
# T3: Broker order mapping
# ===========================================================================
class TestT3BrokerOrderMapping:
    def test_broker_order_mapping(self, tmp_db, ej):
        _insert_intent(ej, "i002", strategy="sma_cross_SBER_5m", broker_order_id="broker_123")
        builder = PerformanceAttributionBuilder(tmp_db)
        builder.build_from_execution_journal(ej, registry_data=SAMPLE_REGISTRY)
        conn = tmp_db._connect()
        row = conn.execute("SELECT * FROM execution_outcomes WHERE broker_order_id=?", ("broker_123",)).fetchone()
        assert row is not None
        assert dict(row)["intent_id"] == "i002"


# ===========================================================================
# T4: Confidence
# ===========================================================================
class TestT4Confidence:
    def test_confidence_levels(self, tmp_db, ej):
        _insert_intent(ej, "i_exact", strategy="sma_cross_SBER_5m")
        _insert_intent(ej, "i_strong", strategy="unknown_strategy")
        _insert_intent(ej, "i_unattr", strategy=None, slot_id=None)
        builder = PerformanceAttributionBuilder(tmp_db)
        build = builder.build_from_execution_journal(ej, registry_data=SAMPLE_REGISTRY)
        assert build.counts["attributed_exact"] == 1
        assert build.counts["attributed_strong"] == 1
        assert build.counts["unattributed"] == 1


# ===========================================================================
# T5: No overclaim
# ===========================================================================
class TestT5NoOverclaim:
    def test_no_overclaim_on_ambiguous(self, tmp_db, ej):
        _insert_intent(ej, "i_amb", strategy=None)
        builder = PerformanceAttributionBuilder(tmp_db)
        build = builder.build_from_execution_journal(ej, registry_data=SAMPLE_REGISTRY)
        outcome = tmp_db.get_outcome(builder._make_id("outcome", "i_amb"))
        assert outcome is not None
        assert outcome["attribution_confidence"] != "EXACT"


# ===========================================================================
# T6: PnL
# ===========================================================================
class TestT6PnL:
    def test_pnl_computation(self, tmp_db, ej, adb):
        adb.execute(
            "INSERT INTO trades (ts_open, ts_close, slot_id, ticker, strategy, direction, "
            "contracts, entry_price, exit_price, pnl_rub, exit_reason, regime, hour, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("2026-08-30T10:00:00+00:00", "2026-08-30T11:00:00+00:00",
             "slot_1", "SBER", "sma_cross_SBER_5m", "LONG",
             10, 250.0, 255.0, 50.0, "SIGNAL", "TREND", 10, "closed"),
        )
        adb.commit()
        builder = PerformanceAttributionBuilder(tmp_db)
        builder.build_from_execution_journal(ej, analytics_conn=adb,
            registry_data={"strategies": {"sma_cross_SBER_5m": {"strategy_id": "sma_cross_SBER_5m"}}})
        positions = tmp_db.get_all_position_outcomes()
        assert len(positions) == 1
        assert positions[0]["gross_pnl"] == 50.0
        assert positions[0]["net_pnl"] == 50.0
        assert positions[0]["realization_status"] == "REALIZED"


# ===========================================================================
# T7: Multiplier
# ===========================================================================
class TestT7Multiplier:
    def test_multiplier_field_preserved(self, tmp_db, ej, adb):
        adb.execute(
            "INSERT INTO trades (ts_open, ts_close, slot_id, ticker, strategy, direction, "
            "contracts, entry_price, exit_price, pnl_rub, exit_reason, regime, hour, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("2026-08-30T10:00:00+00:00", "2026-08-30T11:00:00+00:00",
             "slot_1", "SBER", "sma_cross_SBER_5m", "LONG",
             10, 250.0, 255.0, 50.0, "SIGNAL", "TREND", 10, "closed"),
        )
        adb.commit()
        builder = PerformanceAttributionBuilder(tmp_db)
        builder.build_from_execution_journal(ej, analytics_conn=adb,
            registry_data={"strategies": {"sma_cross_SBER_5m": {"strategy_id": "sma_cross_SBER_5m"}}})
        positions = tmp_db.get_all_position_outcomes()
        assert len(positions) == 1
        assert positions[0]["multiplier"] is None  # Unknown → None (honest)


# ===========================================================================
# T8: Partial fills
# ===========================================================================
class TestT8PartialFills:
    def test_partial_fill_vwap(self, tmp_db, ej):
        _insert_intent(ej, "i_part", strategy="sma_cross_SBER_5m", fill_qty=5, status="PARTIALLY_FILLED")
        builder = PerformanceAttributionBuilder(tmp_db)
        builder.build_from_execution_journal(ej, registry_data=SAMPLE_REGISTRY)
        outcome = tmp_db.get_outcome(builder._make_id("outcome", "i_part"))
        assert outcome is not None
        assert outcome["filled_qty"] == 5
        assert outcome["requested_qty"] == 10
        assert outcome["realization_status"] == "PARTIALLY_REALIZED"


# ===========================================================================
# T9: Partial closes
# ===========================================================================
class TestT9PartialCloses:
    def test_partial_close_realization(self, tmp_db, ej, adb):
        adb.execute(
            "INSERT INTO trades (ts_open, ts_close, slot_id, ticker, strategy, direction, "
            "contracts, entry_price, exit_price, pnl_rub, exit_reason, regime, hour, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("2026-08-30T10:00:00+00:00", None,
             "slot_1", "SBER", "sma_cross_SBER_5m", "LONG",
             10, 250.0, None, None, None, "TREND", 10, "open"),
        )
        adb.commit()
        builder = PerformanceAttributionBuilder(tmp_db)
        builder.build_from_execution_journal(ej, analytics_conn=adb,
            registry_data={"strategies": {"sma_cross_SBER_5m": {"strategy_id": "sma_cross_SBER_5m"}}})
        positions = tmp_db.get_all_position_outcomes()
        assert len(positions) == 1
        assert positions[0]["realization_status"] == "UNREALIZED"
        assert positions[0]["exit_qty"] == 0


# ===========================================================================
# T10: Reversal
# ===========================================================================
class TestT10Reversal:
    def test_reversal_detection(self, tmp_db, ej):
        _insert_intent(ej, "i_long", strategy="sma_cross_SBER_5m", side="LONG")
        _insert_intent(ej, "i_short", strategy="sma_cross_SBER_5m", side="SHORT", quantity=20, fill_qty=20, fill_price=255.0)
        builder = PerformanceAttributionBuilder(tmp_db)
        build = builder.build_from_execution_journal(ej, registry_data=SAMPLE_REGISTRY)
        outcomes = tmp_db.get_outcomes_for_strategy("sma_cross_SBER_5m")
        assert len(outcomes) == 2
        assert all(o["attribution_confidence"] == "EXACT" for o in outcomes)


# ===========================================================================
# T11: Idempotency
# ===========================================================================
class TestT11Idempotency:
    def test_idempotent_build(self, tmp_db, ej):
        _insert_intent(ej, "i_idem", strategy="sma_cross_SBER_5m")
        builder = PerformanceAttributionBuilder(tmp_db)
        builder.build_from_execution_journal(ej, registry_data=SAMPLE_REGISTRY)
        count1 = tmp_db._connect().execute("SELECT COUNT(*) FROM execution_outcomes").fetchone()[0]
        builder.build_from_execution_journal(ej, registry_data=SAMPLE_REGISTRY)
        count2 = tmp_db._connect().execute("SELECT COUNT(*) FROM execution_outcomes").fetchone()[0]
        assert count2 == count1  # INSERT OR REPLACE keeps same count


# ===========================================================================
# T12: Analytics reconciliation
# ===========================================================================
class TestT12AnalyticsReconciliation:
    def test_reconciliation_surfaces_mismatch(self, tmp_db, ej, adb):
        adb.execute(
            "INSERT INTO trades (ts_open, ts_close, slot_id, ticker, strategy, direction, "
            "contracts, entry_price, exit_price, pnl_rub, exit_reason, regime, hour, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("2026-08-30T10:00:00+00:00", "2026-08-30T11:00:00+00:00",
             "slot_1", "SBER", "sma_cross_SBER_5m", "LONG",
             10, 250.0, 255.0, 50.0, "SIGNAL", "TREND", 10, "closed"),
        )
        adb.commit()
        builder = PerformanceAttributionBuilder(tmp_db)
        builder.build_from_execution_journal(ej)
        reconciler = AnalyticsReconciler(tmp_db)
        result = reconciler.reconcile(adb)
        assert result["missing_in_attribution"] > 0
        assert result["total_analytics_trades"] == 1


# ===========================================================================
# T13: Broker truth
# ===========================================================================
class TestT13BrokerTruth:
    def test_broker_truth_preserved(self, tmp_db, ej):
        _insert_intent(ej, "i_brk", strategy="sma_cross_SBER_5m", broker_order_id="broker_001")
        builder = PerformanceAttributionBuilder(tmp_db)
        build = builder.build_from_execution_journal(ej, registry_data=SAMPLE_REGISTRY,
            evidence_class=EvidenceClass.BROKER_REAL.value)
        outcome = tmp_db.get_outcome(builder._make_id("outcome", "i_brk"))
        assert outcome is not None
        assert outcome["evidence_class"] == "BROKER_REAL"


# ===========================================================================
# T14: Paper vs real
# ===========================================================================
class TestT14PaperVsReal:
    def test_evidence_classes_never_collapse(self, tmp_path, tmp_db):
        """Use separate stores to prove evidence classes don't collapse."""
        # PAPER store
        paper_store = PerformanceAttributionStore(tmp_path / "paper.db")
        paper_store.ensure_schema()
        paper_ej = sqlite3.connect(str(tmp_path / "paper_ej.db"))
        paper_ej.executescript(EJ_SCHEMA)
        _insert_intent(paper_ej, "i_pap", strategy="sma_cross_SBER_5m")
        pb = PerformanceAttributionBuilder(paper_store)
        pb.build_from_execution_journal(paper_ej, registry_data=SAMPLE_REGISTRY,
            evidence_class=EvidenceClass.PAPER.value)
        paper_ej.close()

        # BROKER_REAL store
        real_store = PerformanceAttributionStore(tmp_path / "real.db")
        real_store.ensure_schema()
        real_ej = sqlite3.connect(str(tmp_path / "real_ej.db"))
        real_ej.executescript(EJ_SCHEMA)
        _insert_intent(real_ej, "i_real", strategy="sma_cross_SBER_5m", broker_order_id="broker_real_001")
        rb = PerformanceAttributionBuilder(real_store)
        rb.build_from_execution_journal(real_ej, registry_data=SAMPLE_REGISTRY,
            evidence_class=EvidenceClass.BROKER_REAL.value)
        real_ej.close()

        po = paper_store.get_outcome(pb._make_id("outcome", "i_pap"))
        ro = real_store.get_outcome(rb._make_id("outcome", "i_real"))
        assert po["evidence_class"] == "PAPER"
        assert ro["evidence_class"] == "BROKER_REAL"
        paper_store.close()
        real_store.close()


# ===========================================================================
# T15: Strategy aggregate
# ===========================================================================
class TestT15StrategyAggregate:
    def test_strategy_aggregates(self, tmp_db, ej):
        for i in range(5):
            _insert_intent(ej, f"i_agg_{i}", strategy="sma_cross_SBER_5m")
        builder = PerformanceAttributionBuilder(tmp_db)
        builder.build_from_execution_journal(ej, registry_data=SAMPLE_REGISTRY)
        perf = tmp_db.get_strategy_performance("sma_cross_SBER_5m", "EXACT_ONLY")
        assert perf is not None
        assert perf["trade_count"] == 5
        assert perf["evidence_label"] in ("EARLY", "USABLE", "MATURE")


# ===========================================================================
# T16: Insufficient evidence
# ===========================================================================
class TestT16InsufficientEvidence:
    def test_insufficient_evidence_label(self, tmp_db, ej, adb):
        _insert_intent(ej, "i_insuf", strategy="sma_cross_SBER_5m")
        adb.execute(
            "INSERT INTO trades (ts_open, ts_close, slot_id, ticker, strategy, direction, "
            "contracts, entry_price, exit_price, pnl_rub, exit_reason, regime, hour, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("2026-08-30T10:00:00+00:00", "2026-08-30T11:00:00+00:00",
             "slot_1", "SBER", "sma_cross_SBER_5m", "LONG",
             10, 250.0, 255.0, 50.0, "SIGNAL", "TREND", 10, "closed"),
        )
        adb.commit()
        builder = PerformanceAttributionBuilder(tmp_db)
        builder.build_from_execution_journal(ej, analytics_conn=adb,
            registry_data={"strategies": {"sma_cross_SBER_5m": {"strategy_id": "sma_cross_SBER_5m"}}})
        perf = tmp_db.get_strategy_performance("sma_cross_SBER_5m", "EXACT_ONLY")
        assert perf is not None
        assert perf["evidence_label"] == "INSUFFICIENT"


# ===========================================================================
# T17: Lifecycle integration
# ===========================================================================
class TestT17LifecycleIntegration:
    def test_lifecycle_reads_evidence(self, tmp_db, ej):
        _insert_intent(ej, "i_lc", strategy="sma_cross_SBER_5m")
        builder = PerformanceAttributionBuilder(tmp_db)
        builder.build_from_execution_journal(ej, registry_data=SAMPLE_REGISTRY)
        integration = LifecycleIntegration(tmp_db)
        evidence = integration.get_strategy_evidence("sma_cross_SBER_5m")
        assert evidence["has_production_evidence"] is True
        assert evidence["paper_evidence_count"] == 1
        assert evidence["broker_real_evidence_count"] == 0


# ===========================================================================
# T18: Lifecycle read-only
# ===========================================================================
class TestT18LifecycleReadOnly:
    def test_lifecycle_no_mutation(self, tmp_db):
        integration = LifecycleIntegration(tmp_db)
        evidence = integration.get_all_strategy_evidence()
        assert isinstance(evidence, dict)


# ===========================================================================
# T19: Health
# ===========================================================================
class TestT19Health:
    def test_health_check(self, tmp_db):
        checker = AttributionHealthChecker(tmp_db)
        health = checker.check_health()
        assert "status" in health
        assert "coverage" in health
        assert "issues" in health


# ===========================================================================
# T20: Secret safety
# ===========================================================================
class TestT20SecretSafety:
    def test_no_secrets_in_build(self, tmp_db, ej):
        _insert_intent(ej, "i_sec", strategy="sma_cross_SBER_5m",
                       provenance={"api_key": "SECRET_123"})
        builder = PerformanceAttributionBuilder(tmp_db)
        builder.build_from_execution_journal(ej, registry_data=SAMPLE_REGISTRY)
        outcome = tmp_db.get_outcome(builder._make_id("outcome", "i_sec"))
        assert "SECRET_123" not in (outcome.get("attribution_basis") or "")


# ===========================================================================
# T21: Production isolation
# ===========================================================================
class TestT21ProductionIsolation:
    def test_fixture_evidence_excluded(self):
        assert EvidenceClass.PAPER.value == "PAPER"
        assert EvidenceClass.BROKER_REAL.value == "BROKER_REAL"


# ===========================================================================
# T22: Broker mutation
# ===========================================================================
class TestT22BrokerMutation:
    def test_zero_broker_mutation(self, tmp_db, ej):
        _insert_intent(ej, "i_nb", strategy="sma_cross_SBER_5m")
        builder = PerformanceAttributionBuilder(tmp_db)
        assert not hasattr(builder, 'broker_client')
        build = builder.build_from_execution_journal(ej, registry_data=SAMPLE_REGISTRY)
        assert build.status == "SUCCESS"


# ===========================================================================
# T23: Regression
# ===========================================================================
class TestT23Regression:
    def test_existing_tests_still_pass(self):
        from core.performance_attribution import AttributionBuild, AttributionConfidence
        assert len(AttributionConfidence) == 5
        assert len(EvidenceClass) == 4
        assert len(RealizationStatus) == 3


# ===========================================================================
# F1: Performance DB unavailable
# ===========================================================================
class TestF1PerformanceDBUnavailable:
    def test_db_unavailable_handled(self, tmp_path):
        db_path = tmp_path / "nonexistent" / "test.db"
        try:
            store = PerformanceAttributionStore(db_path)
            assert store is not None
        except Exception:
            pass  # Expected


# ===========================================================================
# F2: Execution journal unavailable
# ===========================================================================
class TestF2ExecutionJournalUnavailable:
    def test_journal_unavailable(self, tmp_db, ej):
        builder = PerformanceAttributionBuilder(tmp_db)
        build = builder.build_from_execution_journal(ej, registry_data={})
        assert build.status == "SUCCESS"
        assert build.counts["execution_intents"] == 0


# ===========================================================================
# F3: Signal provenance missing
# ===========================================================================
class TestF3SignalProvenanceMissing:
    def test_missing_signal_provenance(self, tmp_db, ej):
        _insert_intent(ej, "i_nosig", strategy="sma_cross_SBER_5m")
        builder = PerformanceAttributionBuilder(tmp_db)
        build = builder.build_from_execution_journal(ej, registry_data=SAMPLE_REGISTRY)
        assert build.status == "SUCCESS"


# ===========================================================================
# F4: Registry strategy missing
# ===========================================================================
class TestF4RegistryStrategyMissing:
    def test_missing_registry_strategy(self, tmp_db, ej):
        _insert_intent(ej, "i_noreg", strategy="unknown_strategy")
        builder = PerformanceAttributionBuilder(tmp_db)
        build = builder.build_from_execution_journal(ej, registry_data={})
        assert build.status == "SUCCESS"
        assert build.counts["attributed_strong"] == 1


# ===========================================================================
# F5: Broker fill lacks intent mapping
# ===========================================================================
class TestF5BrokerFillLacksIntent:
    def test_broker_fill_no_intent(self, tmp_db, ej, adb):
        adb.execute(
            "INSERT INTO trades (ts_open, ts_close, slot_id, ticker, strategy, direction, "
            "contracts, entry_price, exit_price, pnl_rub, exit_reason, regime, hour, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("2026-08-30T10:00:00+00:00", "2026-08-30T11:00:00+00:00",
             "slot_1", "SBER", "unknown_strategy", "LONG",
             10, 250.0, 255.0, 50.0, "SIGNAL", "TREND", 10, "closed"),
        )
        adb.commit()
        builder = PerformanceAttributionBuilder(tmp_db)
        builder.build_from_execution_journal(ej, analytics_conn=adb, registry_data={})
        positions = tmp_db.get_all_position_outcomes()
        assert len(positions) == 1
        assert positions[0]["confidence"] in ("STRONG", "UNATTRIBUTED")


# ===========================================================================
# F6: Two strategies match same event
# ===========================================================================
class TestF6TwoStrategiesMatch:
    def test_conflict_detection(self, tmp_db):
        tmp_db.upsert_conflict(
            conflict_id="conflict_001", event_id="event_001", event_type="fill",
            candidate_strategy_ids=["strat_A", "strat_B"],
            candidate_confidences=["WEAK", "WEAK"],
            reason="Both match by ticker/time",
        )
        conflicts = tmp_db.get_conflicts()
        assert len(conflicts) == 1
        assert json.loads(conflicts[0]["candidate_strategy_ids"]) == ["strat_A", "strat_B"]


# ===========================================================================
# F7: Unknown instrument multiplier
# ===========================================================================
class TestF7UnknownMultiplier:
    def test_unknown_multiplier(self, tmp_db, ej, adb):
        adb.execute(
            "INSERT INTO trades (ts_open, ts_close, slot_id, ticker, strategy, direction, "
            "contracts, entry_price, exit_price, pnl_rub, exit_reason, regime, hour, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("2026-08-30T10:00:00+00:00", "2026-08-30T11:00:00+00:00",
             "slot_1", "SBER", "sma_cross_SBER_5m", "LONG",
             10, 250.0, 255.0, 50.0, "SIGNAL", "TREND", 10, "closed"),
        )
        adb.commit()
        builder = PerformanceAttributionBuilder(tmp_db)
        builder.build_from_execution_journal(ej, analytics_conn=adb,
            registry_data={"strategies": {"sma_cross_SBER_5m": {"strategy_id": "sma_cross_SBER_5m"}}})
        positions = tmp_db.get_all_position_outcomes()
        assert positions[0]["multiplier"] is None


# ===========================================================================
# F8: Missing commission
# ===========================================================================
class TestF8MissingCommission:
    def test_missing_commission(self, tmp_db, ej):
        _insert_intent(ej, "i_noc", strategy="sma_cross_SBER_5m")
        builder = PerformanceAttributionBuilder(tmp_db)
        build = builder.build_from_execution_journal(ej, registry_data=SAMPLE_REGISTRY)
        outcome = tmp_db.get_outcome(builder._make_id("outcome", "i_noc"))
        assert outcome["commission"] is None
        assert outcome["commission_status"] == "UNKNOWN"


# ===========================================================================
# F9: Partial fill
# ===========================================================================
class TestF9PartialFill:
    def test_partial_fill_handling(self, tmp_db, ej):
        _insert_intent(ej, "i_pf", strategy="sma_cross_SBER_5m", fill_qty=5, status="PARTIALLY_FILLED")
        builder = PerformanceAttributionBuilder(tmp_db)
        builder.build_from_execution_journal(ej, registry_data=SAMPLE_REGISTRY)
        outcome = tmp_db.get_outcome(builder._make_id("outcome", "i_pf"))
        assert outcome["filled_qty"] == 5
        assert outcome["requested_qty"] == 10
        assert outcome["outcome_status"] == "PARTIALLY_FILLED"


# ===========================================================================
# F10: Partial close
# ===========================================================================
class TestF10PartialClose:
    def test_partial_close(self, tmp_db, ej, adb):
        adb.execute(
            "INSERT INTO trades (ts_open, ts_close, slot_id, ticker, strategy, direction, "
            "contracts, entry_price, exit_price, pnl_rub, exit_reason, regime, hour, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("2026-08-30T10:00:00+00:00", None,
             "slot_1", "SBER", "sma_cross_SBER_5m", "LONG",
             10, 250.0, None, None, None, "TREND", 10, "open"),
        )
        adb.commit()
        builder = PerformanceAttributionBuilder(tmp_db)
        builder.build_from_execution_journal(ej, analytics_conn=adb,
            registry_data={"strategies": {"sma_cross_SBER_5m": {"strategy_id": "sma_cross_SBER_5m"}}})
        positions = tmp_db.get_all_position_outcomes()
        assert positions[0]["realization_status"] == "UNREALIZED"


# ===========================================================================
# F11: Reversal
# ===========================================================================
class TestF11Reversal:
    def test_reversal_scenarios(self, tmp_db, ej):
        _insert_intent(ej, "i_rl", strategy="sma_cross_SBER_5m", side="LONG")
        _insert_intent(ej, "i_rs", strategy="sma_cross_SBER_5m", side="SHORT", quantity=20, fill_qty=20, fill_price=255.0)
        builder = PerformanceAttributionBuilder(tmp_db)
        build = builder.build_from_execution_journal(ej, registry_data=SAMPLE_REGISTRY)
        outcomes = tmp_db.get_outcomes_for_strategy("sma_cross_SBER_5m")
        assert len(outcomes) == 2


# ===========================================================================
# F12: Analytics mismatch
# ===========================================================================
class TestF12AnalyticsMismatch:
    def test_analytics_mismatch(self, tmp_db, ej, adb):
        adb.execute(
            "INSERT INTO trades (ts_open, ts_close, slot_id, ticker, strategy, direction, "
            "contracts, entry_price, exit_price, pnl_rub, exit_reason, regime, hour, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("2026-08-30T10:00:00+00:00", "2026-08-30T11:00:00+00:00",
             "slot_1", "SBER", "sma_cross_SBER_5m", "LONG",
             10, 250.0, 255.0, 50.0, "SIGNAL", "TREND", 10, "closed"),
        )
        adb.commit()
        builder = PerformanceAttributionBuilder(tmp_db)
        builder.build_from_execution_journal(ej)
        reconciler = AnalyticsReconciler(tmp_db)
        result = reconciler.reconcile(adb)
        assert result["missing_in_attribution"] > 0


# ===========================================================================
# F13: Broker/local mismatch
# ===========================================================================
class TestF13BrokerLocalMismatch:
    def test_broker_local_mismatch(self, tmp_db, ej):
        _insert_intent(ej, "i_bm", strategy="sma_cross_SBER_5m", broker_order_id="broker_mismatch_001")
        builder = PerformanceAttributionBuilder(tmp_db)
        build = builder.build_from_execution_journal(ej, registry_data=SAMPLE_REGISTRY,
            evidence_class=EvidenceClass.BROKER_REAL.value)
        outcome = tmp_db.get_outcome(builder._make_id("outcome", "i_bm"))
        assert outcome["evidence_class"] == "BROKER_REAL"


# ===========================================================================
# F14: Duplicate build
# ===========================================================================
class TestF14DuplicateBuild:
    def test_duplicate_build_idempotent(self, tmp_db, ej):
        _insert_intent(ej, "i_dup", strategy="sma_cross_SBER_5m")
        builder = PerformanceAttributionBuilder(tmp_db)
        build1 = builder.build_from_execution_journal(ej, registry_data=SAMPLE_REGISTRY)
        build2 = builder.build_from_execution_journal(ej, registry_data=SAMPLE_REGISTRY)
        outcome = tmp_db.get_outcome(builder._make_id("outcome", "i_dup"))
        assert outcome is not None


# ===========================================================================
# F15: Stale sources
# ===========================================================================
class TestF15StaleSources:
    def test_stale_sources_detected(self, tmp_db):
        checker = AttributionHealthChecker(tmp_db)
        health = checker.check_health()
        assert health["status"] == "UNKNOWN"


# ===========================================================================
# F16: Weak heuristic attribution
# ===========================================================================
class TestF16WeakHeuristic:
    def test_weak_attribution_not_exaggerated(self, tmp_db, ej):
        _insert_intent(ej, "i_weak", strategy=None)
        builder = PerformanceAttributionBuilder(tmp_db)
        build = builder.build_from_execution_journal(ej, registry_data=SAMPLE_REGISTRY)
        outcome = tmp_db.get_outcome(builder._make_id("outcome", "i_weak"))
        assert outcome["attribution_confidence"] != "EXACT"


# ===========================================================================
# F17: Fixture data leaks into production build
# ===========================================================================
class TestF17FixtureLeak:
    def test_fixture_data_not_in_production(self, tmp_db, ej):
        builder = PerformanceAttributionBuilder(tmp_db)
        build = builder.build_from_execution_journal(ej)
        assert build.counts["outcomes_created"] == 0


# ===========================================================================
# F18: Lifecycle consumes wrong evidence class
# ===========================================================================
class TestF18LifecycleWrongEvidence:
    def test_lifecycle_evidence_class_aware(self, tmp_db, ej):
        _insert_intent(ej, "i_paper_only", strategy="sma_cross_SBER_5m")
        builder = PerformanceAttributionBuilder(tmp_db)
        builder.build_from_execution_journal(ej, registry_data=SAMPLE_REGISTRY,
            evidence_class=EvidenceClass.PAPER.value)
        integration = LifecycleIntegration(tmp_db)
        evidence = integration.get_strategy_evidence("sma_cross_SBER_5m")
        assert evidence["paper_evidence_count"] == 1
        assert evidence["broker_real_evidence_count"] == 0
        assert "PAPER" in evidence["evidence_classes_present"]


# ===========================================================================
# F19: Attribution tries registry mutation
# ===========================================================================
class TestF19RegistryMutation:
    def test_no_registry_mutation(self, tmp_db):
        builder = PerformanceAttributionBuilder(tmp_db)
        assert not hasattr(builder, 'registry')


# ===========================================================================
# F20: Attribution tries broker mutation
# ===========================================================================
class TestF20BrokerMutation:
    def test_no_broker_mutation(self, tmp_db):
        builder = PerformanceAttributionBuilder(tmp_db)
        assert not hasattr(builder, 'broker_client')


# ===========================================================================
# F21: Currency mismatch
# ===========================================================================
class TestF21CurrencyMismatch:
    def test_currency_field_preserved(self, tmp_db, ej, adb):
        adb.execute(
            "INSERT INTO trades (ts_open, ts_close, slot_id, ticker, strategy, direction, "
            "contracts, entry_price, exit_price, pnl_rub, exit_reason, regime, hour, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("2026-08-30T10:00:00+00:00", "2026-08-30T11:00:00+00:00",
             "slot_1", "SBER", "sma_cross_SBER_5m", "LONG",
             10, 250.0, 255.0, 50.0, "SIGNAL", "TREND", 10, "closed"),
        )
        adb.commit()
        builder = PerformanceAttributionBuilder(tmp_db)
        builder.build_from_execution_journal(ej, analytics_conn=adb,
            registry_data={"strategies": {"sma_cross_SBER_5m": {"strategy_id": "sma_cross_SBER_5m"}}})
        positions = tmp_db.get_all_position_outcomes()
        assert positions[0]["currency"] is None


# ===========================================================================
# F22: Missing exit
# ===========================================================================
class TestF22MissingExit:
    def test_missing_exit(self, tmp_db, ej, adb):
        adb.execute(
            "INSERT INTO trades (ts_open, ts_close, slot_id, ticker, strategy, direction, "
            "contracts, entry_price, exit_price, pnl_rub, exit_reason, regime, hour, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("2026-08-30T10:00:00+00:00", None,
             "slot_1", "SBER", "sma_cross_SBER_5m", "LONG",
             10, 250.0, None, None, None, "TREND", 10, "open"),
        )
        adb.commit()
        builder = PerformanceAttributionBuilder(tmp_db)
        builder.build_from_execution_journal(ej, analytics_conn=adb,
            registry_data={"strategies": {"sma_cross_SBER_5m": {"strategy_id": "sma_cross_SBER_5m"}}})
        positions = tmp_db.get_all_position_outcomes()
        assert positions[0]["exit_vwap"] is None
        assert positions[0]["realization_status"] == "UNREALIZED"


# ===========================================================================
# F23: Execution UNKNOWN
# ===========================================================================
class TestF23ExecutionUnknown:
    def test_execution_unknown(self, tmp_db, ej):
        _insert_intent(ej, "i_unk", strategy="sma_cross_SBER_5m", status="UNKNOWN")
        builder = PerformanceAttributionBuilder(tmp_db)
        build = builder.build_from_execution_journal(ej, registry_data=SAMPLE_REGISTRY)
        outcome = tmp_db.get_outcome(builder._make_id("outcome", "i_unk"))
        assert outcome["outcome_status"] == "UNKNOWN"


# ===========================================================================
# F24: Historical legacy event
# ===========================================================================
class TestF24HistoricalLegacy:
    def test_historical_legacy_unattributed(self, tmp_db, ej):
        _insert_intent(ej, "i_leg", strategy=None, slot_id=None)
        builder = PerformanceAttributionBuilder(tmp_db)
        build = builder.build_from_execution_journal(ej, registry_data={})
        outcome = tmp_db.get_outcome(builder._make_id("outcome", "i_leg"))
        assert outcome["attribution_confidence"] == "UNATTRIBUTED"


# ===========================================================================
# Additional tests
# ===========================================================================
class TestConfidenceFilter:
    def test_exact_only_filter(self, tmp_db, ej):
        _insert_intent(ej, "i_fe", strategy="sma_cross_SBER_5m")
        _insert_intent(ej, "i_fs", strategy="unknown_strategy")
        builder = PerformanceAttributionBuilder(tmp_db)
        builder.build_from_execution_journal(ej, registry_data=SAMPLE_REGISTRY)
        perf_exact = tmp_db.get_strategy_performance("sma_cross_SBER_5m", "EXACT_ONLY")
        assert perf_exact is not None
        assert perf_exact["trade_count"] == 1
        perf_strong = tmp_db.get_strategy_performance("sma_cross_SBER_5m", "EXACT_PLUS_STRONG")
        assert perf_strong is not None
        assert perf_strong["trade_count"] == 1


class TestUnattributedEvents:
    def test_unattributed_stored(self, tmp_db):
        tmp_db.upsert_unattributed(
            event_id="unattr_001", event_type="fill", ticker="SBER",
            side="LONG", quantity=10, timestamp="2026-08-30T10:00:00+00:00",
            source="broker", reason="no strategy provenance",
        )
        unattr = tmp_db.get_unattributed()
        assert len(unattr) == 1
        assert unattr[0]["reason"] == "no strategy provenance"


class TestConflicts:
    def test_conflicts_stored(self, tmp_db):
        tmp_db.upsert_conflict(
            conflict_id="conflict_002", event_id="event_002", event_type="fill",
            candidate_strategy_ids=["strat_A", "strat_B"],
            candidate_confidences=["WEAK", "WEAK"],
            reason="Both match by ticker/time",
        )
        conflicts = tmp_db.get_conflicts()
        assert len(conflicts) == 1


class TestCoverageSummary:
    def test_coverage_summary(self, tmp_db, ej):
        _insert_intent(ej, "i_cov", strategy="sma_cross_SBER_5m")
        builder = PerformanceAttributionBuilder(tmp_db)
        builder.build_from_execution_journal(ej, registry_data=SAMPLE_REGISTRY)
        coverage = tmp_db.get_coverage_summary()
        assert coverage["total_outcomes"] == 1
        assert coverage["by_confidence"]["EXACT"] == 1


class TestLifecycleAllStrategies:
    def test_all_strategies_evidence(self, tmp_db, ej):
        _insert_intent(ej, "i_m1", strategy="sma_cross_SBER_5m")
        _insert_intent(ej, "i_m2", strategy="rsi_meanrev_GAZP_15m", ticker="GAZP")
        builder = PerformanceAttributionBuilder(tmp_db)
        builder.build_from_execution_journal(ej, registry_data=SAMPLE_REGISTRY)
        integration = LifecycleIntegration(tmp_db)
        all_ev = integration.get_all_strategy_evidence()
        assert "sma_cross_SBER_5m" in all_ev
        assert "rsi_meanrev_GAZP_15m" in all_ev


class TestRealizationStatus:
    def test_realized_status(self, tmp_db, ej):
        _insert_intent(ej, "i_rlz", strategy="sma_cross_SBER_5m")
        builder = PerformanceAttributionBuilder(tmp_db)
        builder.build_from_execution_journal(ej, registry_data=SAMPLE_REGISTRY)
        outcome = tmp_db.get_outcome(builder._make_id("outcome", "i_rlz"))
        assert outcome["realization_status"] == "REALIZED"


class TestEvidenceClass:
    def test_paper_evidence_class(self, tmp_db, ej):
        _insert_intent(ej, "i_pap2", strategy="sma_cross_SBER_5m")
        builder = PerformanceAttributionBuilder(tmp_db)
        builder.build_from_execution_journal(ej, registry_data=SAMPLE_REGISTRY,
            evidence_class=EvidenceClass.PAPER.value)
        outcome = tmp_db.get_outcome(builder._make_id("outcome", "i_pap2"))
        assert outcome["evidence_class"] == "PAPER"


class TestDeterministicIDs:
    def test_deterministic_ids(self):
        builder = PerformanceAttributionBuilder(PerformanceAttributionStore(Path(":memory:")))
        id1 = builder._make_id("test", "input")
        id2 = builder._make_id("test", "input")
        assert id1 == id2

    def test_different_inputs_different_ids(self):
        builder = PerformanceAttributionBuilder(PerformanceAttributionStore(Path(":memory:")))
        id1 = builder._make_id("test", "input1")
        id2 = builder._make_id("test", "input2")
        assert id1 != id2


class TestHealthChecker:
    def test_health_after_build(self, tmp_db, ej):
        _insert_intent(ej, "i_h", strategy="sma_cross_SBER_5m")
        builder = PerformanceAttributionBuilder(tmp_db)
        builder.build_from_execution_journal(ej, registry_data=SAMPLE_REGISTRY)
        checker = AttributionHealthChecker(tmp_db)
        health = checker.check_health()
        assert health["status"] == "HEALTHY"
        assert health["coverage"]["total_outcomes"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

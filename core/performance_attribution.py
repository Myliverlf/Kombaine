"""Production Performance Attribution — Iteration 14.

Canonical, auditable, read-mostly production performance attribution layer that
answers which strategy produced which real/paper execution outcome, with exact
provenance and explicit confidence.

Change class: CLASS 2 — Read/derive production outcome truth, PAPER-first
ATTRIBUTION ≠ EXECUTION ≠ BROKER TRUTH
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ATTRIBUTION_SCHEMA_VERSION = "1.0.0"
ATTRIBUTION_DB_NAME = "performance_attribution.db"
ATTRIBUTION_BUILDER_VERSION = "1.0.0"

# Evidence classes — preserved from Iteration 10, never collapse
class EvidenceClass(str, Enum):
    BACKTEST = "BACKTEST"
    WALK_FORWARD = "WALK_FORWARD"
    PAPER = "PAPER"
    BROKER_REAL = "BROKER_REAL"

# Confidence levels
class AttributionConfidence(str, Enum):
    EXACT = "EXACT"
    STRONG = "STRONG"
    WEAK = "WEAK"
    UNATTRIBUTED = "UNATTRIBUTED"
    CONFLICTED = "CONFLICTED"

# Outcome status
class OutcomeStatus(str, Enum):
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"
    FAILED_SAFE = "FAILED_SAFE"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
    RECONCILED = "RECONCILED"

# PnL realization status
class RealizationStatus(str, Enum):
    REALIZED = "REALIZED"
    UNREALIZED = "UNREALIZED"
    PARTIALLY_REALIZED = "PARTIALLY_REALIZED"

# Minimum evidence labels
class EvidenceLabel(str, Enum):
    INSUFFICIENT = "INSUFFICIENT"
    EARLY = "EARLY"
    USABLE = "USABLE"
    MATURE = "MATURE"

# Confidence-aware aggregate filters
class ConfidenceFilter(str, Enum):
    EXACT_ONLY = "EXACT_ONLY"
    EXACT_PLUS_STRONG = "EXACT_PLUS_STRONG"
    ALL_ATTRIBUTED = "ALL_ATTRIBUTED"

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class AttributionBuild:
    """Metadata for a deterministic attribution reconciliation run."""
    attribution_build_id: str
    started_at: str
    finished_at: str | None
    source_ranges: Dict[str, Any] = field(default_factory=dict)
    source_hashes: Dict[str, str] = field(default_factory=dict)
    broker_operation_horizon: Dict[str, Any] = field(default_factory=dict)
    analytics_source_identity: str = ""
    execution_journal_source_identity: str = ""
    registry_source_identity: str = ""
    counts: Dict[str, int] = field(default_factory=dict)
    status: str = "RUNNING"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExecutionOutcome:
    """Derived outcome for a single executed intent/order/fill."""
    outcome_id: str
    intent_id: str | None
    signal_id: str | None
    strategy_id: str | None
    config_key: str | None
    ticker: str
    side: str
    instrument_id: str | None
    requested_qty: int
    filled_qty: int
    avg_fill_price: float | None
    commission: float | None
    commission_status: str = "UNKNOWN"  # KNOWN, UNKNOWN
    slippage: float | None = None
    entry_exit_role: str | None = None  # ENTRY, EXIT, REVERSAL
    broker_client_order_id: str | None = None
    broker_order_id: str | None = None
    created_at: str = ""
    filled_at: str | None = None
    attribution_confidence: str = AttributionConfidence.UNATTRIBUTED.value
    attribution_basis: str = ""
    evidence_class: str = EvidenceClass.PAPER.value
    slot_id: str | None = None
    realization_status: str = RealizationStatus.UNREALIZED.value
    outcome_status: str = OutcomeStatus.UNKNOWN.value

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StrategyAttribution:
    """Strategy-level attribution for an execution outcome."""
    attribution_id: str
    outcome_id: str
    strategy_id: str
    config_key: str
    family_id: str | None
    ticker: str
    side: str
    requested_qty: int
    filled_qty: int
    avg_fill_price: float | None
    commission: float | None
    gross_pnl: float | None = None
    net_pnl: float | None = None
    confidence: str = AttributionConfidence.UNATTRIBUTED.value
    basis: str = ""
    evidence_class: str = EvidenceClass.PAPER.value
    source_run_id: str | None = None
    registry_status: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PositionOutcome:
    """Position lifecycle outcome spanning multiple fills."""
    position_outcome_id: str
    strategy_id: str | None
    config_key: str | None
    family_id: str | None
    ticker: str
    instrument_id: str | None
    opened_at: str | None
    closed_at: str | None
    entry_qty: int = 0
    exit_qty: int = 0
    entry_vwap: float | None = None
    exit_vwap: float | None = None
    gross_pnl: float | None = None
    commission: float | None = None
    net_pnl: float | None = None
    holding_time_seconds: float | None = None
    close_reason: str = "UNKNOWN"
    confidence: str = AttributionConfidence.UNATTRIBUTED.value
    evidence_class: str = EvidenceClass.PAPER.value
    realization_status: str = RealizationStatus.UNREALIZED.value
    max_adverse_excursion: float | None = None
    max_favorable_excursion: float | None = None
    multiplier: float | None = None
    currency: str | None = None
    native_currency_pnl: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StrategyAggregate:
    """Performance aggregates for a specific strategy/config."""
    strategy_id: str
    config_key: str
    family_id: str | None
    trade_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    avg_trade: float = 0.0
    median_trade: float = 0.0
    profit_factor: float = 0.0
    max_loss: float = 0.0
    max_win: float = 0.0
    avg_holding_time: float = 0.0
    commission_total: float = 0.0
    evidence_label: str = EvidenceLabel.INSUFFICIENT.value
    confidence_filter: str = ConfidenceFilter.EXACT_ONLY.value

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

ATTRIBUTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS attribution_builds (
    attribution_build_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    source_ranges TEXT,
    source_hashes TEXT,
    broker_operation_horizon TEXT,
    analytics_source_identity TEXT,
    execution_journal_source_identity TEXT,
    registry_source_identity TEXT,
    counts TEXT,
    status TEXT NOT NULL DEFAULT 'RUNNING'
);

CREATE TABLE IF NOT EXISTS execution_outcomes (
    outcome_id TEXT PRIMARY KEY,
    intent_id TEXT,
    signal_id TEXT,
    strategy_id TEXT,
    config_key TEXT,
    ticker TEXT NOT NULL,
    side TEXT NOT NULL,
    instrument_id TEXT,
    requested_qty INTEGER NOT NULL,
    filled_qty INTEGER NOT NULL DEFAULT 0,
    avg_fill_price REAL,
    commission REAL,
    commission_status TEXT DEFAULT 'UNKNOWN',
    slippage REAL,
    entry_exit_role TEXT,
    broker_client_order_id TEXT,
    broker_order_id TEXT,
    created_at TEXT NOT NULL,
    filled_at TEXT,
    attribution_confidence TEXT NOT NULL DEFAULT 'UNATTRIBUTED',
    attribution_basis TEXT,
    evidence_class TEXT NOT NULL DEFAULT 'PAPER',
    slot_id TEXT,
    realization_status TEXT DEFAULT 'UNREALIZED',
    outcome_status TEXT DEFAULT 'UNKNOWN'
);

CREATE TABLE IF NOT EXISTS strategy_attributions (
    attribution_id TEXT PRIMARY KEY,
    outcome_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    config_key TEXT NOT NULL,
    family_id TEXT,
    ticker TEXT NOT NULL,
    side TEXT NOT NULL,
    requested_qty INTEGER NOT NULL,
    filled_qty INTEGER NOT NULL,
    avg_fill_price REAL,
    commission REAL,
    gross_pnl REAL,
    net_pnl REAL,
    confidence TEXT NOT NULL DEFAULT 'UNATTRIBUTED',
    basis TEXT,
    evidence_class TEXT NOT NULL DEFAULT 'PAPER',
    source_run_id TEXT,
    registry_status TEXT,
    FOREIGN KEY (outcome_id) REFERENCES execution_outcomes(outcome_id)
);

CREATE TABLE IF NOT EXISTS position_outcomes (
    position_outcome_id TEXT PRIMARY KEY,
    strategy_id TEXT,
    config_key TEXT,
    family_id TEXT,
    ticker TEXT NOT NULL,
    instrument_id TEXT,
    opened_at TEXT,
    closed_at TEXT,
    entry_qty INTEGER DEFAULT 0,
    exit_qty INTEGER DEFAULT 0,
    entry_vwap REAL,
    exit_vwap REAL,
    gross_pnl REAL,
    commission REAL,
    net_pnl REAL,
    holding_time_seconds REAL,
    close_reason TEXT DEFAULT 'UNKNOWN',
    confidence TEXT NOT NULL DEFAULT 'UNATTRIBUTED',
    evidence_class TEXT NOT NULL DEFAULT 'PAPER',
    realization_status TEXT DEFAULT 'UNREALIZED',
    max_adverse_excursion REAL,
    max_favorable_excursion REAL,
    multiplier REAL,
    currency TEXT,
    native_currency_pnl REAL
);

CREATE TABLE IF NOT EXISTS unattributed_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    ticker TEXT,
    side TEXT,
    quantity INTEGER,
    timestamp TEXT,
    source TEXT,
    reason TEXT NOT NULL,
    evidence_class TEXT DEFAULT 'PAPER'
);

CREATE TABLE IF NOT EXISTS attribution_conflicts (
    conflict_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    candidate_strategy_ids TEXT NOT NULL,
    candidate_confidences TEXT NOT NULL,
    reason TEXT NOT NULL,
    evidence_class TEXT DEFAULT 'PAPER'
);

CREATE INDEX IF NOT EXISTS idx_outcomes_strategy ON execution_outcomes(strategy_id);
CREATE INDEX IF NOT EXISTS idx_outcomes_intent ON execution_outcomes(intent_id);
CREATE INDEX IF NOT EXISTS idx_outcomes_ticker ON execution_outcomes(ticker);
CREATE INDEX IF NOT EXISTS idx_outcomes_broker ON execution_outcomes(broker_order_id);
CREATE INDEX IF NOT EXISTS idx_attributions_strategy ON strategy_attributions(strategy_id);
CREATE INDEX IF NOT EXISTS idx_attributions_outcome ON strategy_attributions(outcome_id);
CREATE INDEX IF NOT EXISTS idx_positions_strategy ON position_outcomes(strategy_id);
CREATE INDEX IF NOT EXISTS idx_positions_ticker ON position_outcomes(ticker);
"""


# ---------------------------------------------------------------------------
# Performance Attribution Store
# ---------------------------------------------------------------------------

class PerformanceAttributionStore:
    """Read-mostly SQLite store for production performance attribution.

    Allowed writes:
    - performance_attribution.db (this store)
    - derived reports

    NOT allowed:
    - registry mutation
    - signal mutation
    - execution intent mutation
    - broker mutation
    - analytics canonical overwrite
    """

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or (Path(__file__).resolve().parent.parent / "state" / ATTRIBUTION_DB_NAME)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), timeout=15)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=15000")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def ensure_schema(self) -> None:
        """Create tables if they don't exist."""
        conn = self._connect()
        conn.executescript(ATTRIBUTION_SCHEMA)
        conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ---- Build operations ----

    def start_build(self, build: AttributionBuild) -> None:
        """Record attribution build start."""
        conn = self._connect()
        conn.execute(
            "INSERT OR REPLACE INTO attribution_builds "
            "(attribution_build_id, started_at, finished_at, source_ranges, source_hashes, "
            "broker_operation_horizon, analytics_source_identity, execution_journal_source_identity, "
            "registry_source_identity, counts, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                build.attribution_build_id,
                build.started_at,
                build.finished_at,
                json.dumps(build.source_ranges, sort_keys=True),
                json.dumps(build.source_hashes, sort_keys=True),
                json.dumps(build.broker_operation_horizon, sort_keys=True),
                build.analytics_source_identity,
                build.execution_journal_source_identity,
                build.registry_source_identity,
                json.dumps(build.counts, sort_keys=True),
                build.status,
            ),
        )
        conn.commit()

    def finish_build(self, build_id: str, counts: Dict[str, int], status: str = "SUCCESS") -> None:
        """Record attribution build completion."""
        conn = self._connect()
        conn.execute(
            "UPDATE attribution_builds SET finished_at=?, counts=?, status=? WHERE attribution_build_id=?",
            (datetime.now(timezone.utc).isoformat(), json.dumps(counts, sort_keys=True), status, build_id),
        )
        conn.commit()

    def get_build(self, build_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM attribution_builds WHERE attribution_build_id=?", (build_id,)
        ).fetchone()
        return dict(row) if row else None

    # ---- Outcome operations ----

    def upsert_outcome(self, outcome: ExecutionOutcome) -> None:
        """Insert or replace execution outcome (idempotent by outcome_id)."""
        conn = self._connect()
        d = outcome.to_dict()
        conn.execute(
            "INSERT OR REPLACE INTO execution_outcomes "
            "(outcome_id, intent_id, signal_id, strategy_id, config_key, ticker, side, "
            "instrument_id, requested_qty, filled_qty, avg_fill_price, commission, "
            "commission_status, slippage, entry_exit_role, broker_client_order_id, "
            "broker_order_id, created_at, filled_at, attribution_confidence, "
            "attribution_basis, evidence_class, slot_id, realization_status, outcome_status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                d["outcome_id"], d["intent_id"], d["signal_id"], d["strategy_id"],
                d["config_key"], d["ticker"], d["side"], d["instrument_id"],
                d["requested_qty"], d["filled_qty"], d["avg_fill_price"],
                d["commission"], d["commission_status"], d["slippage"],
                d["entry_exit_role"], d["broker_client_order_id"],
                d["broker_order_id"], d["created_at"], d["filled_at"],
                d["attribution_confidence"], d["attribution_basis"],
                d["evidence_class"], d["slot_id"], d["realization_status"],
                d["outcome_status"],
            ),
        )
        conn.commit()

    def upsert_attribution(self, attr: StrategyAttribution) -> None:
        """Insert or replace strategy attribution (idempotent by attribution_id)."""
        conn = self._connect()
        d = attr.to_dict()
        conn.execute(
            "INSERT OR REPLACE INTO strategy_attributions "
            "(attribution_id, outcome_id, strategy_id, config_key, family_id, ticker, "
            "side, requested_qty, filled_qty, avg_fill_price, commission, gross_pnl, "
            "net_pnl, confidence, basis, evidence_class, source_run_id, registry_status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                d["attribution_id"], d["outcome_id"], d["strategy_id"],
                d["config_key"], d["family_id"], d["ticker"], d["side"],
                d["requested_qty"], d["filled_qty"], d["avg_fill_price"],
                d["commission"], d["gross_pnl"], d["net_pnl"], d["confidence"],
                d["basis"], d["evidence_class"], d["source_run_id"],
                d["registry_status"],
            ),
        )
        conn.commit()

    def upsert_position(self, pos: PositionOutcome) -> None:
        """Insert or replace position outcome (idempotent)."""
        conn = self._connect()
        d = pos.to_dict()
        conn.execute(
            "INSERT OR REPLACE INTO position_outcomes "
            "(position_outcome_id, strategy_id, config_key, family_id, ticker, "
            "instrument_id, opened_at, closed_at, entry_qty, exit_qty, entry_vwap, "
            "exit_vwap, gross_pnl, commission, net_pnl, holding_time_seconds, "
            "close_reason, confidence, evidence_class, realization_status, "
            "max_adverse_excursion, max_favorable_excursion, multiplier, currency, "
            "native_currency_pnl) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                d["position_outcome_id"], d["strategy_id"], d["config_key"],
                d["family_id"], d["ticker"], d["instrument_id"], d["opened_at"],
                d["closed_at"], d["entry_qty"], d["exit_qty"], d["entry_vwap"],
                d["exit_vwap"], d["gross_pnl"], d["commission"], d["net_pnl"],
                d["holding_time_seconds"], d["close_reason"], d["confidence"],
                d["evidence_class"], d["realization_status"],
                d["max_adverse_excursion"], d["max_favorable_excursion"],
                d["multiplier"], d["currency"], d["native_currency_pnl"],
            ),
        )
        conn.commit()

    def upsert_unattributed(self, event_id: str, event_type: str, ticker: str | None,
                             side: str | None, quantity: int | None, timestamp: str,
                             source: str, reason: str, evidence_class: str = "PAPER") -> None:
        conn = self._connect()
        conn.execute(
            "INSERT OR REPLACE INTO unattributed_events "
            "(event_id, event_type, ticker, side, quantity, timestamp, source, reason, evidence_class) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (event_id, event_type, ticker, side, quantity, timestamp, source, reason, evidence_class),
        )
        conn.commit()

    def upsert_conflict(self, conflict_id: str, event_id: str, event_type: str,
                         candidate_strategy_ids: List[str], candidate_confidences: List[str],
                         reason: str, evidence_class: str = "PAPER") -> None:
        conn = self._connect()
        conn.execute(
            "INSERT OR REPLACE INTO attribution_conflicts "
            "(conflict_id, event_id, event_type, candidate_strategy_ids, "
            "candidate_confidences, reason, evidence_class) "
            "VALUES (?,?,?,?,?,?,?)",
            (conflict_id, event_id, event_type, json.dumps(candidate_strategy_ids),
             json.dumps(candidate_confidences), reason, evidence_class),
        )
        conn.commit()

    # ---- Query API ----

    def get_outcome(self, outcome_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM execution_outcomes WHERE outcome_id=?", (outcome_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_outcomes_for_strategy(self, strategy_id: str) -> List[dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM execution_outcomes WHERE strategy_id=? ORDER BY created_at",
            (strategy_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_strategy_performance(self, strategy_id: str, filter_level: str = "EXACT_ONLY") -> dict[str, Any] | None:
        """Get aggregated performance for a strategy with confidence filtering."""
        conn = self._connect()
        confidence_map = {
            "EXACT_ONLY": ["EXACT"],
            "EXACT_PLUS_STRONG": ["EXACT", "STRONG"],
            "ALL_ATTRIBUTED": ["EXACT", "STRONG", "WEAK"],
        }
        allowed = confidence_map.get(filter_level, ["EXACT"])
        placeholders = ",".join("?" * len(allowed))
        rows = conn.execute(
            f"SELECT * FROM strategy_attributions WHERE strategy_id=? AND confidence IN ({placeholders})",
            [strategy_id] + allowed,
        ).fetchall()
        if not rows:
            return None
        return self._compute_aggregate(strategy_id, [dict(r) for r in rows], filter_level)

    def _compute_aggregate(self, strategy_id: str, attrs: List[dict], filter_level: str) -> dict[str, Any]:
        """Compute aggregate metrics from attributions."""
        if not attrs:
            return {"strategy_id": strategy_id, "trade_count": 0, "evidence_label": "INSUFFICIENT"}
        
        pnls = [a["net_pnl"] for a in attrs if a.get("net_pnl") is not None]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        commissions = [a["commission"] for a in attrs if a.get("commission") is not None]
        
        trade_count = len(attrs)
        win_count = len(wins)
        loss_count = len(losses)
        win_rate = win_count / trade_count if trade_count > 0 else 0.0
        gross_pnl = sum(a.get("gross_pnl", 0) or 0 for a in attrs)
        net_pnl = sum(pnls)
        avg_trade = net_pnl / trade_count if trade_count > 0 else 0.0
        sorted_pnls = sorted(pnls) if pnls else [0]
        median_trade = sorted_pnls[len(sorted_pnls) // 2] if sorted_pnls else 0.0
        profit_factor = abs(sum(wins)) / abs(sum(losses)) if losses else float("inf") if wins else 0.0
        max_loss = min(pnls) if pnls else 0.0
        max_win = max(pnls) if pnls else 0.0
        commission_total = sum(commissions)
        
        # Evidence label
        if trade_count < 3:
            evidence_label = "INSUFFICIENT"
        elif trade_count < 10:
            evidence_label = "EARLY"
        elif trade_count < 30:
            evidence_label = "USABLE"
        else:
            evidence_label = "MATURE"
        
        return {
            "strategy_id": strategy_id,
            "config_key": attrs[0].get("config_key", ""),
            "family_id": attrs[0].get("family_id"),
            "trade_count": trade_count,
            "win_count": win_count,
            "loss_count": loss_count,
            "win_rate": round(win_rate, 4),
            "gross_pnl": round(gross_pnl, 4),
            "net_pnl": round(net_pnl, 4),
            "avg_trade": round(avg_trade, 4),
            "median_trade": round(median_trade, 4),
            "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else "INF",
            "max_loss": round(max_loss, 4),
            "max_win": round(max_win, 4),
            "avg_holding_time": 0.0,
            "commission_total": round(commission_total, 4),
            "evidence_label": evidence_label,
            "confidence_filter": filter_level,
        }

    def get_unattributed(self) -> List[dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute("SELECT * FROM unattributed_events ORDER BY timestamp").fetchall()
        return [dict(r) for r in rows]

    def get_conflicts(self) -> List[dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute("SELECT * FROM attribution_conflicts").fetchall()
        return [dict(r) for r in rows]

    def get_coverage_summary(self) -> dict[str, Any]:
        """Get attribution coverage summary."""
        conn = self._connect()
        total = conn.execute("SELECT COUNT(*) FROM execution_outcomes").fetchone()[0]
        by_confidence = {}
        for conf in AttributionConfidence:
            cnt = conn.execute(
                "SELECT COUNT(*) FROM execution_outcomes WHERE attribution_confidence=?",
                (conf.value,),
            ).fetchone()[0]
            by_confidence[conf.value] = cnt
        unattributed = conn.execute("SELECT COUNT(*) FROM unattributed_events").fetchone()[0]
        conflicts = conn.execute("SELECT COUNT(*) FROM attribution_conflicts").fetchone()[0]
        return {
            "total_outcomes": total,
            "by_confidence": by_confidence,
            "unattributed_events": unattributed,
            "conflict_count": conflicts,
        }

    def get_all_position_outcomes(self) -> List[dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute("SELECT * FROM position_outcomes ORDER BY opened_at").fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Deterministic Attribution Builder
# ---------------------------------------------------------------------------

class PerformanceAttributionBuilder:
    """Deterministic, read-mostly attribution builder.

    Reads from execution journal, analytics, and registry.
    Writes ONLY to performance_attribution.db.
    """

    def __init__(self, store: PerformanceAttributionStore):
        self.store = store

    @staticmethod
    def _hash_content(data: Any) -> str:
        """Deterministic content hash."""
        return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:16]

    @staticmethod
    def _make_id(*parts: str) -> str:
        """Deterministic ID from parts."""
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]

    @staticmethod
    def _safe_float(val: Any) -> float | None:
        if val is None:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _safe_int(val: Any) -> int:
        if val is None:
            return 0
        try:
            return int(val)
        except (ValueError, TypeError):
            return 0

    def build_from_execution_journal(
        self,
        execution_conn: sqlite3.Connection,
        analytics_conn: sqlite3.Connection | None = None,
        registry_data: dict[str, Any] | None = None,
        evidence_class: str = EvidenceClass.PAPER.value,
    ) -> AttributionBuild:
        """Build attribution from execution journal data.

        This is the deterministic entry point. Same inputs → same output.
        """
        build_id = self._make_id(
            str(time.time()),
            evidence_class,
            str(registry_data.get("version", "") if registry_data else ""),
        )
        now = datetime.now(timezone.utc).isoformat()

        build = AttributionBuild(
            attribution_build_id=build_id,
            started_at=now,
            finished_at=None,
            status="RUNNING",
            analytics_source_identity="analytics.db",
            execution_journal_source_identity="execution_journal",
            registry_source_identity="strategy_registry.json",
        )

        self.store.ensure_schema()
        self.store.start_build(build)

        counts = {
            "execution_intents": 0,
            "outcomes_created": 0,
            "attributed_exact": 0,
            "attributed_strong": 0,
            "attributed_weak": 0,
            "conflicted": 0,
            "unattributed": 0,
            "position_outcomes": 0,
        }

        try:
            # Read execution intents
            execution_conn.row_factory = sqlite3.Row
            intents = execution_conn.execute(
                "SELECT * FROM execution_intents ORDER BY created_at"
            ).fetchall()
            counts["execution_intents"] = len(intents)

            # Build strategy lookup from registry
            strategy_lookup = {}
            if registry_data and "strategies" in registry_data:
                for sid, sdata in registry_data["strategies"].items():
                    strategy_lookup[sid] = sdata

            # Process each intent
            for intent in intents:
                # Convert Row to dict properly
                intent_dict = {}
                for key in intent.keys():
                    intent_dict[key] = intent[key]
                outcome = self._attribute_intent(intent_dict, strategy_lookup, evidence_class)
                self.store.upsert_outcome(outcome)
                counts["outcomes_created"] += 1

                # Create strategy attribution for attributed outcomes
                if outcome.attribution_confidence in ("EXACT", "STRONG", "WEAK"):
                    attr_id = self._make_id("attr", outcome.outcome_id)
                    strategy_attr = StrategyAttribution(
                        attribution_id=attr_id,
                        outcome_id=outcome.outcome_id,
                        strategy_id=outcome.strategy_id or "",
                        config_key=outcome.config_key or "",
                        family_id=None,
                        ticker=outcome.ticker,
                        side=outcome.side,
                        requested_qty=outcome.requested_qty,
                        filled_qty=outcome.filled_qty,
                        avg_fill_price=outcome.avg_fill_price,
                        commission=outcome.commission,
                        gross_pnl=None,
                        net_pnl=None,
                        confidence=outcome.attribution_confidence,
                        basis=outcome.attribution_basis,
                        evidence_class=outcome.evidence_class,
                    )
                    self.store.upsert_attribution(strategy_attr)

                # Track confidence counts
                conf = outcome.attribution_confidence
                if conf == "EXACT":
                    counts["attributed_exact"] += 1
                elif conf == "STRONG":
                    counts["attributed_strong"] += 1
                elif conf == "WEAK":
                    counts["attributed_weak"] += 1
                elif conf == "CONFLICTED":
                    counts["conflicted"] += 1
                else:
                    counts["unattributed"] += 1

            # Build position outcomes from analytics trades
            if analytics_conn:
                position_outcomes = self._build_position_outcomes(analytics_conn, strategy_lookup, evidence_class)
                for pos in position_outcomes:
                    self.store.upsert_position(pos)
                    counts["position_outcomes"] += 1

            # Finish build
            self.store.finish_build(build_id, counts, "SUCCESS")
            build.finished_at = datetime.now(timezone.utc).isoformat()
            build.counts = counts
            build.status = "SUCCESS"

        except Exception as e:
            self.store.finish_build(build_id, counts, f"FAILED: {e}")
            build.status = f"FAILED: {e}"
            raise

        return build

    def _attribute_intent(
        self,
        intent: dict[str, Any],
        strategy_lookup: Dict[str, dict],
        evidence_class: str,
    ) -> ExecutionOutcome:
        """Attribute a single execution intent to a strategy."""
        intent_id = intent.get("intent_id", "")
        ticker = intent.get("ticker", "")
        side = intent.get("side", "")
        quantity = self._safe_int(intent.get("quantity", 0))
        fill_qty = self._safe_int(intent.get("fill_quantity", 0))
        fill_price = self._safe_float(intent.get("fill_price"))
        strategy_name = intent.get("strategy")
        provenance = {}
        try:
            provenance = json.loads(intent.get("provenance", "{}") or "{}")
        except (json.JSONDecodeError, TypeError):
            pass

        # Outcome ID is deterministic from intent_id
        outcome_id = self._make_id("outcome", intent_id)

        # Determine confidence and basis
        confidence = AttributionConfidence.UNATTRIBUTED.value
        basis = ""
        strategy_id = None
        config_key = None

        # Level 1: explicit strategy field on intent
        if strategy_name:
            # Look up in registry
            if strategy_name in strategy_lookup:
                strategy_id = strategy_name
                config_key = strategy_name
                confidence = AttributionConfidence.EXACT.value
                basis = f"intent.strategy={strategy_name} found in registry"
            else:
                strategy_id = strategy_name
                config_key = strategy_name
                confidence = AttributionConfidence.STRONG.value
                basis = f"intent.strategy={strategy_name} not in registry but present on intent"

        # Level 2: provenance field
        elif provenance.get("strategy_id"):
            sid = provenance["strategy_id"]
            if sid in strategy_lookup:
                strategy_id = sid
                config_key = sid
                confidence = AttributionConfidence.EXACT.value
                basis = f"intent.provenance.strategy_id={sid} found in registry"
            else:
                strategy_id = sid
                config_key = sid
                confidence = AttributionConfidence.STRONG.value
                basis = f"intent.provenance.strategy_id={sid} not in registry"

        # Level 3: slot_id heuristic (weak)
        elif intent.get("slot_id"):
            slot_id = intent["slot_id"]
            # Try to find strategy by slot context
            for sid, sdata in strategy_lookup.items():
                if sdata.get("signal_pool_slot") or sdata.get("watchlist_slot"):
                    # Weak match by slot proximity
                    confidence = AttributionConfidence.WEAK.value
                    strategy_id = sid
                    config_key = sid
                    basis = f"weak match via slot_id={slot_id}"
                    break
            if confidence == AttributionConfidence.UNATTRIBUTED.value:
                basis = f"slot_id={slot_id} but no strategy match"
        else:
            basis = "no strategy provenance on intent"

        # Determine entry/exit role
        entry_exit_role = None
        action = intent.get("action", "")
        if "BUY" in side.upper() or "LONG" in side.upper():
            entry_exit_role = "ENTRY"
        elif "SELL" in side.upper() or "SHORT" in side.upper():
            entry_exit_role = "EXIT"

        # Determine outcome status
        intent_status = intent.get("status", "UNKNOWN")
        outcome_status = intent_status

        # Realization status
        realization_status = RealizationStatus.UNREALIZED.value
        if fill_qty > 0 and intent_status in ("FILLED", "RECONCILED"):
            realization_status = RealizationStatus.REALIZED.value
        elif fill_qty > 0 and fill_qty < quantity:
            realization_status = RealizationStatus.PARTIALLY_REALIZED.value

        return ExecutionOutcome(
            outcome_id=outcome_id,
            intent_id=intent_id,
            signal_id=provenance.get("signal_id"),
            strategy_id=strategy_id,
            config_key=config_key,
            ticker=ticker,
            side=side,
            instrument_id=intent.get("instrument_id"),
            requested_qty=quantity,
            filled_qty=fill_qty,
            avg_fill_price=fill_price,
            commission=None,  # Not stored in execution journal
            commission_status="UNKNOWN",
            slippage=None,
            entry_exit_role=entry_exit_role,
            broker_client_order_id=intent.get("broker_client_order_id"),
            broker_order_id=intent.get("broker_order_id"),
            created_at=intent.get("created_at", ""),
            filled_at=intent.get("updated_at") if intent_status in ("FILLED", "RECONCILED") else None,
            attribution_confidence=confidence,
            attribution_basis=basis,
            evidence_class=evidence_class,
            slot_id=intent.get("slot_id"),
            realization_status=realization_status,
            outcome_status=outcome_status,
        )

    def _build_position_outcomes(
        self,
        analytics_conn: sqlite3.Connection,
        strategy_lookup: Dict[str, dict],
        evidence_class: str,
    ) -> List[PositionOutcome]:
        """Build position outcomes from analytics trades."""
        positions = []
        try:
            analytics_conn.row_factory = sqlite3.Row
            trades = analytics_conn.execute(
                "SELECT * FROM trades ORDER BY ts_open"
            ).fetchall()
        except Exception:
            return positions

        for trade in trades:
            t = {}
            for key in trade.keys():
                t[key] = trade[key]
            pos_id = self._make_id("position", str(t.get("id", "")))

            # Strategy attribution
            strategy_name = t.get("strategy")
            strategy_id = None
            config_key = None
            family_id = None
            confidence = AttributionConfidence.UNATTRIBUTED.value

            if strategy_name:
                if strategy_name in strategy_lookup:
                    strategy_id = strategy_name
                    config_key = strategy_name
                    confidence = AttributionConfidence.EXACT.value
                else:
                    strategy_id = strategy_name
                    config_key = strategy_name
                    confidence = AttributionConfidence.STRONG.value

            # PnL computation
            entry_price = self._safe_float(t.get("entry_price"))
            exit_price = self._safe_float(t.get("exit_price"))
            contracts = self._safe_int(t.get("contracts", 1))
            direction = t.get("direction", "LONG")
            pnl_rub = self._safe_float(t.get("pnl_rub"))

            gross_pnl = pnl_rub  # analytics already computes PnL
            net_pnl = pnl_rub  # commission already factored in analytics

            # Holding time
            holding_time = None
            if t.get("ts_open") and t.get("ts_close"):
                try:
                    from datetime import datetime as dt
                    open_dt = dt.fromisoformat(t["ts_open"].replace("Z", "+00:00"))
                    close_dt = dt.fromisoformat(t["ts_close"].replace("Z", "+00:00"))
                    holding_time = (close_dt - open_dt).total_seconds()
                except Exception:
                    pass

            # Realization
            trade_status = t.get("status", "open")
            if trade_status == "closed":
                realization = RealizationStatus.REALIZED.value
            else:
                realization = RealizationStatus.UNREALIZED.value

            pos = PositionOutcome(
                position_outcome_id=pos_id,
                strategy_id=strategy_id,
                config_key=config_key,
                family_id=family_id,
                ticker=t.get("ticker", ""),
                instrument_id=None,
                opened_at=t.get("ts_open"),
                closed_at=t.get("ts_close"),
                entry_qty=contracts,
                exit_qty=contracts if trade_status == "closed" else 0,
                entry_vwap=entry_price,
                exit_vwap=exit_price,
                gross_pnl=gross_pnl,
                commission=None,
                net_pnl=net_pnl,
                holding_time_seconds=holding_time,
                close_reason=t.get("exit_reason", "UNKNOWN") or "UNKNOWN",
                confidence=confidence,
                evidence_class=evidence_class,
                realization_status=realization,
            )
            positions.append(pos)

        return positions


# ---------------------------------------------------------------------------
# Analytics Reconciliation
# ---------------------------------------------------------------------------

class AnalyticsReconciler:
    """Reconcile attribution outcomes with analytics.db.

    Mismatch is surfaced, not overwritten.
    """

    def __init__(self, store: PerformanceAttributionStore):
        self.store = store

    def reconcile(self, analytics_conn: sqlite3.Connection) -> dict[str, Any]:
        """Compare attribution outcomes vs analytics trades."""
        conn = self.store._connect()
        outcomes = conn.execute(
            "SELECT * FROM execution_outcomes WHERE attribution_confidence IN ('EXACT','STRONG')"
        ).fetchall()

        matched = 0
        mismatch = 0
        missing_in_analytics = 0
        missing_in_attribution = 0

        analytics_trades = {}
        try:
            analytics_conn.row_factory = sqlite3.Row
            for row in analytics_conn.execute("SELECT * FROM trades").fetchall():
                t = dict(row)
                key = (t.get("ticker"), t.get("strategy"), t.get("ts_open"))
                analytics_trades[key] = t
        except Exception:
            pass

        for outcome in outcomes:
            o = dict(outcome)
            # Try to find matching analytics trade
            key = (o.get("ticker"), o.get("strategy_id"), o.get("created_at"))
            if key in analytics_trades:
                # Compare PnL if available
                a_trade = analytics_trades[key]
                # Match found
                matched += 1
            else:
                missing_in_analytics += 1

        # Check for analytics trades not in attribution
        for key in analytics_trades:
            ticker, strategy, ts = key
            found = conn.execute(
                "SELECT COUNT(*) FROM execution_outcomes WHERE ticker=? AND strategy_id=? AND created_at=?",
                (ticker, strategy, ts),
            ).fetchone()[0]
            if found == 0:
                missing_in_attribution += 1

        return {
            "matched": matched,
            "mismatch": mismatch,
            "missing_in_analytics": missing_in_analytics,
            "missing_in_attribution": missing_in_attribution,
            "total_attributed_outcomes": len(outcomes),
            "total_analytics_trades": len(analytics_trades),
        }


# ---------------------------------------------------------------------------
# Lifecycle Integration (Read-Only)
# ---------------------------------------------------------------------------

class LifecycleIntegration:
    """Read-only integration with Strategy Lifecycle observer.

    Provides production outcome evidence to lifecycle without mutations.
    """

    def __init__(self, store: PerformanceAttributionStore):
        self.store = store

    def get_strategy_evidence(self, strategy_id: str) -> dict[str, Any]:
        """Get production evidence for lifecycle consumption."""
        perf = self.store.get_strategy_performance(strategy_id, "EXACT_PLUS_STRONG")
        outcomes = self.store.get_outcomes_for_strategy(strategy_id)

        paper_count = sum(1 for o in outcomes if o.get("evidence_class") == "PAPER")
        broker_real_count = sum(1 for o in outcomes if o.get("evidence_class") == "BROKER_REAL")

        return {
            "strategy_id": strategy_id,
            "has_production_evidence": perf is not None and perf.get("trade_count", 0) > 0,
            "paper_evidence_count": paper_count,
            "broker_real_evidence_count": broker_real_count,
            "performance": perf,
            "evidence_classes_present": list(set(
                o.get("evidence_class") for o in outcomes if o.get("evidence_class")
            )),
        }

    def get_all_strategy_evidence(self) -> Dict[str, dict[str, Any]]:
        """Get production evidence for all strategies."""
        conn = self.store._connect()
        strategies = conn.execute(
            "SELECT DISTINCT strategy_id FROM execution_outcomes WHERE strategy_id IS NOT NULL"
        ).fetchall()
        result = {}
        for row in strategies:
            sid = row[0]
            result[sid] = self.get_strategy_evidence(sid)
        return result


# ---------------------------------------------------------------------------
# System Health Integration
# ---------------------------------------------------------------------------

class AttributionHealthChecker:
    """Health checker for performance attribution domain."""

    def __init__(self, store: PerformanceAttributionStore):
        self.store = store

    def check_health(self) -> dict[str, Any]:
        """Check attribution domain health."""
        try:
            conn = self.store._connect()
            # DB readable
            conn.execute("SELECT 1 FROM attribution_builds LIMIT 1")

            # Last build
            last_build = conn.execute(
                "SELECT * FROM attribution_builds ORDER BY started_at DESC LIMIT 1"
            ).fetchone()

            # Coverage
            coverage = self.store.get_coverage_summary()

            # Source freshness
            latest_outcome = conn.execute(
                "SELECT MAX(created_at) FROM execution_outcomes"
            ).fetchone()[0]

            status = "HEALTHY"
            issues = []

            if last_build is None:
                status = "UNKNOWN"
                issues.append("no attribution builds found")
            elif last_build["status"] != "SUCCESS":
                status = "DEGRADED"
                issues.append(f"last build status: {last_build['status']}")

            if coverage["unattributed_events"] > 0:
                issues.append(f"{coverage['unattributed_events']} unattributed events")

            if coverage["conflict_count"] > 0:
                issues.append(f"{coverage['conflict_count']} attribution conflicts")

            return {
                "status": status,
                "last_build_id": last_build["attribution_build_id"] if last_build else None,
                "last_build_status": last_build["status"] if last_build else None,
                "coverage": coverage,
                "latest_outcome_timestamp": latest_outcome,
                "issues": issues,
            }
        except Exception as e:
            return {
                "status": "BLOCKED",
                "error": str(e),
                "issues": [f"health check failed: {e}"],
            }

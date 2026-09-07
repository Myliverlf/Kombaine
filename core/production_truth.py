"""Production Truth & Data Integrity — Iteration 19.

Deterministic, read-only production truth layer that answers:
  - Are market datasets valid?
  - Which exact instrument is this?
  - What is its multiplier and currency?
  - What did the broker actually execute?
  - Do local records agree with broker truth?
  - Is the evidence fresh enough to trust?

Change class: CLASS 2 — Production truth / reconciliation / observability.
HARD BOUNDARY: BROKER READ ≠ BROKER MUTATION.
No order placement, cancellation, position close, swap, or live-mode change.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Version constants
# ---------------------------------------------------------------------------
TRUTH_SCHEMA_VERSION = "1.0.0"
TRUTH_BUILDER_VERSION = "1.0.0"
TRUTH_POLICY_VERSION = "1.0.0"
TRUTH_DB_NAME = "production_truth.db"

# Default data path
DEFAULT_DATA_PATH = "/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data"

# Core universe
CORE_UNIVERSE = ("BR", "GAZP", "SBER", "CNY", "EURRUB", "USDRUB", "IMOEX", "LKOH", "Si")
# FIX: NG removed (no data on disk); LKOH(live!)+Si added.
# FIX: LKOH (live position!) and Si were missing from the universe.

# Required timeframes
REQUIRED_TIMEFRAMES = ("15m", "1h")

# Controlled-live/staged-universe readiness uses 60d as the mandatory floor.
# 365d/1095d remain advisory research horizons, not hard launch blockers.
REQUIRED_HORIZONS = (60,)

# Freshness thresholds by timeframe
FRESHNESS_THRESHOLDS = {
    "15m": {"FRESH_HOURS": 2, "AGING_HOURS": 8, "STALE_HOURS": 48},
    "1h": {"FRESH_HOURS": 4, "AGING_HOURS": 24, "STALE_HOURS": 120},
    "1d": {"FRESH_HOURS": 48, "AGING_HOURS": 168, "STALE_HOURS": 720},
}

# Trading session hours (Moscow Exchange, UTC+3 → UTC offsets)
TRADING_SESSION_START_UTC = 7  # 10:00 MSK
TRADING_SESSION_END_UTC = 18  # 21:00 MSK (approximate)

# Weekend days (Python weekday: 5=Saturday, 6=Sunday)
WEEKEND_DAYS = {5, 6}

# Known Russian market holidays (UTC date strings) — extend as needed
MARKET_HOLIDAYS_UTC: Set[str] = set()  # populated at runtime if needed


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TruthConfidence(str, Enum):
    """Overall confidence in the production truth build."""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INSUFFICIENT = "INSUFFICIENT"


class DataQualityStatus(str, Enum):
    """Per-dataset quality verdict."""
    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class FreshnessStatus(str, Enum):
    """Data freshness classification."""
    FRESH = "FRESH"
    AGING = "AGING"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class CoverageStatus(str, Enum):
    """Historical coverage completeness."""
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    INSUFFICIENT = "INSUFFICIENT"
    MISSING = "MISSING"


class BrokerMethodClass(str, Enum):
    """Classification of broker adapter methods."""
    READ_ONLY = "READ_ONLY"
    MUTATING = "MUTATING"
    UNKNOWN = "UNKNOWN"


class MismatchClass(str, Enum):
    """Reconciliation mismatch taxonomy."""
    BROKER_POSITION_MISSING_LOCAL = "BROKER_POSITION_MISSING_LOCAL"
    LOCAL_POSITION_MISSING_BROKER = "LOCAL_POSITION_MISSING_BROKER"
    BROKER_FILL_MISSING_LOCAL = "BROKER_FILL_MISSING_LOCAL"
    LOCAL_FILL_MISSING_BROKER = "LOCAL_FILL_MISSING_BROKER"
    QUANTITY_MISMATCH = "QUANTITY_MISMATCH"
    PRICE_MISMATCH = "PRICE_MISMATCH"
    COMMISSION_MISMATCH = "COMMISSION_MISMATCH"
    INSTRUMENT_ID_MISMATCH = "INSTRUMENT_ID_MISMATCH"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    ORDER_STATE_MISMATCH = "ORDER_STATE_MISMATCH"
    ATTRIBUTION_MISSING = "ATTRIBUTION_MISSING"
    DUPLICATE_OPERATION = "DUPLICATE_OPERATION"
    UNKNOWN = "UNKNOWN"


class MismatchSeverity(str, Enum):
    """Severity of a reconciliation mismatch."""
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class OperationType(str, Enum):
    """Broker operation taxonomy."""
    BUY = "BUY"
    SELL = "SELL"
    COMMISSION = "COMMISSION"
    TAX = "TAX"
    DIVIDEND = "DIVIDEND"
    COUPON = "COUPON"
    FEE = "FEE"
    MARGIN_FUNDING = "MARGIN_FUNDING"
    CASH_IN = "CASH_IN"
    CASH_OUT = "CASH_OUT"
    OTHER = "OTHER"


class PositionReconstruction(str, Enum):
    """Position reconstruction status."""
    RECONSTRUCTED = "RECONSTRUCTED"
    PARTIAL = "PARTIAL"
    INCOMPARABLE = "INCOMPARABLE"


class TruthBuildStatus(str, Enum):
    """Truth build lifecycle status."""
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class TruthBuild:
    """Immutable record of a production truth run."""
    truth_build_id: str
    started_at: str
    finished_at: Optional[str] = None
    status: str = TruthBuildStatus.RUNNING.value
    code_version: str = TRUTH_BUILDER_VERSION
    data_source_hashes: Dict[str, str] = field(default_factory=dict)
    broker_account_hash: Optional[str] = None
    broker_snapshot_timestamp: Optional[str] = None
    metadata_source_version: str = ""
    reconciliation_policy_version: str = TRUTH_POLICY_VERSION
    counts: Dict[str, int] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DatasetIdentity:
    """Canonical identity for a market dataset."""
    instrument: str
    timeframe: str
    horizon_days: int
    date_range_start: Optional[str] = None
    date_range_end: Optional[str] = None
    row_count: int = 0
    file_hash: str = ""
    source_path: str = ""
    quality_status: str = DataQualityStatus.UNKNOWN.value
    coverage_status: str = CoverageStatus.MISSING.value
    freshness: str = FreshnessStatus.UNKNOWN.value

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DataQualityResult:
    """Result of a data quality check on a single dataset."""
    instrument: str
    timeframe: str
    horizon_days: int
    file_exists: bool = False
    schema_valid: bool = False
    timestamps_parseable: bool = False
    timestamps_monotonic: bool = False
    no_duplicate_timestamps: bool = False
    no_gaps_beyond_expected: bool = False
    no_future_bars: bool = False
    ohlc_consistent: bool = False
    no_negative_prices: bool = False
    no_zero_volume_anomaly: bool = False
    not_stale: bool = False
    horizon_coverage: str = CoverageStatus.MISSING.value
    instrument_identity_correct: bool = False
    timeframe_identity_correct: bool = False
    overall_status: str = DataQualityStatus.UNKNOWN.value
    file_hash: str = ""
    row_count: int = 0
    first_timestamp: Optional[str] = None
    last_timestamp: Optional[str] = None
    gap_count: int = 0
    duplicate_count: int = 0
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DataGap:
    """Record of a detected data gap."""
    instrument: str
    timeframe: str
    gap_start: str
    gap_end: str
    expected_bars: int
    actual_bars: int
    gap_type: str = "UNKNOWN"  # WEEKEND, HOLIDAY, SESSION_BOUNDARY, TRUE_MISSING

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class InstrumentMetadata:
    """Canonical instrument metadata."""
    canonical_symbol: str
    broker_instrument_id: Optional[str] = None
    figi: Optional[str] = None
    instrument_type: str = "FUTURES"
    exchange: str = "MOEX"
    currency: str = "RUB"
    lot_size: int = 1
    price_step: float = 0.01
    price_scale: int = 2
    contract_multiplier: float = 1.0
    expiration: Optional[str] = None
    underlying: Optional[str] = None
    source: str = "MANUAL"
    last_verified: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BrokerSnapshot:
    """Immutable read-only snapshot of broker state."""
    snapshot_id: str
    timestamp_iso: str
    account_id_hash: Optional[str] = None
    positions: List[Dict[str, Any]] = field(default_factory=list)
    money: List[Dict[str, Any]] = field(default_factory=list)
    fills: List[Dict[str, Any]] = field(default_factory=list)
    operations: List[Dict[str, Any]] = field(default_factory=list)
    commissions_total: float = 0.0
    source: str = "READ_ONLY"
    raw_references: Dict[str, Any] = field(default_factory=dict)

    # Prove READ-ONLY: list of methods that were called
    methods_called: List[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BrokerOperation:
    """A single broker operation (fill, commission, etc.)."""
    operation_id: str
    broker_operation_id: Optional[str] = None
    operation_type: str = OperationType.OTHER.value
    instrument: str = ""
    quantity: int = 0
    price: float = 0.0
    amount: float = 0.0
    commission: float = 0.0
    currency: str = "RUB"
    timestamp: str = ""
    parent_order_id: Optional[str] = None
    is_partial_fill: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReconciliationResult:
    """Result of a reconciliation run."""
    reconciliation_id: str
    truth_build_id: str
    timestamp_iso: str
    positions_compared: int = 0
    fills_compared: int = 0
    operations_compared: int = 0
    matches: int = 0
    mismatches: int = 0
    mismatch_details: List[Dict[str, Any]] = field(default_factory=list)
    unattributed_count: int = 0
    status: str = "COMPLETED"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReconciliationMismatch:
    """A single reconciliation mismatch."""
    mismatch_id: str
    mismatch_class: str = MismatchClass.UNKNOWN.value
    severity: str = MismatchSeverity.WARNING.value
    broker_value: Any = None
    local_value: Any = None
    instrument: str = ""
    details: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BackfillCheckpoint:
    """Checkpoint for historical backfill."""
    checkpoint_id: str
    account_hash: str
    date_from: str
    date_to: str
    last_cursor: Optional[str] = None
    operation_count: int = 0
    checksum: str = ""
    status: str = "RUNNING"
    started_at: str = ""
    finished_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Broker method classification — READ-ONLY allowlist
# ---------------------------------------------------------------------------

BROKER_METHOD_CLASSIFICATION: Dict[str, BrokerMethodClass] = {
    # READ-ONLY methods (safe for Iteration 19)
    "get_accounts": BrokerMethodClass.READ_ONLY,
    "get_portfolio": BrokerMethodClass.READ_ONLY,
    "get_positions": BrokerMethodClass.READ_ONLY,
    "get_operations": BrokerMethodClass.READ_ONLY,
    "get_operations_by_cursor": BrokerMethodClass.READ_ONLY,
    "get_orders": BrokerMethodClass.READ_ONLY,
    "get_orders_history": BrokerMethodClass.READ_ONLY,
    "get_trades": BrokerMethodClass.READ_ONLY,
    "get_instruments": BrokerMethodClass.READ_ONLY,
    "get_instrument_by": BrokerMethodClass.READ_ONLY,
    "get_candles": BrokerMethodClass.READ_ONLY,
    "get_last_prices": BrokerMethodClass.READ_ONLY,
    "get_order_book": BrokerMethodClass.READ_ONLY,
    "get_margins": BrokerMethodClass.READ_ONLY,
    "get_portfolio_margins": BrokerMethodClass.READ_ONLY,
    # MUTATING methods (BLOCKED in Iteration 19)
    "post_order": BrokerMethodClass.MUTATING,
    "cancel_order": BrokerMethodClass.MUTATING,
    "replace_order": BrokerMethodClass.MUTATING,
    "set_order_type": BrokerMethodClass.MUTATING,
    "close_position": BrokerMethodClass.MUTATING,
    "open_position": BrokerMethodClass.MUTATING,
    "post_portfolio_order": BrokerMethodClass.MUTATING,
    "post_stop_order": BrokerMethodClass.MUTATING,
}

# The allowlist — only these may be called in Iteration 19
READ_ONLY_ALLOWLIST = frozenset(
    k for k, v in BROKER_METHOD_CLASSIFICATION.items()
    if v == BrokerMethodClass.READ_ONLY
)


def is_broker_method_read_only(method_name: str) -> bool:
    """Prove a broker method is read-only. Returns True only for allowlisted methods."""
    classification = BROKER_METHOD_CLASSIFICATION.get(method_name)
    if classification is None:
        return False  # UNKNOWN methods are NOT callable
    return classification == BrokerMethodClass.READ_ONLY


# ---------------------------------------------------------------------------
# SQLite schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """CREATE TABLE IF NOT EXISTS truth_builds (
    truth_build_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'RUNNING',
    code_version TEXT NOT NULL DEFAULT '1.0.0',
    data_source_hashes TEXT DEFAULT '{}',
    broker_account_hash TEXT,
    broker_snapshot_timestamp TEXT,
    metadata_source_version TEXT DEFAULT '',
    reconciliation_policy_version TEXT DEFAULT '1.0.0',
    counts TEXT DEFAULT '{}',
    warnings TEXT DEFAULT '[]',
    errors TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS data_quality_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    truth_build_id TEXT NOT NULL,
    instrument TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    horizon_days INTEGER NOT NULL,
    file_exists INTEGER DEFAULT 0,
    schema_valid INTEGER DEFAULT 0,
    timestamps_parseable INTEGER DEFAULT 0,
    timestamps_monotonic INTEGER DEFAULT 0,
    no_duplicate_timestamps INTEGER DEFAULT 0,
    no_gaps_beyond_expected INTEGER DEFAULT 0,
    no_future_bars INTEGER DEFAULT 0,
    ohlc_consistent INTEGER DEFAULT 0,
    no_negative_prices INTEGER DEFAULT 0,
    no_zero_volume_anomaly INTEGER DEFAULT 0,
    not_stale INTEGER DEFAULT 0,
    horizon_coverage TEXT DEFAULT 'MISSING',
    instrument_identity_correct INTEGER DEFAULT 0,
    timeframe_identity_correct INTEGER DEFAULT 0,
    overall_status TEXT DEFAULT 'UNKNOWN',
    file_hash TEXT DEFAULT '',
    row_count INTEGER DEFAULT 0,
    first_timestamp TEXT,
    last_timestamp TEXT,
    gap_count INTEGER DEFAULT 0,
    duplicate_count INTEGER DEFAULT 0,
    details TEXT DEFAULT '{}');

CREATE TABLE IF NOT EXISTS data_gaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    truth_build_id TEXT NOT NULL,
    instrument TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    gap_start TEXT NOT NULL,
    gap_end TEXT NOT NULL,
    expected_bars INTEGER DEFAULT 0,
    actual_bars INTEGER DEFAULT 0,
    gap_type TEXT DEFAULT 'UNKNOWN');

CREATE TABLE IF NOT EXISTS instrument_metadata (
    canonical_symbol TEXT PRIMARY KEY,
    broker_instrument_id TEXT,
    figi TEXT,
    instrument_type TEXT DEFAULT 'FUTURES',
    exchange TEXT DEFAULT 'MOEX',
    currency TEXT DEFAULT 'RUB',
    lot_size INTEGER DEFAULT 1,
    price_step REAL DEFAULT 0.01,
    price_scale INTEGER DEFAULT 2,
    contract_multiplier REAL DEFAULT 1.0,
    expiration TEXT,
    underlying TEXT,
    source TEXT DEFAULT 'MANUAL',
    last_verified TEXT
);

CREATE TABLE IF NOT EXISTS broker_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    truth_build_id TEXT,
    timestamp_iso TEXT NOT NULL,
    account_id_hash TEXT,
    positions TEXT DEFAULT '[]',
    money TEXT DEFAULT '[]',
    fills TEXT DEFAULT '[]',
    operations TEXT DEFAULT '[]',
    commissions_total REAL DEFAULT 0.0,
    source TEXT DEFAULT 'READ_ONLY',
    raw_references TEXT DEFAULT '{}',
    methods_called TEXT DEFAULT '[]');

CREATE TABLE IF NOT EXISTS broker_operations (
    operation_id TEXT PRIMARY KEY,
    truth_build_id TEXT,
    broker_operation_id TEXT,
    operation_type TEXT DEFAULT 'OTHER',
    instrument TEXT DEFAULT '',
    quantity INTEGER DEFAULT 0,
    price REAL DEFAULT 0.0,
    amount REAL DEFAULT 0.0,
    commission REAL DEFAULT 0.0,
    currency TEXT DEFAULT 'RUB',
    timestamp TEXT DEFAULT '',
    parent_order_id TEXT,
    is_partial_fill INTEGER DEFAULT 0);

CREATE TABLE IF NOT EXISTS broker_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    truth_build_id TEXT,
    instrument TEXT NOT NULL,
    quantity INTEGER DEFAULT 0,
    average_price REAL DEFAULT 0.0,
    current_price REAL DEFAULT 0.0,
    expected_yield REAL DEFAULT 0.0,
    currency TEXT DEFAULT 'RUB');

CREATE TABLE IF NOT EXISTS broker_money (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    truth_build_id TEXT,
    currency TEXT NOT NULL,
    balance REAL DEFAULT 0.0,
    blocked REAL DEFAULT 0.0);

CREATE TABLE IF NOT EXISTS broker_fills (
    fill_id TEXT PRIMARY KEY,
    truth_build_id TEXT,
    broker_fill_id TEXT,
    order_id TEXT,
    instrument TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER DEFAULT 0,
    price REAL DEFAULT 0.0,
    commission REAL DEFAULT 0.0,
    currency TEXT DEFAULT 'RUB',
    timestamp TEXT DEFAULT '');

CREATE TABLE IF NOT EXISTS reconciliation_results (
    reconciliation_id TEXT PRIMARY KEY,
    truth_build_id TEXT NOT NULL,
    timestamp_iso TEXT NOT NULL,
    positions_compared INTEGER DEFAULT 0,
    fills_compared INTEGER DEFAULT 0,
    operations_compared INTEGER DEFAULT 0,
    matches INTEGER DEFAULT 0,
    mismatches INTEGER DEFAULT 0,
    mismatch_details TEXT DEFAULT '[]',
    unattributed_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'COMPLETED');

CREATE TABLE IF NOT EXISTS reconciliation_mismatches (
    mismatch_id TEXT PRIMARY KEY,
    reconciliation_id TEXT NOT NULL,
    mismatch_class TEXT NOT NULL,
    severity TEXT DEFAULT 'WARNING',
    broker_value TEXT,
    local_value TEXT,
    instrument TEXT DEFAULT '',
    details TEXT DEFAULT '');

CREATE TABLE IF NOT EXISTS backfill_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    truth_build_id TEXT,
    account_hash TEXT NOT NULL,
    date_from TEXT NOT NULL,
    date_to TEXT NOT NULL,
    last_cursor TEXT,
    operation_count INTEGER DEFAULT 0,
    checksum TEXT DEFAULT '',
    status TEXT DEFAULT 'RUNNING',
    started_at TEXT NOT NULL,
    finished_at TEXT);

CREATE TABLE IF NOT EXISTS truth_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    truth_build_id TEXT,
    event_type TEXT NOT NULL,
    event_data TEXT DEFAULT '{}',
    timestamp_iso TEXT NOT NULL,
    fingerprint TEXT
);

CREATE INDEX IF NOT EXISTS idx_dq_instrument ON data_quality_results(instrument, timeframe, horizon_days);
CREATE INDEX IF NOT EXISTS idx_dq_build ON data_quality_results(truth_build_id);
CREATE INDEX IF NOT EXISTS idx_gaps_build ON data_gaps(truth_build_id);
CREATE INDEX IF NOT EXISTS idx_broker_ops_build ON broker_operations(truth_build_id);
CREATE INDEX IF NOT EXISTS idx_recon_build ON reconciliation_results(truth_build_id);
CREATE INDEX IF NOT EXISTS idx_recon_mismatch ON reconciliation_mismatches(reconciliation_id);
CREATE INDEX IF NOT EXISTS idx_backfill_build ON backfill_checkpoints(truth_build_id);
CREATE INDEX IF NOT EXISTS idx_events_build ON truth_events(truth_build_id);
CREATE INDEX IF NOT EXISTS idx_events_fingerprint ON truth_events(fingerprint);"""


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generate_id(prefix: str = "tb") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _hash_file(path: str) -> str:
    """SHA-256 hash of file contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _hash_string(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _parse_timestamp(ts_str: str) -> Optional[datetime]:
    """Parse ISO timestamp string to datetime."""
    try:
        # Handle timezone-aware ISO format
        if "+" in ts_str or ts_str.endswith("Z"):
            ts_str_clean = ts_str.replace("Z", "+00:00")
            return datetime.fromisoformat(ts_str_clean)
        return datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _is_weekend(dt: datetime) -> bool:
    return dt.weekday() in WEEKEND_DAYS


def _is_trading_hours(dt: datetime) -> bool:
    """Check if a datetime falls within approximate MOEX trading hours."""
    hour = dt.hour
    return TRADING_SESSION_START_UTC <= hour < TRADING_SESSION_END_UTC


def _redact_secrets(text: str) -> str:
    """Redact potential secrets from text for Telegram forwarding."""
    text = re.sub(r'(token|key|secret|password|api_key|apikey)[\s:=]+\S+', r'\1=***REDACTED***', text, flags=re.IGNORECASE)
    text = re.sub(r'[A-Za-z0-9]{20,}', '***REDACTED_TOKEN***', text)
    return text


# ---------------------------------------------------------------------------
# ProductionTruthStore — SQLite state/production_truth.db
# ---------------------------------------------------------------------------

class ProductionTruthStore:
    """SQLite-backed production truth evidence store.

    This is a READ-MODEL / evidence store, not broker truth itself.
    """

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            project_root = Path(__file__).resolve().parent.parent
            db_path = str(project_root / "state" / TRUTH_DB_NAME)
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- Truth builds --
    def save_truth_build(self, build: TruthBuild) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO truth_builds
               (truth_build_id, started_at, finished_at, status, code_version,
                data_source_hashes, broker_account_hash, broker_snapshot_timestamp,
                metadata_source_version, reconciliation_policy_version, counts,
                warnings, errors)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (build.truth_build_id, build.started_at, build.finished_at,
             build.status, build.code_version,
             json.dumps(build.data_source_hashes), build.broker_account_hash,
             build.broker_snapshot_timestamp, build.metadata_source_version,
             build.reconciliation_policy_version, json.dumps(build.counts),
             json.dumps(build.warnings), json.dumps(build.errors))
        )
        self._conn.commit()

    def get_truth_build(self, truth_build_id: str) -> Optional[TruthBuild]:
        row = self._conn.execute(
            "SELECT * FROM truth_builds WHERE truth_build_id = ?", (truth_build_id,)
        ).fetchone()
        if row is None:
            return None
        return TruthBuild(
            truth_build_id=row["truth_build_id"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            status=row["status"],
            code_version=row["code_version"],
            data_source_hashes=json.loads(row["data_source_hashes"] or "{}"),
            broker_account_hash=row["broker_account_hash"],
            broker_snapshot_timestamp=row["broker_snapshot_timestamp"],
            metadata_source_version=row["metadata_source_version"],
            reconciliation_policy_version=row["reconciliation_policy_version"],
            counts=json.loads(row["counts"] or "{}"),
            warnings=json.loads(row["warnings"] or "[]"),
            errors=json.loads(row["errors"] or "[]"),
        )

    def get_latest_truth_build(self) -> Optional[TruthBuild]:
        row = self._conn.execute(
            "SELECT truth_build_id FROM truth_builds ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return self.get_truth_build(row["truth_build_id"])

    # -- Data quality --
    def save_data_quality(self, build_id: str, result: DataQualityResult) -> None:
        self._conn.execute(
            """INSERT INTO data_quality_results
               (truth_build_id, instrument, timeframe, horizon_days, file_exists,
                schema_valid, timestamps_parseable, timestamps_monotonic,
                no_duplicate_timestamps, no_gaps_beyond_expected, no_future_bars,
                ohlc_consistent, no_negative_prices, no_zero_volume_anomaly,
                not_stale, horizon_coverage, instrument_identity_correct,
                timeframe_identity_correct, overall_status, file_hash, row_count,
                first_timestamp, last_timestamp, gap_count, duplicate_count, details)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (build_id, result.instrument, result.timeframe, result.horizon_days,
             int(result.file_exists), int(result.schema_valid),
             int(result.timestamps_parseable), int(result.timestamps_monotonic),
             int(result.no_duplicate_timestamps), int(result.no_gaps_beyond_expected),
             int(result.no_future_bars), int(result.ohlc_consistent),
             int(result.no_negative_prices), int(result.no_zero_volume_anomaly),
             int(result.not_stale), result.horizon_coverage,
             int(result.instrument_identity_correct), int(result.timeframe_identity_correct),
             result.overall_status, result.file_hash, result.row_count,
             result.first_timestamp, result.last_timestamp, result.gap_count,
             result.duplicate_count, json.dumps(result.details))
        )
        self._conn.commit()

    def get_data_quality(self, instrument: str, timeframe: str,
                         horizon_days: int, build_id: Optional[str] = None) -> List[DataQualityResult]:
        if build_id:
            rows = self._conn.execute(
                """SELECT * FROM data_quality_results
                   WHERE truth_build_id = ? AND instrument = ? AND timeframe = ? AND horizon_days = ?""",
                (build_id, instrument, timeframe, horizon_days)
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT * FROM data_quality_results
                   WHERE instrument = ? AND timeframe = ? AND horizon_days = ?
                   ORDER BY id DESC LIMIT 1""",
                (instrument, timeframe, horizon_days)
            ).fetchall()
        return [self._row_to_dq(r) for r in rows]

    def _row_to_dq(self, row: sqlite3.Row) -> DataQualityResult:
        return DataQualityResult(
            instrument=row["instrument"], timeframe=row["timeframe"],
            horizon_days=row["horizon_days"], file_exists=bool(row["file_exists"]),
            schema_valid=bool(row["schema_valid"]),
            timestamps_parseable=bool(row["timestamps_parseable"]),
            timestamps_monotonic=bool(row["timestamps_monotonic"]),
            no_duplicate_timestamps=bool(row["no_duplicate_timestamps"]),
            no_gaps_beyond_expected=bool(row["no_gaps_beyond_expected"]),
            no_future_bars=bool(row["no_future_bars"]),
            ohlc_consistent=bool(row["ohlc_consistent"]),
            no_negative_prices=bool(row["no_negative_prices"]),
            no_zero_volume_anomaly=bool(row["no_zero_volume_anomaly"]),
            not_stale=bool(row["not_stale"]),
            horizon_coverage=row["horizon_coverage"],
            instrument_identity_correct=bool(row["instrument_identity_correct"]),
            timeframe_identity_correct=bool(row["timeframe_identity_correct"]),
            overall_status=row["overall_status"],
            file_hash=row["file_hash"], row_count=row["row_count"],
            first_timestamp=row["first_timestamp"],
            last_timestamp=row["last_timestamp"],
            gap_count=row["gap_count"], duplicate_count=row["duplicate_count"],
            details=json.loads(row["details"] or "{}"),
        )

    # -- Instrument metadata --
    def save_instrument_metadata(self, meta: InstrumentMetadata) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO instrument_metadata
               (canonical_symbol, broker_instrument_id, figi, instrument_type,
                exchange, currency, lot_size, price_step, price_scale,
                contract_multiplier, expiration, underlying, source, last_verified)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (meta.canonical_symbol, meta.broker_instrument_id, meta.figi,
             meta.instrument_type, meta.exchange, meta.currency, meta.lot_size,
             meta.price_step, meta.price_scale, meta.contract_multiplier,
             meta.expiration, meta.underlying, meta.source, meta.last_verified)
        )
        self._conn.commit()

    def get_instrument_metadata(self, symbol: str) -> Optional[InstrumentMetadata]:
        row = self._conn.execute(
            "SELECT * FROM instrument_metadata WHERE canonical_symbol = ?", (symbol,)
        ).fetchone()
        if row is None:
            return None
        return InstrumentMetadata(
            canonical_symbol=row["canonical_symbol"],
            broker_instrument_id=row["broker_instrument_id"],
            figi=row["figi"], instrument_type=row["instrument_type"],
            exchange=row["exchange"], currency=row["currency"],
            lot_size=row["lot_size"], price_step=row["price_step"],
            price_scale=row["price_scale"],
            contract_multiplier=row["contract_multiplier"],
            expiration=row["expiration"], underlying=row["underlying"],
            source=row["source"], last_verified=row["last_verified"],
        )

    # -- Broker snapshots --
    def save_broker_snapshot(self, snapshot: BrokerSnapshot) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO broker_snapshots
               (snapshot_id, truth_build_id, timestamp_iso, account_id_hash,
                positions, money, fills, operations, commissions_total,
                source, raw_references, methods_called)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (snapshot.snapshot_id, None, snapshot.timestamp_iso,
             snapshot.account_id_hash,
             json.dumps(snapshot.positions), json.dumps(snapshot.money),
             json.dumps(snapshot.fills), json.dumps(snapshot.operations),
             snapshot.commissions_total, snapshot.source,
             json.dumps(snapshot.raw_references),
             json.dumps(snapshot.methods_called))
        )
        self._conn.commit()

    def get_broker_snapshot(self, snapshot_id: str) -> Optional[BrokerSnapshot]:
        row = self._conn.execute(
            "SELECT * FROM broker_snapshots WHERE snapshot_id = ?", (snapshot_id,)
        ).fetchone()
        if row is None:
            return None
        return BrokerSnapshot(
            snapshot_id=row["snapshot_id"], timestamp_iso=row["timestamp_iso"],
            account_id_hash=row["account_id_hash"],
            positions=json.loads(row["positions"] or "[]"),
            money=json.loads(row["money"] or "[]"),
            fills=json.loads(row["fills"] or "[]"),
            operations=json.loads(row["operations"] or "[]"),
            commissions_total=row["commissions_total"],
            source=row["source"],
            raw_references=json.loads(row["raw_references"] or "{}"),
            methods_called=json.loads(row["methods_called"] or "[]"),
        )

    def get_latest_broker_snapshot(self) -> Optional[BrokerSnapshot]:
        row = self._conn.execute(
            "SELECT snapshot_id FROM broker_snapshots ORDER BY timestamp_iso DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return self.get_broker_snapshot(row["snapshot_id"])

    # -- Broker operations --
    def save_broker_operation(self, build_id: str, op: BrokerOperation) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO broker_operations
               (operation_id, truth_build_id, broker_operation_id, operation_type,
                instrument, quantity, price, amount, commission, currency,
                timestamp, parent_order_id, is_partial_fill)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (op.operation_id, build_id, op.broker_operation_id,
             op.operation_type, op.instrument, op.quantity, op.price,
             op.amount, op.commission, op.currency, op.timestamp,
             op.parent_order_id, int(op.is_partial_fill))
        )
        self._conn.commit()

    def get_broker_operations(self, build_id: Optional[str] = None) -> List[BrokerOperation]:
        if build_id:
            rows = self._conn.execute(
                "SELECT * FROM broker_operations WHERE truth_build_id = ?", (build_id,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM broker_operations ORDER BY timestamp DESC"
            ).fetchall()
        return [BrokerOperation(
            operation_id=r["operation_id"], broker_operation_id=r["broker_operation_id"],
            operation_type=r["operation_type"], instrument=r["instrument"],
            quantity=r["quantity"], price=r["price"], amount=r["amount"],
            commission=r["commission"], currency=r["currency"],
            timestamp=r["timestamp"], parent_order_id=r["parent_order_id"],
            is_partial_fill=bool(r["is_partial_fill"]),
        ) for r in rows]

    # -- Reconciliation --
    def save_reconciliation_result(self, result: ReconciliationResult) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO reconciliation_results
               (reconciliation_id, truth_build_id, timestamp_iso, positions_compared,
                fills_compared, operations_compared, matches, mismatches,
                mismatch_details, unattributed_count, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (result.reconciliation_id, result.truth_build_id, result.timestamp_iso,
             result.positions_compared, result.fills_compared,
             result.operations_compared, result.matches, result.mismatches,
             json.dumps(result.mismatch_details), result.unattributed_count,
             result.status)
        )
        self._conn.commit()

    def save_mismatch(self, recon_id: str, mismatch: ReconciliationMismatch) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO reconciliation_mismatches
               (mismatch_id, reconciliation_id, mismatch_class, severity,
                broker_value, local_value, instrument, details)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (mismatch.mismatch_id, recon_id, mismatch.mismatch_class,
             mismatch.severity, str(mismatch.broker_value),
             str(mismatch.local_value), mismatch.instrument, mismatch.details)
        )
        self._conn.commit()

    def get_reconciliation_summary(self, build_id: Optional[str] = None) -> Dict[str, Any]:
        if build_id:
            rows = self._conn.execute(
                "SELECT * FROM reconciliation_results WHERE truth_build_id = ?",
                (build_id,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM reconciliation_results ORDER BY timestamp_iso DESC LIMIT 1"
            ).fetchall()
        if not rows:
            return {"status": "NO_RECONCILIATION_RUN"}
        r = rows[0] if len(rows) == 1 else rows[0]
        return {
            "reconciliation_id": r["reconciliation_id"],
            "positions_compared": r["positions_compared"],
            "fills_compared": r["fills_compared"],
            "operations_compared": r["operations_compared"],
            "matches": r["matches"],
            "mismatches": r["mismatches"],
            "unattributed_count": r["unattributed_count"],
            "status": r["status"],
        }

    # -- Data gaps --
    def save_data_gap(self, build_id: str, gap: DataGap) -> None:
        self._conn.execute(
            """INSERT INTO data_gaps
               (truth_build_id, instrument, timeframe, gap_start, gap_end,
                expected_bars, actual_bars, gap_type)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (build_id, gap.instrument, gap.timeframe, gap.gap_start,
             gap.gap_end, gap.expected_bars, gap.actual_bars, gap.gap_type)
        )
        self._conn.commit()

    # -- Truth events --
    def save_truth_event(self, build_id: str, event_type: str,
                         event_data: Dict[str, Any], fingerprint: Optional[str] = None) -> None:
        if fingerprint is None:
            fingerprint = _hash_string(f"{event_type}:{json.dumps(event_data, sort_keys=True)}")
        self._conn.execute(
            """INSERT INTO truth_events
               (truth_build_id, event_type, event_data, timestamp_iso, fingerprint)
               VALUES (?, ?, ?, ?, ?)""",
            (build_id, event_type, json.dumps(event_data), _now_iso(), fingerprint)
        )
        self._conn.commit()

    def is_event_duplicate(self, fingerprint: str, cooldown_seconds: int = 300) -> bool:
        """Check if an event fingerprint was already emitted within cooldown."""
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=cooldown_seconds)).isoformat()
        row = self._conn.execute(
            "SELECT 1 FROM truth_events WHERE fingerprint = ? AND timestamp_iso > ?",
            (fingerprint, cutoff)
        ).fetchone()
        return row is not None

    # -- Backfill checkpoints --
    def save_backfill_checkpoint(self, checkpoint: BackfillCheckpoint) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO backfill_checkpoints
               (checkpoint_id, truth_build_id, account_hash, date_from, date_to,
                last_cursor, operation_count, checksum, status, started_at, finished_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (checkpoint.checkpoint_id, None, checkpoint.account_hash,
             checkpoint.date_from, checkpoint.date_to, checkpoint.last_cursor,
             checkpoint.operation_count, checkpoint.checksum, checkpoint.status,
             checkpoint.started_at, checkpoint.finished_at)
        )
        self._conn.commit()

    def get_backfill_checkpoint(self, account_hash: str) -> Optional[BackfillCheckpoint]:
        row = self._conn.execute(
            """SELECT * FROM backfill_checkpoints
               WHERE account_hash = ? ORDER BY started_at DESC LIMIT 1""",
            (account_hash,)
        ).fetchone()
        if row is None:
            return None
        return BackfillCheckpoint(
            checkpoint_id=row["checkpoint_id"],
            account_hash=row["account_hash"],
            date_from=row["date_from"],
            date_to=row["date_to"],
            last_cursor=row["last_cursor"],
            operation_count=row["operation_count"],
            checksum=row["checksum"],
            status=row["status"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )


# ---------------------------------------------------------------------------
# DataQualityGuardian — deterministic quality checks per instrument/timeframe
# ---------------------------------------------------------------------------

class DataQualityGuardian:
    """Deterministic data quality checks for production market datasets.

    Checks: schema, timestamp parseability, monotonicity, gaps, duplicates,
    OHLC consistency, negative prices, freshness, horizon coverage.
    """

    def __init__(self, data_path: Optional[str] = None):
        self.data_path = data_path or DEFAULT_DATA_PATH

    def _expected_filename(self, instrument: str, horizon: int, timeframe: str) -> str:
        return f"{instrument}_{horizon}d_{timeframe}_continuous.csv"

    def _file_path(self, instrument: str, horizon: int, timeframe: str) -> str:
        return os.path.join(self.data_path, self._expected_filename(instrument, horizon, timeframe))

    def check_dataset(self, instrument: str, timeframe: str, horizon_days: int,
                      now: Optional[datetime] = None) -> DataQualityResult:
        """Run full quality check on a single dataset. Purely deterministic."""
        if now is None:
            now = datetime.now(timezone.utc)

        result = DataQualityResult(
            instrument=instrument, timeframe=timeframe, horizon_days=horizon_days
        )

        # F1: file exists?
        fpath = self._file_path(instrument, horizon_days, timeframe)
        result.file_exists = os.path.isfile(fpath)
        if not result.file_exists:
            result.overall_status = DataQualityStatus.FAIL.value
            result.details["error"] = f"File not found: {fpath}"
            result.horizon_coverage = CoverageStatus.MISSING.value
            return result

        # Hash
        result.file_hash = _hash_file(fpath)

        # Read CSV
        try:
            rows = self._read_csv(fpath)
        except Exception as e:
            result.details["error"] = f"CSV read error: {e}"
            result.overall_status = DataQualityStatus.FAIL.value
            return result

        result.row_count = len(rows)
        if result.row_count == 0:
            result.overall_status = DataQualityStatus.FAIL.value
            result.details["error"] = "Empty dataset"
            return result

        # F2: Schema check — must have time,open,high,low,close,volume
        required_cols = {"time", "open", "high", "low", "close", "volume"}
        actual_cols = set(rows[0].keys()) if rows else set()
        result.schema_valid = required_cols.issubset(actual_cols)

        # Parse timestamps
        timestamps = []
        parse_errors = 0
        for row in rows:
            ts = _parse_timestamp(row.get("time", ""))
            if ts is None:
                parse_errors += 1
            else:
                timestamps.append(ts)
        result.timestamps_parseable = parse_errors == 0

        if not timestamps:
            result.overall_status = DataQualityStatus.FAIL.value
            result.details["parse_errors"] = parse_errors
            return result

        result.first_timestamp = timestamps[0].isoformat()
        result.last_timestamp = timestamps[-1].isoformat()

        # Monotonic check
        monotonic = all(timestamps[i] <= timestamps[i + 1] for i in range(len(timestamps) - 1))
        result.timestamps_monotonic = monotonic

        # Duplicate check
        ts_strings = [t.isoformat() for t in timestamps]
        unique_ts = set(ts_strings)
        result.duplicate_count = len(ts_strings) - len(unique_ts)
        result.no_duplicate_timestamps = result.duplicate_count == 0

        # Future bar check
        future_bars = sum(1 for t in timestamps if t > now)
        result.no_future_bars = future_bars == 0

        # OHLC consistency: low <= open,close <= high; all >= 0
        ohlc_ok = True
        negative_prices = 0
        for row in rows:
            try:
                o = float(row.get("open", 0))
                h = float(row.get("high", 0))
                l = float(row.get("low", 0))
                c = float(row.get("close", 0))
                if not (l <= o <= h and l <= c <= h):
                    ohlc_ok = False
                if o < 0 or h < 0 or l < 0 or c < 0:
                    negative_prices += 1
            except (ValueError, TypeError):
                ohlc_ok = False
        result.ohlc_consistent = ohlc_ok
        result.no_negative_prices = negative_prices == 0

        # Zero volume check (warning, not blocking for some instruments)
        zero_volumes = sum(1 for row in rows if int(row.get("volume", 0)) == 0)
        # Zero volume is expected for some bars — only flag if > 50% are zero
        result.no_zero_volume_anomaly = (zero_volumes / len(rows)) < 0.5

        # Gap detection (distinguishing expected non-trading intervals)
        gaps = self._detect_gaps(timestamps, timeframe)
        result.gap_count = len(gaps)
        true_gaps = [g for g in gaps if g.gap_type == "TRUE_MISSING"]
        result.no_gaps_beyond_expected = len(true_gaps) == 0

        # Freshness check
        freshness = self._check_freshness(timestamps[-1], timeframe, now)
        result.not_stale = freshness != FreshnessStatus.STALE.value
        result.details["freshness"] = freshness

        # Horizon coverage
        expected_min_rows = self._expected_min_rows(horizon_days, timeframe)
        if result.row_count >= expected_min_rows * 0.95:
            result.horizon_coverage = CoverageStatus.COMPLETE.value
        elif result.row_count >= expected_min_rows * 0.5:
            result.horizon_coverage = CoverageStatus.PARTIAL.value
        elif result.row_count > 0:
            result.horizon_coverage = CoverageStatus.INSUFFICIENT.value
        else:
            result.horizon_coverage = CoverageStatus.MISSING.value

        # Instrument identity (check filename matches)
        result.instrument_identity_correct = True  # We construct filename from instrument

        # Timeframe identity
        result.timeframe_identity_correct = True

        # Overall status
        checks = [
            result.schema_valid, result.timestamps_parseable,
            result.timestamps_monotonic, result.no_duplicate_timestamps,
            result.no_future_bars, result.ohlc_consistent,
            result.no_negative_prices, result.not_stale,
        ]
        failures = sum(1 for c in checks if not c)
        if failures == 0:
            result.overall_status = DataQualityStatus.PASS.value
        elif failures <= 2:
            result.overall_status = DataQualityStatus.WARNING.value
        else:
            result.overall_status = DataQualityStatus.FAIL.value

        return result

    def check_all_datasets(self, universe: Tuple[str, ...] = CORE_UNIVERSE,
                           timeframes: Tuple[str, ...] = REQUIRED_TIMEFRAMES,
                           horizons: Tuple[int, ...] = REQUIRED_HORIZONS,
                           now: Optional[datetime] = None) -> List[DataQualityResult]:
        """Check all datasets for the production universe."""
        results = []
        for instrument in universe:
            for tf in timeframes:
                for horizon in horizons:
                    results.append(self.check_dataset(instrument, tf, horizon, now))
        return results

    def _read_csv(self, path: str) -> List[Dict[str, str]]:
        """Read CSV file and return list of dicts."""
        with open(path, "r") as f:
            reader = csv.DictReader(f)
            return list(reader)

    def _detect_gaps(self, timestamps: List[datetime], timeframe: str) -> List[DataGap]:
        """Detect gaps in timestamps, distinguishing expected from true gaps."""
        if len(timestamps) < 2:
            return []

        gaps = []
        # Determine expected interval
        if timeframe == "15m":
            expected_delta = timedelta(minutes=15)
        elif timeframe == "1h":
            expected_delta = timedelta(hours=1)
        else:
            expected_delta = timedelta(hours=1)

        for i in range(len(timestamps) - 1):
            diff = timestamps[i + 1] - timestamps[i]
            if diff > expected_delta * 1.5:  # Allow some tolerance
                # Classify gap type
                gap_type = self._classify_gap(timestamps[i], timestamps[i + 1], expected_delta)
                expected_bars = int(diff / expected_delta) - 1
                gaps.append(DataGap(
                    instrument="",  # filled by caller
                    timeframe=timeframe,
                    gap_start=timestamps[i].isoformat(),
                    gap_end=timestamps[i + 1].isoformat(),
                    expected_bars=max(0, expected_bars),
                    actual_bars=0,
                    gap_type=gap_type,
                ))
        return gaps

    def _classify_gap(self, start: datetime, end: datetime, expected_delta: timedelta) -> str:
        """Classify whether a gap is expected (weekend/holiday) or truly missing.

        Checks if any day in the gap range falls on a weekend or holiday.
        If the gap spans a full weekend or holiday, classify as expected.
        """
        # Check each calendar day in the gap for weekends/holidays
        current_date = (start + timedelta(days=1)).date()
        end_date = end.date()
        while current_date <= end_date:
            # Check if this date is a weekend
            if current_date.weekday() in WEEKEND_DAYS:
                return "WEEKEND"
            # Check if this date is a holiday
            if current_date.isoformat() in MARKET_HOLIDAYS_UTC:
                return "HOLIDAY"
            current_date += timedelta(days=1)
        return "TRUE_MISSING"

    def _check_freshness(self, last_timestamp: datetime, timeframe: str,
                         now: datetime) -> str:
        """Check data freshness based on timeframe thresholds."""
        thresholds = FRESHNESS_THRESHOLDS.get(timeframe, FRESHNESS_THRESHOLDS["1h"])
        age_hours = (now - last_timestamp).total_seconds() / 3600

        if age_hours <= thresholds["FRESH_HOURS"]:
            return FreshnessStatus.FRESH.value
        elif age_hours <= thresholds["AGING_HOURS"]:
            return FreshnessStatus.AGING.value
        elif age_hours <= thresholds["STALE_HOURS"]:
            return FreshnessStatus.STALE.value
        else:
            return FreshnessStatus.STALE.value

    def _expected_min_rows(self, horizon_days: int, timeframe: str) -> int:
        """Minimum expected rows for a given horizon and timeframe."""
        # Rough estimate: trading days * bars per day
        trading_days = int(horizon_days * 5 / 7)  # ~5/7 are trading days
        if timeframe == "15m":
            return trading_days * 40  # ~40 bars per day for 15m
        elif timeframe == "1h":
            return trading_days * 10  # ~10 bars per day for 1h
        return trading_days * 1


# ---------------------------------------------------------------------------
# InstrumentMaster — canonical instrument metadata
# ---------------------------------------------------------------------------

class InstrumentMaster:
    """Canonical instrument metadata layer.

    Maps internal ticker ↔ broker instrument identity deterministically.
    No fuzzy symbol matching.
    """

    # Default instrument specifications for core universe
    DEFAULT_INSTRUMENTS: Dict[str, InstrumentMetadata] = {
        "BR": InstrumentMetadata(
            canonical_symbol="BR",
            broker_instrument_id="c8968ffd-db1c-4a35-9f6f-7013a2b7c35a",
            instrument_type="FUTURES",
            exchange="MOEX",
            currency="RUB",
            lot_size=1,
            price_step=0.01,
            price_scale=2,
            contract_multiplier=865.857,  # FIX: real MOEX spec (was fake 10.0)
            underlying="Brent",
            source="TINKOFF_FUTURES_SPECS",
        ),
        "GAZP": InstrumentMetadata(
            canonical_symbol="GAZP",
            broker_instrument_id="82b34e5c-30c3-4be2-ba9d-5ba3b37ca99b",
            instrument_type="FUTURES",
            exchange="MOEX",
            currency="RUB",
            lot_size=1,
            price_step=0.01,
            price_scale=2,
            contract_multiplier=100.0,  # FIX: real MOEX spec (was fake 1000.0)
            underlying="GAZP",
            source="TINKOFF_FUTURES_SPECS",
        ),
        "LKOH": InstrumentMetadata(
            canonical_symbol="LKOH",
            broker_instrument_id="54b3c523-d152-4030-bbc0-7f31155cfca9",
            instrument_type="FUTURES",
            exchange="MOEX",
            currency="RUB",
            lot_size=1,
            price_step=1.0,
            price_scale=0,
            contract_multiplier=1.0,  # real MOEX spec: step 1.0, step_amt 1.0
            underlying="LKOH",
            source="TINKOFF_FUTURES_SPECS",
        ),
        "SBER": InstrumentMetadata(
            canonical_symbol="SBER",
            broker_instrument_id="9e9c5921-43ac-47a8-b537-ccf32ebc6da3",
            instrument_type="FUTURES",
            exchange="MOEX",
            currency="RUB",
            lot_size=1,
            price_step=0.01,
            price_scale=2,
            contract_multiplier=100.0,  # FIX: real MOEX spec (was fake 1.0)
            underlying="SBER",
            source="TINKOFF_FUTURES_SPECS",
        ),
        "Si": InstrumentMetadata(
            canonical_symbol="Si",
            broker_instrument_id="SiZ4",  # Example
            instrument_type="FUTURES",
            exchange="MOEX",
            currency="RUB",
            lot_size=1,
            price_step=1.0,
            price_scale=0,
            contract_multiplier=1000.0,  # Si futures: 1000 rubles per point
            underlying="Si",
            source="MANUAL",
        ),
        # FIX(T7/T10/F24): CORE_UNIVERSE contains 8 symbols but only 5 had
        # metadata. Values below come from real MOEX specs fetched via Tinkoff
        # REST (futures_lab/futures_specs.json, 2026-09-05).
        "CNY": InstrumentMetadata(
            canonical_symbol="CNY",
            broker_instrument_id="c300543d-aa18-4249-b110-615409dde036",
            instrument_type="FUTURES",
            exchange="MOEX",
            currency="RUB",
            lot_size=1,
            price_step=0.001,
            price_scale=3,
            contract_multiplier=1000.0,  # step_amount 1.0 RUB / step 0.001
            underlying="CNY/RUB",
            source="TINKOFF_FUTURES_SPECS",
        ),
        "EURRUB": InstrumentMetadata(
            canonical_symbol="EURRUB",
            broker_instrument_id="e797d92b-d398-47a6-aca9-856e3106d78d",
            instrument_type="FUTURES",
            exchange="MOEX",
            currency="RUB",
            lot_size=1,
            price_step=0.01,
            price_scale=2,
            contract_multiplier=1000.0,  # step_amount 10.0 RUB / step 0.01
            underlying="EUR/RUB",
            source="TINKOFF_FUTURES_SPECS",
        ),
        "USDRUB": InstrumentMetadata(
            canonical_symbol="USDRUB",
            broker_instrument_id="48706c30-0bd7-42ad-a936-150287cd9de4",
            instrument_type="FUTURES",
            exchange="MOEX",
            currency="RUB",
            lot_size=1,
            price_step=0.01,
            price_scale=2,
            contract_multiplier=1000.0,  # step_amount 10.0 RUB / step 0.01
            underlying="USD/RUB",
            source="TINKOFF_FUTURES_SPECS",
        ),
        "IMOEX": InstrumentMetadata(
            canonical_symbol="IMOEX",
            broker_instrument_id="5bcff194-f10d-4314-b9ee-56b7fdb344fd",
            instrument_type="FUTURES",
            exchange="MOEX",
            currency="RUB",
            lot_size=1,
            price_step=0.5,
            price_scale=1,
            contract_multiplier=10.0,  # step_amount 5.0 RUB / step 0.5
            underlying="IMOEX",
            source="TINKOFF_FUTURES_SPECS",
        ),
        "NG": InstrumentMetadata(
            canonical_symbol="NG",
            broker_instrument_id="7326ae99-d9f1-4df1-8e3a-d85390686038",
            instrument_type="FUTURES",
            exchange="MOEX",
            currency="RUB",
            lot_size=1,
            price_step=0.001,
            price_scale=3,
            contract_multiplier=8658.57,  # step_amount 8.65857 RUB / step 0.001
            underlying="NG",
            source="TINKOFF_FUTURES_SPECS",
        ),
    }

    def __init__(self, store: Optional[ProductionTruthStore] = None):
        self._store = store
        self._cache: Dict[str, InstrumentMetadata] = {}
        self._init_defaults()

    def _init_defaults(self) -> None:
        for symbol, meta in self.DEFAULT_INSTRUMENTS.items():
            self._cache[symbol] = meta
            if self._store:
                self._store.save_instrument_metadata(meta)

    def resolve(self, symbol: str) -> Optional[InstrumentMetadata]:
        """Resolve canonical symbol to instrument metadata.

        Returns None for unknown/ambiguous symbols (fails closed).
        """
        return self._cache.get(symbol)

    def resolve_broker_id(self, broker_instrument_id: str) -> Optional[InstrumentMetadata]:
        """Resolve broker instrument ID to canonical metadata.

        Deterministic — no fuzzy matching.
        """
        for meta in self._cache.values():
            if meta.broker_instrument_id == broker_instrument_id:
                return meta
        return None  # Unknown broker ID — fails closed

    def register(self, meta: InstrumentMetadata) -> None:
        """Register a new instrument or update existing."""
        self._cache[meta.canonical_symbol] = meta
        if self._store:
            self._store.save_instrument_metadata(meta)

    def get_multiplier(self, symbol: str) -> float:
        """Get contract multiplier for PnL calculation."""
        meta = self.resolve(symbol)
        if meta is None:
            return 1.0  # Fallback — should not be used without metadata
        return meta.contract_multiplier

    def get_currency(self, symbol: str) -> str:
        """Get trading currency for an instrument."""
        meta = self.resolve(symbol)
        if meta is None:
            return "UNKNOWN"
        return meta.currency

    def get_price_scale(self, symbol: str) -> int:
        """Get price scale (decimal places) for an instrument."""
        meta = self.resolve(symbol)
        if meta is None:
            return 2
        return meta.price_scale

    def list_instruments(self) -> List[InstrumentMetadata]:
        """List all registered instruments."""
        return list(self._cache.values())


# ---------------------------------------------------------------------------
# BrokerSnapshotBuilder — READ-ONLY broker snapshot
# ---------------------------------------------------------------------------

class BrokerSnapshotBuilder:
    """Build immutable read-only snapshots from broker data.

    This class does NOT make any broker API calls.
    It normalizes data provided by a caller who has already fetched it.
    All methods that produce BrokerSnapshot are documented as READ_ONLY.
    """

    # Methods that this builder uses — ALL must be READ_ONLY
    ALLOWED_METHODS = frozenset({
        "normalize_positions",
        "normalize_money",
        "normalize_fills",
        "normalize_operations",
        "build_snapshot",
    })

    def build_snapshot(self, *,
                       positions: Optional[List[Dict[str, Any]]] = None,
                       money: Optional[List[Dict[str, Any]]] = None,
                       fills: Optional[List[Dict[str, Any]]] = None,
                       operations: Optional[List[Dict[str, Any]]] = None,
                       account_id: Optional[str] = None,
                       methods_called: Optional[List[str]] = None,
                       broker_snapshot_timestamp: Optional[str] = None) -> BrokerSnapshot:
        """Build an immutable read-only snapshot.

        All inputs must be pre-fetched by the caller using READ_ONLY broker methods.
        """
        snapshot_id = _generate_id("bsnap")
        ts = broker_snapshot_timestamp or _now_iso()

        return BrokerSnapshot(
            snapshot_id=snapshot_id,
            timestamp_iso=ts,
            account_id_hash=_hash_string(account_id) if account_id else None,
            positions=positions or [],
            money=money or [],
            fills=fills or [],
            operations=operations or [],
            commissions_total=sum(op.get("commission", 0) for op in (operations or [])),
            source="READ_ONLY",
            methods_called=methods_called or [],
        )


# ---------------------------------------------------------------------------
# ReconciliationEngine — broker ↔ local mismatch detection (READ-ONLY)
# ---------------------------------------------------------------------------

class ReconciliationEngine:
    """Reconciliation engine — detects mismatches between broker and local state.

    HARD BOUNDARY: mismatch detection ONLY, no corrective trading.
    """

    def __init__(self, store: Optional[ProductionTruthStore] = None):
        self._store = store

    def reconcile_positions(self, broker_positions: List[Dict[str, Any]],
                            local_positions: List[Dict[str, Any]],
                            instrument_master: InstrumentMaster) -> Tuple[List[ReconciliationMismatch], int]:
        """Compare broker vs local positions. Returns (mismatches, match_count)."""
        mismatches = []
        matches = 0

        # Index by instrument
        broker_by_inst = {p.get("instrument", p.get("ticker", "")): p for p in broker_positions}
        local_by_inst = {p.get("instrument", p.get("ticker", "")): p for p in local_positions}

        all_instruments = set(broker_by_inst.keys()) | set(local_by_inst.keys())

        for inst in all_instruments:
            broker_p = broker_by_inst.get(inst)
            local_p = local_by_inst.get(inst)

            if broker_p and not local_p:
                mismatches.append(ReconciliationMismatch(
                    mismatch_id=_generate_id("mm"),
                    mismatch_class=MismatchClass.BROKER_POSITION_MISSING_LOCAL.value,
                    severity=MismatchSeverity.WARNING.value,
                    broker_value=broker_p,
                    local_value=None,
                    instrument=inst,
                    details=f"Broker has position in {inst} but local does not",
                ))
            elif local_p and not broker_p:
                mismatches.append(ReconciliationMismatch(
                    mismatch_id=_generate_id("mm"),
                    mismatch_class=MismatchClass.LOCAL_POSITION_MISSING_BROKER.value,
                    severity=MismatchSeverity.WARNING.value,
                    broker_value=None,
                    local_value=local_p,
                    instrument=inst,
                    details=f"Local has position in {inst} but broker does not",
                ))
            else:
                # Both have positions — compare quantities
                broker_qty = broker_p.get("quantity", 0) if broker_p else 0
                local_qty = local_p.get("quantity", 0) if local_p else 0
                if broker_qty != local_qty:
                    mismatches.append(ReconciliationMismatch(
                        mismatch_id=_generate_id("mm"),
                        mismatch_class=MismatchClass.QUANTITY_MISMATCH.value,
                        severity=MismatchSeverity.CRITICAL.value,
                        broker_value=broker_qty,
                        local_value=local_qty,
                        instrument=inst,
                        details=f"Quantity mismatch: broker={broker_qty}, local={local_qty}",
                    ))
                else:
                    matches += 1

        return mismatches, matches

    def reconcile_fills(self, broker_fills: List[Dict[str, Any]],
                        local_fills: List[Dict[str, Any]]) -> Tuple[List[ReconciliationMismatch], int]:
        """Compare broker vs local fills. Returns (mismatches, match_count)."""
        mismatches = []
        matches = 0

        # Index by broker fill ID or composite key
        broker_by_id = {}
        for f in broker_fills:
            fid = f.get("fill_id") or f.get("trade_id") or f.get("id", "")
            broker_by_id[fid] = f

        local_by_id = {}
        for f in local_fills:
            fid = f.get("fill_id") or f.get("broker_fill_id") or f.get("id", "")
            local_by_id[fid] = f

        all_ids = set(broker_by_id.keys()) | set(local_by_id.keys())

        for fid in all_ids:
            broker_f = broker_by_id.get(fid)
            local_f = local_by_id.get(fid)

            if broker_f and not local_f:
                mismatches.append(ReconciliationMismatch(
                    mismatch_id=_generate_id("mm"),
                    mismatch_class=MismatchClass.BROKER_FILL_MISSING_LOCAL.value,
                    severity=MismatchSeverity.WARNING.value,
                    broker_value=broker_f,
                    local_value=None,
                    instrument=broker_f.get("instrument", ""),
                    details=f"Broker fill {fid} not in local records",
                ))
            elif local_f and not broker_f:
                mismatches.append(ReconciliationMismatch(
                    mismatch_id=_generate_id("mm"),
                    mismatch_class=MismatchClass.LOCAL_FILL_MISSING_BROKER.value,
                    severity=MismatchSeverity.WARNING.value,
                    broker_value=None,
                    local_value=local_f,
                    instrument=local_f.get("instrument", ""),
                    details=f"Local fill {fid} not in broker records",
                ))
            else:
                # Both have fill — compare key fields
                broker_price = broker_f.get("price", 0)
                local_price = local_f.get("price", 0)
                if abs(broker_price - local_price) > 0.001:
                    mismatches.append(ReconciliationMismatch(
                        mismatch_id=_generate_id("mm"),
                        mismatch_class=MismatchClass.PRICE_MISMATCH.value,
                        severity=MismatchSeverity.CRITICAL.value,
                        broker_value=broker_price,
                        local_value=local_price,
                        instrument=broker_f.get("instrument", ""),
                        details=f"Fill price mismatch: broker={broker_price}, local={local_price}",
                    ))
                else:
                    matches += 1

        return mismatches, matches

    def run_full_reconciliation(self, broker_snapshot: BrokerSnapshot,
                                local_positions: List[Dict[str, Any]],
                                local_fills: List[Dict[str, Any]],
                                instrument_master: InstrumentMaster,
                                truth_build_id: str) -> ReconciliationResult:
        """Run full reconciliation and persist results."""
        recon_id = _generate_id("recon")

        pos_mismatches, pos_matches = self.reconcile_positions(
            broker_snapshot.positions, local_positions, instrument_master
        )
        fill_mismatches, fill_matches = self.reconcile_fills(
            broker_snapshot.fills, local_fills
        )

        all_mismatches = pos_mismatches + fill_mismatches
        total_matches = pos_matches + fill_matches

        result = ReconciliationResult(
            reconciliation_id=recon_id,
            truth_build_id=truth_build_id,
            timestamp_iso=_now_iso(),
            positions_compared=len(broker_snapshot.positions) + len(local_positions),
            fills_compared=len(broker_snapshot.fills) + len(local_fills),
            operations_compared=len(broker_snapshot.operations),
            matches=total_matches,
            mismatches=len(all_mismatches),
            mismatch_details=[m.to_dict() for m in all_mismatches],
            unattributed_count=0,  # Would be set by attribution engine
            status="COMPLETED",
        )

        if self._store:
            self._store.save_reconciliation_result(result)
            for mm in all_mismatches:
                self._store.save_mismatch(recon_id, mm)

        return result


# ---------------------------------------------------------------------------
# BackfillManager — checkpointed historical backfill (READ-ONLY)
# ---------------------------------------------------------------------------

class BackfillManager:
    """Checkpointed, idempotent, paginated historical backfill from broker.

    All operations are READ-ONLY — fetches historical data, never places orders.
    """

    def __init__(self, store: Optional[ProductionTruthStore] = None):
        self._store = store

    def create_checkpoint(self, account_id: str, date_from: str, date_to: str) -> BackfillCheckpoint:
        """Create a new backfill checkpoint."""
        checkpoint = BackfillCheckpoint(
            checkpoint_id=_generate_id("bfill"),
            account_hash=_hash_string(account_id),
            date_from=date_from,
            date_to=date_to,
            status="RUNNING",
            started_at=_now_iso(),
        )
        if self._store:
            self._store.save_backfill_checkpoint(checkpoint)
        return checkpoint

    def resume_checkpoint(self, account_id: str) -> Optional[BackfillCheckpoint]:
        """Get the latest checkpoint for resume."""
        if self._store:
            return self._store.get_backfill_checkpoint(_hash_string(account_id))
        return None

    def complete_checkpoint(self, checkpoint: BackfillCheckpoint,
                            operation_count: int, checksum: str) -> BackfillCheckpoint:
        """Mark checkpoint as completed."""
        checkpoint.status = "COMPLETED"
        checkpoint.operation_count = operation_count
        checkpoint.checksum = checksum
        checkpoint.finished_at = _now_iso()
        if self._store:
            self._store.save_backfill_checkpoint(checkpoint)
        return checkpoint

    def deduplicate_operations(self, operations: List[BrokerOperation]) -> List[BrokerOperation]:
        """Deduplicate broker operations using stable IDs."""
        seen = set()
        deduped = []
        for op in operations:
            key = op.broker_operation_id or f"{op.operation_type}:{op.instrument}:{op.timestamp}:{op.amount}"
            if key not in seen:
                seen.add(key)
                deduped.append(op)
            else:
                logger.info("Deduplicated operation: %s", key)
        return deduped


# ---------------------------------------------------------------------------
# TruthConfidenceModel — overall confidence assessment
# ---------------------------------------------------------------------------

class TruthConfidenceModel:
    """Assess overall truth confidence based on build results.

    This is evidence confidence, not trading permission.
    """

    @staticmethod
    def assess(data_quality_results: List[DataQualityResult],
               instrument_metadata_coverage: int,
               broker_snapshot_available: bool,
               reconciliation_mismatches: int,
               unattributed_operations: int,
               total_operations: int) -> TruthConfidence:
        """Assess overall truth confidence."""
        # Factor 1: Data quality pass rate
        if not data_quality_results:
            return TruthConfidence.INSUFFICIENT
        dq_pass = sum(1 for r in data_quality_results if r.overall_status == DataQualityStatus.PASS.value)
        dq_rate = dq_pass / len(data_quality_results) if data_quality_results else 0

        # Factor 2: Instrument metadata coverage
        meta_coverage = instrument_metadata_coverage / len(CORE_UNIVERSE)

        # Factor 3: Broker snapshot
        broker_ok = broker_snapshot_available

        # Factor 4: Reconciliation mismatches
        no_critical_mismatches = reconciliation_mismatches == 0

        # Factor 5: Attribution coverage
        attr_coverage = (total_operations - unattributed_operations) / max(total_operations, 1)

        # Scoring
        score = 0
        if dq_rate >= 0.9:
            score += 3
        elif dq_rate >= 0.7:
            score += 2
        elif dq_rate >= 0.5:
            score += 1

        if meta_coverage >= 0.8:
            score += 2
        elif meta_coverage >= 0.5:
            score += 1

        if broker_ok:
            score += 2

        if no_critical_mismatches:
            score += 2
        elif reconciliation_mismatches <= 3:
            score += 1

        if attr_coverage >= 0.8:
            score += 1

        if score >= 8:
            return TruthConfidence.HIGH
        elif score >= 5:
            return TruthConfidence.MEDIUM
        elif score >= 2:
            return TruthConfidence.LOW
        else:
            return TruthConfidence.INSUFFICIENT


# ---------------------------------------------------------------------------
# ProductionTruthBuilder — orchestrator
# ---------------------------------------------------------------------------

class ProductionTruthBuilder:
    """Orchestrate a complete production truth build.

    Combines: Data Quality + Instrument Master + Broker Snapshot + Reconciliation + Confidence.
    """

    def __init__(self, store: Optional[ProductionTruthStore] = None,
                 data_path: Optional[str] = None):
        self.store = store or ProductionTruthStore()
        self.guardian = DataQualityGuardian(data_path)
        self.instrument_master = InstrumentMaster(self.store)
        self.snapshot_builder = BrokerSnapshotBuilder()
        self.reconciliation_engine = ReconciliationEngine(self.store)
        self.backfill_manager = BackfillManager(self.store)
        self.confidence_model = TruthConfidenceModel()

    def run_build(self, universe: Tuple[str, ...] = CORE_UNIVERSE,
                  broker_positions: Optional[List[Dict[str, Any]]] = None,
                  broker_fills: Optional[List[Dict[str, Any]]] = None,
                  broker_operations: Optional[List[Dict[str, Any]]] = None,
                  broker_account_id: Optional[str] = None,
                  local_positions: Optional[List[Dict[str, Any]]] = None,
                  local_fills: Optional[List[Dict[str, Any]]] = None) -> Tuple[TruthBuild, TruthConfidence]:
        """Run a complete production truth build."""
        build_id = _generate_id("tb")
        build = TruthBuild(
            truth_build_id=build_id,
            started_at=_now_iso(),
            code_version=TRUTH_BUILDER_VERSION,
        )
        self.store.save_truth_build(build)

        try:
            # 1. Data Quality Guardian
            dq_results = self.guardian.check_all_datasets(universe)
            for dq in dq_results:
                self.store.save_data_quality(build_id, dq)

            # 2. Compute data source hashes
            data_hashes = {}
            for dq in dq_results:
                key = f"{dq.instrument}_{dq.timeframe}_{dq.horizon_days}d"
                data_hashes[key] = dq.file_hash
            build.data_source_hashes = data_hashes

            # 3. Instrument Master — already initialized in constructor
            meta_count = len(self.instrument_master.list_instruments())

            # 4. Broker snapshot (if data provided)
            broker_snapshot = None
            if broker_positions is not None or broker_fills is not None:
                broker_snapshot = self.snapshot_builder.build_snapshot(
                    positions=broker_positions or [],
                    money=[],
                    fills=broker_fills or [],
                    operations=broker_operations or [],
                    account_id=broker_account_id,
                )
                self.store.save_broker_snapshot(broker_snapshot)
                build.broker_snapshot_timestamp = broker_snapshot.timestamp_iso
                if broker_account_id:
                    build.broker_account_hash = _hash_string(broker_account_id)

            # 5. Reconciliation (if both broker and local data available)
            recon_mismatches = 0
            if broker_snapshot and local_positions is not None:
                recon_result = self.reconciliation_engine.run_full_reconciliation(
                    broker_snapshot=broker_snapshot,
                    local_positions=local_positions or [],
                    local_fills=local_fills or [],
                    instrument_master=self.instrument_master,
                    truth_build_id=build_id,
                )
                recon_mismatches = recon_result.mismatches

            # 6. Truth confidence
            total_ops = len(broker_operations or [])
            confidence = self.confidence_model.assess(
                data_quality_results=dq_results,
                instrument_metadata_coverage=meta_count,
                broker_snapshot_available=broker_snapshot is not None,
                reconciliation_mismatches=recon_mismatches,
                unattributed_operations=0,  # No attribution engine in this scope
                total_operations=total_ops,
            )

            # 7. Finalize build
            dq_pass = sum(1 for r in dq_results if r.overall_status == DataQualityStatus.PASS.value)
            build.counts = {
                "total_datasets": len(dq_results),
                "dq_pass": dq_pass,
                "dq_warning": sum(1 for r in dq_results if r.overall_status == DataQualityStatus.WARNING.value),
                "dq_fail": sum(1 for r in dq_results if r.overall_status == DataQualityStatus.FAIL.value),
                "instrument_metadata_count": meta_count,
                "reconciliation_mismatches": recon_mismatches,
            }
            build.status = TruthBuildStatus.COMPLETED.value
            build.finished_at = _now_iso()
            self.store.save_truth_build(build)

            return build, confidence

        except Exception as e:
            build.status = TruthBuildStatus.FAILED.value
            build.errors.append(str(e))
            build.finished_at = _now_iso()
            self.store.save_truth_build(build)
            raise


# ---------------------------------------------------------------------------
# TelegramAlerter — redacted, rate-limited, informational only
# ---------------------------------------------------------------------------

class TelegramAlerter:
    """Forward alerts to Telegram with redaction and rate limiting.

    INFORMATIONAL ONLY — Telegram response must not become trading approval.
    """

    def __init__(self, store: Optional[ProductionTruthStore] = None,
                 cooldown_seconds: int = 300):
        self._store = store
        self._cooldown = cooldown_seconds

    def should_alert(self, fingerprint: str) -> bool:
        """Check if alert should be sent (deduplication)."""
        if self._store:
            return not self._store.is_event_duplicate(fingerprint, self._cooldown)
        return True

    def format_alert(self, title: str, body: str, severity: str = "INFO") -> str:
        """Format alert with redaction. Informational only."""
        redacted_body = _redact_secrets(body)
        return (
            f"MISSION CONTROL / ITERATION 19 / NON-TRADING\n"
            f"Severity: {severity}\n"
            f"Title: {title}\n"
            f"---\n"
            f"{redacted_body}\n"
            f"---\n"
            f"⚠️ This alert is informational only. No trading action required."
        )

    def send_alert(self, title: str, body: str, severity: str = "INFO",
                   fingerprint: Optional[str] = None) -> bool:
        """Send alert with deduplication. Returns True if sent.

        Actual Telegram send is not implemented here — this is the interface.
        Integration with Hermes Telegram infrastructure is the responsibility
        of the caller.
        """
        if fingerprint is None:
            fingerprint = _hash_string(f"{title}:{body[:100]}")

        if not self.should_alert(fingerprint):
            logger.info("Alert deduplicated: %s", fingerprint)
            return False

        formatted = self.format_alert(title, body, severity)
        logger.info("Telegram alert (not sent — integration gap): %s", formatted[:200])

        if self._store:
            self._store.save_truth_event(
                build_id=None,  # Would be set by caller
                event_type="TELEGRAM_ALERT",
                event_data={"title": title, "severity": severity, "fingerprint": fingerprint},
                fingerprint=fingerprint,
            )

        return True

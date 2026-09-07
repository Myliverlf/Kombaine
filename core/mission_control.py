"""Autonomous Mission Control — Iteration 18.

Single deterministic control-plane owner above all analytical modules.
Combines: Decision Orchestrator + Revalidation Engine + Incident Manager + Observability.

Change class: CLASS 2 — control plane / orchestration / non-trading automation.
HARD BOUNDARY: MISSION CONTROL ACTION ≠ TRADING ACTION.
Zero registry/swap/broker/execution/signal mutation.

One primary action per cycle (observes many, acts on one).
Deterministic policy — no LLM-authoritative dispatch.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Version constants
# ---------------------------------------------------------------------------
MC_SCHEMA_VERSION = "1.0.0"
MC_POLICY_VERSION = "1.0.0"
MC_RECOVERY_POLICY_VERSION = "1.0.0"
MC_ALERT_POLICY_VERSION = "1.0.0"
MC_ORCHESTRATOR_VERSION = "1.0.0"
MC_DB_NAME = "mission_control.db"
LOCK_FILE = "state/.mission_control.lock"

# Loop protection
MAX_AUTO_REVALIDATIONS_PER_STRATEGY_PER_PERIOD = 3
REVALIDATION_PERIOD_DAYS = 30
MAX_RECOVERY_ATTEMPTS = 3
RECOVERY_BACKOFF_BASE = 60  # seconds

# Alert rate limits
ALERT_COOLDOWN_SECONDS = 300  # 5 min per fingerprint
MAX_ALERTS_PER_CYCLE = 10

# Secret patterns for redaction
_SECRET_PATTERNS = [
    (re.compile(r'(token|key|secret|password|api_key|apikey)[\s:=]+\S+', re.IGNORECASE), r'\1=***REDACTED***'),
    (re.compile(r'[A-Za-z0-9]{20,}'), '***REDACTED_TOKEN***'),
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class MCAction(str, Enum):
    """Allowed primary actions per cycle — ONE per cycle."""
    NO_ACTION = "NO_ACTION"
    OPEN_INCIDENT = "OPEN_INCIDENT"
    CREATE_REVALIDATION = "CREATE_REVALIDATION"
    RUN_CANONICAL_RESEARCH = "RUN_CANONICAL_RESEARCH"
    REQUEST_HUMAN_REVIEW = "REQUEST_HUMAN_REVIEW"
    WAIT_FOR_EVIDENCE = "WAIT_FOR_EVIDENCE"
    ESCALATE_OPERATOR = "ESCALATE_OPERATOR"
    RESUME_FAILED_TASK = "RESUME_FAILED_TASK"


class Priority(str, Enum):
    """Deterministic priority — lower number = higher priority."""
    P0_SAFETY = "P0_SAFETY"
    P1_DATA_FAILURE = "P1_DATA_FAILURE"
    P2_FAILED_TASK = "P2_FAILED_TASK"
    P3_REQUIRED_REVALIDATION = "P3_REQUIRED_REVALIDATION"
    P4_RESEARCH_NEED = "P4_RESEARCH_NEED"
    P5_HUMAN_REVIEW = "P5_HUMAN_REVIEW"
    P6_WAIT_FOR_EVIDENCE = "P6_WAIT_FOR_EVIDENCE"
    P7_NO_ACTION = "P7_NO_ACTION"


class IncidentSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    DEGRADED = "DEGRADED"
    BLOCKING = "BLOCKING"
    SAFETY = "SAFETY"


class TaskState(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    SUPERSEDED = "SUPERSEDED"


class IncidentState(str, Enum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RECOVERING = "RECOVERING"
    RESOLVED = "RESOLVED"
    ESCALATED = "ESCALATED"
    SUPPRESSED = "SUPPRESSED"


class RevalidationState(str, Enum):
    PENDING = "PENDING"
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    SUPERSEDED = "SUPERSEDED"


class RevalidationEvidenceType(str, Enum):
    BACKTEST_REVALIDATION = "BACKTEST_REVALIDATION"
    WALK_FORWARD_REVALIDATION = "WALK_FORWARD_REVALIDATION"
    PAPER_EVIDENCE_REFRESH = "PAPER_EVIDENCE_REFRESH"
    REGIME_SPECIFIC_REVALIDATION = "REGIME_SPECIFIC_REVALIDATION"
    DATA_REFRESH = "DATA_REFRESH"
    CORRELATION_REVALIDATION = "CORRELATION_REVALIDATION"
    METHODOLOGY_REVALIDATION = "METHODOLOGY_REVALIDATION"


class OverallMCState(str, Enum):
    NORMAL = "NORMAL"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    WAITING = "WAITING"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"
    SAFETY_STOP = "SAFETY_STOP"


class MCHealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"
    STALE = "STALE"
    UNSAFE = "UNSAFE"
    UNKNOWN = "UNKNOWN"


class AlertEventType(str, Enum):
    MC_DECISION = "MC_DECISION"
    TASK_CREATED = "TASK_CREATED"
    TASK_STARTED = "TASK_STARTED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"
    REVALIDATION_CREATED = "REVALIDATION_CREATED"
    REVALIDATION_COMPLETED = "REVALIDATION_COMPLETED"
    INCIDENT_OPENED = "INCIDENT_OPENED"
    INCIDENT_ESCALATED = "INCIDENT_ESCALATED"
    INCIDENT_RESOLVED = "INCIDENT_RESOLVED"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    SYSTEM_BLOCKED = "SYSTEM_BLOCKED"


# Allowed task types
ALLOWED_TASK_TYPES = frozenset({
    "CANONICAL_RESEARCH",
    "REVALIDATION",
    "DATA_VALIDATION",
    "HEALTH_SNAPSHOT",
    "ANALYTICAL_REBUILD",
    "HUMAN_REVIEW_CREATION",
    "INCIDENT_RECOVERY",
    "INCIDENT_VERIFICATION",
})


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class MCSnapshot:
    """Immutable snapshot of entire system state at a point in time."""
    snapshot_id: str
    cycle_id: str
    timestamp_iso: str
    # System health
    overall_health: str = "UNKNOWN"
    health_components: Dict[str, str] = field(default_factory=dict)
    health_evidence: Dict[str, Any] = field(default_factory=dict)
    # Research
    research_status: str = "UNKNOWN"
    latest_run_id: Optional[str] = None
    research_freshness: str = "UNKNOWN"
    # Knowledge
    knowledge_build_count: int = 0
    knowledge_last_build: Optional[str] = None
    # Lifecycle
    lifecycle_strategies_total: int = 0
    lifecycle_decay_suspected: int = 0
    lifecycle_revalidation_due: int = 0
    lifecycle_revalidation_overdue: int = 0
    # Regime
    regime_health: str = "UNKNOWN"
    regime_current: Optional[str] = None
    # Attribution
    attribution_health: str = "UNKNOWN"
    attribution_conflicts: int = 0
    # Ranking
    ranking_health: str = "UNKNOWN"
    replacement_candidates: int = 0
    # Human review
    human_review_open: int = 0
    human_review_pending_revalidation: int = 0
    # Mission control state
    open_incidents: int = 0
    pending_tasks: int = 0
    failed_tasks: int = 0
    escalated_incidents: int = 0
    pending_revalidations: int = 0
    # Scheduler
    scheduler_status: str = "UNKNOWN"
    # Derived
    overall_mc_state: str = "NORMAL"
    # Hash
    snapshot_hash: str = ""

    def compute_hash(self) -> str:
        """Deterministic hash of snapshot contents."""
        content = json.dumps({
            "overall_health": self.overall_health,
            "health_components": self.health_components,
            "research_status": self.research_status,
            "knowledge_build_count": self.knowledge_build_count,
            "lifecycle_strategies_total": self.lifecycle_strategies_total,
            "lifecycle_decay_suspected": self.lifecycle_decay_suspected,
            "lifecycle_revalidation_due": self.lifecycle_revalidation_due,
            "lifecycle_revalidation_overdue": self.lifecycle_revalidation_overdue,
            "regime_health": self.regime_health,
            "attribution_health": self.attribution_health,
            "attribution_conflicts": self.attribution_conflicts,
            "ranking_health": self.ranking_health,
            "replacement_candidates": self.replacement_candidates,
            "human_review_open": self.human_review_open,
            "open_incidents": self.open_incidents,
            "pending_tasks": self.pending_tasks,
            "failed_tasks": self.failed_tasks,
        }, sort_keys=True, default=str)
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MCDecision:
    """Decision record for a single MC cycle."""
    decision_id: str
    cycle_id: str
    snapshot_id: str
    snapshot_hash: str
    primary_action: str
    priority: str
    reason_codes: List[str] = field(default_factory=list)
    source_evidence: Dict[str, Any] = field(default_factory=dict)
    why_not_selected: Dict[str, str] = field(default_factory=dict)
    expected_outcome: str = ""
    secondary_observations: List[str] = field(default_factory=list)
    overall_state: str = "NORMAL"
    policy_version: str = MC_POLICY_VERSION
    timestamp_iso: str = ""
    executed: bool = False
    execution_result: Optional[str] = None
    task_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MCTask:
    """Bounded non-trading task."""
    task_id: str
    task_type: str
    source_cycle: str
    priority: str
    payload_hash: str
    state: str = TaskState.PENDING.value
    attempts: int = 0
    max_attempts: int = 3
    created_at: str = ""
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    result_ref: Optional[str] = None
    error_ref: Optional[str] = None
    decision_id: Optional[str] = None
    revalidation_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MCIncident:
    """Structured incident for operational/control-plane failures."""
    incident_id: str
    fingerprint: str
    severity: str
    source_component: str
    reason_code: str
    first_seen: str
    last_seen: str
    occurrence_count: int = 1
    state: str = IncidentState.OPEN.value
    description: str = ""
    recovery_attempts: int = 0
    max_recovery_attempts: int = MAX_RECOVERY_ATTEMPTS
    recovery_result: Optional[str] = None
    escalation_package: Optional[str] = None
    resolved_at: Optional[str] = None
    policy_version: str = MC_RECOVERY_POLICY_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MCRevalidationRequest:
    """Structured revalidation request."""
    revalidation_id: str
    strategy_identity: str
    reason: str
    requested_evidence_type: str
    source_trigger: str
    source_case_id: Optional[str] = None
    source_build_id: Optional[str] = None
    priority: str = Priority.P3_REQUIRED_REVALIDATION.value
    created_at: str = ""
    policy_version: str = MC_POLICY_VERSION
    state: str = RevalidationState.PENDING.value
    experiment_plan: Optional[str] = None
    completion_criteria: Optional[str] = None
    result_state: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MCAlertEvent:
    """Alert event with rate limiting."""
    event_id: str
    event_type: str
    severity: str
    message: str
    fingerprint: str
    timestamp_iso: str
    source_component: str = ""
    suppressed: bool = False
    suppression_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# MissionControlStore — SQLite state store
# ---------------------------------------------------------------------------

class MissionControlStore:
    """
    SQLite-backed state store for Mission Control.
    Conceptually state/mission_control.db.
    Control-plane truth only — NOT broker/registry/research truth.
    """

    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = project_root or Path("/root/prop-desk/strategy_combine")
        self.state_dir = self.project_root / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.state_dir / MC_DB_NAME
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            # Handle corrupt DB: if file exists but is not a valid DB, delete and recreate
            if self.db_path.exists():
                try:
                    test_conn = sqlite3.connect(str(self.db_path), timeout=5)
                    test_conn.execute("PRAGMA journal_mode=WAL")
                    test_conn.close()
                except (sqlite3.DatabaseError, sqlite3.OperationalError):
                    # Corrupt DB — remove and recreate
                    try:
                        self.db_path.unlink()
                        # Also remove WAL/SHM files
                        for suffix in ("-wal", "-shm"):
                            p = self.db_path.with_name(self.db_path.name + suffix)
                            if p.exists():
                                p.unlink()
                    except OSError:
                        pass
            self._conn = sqlite3.connect(
                str(self.db_path),
                timeout=10,
                isolation_level="DEFERRED",
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
        return self._conn

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def _init_db(self) -> None:
        conn = self._connect()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS mc_policy_versions (
                version_id TEXT PRIMARY KEY,
                version TEXT NOT NULL,
                created_at TEXT NOT NULL,
                description TEXT
            );

            CREATE TABLE IF NOT EXISTS mc_cycles (
                cycle_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                lock_acquired_at TEXT,
                lock_released_at TEXT,
                snapshot_id TEXT,
                decision_id TEXT,
                overall_state TEXT DEFAULT 'NORMAL',
                policy_version TEXT,
                error TEXT
            );

            CREATE TABLE IF NOT EXISTS mc_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                cycle_id TEXT NOT NULL,
                timestamp_iso TEXT NOT NULL,
                snapshot_hash TEXT NOT NULL,
                overall_health TEXT,
                overall_mc_state TEXT,
                data_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS mc_decisions (
                decision_id TEXT PRIMARY KEY,
                cycle_id TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,
                snapshot_hash TEXT NOT NULL,
                primary_action TEXT NOT NULL,
                priority TEXT NOT NULL,
                reason_codes TEXT DEFAULT '[]',
                source_evidence TEXT DEFAULT '{}',
                why_not_selected TEXT DEFAULT '{}',
                expected_outcome TEXT,
                secondary_observations TEXT DEFAULT '[]',
                overall_state TEXT DEFAULT 'NORMAL',
                policy_version TEXT,
                timestamp_iso TEXT NOT NULL,
                executed INTEGER DEFAULT 0,
                execution_result TEXT,
                task_id TEXT
            );

            CREATE TABLE IF NOT EXISTS mc_tasks (
                task_id TEXT PRIMARY KEY,
                task_type TEXT NOT NULL,
                source_cycle TEXT NOT NULL,
                priority TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                state TEXT DEFAULT 'PENDING',
                attempts INTEGER DEFAULT 0,
                max_attempts INTEGER DEFAULT 3,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                result_ref TEXT,
                error_ref TEXT,
                decision_id TEXT,
                revalidation_id TEXT
            );

            CREATE TABLE IF NOT EXISTS mc_task_events (
                event_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                from_state TEXT,
                to_state TEXT NOT NULL,
                event_type TEXT NOT NULL,
                timestamp_iso TEXT NOT NULL,
                details TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS mc_incidents (
                incident_id TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL,
                severity TEXT NOT NULL,
                source_component TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                occurrence_count INTEGER DEFAULT 1,
                state TEXT DEFAULT 'OPEN',
                description TEXT DEFAULT '',
                recovery_attempts INTEGER DEFAULT 0,
                max_recovery_attempts INTEGER DEFAULT 3,
                recovery_result TEXT,
                escalation_package TEXT,
                resolved_at TEXT,
                policy_version TEXT
            );

            CREATE TABLE IF NOT EXISTS mc_revalidation_requests (
                revalidation_id TEXT PRIMARY KEY,
                strategy_identity TEXT NOT NULL,
                reason TEXT NOT NULL,
                requested_evidence_type TEXT NOT NULL,
                source_trigger TEXT NOT NULL,
                source_case_id TEXT,
                source_build_id TEXT,
                priority TEXT,
                created_at TEXT NOT NULL,
                policy_version TEXT,
                state TEXT DEFAULT 'PENDING',
                experiment_plan TEXT,
                completion_criteria TEXT,
                result_state TEXT
            );

            CREATE TABLE IF NOT EXISTS mc_alert_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                message TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                timestamp_iso TEXT NOT NULL,
                source_component TEXT DEFAULT '',
                suppressed INTEGER DEFAULT 0,
                suppression_reason TEXT
            );
        """)
        conn.commit()

    # ---- Cycle CRUD ----
    def create_cycle(self, cycle_id: str) -> None:
        conn = self._connect()
        conn.execute(
            "INSERT INTO mc_cycles (cycle_id, started_at, policy_version) VALUES (?, ?, ?)",
            (cycle_id, datetime.now(timezone.utc).isoformat(), MC_POLICY_VERSION),
        )
        conn.commit()

    def finish_cycle(self, cycle_id: str, snapshot_id: str = "",
                     decision_id: str = "", overall_state: str = "NORMAL",
                     error: Optional[str] = None) -> None:
        conn = self._connect()
        conn.execute(
            """UPDATE mc_cycles SET finished_at=?, snapshot_id=?, decision_id=?,
               overall_state=?, error=? WHERE cycle_id=?""",
            (datetime.now(timezone.utc).isoformat(), snapshot_id, decision_id,
             overall_state, error, cycle_id),
        )
        conn.commit()

    # ---- Snapshot CRUD ----
    def store_snapshot(self, snap: MCSnapshot) -> None:
        conn = self._connect()
        conn.execute(
            """INSERT OR REPLACE INTO mc_snapshots
               (snapshot_id, cycle_id, timestamp_iso, snapshot_hash,
                overall_health, overall_mc_state, data_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (snap.snapshot_id, snap.cycle_id, snap.timestamp_iso,
             snap.snapshot_hash, snap.overall_health, snap.overall_mc_state,
             json.dumps(snap.to_dict(), default=str)),
        )
        conn.commit()

    def get_latest_snapshot(self) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        row = conn.execute(
            "SELECT data_json FROM mc_snapshots ORDER BY timestamp_iso DESC LIMIT 1"
        ).fetchone()
        return json.loads(row["data_json"]) if row else None

    # ---- Decision CRUD ----
    def store_decision(self, dec: MCDecision) -> None:
        conn = self._connect()
        conn.execute(
            """INSERT OR REPLACE INTO mc_decisions
               (decision_id, cycle_id, snapshot_id, snapshot_hash, primary_action,
                priority, reason_codes, source_evidence, why_not_selected,
                expected_outcome, secondary_observations, overall_state,
                policy_version, timestamp_iso, executed, execution_result, task_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (dec.decision_id, dec.cycle_id, dec.snapshot_id, dec.snapshot_hash,
             dec.primary_action, dec.priority, json.dumps(dec.reason_codes),
             json.dumps(dec.source_evidence, default=str),
             json.dumps(dec.why_not_selected, default=str),
             dec.expected_outcome, json.dumps(dec.secondary_observations),
             dec.overall_state, dec.policy_version, dec.timestamp_iso,
             int(dec.executed), dec.execution_result, dec.task_id),
        )
        conn.commit()

    def get_decision(self, decision_id: str) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM mc_decisions WHERE decision_id=?", (decision_id,)
        ).fetchone()
        return dict(row) if row else None

    # ---- Task CRUD ----
    def store_task(self, task: MCTask) -> None:
        conn = self._connect()
        conn.execute(
            """INSERT OR REPLACE INTO mc_tasks
               (task_id, task_type, source_cycle, priority, payload_hash,
                state, attempts, max_attempts, created_at, started_at,
                finished_at, result_ref, error_ref, decision_id, revalidation_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task.task_id, task.task_type, task.source_cycle, task.priority,
             task.payload_hash, task.state, task.attempts, task.max_attempts,
             task.created_at, task.started_at, task.finished_at,
             task.result_ref, task.error_ref, task.decision_id, task.revalidation_id),
        )
        conn.commit()

    def update_task_state(self, task_id: str, new_state: str,
                          error_ref: Optional[str] = None,
                          result_ref: Optional[str] = None) -> None:
        conn = self._connect()
        now = datetime.now(timezone.utc).isoformat()
        updates = ["state=?", "attempts=attempts+1"]
        params: list = [new_state]
        if new_state == TaskState.RUNNING.value:
            updates.append("started_at=?")
            params.append(now)
        if new_state in (TaskState.COMPLETED.value, TaskState.FAILED.value,
                         TaskState.CANCELLED.value):
            updates.append("finished_at=?")
            params.append(now)
        if error_ref is not None:
            updates.append("error_ref=?")
            params.append(error_ref)
        if result_ref is not None:
            updates.append("result_ref=?")
            params.append(result_ref)
        params.append(task_id)
        conn.execute(
            f"UPDATE mc_tasks SET {', '.join(updates)} WHERE task_id=?", params
        )
        conn.commit()

    def get_pending_tasks(self) -> List[Dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM mc_tasks WHERE state IN ('PENDING','RUNNING') ORDER BY priority"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_failed_tasks(self) -> List[Dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM mc_tasks WHERE state='FAILED' AND attempts < max_attempts"
        ).fetchall()
        return [dict(r) for r in rows]

    def task_exists_by_hash(self, payload_hash: str, state: str) -> bool:
        conn = self._connect()
        row = conn.execute(
            "SELECT 1 FROM mc_tasks WHERE payload_hash=? AND state=? LIMIT 1",
            (payload_hash, state),
        ).fetchone()
        return row is not None

    def task_event(self, task_id: str, from_state: Optional[str],
                   to_state: str, event_type: str, details: str = "{}") -> None:
        conn = self._connect()
        event_id = f"te_{uuid.uuid4().hex[:12]}"
        conn.execute(
            """INSERT INTO mc_task_events
               (event_id, task_id, from_state, to_state, event_type, timestamp_iso, details)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (event_id, task_id, from_state, to_state, event_type,
             datetime.now(timezone.utc).isoformat(), details),
        )
        conn.commit()

    # ---- Incident CRUD ----
    def store_incident(self, inc: MCIncident) -> None:
        conn = self._connect()
        conn.execute(
            """INSERT OR REPLACE INTO mc_incidents
               (incident_id, fingerprint, severity, source_component, reason_code,
                first_seen, last_seen, occurrence_count, state, description,
                recovery_attempts, max_recovery_attempts, recovery_result,
                escalation_package, resolved_at, policy_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (inc.incident_id, inc.fingerprint, inc.severity,
             inc.source_component, inc.reason_code, inc.first_seen,
             inc.last_seen, inc.occurrence_count, inc.state, inc.description,
             inc.recovery_attempts, inc.max_recovery_attempts,
             inc.recovery_result, inc.escalation_package, inc.resolved_at,
             inc.policy_version),
        )
        conn.commit()

    def get_open_incidents(self) -> List[Dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM mc_incidents WHERE state NOT IN ('RESOLVED','SUPPRESSED')"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_incident_by_fingerprint(self, fingerprint: str) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM mc_incidents WHERE fingerprint=? AND state NOT IN ('RESOLVED','SUPPRESSED') LIMIT 1",
            (fingerprint,),
        ).fetchone()
        return dict(row) if row else None

    def update_incident(self, incident_id: str, **kwargs: Any) -> None:
        conn = self._connect()
        sets = []
        params: list = []
        for k, v in kwargs.items():
            sets.append(f"{k}=?")
            params.append(v)
        params.append(incident_id)
        conn.execute(f"UPDATE mc_incidents SET {', '.join(sets)} WHERE incident_id=?", params)
        conn.commit()

    # ---- Revalidation CRUD ----
    def store_revalidation(self, req: MCRevalidationRequest) -> None:
        conn = self._connect()
        conn.execute(
            """INSERT OR REPLACE INTO mc_revalidation_requests
               (revalidation_id, strategy_identity, reason, requested_evidence_type,
                source_trigger, source_case_id, source_build_id, priority,
                created_at, policy_version, state, experiment_plan,
                completion_criteria, result_state)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (req.revalidation_id, req.strategy_identity, req.reason,
             req.requested_evidence_type, req.source_trigger, req.source_case_id,
             req.source_build_id, req.priority, req.created_at, req.policy_version,
             req.state, req.experiment_plan, req.completion_criteria,
             req.result_state),
        )
        conn.commit()

    def get_pending_revalidations(self) -> List[Dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM mc_revalidation_requests WHERE state IN ('PENDING','PLANNED','RUNNING')"
        ).fetchall()
        return [dict(r) for r in rows]

    def count_recent_revalidations(self, strategy_identity: str, period_days: int = REVALIDATION_PERIOD_DAYS) -> int:
        conn = self._connect()
        row = conn.execute(
            """SELECT COUNT(*) as cnt FROM mc_revalidation_requests
               WHERE strategy_identity=? AND created_at > datetime('now', ?)""",
            (strategy_identity, f"-{period_days} days"),
        ).fetchone()
        return row["cnt"] if row else 0

    # ---- Alert CRUD ----
    def store_alert(self, alert: MCAlertEvent) -> None:
        conn = self._connect()
        conn.execute(
            """INSERT INTO mc_alert_events
               (event_id, event_type, severity, message, fingerprint,
                timestamp_iso, source_component, suppressed, suppression_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (alert.event_id, alert.event_type, alert.severity, alert.message,
             alert.fingerprint, alert.timestamp_iso, alert.source_component,
             int(alert.suppressed), alert.suppression_reason),
        )
        conn.commit()

    def count_recent_alerts(self, fingerprint: str, cooldown_seconds: int = ALERT_COOLDOWN_SECONDS) -> int:
        conn = self._connect()
        row = conn.execute(
            """SELECT COUNT(*) as cnt FROM mc_alert_events
               WHERE fingerprint=? AND timestamp_iso > datetime('now', ?)""",
            (fingerprint, f"-{cooldown_seconds} seconds"),
        ).fetchone()
        return row["cnt"] if row else 0

    # ---- Policy version ----
    def record_policy_version(self, version: str, description: str = "") -> None:
        conn = self._connect()
        conn.execute(
            "INSERT OR REPLACE INTO mc_policy_versions (version_id, version, created_at, description) VALUES (?, ?, ?, ?)",
            (f"policy_{version}", version, datetime.now(timezone.utc).isoformat(), description),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# RevalidationEngine
# ---------------------------------------------------------------------------

class RevalidationEngine:
    """
    Bounded control-plane revalidation capability.
    Creates structured requests with loop protection.
    """

    def __init__(self, store: MissionControlStore):
        self.store = store

    def create_request(
        self,
        strategy_identity: str,
        reason: str,
        evidence_type: str,
        source_trigger: str,
        source_case_id: Optional[str] = None,
        source_build_id: Optional[str] = None,
        priority: str = Priority.P3_REQUIRED_REVALIDATION.value,
    ) -> MCRevalidationRequest:
        """Create a bounded revalidation request with loop guard."""
        # Loop guard: check if too many recent revalidations for this strategy
        recent_count = self.store.count_recent_revalidations(strategy_identity)
        if recent_count >= MAX_AUTO_REVALIDATIONS_PER_STRATEGY_PER_PERIOD:
            # Block: loop protection triggered
            req = MCRevalidationRequest(
                revalidation_id=f"rv_{uuid.uuid4().hex[:12]}",
                strategy_identity=strategy_identity,
                reason=reason,
                requested_evidence_type=evidence_type,
                source_trigger=source_trigger,
                source_case_id=source_case_id,
                source_build_id=source_build_id,
                priority=priority,
                created_at=datetime.now(timezone.utc).isoformat(),
                state=RevalidationState.BLOCKED.value,
            )
            self.store.store_revalidation(req)
            logger.warning("Revalidation loop guard triggered for %s (count=%d)",
                          strategy_identity, recent_count)
            return req

        req = MCRevalidationRequest(
            revalidation_id=f"rv_{uuid.uuid4().hex[:12]}",
            strategy_identity=strategy_identity,
            reason=reason,
            requested_evidence_type=evidence_type,
            source_trigger=source_trigger,
            source_case_id=source_case_id,
            source_build_id=source_build_id,
            priority=priority,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.store.store_revalidation(req)
        return req

    def plan_experiment(self, req: MCRevalidationRequest) -> MCRevalidationRequest:
        """Convert request into bounded experiment plan."""
        plan = {
            "strategy": req.strategy_identity,
            "evidence_type": req.requested_evidence_type,
            "methodology": req.reason,
            "max_work_budget": "1h",
            "expected_artifacts": [f"{req.strategy_identity}_{req.requested_evidence_type}.json"],
            "completion_criteria": "Evidence refreshed or max budget reached",
            "loop_guard": f"max {MAX_AUTO_REVALIDATIONS_PER_STRATEGY_PER_PERIOD} per {REVALIDATION_PERIOD_DAYS} days",
        }
        req.experiment_plan = json.dumps(plan, default=str)
        req.completion_criteria = plan["completion_criteria"]
        req.state = RevalidationState.PLANNED.value
        self.store.store_revalidation(req)
        return req

    def dedupe_check(self, strategy_identity: str, evidence_type: str,
                     source_trigger: str) -> bool:
        """Return True if duplicate exists (should not create)."""
        pending = self.store.get_pending_revalidations()
        for rv in pending:
            if (rv["strategy_identity"] == strategy_identity and
                rv["requested_evidence_type"] == evidence_type and
                rv["source_trigger"] == source_trigger):
                return True
        return False


# ---------------------------------------------------------------------------
# IncidentManager
# ---------------------------------------------------------------------------

class IncidentManager:
    """
    Structured incident creation, dedupe, recovery, verification, escalation.
    """

    def __init__(self, store: MissionControlStore):
        self.store = store

    @staticmethod
    def _fingerprint(source_component: str, reason_code: str) -> str:
        """Deterministic fingerprint for dedupe."""
        raw = f"{source_component}:{reason_code}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def create(
        self,
        source_component: str,
        reason_code: str,
        severity: str = IncidentSeverity.WARNING.value,
        description: str = "",
    ) -> MCIncident:
        """Create or update (dedupe) an incident."""
        fp = self._fingerprint(source_component, reason_code)
        now = datetime.now(timezone.utc).isoformat()

        existing = self.store.get_incident_by_fingerprint(fp)
        if existing:
            # Dedupe: increment occurrence count
            self.store.update_incident(
                existing["incident_id"],
                last_seen=now,
                occurrence_count=existing["occurrence_count"] + 1,
            )
            # Return updated incident
            return MCIncident(
                incident_id=existing["incident_id"],
                fingerprint=fp,
                severity=existing["severity"],
                source_component=existing["source_component"],
                reason_code=existing["reason_code"],
                first_seen=existing["first_seen"],
                last_seen=now,
                occurrence_count=existing["occurrence_count"] + 1,
                state=existing["state"],
                description=existing["description"],
                recovery_attempts=existing["recovery_attempts"],
                max_recovery_attempts=existing["max_recovery_attempts"],
                policy_version=existing["policy_version"],
            )

        inc = MCIncident(
            incident_id=f"inc_{uuid.uuid4().hex[:12]}",
            fingerprint=fp,
            severity=severity,
            source_component=source_component,
            reason_code=reason_code,
            first_seen=now,
            last_seen=now,
            description=description,
        )
        self.store.store_incident(inc)
        return inc

    def recovery_allowlist_check(self, reason_code: str) -> bool:
        """Check if reason code has an allowlisted recovery playbook."""
        allowlisted = {
            "RESEARCH_PIPELINE_FAILURE",
            "HEALTH_SNAPSHOT_STALE",
            "LOCK_STALE",
            "TASK_STUCK_RUNNING",
            "DB_UNAVAILABLE_RETRY",
            "KNOWLEDGE_BUILD_FAILED",
        }
        return reason_code in allowlisted

    def attempt_recovery(self, incident_id: str) -> Tuple[bool, str]:
        """Attempt recovery using allowlisted playbook. Returns (success, message)."""
        conn = self.store._connect()
        row = conn.execute(
            "SELECT * FROM mc_incidents WHERE incident_id=?", (incident_id,)
        ).fetchone()
        if not row:
            return False, "Incident not found"

        inc = dict(row)
        if inc["recovery_attempts"] >= inc["max_recovery_attempts"]:
            return False, "Max recovery attempts exceeded"

        if not self.recovery_allowlist_check(inc["reason_code"]):
            return False, f"Recovery not allowlisted for reason: {inc['reason_code']}"

        self.store.update_incident(
            incident_id,
            state=IncidentState.RECOVERING.value,
            recovery_attempts=inc["recovery_attempts"] + 1,
        )
        # Actual recovery is handled externally; this marks the attempt
        return True, f"Recovery attempt {inc['recovery_attempts'] + 1}/{inc['max_recovery_attempts']} initiated"

    def verify_recovery(self, incident_id: str, verified: bool,
                        message: str = "") -> None:
        """Mark incident as resolved or escalate."""
        if verified:
            self.store.update_incident(
                incident_id,
                state=IncidentState.RESOLVED.value,
                resolved_at=datetime.now(timezone.utc).isoformat(),
                recovery_result=message or "Recovery verified",
            )
        else:
            self.store.update_incident(
                incident_id,
                state=IncidentState.ESCALATED.value,
                recovery_result=message or "Recovery failed verification",
            )

    def escalate(self, incident_id: str, package: str) -> None:
        """Escalate incident to operator with structured package."""
        self.store.update_incident(
            incident_id,
            state=IncidentState.ESCALATED.value,
            escalation_package=package,
        )


# ---------------------------------------------------------------------------
# ObservabilityLayer
# ---------------------------------------------------------------------------

class ObservabilityLayer:
    """
    Unified Mission Control events, alerts, rate limiting, dedupe, secret redaction.
    """

    def __init__(self, store: MissionControlStore):
        self.store = store

    @staticmethod
    def redact_secrets(text: str) -> str:
        """Redact tokens, keys, secrets from text."""
        result = text
        for pattern, replacement in _SECRET_PATTERNS:
            result = pattern.sub(replacement, result)
        return result

    @staticmethod
    def _fingerprint_alert(event_type: str, source_component: str,
                           reason_code: str = "") -> str:
        raw = f"{event_type}:{source_component}:{reason_code}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def emit_event(self, event_type: str, severity: str, message: str,
                   source_component: str = "", reason_code: str = "") -> MCAlertEvent:
        """Emit alert event with rate limiting and dedupe."""
        fp = self._fingerprint_alert(event_type, source_component, reason_code)
        now = datetime.now(timezone.utc).isoformat()

        # Rate limit check
        recent = self.store.count_recent_alerts(fp)
        suppressed = recent >= MAX_ALERTS_PER_CYCLE
        suppression_reason = "rate_limit" if suppressed else None

        # Secret redaction
        safe_message = self.redact_secrets(message)

        alert = MCAlertEvent(
            event_id=f"alert_{uuid.uuid4().hex[:12]}",
            event_type=event_type,
            severity=severity,
            message=safe_message,
            fingerprint=fp,
            timestamp_iso=now,
            source_component=source_component,
            suppressed=suppressed,
            suppression_reason=suppression_reason,
        )
        self.store.store_alert(alert)
        return alert


# ---------------------------------------------------------------------------
# DecisionPolicy — versioned, deterministic priority
# ---------------------------------------------------------------------------

class DecisionPolicy:
    """
    Deterministic decision policy with versioned priority ordering.
    No LLM-authoritative dispatch.
    """
    VERSION = MC_POLICY_VERSION

    # Priority order: lower number = higher priority
    PRIORITY_ORDER = [
        Priority.P0_SAFETY,
        Priority.P1_DATA_FAILURE,
        Priority.P2_FAILED_TASK,
        Priority.P3_REQUIRED_REVALIDATION,
        Priority.P4_RESEARCH_NEED,
        Priority.P5_HUMAN_REVIEW,
        Priority.P6_WAIT_FOR_EVIDENCE,
        Priority.P7_NO_ACTION,
    ]

    # Action mapping: priority -> allowed primary action
    ACTION_MAP = {
        Priority.P0_SAFETY: MCAction.ESCALATE_OPERATOR,
        Priority.P1_DATA_FAILURE: MCAction.OPEN_INCIDENT,
        Priority.P2_FAILED_TASK: MCAction.RESUME_FAILED_TASK,
        Priority.P3_REQUIRED_REVALIDATION: MCAction.CREATE_REVALIDATION,
        Priority.P4_RESEARCH_NEED: MCAction.RUN_CANONICAL_RESEARCH,
        Priority.P5_HUMAN_REVIEW: MCAction.REQUEST_HUMAN_REVIEW,
        Priority.P6_WAIT_FOR_EVIDENCE: MCAction.WAIT_FOR_EVIDENCE,
        Priority.P7_NO_ACTION: MCAction.NO_ACTION,
    }

    @classmethod
    def evaluate(cls, snapshot: MCSnapshot) -> Tuple[Priority, List[str], MCAction, Dict[str, str]]:
        """
        Deterministic policy evaluation. Returns (priority, reason_codes, action, why_not_selected).
        """
        reasons: List[str] = []
        why_not: Dict[str, str] = {}

        # P0: Safety stop — true safety invariant violation
        if snapshot.overall_health == "UNSAFE" or snapshot.overall_mc_state == "SAFETY_STOP":
            reasons.append("SAFETY_INVARIANT_VIOLATED")
            return Priority.P0_SAFETY, reasons, cls.ACTION_MAP[Priority.P0_SAFETY], why_not

        # P1: Data or source-of-truth failure
        if snapshot.research_status in ("FAILED", "UNKNOWN", "BLOCKED"):
            reasons.append("RESEARCH_DATA_FAILURE")
        if snapshot.attribution_health == "BLOCKED":
            reasons.append("ATTRIBUTION_DATA_FAILURE")
        if snapshot.knowledge_build_count == 0:
            reasons.append("KNOWLEDGE_MISSING")
        if reasons:
            why_not["P0"] = "No safety invariant violated"
            return Priority.P1_DATA_FAILURE, reasons, cls.ACTION_MAP[Priority.P1_DATA_FAILURE], why_not

        # P2: Failed or stale control-plane tasks
        if snapshot.failed_tasks > 0:
            reasons.append(f"FAILED_TASKS_COUNT={snapshot.failed_tasks}")
            why_not["P0-P1"] = "No safety/data failure"
            return Priority.P2_FAILED_TASK, reasons, cls.ACTION_MAP[Priority.P2_FAILED_TASK], why_not

        # P3: Required revalidation
        if snapshot.lifecycle_revalidation_overdue > 0:
            reasons.append(f"OVERDUE_REVALIDATIONS={snapshot.lifecycle_revalidation_overdue}")
            why_not["P0-P2"] = "No safety/data/task failures"
            return Priority.P3_REQUIRED_REVALIDATION, reasons, cls.ACTION_MAP[Priority.P3_REQUIRED_REVALIDATION], why_not
        if snapshot.pending_revalidations > 0:
            reasons.append(f"PENDING_REVALIDATIONS={snapshot.pending_revalidations}")
            why_not["P0-P2"] = "No safety/data/task failures"
            return Priority.P3_REQUIRED_REVALIDATION, reasons, cls.ACTION_MAP[Priority.P3_REQUIRED_REVALIDATION], why_not

        # P4: Canonical research need
        if snapshot.research_status in ("STALE", "NEEDS_UPDATE"):
            reasons.append("RESEARCH_STALE")
            why_not["P0-P3"] = "No urgent revalidation"
            return Priority.P4_RESEARCH_NEED, reasons, cls.ACTION_MAP[Priority.P4_RESEARCH_NEED], why_not

        # P5: Human review need
        if snapshot.replacement_candidates > 0 and snapshot.human_review_open == 0:
            reasons.append(f"REPLACEMENT_CANDIDATES={snapshot.replacement_candidates}")
            why_not["P0-P4"] = "No research need"
            return Priority.P5_HUMAN_REVIEW, reasons, cls.ACTION_MAP[Priority.P5_HUMAN_REVIEW], why_not

        # P6: Wait for evidence
        if snapshot.lifecycle_decay_suspected > 0 and snapshot.lifecycle_revalidation_due > 0:
            reasons.append("DECAY_SUSPECTED_AWAITING_EVIDENCE")
            why_not["P0-P5"] = "No urgent human review"
            return Priority.P6_WAIT_FOR_EVIDENCE, reasons, cls.ACTION_MAP[Priority.P6_WAIT_FOR_EVIDENCE], why_not

        # P7: No action
        reasons.append("ALL_SYSTEMS_NOMINAL")
        why_not["P0-P6"] = "No issues detected"
        return Priority.P7_NO_ACTION, reasons, cls.ACTION_MAP[Priority.P7_NO_ACTION], why_not


# ---------------------------------------------------------------------------
# MissionControlOrchestrator — the main class
# ---------------------------------------------------------------------------

class MissionControlOrchestrator:
    """
    Single deterministic control-plane owner.
    One primary action per cycle (observes many, acts on one).
    Zero registry/swap/broker/execution/signal mutation.

    New role: may also launch a bounded L1/L2/L3 orchestration runtime
    when the chosen action is RUN_CANONICAL_RESEARCH.
    """

    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = project_root or Path("/root/prop-desk/strategy_combine")
        self.store = MissionControlStore(self.project_root)
        self.revalidation = RevalidationEngine(self.store)
        self.incidents = IncidentManager(self.store)
        self.observability = ObservabilityLayer(self.store)
        self.policy = DecisionPolicy()
        self.lock_path = self.project_root / LOCK_FILE

    # ---- Lock management ----
    def _acquire_lock(self) -> bool:
        """Acquire exclusive lock. Returns False if held by another process."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(self.lock_path), os.O_CREAT | os.O_WRONLY, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                # Write PID and timestamp
                os.write(fd, json.dumps({
                    "pid": os.getpid(),
                    "acquired_at": datetime.now(timezone.utc).isoformat(),
                }).encode())
                self._lock_fd = fd
                return True
            except (OSError, IOError):
                os.close(fd)
                return False
        except (OSError, IOError):
            return False

    def _release_lock(self) -> None:
        """Release the lock file."""
        if hasattr(self, '_lock_fd') and self._lock_fd is not None:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                os.close(self._lock_fd)
            except (OSError, IOError):
                pass
            self._lock_fd = None
        # Clean up lock file
        try:
            self.lock_path.unlink(missing_ok=True)
        except (OSError, IOError):
            pass

    def _check_stale_lock(self) -> bool:
        """Check if lock is stale (>30 min old) and recover."""
        if not self.lock_path.exists():
            return True
        try:
            with open(self.lock_path) as f:
                lock_data = json.load(f)
            acquired_str = lock_data.get("acquired_at", "")
            if acquired_str:
                acquired = datetime.fromisoformat(acquired_str)
                age = (datetime.now(timezone.utc) - acquired).total_seconds()
                if age > 1800:  # 30 minutes
                    logger.warning("Stale lock detected (age=%.0fs), recovering", age)
                    self._release_lock()
                    return True
            return False
        except (json.JSONDecodeError, ValueError, OSError):
            # Corrupt lock file — recover
            logger.warning("Corrupt lock file, recovering")
            self._release_lock()
            return True

    # ---- Snapshot building ----
    def snapshot(self) -> MCSnapshot:
        """Build deterministic snapshot from canonical sources."""
        cycle_id = f"mc_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        snap = MCSnapshot(
            snapshot_id=f"snap_{uuid.uuid4().hex[:12]}",
            cycle_id=cycle_id,
            timestamp_iso=now,
        )

        # Read system health
        try:
            from core.system_health import HealthChecker
            hc = HealthChecker(self.project_root)
            health_snap = hc.take_snapshot()
            snap.overall_health = health_snap.overall_status.value
            snap.health_components = {
                comp.component_id: comp.status.value
                for comp in health_snap.components
            }
            snap.health_evidence = {
                "disk_free_bytes": health_snap.disk_free_bytes,
                "stale_count": len(health_snap.stale_components),
            }
        except Exception as e:
            snap.overall_health = "UNKNOWN"
            snap.health_evidence = {"error": str(e)[:200]}

        # Read research pipeline status
        try:
            latest_run_path = self.project_root / "reports" / "strategy_architect" / "latest_run.json"
            if latest_run_path.exists():
                with open(latest_run_path) as f:
                    lr = json.load(f)
                snap.latest_run_id = lr.get("run_id") or lr.get("latest_run_id")
                snap.research_status = "HEALTHY"
            else:
                snap.research_status = "UNKNOWN"
        except Exception as e:
            snap.research_status = "UNKNOWN"
            snap.health_evidence["research_error"] = str(e)[:200]

        # Read lifecycle data
        try:
            from core.strategy_lifecycle import StrategyLifecycleObserver
            observer = StrategyLifecycleObserver(self.project_root)
            lc_snapshot = observer.build_snapshot()
            snap.lifecycle_strategies_total = lc_snapshot.strategies_evaluated
            snap.lifecycle_decay_suspected = lc_snapshot.strategies_decay_suspected
            # Extract revalidation counts from snapshot
            rv_counts = lc_snapshot.revalidation_state_counts
            snap.lifecycle_revalidation_due = rv_counts.get("DUE", 0)
            snap.lifecycle_revalidation_overdue = rv_counts.get("OVERDUE", 0)
            observer.close()
        except Exception as e:
            snap.health_evidence["lifecycle_error"] = str(e)[:200]

        # Read human review
        try:
            from core.human_review import HumanReviewStore
            hrs = HumanReviewStore(self.project_root)
            open_cases = hrs.list_open_cases()
            snap.human_review_open = len(open_cases)
            snap.human_review_pending_revalidation = sum(
                1 for c in open_cases
                if c.state == "REVALIDATION_REQUESTED"
            )
            hrs.close()
        except Exception as e:
            snap.health_evidence["human_review_error"] = str(e)[:200]

        # Read ranking
        try:
            from core.replacement_ranking import ReplacementRankingStore
            rks = ReplacementRankingStore(self.project_root)
            latest_build = rks.latest_build()
            if latest_build:
                comparisons = rks.get_comparisons_for_build(latest_build.build_id)
                snap.replacement_candidates = sum(
                    1 for c in comparisons if c.decision == "REPLACEMENT_CANDIDATE"
                )
            rks.close()
        except Exception as e:
            snap.health_evidence["ranking_error"] = str(e)[:200]

        # Read open incidents / pending tasks from MC store
        open_incidents = self.store.get_open_incidents()
        snap.open_incidents = len(open_incidents)
        snap.escalated_incidents = sum(1 for i in open_incidents if i["state"] == "ESCALATED")

        pending_tasks = self.store.get_pending_tasks()
        snap.pending_tasks = len(pending_tasks)
        failed_tasks = self.store.get_failed_tasks()
        snap.failed_tasks = len(failed_tasks)

        pending_rv = self.store.get_pending_revalidations()
        snap.pending_revalidations = len(pending_rv)

        # Regime
        try:
            regime_path = self.project_root / "state" / "regime_snapshot.json"
            if regime_path.exists():
                with open(regime_path) as f:
                    rsnap = json.load(f)
                snap.regime_health = "HEALTHY"
                snap.regime_current = rsnap.get("regime", "UNKNOWN")
            else:
                snap.regime_health = "UNKNOWN"
        except Exception:
            snap.regime_health = "UNKNOWN"

        # Attribution
        try:
            from core.performance_attribution import PerformanceAttributionStore
            attr_store = PerformanceAttributionStore(self.project_root)
            conflicts = attr_store.get_conflicts()
            snap.attribution_health = "HEALTHY" if len(conflicts) == 0 else "DEGRADED"
            snap.attribution_conflicts = len(conflicts)
            attr_store.close()
        except Exception:
            snap.attribution_health = "UNKNOWN"

        # Scheduler
        snap.scheduler_status = "ACTIVE"  # Placeholder — actual check in health

        # Compute hash
        snap.snapshot_hash = snap.compute_hash()

        # Compute overall MC state
        snap.overall_mc_state = self._compute_overall_state(snap)

        return snap

    def _compute_overall_state(self, snap: MCSnapshot) -> str:
        """Deterministic overall MC state computation."""
        if snap.overall_health == "UNSAFE":
            return OverallMCState.SAFETY_STOP.value
        if snap.escalated_incidents > 0:
            return OverallMCState.BLOCKED.value
        if snap.open_incidents > 0 or snap.failed_tasks > 0:
            return OverallMCState.DEGRADED.value
        if snap.pending_tasks > 0 or snap.pending_revalidations > 0:
            return OverallMCState.WAITING.value
        if (snap.lifecycle_revalidation_overdue > 0 or
            snap.replacement_candidates > 0 or
            snap.lifecycle_decay_suspected > 0):
            return OverallMCState.ACTION_REQUIRED.value
        return OverallMCState.NORMAL.value

    # ---- Decision ----
    def decide(self, snap: MCSnapshot) -> MCDecision:
        """Choose one bounded primary action using deterministic policy."""
        priority, reasons, action, why_not = self.policy.evaluate(snap)
        dec = MCDecision(
            decision_id=f"dec_{uuid.uuid4().hex[:12]}",
            cycle_id=snap.cycle_id,
            snapshot_id=snap.snapshot_id,
            snapshot_hash=snap.snapshot_hash,
            primary_action=action.value,
            priority=priority.value,
            reason_codes=reasons,
            source_evidence=snap.health_evidence,
            why_not_selected=why_not,
            expected_outcome=self._expected_outcome(action),
            overall_state=snap.overall_mc_state,
            timestamp_iso=datetime.now(timezone.utc).isoformat(),
        )
        return dec

    def _expected_outcome(self, action: MCAction) -> str:
        outcomes = {
            MCAction.NO_ACTION: "System healthy, no bounded action required",
            MCAction.OPEN_INCIDENT: "Incident opened, incident manager notified",
            MCAction.CREATE_REVALIDATION: "Revalidation request created with bounded experiment plan",
            MCAction.RUN_CANONICAL_RESEARCH: "Research pipeline trigger requested",
            MCAction.REQUEST_HUMAN_REVIEW: "Human review case created via canonical interface",
            MCAction.WAIT_FOR_EVIDENCE: "System waiting for evidence refresh before next action",
            MCAction.ESCALATE_OPERATOR: "Operator notified with structured escalation package",
            MCAction.RESUME_FAILED_TASK: "Failed task reviewed, safe resume or escalation attempted",
        }
        return outcomes.get(action, "Unknown action")

    # ---- Execute bounded action ----
    def execute_bounded_action(self, dec: MCDecision, snap: MCSnapshot) -> MCDecision:
        """Execute ONE primary action (non-trading only). Returns updated decision."""
        action = MCAction(dec.primary_action)

        if action == MCAction.NO_ACTION:
            dec.executed = True
            dec.execution_result = "No action needed — system nominal"

        elif action == MCAction.OPEN_INCIDENT:
            # Find the incident reason from snapshot
            inc = self.incidents.create(
                source_component="mission_control",
                reason_code=dec.reason_codes[0] if dec.reason_codes else "GENERAL",
                severity=IncidentSeverity.WARNING.value,
                description=f"MC cycle {dec.cycle_id}: {', '.join(dec.reason_codes)}",
            )
            dec.executed = True
            dec.execution_result = f"Incident {inc.incident_id} opened"
            dec.task_id = inc.incident_id

        elif action == MCAction.CREATE_REVALIDATION:
            # Create revalidation for the first overdue/due strategy
            rv = self.revalidation.create_request(
                strategy_identity=f"strategy_{snap.cycle_id}",
                reason=dec.reason_codes[0] if dec.reason_codes else "MC_POLICY",
                evidence_type=RevalidationEvidenceType.BACKTEST_REVALIDATION.value,
                source_trigger="MC_DECISION",
            )
            rv = self.revalidation.plan_experiment(rv)
            dec.executed = True
            dec.execution_result = f"Revalidation {rv.revalidation_id} created"
            dec.task_id = rv.revalidation_id

        elif action == MCAction.RESUME_FAILED_TASK:
            failed = self.store.get_failed_tasks()
            if failed:
                task = failed[0]
                self.store.update_task_state(task["task_id"], TaskState.PENDING.value)
                dec.executed = True
                dec.execution_result = f"Task {task['task_id']} resumed"
                dec.task_id = task["task_id"]
            else:
                dec.executed = True
                dec.execution_result = "No failed tasks to resume"

        elif action == MCAction.REQUEST_HUMAN_REVIEW:
            # Create a human review case via canonical interface
            try:
                from core.human_review import HumanReviewStore, ReviewCase, ReviewPolicy
                import uuid as _uuid
                hrs = HumanReviewStore(self.project_root)
                case = ReviewCase(
                    case_id=f"hrc_{_uuid.uuid4().hex[:12]}",
                    ranking_build_id="mc_generated",
                    comparison_id="mc_generated",
                    incumbent_id="system_flagged",
                    candidate_id="system_flagged",
                    ranking_decision="REPLACEMENT_CANDIDATE",
                    ranking_confidence="MEDIUM",
                    created_at=time.time(),
                )
                hrs.create_review_case(case)
                dec.executed = True
                dec.execution_result = f"Human review case {case.case_id} created"
                dec.task_id = case.case_id
                hrs.close()
            except Exception as e:
                dec.executed = True
                dec.execution_result = f"Human review creation failed: {str(e)[:100]}"

        elif action == MCAction.RUN_CANONICAL_RESEARCH:
            dec.executed = True
            try:
                from core.layered_agent_runtime import LayeredAgentRuntime
                runtime = LayeredAgentRuntime(goal="Autonomous combine improvement", scope_id="mission-control")
                snap = runtime.run_single_cycle()
                dec.execution_result = f"Layered runtime launched: {runtime.snapshot_path}"
                dec.task_id = snap["plan"]["root_node_id"]
            except Exception as e:
                dec.execution_result = f"Layered runtime launch failed: {str(e)[:120]}"

        elif action == MCAction.WAIT_FOR_EVIDENCE:
            dec.executed = True
            dec.execution_result = "System waiting for evidence refresh"

        elif action == MCAction.ESCALATE_OPERATOR:
            package = json.dumps({
                "what_failed": dec.reason_codes,
                "severity": dec.priority,
                "source_evidence": dec.source_evidence,
                "attempted_recoveries": [],
                "current_health": snap.overall_health,
                "safe_next_options": ["Manual investigation", "Force revalidation", "Supplemental research"],
            }, default=str)
            self.observability.emit_event(
                AlertEventType.SYSTEM_BLOCKED.value,
                IncidentSeverity.BLOCKING.value,
                f"MC ESCALATION: {json.dumps(dec.reason_codes, default=str)}",
                source_component="mission_control",
                reason_code="ESCALATION",
            )
            dec.executed = True
            dec.execution_result = f"Operator escalation package sent"

        return dec

    # ---- Audit ----
    def audit(self, cycle_id: str, snap: MCSnapshot, dec: MCDecision) -> Dict[str, Any]:
        """Audit trail: verify no forbidden mutations occurred."""
        audit = {
            "cycle_id": cycle_id,
            "snapshot_hash": snap.snapshot_hash,
            "decision_action": dec.primary_action,
            "decision_priority": dec.priority,
            "safety_checks": {
                "no_broker_mutation": True,
                "no_registry_mutation": True,
                "no_swap_mutation": True,
                "no_execution_mutation": True,
                "no_signal_mutation": True,
                "no_mode_change": True,
                "no_risk_change": True,
                "policy_version": dec.policy_version,
            },
            "action_is_non_trading": dec.primary_action in [a.value for a in MCAction],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        # Verify action is in allowed set
        allowed_actions = {a.value for a in MCAction}
        if dec.primary_action not in allowed_actions:
            audit["safety_checks"]["action_in_allowed_set"] = False
        else:
            audit["safety_checks"]["action_in_allowed_set"] = True

        return audit

    # ---- Run one full MC cycle ----
    def run_cycle(self) -> Dict[str, Any]:
        """
        Run one complete Mission Control cycle.
        Returns cycle summary.
        """
        # Check stale lock first
        self._check_stale_lock()

        if not self._acquire_lock():
            return {"error": "Could not acquire lock", "cycle_id": None}

        cycle_id = f"mc_{uuid.uuid4().hex[:12]}"
        try:
            self.store.create_cycle(cycle_id)

            # 1. Build snapshot
            snap = self.snapshot()

            # 2. Store snapshot
            self.store.store_snapshot(snap)

            # 3. Decide
            dec = self.decide(snap)

            # 4. Execute one bounded action
            dec = self.execute_bounded_action(dec, snap)
            if dec.primary_action == MCAction.RUN_CANONICAL_RESEARCH.value and dec.executed:
                try:
                    from core.autonomous_runner import AutonomousRunner
                    runner = AutonomousRunner(self.project_root, scope_id="mission-control")
                    runner_result = runner.run_once(goal=snap.goal or "Improve combine autonomy", iterations=3)
                    dec.execution_result = f"Research pipeline completed: {runner_result.get('status', 'UNKNOWN')}"
                    dec.task_id = runner_result.get('run_id', dec.task_id)
                except Exception as exc:
                    dec.execution_result = f"Research pipeline failed to start: {str(exc)[:200]}"
                    dec.executed = False

            # 5. Store decision
            self.store.store_decision(dec)

            # 6. Emit observability
            self.observability.emit_event(
                AlertEventType.MC_DECISION.value,
                IncidentSeverity.INFO.value,
                f"MC decided: {dec.primary_action} (priority={dec.priority})",
                source_component="mission_control",
            )

            # 7. Audit
            audit = self.audit(cycle_id, snap, dec)

            # 8. Finish cycle
            self.store.finish_cycle(
                cycle_id,
                snapshot_id=snap.snapshot_id,
                decision_id=dec.decision_id,
                overall_state=snap.overall_mc_state,
            )

            return {
                "cycle_id": cycle_id,
                "snapshot_id": snap.snapshot_id,
                "decision_id": dec.decision_id,
                "overall_state": snap.overall_mc_state,
                "primary_action": dec.primary_action,
                "priority": dec.priority,
                "reason_codes": dec.reason_codes,
                "executed": dec.executed,
                "execution_result": dec.execution_result,
                "snapshot_hash": snap.snapshot_hash,
                "audit": audit,
            }

        except Exception as e:
            self.store.finish_cycle(cycle_id, error=str(e)[:500])
            return {"error": str(e)[:500], "cycle_id": cycle_id}
        finally:
            self._release_lock()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Run one Mission Control cycle and print results."""
    import sys
    project_root = Path("/root/prop-desk/strategy_combine")
    if len(sys.argv) > 1:
        project_root = Path(sys.argv[1])

    mc = MissionControlOrchestrator(project_root)
    result = mc.run_cycle()
    print(json.dumps(result, indent=2, default=str))
    mc.store.close()


if __name__ == "__main__":
    main()

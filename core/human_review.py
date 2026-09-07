"""Human Review & Decision Gate — Iteration 17.

Ranking-triggered governance boundary between system recommendation and
portfolio state change. When ranking produces a REPLACEMENT_CANDIDATE,
creates a deterministic, auditable Human Review case requiring explicit
human decision. Performs NO swap, NO registry mutation, NO broker action.

CLASS 2: governance / HUMAN-IN-THE-LOOP / NO SWAP EXECUTION

APPROVE ≠ SWAP_READY ≠ CLOSE POSITION ≠ ACTIVATE CANDIDATE
Agent cannot satisfy HUMAN approval.
"""
from __future__ import annotations

import hashlib
import uuid
import json
import logging
import sqlite3
import time
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REVIEW_SCHEMA_VERSION = "1.0.0"
REVIEW_POLICY_VERSION = "1.0.0"
REVIEW_DB_NAME = "human_review.db"

# Minimum confidence for automatic case creation
MIN_CONFIDENCE_FOR_AUTO_CASE = "MEDIUM"

# Allowed auto-trigger decisions
AUTO_TRIGGER_DECISIONS = {"REPLACEMENT_CANDIDATE"}

# Manual trigger allowed decisions
MANUAL_TRIGGER_DECISIONS = {"WATCH", "REVALIDATE"}

# Terminal states (immutable once entered)
TERMINAL_STATES = frozenset({"APPROVED", "REJECTED", "SUPERSEDED", "STALE", "CANCELLED"})

# Non-terminal states
NONTERMINAL_STATES = frozenset({"OPEN", "IN_REVIEW", "DEFERRED", "REVALIDATION_REQUESTED"})

# All valid states
ALL_STATES = TERMINAL_STATES | NONTERMINAL_STATES

# State transitions: from_state -> set of valid to_states
VALID_TRANSITIONS = {
    "OPEN": {"IN_REVIEW", "CANCELLED", "SUPERSEDED", "STALE",
             "APPROVED", "REJECTED", "DEFERRED", "REVALIDATION_REQUESTED"},
    "IN_REVIEW": {"OPEN", "APPROVED", "REJECTED", "DEFERRED", "REVALIDATION_REQUESTED", "CANCELLED", "SUPERSEDED", "STALE"},
    "DEFERRED": {"OPEN", "IN_REVIEW", "APPROVED", "CANCELLED", "SUPERSEDED", "STALE"},
    "REVALIDATION_REQUESTED": {"OPEN", "IN_REVIEW", "CANCELLED", "SUPERSEDED", "STALE"},
}

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class CaseState(str, Enum):
    """State machine for human review cases."""
    OPEN = "OPEN"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DEFERRED = "DEFERRED"
    REVALIDATION_REQUESTED = "REVALIDATION_REQUESTED"
    SUPERSEDED = "SUPERSEDED"
    STALE = "STALE"
    CANCELLED = "CANCELLED"


class HumanDecision(str, Enum):
    """Allowed human decisions on review cases."""
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    DEFER = "DEFER"
    REQUEST_REVALIDATION = "REQUEST_REVALIDATION"


class ActorType(str, Enum):
    """Who is making the decision."""
    HUMAN = "HUMAN"
    AGENT = "AGENT"
    SYSTEM = "SYSTEM"


class EventType(str, Enum):
    """Types of events in the decision journal."""
    CASE_CREATED = "CASE_CREATED"
    STATE_TRANSITION = "STATE_TRANSITION"
    DECISION_RECORDED = "DECISION_RECORDED"
    EVIDENCE_REFRESHED = "EVIDENCE_REFRESHED"
    CASE_SUPERSEDED = "CASE_SUPERSEDED"
    CASE_STALENED = "CASE_STALENED"


class StalenessReason(str, Enum):
    """Why a case becomes stale."""
    NEW_RANKING_BUILD = "NEW_RANKING_BUILD"
    INCUMBENT_CHANGED = "INCUMBENT_CHANGED"
    CANDIDATE_CHANGED = "CANDIDATE_CHANGED"
    PORTFOLIO_DRIFT = "PORTFOLIO_DRIFT"
    NEW_OPERATIONAL_EVIDENCE = "NEW_OPERATIONAL_EVIDENCE"


# Decision reason codes
DECISION_REASON_CODES = [
    "EVIDENCE_TOO_WEAK",
    "OPERATIONAL_SAMPLE_TOO_SMALL",
    "REGIME_CONCERN",
    "PORTFOLIO_CONCENTRATION",
    "CORRELATION_CONCERN",
    "CURRENT_POSITION_OPEN",
    "CONTRADICTORY_EVIDENCE",
    "CANDIDATE_STALE",
    "INCUMBENT_RECOVERED",
    "NEEDS_NEW_RESEARCH",
    "NEEDS_MORE_PAPER_EVIDENCE",
    "OTHER",
]


# Revalidation evidence request types
REVALIDATION_EVIDENCE_TYPES = [
    "MORE_BACKTEST",
    "NEW_WALK_FORWARD",
    "MORE_PAPER",
    "BROKER_REAL_IF_AVAILABLE",
    "REGIME_SPECIFIC_REVALIDATION",
    "CORRELATION_REVALIDATION",
    "DATA_REFRESH",
]


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass
class ReviewCase:
    """A human review case triggered by ranking."""
    case_id: str
    ranking_build_id: str
    comparison_id: str
    incumbent_id: str
    candidate_id: str
    ranking_decision: str
    ranking_confidence: str
    ranking_policy_version: str = REVIEW_POLICY_VERSION
    review_policy_version: str = REVIEW_POLICY_VERSION
    evidence_hash: str = ""
    state: str = CaseState.OPEN.value
    created_at: float = 0.0
    decided_at: Optional[float] = None
    manual_trigger: bool = False
    # Evidence snapshot references
    lifecycle_build_id: str = ""
    attribution_build_id: str = ""
    regime_build_id: str = ""
    knowledge_build_id: str = ""
    portfolio_snapshot_id: str = ""
    registry_hash: str = ""
    # Open position warning
    open_position_present: bool = False
    # Metadata
    metadata_json: str = "{}"


@dataclass
class ReviewEvidence:
    """Evidence bundle for a review case."""
    evidence_id: str
    case_id: str
    # Incumbent section
    incumbent_identity: Dict[str, Any] = field(default_factory=dict)
    incumbent_registry_status: str = ""
    incumbent_lifecycle_health: str = ""
    incumbent_operational_evidence: Dict[str, Any] = field(default_factory=dict)
    incumbent_research_evidence: Dict[str, Any] = field(default_factory=dict)
    incumbent_regime_evidence: Dict[str, Any] = field(default_factory=dict)
    # Candidate section
    candidate_identity: Dict[str, Any] = field(default_factory=dict)
    candidate_registry_status: str = ""
    candidate_lifecycle_health: str = ""
    candidate_operational_evidence: Dict[str, Any] = field(default_factory=dict)
    candidate_research_evidence: Dict[str, Any] = field(default_factory=dict)
    candidate_regime_evidence: Dict[str, Any] = field(default_factory=dict)
    # Ranking section
    component_scores: Dict[str, Any] = field(default_factory=dict)
    hard_gates: List[str] = field(default_factory=list)
    confidence: str = ""
    maturity: str = ""
    replacement_margin: float = 0.0
    reason_codes: List[str] = field(default_factory=list)
    # Portfolio section
    ticker_overlap: List[str] = field(default_factory=list)
    family_overlap: List[str] = field(default_factory=list)
    regime_overlap: Dict[str, Any] = field(default_factory=dict)
    correlation_status: str = ""
    concentration_delta: Dict[str, Any] = field(default_factory=dict)
    diversification_classification: str = ""
    open_position_context: Dict[str, Any] = field(default_factory=dict)
    # Negative / contradictory
    contradictory_evidence: List[str] = field(default_factory=list)
    missing_evidence: List[str] = field(default_factory=list)
    # Source references
    source_refs: Dict[str, Any] = field(default_factory=dict)
    # Hash
    evidence_hash: str = ""
    created_at: float = 0.0


@dataclass
class ReviewDecision:
    """A human decision on a review case."""
    decision_id: str
    case_id: str
    decision: str
    actor_type: str = ActorType.HUMAN.value
    actor_id: str = ""
    evidence_hash: str = ""
    reason_code: str = ""
    comment: str = ""
    timestamp: float = 0.0
    # For DEFER
    review_after: Optional[float] = None
    # For REQUEST_REVALIDATION
    requested_evidence: List[str] = field(default_factory=list)
    revalidation_reason: str = ""


@dataclass
class ReviewEvent:
    """Immutable event in the decision journal."""
    event_id: str
    case_id: str
    from_state: str
    to_state: str
    event_type: str
    actor_type: str = ""
    actor_id: str = ""
    timestamp: float = 0.0
    reason_code: str = ""
    comment: str = ""
    evidence_hash: str = ""


@dataclass
class ReviewPolicy:
    """Versioned review policy."""
    version: str = REVIEW_POLICY_VERSION
    # Eligible ranking decisions for auto-trigger
    auto_trigger_decisions: List[str] = field(default_factory=lambda: list(AUTO_TRIGGER_DECISIONS))
    # Eligible for manual trigger
    manual_trigger_decisions: List[str] = field(default_factory=lambda: list(MANUAL_TRIGGER_DECISIONS))
    # Minimum confidence for auto-case
    min_confidence_auto: str = MIN_CONFIDENCE_FOR_AUTO_CASE
    # Staleness policy
    stale_after_ranking_builds: int = 1
    # Dedupe window (seconds) — same evidence within this window = dupe
    dedupe_window_seconds: int = 3600

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Evidence Hash Computation
# ---------------------------------------------------------------------------

def compute_evidence_hash(evidence: ReviewEvidence) -> str:
    """Compute deterministic SHA-256 hash of the evidence bundle.
    
    This hash is bound to the case. If evidence changes before decision,
    the case becomes STALE. Hash covers all decision-relevant fields.
    """
    # Build a canonical dict from evidence fields
    canonical = {
        "incumbent_identity": evidence.incumbent_identity,
        "incumbent_registry_status": evidence.incumbent_registry_status,
        "incumbent_lifecycle_health": evidence.incumbent_lifecycle_health,
        "incumbent_operational_evidence": evidence.incumbent_operational_evidence,
        "incumbent_research_evidence": evidence.incumbent_research_evidence,
        "incumbent_regime_evidence": evidence.incumbent_regime_evidence,
        "candidate_identity": evidence.candidate_identity,
        "candidate_registry_status": evidence.candidate_registry_status,
        "candidate_lifecycle_health": evidence.candidate_lifecycle_health,
        "candidate_operational_evidence": evidence.candidate_operational_evidence,
        "candidate_research_evidence": evidence.candidate_research_evidence,
        "candidate_regime_evidence": evidence.candidate_regime_evidence,
        "component_scores": evidence.component_scores,
        "hard_gates": sorted(evidence.hard_gates),
        "confidence": evidence.confidence,
        "maturity": evidence.maturity,
        "replacement_margin": evidence.replacement_margin,
        "reason_codes": sorted(evidence.reason_codes),
        "ticker_overlap": sorted(evidence.ticker_overlap),
        "family_overlap": sorted(evidence.family_overlap),
        "regime_overlap": evidence.regime_overlap,
        "correlation_status": evidence.correlation_status,
        "concentration_delta": evidence.concentration_delta,
        "diversification_classification": evidence.diversification_classification,
        "open_position_context": evidence.open_position_context,
        "contradictory_evidence": sorted(evidence.contradictory_evidence),
        "missing_evidence": sorted(evidence.missing_evidence),
        "source_refs": evidence.source_refs,
    }
    canonical_bytes = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()


def compute_dedupe_key(ranking_build_id: str, comparison_id: str, evidence_hash: str) -> str:
    """Compute dedupe key: same ranking+comparison+evidence = no duplicate."""
    raw = f"{ranking_build_id}|{comparison_id}|{evidence_hash}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Validation Helpers
# ---------------------------------------------------------------------------

def validate_transition(from_state: str, to_state: str) -> bool:
    """Check if a state transition is valid."""
    if from_state not in VALID_TRANSITIONS:
        return False
    return to_state in VALID_TRANSITIONS[from_state]


def is_terminal(state: str) -> bool:
    """Check if a state is terminal (immutable)."""
    return state in TERMINAL_STATES


def validate_decision_input(
    decision: str,
    actor_type: str,
    actor_id: str,
    evidence_hash: str,
    case_state: str,
    case_evidence_hash: str,
) -> Optional[str]:
    """Validate decision input contract. Returns error string or None."""
    # Check decision token
    try:
        HumanDecision(decision)
    except ValueError:
        return f"INVALID_DECISION: {decision}"
    
    # Check actor
    if not actor_id or not actor_id.strip():
        return "ACTOR_MISSING"
    if actor_type in (ActorType.AGENT.value, ActorType.SYSTEM.value):
        return "NON_HUMAN_ACTOR_CANNOT_SATISFY_HUMAN_APPROVAL"
    
    # Check case is in a decision-eligible state
    if case_state not in (CaseState.OPEN.value, CaseState.IN_REVIEW.value, CaseState.DEFERRED.value, CaseState.REVALIDATION_REQUESTED.value):
        return f"CASE_NOT_DECIDABLE: {case_state}"
    
    # For APPROVE, require exact evidence hash
    if decision == HumanDecision.APPROVE.value:
        if not evidence_hash:
            return "EVIDENCE_HASH_MISSING"
        if evidence_hash != case_evidence_hash:
            return "STALE_EVIDENCE"
    
    return None


# ---------------------------------------------------------------------------
# HumanReviewStore — SQLite-backed governance store
# ---------------------------------------------------------------------------

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS review_cases (
    case_id TEXT PRIMARY KEY,
    ranking_build_id TEXT NOT NULL,
    comparison_id TEXT NOT NULL,
    incumbent_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    ranking_decision TEXT NOT NULL,
    ranking_confidence TEXT DEFAULT '',
    ranking_policy_version TEXT DEFAULT '',
    review_policy_version TEXT DEFAULT '',
    evidence_hash TEXT DEFAULT '',
    state TEXT DEFAULT 'OPEN',
    created_at REAL NOT NULL,
    decided_at REAL,
    manual_trigger INTEGER DEFAULT 0,
    lifecycle_build_id TEXT DEFAULT '',
    attribution_build_id TEXT DEFAULT '',
    regime_build_id TEXT DEFAULT '',
    knowledge_build_id TEXT DEFAULT '',
    portfolio_snapshot_id TEXT DEFAULT '',
    registry_hash TEXT DEFAULT '',
    open_position_present INTEGER DEFAULT 0,
    metadata_json TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS review_evidence (
    evidence_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    incumbent_identity_json TEXT DEFAULT '{}',
    incumbent_registry_status TEXT DEFAULT '',
    incumbent_lifecycle_health TEXT DEFAULT '',
    incumbent_operational_evidence_json TEXT DEFAULT '{}',
    incumbent_research_evidence_json TEXT DEFAULT '{}',
    incumbent_regime_evidence_json TEXT DEFAULT '{}',
    candidate_identity_json TEXT DEFAULT '{}',
    candidate_registry_status TEXT DEFAULT '',
    candidate_lifecycle_health TEXT DEFAULT '',
    candidate_operational_evidence_json TEXT DEFAULT '{}',
    candidate_research_evidence_json TEXT DEFAULT '{}',
    candidate_regime_evidence_json TEXT DEFAULT '{}',
    component_scores_json TEXT DEFAULT '{}',
    hard_gates_json TEXT DEFAULT '[]',
    confidence TEXT DEFAULT '',
    maturity TEXT DEFAULT '',
    replacement_margin REAL DEFAULT 0,
    reason_codes_json TEXT DEFAULT '[]',
    ticker_overlap_json TEXT DEFAULT '[]',
    family_overlap_json TEXT DEFAULT '[]',
    regime_overlap_json TEXT DEFAULT '{}',
    correlation_status TEXT DEFAULT '',
    concentration_delta_json TEXT DEFAULT '{}',
    diversification_classification TEXT DEFAULT '',
    open_position_context_json TEXT DEFAULT '{}',
    contradictory_evidence_json TEXT DEFAULT '[]',
    missing_evidence_json TEXT DEFAULT '[]',
    source_refs_json TEXT DEFAULT '{}',
    evidence_hash TEXT DEFAULT '',
    created_at REAL NOT NULL,
    FOREIGN KEY (case_id) REFERENCES review_cases(case_id)
);

CREATE TABLE IF NOT EXISTS review_decisions (
    decision_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    actor_type TEXT DEFAULT 'HUMAN',
    actor_id TEXT DEFAULT '',
    evidence_hash TEXT DEFAULT '',
    reason_code TEXT DEFAULT '',
    comment TEXT DEFAULT '',
    timestamp REAL NOT NULL,
    review_after REAL,
    requested_evidence_json TEXT DEFAULT '[]',
    revalidation_reason TEXT DEFAULT '',
    FOREIGN KEY (case_id) REFERENCES review_cases(case_id)
);

CREATE TABLE IF NOT EXISTS review_events (
    event_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor_type TEXT DEFAULT '',
    actor_id TEXT DEFAULT '',
    timestamp REAL NOT NULL,
    reason_code TEXT DEFAULT '',
    comment TEXT DEFAULT '',
    evidence_hash TEXT DEFAULT '',
    FOREIGN KEY (case_id) REFERENCES review_cases(case_id)
);

CREATE TABLE IF NOT EXISTS review_dedup (
    dedup_key TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS review_policy (
    version TEXT PRIMARY KEY,
    policy_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


class HumanReviewStore:
    """SQLite-backed governance store for human review cases.
    
    This store is governance truth for review decisions.
    It is NOT registry truth, NOT swap truth, NOT broker truth.
    """

    def __init__(self, path: Optional[Path] = None):
        if path is None:
            from core.strategy_registry import STATE_DIR
            path = STATE_DIR / REVIEW_DB_NAME
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Handle corrupt DB files
        db_recovered = False
        if self.path.exists():
            try:
                test_conn = sqlite3.connect(str(self.path))
                test_conn.execute("SELECT 1")
                test_conn.close()
            except sqlite3.DatabaseError:
                ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
                corrupt = self.path.with_name(self.path.name + f".corrupt-{ts}")
                self.path.replace(corrupt)
                db_recovered = True
                # Fresh start - remove any leftover empty/journal files
                for suffix in ["-wal", "-shm", "-journal"]:
                    stale = self.path.with_suffix(self.path.suffix + suffix)
                    if stale.exists():
                        stale.unlink()
        # After corrupt recovery, delete any 0-byte leftover and create fresh
        if db_recovered and self.path.exists() and self.path.stat().st_size == 0:
            self.path.unlink()
        self._conn = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        except sqlite3.DatabaseError:
            # Empty/fresh file - set pragmas after schema init
            pass
        self._ensure_schema()
        # Retry pragmas after schema init if they failed
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        except sqlite3.DatabaseError:
            pass

    def _ensure_schema(self) -> None:
        # Execute each statement individually to handle fresh/empty DB files
        failed = 0
        for stmt in _CREATE_TABLES.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                try:
                    self._conn.execute(stmt)
                except sqlite3.DatabaseError:
                    failed += 1
                    logger.warning(f"Schema statement skipped: {stmt[:80]}...")
        self._conn.commit()
        if failed > 0:
            # If all statements failed, the DB is likely still corrupt — try recovery
            try:
                self._conn.close()
            except Exception:
                pass
            # Delete and recreate
            if self.path.exists():
                ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
                corrupt = self.path.with_name(self.path.name + f".corrupt-retry-{ts}")
                try:
                    self.path.replace(corrupt)
                except Exception:
                    try:
                        self.path.unlink()
                    except Exception:
                        pass
            self._conn = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA foreign_keys=ON")
            except sqlite3.DatabaseError:
                pass
            # Retry schema
            for stmt in _CREATE_TABLES.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    try:
                        self._conn.execute(stmt)
                    except sqlite3.DatabaseError:
                        logger.error(f"Schema creation failed after recovery: {stmt[:80]}...")
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- Review Policy --

    def save_policy(self, policy: ReviewPolicy) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO review_policy (version, policy_json, created_at) VALUES (?,?,?)",
            (policy.version, json.dumps(policy.to_dict()), time.time()),
        )
        self._conn.commit()

    def get_policy(self, version: Optional[str] = None) -> Optional[ReviewPolicy]:
        if version:
            row = self._conn.execute("SELECT * FROM review_policy WHERE version=?", (version,)).fetchone()
        else:
            row = self._conn.execute("SELECT * FROM review_policy ORDER BY created_at DESC LIMIT 1").fetchone()
        if not row:
            return None
        data = json.loads(row["policy_json"])
        return ReviewPolicy(**data)

    # -- Review Cases --

    def create_review_case(self, case: ReviewCase) -> str:
        """Create a review case. Returns case_id or empty string if deduplicated."""
        dedupe_key = compute_dedupe_key(
            case.ranking_build_id, case.comparison_id, case.evidence_hash
        )
        # Check dedup
        existing = self._conn.execute(
            "SELECT case_id FROM review_dedup WHERE dedup_key=?", (dedupe_key,)
        ).fetchone()
        if existing:
            logger.info(f"Dedup hit: case {existing['case_id']} already exists for this ranking+comparison+evidence")
            return ""

        self._conn.execute(
            """INSERT INTO review_cases
               (case_id, ranking_build_id, comparison_id, incumbent_id, candidate_id,
                ranking_decision, ranking_confidence, ranking_policy_version,
                review_policy_version, evidence_hash, state, created_at,
                manual_trigger, lifecycle_build_id, attribution_build_id,
                regime_build_id, knowledge_build_id, portfolio_snapshot_id,
                registry_hash, open_position_present, metadata_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (case.case_id, case.ranking_build_id, case.comparison_id,
             case.incumbent_id, case.candidate_id, case.ranking_decision,
             case.ranking_confidence, case.ranking_policy_version,
             case.review_policy_version, case.evidence_hash, case.state,
             case.created_at, 1 if case.manual_trigger else 0,
             case.lifecycle_build_id, case.attribution_build_id,
             case.regime_build_id, case.knowledge_build_id,
             case.portfolio_snapshot_id, case.registry_hash,
             1 if case.open_position_present else 0, case.metadata_json),
        )
        # Record dedup
        self._conn.execute(
            "INSERT INTO review_dedup (dedup_key, case_id, created_at) VALUES (?,?,?)",
            (dedupe_key, case.case_id, case.created_at),
        )
        # Create event
        event = ReviewEvent(
            event_id=f"evt_{case.case_id}_created",
            case_id=case.case_id,
            from_state="",
            to_state=CaseState.OPEN.value,
            event_type=EventType.CASE_CREATED.value,
            timestamp=case.created_at,
        )
        self._save_event(event)
        self._conn.commit()
        logger.info(f"Created review case {case.case_id} for ranking {case.ranking_build_id}")
        return case.case_id

    def get_review_case(self, case_id: str) -> Optional[ReviewCase]:
        row = self._conn.execute(
            "SELECT * FROM review_cases WHERE case_id=?", (case_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_case(row)

    def list_open_cases(self) -> List[ReviewCase]:
        """List all OPEN review cases."""
        rows = self._conn.execute(
            "SELECT * FROM review_cases WHERE state IN ('OPEN', 'IN_REVIEW') ORDER BY created_at DESC"
        ).fetchall()
        return [self._row_to_case(r) for r in rows]

    def list_all_cases(self, state: Optional[str] = None) -> List[ReviewCase]:
        """List cases, optionally filtered by state."""
        if state:
            rows = self._conn.execute(
                "SELECT * FROM review_cases WHERE state=? ORDER BY created_at DESC",
                (state,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM review_cases ORDER BY created_at DESC"
            ).fetchall()
        return [self._row_to_case(r) for r in rows]

    def _row_to_case(self, row: sqlite3.Row) -> ReviewCase:
        return ReviewCase(
            case_id=row["case_id"],
            ranking_build_id=row["ranking_build_id"],
            comparison_id=row["comparison_id"],
            incumbent_id=row["incumbent_id"],
            candidate_id=row["candidate_id"],
            ranking_decision=row["ranking_decision"],
            ranking_confidence=row["ranking_confidence"],
            ranking_policy_version=row["ranking_policy_version"],
            review_policy_version=row["review_policy_version"],
            evidence_hash=row["evidence_hash"],
            state=row["state"],
            created_at=row["created_at"],
            decided_at=row["decided_at"],
            manual_trigger=bool(row["manual_trigger"]),
            lifecycle_build_id=row["lifecycle_build_id"],
            attribution_build_id=row["attribution_build_id"],
            regime_build_id=row["regime_build_id"],
            knowledge_build_id=row["knowledge_build_id"],
            portfolio_snapshot_id=row["portfolio_snapshot_id"],
            registry_hash=row["registry_hash"],
            open_position_present=bool(row["open_position_present"]),
            metadata_json=row["metadata_json"],
        )

    # -- Evidence --

    def save_evidence(self, evidence: ReviewEvidence) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO review_evidence
               (evidence_id, case_id,
                incumbent_identity_json, incumbent_registry_status,
                incumbent_lifecycle_health, incumbent_operational_evidence_json,
                incumbent_research_evidence_json, incumbent_regime_evidence_json,
                candidate_identity_json, candidate_registry_status,
                candidate_lifecycle_health, candidate_operational_evidence_json,
                candidate_research_evidence_json, candidate_regime_evidence_json,
                component_scores_json, hard_gates_json, confidence, maturity,
                replacement_margin, reason_codes_json,
                ticker_overlap_json, family_overlap_json, regime_overlap_json,
                correlation_status, concentration_delta_json,
                diversification_classification, open_position_context_json,
                contradictory_evidence_json, missing_evidence_json,
                source_refs_json, evidence_hash, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (evidence.evidence_id, evidence.case_id,
             json.dumps(evidence.incumbent_identity), evidence.incumbent_registry_status,
             evidence.incumbent_lifecycle_health, json.dumps(evidence.incumbent_operational_evidence),
             json.dumps(evidence.incumbent_research_evidence), json.dumps(evidence.incumbent_regime_evidence),
             json.dumps(evidence.candidate_identity), evidence.candidate_registry_status,
             evidence.candidate_lifecycle_health, json.dumps(evidence.candidate_operational_evidence),
             json.dumps(evidence.candidate_research_evidence), json.dumps(evidence.candidate_regime_evidence),
             json.dumps(evidence.component_scores), json.dumps(sorted(evidence.hard_gates)),
             evidence.confidence, evidence.maturity,
             evidence.replacement_margin, json.dumps(sorted(evidence.reason_codes)),
             json.dumps(sorted(evidence.ticker_overlap)), json.dumps(sorted(evidence.family_overlap)),
             json.dumps(evidence.regime_overlap), evidence.correlation_status,
             json.dumps(evidence.concentration_delta), evidence.diversification_classification,
             json.dumps(evidence.open_position_context),
             json.dumps(sorted(evidence.contradictory_evidence)),
             json.dumps(sorted(evidence.missing_evidence)),
             json.dumps(evidence.source_refs), evidence.evidence_hash, evidence.created_at),
        )
        # Update case evidence hash to reflect latest evidence
        if evidence.evidence_hash:
            self._conn.execute(
                "UPDATE review_cases SET evidence_hash=? WHERE case_id=?",
                (evidence.evidence_hash, evidence.case_id),
            )
        self._conn.commit()

    def get_evidence(self, case_id: str) -> Optional[ReviewEvidence]:
        row = self._conn.execute(
            "SELECT * FROM review_evidence WHERE case_id=? ORDER BY created_at DESC LIMIT 1",
            (case_id,),
        ).fetchone()
        if not row:
            return None
        return self._row_to_evidence(row)

    def _row_to_evidence(self, row: sqlite3.Row) -> ReviewEvidence:
        return ReviewEvidence(
            evidence_id=row["evidence_id"],
            case_id=row["case_id"],
            incumbent_identity=json.loads(row["incumbent_identity_json"]),
            incumbent_registry_status=row["incumbent_registry_status"],
            incumbent_lifecycle_health=row["incumbent_lifecycle_health"],
            incumbent_operational_evidence=json.loads(row["incumbent_operational_evidence_json"]),
            incumbent_research_evidence=json.loads(row["incumbent_research_evidence_json"]),
            incumbent_regime_evidence=json.loads(row["incumbent_regime_evidence_json"]),
            candidate_identity=json.loads(row["candidate_identity_json"]),
            candidate_registry_status=row["candidate_registry_status"],
            candidate_lifecycle_health=row["candidate_lifecycle_health"],
            candidate_operational_evidence=json.loads(row["candidate_operational_evidence_json"]),
            candidate_research_evidence=json.loads(row["candidate_research_evidence_json"]),
            candidate_regime_evidence=json.loads(row["candidate_regime_evidence_json"]),
            component_scores=json.loads(row["component_scores_json"]),
            hard_gates=json.loads(row["hard_gates_json"]),
            confidence=row["confidence"],
            maturity=row["maturity"],
            replacement_margin=row["replacement_margin"],
            reason_codes=json.loads(row["reason_codes_json"]),
            ticker_overlap=json.loads(row["ticker_overlap_json"]),
            family_overlap=json.loads(row["family_overlap_json"]),
            regime_overlap=json.loads(row["regime_overlap_json"]),
            correlation_status=row["correlation_status"],
            concentration_delta=json.loads(row["concentration_delta_json"]),
            diversification_classification=row["diversification_classification"],
            open_position_context=json.loads(row["open_position_context_json"]),
            contradictory_evidence=json.loads(row["contradictory_evidence_json"]),
            missing_evidence=json.loads(row["missing_evidence_json"]),
            source_refs=json.loads(row["source_refs_json"]),
            evidence_hash=row["evidence_hash"],
            created_at=row["created_at"],
        )

    # -- Decisions --

    def decide_review_case(
        self,
        case_id: str,
        decision: str,
        actor_type: str,
        actor_id: str,
        evidence_hash: str,
        reason_code: str = "",
        comment: str = "",
        review_after: Optional[float] = None,
        requested_evidence: Optional[List[str]] = None,
        revalidation_reason: str = "",
    ) -> tuple[bool, str]:
        """Apply a human decision to a case. Returns (success, error_message).
        
        Concurrency safe: uses transaction with case-level check.
        Terminal decisions are immutable.
        """
        now = time.time()
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                case = self.get_review_case(case_id)
                if not case:
                    self._conn.execute("ROLLBACK")
                    return False, "CASE_NOT_FOUND"

                # Validate decision input FIRST (catches invalid tokens, bad actors, stale evidence)
                error = validate_decision_input(
                    decision, actor_type, actor_id, evidence_hash,
                    case.state, case.evidence_hash,
                )
                if error:
                    self._conn.execute("ROLLBACK")
                    return False, error

                # Validate state transition
                to_state = _decision_to_state(decision)
                if not validate_transition(case.state, to_state):
                    self._conn.execute("ROLLBACK")
                    return False, f"INVALID_TRANSITION: {case.state} -> {to_state}"

                # For non-APPROVE, require reason
                if decision != HumanDecision.APPROVE.value and not reason_code:
                    self._conn.execute("ROLLBACK")
                    return False, "REASON_CODE_REQUIRED"

                # Record decision
                decision_id = f"dec_{case_id}_{uuid.uuid4().hex[:12]}"
                review_decision = ReviewDecision(
                    decision_id=decision_id,
                    case_id=case_id,
                    decision=decision,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    evidence_hash=evidence_hash,
                    reason_code=reason_code,
                    comment=comment,
                    timestamp=now,
                    review_after=review_after,
                    requested_evidence=requested_evidence or [],
                    revalidation_reason=revalidation_reason,
                )
                self._save_decision(review_decision)

                # Transition state
                self._conn.execute(
                    "UPDATE review_cases SET state=?, decided_at=? WHERE case_id=?",
                    (to_state, now, case_id),
                )

                # Record event
                event = ReviewEvent(
                    event_id=f"evt_{uuid.uuid4().hex[:12]}",
                    case_id=case_id,
                    from_state=case.state,
                    to_state=to_state,
                    event_type=EventType.DECISION_RECORDED.value,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    timestamp=now,
                    reason_code=reason_code,
                    comment=comment,
                    evidence_hash=evidence_hash,
                )
                self._save_event(event)
                self._conn.commit()

                logger.info(f"Decision {decision} recorded for case {case_id} by {actor_id}")
                return True, ""
            except Exception as e:
                try:
                    self._conn.execute("ROLLBACK")
                except Exception:
                    pass
                logger.error(f"Decision failed for case {case_id}: {e}")
                return False, f"INTERNAL_ERROR: {e}"

    def _save_decision(self, decision: ReviewDecision) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO review_decisions
               (decision_id, case_id, decision, actor_type, actor_id,
                evidence_hash, reason_code, comment, timestamp,
                review_after, requested_evidence_json, revalidation_reason)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (decision.decision_id, decision.case_id, decision.decision,
             decision.actor_type, decision.actor_id, decision.evidence_hash,
             decision.reason_code, decision.comment, decision.timestamp,
             decision.review_after, json.dumps(decision.requested_evidence),
             decision.revalidation_reason),
        )

    def get_decision_history(self, case_id: str) -> List[ReviewDecision]:
        rows = self._conn.execute(
            "SELECT * FROM review_decisions WHERE case_id=? ORDER BY timestamp ASC",
            (case_id,),
        ).fetchall()
        return [ReviewDecision(
            decision_id=r["decision_id"],
            case_id=r["case_id"],
            decision=r["decision"],
            actor_type=r["actor_type"],
            actor_id=r["actor_id"],
            evidence_hash=r["evidence_hash"],
            reason_code=r["reason_code"],
            comment=r["comment"],
            timestamp=r["timestamp"],
            review_after=r["review_after"],
            requested_evidence=json.loads(r["requested_evidence_json"]),
            revalidation_reason=r["revalidation_reason"],
        ) for r in rows]

    # -- Events (Decision Journal) --

    def _save_event(self, event: ReviewEvent) -> None:
        self._conn.execute(
            """INSERT INTO review_events
               (event_id, case_id, from_state, to_state, event_type,
                actor_type, actor_id, timestamp, reason_code, comment, evidence_hash)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (event.event_id, event.case_id, event.from_state, event.to_state,
             event.event_type, event.actor_type, event.actor_id, event.timestamp,
             event.reason_code, event.comment, event.evidence_hash),
        )

    def get_event_journal(self, case_id: str) -> List[ReviewEvent]:
        rows = self._conn.execute(
            "SELECT * FROM review_events WHERE case_id=? ORDER BY timestamp ASC",
            (case_id,),
        ).fetchall()
        return [ReviewEvent(
            event_id=r["event_id"],
            case_id=r["case_id"],
            from_state=r["from_state"],
            to_state=r["to_state"],
            event_type=r["event_type"],
            actor_type=r["actor_type"],
            actor_id=r["actor_id"],
            timestamp=r["timestamp"],
            reason_code=r["reason_code"],
            comment=r["comment"],
            evidence_hash=r["evidence_hash"],
        ) for r in rows]

    # -- Supersession --

    def supersede_cases(self, superseded_case_ids: List[str], new_case_id: str) -> int:
        """Supersede old open cases. Returns count of superseded cases."""
        now = time.time()
        count = 0
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            for old_id in superseded_case_ids:
                case = self.get_review_case(old_id)
                if not case:
                    continue
                if case.state in TERMINAL_STATES:
                    continue
                if old_id == new_case_id:
                    continue
                self._conn.execute(
                    "UPDATE review_cases SET state=? WHERE case_id=?",
                    (CaseState.SUPERSEDED.value, old_id),
                )
                event = ReviewEvent(
                    event_id=f"evt_{old_id}_superseded_{int(now)}",
                    case_id=old_id,
                    from_state=case.state,
                    to_state=CaseState.SUPERSEDED.value,
                    event_type=EventType.CASE_SUPERSEDED.value,
                    timestamp=now,
                    comment=f"Superseded by {new_case_id}",
                )
                self._save_event(event)
                count += 1
            self._conn.commit()
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        return count

    # -- Staleness Detection --

    def detect_stale_cases(self, current_build_id: str) -> List[str]:
        """Detect cases that should become stale.
        
        A case is stale if:
        - Its ranking_build_id is older than current AND
        - A new ranking build with a different build_id exists for the same comparison
        """
        stale_ids = []
        # Find cases whose ranking build is older
        rows = self._conn.execute(
            """SELECT case_id, ranking_build_id, comparison_id, state 
               FROM review_cases 
               WHERE state IN ('OPEN', 'IN_REVIEW')
               AND ranking_build_id != ?""",
            (current_build_id,),
        ).fetchall()
        
        for row in rows:
            # Check if a new build has a competing comparison for the same incumbent
            case = self.get_review_case(row["case_id"])
            if not case:
                continue
            # Check if there's a newer build with a replacement candidate for same incumbent
            newer = self._conn.execute(
                """SELECT comparison_id FROM replacement_comparisons
                   WHERE build_id = ? AND incumbent_id = ?
                   AND decision = 'REPLACEMENT_CANDIDATE'
                   AND comparison_id != ?""",
                (current_build_id, case.incumbent_id, case.comparison_id),
            ).fetchone()
            if newer:
                stale_ids.append(row["case_id"])
        
        return stale_ids

    def mark_stale(self, case_id: str, reason: str = "") -> bool:
        """Mark a case as STALE. Returns True if successful."""
        now = time.time()
        case = self.get_review_case(case_id)
        if not case:
            return False
        if is_terminal(case.state):
            return False
        
        self._conn.execute(
            "UPDATE review_cases SET state=? WHERE case_id=?",
            (CaseState.STALE.value, case_id),
        )
        event = ReviewEvent(
            event_id=f"evt_{case_id}_stale_{int(now)}",
            case_id=case_id,
            from_state=case.state,
            to_state=CaseState.STALE.value,
            event_type=EventType.CASE_STALENED.value,
            timestamp=now,
            reason_code=reason or StalenessReason.NEW_RANKING_BUILD.value,
        )
        self._save_event(event)
        self._conn.commit()
        return True

    # -- Review Evidence File Generation --

    def generate_review_report(self, case_id: str, output_dir: Path) -> Optional[Path]:
        """Generate human-readable review.md for a case."""
        case = self.get_review_case(case_id)
        if not case:
            return None
        evidence = self.get_evidence(case_id)
        if not evidence:
            return None

        case_dir = output_dir / case_id
        case_dir.mkdir(parents=True, exist_ok=True)

        # Markdown report
        md_lines = [
            f"# Human Review Case: {case_id}",
            "",
            f"**State:** {case.state}",
            f"**Created:** {datetime.fromtimestamp(case.created_at, tz=timezone.utc).isoformat()}",
            f"**Ranking Build:** {case.ranking_build_id}",
            f"**Comparison:** {case.comparison_id}",
            f"**Decision Requested:** REPLACEMENT of {case.incumbent_id} with {case.candidate_id}",
            "",
            "## 1. Decision Requested",
            f"Replace **{case.incumbent_id}** with **{case.candidate_id}**",
            f"Ranking decision: {case.ranking_decision} (confidence: {case.ranking_confidence})",
            "",
            "## 2. Incumbent",
            f"- Strategy ID: {case.incumbent_id}",
            f"- Registry Status: {evidence.incumbent_registry_status}",
            f"- Lifecycle Health: {evidence.incumbent_lifecycle_health}",
            "",
            "## 3. Candidate",
            f"- Strategy ID: {case.candidate_id}",
            f"- Registry Status: {evidence.candidate_registry_status}",
            f"- Lifecycle Health: {evidence.candidate_lifecycle_health}",
            "",
            "## 4. Why Candidate Was Nominated",
            f"- Confidence: {evidence.confidence}",
            f"- Replacement margin: {evidence.replacement_margin}",
            f"- Reason codes: {', '.join(evidence.reason_codes)}",
            "",
            "## 5. Evidence Supporting Replacement",
            json.dumps(evidence.component_scores, indent=2),
            "",
            "## 6. Evidence Against Replacement",
            f"- Contradictory evidence: {evidence.contradictory_evidence}",
            "",
            "## 7. Portfolio Impact",
            f"- Ticker overlap: {evidence.ticker_overlap}",
            f"- Family overlap: {evidence.family_overlap}",
            f"- Diversification: {evidence.diversification_classification}",
            "",
            "## 8. Regime Context",
            f"- Regime overlap: {json.dumps(evidence.regime_overlap, indent=2)}",
            "",
            "## 9. Operational Evidence",
            f"- Incumbent: {json.dumps(evidence.incumbent_operational_evidence, indent=2)}",
            f"- Candidate: {json.dumps(evidence.candidate_operational_evidence, indent=2)}",
            "",
            "## 10. Missing / Uncertain Evidence",
            f"- Missing: {evidence.missing_evidence}",
            "",
            "## 11. Ranking Confidence",
            f"{evidence.confidence} (maturity: {evidence.maturity})",
            "",
            "## 12. Open Position / Execution Context",
            f"- Open position present: {case.open_position_present}",
            "",
            "## 13. Source Manifest",
            f"- Evidence hash: {case.evidence_hash}",
            f"- Ranking build: {case.ranking_build_id}",
            f"- Lifecycle build: {case.lifecycle_build_id}",
            f"- Attribution build: {case.attribution_build_id}",
            f"- Regime build: {case.regime_build_id}",
            f"- Knowledge build: {case.knowledge_build_id}",
            "",
            "## 14. Allowed Human Decisions",
            "- **APPROVE** — Accept this replacement recommendation",
            "- **REJECT** — Reject this recommendation (requires reason)",
            "- **DEFER** — Defer decision (requires reason, optional review_after)",
            "- **REQUEST_REVALIDATION** — Request more evidence (requires reason)",
            "",
            f"**Evidence Hash:** `{case.evidence_hash}`",
            "",
            f"**APPROVE requires exact evidence hash match.**",
        ]

        review_md = case_dir / "review.md"
        review_md.write_text("\n".join(md_lines), encoding="utf-8")

        # JSON artifact
        review_json = case_dir / "review.json"
        review_json.write_text(json.dumps({
            "case_id": case.case_id,
            "state": case.state,
            "created_at": case.created_at,
            "ranking_build_id": case.ranking_build_id,
            "comparison_id": case.comparison_id,
            "incumbent_id": case.incumbent_id,
            "candidate_id": case.candidate_id,
            "evidence_hash": case.evidence_hash,
            "ranking_decision": case.ranking_decision,
            "ranking_confidence": case.ranking_confidence,
        }, indent=2), encoding="utf-8")

        # Manifest
        manifest = case_dir / "manifest.json"
        manifest.write_text(json.dumps({
            "case_id": case.case_id,
            "evidence_hash": case.evidence_hash,
            "ranking_build_id": case.ranking_build_id,
            "comparison_id": case.comparison_id,
            "ranking_policy_version": case.ranking_policy_version,
            "review_policy_version": case.review_policy_version,
            "files": ["review.md", "review.json", "manifest.json"],
        }, indent=2), encoding="utf-8")

        return case_dir

    # -- Summary Report --

    def generate_summary_report(self, output_dir: Path) -> Dict[str, Any]:
        """Generate latest.md and latest.json summary."""
        output_dir.mkdir(parents=True, exist_ok=True)

        state_counts = {}
        for state in ALL_STATES:
            rows = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM review_cases WHERE state=?", (state,)
            ).fetchone()
            state_counts[state] = rows["cnt"]

        total_cases = sum(state_counts.values())
        cases_by_state = {}
        for state in ALL_STATES:
            rows = self._conn.execute(
                "SELECT case_id, incumbent_id, candidate_id, created_at FROM review_cases WHERE state=?",
                (state,),
            ).fetchall()
            cases_by_state[state] = [
                {"case_id": r["case_id"], "incumbent_id": r["incumbent_id"],
                 "candidate_id": r["candidate_id"], "created_at": r["created_at"]}
                for r in rows
            ]

        summary = {
            "total_cases": total_cases,
            "state_counts": state_counts,
            "cases_by_state": cases_by_state,
            "generated_at": time.time(),
        }

        # Write JSON
        json_path = output_dir / "latest.json"
        json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

        # Write Markdown
        md_lines = ["# Human Review Summary", ""]
        md_lines.append(f"**Generated:** {datetime.now(timezone.utc).isoformat()}")
        md_lines.append(f"**Total cases:** {total_cases}")
        md_lines.append("")
        md_lines.append("## State Counts")
        for state in ALL_STATES:
            md_lines.append(f"- {state}: {state_counts[state]}")
        md_lines.append("")

        for state in ALL_STATES:
            if cases_by_state[state]:
                md_lines.append(f"## {state}")
                for c in cases_by_state[state]:
                    md_lines.append(f"- {c['case_id']}: {c['incumbent_id']} → {c['candidate_id']}")
                md_lines.append("")

        md_path = output_dir / "latest.md"
        md_path.write_text("\n".join(md_lines), encoding="utf-8")

        return summary

    # -- Health Check --

    def health_check(self) -> Dict[str, Any]:
        """Check health of the human review store."""
        result = {
            "db_accessible": False,
            "open_count": 0,
            "stale_count": 0,
            "approved_count": 0,
            "total_count": 0,
            "oldest_open_age_seconds": 0,
            "integrity_violations": [],
        }
        try:
            self._conn.execute("SELECT 1")
            result["db_accessible"] = True
        except Exception:
            result["integrity_violations"].append("DB_INACCESSIBLE")
            return result

        for state in ALL_STATES:
            rows = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM review_cases WHERE state=?", (state,)
            ).fetchone()
            cnt = rows["cnt"]
            if state == CaseState.OPEN.value:
                result["open_count"] = cnt
            elif state == CaseState.STALE.value:
                result["stale_count"] = cnt
            elif state == CaseState.APPROVED.value:
                result["approved_count"] = cnt
            result["total_count"] += cnt

        # Oldest open case age
        oldest = self._conn.execute(
            """SELECT MIN(created_at) as oldest FROM review_cases 
               WHERE state IN ('OPEN', 'IN_REVIEW')"""
        ).fetchone()
        if oldest["oldest"]:
            result["oldest_open_age_seconds"] = time.time() - oldest["oldest"]

        # Check for evidence hash integrity
        cases = self._conn.execute(
            "SELECT case_id, evidence_hash FROM review_cases WHERE evidence_hash != ''"
        ).fetchall()
        for c in cases:
            ev = self._conn.execute(
                "SELECT evidence_hash FROM review_evidence WHERE case_id=?",
                (c["case_id"],),
            ).fetchone()
            if ev and ev["evidence_hash"] != c["evidence_hash"]:
                result["integrity_violations"].append(
                    f"EVIDENCE_HASH_MISMATCH:{c['case_id']}"
                )

        return result


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _decision_to_state(decision: str) -> str:
    """Map human decision to resulting case state."""
    mapping = {
        HumanDecision.APPROVE.value: CaseState.APPROVED.value,
        HumanDecision.REJECT.value: CaseState.REJECTED.value,
        HumanDecision.DEFER.value: CaseState.DEFERRED.value,
        HumanDecision.REQUEST_REVALIDATION.value: CaseState.REVALIDATION_REQUESTED.value,
    }
    return mapping.get(decision, "")


# ---------------------------------------------------------------------------
# CLI Interface
# ---------------------------------------------------------------------------

def cli_main(args: Optional[List[str]] = None) -> None:
    """CLI interface: review list/show/decide."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Human Review Decision Gate")
    sub = parser.add_subparsers(dest="command")

    # list
    list_p = sub.add_parser("list", help="List review cases")
    list_p.add_argument("--state", help="Filter by state")

    # show
    show_p = sub.add_parser("show", help="Show a review case")
    show_p.add_argument("case_id", help="Case ID")

    # decide
    decide_p = sub.add_parser("decide", help="Decide on a review case")
    decide_p.add_argument("case_id", help="Case ID")
    decide_p.add_argument("--decision", required=True,
                          choices=[d.value for d in HumanDecision])
    decide_p.add_argument("--actor", required=True, help="Human actor ID")
    decide_p.add_argument("--evidence-hash", help="Evidence hash (required for APPROVE)")
    decide_p.add_argument("--reason", help="Reason code")
    decide_p.add_argument("--comment", help="Comment")
    decide_p.add_argument("--review-after", type=float, help="Review after timestamp (DEFER)")
    decide_p.add_argument("--requested-evidence", nargs="*", help="Requested evidence types")

    parsed = parser.parse_args(args)
    if not parsed.command:
        parser.print_help()
        sys.exit(1)

    store = HumanReviewStore()

    if parsed.command == "list":
        cases = store.list_all_cases(state=parsed.state)
        for c in cases:
            print(f"{c.case_id}  {c.state}  {c.incumbent_id} -> {c.candidate_id}")

    elif parsed.command == "show":
        case = store.get_review_case(parsed.case_id)
        if not case:
            print(f"Case not found: {parsed.case_id}")
            sys.exit(1)
        print(f"Case ID: {case.case_id}")
        print(f"State: {case.state}")
        print(f"Ranking: {case.ranking_decision} ({case.ranking_confidence})")
        print(f"Incumbent: {case.incumbent_id}")
        print(f"Candidate: {case.candidate_id}")
        print(f"Evidence Hash: {case.evidence_hash}")
        evidence = store.get_evidence(case.case_id)
        if evidence:
            print(f"Evidence Sections: incumbent, candidate, ranking, portfolio, negative, missing")
        journal = store.get_event_journal(case.case_id)
        if journal:
            print("Event Journal:")
            for e in journal:
                print(f"  {e.event_type}: {e.from_state} -> {e.to_state} @ {e.timestamp}")

    elif parsed.command == "decide":
        ok, err = store.decide_review_case(
            case_id=parsed.case_id,
            decision=parsed.decision,
            actor_type=ActorType.HUMAN.value,
            actor_id=parsed.actor,
            evidence_hash=parsed.evidence_hash or "",
            reason_code=parsed.reason or "",
            comment=parsed.comment or "",
            review_after=parsed.review_after,
            requested_evidence=parsed.requested_evidence,
        )
        if ok:
            print(f"Decision recorded: {parsed.decision} on {parsed.case_id}")
        else:
            print(f"Decision failed: {err}")
            sys.exit(1)

    store.close()


if __name__ == "__main__":
    cli_main()

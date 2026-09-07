"""Portfolio-Level Replacement Ranking — Iteration 16.

Deterministic, evidence-aware, advisory-only ranking layer that compares
incumbent strategies against candidates across research, operational,
lifecycle, regime, and portfolio dimensions.

CLASS 2: Decision-support analytics / NON-AUTHORITATIVE.

RANKING ≠ SWAP ≠ ORDER ≠ ELIGIBILITY MUTATION.

This module:
  - Reads: registry, performance attribution, lifecycle, regime, research knowledge
  - Writes: state/replacement_ranking.db (DERIVED, never becomes registry truth)
  - NEVER: registry mutations, swap mutations, broker calls, trading, risk changes

Every ranking output is advisory. No automatic replacement, activation, or
deactivation is produced by this module.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
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

RANKING_SCHEMA_VERSION = "1.0.0"
RANKING_BUILDER_VERSION = "1.0.0"
RANKING_POLICY_VERSION = "1.0.0"
RANKING_DB_NAME = "replacement_ranking.db"

# Minimum evidence counts
MIN_BACKTEST_TRADES = 10
MIN_ALIGNED_OBSERVATIONS = 30
MIN_PAPER_TRADES = 5

# Anti-churn thresholds
MIN_SCORE_MARGIN_FOR_REPLACEMENT = 0.15
MIN_EVIDENCE_MARGIN_FOR_HEALTHY_INCUMBENT = 0.25
REGIME_ONLY_CHANGE_THRESHOLD = 0.30  # regime advantage alone below this won't trigger replacement


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class DecisionOutput(str, Enum):
    """Advisory decision outputs for replacement ranking."""
    KEEP = "KEEP"
    WATCH = "WATCH"
    REVALIDATE = "REVALIDATE"
    REPLACEMENT_CANDIDATE = "REPLACEMENT_CANDIDATE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NO_VALID_CANDIDATE = "NO_VALID_CANDIDATE"


class EvidenceMaturity(str, Enum):
    """Evidence maturity levels (shared with lifecycle/attribution)."""
    INSUFFICIENT = "INSUFFICIENT"
    EARLY = "EARLY"
    USABLE = "USABLE"
    MATURE = "MATURE"


class ConfidenceLevel(str, Enum):
    """Ranking confidence levels."""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INSUFFICIENT = "INSUFFICIENT"


class HardGateFailure(str, Enum):
    """Reasons a candidate fails a hard gate."""
    INVALID_CONFIG = "INVALID_CONFIG"
    MISSING_IDENTITY = "MISSING_IDENTITY"
    UNSAFE_PROVENANCE = "UNSAFE_PROVENANCE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    STALE_EVIDENCE = "STALE_EVIDENCE"
    SEVERE_CONTRADICTION = "SEVERE_CONTRADICTION"
    SAME_FAMILY_DUPLICATE = "SAME_FAMILY_DUPLICATE"


class DiversificationBenefit(str, Enum):
    """Portfolio diversification assessment."""
    POSITIVE = "DIVERSIFICATION_POSITIVE"
    NEUTRAL = "DIVERSIFICATION_NEUTRAL"
    NEGATIVE = "DIVERSIFICATION_NEGATIVE"
    INSUFFICIENT = "INSUFFICIENT_EVIDENCE"


class ReplacementUrgency(str, Enum):
    """Advisory replacement urgency levels."""
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class LifecycleHealth(str, Enum):
    """Lifecycle health states consumed from strategy_lifecycle."""
    HEALTHY = "HEALTHY"
    WATCH = "WATCH"
    DECAY_SUSPECTED = "DECAY_SUSPECTED"
    DECAY_CONFIRMED = "DECAY_CONFIRMED"
    UNKNOWN = "UNKNOWN"


class CorrelationStatus(str, Enum):
    """Correlation data availability."""
    COMPUTED = "COMPUTED"
    INSUFFICIENT = "CORRELATION_INSUFFICIENT"
    UNKNOWN = "CORRELATION_UNKNOWN"


# ---------------------------------------------------------------------------
# Reason Codes
# ---------------------------------------------------------------------------

REASON_CODES = [
    "CANDIDATE_RESEARCH_ADVANTAGE",
    "CANDIDATE_OPERATIONAL_ADVANTAGE",
    "CANDIDATE_REGIME_ROBUSTNESS",
    "CANDIDATE_DIVERSIFICATION_BENEFIT",
    "INCUMBENT_DECAY_CONFIRMED",
    "INCUMBENT_HEALTHY",
    "CANDIDATE_EVIDENCE_WEAK",
    "CANDIDATE_TOO_CORRELATED",
    "CANDIDATE_CONCENTRATION_PENALTY",
    "CURRENT_REGIME_FAVORABLE",
    "CURRENT_REGIME_UNFAVORABLE",
    "CONTRADICTORY_EVIDENCE",
    "REVALIDATION_REQUIRED",
    "INSUFFICIENT_SAMPLE",
    "HARD_GATE_REJECTED",
    "ANTI_CHURN_PROTECTED",
]


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass
class RankingBuild:
    """Metadata for a single ranking build run."""
    build_id: str
    started_at: float
    finished_at: float = 0.0
    registry_hash: str = ""
    attribution_build_id: str = ""
    regime_build_id: str = ""
    lifecycle_build_id: str = ""
    knowledge_build_id: str = ""
    portfolio_snapshot_id: str = ""
    policy_version: str = RANKING_POLICY_VERSION
    candidate_count: int = 0
    incumbent_count: int = 0
    valid_comparisons: int = 0
    hard_gate_rejects: int = 0
    status: str = "started"


@dataclass
class PortfolioSnapshot:
    """Read-only snapshot of current portfolio decision context."""
    snapshot_id: str
    created_at: float
    active_strategies: List[Dict[str, Any]] = field(default_factory=list)
    instrument_exposure: Dict[str, float] = field(default_factory=dict)
    capital_allocation: float = 0.0
    slot_occupancy: int = 0
    max_slots: int = 3
    open_positions: List[Dict[str, Any]] = field(default_factory=list)
    family_exposure: Dict[str, int] = field(default_factory=dict)
    ticker_concentration: Dict[str, int] = field(default_factory=dict)
    directional_concentration: Dict[str, int] = field(default_factory=dict)
    regime_concentration: Dict[str, int] = field(default_factory=dict)
    total_active: int = 0
    registry_hash: str = ""


@dataclass
class CandidateScore:
    """Multidimensional score for a candidate strategy."""
    research_score: float = 0.0
    operational_score: float = 0.0
    lifecycle_score: float = 0.0
    regime_score: float = 0.0
    diversification_score: float = 0.0
    risk_penalty: float = 0.0
    confidence_penalty: float = 0.0
    total_score: float = 0.0
    evidence_maturity: str = EvidenceMaturity.INSUFFICIENT.value
    confidence_level: str = ConfidenceLevel.INSUFFICIENT.value
    hard_gate_failures: List[str] = field(default_factory=list)
    passed_hard_gates: bool = True
    component_details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReplacementComparison:
    """Pairwise comparison between incumbent and candidate."""
    comparison_id: str
    build_id: str
    incumbent_id: str
    candidate_id: str
    incumbent_ticker: str = ""
    candidate_ticker: str = ""
    incumbent_family: str = ""
    candidate_family: str = ""
    research_comparison: Dict[str, Any] = field(default_factory=dict)
    operational_comparison: Dict[str, Any] = field(default_factory=dict)
    lifecycle_comparison: Dict[str, Any] = field(default_factory=dict)
    regime_comparison: Dict[str, Any] = field(default_factory=dict)
    portfolio_overlap: Dict[str, Any] = field(default_factory=dict)
    risk_comparison: Dict[str, Any] = field(default_factory=dict)
    confidence: str = ConfidenceLevel.INSUFFICIENT.value
    decision: str = DecisionOutput.INSUFFICIENT_EVIDENCE.value
    reason_codes: List[str] = field(default_factory=list)
    incumbent_score: float = 0.0
    candidate_score: float = 0.0
    score_margin: float = 0.0
    anti_churn_triggered: bool = False
    explanation: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RankingExplanation:
    """Full explanation for a ranking decision."""
    explanation_id: str
    build_id: str
    incumbent_id: str
    best_candidate_id: Optional[str] = None
    decision: str = DecisionOutput.KEEP.value
    urgency: str = ReplacementUrgency.NONE.value
    reason_codes: List[str] = field(default_factory=list)
    component_scores: Dict[str, Any] = field(default_factory=dict)
    what_supports: List[str] = field(default_factory=list)
    what_contradicts: List[str] = field(default_factory=list)
    what_is_missing: List[str] = field(default_factory=list)
    what_would_change: List[str] = field(default_factory=list)
    counterfactual_label: Optional[str] = None


# ---------------------------------------------------------------------------
# Ranking Policy
# ---------------------------------------------------------------------------

@dataclass
class RankingPolicy:
    """Versioned ranking policy. Weights, gates, penalties are policy, not truth."""
    version: str = RANKING_POLICY_VERSION
    # Soft score weights (sum = 1.0)
    weight_research: float = 0.25
    weight_operational: float = 0.20
    weight_lifecycle: float = 0.15
    weight_regime: float = 0.15
    weight_diversification: float = 0.15
    weight_risk: float = 0.10
    # Anti-churn
    min_score_margin: float = MIN_SCORE_MARGIN_FOR_REPLACEMENT
    min_healthy_incumbent_margin: float = MIN_EVIDENCE_MARGIN_FOR_HEALTHY_INCUMBENT
    regime_only_threshold: float = REGIME_ONLY_CHANGE_THRESHOLD
    # Evidence maturity multiplier
    maturity_multiplier: Dict[str, float] = field(default_factory=lambda: {
        EvidenceMaturity.INSUFFICIENT.value: 0.3,
        EvidenceMaturity.EARLY.value: 0.6,
        EvidenceMaturity.USABLE.value: 0.85,
        EvidenceMaturity.MATURE.value: 1.0,
    })
    # Confidence penalty
    confidence_penalty: Dict[str, float] = field(default_factory=lambda: {
        ConfidenceLevel.HIGH.value: 0.0,
        ConfidenceLevel.MEDIUM.value: 0.10,
        ConfidenceLevel.LOW.value: 0.25,
        ConfidenceLevel.INSUFFICIENT.value: 0.50,
    })
    # Lifecycle impact on replacement barrier
    incumbent_lifecycle_bonus: Dict[str, float] = field(default_factory=lambda: {
        LifecycleHealth.HEALTHY.value: 0.15,
        LifecycleHealth.WATCH.value: 0.0,
        LifecycleHealth.DECAY_SUSPECTED.value: -0.10,
        LifecycleHealth.DECAY_CONFIRMED.value: -0.20,
        LifecycleHealth.UNKNOWN.value: 0.0,
    })

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# ReplacementRankingStore — SQLite-backed derived store
# ---------------------------------------------------------------------------

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS ranking_builds (
    build_id TEXT PRIMARY KEY,
    started_at REAL NOT NULL,
    finished_at REAL DEFAULT 0,
    registry_hash TEXT DEFAULT '',
    attribution_build_id TEXT DEFAULT '',
    regime_build_id TEXT DEFAULT '',
    lifecycle_build_id TEXT DEFAULT '',
    knowledge_build_id TEXT DEFAULT '',
    portfolio_snapshot_id TEXT DEFAULT '',
    policy_version TEXT DEFAULT '',
    candidate_count INTEGER DEFAULT 0,
    incumbent_count INTEGER DEFAULT 0,
    valid_comparisons INTEGER DEFAULT 0,
    hard_gate_rejects INTEGER DEFAULT 0,
    status TEXT DEFAULT 'started'
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    active_strategies_json TEXT DEFAULT '[]',
    instrument_exposure_json TEXT DEFAULT '{}',
    capital_allocation REAL DEFAULT 0,
    slot_occupancy INTEGER DEFAULT 0,
    max_slots INTEGER DEFAULT 3,
    open_positions_json TEXT DEFAULT '[]',
    family_exposure_json TEXT DEFAULT '{}',
    ticker_concentration_json TEXT DEFAULT '{}',
    directional_concentration_json TEXT DEFAULT '{}',
    regime_concentration_json TEXT DEFAULT '{}',
    total_active INTEGER DEFAULT 0,
    registry_hash TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS replacement_comparisons (
    comparison_id TEXT PRIMARY KEY,
    build_id TEXT NOT NULL,
    incumbent_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    incumbent_ticker TEXT DEFAULT '',
    candidate_ticker TEXT DEFAULT '',
    incumbent_family TEXT DEFAULT '',
    candidate_family TEXT DEFAULT '',
    research_comparison_json TEXT DEFAULT '{}',
    operational_comparison_json TEXT DEFAULT '{}',
    lifecycle_comparison_json TEXT DEFAULT '{}',
    regime_comparison_json TEXT DEFAULT '{}',
    portfolio_overlap_json TEXT DEFAULT '{}',
    risk_comparison_json TEXT DEFAULT '{}',
    confidence TEXT DEFAULT 'INSUFFICIENT',
    decision TEXT DEFAULT 'INSUFFICIENT_EVIDENCE',
    reason_codes_json TEXT DEFAULT '[]',
    incumbent_score REAL DEFAULT 0,
    candidate_score REAL DEFAULT 0,
    score_margin REAL DEFAULT 0,
    anti_churn_triggered INTEGER DEFAULT 0,
    explanation_json TEXT DEFAULT '{}',
    FOREIGN KEY (build_id) REFERENCES ranking_builds(build_id)
);

CREATE TABLE IF NOT EXISTS candidate_scores (
    score_id TEXT PRIMARY KEY,
    build_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    research_score REAL DEFAULT 0,
    operational_score REAL DEFAULT 0,
    lifecycle_score REAL DEFAULT 0,
    regime_score REAL DEFAULT 0,
    diversification_score REAL DEFAULT 0,
    risk_penalty REAL DEFAULT 0,
    confidence_penalty REAL DEFAULT 0,
    total_score REAL DEFAULT 0,
    evidence_maturity TEXT DEFAULT 'INSUFFICIENT',
    confidence_level TEXT DEFAULT 'INSUFFICIENT',
    hard_gate_failures_json TEXT DEFAULT '[]',
    passed_hard_gates INTEGER DEFAULT 1,
    component_details_json TEXT DEFAULT '{}',
    FOREIGN KEY (build_id) REFERENCES ranking_builds(build_id)
);

CREATE TABLE IF NOT EXISTS ranking_explanations (
    explanation_id TEXT PRIMARY KEY,
    build_id TEXT NOT NULL,
    incumbent_id TEXT NOT NULL,
    best_candidate_id TEXT,
    decision TEXT DEFAULT 'KEEP',
    urgency TEXT DEFAULT 'NONE',
    reason_codes_json TEXT DEFAULT '[]',
    component_scores_json TEXT DEFAULT '{}',
    what_supports_json TEXT DEFAULT '[]',
    what_contradicts_json TEXT DEFAULT '[]',
    what_is_missing_json TEXT DEFAULT '[]',
    what_would_change_json TEXT DEFAULT '[]',
    counterfactual_label TEXT,
    FOREIGN KEY (build_id) REFERENCES ranking_builds(build_id)
);
"""


class ReplacementRankingStore:
    """SQLite-backed derived store for replacement ranking data.

    This store is DERIVED. It does not become registry truth.
    All reads are deterministic. All writes are append-only within a build.
    """

    def __init__(self, path: Optional[Path] = None):
        if path is None:
            from core.strategy_registry import STATE_DIR
            path = STATE_DIR / RANKING_DB_NAME
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Handle corrupt DB files
        if self.path.exists():
            try:
                test_conn = sqlite3.connect(str(self.path))
                test_conn.execute("SELECT 1")
                test_conn.close()
            except sqlite3.DatabaseError:
                ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
                corrupt = self.path.with_name(self.path.name + f".corrupt-{ts}")
                self.path.replace(corrupt)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._conn.executescript(_CREATE_TABLES)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- Ranking Builds --

    def save_build(self, build: RankingBuild) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO ranking_builds
               (build_id, started_at, finished_at, registry_hash,
                attribution_build_id, regime_build_id, lifecycle_build_id,
                knowledge_build_id, portfolio_snapshot_id, policy_version,
                candidate_count, incumbent_count, valid_comparisons,
                hard_gate_rejects, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (build.build_id, build.started_at, build.finished_at,
             build.registry_hash, build.attribution_build_id,
             build.regime_build_id, build.lifecycle_build_id,
             build.knowledge_build_id, build.portfolio_snapshot_id,
             build.policy_version, build.candidate_count,
             build.incumbent_count, build.valid_comparisons,
             build.hard_gate_rejects, build.status),
        )
        self._conn.commit()

    def get_build(self, build_id: str) -> Optional[RankingBuild]:
        row = self._conn.execute(
            "SELECT * FROM ranking_builds WHERE build_id=?", (build_id,)
        ).fetchone()
        if not row:
            return None
        return RankingBuild(
            build_id=row["build_id"], started_at=row["started_at"],
            finished_at=row["finished_at"], registry_hash=row["registry_hash"],
            attribution_build_id=row["attribution_build_id"],
            regime_build_id=row["regime_build_id"],
            lifecycle_build_id=row["lifecycle_build_id"],
            knowledge_build_id=row["knowledge_build_id"],
            portfolio_snapshot_id=row["portfolio_snapshot_id"],
            policy_version=row["policy_version"],
            candidate_count=row["candidate_count"],
            incumbent_count=row["incumbent_count"],
            valid_comparisons=row["valid_comparisons"],
            hard_gate_rejects=row["hard_gate_rejects"],
            status=row["status"],
        )

    def latest_build(self) -> Optional[RankingBuild]:
        row = self._conn.execute(
            "SELECT build_id FROM ranking_builds ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        return self.get_build(row["build_id"])

    # -- Portfolio Snapshots --

    def save_snapshot(self, snap: PortfolioSnapshot) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO portfolio_snapshots
               (snapshot_id, created_at, active_strategies_json,
                instrument_exposure_json, capital_allocation, slot_occupancy,
                max_slots, open_positions_json, family_exposure_json,
                ticker_concentration_json, directional_concentration_json,
                regime_concentration_json, total_active, registry_hash)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (snap.snapshot_id, snap.created_at,
             json.dumps(snap.active_strategies),
             json.dumps(snap.instrument_exposure),
             snap.capital_allocation, snap.slot_occupancy, snap.max_slots,
             json.dumps(snap.open_positions),
             json.dumps(snap.family_exposure),
             json.dumps(snap.ticker_concentration),
             json.dumps(snap.directional_concentration),
             json.dumps(snap.regime_concentration),
             snap.total_active, snap.registry_hash),
        )
        self._conn.commit()

    def get_snapshot(self, snapshot_id: str) -> Optional[PortfolioSnapshot]:
        row = self._conn.execute(
            "SELECT * FROM portfolio_snapshots WHERE snapshot_id=?", (snapshot_id,)
        ).fetchone()
        if not row:
            return None
        return PortfolioSnapshot(
            snapshot_id=row["snapshot_id"],
            created_at=row["created_at"],
            active_strategies=json.loads(row["active_strategies_json"]),
            instrument_exposure=json.loads(row["instrument_exposure_json"]),
            capital_allocation=row["capital_allocation"],
            slot_occupancy=row["slot_occupancy"],
            max_slots=row["max_slots"],
            open_positions=json.loads(row["open_positions_json"]),
            family_exposure=json.loads(row["family_exposure_json"]),
            ticker_concentration=json.loads(row["ticker_concentration_json"]),
            directional_concentration=json.loads(row["directional_concentration_json"]),
            regime_concentration=json.loads(row["regime_concentration_json"]),
            total_active=row["total_active"],
            registry_hash=row["registry_hash"],
        )

    # -- Replacement Comparisons --

    def save_comparison(self, comp: ReplacementComparison) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO replacement_comparisons
               (comparison_id, build_id, incumbent_id, candidate_id,
                incumbent_ticker, candidate_ticker, incumbent_family,
                candidate_family, research_comparison_json,
                operational_comparison_json, lifecycle_comparison_json,
                regime_comparison_json, portfolio_overlap_json,
                risk_comparison_json, confidence, decision,
                reason_codes_json, incumbent_score, candidate_score,
                score_margin, anti_churn_triggered, explanation_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (comp.comparison_id, comp.build_id, comp.incumbent_id,
             comp.candidate_id, comp.incumbent_ticker, comp.candidate_ticker,
             comp.incumbent_family, comp.candidate_family,
             json.dumps(comp.research_comparison),
             json.dumps(comp.operational_comparison),
             json.dumps(comp.lifecycle_comparison),
             json.dumps(comp.regime_comparison),
             json.dumps(comp.portfolio_overlap),
             json.dumps(comp.risk_comparison),
             comp.confidence, comp.decision,
             json.dumps(comp.reason_codes),
             comp.incumbent_score, comp.candidate_score,
             comp.score_margin, 1 if comp.anti_churn_triggered else 0,
             json.dumps(comp.explanation)),
        )
        self._conn.commit()

    def get_comparisons_for_build(self, build_id: str) -> List[ReplacementComparison]:
        rows = self._conn.execute(
            "SELECT * FROM replacement_comparisons WHERE build_id=?", (build_id,)
        ).fetchall()
        return [self._row_to_comparison(r) for r in rows]

    def get_comparison(self, incumbent_id: str, candidate_id: str,
                       build_id: Optional[str] = None) -> Optional[ReplacementComparison]:
        if build_id:
            row = self._conn.execute(
                "SELECT * FROM replacement_comparisons WHERE incumbent_id=? AND candidate_id=? AND build_id=?",
                (incumbent_id, candidate_id, build_id),
            ).fetchone()
        else:
            row = self._conn.execute(
                """SELECT * FROM replacement_comparisons
                   WHERE incumbent_id=? AND candidate_id=?
                   ORDER BY rowid DESC LIMIT 1""",
                (incumbent_id, candidate_id),
            ).fetchone()
        return self._row_to_comparison(row) if row else None

    def _row_to_comparison(self, row: sqlite3.Row) -> ReplacementComparison:
        return ReplacementComparison(
            comparison_id=row["comparison_id"],
            build_id=row["build_id"],
            incumbent_id=row["incumbent_id"],
            candidate_id=row["candidate_id"],
            incumbent_ticker=row["incumbent_ticker"],
            candidate_ticker=row["candidate_ticker"],
            incumbent_family=row["incumbent_family"],
            candidate_family=row["candidate_family"],
            research_comparison=json.loads(row["research_comparison_json"]),
            operational_comparison=json.loads(row["operational_comparison_json"]),
            lifecycle_comparison=json.loads(row["lifecycle_comparison_json"]),
            regime_comparison=json.loads(row["regime_comparison_json"]),
            portfolio_overlap=json.loads(row["portfolio_overlap_json"]),
            risk_comparison=json.loads(row["risk_comparison_json"]),
            confidence=row["confidence"],
            decision=row["decision"],
            reason_codes=json.loads(row["reason_codes_json"]),
            incumbent_score=row["incumbent_score"],
            candidate_score=row["candidate_score"],
            score_margin=row["score_margin"],
            anti_churn_triggered=bool(row["anti_churn_triggered"]),
            explanation=json.loads(row["explanation_json"]),
        )

    # -- Candidate Scores --

    def save_score(self, build_id: str, strategy_id: str, score: CandidateScore) -> None:
        score_id = f"{build_id}__{strategy_id}"
        self._conn.execute(
            """INSERT OR REPLACE INTO candidate_scores
               (score_id, build_id, strategy_id, research_score,
                operational_score, lifecycle_score, regime_score,
                diversification_score, risk_penalty, confidence_penalty,
                total_score, evidence_maturity, confidence_level,
                hard_gate_failures_json, passed_hard_gates,
                component_details_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (score_id, build_id, strategy_id,
             score.research_score, score.operational_score,
             score.lifecycle_score, score.regime_score,
             score.diversification_score, score.risk_penalty,
             score.confidence_penalty, score.total_score,
             score.evidence_maturity, score.confidence_level,
             json.dumps(score.hard_gate_failures),
             1 if score.passed_hard_gates else 0,
             json.dumps(score.component_details)),
        )
        self._conn.commit()

    def get_score(self, build_id: str, strategy_id: str) -> Optional[CandidateScore]:
        row = self._conn.execute(
            "SELECT * FROM candidate_scores WHERE build_id=? AND strategy_id=?",
            (build_id, strategy_id),
        ).fetchone()
        if not row:
            return None
        return CandidateScore(
            research_score=row["research_score"],
            operational_score=row["operational_score"],
            lifecycle_score=row["lifecycle_score"],
            regime_score=row["regime_score"],
            diversification_score=row["diversification_score"],
            risk_penalty=row["risk_penalty"],
            confidence_penalty=row["confidence_penalty"],
            total_score=row["total_score"],
            evidence_maturity=row["evidence_maturity"],
            confidence_level=row["confidence_level"],
            hard_gate_failures=json.loads(row["hard_gate_failures_json"]),
            passed_hard_gates=bool(row["passed_hard_gates"]),
            component_details=json.loads(row["component_details_json"]),
        )

    # -- Ranking Explanations --

    def save_explanation(self, exp: RankingExplanation) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO ranking_explanations
               (explanation_id, build_id, incumbent_id, best_candidate_id,
                decision, urgency, reason_codes_json, component_scores_json,
                what_supports_json, what_contradicts_json, what_is_missing_json,
                what_would_change_json, counterfactual_label)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (exp.explanation_id, exp.build_id, exp.incumbent_id,
             exp.best_candidate_id, exp.decision, exp.urgency,
             json.dumps(exp.reason_codes),
             json.dumps(exp.component_scores),
             json.dumps(exp.what_supports),
             json.dumps(exp.what_contradicts),
             json.dumps(exp.what_is_missing),
             json.dumps(exp.what_would_change),
             exp.counterfactual_label),
        )
        self._conn.commit()

    def get_explanations_for_build(self, build_id: str) -> List[RankingExplanation]:
        rows = self._conn.execute(
            "SELECT * FROM ranking_explanations WHERE build_id=?", (build_id,)
        ).fetchall()
        return [RankingExplanation(
            explanation_id=r["explanation_id"],
            build_id=r["build_id"],
            incumbent_id=r["incumbent_id"],
            best_candidate_id=r["best_candidate_id"],
            decision=r["decision"],
            urgency=r["urgency"],
            reason_codes=json.loads(r["reason_codes_json"]),
            component_scores=json.loads(r["component_scores_json"]),
            what_supports=json.loads(r["what_supports_json"]),
            what_contradicts=json.loads(r["what_contradicts_json"]),
            what_is_missing=json.loads(r["what_is_missing_json"]),
            what_would_change=json.loads(r["what_would_change_json"]),
            counterfactual_label=r["counterfactual_label"],
        ) for r in rows]

    def health_check(self) -> Dict[str, Any]:
        """System Health integration: report ranking store status."""
        latest = self.latest_build()
        if not latest:
            return {
                "status": "UNKNOWN",
                "last_build": None,
                "message": "No ranking builds recorded",
            }
        age = time.time() - latest.started_at
        hours = age / 3600
        if hours > 48:
            status = "STALE"
        elif hours > 24:
            status = "DEGRADED"
        else:
            status = "HEALTHY"
        return {
            "status": status,
            "last_build_id": latest.build_id,
            "last_build_age_hours": round(hours, 2),
            "policy_version": latest.policy_version,
            "incumbent_count": latest.incumbent_count,
            "candidate_count": latest.candidate_count,
            "valid_comparisons": latest.valid_comparisons,
            "hard_gate_rejects": latest.hard_gate_rejects,
            "build_status": latest.status,
        }


# ---------------------------------------------------------------------------
# Builder Functions
# ---------------------------------------------------------------------------

def _make_id(prefix: str, *parts: str) -> str:
    """Deterministic ID from parts."""
    raw = f"{prefix}:" + ":".join(str(p) for p in parts)
    h = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"{prefix}_{h}"


def _registry_hash(registry_data: Dict[str, Any]) -> str:
    """Deterministic hash of registry content for build provenance."""
    content = json.dumps(registry_data.get("strategies", {}), sort_keys=True, default=str)
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _extract_family(strategy_name: str) -> str:
    """Extract strategy family from strategy name (e.g., 'vwap_reversion' → 'vwap_reversion')."""
    # Family is the strategy name without ticker prefix/id suffix
    parts = strategy_name.split("__")
    if len(parts) >= 2:
        return parts[1] if len(parts) > 1 else parts[0]
    return strategy_name


def _determine_evidence_maturity(metrics: Dict[str, Any], history: List[Dict[str, Any]]) -> str:
    """Determine evidence maturity from metrics and history."""
    trades = metrics.get("trades", 0) or metrics.get("trade_count", 0)
    paper_trades = metrics.get("paper_trades", 0)
    broker_trades = metrics.get("broker_trades", 0)
    backtest_quality = metrics.get("backtest_quality_score", 0)
    walk_forward = metrics.get("walk_forward_passed", False)

    if trades < MIN_BACKTEST_TRADES and paper_trades < MIN_PAPER_TRADES:
        return EvidenceMaturity.INSUFFICIENT.value
    if trades < MIN_BACKTEST_TRADES * 2 and paper_trades < MIN_PAPER_TRADES * 2:
        return EvidenceMaturity.EARLY.value
    if (trades >= MIN_BACKTEST_TRADES * 2 and walk_forward) or paper_trades >= MIN_PAPER_TRADES * 2:
        return EvidenceMaturity.MATURE.value
    return EvidenceMaturity.USABLE.value


def _compute_confidence(
    maturity: str,
    metrics: Dict[str, Any],
    has_attribution: bool = False,
    has_regime: bool = False,
    data_freshness_hours: float = 0.0,
    cross_source_consistent: bool = True,
) -> str:
    """Compute ranking confidence from evidence quality signals."""
    score = 0.0
    maturity_scores = {
        EvidenceMaturity.INSUFFICIENT.value: 0,
        EvidenceMaturity.EARLY.value: 1,
        EvidenceMaturity.USABLE.value: 2,
        EvidenceMaturity.MATURE.value: 3,
    }
    score += maturity_scores.get(maturity, 0) * 2
    if has_attribution:
        score += 1.5
    if has_regime:
        score += 1.0
    if data_freshness_hours < 24:
        score += 1.0
    elif data_freshness_hours < 168:
        score += 0.5
    elif data_freshness_hours > 720:
        score -= 1.0
    if not cross_source_consistent:
        score -= 1.5
    # Check for contradictions
    contradictions = metrics.get("contradictions", 0)
    score -= contradictions * 0.5

    if score >= 6:
        return ConfidenceLevel.HIGH.value
    if score >= 4:
        return ConfidenceLevel.MEDIUM.value
    if score >= 2:
        return ConfidenceLevel.LOW.value
    return ConfidenceLevel.INSUFFICIENT.value


def _check_hard_gates(candidate_record: Dict[str, Any]) -> List[str]:
    """Check candidate against hard evidence gates. Returns list of failures."""
    failures = []
    # Missing identity
    if not candidate_record.get("strategy_id") or not candidate_record.get("ticker"):
        failures.append(HardGateFailure.MISSING_IDENTITY.value)
    # Invalid config (no strategy name or params)
    if not candidate_record.get("strategy"):
        failures.append(HardGateFailure.INVALID_CONFIG.value)
    # Insufficient data
    metrics = candidate_record.get("metrics", {})
    trades = metrics.get("trades", 0) or metrics.get("trade_count", 0)
    if trades < MIN_BACKTEST_TRADES and metrics.get("paper_trades", 0) < MIN_PAPER_TRADES:
        failures.append(HardGateFailure.INSUFFICIENT_DATA.value)
    # Severe contradiction
    contradictions = metrics.get("contradictions", 0) or metrics.get("severe_contradictions", 0)
    if contradictions >= 3:
        failures.append(HardGateFailure.SEVERE_CONTRADICTION.value)
    return failures


def _compute_research_score(metrics: Dict[str, Any]) -> float:
    """Compute research quality score from backtest metrics. Range [0, 1]."""
    if not metrics:
        return 0.0
    score = 0.0
    # Profit factor
    pf = metrics.get("pf", metrics.get("profit_factor", 0))
    if pf > 0:
        score += min(pf / 2.0, 0.3)  # cap at 0.3
    # Sharpe ratio
    sharpe = metrics.get("sharpe", 0)
    if sharpe > 0:
        score += min(sharpe / 2.0, 0.25)
    # Win rate
    wr = metrics.get("win_rate", 0)
    if wr > 0:
        score += min(wr / 100.0, 0.15)
    # Max drawdown (lower is better)
    dd = metrics.get("max_drawdown", metrics.get("drawdown", 0))
    if dd > 0:
        score += max(0, 0.15 - dd / 200.0)  # penalty for high drawdown
    # Sample size bonus
    trades = metrics.get("trades", 0) or metrics.get("trade_count", 0)
    if trades >= 100:
        score += 0.15
    elif trades >= 50:
        score += 0.10
    elif trades >= 20:
        score += 0.05
    return min(score, 1.0)


def _compute_operational_score(metrics: Dict[str, Any], history: List[Dict[str, Any]]) -> float:
    """Compute operational evidence score. Range [0, 1]."""
    score = 0.0
    paper_trades = metrics.get("paper_trades", 0)
    broker_trades = metrics.get("broker_trades", 0)
    paper_pnl = metrics.get("paper_pnl", 0)
    broker_pnl = metrics.get("broker_pnl", 0)
    # Paper trading evidence
    if paper_trades > 0:
        score += min(paper_trades / 50.0, 0.3)
    if paper_pnl > 0:
        score += 0.1
    # Broker real evidence
    if broker_trades > 0:
        score += min(broker_trades / 30.0, 0.3)
    if broker_pnl > 0:
        score += 0.15
    # Attribution confidence
    if metrics.get("attribution_confidence"):
        score += 0.15
    return min(score, 1.0)


def _compute_lifecycle_score(lifecycle_health: str, lifecycle_data: Dict[str, Any]) -> float:
    """Compute lifecycle health score. Range [0, 1]."""
    health_scores = {
        LifecycleHealth.HEALTHY.value: 1.0,
        LifecycleHealth.WATCH.value: 0.6,
        LifecycleHealth.DECAY_SUSPECTED.value: 0.3,
        LifecycleHealth.DECAY_CONFIRMED.value: 0.1,
        LifecycleHealth.UNKNOWN.value: 0.4,
    }
    return health_scores.get(lifecycle_health, 0.4)


def _compute_regime_score(
    regime_data: Dict[str, Any],
    current_regime: Optional[Dict[str, Any]] = None,
) -> float:
    """Compute regime robustness score. Range [0, 1]."""
    if not regime_data:
        return 0.3  # neutral when no regime data
    score = 0.0
    # Regime coverage
    coverage = regime_data.get("regime_coverage", 0)
    score += min(coverage / 4.0, 0.3)  # up to 4 regimes
    # Regime-specific performance consistency
    regime_results = regime_data.get("regime_results", {})
    if regime_results:
        positive_count = sum(1 for v in regime_results.values()
                           if isinstance(v, dict) and v.get("pnl", 0) > 0)
        total = len(regime_results) or 1
        score += (positive_count / total) * 0.4
    # Regime concentration penalty
    concentration = regime_data.get("regime_concentration", 0)
    score -= concentration * 0.1
    # Current regime relevance (one dimension, not auto-gating)
    if current_regime and regime_data.get("best_regime"):
        if regime_data["best_regime"] == current_regime.get("classification"):
            score += 0.15  # small bonus, not gate
    return max(0, min(score, 1.0))


def _compute_diversification_score(
    candidate_ticker: str,
    candidate_family: str,
    portfolio_snapshot: PortfolioSnapshot,
    candidate_regime_data: Optional[Dict[str, Any]] = None,
    correlation: Optional[float] = None,
    correlation_status: str = CorrelationStatus.UNKNOWN.value,
) -> Tuple[float, str, Dict[str, Any]]:
    """Compute diversification benefit. Returns (score, benefit_label, details).

    Range [0, 1] where higher = more diversification benefit.
    """
    details: Dict[str, Any] = {}
    penalty = 0.0
    benefit = 0.0

    # Same ticker overlap
    ticker_count = portfolio_snapshot.ticker_concentration.get(candidate_ticker, 0)
    if ticker_count > 0:
        penalty += 0.2 * min(ticker_count, 3)
        details["ticker_overlap"] = ticker_count
    else:
        benefit += 0.15
        details["new_ticker"] = True

    # Same family overlap
    family_count = portfolio_snapshot.family_exposure.get(candidate_family, 0)
    if family_count > 0:
        penalty += 0.15 * min(family_count, 3)
        details["family_overlap"] = family_count
    else:
        benefit += 0.10
        details["new_family"] = True

    # Correlation-based overlap
    if correlation_status == CorrelationStatus.COMPUTED.value and correlation is not None:
        if correlation > 0.7:
            penalty += 0.2
            details["high_correlation"] = correlation
        elif correlation > 0.4:
            penalty += 0.1
            details["moderate_correlation"] = correlation
        elif correlation < 0.2:
            benefit += 0.15
            details["low_correlation"] = correlation
        details["correlation"] = correlation
    else:
        details["correlation_status"] = correlation_status

    # Regime overlap
    if candidate_regime_data and portfolio_snapshot.regime_concentration:
        best_regime = candidate_regime_data.get("best_regime")
        if best_regime and portfolio_snapshot.regime_concentration.get(best_regime, 0) > 1:
            penalty += 0.1
            details["regime_overlap"] = best_regime

    score = max(0, min(0.5 + benefit - penalty, 1.0))

    if benefit > penalty:
        label = DiversificationBenefit.POSITIVE.value
    elif penalty > benefit:
        label = DiversificationBenefit.NEGATIVE.value
    else:
        label = DiversificationBenefit.NEUTRAL.value

    if not portfolio_snapshot.active_strategies:
        label = DiversificationBenefit.INSUFFICIENT.value

    return score, label, details


def _compute_risk_penalty(candidate_record: Dict[str, Any], portfolio_snapshot: PortfolioSnapshot) -> float:
    """Compute risk penalty. Higher = worse risk profile. Range [0, 1]."""
    penalty = 0.0
    metrics = candidate_record.get("metrics", {})
    # High drawdown penalty
    dd = metrics.get("max_drawdown", metrics.get("drawdown", 0))
    if dd > 20:
        penalty += 0.3
    elif dd > 10:
        penalty += 0.15
    # Concentration risk
    ticker = candidate_record.get("ticker", "")
    ticker_count = portfolio_snapshot.ticker_concentration.get(ticker, 0)
    if ticker_count >= 2:
        penalty += 0.2
    # Slot pressure
    if portfolio_snapshot.slot_occupancy >= portfolio_snapshot.max_slots:
        penalty += 0.15
    return min(penalty, 1.0)


def _anti_churn_check(
    incumbent_score: float,
    candidate_score: float,
    incumbent_lifecycle: str,
    policy: RankingPolicy,
) -> Tuple[bool, str]:
    """Check anti-churn protection. Returns (triggered, reason)."""
    margin = candidate_score - incumbent_score
    required_margin = policy.min_score_margin

    # Healthy incumbent requires stronger evidence
    if incumbent_lifecycle == LifecycleHealth.HEALTHY.value:
        required_margin = max(required_margin, policy.min_healthy_incumbent_margin)

    if margin < required_margin:
        return True, f"margin {margin:.3f} < required {required_margin:.3f}"

    return False, ""


# ---------------------------------------------------------------------------
# Main Builder Functions
# ---------------------------------------------------------------------------

def build_portfolio_snapshot(
    registry_data: Dict[str, Any],
    lifecycle_data: Optional[Dict[str, Any]] = None,
    open_positions: Optional[List[Dict[str, Any]]] = None,
    max_slots: int = 3,
    capital: float = 0.0,
) -> PortfolioSnapshot:
    """Build a read-only snapshot of current portfolio decision context.

    Registry is the canonical source. Broker truth for open positions.
    If no positions are open, snapshot still valid with zero live exposure.
    """
    snapshot_id = _make_id("psnap", _registry_hash(registry_data), str(time.time()))
    strategies = registry_data.get("strategies", {})

    # Active strategies (active_watchlist + active_signal_pool)
    active = []
    ticker_conc: Dict[str, int] = {}
    family_exp: Dict[str, int] = {}
    dir_exp: Dict[str, int] = {}
    regime_exp: Dict[str, int] = {}

    for sid, sdata in strategies.items():
        status = sdata.get("status", "")
        if status in ("active_watchlist", "active_signal_pool"):
            ticker = sdata.get("ticker", "")
            strategy_name = sdata.get("strategy", "")
            family = _extract_family(strategy_name)
            active.append({
                "strategy_id": sid,
                "ticker": ticker,
                "strategy": strategy_name,
                "family": family,
                "status": status,
                "params": sdata.get("params", {}),
                "metrics": sdata.get("metrics", {}),
                "active_rank": sdata.get("active_rank", 0),
            })
            ticker_conc[ticker] = ticker_conc.get(ticker, 0) + 1
            family_exp[family] = family_exp.get(family, 0) + 1
            # Direction from metrics or default
            direction = sdata.get("metrics", {}).get("direction", "long")
            dir_exp[direction] = dir_exp.get(direction, 0) + 1

    # Regime concentration from lifecycle data if available
    if lifecycle_data:
        for sid, ldata in lifecycle_data.items():
            if isinstance(ldata, dict):
                best_r = ldata.get("best_regime", "")
                if best_r:
                    regime_exp[best_r] = regime_exp.get(best_r, 0) + 1

    snapshot = PortfolioSnapshot(
        snapshot_id=snapshot_id,
        created_at=time.time(),
        active_strategies=active,
        instrument_exposure={t: 1.0 for t in ticker_conc},
        capital_allocation=capital,
        slot_occupancy=len(active),
        max_slots=max_slots,
        open_positions=open_positions or [],
        family_exposure=family_exp,
        ticker_concentration=ticker_conc,
        directional_concentration=dir_exp,
        regime_concentration=regime_exp,
        total_active=len(active),
        registry_hash=_registry_hash(registry_data),
    )
    return snapshot


def score_candidate(
    candidate_record: Dict[str, Any],
    lifecycle_health: str = LifecycleHealth.UNKNOWN.value,
    lifecycle_data: Optional[Dict[str, Any]] = None,
    regime_data: Optional[Dict[str, Any]] = None,
    current_regime: Optional[Dict[str, Any]] = None,
    portfolio_snapshot: Optional[PortfolioSnapshot] = None,
    has_attribution: bool = False,
    has_regime_evidence: bool = False,
    data_freshness_hours: float = 0.0,
    cross_source_consistent: bool = True,
    correlation: Optional[float] = None,
    correlation_status: str = CorrelationStatus.UNKNOWN.value,
    policy: Optional[RankingPolicy] = None,
) -> CandidateScore:
    """Score a candidate across all dimensions. Deterministic and auditable."""
    if policy is None:
        policy = RankingPolicy()

    metrics = candidate_record.get("metrics", {})
    history = candidate_record.get("history", [])

    # Hard gates first
    hard_failures = _check_hard_gates(candidate_record)
    passed_gates = len(hard_failures) == 0

    # Evidence maturity
    maturity = _determine_evidence_maturity(metrics, history)

    # Confidence
    confidence = _compute_confidence(
        maturity, metrics, has_attribution, has_regime_evidence,
        data_freshness_hours, cross_source_consistent,
    )

    # Component scores
    research_s = _compute_research_score(metrics)
    operational_s = _compute_operational_score(metrics, history)
    lifecycle_s = _compute_lifecycle_score(lifecycle_health, lifecycle_data or {})
    regime_s = _compute_regime_score(regime_data or {}, current_regime)

    if portfolio_snapshot:
        family = _extract_family(candidate_record.get("strategy", ""))
        div_s, _, div_details = _compute_diversification_score(
            candidate_record.get("ticker", ""),
            family,
            portfolio_snapshot,
            regime_data,
            correlation,
            correlation_status,
        )
        risk_p = _compute_risk_penalty(candidate_record, portfolio_snapshot)
    else:
        div_s = 0.5
        div_details = {}
        risk_p = 0.0

    # Maturity multiplier
    mat_mult = policy.maturity_multiplier.get(maturity, 0.5)

    # Weighted total (before penalties)
    weighted = (
        research_s * policy.weight_research
        + operational_s * policy.weight_operational
        + lifecycle_s * policy.weight_lifecycle
        + regime_s * policy.weight_regime
        + div_s * policy.weight_diversification
    ) * mat_mult

    # Apply risk penalty
    total = max(0, weighted - risk_p * policy.weight_risk)

    # Confidence penalty
    conf_pen = policy.confidence_penalty.get(confidence, 0.3)
    total = max(0, total - conf_pen)

    # If hard gates failed, total doesn't matter but record it
    if not passed_gates:
        total = 0.0

    component_details = {
        "research_score_raw": research_s,
        "operational_score_raw": operational_s,
        "lifecycle_score_raw": lifecycle_s,
        "regime_score_raw": regime_s,
        "diversification_score_raw": div_s,
        "risk_penalty_raw": risk_p,
        "maturity_multiplier": mat_mult,
        "confidence_penalty": conf_pen,
        "diversification_details": div_details,
        "maturity": maturity,
    }

    return CandidateScore(
        research_score=round(research_s, 4),
        operational_score=round(operational_s, 4),
        lifecycle_score=round(lifecycle_s, 4),
        regime_score=round(regime_s, 4),
        diversification_score=round(div_s, 4),
        risk_penalty=round(risk_p, 4),
        confidence_penalty=round(conf_pen, 4),
        total_score=round(total, 4),
        evidence_maturity=maturity,
        confidence_level=confidence,
        hard_gate_failures=hard_failures,
        passed_hard_gates=passed_gates,
        component_details=component_details,
    )


def pairwise_compare(
    incumbent_record: Dict[str, Any],
    candidate_record: Dict[str, Any],
    incumbent_score: CandidateScore,
    candidate_score: CandidateScore,
    build_id: str,
    portfolio_snapshot: Optional[PortfolioSnapshot] = None,
    lifecycle_health_incumbent: str = LifecycleHealth.UNKNOWN.value,
    lifecycle_health_candidate: str = LifecycleHealth.UNKNOWN.value,
    policy: Optional[RankingPolicy] = None,
) -> ReplacementComparison:
    """Produce a pairwise comparison between incumbent and candidate.

    Returns a ReplacementComparison with decision, reason codes, and explanation.
    """
    if policy is None:
        policy = RankingPolicy()

    comp_id = _make_id("comp", incumbent_record.get("strategy_id", ""),
                       candidate_record.get("strategy_id", ""))

    inc_id = incumbent_record.get("strategy_id", "")
    cand_id = candidate_record.get("strategy_id", "")
    inc_ticker = incumbent_record.get("ticker", "")
    cand_ticker = candidate_record.get("ticker", "")
    inc_family = _extract_family(incumbent_record.get("strategy", ""))
    cand_family = _extract_family(candidate_record.get("strategy", ""))

    reason_codes: List[str] = []
    explanation: Dict[str, Any] = {}

    # Research comparison
    research_comp = {
        "incumbent_research_score": incumbent_score.research_score,
        "candidate_research_score": candidate_score.research_score,
        "candidate_advantage": round(candidate_score.research_score - incumbent_score.research_score, 4),
    }
    if candidate_score.research_score > incumbent_score.research_score + 0.1:
        reason_codes.append("CANDIDATE_RESEARCH_ADVANTAGE")

    # Operational comparison
    ops_comp = {
        "incumbent_operational_score": incumbent_score.operational_score,
        "candidate_operational_score": candidate_score.operational_score,
        "candidate_advantage": round(candidate_score.operational_score - incumbent_score.operational_score, 4),
    }
    if candidate_score.operational_score > incumbent_score.operational_score + 0.1:
        reason_codes.append("CANDIDATE_OPERATIONAL_ADVANTAGE")

    # Lifecycle comparison
    lc_comp = {
        "incumbent_health": lifecycle_health_incumbent,
        "candidate_health": lifecycle_health_candidate,
        "incumbent_lifecycle_score": incumbent_score.lifecycle_score,
        "candidate_lifecycle_score": candidate_score.lifecycle_score,
    }
    if lifecycle_health_incumbent in (LifecycleHealth.DECAY_CONFIRMED.value, LifecycleHealth.DECAY_SUSPECTED.value):
        reason_codes.append("INCUMBENT_DECAY_CONFIRMED")
    if lifecycle_health_incumbent == LifecycleHealth.HEALTHY.value:
        reason_codes.append("INCUMBENT_HEALTHY")

    # Regime comparison
    regime_comp = {
        "incumbent_regime_score": incumbent_score.regime_score,
        "candidate_regime_score": candidate_score.regime_score,
    }
    if candidate_score.regime_score > incumbent_score.regime_score + 0.15:
        reason_codes.append("CANDIDATE_REGIME_ROBUSTNESS")

    # Portfolio overlap
    portfolio_overlap = {
        "ticker_same": inc_ticker == cand_ticker,
        "family_same": inc_family == cand_family,
        "diversification_score": candidate_score.diversification_score,
    }
    if candidate_score.diversification_score < 0.3:
        reason_codes.append("CANDIDATE_TOO_CORRELATED")
    elif candidate_score.diversification_score > 0.6:
        reason_codes.append("CANDIDATE_DIVERSIFICATION_BENEFIT")
    if candidate_score.risk_penalty > 0.3:
        reason_codes.append("CANDIDATE_CONCENTRATION_PENALTY")

    # Risk comparison
    risk_comp = {
        "incumbent_risk_penalty": incumbent_score.risk_penalty,
        "candidate_risk_penalty": candidate_score.risk_penalty,
    }

    # Same family duplicate detection
    if inc_family == cand_family and inc_ticker == cand_ticker:
        # Check if params are very similar
        inc_params = incumbent_record.get("params", {})
        cand_params = candidate_record.get("params", {})
        if inc_params == cand_params or (
            not inc_params and not cand_params
        ):
            reason_codes.append("HARD_GATE_REJECTED")
            explanation["hard_gate"] = HardGateFailure.SAME_FAMILY_DUPLICATE.value

    # Confidence
    # Use the lower of the two confidence levels
    conf_order = {
        ConfidenceLevel.HIGH.value: 3,
        ConfidenceLevel.MEDIUM.value: 2,
        ConfidenceLevel.LOW.value: 1,
        ConfidenceLevel.INSUFFICIENT.value: 0,
    }
    inc_conf = conf_order.get(incumbent_score.confidence_level, 0)
    cand_conf = conf_order.get(candidate_score.confidence_level, 0)
    min_conf_val = min(inc_conf, cand_conf)
    confidence = {v: k for k, v in conf_order.items()}[min_conf_val]

    # Anti-churn check
    anti_triggered, anti_reason = _anti_churn_check(
        incumbent_score.total_score,
        candidate_score.total_score,
        lifecycle_health_incumbent,
        policy,
    )
    if anti_triggered:
        reason_codes.append("ANTI_CHURN_PROTECTED")
        explanation["anti_churn_reason"] = anti_reason

    # Score margin
    score_margin = candidate_score.total_score - incumbent_score.total_score

    # Decision logic
    if not candidate_score.passed_hard_gates:
        decision = DecisionOutput.INSUFFICIENT_EVIDENCE.value
        explanation["decision_reason"] = "candidate_failed_hard_gates"
    elif confidence == ConfidenceLevel.INSUFFICIENT.value:
        decision = DecisionOutput.INSUFFICIENT_EVIDENCE.value
        explanation["decision_reason"] = "insufficient_confidence"
    elif anti_triggered:
        decision = DecisionOutput.KEEP.value
        explanation["decision_reason"] = "anti_churn_protected"
    elif score_margin <= 0:
        decision = DecisionOutput.KEEP.value
        explanation["decision_reason"] = "candidate_not_ahead"
    elif score_margin < policy.min_score_margin:
        # Small advantage — watch or revalidate
        if lifecycle_health_incumbent in (
            LifecycleHealth.DECAY_CONFIRMED.value,
            LifecycleHealth.DECAY_SUSPECTED.value,
        ):
            decision = DecisionOutput.REVALIDATE.value
            reason_codes.append("REVALIDATION_REQUIRED")
            explanation["decision_reason"] = "weak_candidate_incumbent_decaying"
        else:
            decision = DecisionOutput.WATCH.value
            explanation["decision_reason"] = "small_advantage_watch"
    else:
        # Candidate has meaningful advantage
        if lifecycle_health_incumbent == LifecycleHealth.HEALTHY.value:
            # Healthy incumbent — need even stronger evidence
            if score_margin >= policy.min_healthy_incumbent_margin:
                decision = DecisionOutput.REPLACEMENT_CANDIDATE.value
                explanation["decision_reason"] = "strong_evidence_vs_healthy_incumbent"
            else:
                decision = DecisionOutput.WATCH.value
                explanation["decision_reason"] = "insufficient_margin_vs_healthy"
        else:
            decision = DecisionOutput.REPLACEMENT_CANDIDATE.value
            explanation["decision_reason"] = "candidate_ahead_of_non_healthy_incumbent"

    # Override if contradictions exist
    contradictions = incumbent_record.get("metrics", {}).get("contradictions", 0) or 0
    contradictions += candidate_record.get("metrics", {}).get("contradictions", 0) or 0
    if contradictions >= 2:
        reason_codes.append("CONTRADICTORY_EVIDENCE")

    # What would change the recommendation
    what_would_change = []
    if decision == DecisionOutput.KEEP.value:
        what_would_change.append("candidate showing larger evidence margin")
        what_would_change.append("incumbent entering decay")
    elif decision == DecisionOutput.WATCH.value:
        what_would_change.append("sustained evidence improvement over time")
    elif decision == DecisionOutput.REVALIDATE.value:
        what_would_change.append("clearer evidence distinguishing incumbent vs candidate")

    explanation["what_would_change"] = what_would_change

    return ReplacementComparison(
        comparison_id=comp_id,
        build_id=build_id,
        incumbent_id=inc_id,
        candidate_id=cand_id,
        incumbent_ticker=inc_ticker,
        candidate_ticker=cand_ticker,
        incumbent_family=inc_family,
        candidate_family=cand_family,
        research_comparison=research_comp,
        operational_comparison=ops_comp,
        lifecycle_comparison=lc_comp,
        regime_comparison=regime_comp,
        portfolio_overlap=portfolio_overlap,
        risk_comparison=risk_comp,
        confidence=confidence,
        decision=decision,
        reason_codes=reason_codes,
        incumbent_score=incumbent_score.total_score,
        candidate_score=candidate_score.total_score,
        score_margin=round(score_margin, 4),
        anti_churn_triggered=anti_triggered,
        explanation=explanation,
    )


def rank_candidates_for_incumbent(
    incumbent_record: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    build_id: str,
    portfolio_snapshot: Optional[PortfolioSnapshot] = None,
    lifecycle_health: str = LifecycleHealth.UNKNOWN.value,
    lifecycle_data: Optional[Dict[str, Any]] = None,
    regime_data_map: Optional[Dict[str, Dict[str, Any]]] = None,
    current_regime: Optional[Dict[str, Any]] = None,
    policy: Optional[RankingPolicy] = None,
) -> Tuple[DecisionOutput, Optional[ReplacementComparison], List[ReplacementComparison]]:
    """Rank all candidates for a single incumbent.

    Returns (best_decision, best_comparison, all_comparisons).
    """
    if policy is None:
        policy = RankingPolicy()

    if not candidates:
        return DecisionOutput.NO_VALID_CANDIDATE, None, []

    # Score the incumbent
    inc_lifecycle = lifecycle_data.get(incumbent_record.get("strategy_id", ""), {}) if lifecycle_data else {}
    inc_lifecycle_health = inc_lifecycle.get("evidence_health", lifecycle_health)
    inc_regime = regime_data_map.get(incumbent_record.get("strategy_id", {}), {}) if regime_data_map else {}
    inc_score = score_candidate(
        incumbent_record,
        lifecycle_health=inc_lifecycle_health,
        lifecycle_data=inc_lifecycle,
        regime_data=inc_regime,
        current_regime=current_regime,
        portfolio_snapshot=portfolio_snapshot,
        policy=policy,
    )

    all_comparisons: List[ReplacementComparison] = []
    valid_candidates = 0

    for cand in candidates:
        # Skip self-comparison
        if cand.get("strategy_id") == incumbent_record.get("strategy_id"):
            continue

        # Score candidate
        cand_id = cand.get("strategy_id", "")
        cand_lifecycle_data = lifecycle_data.get(cand_id, {}) if lifecycle_data else {}
        cand_lifecycle_health = cand_lifecycle_data.get("evidence_health", LifecycleHealth.UNKNOWN.value)
        cand_regime = regime_data_map.get(cand_id, {}) if regime_data_map else {}

        cand_score = score_candidate(
            cand,
            lifecycle_health=cand_lifecycle_health,
            lifecycle_data=cand_lifecycle_data,
            regime_data=cand_regime,
            current_regime=current_regime,
            portfolio_snapshot=portfolio_snapshot,
            policy=policy,
        )

        comp = pairwise_compare(
            incumbent_record, cand,
            inc_score, cand_score,
            build_id,
            portfolio_snapshot=portfolio_snapshot,
            lifecycle_health_incumbent=inc_lifecycle_health,
            lifecycle_health_candidate=cand_lifecycle_health,
            policy=policy,
        )
        all_comparisons.append(comp)
        if cand_score.passed_hard_gates:
            valid_candidates += 1

    if not all_comparisons:
        return DecisionOutput.NO_VALID_CANDIDATE, None, []

    # Find best candidate
    valid_comps = [c for c in all_comparisons if c.decision != DecisionOutput.INSUFFICIENT_EVIDENCE.value]
    if not valid_comps:
        return DecisionOutput.INSUFFICIENT_EVIDENCE, None, all_comparisons

    # Sort by candidate_score descending
    valid_comps.sort(key=lambda c: c.candidate_score, reverse=True)
    best = valid_comps[0]

    best_decision = DecisionOutput(best.decision)
    return best_decision, best, all_comparisons


def build_ranking(
    registry_data: Dict[str, Any],
    lifecycle_data: Optional[Dict[str, Any]] = None,
    regime_data_map: Optional[Dict[str, Dict[str, Any]]] = None,
    current_regime: Optional[Dict[str, Any]] = None,
    open_positions: Optional[List[Dict[str, Any]]] = None,
    max_slots: int = 3,
    capital: float = 0.0,
    policy: Optional[RankingPolicy] = None,
    store: Optional[ReplacementRankingStore] = None,
) -> RankingBuild:
    """Execute a full portfolio-level replacement ranking build.

    Read-only operation. No mutations to registry, broker, or swap state.
    Returns the RankingBuild with all results persisted to the store.
    """
    if policy is None:
        policy = RankingPolicy()

    started_at = time.time()
    build_id = _make_id("ranking", _registry_hash(registry_data), str(started_at))

    # Build portfolio snapshot
    snapshot = build_portfolio_snapshot(
        registry_data, lifecycle_data, open_positions, max_slots, capital,
    )

    # Separate incumbents and candidates
    strategies = registry_data.get("strategies", {})
    incumbents = []
    candidates = []
    for sid, sdata in strategies.items():
        status = sdata.get("status", "")
        if status in ("active_watchlist", "active_signal_pool"):
            incumbents.append({"strategy_id": sid, **sdata})
        elif status in ("registry/candidate", "waitlist"):
            candidates.append({"strategy_id": sid, **sdata})
        # rejected/rotated_out/expired/conflicted → not candidates

    # Store all scores
    all_scores: Dict[str, CandidateScore] = {}
    for inc in incumbents:
        inc_id = inc.get("strategy_id", "")
        inc_lifecycle_data = lifecycle_data.get(inc_id, {}) if lifecycle_data else {}
        inc_lh = inc_lifecycle_data.get("evidence_health", LifecycleHealth.UNKNOWN.value)
        inc_regime = regime_data_map.get(inc_id, {}) if regime_data_map else {}
        score = score_candidate(
            inc, lifecycle_health=inc_lh, lifecycle_data=inc_lifecycle_data,
            regime_data=inc_regime, current_regime=current_regime,
            portfolio_snapshot=snapshot, policy=policy,
        )
        all_scores[inc_id] = score

    for cand in candidates:
        cand_id = cand.get("strategy_id", "")
        cand_lifecycle_data = lifecycle_data.get(cand_id, {}) if lifecycle_data else {}
        cand_lh = cand_lifecycle_data.get("evidence_health", LifecycleHealth.UNKNOWN.value)
        cand_regime = regime_data_map.get(cand_id, {}) if regime_data_map else {}
        score = score_candidate(
            cand, lifecycle_health=cand_lh, lifecycle_data=cand_lifecycle_data,
            regime_data=cand_regime, current_regime=current_regime,
            portfolio_snapshot=snapshot, policy=policy,
        )
        all_scores[cand_id] = score

    # Rank for each incumbent
    all_comparisons: List[ReplacementComparison] = []
    explanations: List[RankingExplanation] = []
    decision_counts = {d.value: 0 for d in DecisionOutput}
    hard_gate_rejects = 0

    for inc in incumbents:
        inc_id = inc.get("strategy_id", "")
        inc_lifecycle_data = lifecycle_data.get(inc_id, {}) if lifecycle_data else {}
        inc_lh = inc_lifecycle_data.get("evidence_health", LifecycleHealth.UNKNOWN.value)

        best_decision, best_comp, comps = rank_candidates_for_incumbent(
            inc, candidates, build_id,
            portfolio_snapshot=snapshot,
            lifecycle_health=inc_lh,
            lifecycle_data=lifecycle_data,
            regime_data_map=regime_data_map,
            current_regime=current_regime,
            policy=policy,
        )
        all_comparisons.extend(comps)
        decision_counts[best_decision.value] = decision_counts.get(best_decision.value, 0) + 1

        # Build explanation
        exp_id = _make_id("exp", inc_id, build_id)
        what_supports = []
        what_contradicts = []
        what_is_missing = []

        if best_comp:
            if best_comp.candidate_score > best_comp.incumbent_score:
                what_supports.append(f"Candidate {best_comp.candidate_id} shows score advantage")
            if "INCUMBENT_HEALTHY" in best_comp.reason_codes:
                what_supports.append("Incumbent lifecycle is healthy")
            if "INCUMBENT_DECAY_CONFIRMED" in best_comp.reason_codes:
                what_contradicts.append("Incumbent shows decay indicators")
            if "CANDIDATE_EVIDENCE_WEAK" in best_comp.reason_codes:
                what_contradicts.append("Candidate evidence is weak")
            if best_comp.confidence == ConfidenceLevel.LOW.value:
                what_is_missing.append("Higher confidence evidence needed")
            if best_comp.confidence == ConfidenceLevel.INSUFFICIENT.value:
                what_is_missing.append("Sufficient evidence for comparison")

        # Determine urgency
        urgency = ReplacementUrgency.NONE.value
        if best_decision == DecisionOutput.REPLACEMENT_CANDIDATE.value:
            urgency = ReplacementUrgency.HIGH.value
        elif best_decision == DecisionOutput.REVALIDATE.value:
            urgency = ReplacementUrgency.MEDIUM.value
        elif best_decision == DecisionOutput.WATCH.value:
            urgency = ReplacementUrgency.LOW.value

        exp = RankingExplanation(
            explanation_id=exp_id,
            build_id=build_id,
            incumbent_id=inc_id,
            best_candidate_id=best_comp.candidate_id if best_comp else None,
            decision=best_decision.value,
            urgency=urgency,
            reason_codes=best_comp.reason_codes if best_comp else [],
            component_scores=all_scores.get(inc_id, CandidateScore()).component_details,
            what_supports=what_supports,
            what_contradicts=what_contradicts,
            what_is_missing=what_is_missing,
            what_would_change=best_comp.explanation.get("what_would_change", []) if best_comp else [],
        )
        explanations.append(exp)

        # Count hard gate rejects
        for comp in comps:
            if not all_scores.get(comp.candidate_id, CandidateScore()).passed_hard_gates:
                hard_gate_rejects += 1

    finished_at = time.time()

    # Build metadata
    build = RankingBuild(
        build_id=build_id,
        started_at=started_at,
        finished_at=finished_at,
        registry_hash=_registry_hash(registry_data),
        policy_version=policy.version,
        candidate_count=len(candidates),
        incumbent_count=len(incumbents),
        valid_comparisons=len([c for c in all_comparisons
                               if c.decision != DecisionOutput.INSUFFICIENT_EVIDENCE.value]),
        hard_gate_rejects=hard_gate_rejects,
        status="completed",
    )

    # Persist to store if provided
    if store is not None:
        store.save_snapshot(snapshot)
        build.portfolio_snapshot_id = snapshot.snapshot_id
        store.save_build(build)
        for sid, sc in all_scores.items():
            store.save_score(build_id, sid, sc)
        for comp in all_comparisons:
            store.save_comparison(comp)
        for exp in explanations:
            store.save_explanation(exp)

    return build


# ---------------------------------------------------------------------------
# Query API
# ---------------------------------------------------------------------------

def get_ranking_build(store: ReplacementRankingStore, build_id: str) -> Optional[RankingBuild]:
    """Get a ranking build by ID."""
    return store.get_build(build_id)


def get_incumbent_rankings(
    store: ReplacementRankingStore, build_id: str, incumbent_id: str
) -> Optional[RankingExplanation]:
    """Get ranking explanation for an incumbent in a build."""
    exps = store.get_explanations_for_build(build_id)
    for e in exps:
        if e.incumbent_id == incumbent_id:
            return e
    return None


def get_candidate_comparison(
    store: ReplacementRankingStore,
    incumbent_id: str,
    candidate_id: str,
    build_id: Optional[str] = None,
) -> Optional[ReplacementComparison]:
    """Get a specific pairwise comparison."""
    return store.get_comparison(incumbent_id, candidate_id, build_id)


def get_portfolio_replacement_summary(
    store: ReplacementRankingStore,
    build_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Get a summary of the latest (or specified) ranking build."""
    if build_id:
        build = store.get_build(build_id)
    else:
        build = store.latest_build()
    if not build:
        return {"status": "NO_BUILDS"}

    exps = store.get_explanations_for_build(build.build_id)
    comps = store.get_comparisons_for_build(build.build_id)

    decision_dist = {}
    urgency_dist = {}
    for e in exps:
        decision_dist[e.decision] = decision_dist.get(e.decision, 0) + 1
        urgency_dist[e.urgency] = urgency_dist.get(e.urgency, 0) + 1

    confidence_dist = {}
    for c in comps:
        confidence_dist[c.confidence] = confidence_dist.get(c.confidence, 0) + 1

    return {
        "build_id": build.build_id,
        "policy_version": build.policy_version,
        "incumbent_count": build.incumbent_count,
        "candidate_count": build.candidate_count,
        "valid_comparisons": build.valid_comparisons,
        "hard_gate_rejects": build.hard_gate_rejects,
        "decision_distribution": decision_dist,
        "urgency_distribution": urgency_dist,
        "confidence_distribution": confidence_dist,
        "status": build.status,
    }


# ---------------------------------------------------------------------------
# Counterfactual Portfolio Comparison (SIMULATED)
# ---------------------------------------------------------------------------

def simulate_portfolio_comparison(
    incumbent_record: Dict[str, Any],
    candidate_record: Dict[str, Any],
    other_active: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Simulated counterfactual: portfolio with incumbent vs portfolio with candidate.

    Label: SIMULATED / NON-REALIZED.
    Only produces output when both strategies have aligned return data.
    Returns None when data is insufficient.
    """
    inc_returns = incumbent_record.get("metrics", {}).get("daily_returns")
    cand_returns = candidate_record.get("metrics", {}).get("daily_returns")

    if not inc_returns or not cand_returns:
        return None
    if len(inc_returns) < MIN_ALIGNED_OBSERVATIONS or len(cand_returns) < MIN_ALIGNED_OBSERVATIONS:
        return None

    import numpy as np
    inc_arr = np.array(inc_returns, dtype=float)
    cand_arr = np.array(cand_returns, dtype=float)
    min_len = min(len(inc_arr), len(cand_arr))
    inc_arr = inc_arr[:min_len]
    cand_arr = cand_arr[:min_len]

    # Simple equal-weight portfolio proxy
    other_returns = []
    for o in other_active:
        oret = o.get("metrics", {}).get("daily_returns")
        if oret and len(oret) >= min_len:
            other_returns.append(np.array(oret[:min_len], dtype=float))

    if other_returns:
        other_avg = np.mean(other_returns, axis=0)
        n_total = 2 + len(other_returns)
        port_with_inc = (inc_arr + other_avg * len(other_returns)) / n_total
        port_with_cand = (cand_arr + other_avg * len(other_returns)) / n_total
    else:
        port_with_inc = inc_arr
        port_with_cand = cand_arr

    inc_cum = np.cumprod(1 + port_with_inc)
    cand_cum = np.cumprod(1 + port_with_cand)

    inc_total_return = float(inc_cum[-1] - 1) if len(inc_cum) > 0 else 0.0
    cand_total_return = float(cand_cum[-1] - 1) if len(cand_cum) > 0 else 0.0

    inc_dd = float(np.min(inc_cum / np.maximum.accumulate(inc_cum)) - 1) if len(inc_cum) > 1 else 0.0
    cand_dd = float(np.min(cand_cum / np.maximum.accumulate(cand_cum)) - 1) if len(cand_cum) > 1 else 0.0

    inc_vol = float(np.std(port_with_inc) * np.sqrt(252)) if len(port_with_inc) > 1 else 0.0
    cand_vol = float(np.std(port_with_cand) * np.sqrt(252)) if len(port_with_cand) > 1 else 0.0

    corr = float(np.corrcoef(inc_arr, cand_arr)[0, 1]) if min_len > 2 else None

    return {
        "label": "SIMULATED / NON-REALIZED",
        "methodology": "equal_weight_proxy",
        "observations": min_len,
        "incumbent_total_return": round(inc_total_return, 4),
        "candidate_total_return": round(cand_total_return, 4),
        "incumbent_max_drawdown": round(inc_dd, 4),
        "candidate_max_drawdown": round(cand_dd, 4),
        "incumbent_volatility": round(inc_vol, 4),
        "candidate_volatility": round(cand_vol, 4),
        "return_correlation": round(corr, 4) if corr is not None else None,
        "return_delta": round(cand_total_return - inc_total_return, 4),
        "drawdown_delta": round(cand_dd - inc_dd, 4),
    }

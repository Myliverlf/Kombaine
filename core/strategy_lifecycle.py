"""Strategy Lifecycle & Decay Detection — Iteration 10.

Read-only observer that evaluates strategy evidence health, decay indicators,
revalidation status, and generates bounded recommendations.

HARD BOUNDARY: This module is CLASS 1 — monitoring/recommendation only.
- Reads: registry, experiment memory, research knowledge
- Writes: lifecycle DB only (state/strategy_lifecycle.db)
- NEVER: registry mutations, swap/signal mutations, broker calls, trading

Every recommendation is reconstructable from canonical evidence.
No LLM involvement in decay/recommendation decisions.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LIFECYCLE_SCHEMA_VERSION = "1.0.0"
OBSERVER_VERSION = "1.0.0"
REVALIDATION_POLICY_VERSION = "1.0.0"
DECAY_POLICY_VERSION = "1.0.0"
LIFECYCLE_DB_NAME = "strategy_lifecycle.db"

# Revalidation cadence defaults (in seconds)
REVALIDATION_DUE_DAYS = 90       # revalidation due after 90 days
REVALIDATION_OVERDUE_DAYS = 180  # overdue after 180 days

# Decay thresholds
PF_DROP_THRESHOLD = 0.30         # 30% drop in profit factor
SHARPE_DROP_THRESHOLD = 0.40     # 40% drop in Sharpe
DD_WORSENING_THRESHOLD = 0.50    # 50% increase in max drawdown
WINRATE_DROP_THRESHOLD = 0.15    # 15 percentage point drop
STALE_EVIDENCE_DAYS = 120        # evidence older than 120 days is stale
CONFIDENCE_DOWNGRADE_THRESHOLD = 1  # confidence level drop count

# Evidence freshness windows
FRESH_BACKTEST_DAYS = 180
FRESH_PAPER_DAYS = 60


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class EvidenceHealth(str, Enum):
    """Taxonomy of evidence health states for a strategy."""
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    HEALTHY = "HEALTHY"
    WATCH = "WATCH"
    DECAY_SUSPECTED = "DECAY_SUSPECTED"
    DECAY_CONFIRMED = "DECAY_CONFIRMED"
    CONTESTED = "CONTESTED"
    STALE = "STALE"


class DecayState(str, Enum):
    """Binary decay state tracking."""
    UNKNOWN = "UNKNOWN"
    HEALTHY = "HEALTHY"
    DECAYING = "DECAYING"
    RECOVERING = "RECOVERING"


class RevalidationState(str, Enum):
    """Revalidation due tracking."""
    NOT_DUE = "NOT_DUE"
    DUE = "DUE"
    OVERDUE = "OVERDUE"
    BLOCKED_INSUFFICIENT_CONTEXT = "BLOCKED_INSUFFICIENT_CONTEXT"


class Recommendation(str, Enum):
    """Bounded recommendation taxonomy — never an action."""
    NO_ACTION = "NO_ACTION"
    MONITOR = "MONITOR"
    REVALIDATE = "REVALIDATE"
    REVIEW = "REVIEW"
    CONSIDER_ROTATION = "CONSIDER_ROTATION"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"


class IndicatorType(str, Enum):
    """Deterministic decay indicator types."""
    PF_DROP = "PF_DROP"
    SHARPE_DROP = "SHARPE_DROP"
    DD_WORSENING = "DD_WORSENING"
    WINRATE_DROP = "WINRATE_DROP"
    ELIGIBILITY_REVERSAL = "ELIGIBILITY_REVERSAL"
    REVALIDATION_FAILURE = "REVALIDATION_FAILURE"
    CONFIDENCE_WEAKENING = "CONFIDENCE_WEAKENING"
    CONTRADICTION_INCREASE = "CONTRADICTION_INCREASE"
    EVIDENCE_STALE = "EVIDENCE_STALE"
    EVIDENCE_INSUFFICIENT = "EVIDENCE_INSUFFICIENT"
    PERFORMANCE_SIGN_REVERSAL = "PERFORMANCE_SIGN_REVERSAL"
    COST_SENSITIVITY_WORSENING = "COST_SENSITIVITY_WORSENING"


class EvidenceClass(str, Enum):
    """Evidence source classification — never silently mixed."""
    BACKTEST = "BACKTEST"
    WALK_FORWARD = "WALK_FORWARD"
    PAPER = "PAPER"
    BROKER_REAL = "BROKER_REAL"


# Severity mapping for indicators
INDICATOR_SEVERITY: Dict[IndicatorType, str] = {
    IndicatorType.PF_DROP: "MATERIAL",
    IndicatorType.SHARPE_DROP: "MATERIAL",
    IndicatorType.DD_WORSENING: "MATERIAL",
    IndicatorType.WINRATE_DROP: "WEAK",
    IndicatorType.ELIGIBILITY_REVERSAL: "MATERIAL",
    IndicatorType.REVALIDATION_FAILURE: "MATERIAL",
    IndicatorType.CONFIDENCE_WEAKENING: "WEAK",
    IndicatorType.CONTRADICTION_INCREASE: "MATERIAL",
    IndicatorType.EVIDENCE_STALE: "WEAK",
    IndicatorType.EVIDENCE_INSUFFICIENT: "WEAK",
    IndicatorType.PERFORMANCE_SIGN_REVERSAL: "MATERIAL",
    IndicatorType.COST_SENSITIVITY_WORSENING: "WEAK",
}

# Confidence level ordering for comparison
_CONFIDENCE_ORDER = {
    "INSUFFICIENT": 0,
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class DecayIndicator:
    """A single deterministic decay indicator."""
    indicator_type: IndicatorType
    current_value: Optional[float]
    baseline_value: Optional[float]
    delta: Optional[float]
    threshold: float
    severity: str  # WEAK / MATERIAL
    evidence_refs: List[Dict[str, Any]] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["indicator_type"] = self.indicator_type.value
        return d


@dataclass
class LifecycleObservation:
    """Complete lifecycle observation for a strategy."""
    observation_id: str
    strategy_id: str
    ticker: str
    strategy_name: str
    observed_at: str

    registry_state: str
    evidence_health: EvidenceHealth
    decay_state: DecayState
    revalidation_state: RevalidationState
    recommendation: Recommendation

    indicators: List[Dict[str, Any]] = field(default_factory=list)
    indicator_summary: Dict[str, Any] = field(default_factory=dict)

    evidence_refs: List[Dict[str, Any]] = field(default_factory=list)
    knowledge_refs: List[Dict[str, Any]] = field(default_factory=list)
    experiment_refs: List[Dict[str, Any]] = field(default_factory=list)

    baseline_provenance: str = ""
    evidence_class: str = ""

    policy_version: str = DECAY_POLICY_VERSION
    schema_version: str = LIFECYCLE_SCHEMA_VERSION
    revalidation_policy_version: str = REVALIDATION_POLICY_VERSION
    lifecycle_build_id: str = ""

    # Age tracking
    first_seen_at: str = ""
    last_research_validation_at: str = ""
    last_revalidation_at: str = ""
    last_supporting_evidence_at: str = ""
    last_contradicting_evidence_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence_health"] = self.evidence_health.value
        d["decay_state"] = self.decay_state.value
        d["revalidation_state"] = self.revalidation_state.value
        d["recommendation"] = self.recommendation.value
        return d


@dataclass
class LifecycleSnapshot:
    """Snapshot of a full lifecycle evaluation run."""
    lifecycle_build_id: str
    observed_at: str

    registry_strategy_count: int
    registry_states: Dict[str, int]
    experiment_families: int
    experiment_instances: int
    knowledge_findings: int
    knowledge_confidence_distribution: Dict[str, int]

    strategies_evaluated: int
    evidence_health_counts: Dict[str, int]
    revalidation_state_counts: Dict[str, int]
    decay_state_counts: Dict[str, int]
    recommendation_counts: Dict[str, int]

    strategies_insufficient_evidence: int
    strategies_decay_suspected: int
    strategies_review_recommended: int
    strategies_rotation_recommended: int

    broken_provenance_links: int

    policy_version: str = DECAY_POLICY_VERSION
    revalidation_policy_version: str = REVALIDATION_POLICY_VERSION
    observer_version: str = OBSERVER_VERSION
    schema_version: str = LIFECYCLE_SCHEMA_VERSION

    observations: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _safe_float(val: Any) -> Optional[float]:
    """Convert metric value to float, returning None for missing/invalid."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        try:
            return float(val)
        except (ValueError, TypeError):
            return None
    return None


def _compute_observation_id(strategy_id: str, build_id: str) -> str:
    """Deterministic observation identity."""
    raw = f"{strategy_id}:{build_id}"
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"lc_{h}"


def _generate_build_id() -> str:
    """Generate a deterministic lifecycle build ID."""
    now = datetime.now(timezone.utc).isoformat()
    h = hashlib.sha256(now.encode("utf-8")).hexdigest()[:12]
    return f"lb_{h}"


def _parse_timestamp(ts: str) -> Optional[datetime]:
    """Parse ISO timestamp string to datetime."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _days_since(ts_str: str) -> Optional[float]:
    """Compute days since a timestamp. None if invalid."""
    dt = _parse_timestamp(ts_str)
    if dt is None:
        return None
    now = datetime.now(timezone.utc)
    delta = now - dt
    return delta.total_seconds() / 86400.0


# ---------------------------------------------------------------------------
# Indicator Computation
# ---------------------------------------------------------------------------

def _compute_indicator(
    indicator_type: IndicatorType,
    current: Optional[float],
    baseline: Optional[float],
    threshold: float,
    evidence_refs: List[Dict[str, Any]],
    reason: str = "",
) -> Optional[DecayIndicator]:
    """Compute a single indicator. Returns None if baseline unknown."""
    if current is None or baseline is None:
        return None
    if baseline == 0:
        # Can't compute relative change from zero baseline
        return None

    if indicator_type in (IndicatorType.PF_DROP, IndicatorType.SHARPE_DROP,
                          IndicatorType.WINRATE_DROP):
        # Relative drop: (baseline - current) / |baseline|
        delta = (baseline - current) / abs(baseline)
        triggered = delta > threshold
    elif indicator_type == IndicatorType.DD_WORSENING:
        # Drawdown worsening: (current - baseline) / |baseline|
        # Positive delta means worse (higher) drawdown
        delta = (current - baseline) / abs(baseline)
        triggered = delta > threshold
    else:
        return None

    if not triggered:
        return None

    return DecayIndicator(
        indicator_type=indicator_type,
        current_value=current,
        baseline_value=baseline,
        delta=delta,
        threshold=threshold,
        severity=INDICATOR_SEVERITY.get(indicator_type, "WEAK"),
        evidence_refs=evidence_refs,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Core Evaluation Logic
# ---------------------------------------------------------------------------

def compute_decay_indicators(
    strategy_metrics: Dict[str, Any],
    baseline_metrics: Dict[str, Any],
    evidence_refs: List[Dict[str, Any]],
) -> List[DecayIndicator]:
    """Compute deterministic decay indicators from metrics vs baseline.

    Both dicts should have keys like profit_factor, sharpe_ratio,
    max_drawdown, win_rate.  Missing metrics → no indicator (unknown, never zero).
    """
    indicators: List[DecayIndicator] = []

    # PF drop
    pf_cur = _safe_float(strategy_metrics.get("profit_factor"))
    pf_base = _safe_float(baseline_metrics.get("profit_factor"))
    ind = _compute_indicator(
        IndicatorType.PF_DROP, pf_cur, pf_base, PF_DROP_THRESHOLD,
        evidence_refs, "Profit factor declined relative to baseline"
    )
    if ind:
        indicators.append(ind)

    # Sharpe drop
    sr_cur = _safe_float(strategy_metrics.get("sharpe_ratio"))
    sr_base = _safe_float(baseline_metrics.get("sharpe_ratio"))
    ind = _compute_indicator(
        IndicatorType.SHARPE_DROP, sr_cur, sr_base, SHARPE_DROP_THRESHOLD,
        evidence_refs, "Sharpe ratio declined relative to baseline"
    )
    if ind:
        indicators.append(ind)

    # DD worsening
    dd_cur = _safe_float(strategy_metrics.get("max_drawdown"))
    dd_base = _safe_float(baseline_metrics.get("max_drawdown"))
    ind = _compute_indicator(
        IndicatorType.DD_WORSENING, dd_cur, dd_base, DD_WORSENING_THRESHOLD,
        evidence_refs, "Max drawdown worsened relative to baseline"
    )
    if ind:
        indicators.append(ind)

    # Win rate drop (absolute percentage point change)
    wr_cur = _safe_float(strategy_metrics.get("win_rate"))
    wr_base = _safe_float(baseline_metrics.get("win_rate"))
    if wr_cur is not None and wr_base is not None:
        delta = wr_base - wr_cur
        if delta > WINRATE_DROP_THRESHOLD:
            indicators.append(DecayIndicator(
                indicator_type=IndicatorType.WINRATE_DROP,
                current_value=wr_cur,
                baseline_value=wr_base,
                delta=delta,
                threshold=WINRATE_DROP_THRESHOLD,
                severity="WEAK",
                evidence_refs=evidence_refs,
                reason="Win rate dropped relative to baseline",
            ))

    # Performance sign reversal
    pnl_cur = _safe_float(strategy_metrics.get("total_pnl"))
    pnl_base = _safe_float(baseline_metrics.get("total_pnl"))
    if pnl_cur is not None and pnl_base is not None:
        if pnl_base > 0 and pnl_cur < 0:
            indicators.append(DecayIndicator(
                indicator_type=IndicatorType.PERFORMANCE_SIGN_REVERSAL,
                current_value=pnl_cur,
                baseline_value=pnl_base,
                delta=pnl_cur - pnl_base,
                threshold=0.0,
                severity="MATERIAL",
                evidence_refs=evidence_refs,
                reason="Performance sign reversed: positive baseline to negative current",
            ))

    return indicators


def determine_evidence_health(
    indicators: List[DecayIndicator],
    knowledge_findings: List[Dict[str, Any]],
    evidence_count: int,
    confidence_level: str,
    is_contested: bool,
    evidence_age_days: Optional[float],
    has_baseline: bool = True,
) -> EvidenceHealth:
    """Determine evidence health from indicators and knowledge state.

    Policy:
    - No experiment observations AND insufficient knowledge → INSUFFICIENT_EVIDENCE
    - Contested findings → CONTESTED
    - 2+ independent material indicators → DECAY_SUSPECTED
    - 1 material + 1 weak → DECAY_SUSPECTED
    - 2+ weak indicators → WATCH
    - 1 weak indicator → WATCH
    - Stale evidence (no fresh evidence) → STALE
    - Otherwise → HEALTHY
    """
    if evidence_count == 0 and not has_baseline:
        return EvidenceHealth.INSUFFICIENT_EVIDENCE

    # If we have experiment observations but no knowledge findings,
    # that means the knowledge layer hasn't evaluated this strategy yet.
    # We still have experiment evidence — use indicators.
    # INSUFFICIENT only when truly no evidence at all.

    if is_contested:
        return EvidenceHealth.CONTESTED

    material_indicators = [i for i in indicators if i.severity == "MATERIAL"]
    weak_indicators = [i for i in indicators if i.severity == "WEAK"]
    # Separate stale-only indicators from other weak indicators
    stale_only = all(
        i.indicator_type == IndicatorType.EVIDENCE_STALE
        for i in weak_indicators
    ) if weak_indicators else False
    non_stale_weak = [i for i in weak_indicators if i.indicator_type != IndicatorType.EVIDENCE_STALE]

    if len(material_indicators) >= 2:
        return EvidenceHealth.DECAY_SUSPECTED

    if len(material_indicators) >= 1 and len(non_stale_weak) >= 1:
        return EvidenceHealth.DECAY_SUSPECTED

    # Only stale indicators (EVIDENCE_STALE or EVIDENCE_INSUFFICIENT) → STALE
    if stale_only and not material_indicators:
        return EvidenceHealth.STALE

    if len(non_stale_weak) >= 2:
        return EvidenceHealth.WATCH

    if len(non_stale_weak) == 1:
        return EvidenceHealth.WATCH

    # Mixed stale + other weak
    if weak_indicators and not material_indicators:
        return EvidenceHealth.WATCH

    # Check staleness as final consideration (no indicators at all)
    if evidence_age_days is not None and evidence_age_days > STALE_EVIDENCE_DAYS:
        return EvidenceHealth.STALE

    return EvidenceHealth.HEALTHY


def determine_revalidation_status(
    last_research_validation_at: str,
    last_revalidation_at: str,
    evidence_age_days: Optional[float],
    has_decay_indicators: bool,
    knowledge_confidence: str,
) -> RevalidationState:
    """Determine revalidation due status.

    Policy:
    - BLOCKED_INSUFFICIENT_CONTEXT: no validation history at all
    - OVERDUE: last validation > REVALIDATION_OVERDUE_DAYS ago
    - DUE: last validation > REVALIDATION_DUE_DAYS ago, or decay indicators present
    - NOT_DUE: recently validated
    """
    # If no validation history at all
    if not last_research_validation_at and not last_revalidation_at:
        return RevalidationState.BLOCKED_INSUFFICIENT_CONTEXT

    # Use the most recent validation timestamp
    last_val = last_revalidation_at or last_research_validation_at
    days_since = _days_since(last_val)

    if days_since is None:
        return RevalidationState.BLOCKED_INSUFFICIENT_CONTEXT

    if days_since > REVALIDATION_OVERDUE_DAYS:
        return RevalidationState.OVERDUE

    if days_since > REVALIDATION_DUE_DAYS:
        return RevalidationState.DUE

    # Even if within cadence, decay indicators trigger revalidation
    if has_decay_indicators and knowledge_confidence in ("HIGH", "MEDIUM"):
        return RevalidationState.DUE

    return RevalidationState.NOT_DUE


def make_recommendation(
    evidence_health: EvidenceHealth,
    decay_state: DecayState,
    revalidation_state: RevalidationState,
    indicators: List[DecayIndicator],
    confidence_level: str,
) -> Recommendation:
    """Generate bounded recommendation from observation state.

    Policy:
    - INSUFFICIENT_EVIDENCE + NOT_DUE → MONITOR (need more evidence)
    - INSUFFICIENT_EVIDENCE + DUE/OVERDUE → REVALIDATE
    - HEALTHY → NO_ACTION
    - WATCH → MONITOR
    - DECAY_SUSPECTED → REVIEW
    - DECAY_CONFIRMED + sufficient baseline + material decay + repeated → CONSIDER_ROTATION
    - CONTESTED → MANUAL_REVIEW_REQUIRED
    - STALE → REVALIDATE
    - OVERDUE → REVALIDATE
    """
    material_count = sum(1 for i in indicators if i.severity == "MATERIAL")

    if evidence_health == EvidenceHealth.INSUFFICIENT_EVIDENCE:
        if revalidation_state in (RevalidationState.DUE, RevalidationState.OVERDUE):
            return Recommendation.REVALIDATE
        return Recommendation.MONITOR

    if evidence_health == EvidenceHealth.CONTESTED:
        return Recommendation.MANUAL_REVIEW_REQUIRED

    if evidence_health == EvidenceHealth.STALE:
        return Recommendation.REVALIDATE

    if evidence_health == EvidenceHealth.DECAY_CONFIRMED:
        # Strongest: CONSIDER_ROTATION requires sufficient evidence
        if (confidence_level in ("HIGH", "MEDIUM")
                and material_count >= 2
                and revalidation_state == RevalidationState.OVERDUE):
            return Recommendation.CONSIDER_ROTATION
        return Recommendation.REVIEW

    if evidence_health == EvidenceHealth.DECAY_SUSPECTED:
        if revalidation_state in (RevalidationState.OVERDUE,):
            return Recommendation.REVIEW
        return Recommendation.REVIEW

    if evidence_health == EvidenceHealth.WATCH:
        return Recommendation.MONITOR

    # HEALTHY
    if revalidation_state == RevalidationState.OVERDUE:
        return Recommendation.REVALIDATE
    if revalidation_state == RevalidationState.DUE:
        return Recommendation.MONITOR

    return Recommendation.NO_ACTION


# ---------------------------------------------------------------------------
# SQLite Schema
# ---------------------------------------------------------------------------

_LIFECYCLE_SCHEMA_SQL = """
-- Strategy Lifecycle & Decay Detection schema v1.0.0 (Iteration 10)
-- Separate from experiment_memory.db, research_knowledge.db, analytics.db.

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_meta (key, value)
VALUES ('schema_version', '1.0.0'),
       ('created_at', datetime('now'));

-- Lifecycle observations: per-strategy evaluation results.
CREATE TABLE IF NOT EXISTS lifecycle_observations (
    observation_id             TEXT PRIMARY KEY,
    strategy_id                TEXT NOT NULL,
    ticker                     TEXT NOT NULL,
    strategy_name              TEXT NOT NULL,
    observed_at                TEXT NOT NULL,

    registry_state             TEXT NOT NULL,
    evidence_health            TEXT NOT NULL,
    decay_state                TEXT NOT NULL,
    revalidation_state         TEXT NOT NULL,
    recommendation             TEXT NOT NULL,

    indicators_json            TEXT NOT NULL DEFAULT '[]',
    indicator_summary_json     TEXT NOT NULL DEFAULT '{}',

    evidence_refs_json         TEXT NOT NULL DEFAULT '[]',
    knowledge_refs_json        TEXT NOT NULL DEFAULT '[]',
    experiment_refs_json       TEXT NOT NULL DEFAULT '[]',

    baseline_provenance        TEXT NOT NULL DEFAULT '',
    evidence_class             TEXT NOT NULL DEFAULT '',

    policy_version             TEXT NOT NULL DEFAULT '1.0.0',
    schema_version             TEXT NOT NULL DEFAULT '1.0.0',
    revalidation_policy_version TEXT NOT NULL DEFAULT '1.0.0',
    lifecycle_build_id         TEXT NOT NULL DEFAULT '',

    first_seen_at              TEXT NOT NULL DEFAULT '',
    last_research_validation_at TEXT NOT NULL DEFAULT '',
    last_revalidation_at       TEXT NOT NULL DEFAULT '',
    last_supporting_evidence_at TEXT NOT NULL DEFAULT '',
    last_contradicting_evidence_at TEXT NOT NULL DEFAULT ''
);

-- Lifecycle snapshots: per-build summary records.
CREATE TABLE IF NOT EXISTS lifecycle_snapshots (
    lifecycle_build_id                 TEXT PRIMARY KEY,
    observed_at                        TEXT NOT NULL,

    registry_strategy_count            INTEGER NOT NULL DEFAULT 0,
    registry_states_json               TEXT NOT NULL DEFAULT '{}',
    experiment_families                INTEGER NOT NULL DEFAULT 0,
    experiment_instances               INTEGER NOT NULL DEFAULT 0,
    knowledge_findings                 INTEGER NOT NULL DEFAULT 0,
    knowledge_confidence_distribution_json TEXT NOT NULL DEFAULT '{}',

    strategies_evaluated               INTEGER NOT NULL DEFAULT 0,
    evidence_health_counts_json        TEXT NOT NULL DEFAULT '{}',
    revalidation_state_counts_json     TEXT NOT NULL DEFAULT '{}',
    decay_state_counts_json            TEXT NOT NULL DEFAULT '{}',
    recommendation_counts_json         TEXT NOT NULL DEFAULT '{}',

    strategies_insufficient_evidence   INTEGER NOT NULL DEFAULT 0,
    strategies_decay_suspected         INTEGER NOT NULL DEFAULT 0,
    strategies_review_recommended      INTEGER NOT NULL DEFAULT 0,
    strategies_rotation_recommended    INTEGER NOT NULL DEFAULT 0,

    broken_provenance_links            INTEGER NOT NULL DEFAULT 0,

    policy_version                     TEXT NOT NULL DEFAULT '1.0.0',
    revalidation_policy_version        TEXT NOT NULL DEFAULT '1.0.0',
    observer_version                   TEXT NOT NULL DEFAULT '1.0.0',
    schema_version                     TEXT NOT NULL DEFAULT '1.0.0',

    observations_json                  TEXT NOT NULL DEFAULT '[]',
    errors_json                        TEXT NOT NULL DEFAULT '[]'
);

-- Lifecycle recommendations: material recommendation changes.
CREATE TABLE IF NOT EXISTS lifecycle_recommendations (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id         TEXT NOT NULL,
    strategy_id            TEXT NOT NULL,
    previous_recommendation TEXT,
    new_recommendation      TEXT NOT NULL,
    changed_at             TEXT NOT NULL,
    lifecycle_build_id     TEXT NOT NULL,
    reason                 TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_obs_strategy ON lifecycle_observations(strategy_id);
CREATE INDEX IF NOT EXISTS idx_obs_health ON lifecycle_observations(evidence_health);
CREATE INDEX IF NOT EXISTS idx_obs_rec ON lifecycle_observations(recommendation);
CREATE INDEX IF NOT EXISTS idx_obs_build ON lifecycle_observations(lifecycle_build_id);
CREATE INDEX IF NOT EXISTS idx_rec_strategy ON lifecycle_recommendations(strategy_id);
CREATE INDEX IF NOT EXISTS idx_snap_build ON lifecycle_snapshots(lifecycle_build_id);
"""


# ---------------------------------------------------------------------------
# StrategyLifecycleObserver
# ---------------------------------------------------------------------------

class StrategyLifecycleObserver:
    """Read-only lifecycle observer backed by SQLite.

    Reads:
      - strategy_registry.json (via StrategyRegistry or direct JSON)
      - state/experiment_memory.db (via ExperimentMemory or direct SQLite)
      - state/research_knowledge.db (via KnowledgeStore or direct SQLite)

    Writes:
      - state/strategy_lifecycle.db (lifecycle observations, snapshots, recommendations)
    """

    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = base_dir or Path(__file__).resolve().parent.parent
        self.state_dir = self.base_dir / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)

        self.lifecycle_db_path = self.state_dir / LIFECYCLE_DB_NAME
        self.registry_path = self.state_dir / "strategy_registry.json"
        self.experiment_memory_path = self.state_dir / "experiment_memory.db"
        self.knowledge_db_path = self.state_dir / "research_knowledge.db"

        self._lifecycle_conn: Optional[sqlite3.Connection] = None
        self._exp_conn: Optional[sqlite3.Connection] = None
        self._knowledge_conn: Optional[sqlite3.Connection] = None

        self._ensure_schema()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _connect_lifecycle(self) -> sqlite3.Connection:
        if self._lifecycle_conn is None:
            self._lifecycle_conn = sqlite3.connect(
                str(self.lifecycle_db_path),
                timeout=10,
                isolation_level="DEFERRED",
            )
            self._lifecycle_conn.row_factory = sqlite3.Row
            self._lifecycle_conn.execute("PRAGMA journal_mode=WAL")
            self._lifecycle_conn.execute("PRAGMA foreign_keys=ON")
        return self._lifecycle_conn

    def _connect_experiment_memory(self) -> Optional[sqlite3.Connection]:
        if self._exp_conn is not None:
            return self._exp_conn
        if not self.experiment_memory_path.exists():
            return None
        try:
            self._exp_conn = sqlite3.connect(
                str(self.experiment_memory_path),
                timeout=10,
            )
            self._exp_conn.row_factory = sqlite3.Row
            return self._exp_conn
        except Exception as e:
            logger.warning("Cannot connect to experiment_memory: %s", e)
            return None

    def _connect_knowledge(self) -> Optional[sqlite3.Connection]:
        if self._knowledge_conn is not None:
            return self._knowledge_conn
        if not self.knowledge_db_path.exists():
            return None
        try:
            self._knowledge_conn = sqlite3.connect(
                str(self.knowledge_db_path),
                timeout=10,
            )
            self._knowledge_conn.row_factory = sqlite3.Row
            return self._knowledge_conn
        except Exception as e:
            logger.warning("Cannot connect to research_knowledge: %s", e)
            return None

    def _ensure_schema(self) -> None:
        conn = self._connect_lifecycle()
        conn.executescript(_LIFECYCLE_SCHEMA_SQL)
        conn.commit()

    def close(self) -> None:
        for conn in (self._lifecycle_conn, self._exp_conn, self._knowledge_conn):
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
        self._lifecycle_conn = None
        self._exp_conn = None
        self._knowledge_conn = None

    # ------------------------------------------------------------------
    # Data Source Readers
    # ------------------------------------------------------------------

    def _read_registry(self) -> Dict[str, Any]:
        """Read the canonical strategy registry."""
        if not self.registry_path.exists():
            return {"strategies": [], "error": "REGISTRY_NOT_FOUND"}
        try:
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
            # Registry can be a dict with 'strategies' key or a list
            if isinstance(data, dict):
                strategies = data.get("strategies", data.get("entries", []))
                return {"strategies": strategies, "raw": data}
            elif isinstance(data, list):
                return {"strategies": data, "raw": {"strategies": data}}
            return {"strategies": [], "error": "REGISTRY_UNKNOWN_FORMAT"}
        except Exception as e:
            return {"strategies": [], "error": f"REGISTRY_READ_ERROR: {e}"}

    def _read_experiment_memory(self) -> Dict[str, Any]:
        """Read experiment memory stats and observations."""
        conn = self._connect_experiment_memory()
        if conn is None:
            return {
                "families": 0, "instances": 0, "observations": [],
                "error": "EXPERIMENT_MEMORY_UNAVAILABLE"
            }
        try:
            # Try to get counts
            try:
                fam_count = conn.execute(
                    "SELECT COUNT(*) as cnt FROM experiment_families"
                ).fetchone()["cnt"]
            except (sqlite3.OperationalError, AttributeError):
                fam_count = 0

            try:
                inst_count = conn.execute(
                    "SELECT COUNT(*) as cnt FROM experiment_instances"
                ).fetchone()["cnt"]
            except (sqlite3.OperationalError, AttributeError):
                inst_count = 0

            try:
                rows = conn.execute(
                    "SELECT * FROM experiment_instances ORDER BY indexed_at DESC"
                ).fetchall()
                observations = [dict(r) for r in rows]
            except (sqlite3.OperationalError, AttributeError):
                observations = []

            return {
                "families": fam_count,
                "instances": inst_count,
                "observations": observations,
            }
        except Exception as e:
            return {
                "families": 0, "instances": 0, "observations": [],
                "error": f"EXPERIMENT_MEMORY_ERROR: {e}"
            }

    def _read_knowledge(self) -> Dict[str, Any]:
        """Read research knowledge findings and confidence."""
        conn = self._connect_knowledge()
        if conn is None:
            return {
                "findings": [], "confidence_distribution": {},
                "error": "KNOWLEDGE_UNAVAILABLE"
            }
        try:
            try:
                rows = conn.execute(
                    "SELECT * FROM research_findings ORDER BY last_updated_at DESC"
                ).fetchall()
                findings = [dict(r) for r in rows]
            except (sqlite3.OperationalError, AttributeError):
                findings = []

            # Confidence distribution
            conf_dist: Dict[str, int] = {}
            for f in findings:
                c = f.get("confidence", "UNKNOWN")
                conf_dist[c] = conf_dist.get(c, 0) + 1

            return {
                "findings": findings,
                "confidence_distribution": conf_dist,
            }
        except Exception as e:
            return {
                "findings": [], "confidence_distribution": {},
                "error": f"KNOWLEDGE_ERROR: {e}"
            }

    # ------------------------------------------------------------------
    # Baseline Computation
    # ------------------------------------------------------------------

    def _compute_baseline(
        self,
        strategy_id: str,
        strategy_name: str,
        ticker: str,
        experiment_observations: List[Dict[str, Any]],
        knowledge_findings: List[Dict[str, Any]],
    ) -> Tuple[Dict[str, Any], str]:
        """Compute baseline metrics from historical validated evidence.

        Returns (baseline_metrics_dict, provenance_string).
        Baseline is derived from the most recent validated observations
        with eligible=true and sufficient confidence.

        If no baseline can be established, returns ({}, "NO_BASELINE").
        """
        # Filter to relevant observations
        relevant = []
        for obs in experiment_observations:
            obs_strategy = obs.get("strategy", "").lower()
            obs_ticker = (obs.get("instrument", "") or obs.get("ticker", "")).upper()
            if (obs_strategy and strategy_name.lower() in obs_strategy
                    or strategy_name.lower() in obs_strategy):
                if obs_ticker and obs_ticker == ticker.upper():
                    relevant.append(obs)

        # Also try matching by config_key or strategy_id
        if not relevant:
            for obs in experiment_observations:
                ck = obs.get("config_key", "")
                if strategy_id in ck or strategy_name.lower() in ck.lower():
                    relevant.append(obs)

        if not relevant:
            return {}, "NO_BASELINE_NO_OBSERVATIONS"

        # Filter to eligible observations only for baseline
        eligible = [o for o in relevant if o.get("eligible")]
        if not eligible:
            eligible = relevant  # Use all if none eligible

        # Sort by most recent
        eligible.sort(key=lambda o: o.get("indexed_at", ""), reverse=True)

        # Aggregate metrics from eligible observations
        all_metrics: Dict[str, List[float]] = {}
        for obs in eligible:
            try:
                metrics = json.loads(obs.get("metrics_json", "{}"))
            except (json.JSONDecodeError, TypeError):
                metrics = {}
            for key, val in metrics.items():
                fval = _safe_float(val)
                if fval is not None:
                    all_metrics.setdefault(key, []).append(fval)

        if not all_metrics:
            return {}, "NO_BASELINE_NO_METRICS"

        # Compute baseline: median of available observations
        baseline: Dict[str, Any] = {}
        for key, values in all_metrics.items():
            sorted_vals = sorted(values)
            n = len(sorted_vals)
            if n % 2 == 1:
                baseline[key] = sorted_vals[n // 2]
            else:
                baseline[key] = (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2

        provenance = (
            f"baseline_from_{len(eligible)}_eligible_observations_"
            f"{eligible[0].get('experiment_family_id', 'unknown')}"
        )

        return baseline, provenance

    # ------------------------------------------------------------------
    # Core Evaluation
    # ------------------------------------------------------------------

    def evaluate_strategy(
        self,
        strategy_record: Dict[str, Any],
        experiment_observations: List[Dict[str, Any]],
        knowledge_findings: List[Dict[str, Any]],
        build_id: str,
    ) -> LifecycleObservation:
        """Evaluate a single strategy and produce a lifecycle observation.

        This is the core deterministic evaluation function.
        No LLM, no broker, no registry mutation.
        """
        strategy_id = strategy_record.get("strategy_id", "")
        ticker = strategy_record.get("ticker", "")
        strategy_name = strategy_record.get("strategy", "")
        registry_state = strategy_record.get("status", "unknown")
        strategy_metrics = strategy_record.get("metrics", {})
        created_ts = strategy_record.get("created_ts", 0)

        now = datetime.now(timezone.utc).isoformat()

        # Compute observation ID
        obs_id = _compute_observation_id(strategy_id, build_id)

        # --- Baseline ---
        baseline_metrics, baseline_provenance = self._compute_baseline(
            strategy_id, strategy_name, ticker,
            experiment_observations, knowledge_findings,
        )

        # --- Knowledge state for this strategy ---
        strategy_knowledge = [
            f for f in knowledge_findings
            if strategy_name.lower() in f.get("subject", "").lower()
            or strategy_id in f.get("subject", "")
        ]
        has_contested = any(
            f.get("status") == "CONTESTED" for f in strategy_knowledge
        )
        confidence_level = "INSUFFICIENT"
        if strategy_knowledge:
            # Use the highest confidence among findings
            best_conf = "INSUFFICIENT"
            for f in strategy_knowledge:
                c = f.get("confidence", "INSUFFICIENT")
                if _CONFIDENCE_ORDER.get(c, 0) > _CONFIDENCE_ORDER.get(best_conf, 0):
                    best_conf = c
            confidence_level = best_conf

        # --- Experiment observation refs ---
        strategy_experiment_refs = [
            {
                "experiment_instance_id": o.get("experiment_instance_id", ""),
                "run_id": o.get("run_id", ""),
                "config_key": o.get("config_key", ""),
                "eligible": bool(o.get("eligible")),
                "classification": o.get("classification", ""),
            }
            for o in experiment_observations
            if strategy_name.lower() in (o.get("strategy", "").lower())
            or strategy_id in o.get("config_key", "")
        ]

        # --- Knowledge refs ---
        knowledge_refs = [
            {
                "finding_id": f.get("finding_id", ""),
                "status": f.get("status", ""),
                "confidence": f.get("confidence", ""),
                "finding_type": f.get("finding_type", ""),
            }
            for f in strategy_knowledge
        ]

        # --- Evidence age ---
        evidence_age_days: Optional[float] = None
        last_evidence_at = ""
        for ref in strategy_experiment_refs:
            for obs in experiment_observations:
                if obs.get("experiment_instance_id") == ref["experiment_instance_id"]:
                    idx_at = obs.get("indexed_at", "")
                    if idx_at:
                        d = _days_since(idx_at)
                        if d is not None:
                            if evidence_age_days is None or d < evidence_age_days:
                                evidence_age_days = d
                                last_evidence_at = idx_at

        # --- Compute decay indicators ---
        # Use strategy_record metrics as the "current" state.
        # Baseline comes from historical validated experiment observations.
        indicators = compute_decay_indicators(
            strategy_metrics, baseline_metrics, strategy_experiment_refs,
        )

        # --- Recovery detection ---
        # If the most recent experiment observation shows improvement
        # over the baseline, and there WERE decay indicators from the
        # strategy_record metrics, override to HEALTHY.
        # This supports the recovery path: decay → revalidation → healthy.
        recovery_detected = False
        if (experiment_observations and baseline_metrics
                and indicators):  # Only if there WERE decay indicators
            latest_obs_metrics = None
            latest_obs_idx = ""
            for obs in experiment_observations:
                obs_strategy = obs.get("strategy", "").lower()
                obs_ticker = (obs.get("instrument", "") or obs.get("ticker", "")).upper()
                if ((strategy_name.lower() in obs_strategy or strategy_id in obs.get("config_key", ""))
                        and (not obs_ticker or obs_ticker == ticker.upper())):
                    idx_at = obs.get("indexed_at", "")
                    if idx_at and (not latest_obs_idx or idx_at > latest_obs_idx):
                        latest_obs_idx = idx_at
                        try:
                            latest_obs_metrics = json.loads(obs.get("metrics_json", "{}"))
                        except (json.JSONDecodeError, TypeError):
                            pass
            if latest_obs_metrics:
                # Check if latest observation is materially different from
                # baseline AND better than baseline (recovery)
                latest_vs_baseline = compute_decay_indicators(
                    latest_obs_metrics, baseline_metrics, [],
                )
                baseline_vs_latest = compute_decay_indicators(
                    baseline_metrics, latest_obs_metrics, [],
                )
                # Recovery: latest is better than baseline AND they differ
                if not latest_vs_baseline and baseline_vs_latest:
                    recovery_detected = True

        # --- Confidence weakening indicator ---
        if strategy_knowledge:
            findings_with_baseline = [
                f for f in knowledge_findings
                if f.get("subject", "") != ""
                and f.get("confidence") != "INSUFFICIENT"
            ]
            # Check if any finding for this strategy was previously higher confidence
            # (This is a simplified check — real implementation would track history)
            for f in strategy_knowledge:
                if f.get("status") == "WEAKENED":
                    indicators.append(DecayIndicator(
                        indicator_type=IndicatorType.CONFIDENCE_WEAKENING,
                        current_value=_CONFIDENCE_ORDER.get(f.get("confidence", "INSUFFICIENT"), 0),
                        baseline_value=None,
                        delta=None,
                        threshold=CONFIDENCE_DOWNGRADE_THRESHOLD,
                        severity="WEAK",
                        evidence_refs=[{"finding_id": f.get("finding_id", "")}],
                        reason="Finding status is WEAKENED",
                    ))

        # --- Evidence insufficient indicator ---
        if len(strategy_experiment_refs) == 0 and not baseline_metrics:
            indicators.append(DecayIndicator(
                indicator_type=IndicatorType.EVIDENCE_INSUFFICIENT,
                current_value=None,
                baseline_value=None,
                delta=None,
                threshold=0,
                severity="WEAK",
                evidence_refs=[],
                reason="No experiment observations found for this strategy",
            ))

        # --- Evidence stale indicator ---
        if evidence_age_days is not None and evidence_age_days > STALE_EVIDENCE_DAYS:
            indicators.append(DecayIndicator(
                indicator_type=IndicatorType.EVIDENCE_STALE,
                current_value=evidence_age_days,
                baseline_value=float(STALE_EVIDENCE_DAYS),
                delta=evidence_age_days - STALE_EVIDENCE_DAYS,
                threshold=float(STALE_EVIDENCE_DAYS),
                severity="WEAK",
                evidence_refs=strategy_experiment_refs[:3],
                reason=f"Evidence is {evidence_age_days:.0f} days old (threshold: {STALE_EVIDENCE_DAYS})",
            ))

        # --- Evidence health ---
        has_baseline = bool(baseline_metrics)
        evidence_health = determine_evidence_health(
            indicators, strategy_knowledge,
            evidence_count=len(strategy_experiment_refs),
            confidence_level=confidence_level,
            is_contested=has_contested,
            evidence_age_days=evidence_age_days,
            has_baseline=has_baseline,
        )

        # --- Revalidation status ---
        last_research_val = ""
        last_reval = ""
        for f in strategy_knowledge:
            lu = f.get("last_updated_at", "")
            if lu and (not last_research_val or lu > last_research_val):
                last_research_val = lu

        revalidation_state = determine_revalidation_status(
            last_research_validation_at=last_research_val,
            last_revalidation_at=last_reval,
            evidence_age_days=evidence_age_days,
            has_decay_indicators=len(indicators) > 0,
            knowledge_confidence=confidence_level,
        )

        # --- Decay state ---
        if recovery_detected and evidence_health in (
            EvidenceHealth.DECAY_SUSPECTED, EvidenceHealth.WATCH,
        ):
            # Recovery detected: fresh evidence shows improvement
            decay_state = DecayState.RECOVERING
            evidence_health = EvidenceHealth.HEALTHY
        elif evidence_health in (EvidenceHealth.DECAY_CONFIRMED,):
            decay_state = DecayState.DECAYING
        elif evidence_health == EvidenceHealth.DECAY_SUSPECTED:
            decay_state = DecayState.DECAYING
        elif evidence_health == EvidenceHealth.WATCH:
            decay_state = DecayState.UNKNOWN  # Not enough to confirm or deny
        elif evidence_health == EvidenceHealth.HEALTHY:
            decay_state = DecayState.HEALTHY
        else:
            decay_state = DecayState.UNKNOWN

        # --- Recommendation ---
        recommendation = make_recommendation(
            evidence_health, decay_state, revalidation_state,
            indicators, confidence_level,
        )

        # --- Indicator summary ---
        indicator_summary = {
            "total": len(indicators),
            "material": sum(1 for i in indicators if i.severity == "MATERIAL"),
            "weak": sum(1 for i in indicators if i.severity == "WEAK"),
            "types": [i.indicator_type.value for i in indicators],
        }

        # --- Age tracking ---
        first_seen = ""
        if created_ts:
            try:
                first_seen = datetime.fromtimestamp(
                    created_ts, tz=timezone.utc
                ).isoformat()
            except (ValueError, TypeError, OSError):
                pass

        # Determine evidence class
        evidence_class = "BACKTEST"  # Default — most evidence is backtest
        for obs in experiment_observations:
            cl = obs.get("classification", "")
            if "PAPER" in cl.upper():
                evidence_class = "PAPER"
                break

        observation = LifecycleObservation(
            observation_id=obs_id,
            strategy_id=strategy_id,
            ticker=ticker,
            strategy_name=strategy_name,
            observed_at=now,
            registry_state=registry_state,
            evidence_health=evidence_health,
            decay_state=decay_state,
            revalidation_state=revalidation_state,
            recommendation=recommendation,
            indicators=[i.to_dict() for i in indicators],
            indicator_summary=indicator_summary,
            evidence_refs=strategy_experiment_refs,
            knowledge_refs=knowledge_refs,
            experiment_refs=strategy_experiment_refs,
            baseline_provenance=baseline_provenance,
            evidence_class=evidence_class,
            lifecycle_build_id=build_id,
            first_seen_at=first_seen,
            last_research_validation_at=last_research_val,
            last_revalidation_at=last_reval,
            last_supporting_evidence_at=last_evidence_at,
        )

        return observation

    # ------------------------------------------------------------------
    # Snapshot & Persistence
    # ------------------------------------------------------------------

    def build_snapshot(self) -> LifecycleSnapshot:
        """Build a full lifecycle snapshot from current state.

        Reads all sources, evaluates all strategies, persists results.
        Idempotent: same source state + same policy → same logical result.
        """
        build_id = _generate_build_id()
        now = datetime.now(timezone.utc).isoformat()

        # --- Read all sources ---
        registry_data = self._read_registry()
        strategies = registry_data.get("strategies", [])
        registry_error = registry_data.get("error", "")

        exp_data = self._read_experiment_memory()
        experiment_observations = exp_data.get("observations", [])
        exp_families = exp_data.get("families", 0)
        exp_instances = exp_data.get("instances", 0)
        exp_error = exp_data.get("error", "")

        knowledge_data = self._read_knowledge()
        knowledge_findings = knowledge_data.get("findings", [])
        confidence_dist = knowledge_data.get("confidence_distribution", {})
        knowledge_error = knowledge_data.get("error", "")

        errors = [e for e in [registry_error, exp_error, knowledge_error] if e]

        # --- Registry state counts ---
        registry_states: Dict[str, int] = {}
        for s in strategies:
            if isinstance(s, dict):
                st = s.get("status", "unknown")
            elif isinstance(s, str):
                st = "active"  # string entries are active strategy names
            else:
                st = "unknown"
            registry_states[st] = registry_states.get(st, 0) + 1

        # --- Evaluate each strategy ---
        observations: List[LifecycleObservation] = []
        for strat in strategies:
            if not isinstance(strat, dict):
                continue  # skip string-only entries
            try:
                obs = self.evaluate_strategy(
                    strat, experiment_observations,
                    knowledge_findings, build_id,
                )
                observations.append(obs)
            except Exception as e:
                errors.append(f"Error evaluating {strat.get('strategy_id', '?')}: {e}")

        # --- Persist observations ---
        conn = self._connect_lifecycle()
        for obs in observations:
            self._persist_observation(conn, obs)

        # --- Aggregate counts ---
        evidence_health_counts: Dict[str, int] = {}
        revalidation_state_counts: Dict[str, int] = {}
        decay_state_counts: Dict[str, int] = {}
        recommendation_counts: Dict[str, int] = {}

        for obs in observations:
            eh = obs.evidence_health.value
            evidence_health_counts[eh] = evidence_health_counts.get(eh, 0) + 1

            rs = obs.revalidation_state.value
            revalidation_state_counts[rs] = revalidation_state_counts.get(rs, 0) + 1

            ds = obs.decay_state.value
            decay_state_counts[ds] = decay_state_counts.get(ds, 0) + 1

            rc = obs.recommendation.value
            recommendation_counts[rc] = recommendation_counts.get(rc, 0) + 1

        # --- Build snapshot ---
        snapshot = LifecycleSnapshot(
            lifecycle_build_id=build_id,
            observed_at=now,
            registry_strategy_count=len(strategies),
            registry_states=registry_states,
            experiment_families=exp_families,
            experiment_instances=exp_instances,
            knowledge_findings=len(knowledge_findings),
            knowledge_confidence_distribution=confidence_dist,
            strategies_evaluated=len(observations),
            evidence_health_counts=evidence_health_counts,
            revalidation_state_counts=revalidation_state_counts,
            decay_state_counts=decay_state_counts,
            recommendation_counts=recommendation_counts,
            strategies_insufficient_evidence=evidence_health_counts.get(
                "INSUFFICIENT_EVIDENCE", 0
            ),
            strategies_decay_suspected=evidence_health_counts.get(
                "DECAY_SUSPECTED", 0
            ) + evidence_health_counts.get("DECAY_CONFIRMED", 0),
            strategies_review_recommended=recommendation_counts.get("REVIEW", 0),
            strategies_rotation_recommended=recommendation_counts.get(
                "CONSIDER_ROTATION", 0
            ),
            broken_provenance_links=0,
            observations=[o.to_dict() for o in observations],
            errors=errors,
        )

        # --- Persist snapshot ---
        self._persist_snapshot(conn, snapshot)
        conn.commit()

        return snapshot

    def _persist_observation(
        self, conn: sqlite3.Connection, obs: LifecycleObservation,
    ) -> None:
        """Persist a lifecycle observation to the database."""
        conn.execute(
            """INSERT OR REPLACE INTO lifecycle_observations
               (observation_id, strategy_id, ticker, strategy_name, observed_at,
                registry_state, evidence_health, decay_state, revalidation_state,
                recommendation, indicators_json, indicator_summary_json,
                evidence_refs_json, knowledge_refs_json, experiment_refs_json,
                baseline_provenance, evidence_class,
                policy_version, schema_version, revalidation_policy_version,
                lifecycle_build_id,
                first_seen_at, last_research_validation_at, last_revalidation_at,
                last_supporting_evidence_at, last_contradicting_evidence_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                obs.observation_id, obs.strategy_id, obs.ticker,
                obs.strategy_name, obs.observed_at,
                obs.registry_state, obs.evidence_health.value,
                obs.decay_state.value, obs.revalidation_state.value,
                obs.recommendation.value,
                json.dumps(obs.indicators, ensure_ascii=False),
                json.dumps(obs.indicator_summary, ensure_ascii=False),
                json.dumps(obs.evidence_refs, ensure_ascii=False),
                json.dumps(obs.knowledge_refs, ensure_ascii=False),
                json.dumps(obs.experiment_refs, ensure_ascii=False),
                obs.baseline_provenance, obs.evidence_class,
                obs.policy_version, obs.schema_version,
                obs.revalidation_policy_version, obs.lifecycle_build_id,
                obs.first_seen_at, obs.last_research_validation_at,
                obs.last_revalidation_at, obs.last_supporting_evidence_at,
                obs.last_contradicting_evidence_at,
            ),
        )

    def _persist_snapshot(
        self, conn: sqlite3.Connection, snapshot: LifecycleSnapshot,
    ) -> None:
        """Persist a lifecycle snapshot to the database."""
        conn.execute(
            """INSERT OR REPLACE INTO lifecycle_snapshots
               (lifecycle_build_id, observed_at,
                registry_strategy_count, registry_states_json,
                experiment_families, experiment_instances,
                knowledge_findings, knowledge_confidence_distribution_json,
                strategies_evaluated, evidence_health_counts_json,
                revalidation_state_counts_json, decay_state_counts_json,
                recommendation_counts_json,
                strategies_insufficient_evidence, strategies_decay_suspected,
                strategies_review_recommended, strategies_rotation_recommended,
                broken_provenance_links,
                policy_version, revalidation_policy_version,
                observer_version, schema_version,
                observations_json, errors_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot.lifecycle_build_id, snapshot.observed_at,
                snapshot.registry_strategy_count,
                json.dumps(snapshot.registry_states, ensure_ascii=False),
                snapshot.experiment_families, snapshot.experiment_instances,
                snapshot.knowledge_findings,
                json.dumps(snapshot.knowledge_confidence_distribution, ensure_ascii=False),
                snapshot.strategies_evaluated,
                json.dumps(snapshot.evidence_health_counts, ensure_ascii=False),
                json.dumps(snapshot.revalidation_state_counts, ensure_ascii=False),
                json.dumps(snapshot.decay_state_counts, ensure_ascii=False),
                json.dumps(snapshot.recommendation_counts, ensure_ascii=False),
                snapshot.strategies_insufficient_evidence,
                snapshot.strategies_decay_suspected,
                snapshot.strategies_review_recommended,
                snapshot.strategies_rotation_recommended,
                snapshot.broken_provenance_links,
                snapshot.policy_version, snapshot.revalidation_policy_version,
                snapshot.observer_version, snapshot.schema_version,
                json.dumps(snapshot.observations, ensure_ascii=False),
                json.dumps(snapshot.errors, ensure_ascii=False),
            ),
        )

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def get_latest_snapshot(self) -> Optional[Dict[str, Any]]:
        """Get the most recent lifecycle snapshot."""
        conn = self._connect_lifecycle()
        row = conn.execute(
            "SELECT * FROM lifecycle_snapshots ORDER BY observed_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def get_strategy_observation(
        self, strategy_id: str, build_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get lifecycle observation for a specific strategy."""
        conn = self._connect_lifecycle()
        if build_id:
            row = conn.execute(
                "SELECT * FROM lifecycle_observations WHERE strategy_id = ? AND lifecycle_build_id = ?",
                (strategy_id, build_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM lifecycle_observations WHERE strategy_id = ? ORDER BY observed_at DESC LIMIT 1",
                (strategy_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_strategies_by_health(
        self, evidence_health: str,
    ) -> List[Dict[str, Any]]:
        """Get all strategies with a specific evidence health state."""
        conn = self._connect_lifecycle()
        rows = conn.execute(
            """SELECT * FROM lifecycle_observations
               WHERE evidence_health = ?
               AND observation_id IN (
                   SELECT observation_id FROM lifecycle_observations
                   WHERE strategy_id = lifecycle_observations.strategy_id
                   ORDER BY observed_at DESC LIMIT 1
               )
               ORDER BY strategy_id""",
            (evidence_health,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_strategies_by_recommendation(
        self, recommendation: str,
    ) -> List[Dict[str, Any]]:
        """Get all strategies with a specific recommendation."""
        conn = self._connect_lifecycle()
        rows = conn.execute(
            """SELECT * FROM lifecycle_observations
               WHERE recommendation = ?
               AND observation_id IN (
                   SELECT observation_id FROM lifecycle_observations
                   WHERE strategy_id = lifecycle_observations.strategy_id
                   ORDER BY observed_at DESC LIMIT 1
               )
               ORDER BY strategy_id""",
            (recommendation,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_recommendation_history(
        self, strategy_id: str,
    ) -> List[Dict[str, Any]]:
        """Get recommendation change history for a strategy."""
        conn = self._connect_lifecycle()
        rows = conn.execute(
            """SELECT * FROM lifecycle_recommendations
               WHERE strategy_id = ?
               ORDER BY changed_at ASC""",
            (strategy_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_observations(
        self, build_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get all observations, optionally filtered by build."""
        conn = self._connect_lifecycle()
        if build_id:
            rows = conn.execute(
                "SELECT * FROM lifecycle_observations WHERE lifecycle_build_id = ? ORDER BY strategy_id",
                (build_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM lifecycle_observations
                   WHERE observation_id IN (
                       SELECT observation_id FROM lifecycle_observations
                       WHERE strategy_id = lifecycle_observations.strategy_id
                       ORDER BY observed_at DESC LIMIT 1
                   )
                   ORDER BY strategy_id""",
            ).fetchall()
        return [dict(r) for r in rows]

    def get_snapshot_history(self) -> List[Dict[str, Any]]:
        """Get all lifecycle snapshot history."""
        conn = self._connect_lifecycle()
        rows = conn.execute(
            "SELECT * FROM lifecycle_snapshots ORDER BY observed_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def summary(self) -> Dict[str, Any]:
        """Generate a derived summary report (not source of truth)."""
        conn = self._connect_lifecycle()
        try:
            total_obs = conn.execute(
                "SELECT COUNT(*) as cnt FROM lifecycle_observations"
            ).fetchone()["cnt"]
        except (sqlite3.OperationalError, AttributeError):
            total_obs = 0

        try:
            total_snaps = conn.execute(
                "SELECT COUNT(*) as cnt FROM lifecycle_snapshots"
            ).fetchone()["cnt"]
        except (sqlite3.OperationalError, AttributeError):
            total_snaps = 0

        try:
            health_rows = conn.execute(
                "SELECT evidence_health, COUNT(*) as cnt FROM lifecycle_observations GROUP BY evidence_health"
            ).fetchall()
            health_dist = {r["evidence_health"]: r["cnt"] for r in health_rows}
        except (sqlite3.OperationalError, AttributeError):
            health_dist = {}

        try:
            rec_rows = conn.execute(
                "SELECT recommendation, COUNT(*) as cnt FROM lifecycle_observations GROUP BY recommendation"
            ).fetchall()
            rec_dist = {r["recommendation"]: r["cnt"] for r in rec_rows}
        except (sqlite3.OperationalError, AttributeError):
            rec_dist = {}

        return {
            "schema_version": LIFECYCLE_SCHEMA_VERSION,
            "observer_version": OBSERVER_VERSION,
            "total_observations": total_obs,
            "total_snapshots": total_snaps,
            "evidence_health_distribution": health_dist,
            "recommendation_distribution": rec_dist,
        }

    def write_report(self, output_dir: Path) -> Dict[str, str]:
        """Write human-readable derived lifecycle report."""
        output_dir.mkdir(parents=True, exist_ok=True)
        summary = self.summary()
        latest = self.get_latest_snapshot()

        md_lines = [
            "# Strategy Lifecycle Report",
            f"\n**Generated:** {datetime.now(timezone.utc).isoformat()}",
            f"**Schema version:** {summary['schema_version']}",
            f"**Observer version:** {summary['observer_version']}",
            "",
            "## Summary",
            "",
            f"- **Total observations:** {summary['total_observations']}",
            f"- **Total snapshots:** {summary['total_snapshots']}",
            "",
        ]

        if latest:
            md_lines.extend([
                "## Latest Snapshot",
                "",
                f"- **Build ID:** {latest.get('lifecycle_build_id', '')}",
                f"- **Observed at:** {latest.get('observed_at', '')}",
                f"- **Registry strategies:** {latest.get('registry_strategy_count', 0)}",
                f"- **Strategies evaluated:** {latest.get('strategies_evaluated', 0)}",
                "",
                "### Evidence Health",
                "",
            ])
            for health, count in summary.get("evidence_health_distribution", {}).items():
                md_lines.append(f"- {health}: {count}")

            md_lines.extend(["", "### Recommendations", ""])
            for rec, count in summary.get("recommendation_distribution", {}).items():
                md_lines.append(f"- {rec}: {count}")

        md_content = "\n".join(md_lines)
        md_path = output_dir / "latest.md"
        md_path.write_text(md_content, encoding="utf-8")

        json_path = output_dir / "latest.json"
        json_path.write_text(
            json.dumps({
                "summary": summary,
                "latest_snapshot": latest,
            }, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        return {"md_path": str(md_path), "json_path": str(json_path)}

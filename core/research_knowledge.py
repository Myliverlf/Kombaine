"""Research Knowledge Layer Foundation — Iteration 09.

Converts groups of experiment observations into evidence-backed findings,
confidence, contradictions, and unresolved questions.  CLASS 1 only:
research intelligence / knowledge infrastructure.

HARD BOUNDARY: Knowledge ≠ Policy ≠ Promotion ≠ Trading Signal.
Findings are read-only observations.  They cannot mutate registry,
novelty policy, or trading.

Deterministic distillation from Experiment Memory (no LLM opinion as
source of truth).  Evidence chain: finding → observations →
instance/family → run_id/config_key → bundle.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
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

KNOWLEDGE_SCHEMA_VERSION = "1.0.0"
DISTILLER_VERSION = "1.0.0"
KNOWLEDGE_DB_NAME = "research_knowledge.db"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class FindingType(str, Enum):
    """Taxonomy of research finding types."""
    PERFORMANCE = "PERFORMANCE"
    ROBUSTNESS = "ROBUSTNESS"
    FAILURE_PATTERN = "FAILURE_PATTERN"
    COST_SENSITIVITY = "COST_SENSITIVITY"
    DATA_SENSITIVITY = "DATA_SENSITIVITY"
    REVALIDATION_TREND = "REVALIDATION_TREND"
    CONTRADICTION = "CONTRADICTION"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class FindingStatus(str, Enum):
    """Lifecycle status of a research finding."""
    ACTIVE = "ACTIVE"
    CONTESTED = "CONTESTED"
    WEAKENED = "WEAKENED"
    SUPERSEDED = "SUPERSEDED"
    RETRACTED = "RETRACTED"
    INSUFFICIENT = "INSUFFICIENT"


class ConfidenceLevel(str, Enum):
    """Confidence states for research findings."""
    INSUFFICIENT = "INSUFFICIENT"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


# Frozensets for validation
FINDING_TYPES = frozenset(ft.value for ft in FindingType)
FINDING_STATUSES = frozenset(fs.value for fs in FindingStatus)
CONFIDENCE_LEVELS = frozenset(cl.value for cl in ConfidenceLevel)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class EvidenceRef:
    """Reference to a canonical observation in experiment memory."""
    observation_id: str          # experiment_instance_id
    run_id: str
    config_key: str
    experiment_family_id: str
    classification: str
    status: str
    eligible: bool
    metrics_json: str = "{}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EvidenceRef":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class ResearchFinding:
    """Canonical research finding — an evidence-backed statement
    synthesized from one or more observations.

    Every finding has:
    - deterministic identity (finding_id)
    - scope (what it applies to)
    - evidence chain (observation refs)
    - confidence with explicit basis
    - status (lifecycle)
    - build provenance
    """
    finding_id: str
    finding_type: FindingType
    subject: str                 # e.g. "sma_cross" or "sma_cross:SBER:1h"
    scope: Dict[str, Any]       # strategy_family, instrument, timeframe, etc.
    statement: str              # machine-readable + human-auditable

    status: FindingStatus = FindingStatus.ACTIVE
    confidence: ConfidenceLevel = ConfidenceLevel.INSUFFICIENT
    confidence_basis: str = ""   # explicit explanation of why this confidence

    evidence_refs: List[Dict[str, Any]] = field(default_factory=list)
    supporting_observations: int = 0
    contradicting_observations: int = 0

    first_observed_at: str = ""
    last_updated_at: str = ""
    last_evidence_at: str = ""

    distiller_version: str = DISTILLER_VERSION
    schema_version: str = KNOWLEDGE_SCHEMA_VERSION
    knowledge_build_id: str = ""

    # Open questions related to this finding
    open_questions: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["finding_type"] = self.finding_type.value
        d["status"] = self.status.value
        d["confidence"] = self.confidence.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ResearchFinding":
        dd = dict(d)
        dd["finding_type"] = FindingType(dd["finding_type"]) if isinstance(dd["finding_type"], str) else dd["finding_type"]
        dd["status"] = FindingStatus(dd["status"]) if isinstance(dd["status"], str) else dd["status"]
        dd["confidence"] = ConfidenceLevel(dd["confidence"]) if isinstance(dd["confidence"], str) else dd["confidence"]
        return cls(**{k: v for k, v in dd.items() if k in cls.__dataclass_fields__})


@dataclass
class OpenQuestion:
    """Structured unresolved knowledge gap."""
    question_id: str
    subject: str
    reason: str
    evidence_refs: List[Dict[str, Any]] = field(default_factory=list)
    priority_hint: str = ""
    created_at: str = ""
    status: str = "OPEN"   # OPEN / RESOLVED / SUPERSDED

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "OpenQuestion":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class KnowledgeBuild:
    """Provenance record for a distillation execution."""
    knowledge_build_id: str
    started_at: str
    completed_at: str
    distiller_version: str
    source_memory_version: str
    findings_created: int
    findings_updated: int
    findings_unchanged: int
    open_questions_created: int
    errors: List[str] = field(default_factory=list)
    status: str = "COMPLETED"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "OpenQuestion":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Finding identity (deterministic)
# ---------------------------------------------------------------------------

def compute_finding_id(
    finding_type: FindingType,
    subject: str,
    scope: Dict[str, Any],
    methodology_version: str = "default",
) -> str:
    """Deterministic finding identity based on type + subject + scope."""
    identity_blob = {
        "finding_type": finding_type.value,
        "subject": subject,
        "scope": {k: scope[k] for k in sorted(scope.keys())} if scope else {},
        "methodology_version": methodology_version,
    }
    raw = json.dumps(identity_blob, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"find_{h}"


def compute_question_id(subject: str, reason: str, scope: Dict[str, Any]) -> str:
    """Deterministic open question identity."""
    blob = {"subject": subject, "reason": reason,
            "scope": {k: scope[k] for k in sorted(scope.keys())} if scope else {}}
    raw = json.dumps(blob, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"q_{h}"


# ---------------------------------------------------------------------------
# Comparability Gate
# ---------------------------------------------------------------------------

# Fields that must match for two observations to be aggregated.
_COMPARABLE_DIMENSIONS = [
    "strategy",
    "instrument",
    "timeframe",
    "validation_version",
    "cost_model_hash",
]


def check_comparability(
    obs_a: Dict[str, Any],
    obs_b: Dict[str, Any],
    extra_dimensions: Optional[List[str]] = None,
) -> Tuple[bool, List[str]]:
    """Check if two observations are meaningfully comparable.

    Returns (is_comparable, list_of_differences).
    If materially incompatible → do not average together.
    """
    dimensions = list(_COMPARABLE_DIMENSIONS)
    if extra_dimensions:
        dimensions.extend(extra_dimensions)

    differences: List[str] = []
    for dim in dimensions:
        va = str(obs_a.get(dim, "")).strip().lower()
        vb = str(obs_b.get(dim, "")).strip().lower()
        if va != vb:
            differences.append(dim)

    # Also compare parameters (normalized)
    params_a = obs_a.get("parameters_json", "{}")
    params_b = obs_b.get("parameters_json", "{}")
    try:
        pa = json.loads(params_a) if isinstance(params_a, str) else params_a
        pb = json.loads(params_b) if isinstance(params_b, str) else params_b
    except (json.JSONDecodeError, TypeError):
        pa, pb = {}, {}
    if pa != pb:
        differences.append("parameters")

    return (len(differences) == 0, differences)


def group_comparable_observations(
    observations: List[Dict[str, Any]],
) -> List[List[Dict[str, Any]]]:
    """Group observations into comparable clusters.

    Two observations are in the same group if they are pairwise
    comparable on all standard dimensions.  Uses union-find for
    transitive closure.
    """
    if not observations:
        return []

    n = len(observations)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    for i in range(n):
        for j in range(i + 1, n):
            comparable, _ = check_comparability(observations[i], observations[j])
            if comparable:
                union(i, j)

    groups: Dict[int, List[Dict[str, Any]]] = {}
    for i in range(n):
        root = find(i)
        groups.setdefault(root, []).append(observations[i])

    return list(groups.values())


# ---------------------------------------------------------------------------
# Confidence Model
# ---------------------------------------------------------------------------

def compute_confidence(
    evidence_count: int,
    independent_revalidations: int,
    unique_instruments: int,
    consistency_ratio: float,   # fraction of observations with consistent direction
    total_trades: int,
    contradiction_count: int,
    methodology_versions: int,
) -> Tuple[ConfidenceLevel, str]:
    """Compute confidence level from evidence dimensions.

    Returns (level, basis_text).  The basis is always explicit.

    Thresholds are intentionally conservative:
    - INSUFFICIENT: < 2 comparable observations
    - LOW: 2-3 observations, low consistency or few trades
    - MEDIUM: 4+ observations, moderate consistency, some trades
    - HIGH: 6+ observations, high consistency, many trades, multiple revalidations

    Contradictions reduce confidence.
    """
    basis_parts: List[str] = []

    # --- INSUFFICIENT: not enough evidence ---
    if evidence_count < 2:
        basis_parts.append(f"only {evidence_count} observation(s); minimum 2 required")
        return ConfidenceLevel.INSUFFICIENT, "; ".join(basis_parts)

    basis_parts.append(f"{evidence_count} observations")

    # --- Compute base level ---
    level = ConfidenceLevel.LOW

    if evidence_count >= 6 and total_trades >= 100 and consistency_ratio >= 0.7:
        level = ConfidenceLevel.HIGH
        basis_parts.append(f"{total_trades} total trades")
        basis_parts.append(f"consistency {consistency_ratio:.0%}")
    elif evidence_count >= 4 and total_trades >= 30:
        level = ConfidenceLevel.MEDIUM
        basis_parts.append(f"{total_trades} total trades")
        basis_parts.append(f"consistency {consistency_ratio:.0%}")
    else:
        basis_parts.append(f"consistency {consistency_ratio:.0%}")

    # --- Adjustments ---
    if independent_revalidations >= 2:
        basis_parts.append(f"{independent_revalidations} revalidations")
    else:
        if level == ConfidenceLevel.HIGH:
            level = ConfidenceLevel.MEDIUM
            basis_parts.append(f"only {independent_revalidations} revalidation(s); capped at MEDIUM")

    if unique_instruments > 1:
        basis_parts.append(f"{unique_instruments} instruments")

    if contradiction_count > 0:
        # Contradictions reduce confidence
        level_map = {
            ConfidenceLevel.HIGH: ConfidenceLevel.MEDIUM,
            ConfidenceLevel.MEDIUM: ConfidenceLevel.LOW,
            ConfidenceLevel.LOW: ConfidenceLevel.LOW,
            ConfidenceLevel.INSUFFICIENT: ConfidenceLevel.INSUFFICIENT,
        }
        level = level_map.get(level, ConfidenceLevel.LOW)
        basis_parts.append(f"{contradiction_count} contradicting observation(s)")

    if methodology_versions > 1:
        basis_parts.append(f"{methodology_versions} methodology versions")

    return level, "; ".join(basis_parts)


# ---------------------------------------------------------------------------
# Deterministic Distiller
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


def _compute_directional_consistency(
    observations: List[Dict[str, Any]],
    metric_key: str = "total_pnl",
) -> Tuple[float, int, int, int]:
    """Compute directional consistency for a metric across observations.

    Returns (consistency_ratio, positive_count, negative_count, total_count).
    Missing metrics are excluded from the count.
    """
    positive = 0
    negative = 0
    total = 0
    for obs in observations:
        metrics = {}
        try:
            metrics = json.loads(obs.get("metrics_json", "{}"))
        except (json.JSONDecodeError, TypeError):
            pass
        val = _safe_float(metrics.get(metric_key))
        if val is not None:
            total += 1
            if val > 0:
                positive += 1
            elif val < 0:
                negative += 1
    if total == 0:
        return 0.0, 0, 0, 0
    consistency = max(positive, negative) / total
    return consistency, positive, negative, total


def _count_unique_instruments(observations: List[Dict[str, Any]]) -> int:
    instruments = set()
    for obs in observations:
        inst = obs.get("instrument", "")
        if inst:
            instruments.add(inst.upper())
    return len(instruments)


def _count_methodology_versions(observations: List[Dict[str, Any]]) -> int:
    versions = set()
    for obs in observations:
        v = obs.get("validation_version", "")
        if v:
            versions.add(v)
    return len(versions)


def _aggregate_metrics(observations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate metrics across comparable observations.

    Returns summary statistics.  Missing metrics remain absent (not zero).
    Duplicate reproductions are counted but weighted carefully.
    """
    all_metrics: Dict[str, List[float]] = {}
    missing_keys: Dict[str, int] = {}
    total_count = 0

    for obs in observations:
        try:
            metrics = json.loads(obs.get("metrics_json", "{}"))
        except (json.JSONDecodeError, TypeError):
            metrics = {}
        total_count += 1
        for key, val in metrics.items():
            fval = _safe_float(val)
            if fval is not None:
                all_metrics.setdefault(key, []).append(fval)
            else:
                missing_keys[key] = missing_keys.get(key, 0) + 1

    summary: Dict[str, Any] = {
        "observation_count": total_count,
        "metrics_present": list(all_metrics.keys()),
        "metrics_missing": missing_keys,
    }

    for key, values in all_metrics.items():
        if not values:
            continue
        summary[f"{key}_min"] = min(values)
        summary[f"{key}_max"] = max(values)
        summary[f"{key}_mean"] = sum(values) / len(values)
        summary[f"{key}_median"] = sorted(values)[len(values) // 2]
        summary[f"{key}_count"] = len(values)

    return summary


def _count_exact_duplicates(observations: List[Dict[str, Any]]) -> int:
    """Count how many observations share the same instance_id.

    Exact duplicates verify reproducibility but should not be
    counted as independent evidence for confidence.
    """
    seen: Dict[str, int] = {}
    for obs in observations:
        iid = obs.get("experiment_instance_id", "")
        if iid:
            seen[iid] = seen.get(iid, 0) + 1
    duplicates = sum(1 for cnt in seen.values() if cnt > 1)
    return duplicates


def _determine_finding_type(
    observations: List[Dict[str, Any]],
    metric_summary: Dict[str, Any],
    has_eligible: bool,
    has_rejected: bool,
) -> FindingType:
    """Determine the finding type from observation characteristics."""
    eligible_count = sum(1 for o in observations if o.get("eligible"))
    rejected_count = sum(1 for o in observations if not o.get("eligible"))

    # If evidence is insufficient for any conclusion
    if len(observations) < 1:
        return FindingType.INSUFFICIENT_EVIDENCE

    # Contradiction: mix of positive and negative results
    _, positive, negative, total = _compute_directional_consistency(observations)
    if positive > 0 and negative > 0 and total >= 3:
        contradiction_ratio = min(positive, negative) / total
        if contradiction_ratio >= 0.3:
            return FindingType.CONTRADICTION

    # If all evidence is insufficient for meaningful conclusion
    total_trades = metric_summary.get("trade_count_mean", 0)
    if total_trades and total_trades < 5:
        return FindingType.INSUFFICIENT_EVIDENCE

    # Check revalidation trend
    classifications = [o.get("classification", "") for o in observations]
    if "REVALIDATION" in classifications and len(observations) >= 3:
        return FindingType.REVALIDATION_TREND

    # Check for robustness (multiple instruments)
    unique_instruments = _count_unique_instruments(observations)
    if unique_instruments > 1:
        return FindingType.ROBUSTNESS

    # Check cost sensitivity
    cost_hashes = set(o.get("cost_model_hash", "") for o in observations)
    if len(cost_hashes) > 1:
        return FindingType.COST_SENSITIVITY

    # Check data sensitivity
    dataset_hashes = set(o.get("dataset_hash", "") for o in observations)
    if len(dataset_hashes) > 1:
        return FindingType.DATA_SENSITIVITY

    # Default: performance
    if has_eligible:
        return FindingType.PERFORMANCE
    elif has_rejected:
        return FindingType.FAILURE_PATTERN

    return FindingType.INSUFFICIENT_EVIDENCE


def _build_scope(observations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build explicit scope from observations."""
    instruments = set()
    timeframes = set()
    strategies = set()
    for obs in observations:
        inst = obs.get("instrument", "")
        if inst:
            instruments.add(inst.upper())
        tf = obs.get("timeframe", "")
        if tf:
            timeframes.add(tf.lower())
        strat = obs.get("strategy", "")
        if strat:
            strategies.add(strat.lower())

    return {
        "instruments": sorted(instruments),
        "timeframes": sorted(timeframes),
        "strategies": sorted(strategies),
    }


def _build_statement(
    finding_type: FindingType,
    scope: Dict[str, Any],
    metric_summary: Dict[str, Any],
    consistency_ratio: float,
    evidence_count: int,
    total_trades: int,
) -> str:
    """Build machine-readable + human-auditable statement."""
    instruments = ", ".join(scope.get("instruments", []))
    strategies = ", ".join(scope.get("strategies", []))
    timeframes = ", ".join(scope.get("timeframes", []))

    pf_mean = metric_summary.get("profit_factor_mean")
    pf_str = f"median PF {pf_mean:.2f}" if pf_mean is not None else "PF unknown"
    pnl_mean = metric_summary.get("total_pnl_mean")
    pnl_str = f"mean PnL {pnl_mean:.0f}" if pnl_mean is not None else "PnL unknown"

    if finding_type == FindingType.CONTRADICTION:
        return (
            f"Mixed evidence for {strategies} on {instruments} [{timeframes}]: "
            f"{evidence_count} comparable observations, consistency {consistency_ratio:.0%}. "
            f"Evidence points in opposing directions."
        )
    elif finding_type == FindingType.INSUFFICIENT_EVIDENCE:
        return (
            f"Insufficient evidence for {strategies} on {instruments} [{timeframes}]: "
            f"only {evidence_count} observation(s); cannot draw conclusion."
        )
    elif finding_type == FindingType.REVALIDATION_TREND:
        return (
            f"Revalidation trend for {strategies} on {instruments} [{timeframes}]: "
            f"{evidence_count} observations including revalidations, "
            f"consistency {consistency_ratio:.0%}, {pf_str}, {pnl_str}."
        )
    elif finding_type == FindingType.FAILURE_PATTERN:
        return (
            f"Failure pattern for {strategies} on {instruments} [{timeframes}]: "
            f"all {evidence_count} observations were rejected, "
            f"consistency {consistency_ratio:.0%}."
        )
    else:
        return (
            f"Performance finding for {strategies} on {instruments} [{timeframes}]: "
            f"{evidence_count} comparable observations, "
            f"{total_trades} total trades, consistency {consistency_ratio:.0%}, "
            f"{pf_str}, {pnl_str}."
        )


def distill_findings(
    experiment_memory_conn: Optional[sqlite3.Connection] = None,
    experiment_memory_path: Optional[Path] = None,
    knowledge_store: Optional["KnowledgeStore"] = None,
    knowledge_build_id: str = "",
) -> KnowledgeBuild:
    """Deterministic distillation from Experiment Memory.

    Queries structured Experiment Memory, aggregates comparable
    observations, computes summary statistics, evaluates finding
    rules, and persists findings + evidence refs.

    This is the core distiller: no LLM involvement in finding creation.
    """
    started_at = datetime.now(timezone.utc).isoformat()
    build_id = knowledge_build_id or _generate_build_id(started_at)

    errors: List[str] = []
    findings_created = 0
    findings_updated = 0
    findings_unchanged = 0
    questions_created = 0

    # Connect to experiment memory
    if experiment_memory_conn is not None:
        conn = experiment_memory_conn
        close_conn = False
    elif experiment_memory_path is not None:
        conn = sqlite3.connect(str(experiment_memory_path), timeout=10)
        conn.row_factory = sqlite3.Row
        close_conn = True
    else:
        # Default path
        db_path = Path("state") / "experiment_memory.db"
        if not db_path.exists():
            errors.append(f"Experiment Memory DB not found: {db_path}")
            return KnowledgeBuild(
                knowledge_build_id=build_id,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
                distiller_version=DISTILLER_VERSION,
                source_memory_version="unknown",
                findings_created=0,
                findings_updated=0,
                findings_unchanged=0,
                open_questions_created=0,
                errors=errors,
                status="FAILED_NO_MEMORY",
            )
        conn = sqlite3.connect(str(db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        close_conn = True

    try:
        # Get all instances (handle missing tables / corrupt DB)
        try:
            rows = conn.execute(
                "SELECT * FROM experiment_instances ORDER BY indexed_at ASC"
            ).fetchall()
            observations = [dict(r) for r in rows]
        except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
            errors.append(f"Failed to query experiment_instances: {e}")
            return KnowledgeBuild(
                knowledge_build_id=build_id,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
                distiller_version=DISTILLER_VERSION,
                source_memory_version="unknown",
                findings_created=0,
                findings_updated=0,
                findings_unchanged=0,
                open_questions_created=0,
                errors=errors,
                status="FAILED_MEMORY_QUERY",
            )

        if not observations:
            # No observations → empty/insufficient knowledge build
            build = KnowledgeBuild(
                knowledge_build_id=build_id,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
                distiller_version=DISTILLER_VERSION,
                source_memory_version="1.0.0",
                findings_created=0,
                findings_updated=0,
                findings_unchanged=0,
                open_questions_created=0,
                errors=[],
                status="COMPLETED_EMPTY",
            )
            if knowledge_store is not None:
                knowledge_store._record_build(build)
            return build

        # Group by family
        family_groups: Dict[str, List[Dict[str, Any]]] = {}
        for obs in observations:
            fid = obs.get("experiment_family_id", "")
            if fid:
                family_groups.setdefault(fid, []).append(obs)

        # Process each family
        if knowledge_store is not None:
            for family_id, family_obs in family_groups.items():
                try:
                    finding, question = _distill_family(
                        family_id, family_obs, build_id,
                    )
                    if finding is not None:
                        existing = knowledge_store.find_finding(finding.finding_id)
                        if existing is None:
                            knowledge_store._upsert_finding(finding)
                            findings_created += 1
                        else:
                            # Check if materially changed
                            if existing["statement"] != finding.statement:
                                knowledge_store._record_history(existing, finding, build_id)
                                knowledge_store._upsert_finding(finding)
                                findings_updated += 1
                            else:
                                findings_unchanged += 1
                    if question is not None:
                        knowledge_store._upsert_question(question)
                        questions_created += 1
                except Exception as e:
                    errors.append(f"Error distilling family {family_id}: {e}")

        # Record the build
        completed_at = datetime.now(timezone.utc).isoformat()
        if knowledge_store is not None:
            knowledge_store._record_build(KnowledgeBuild(
                knowledge_build_id=build_id,
                started_at=started_at,
                completed_at=completed_at,
                distiller_version=DISTILLER_VERSION,
                source_memory_version="1.0.0",
                findings_created=findings_created,
                findings_updated=findings_updated,
                findings_unchanged=findings_unchanged,
                open_questions_created=questions_created,
                errors=errors,
                status="COMPLETED" if not errors else "COMPLETED_WITH_ERRORS",
            ))

        return KnowledgeBuild(
            knowledge_build_id=build_id,
            started_at=started_at,
            completed_at=completed_at,
            distiller_version=DISTILLER_VERSION,
            source_memory_version="1.0.0",
            findings_created=findings_created,
            findings_updated=findings_updated,
            findings_unchanged=findings_unchanged,
            open_questions_created=questions_created,
            errors=errors,
            status="COMPLETED" if not errors else "COMPLETED_WITH_ERRORS",
        )

    finally:
        if close_conn:
            conn.close()


def _distill_family(
    family_id: str,
    observations: List[Dict[str, Any]],
    build_id: str,
) -> Tuple[Optional[ResearchFinding], Optional[OpenQuestion]]:
    """Distill findings from a single experiment family's observations.

    Returns (finding, open_question).  Either may be None.
    """
    if not observations:
        return None, None

    # --- Group comparable observations ---
    comparable_groups = group_comparable_observations(observations)

    findings: List[ResearchFinding] = []
    questions: List[OpenQuestion] = []

    for group in comparable_groups:
        if len(group) == 0:
            continue

        # --- Build scope ---
        scope = _build_scope(group)

        # --- Aggregate metrics ---
        metric_summary = _aggregate_metrics(group)

        # --- Directional consistency ---
        consistency_ratio, pos, neg, total = _compute_directional_consistency(group)

        # --- Trade count ---
        total_trades = int(metric_summary.get("trade_count_mean", 0) * len(group)
                          if "trade_count_mean" in metric_summary else 0)

        # --- Unique dimensions ---
        unique_instruments = _count_unique_instruments(group)
        independent_revalidations = sum(
            1 for o in group if o.get("classification") == "REVALIDATION"
        )
        methodology_versions = _count_methodology_versions(group)

        # --- Eligible / rejected ---
        has_eligible = any(o.get("eligible") for o in group)
        has_rejected = any(not o.get("eligible") for o in group)

        # --- Contradictions ---
        contradiction_count = neg if pos > 0 and neg > 0 else 0

        # --- Duplicate weight ---
        exact_dups = _count_exact_duplicates(group)
        # Use unique instances for evidence count
        unique_ids = set(o.get("experiment_instance_id", "") for o in group)
        evidence_count = len(unique_ids)

        # --- Determine finding type ---
        finding_type = _determine_finding_type(group, metric_summary, has_eligible, has_rejected)

        # --- Compute confidence ---
        confidence, confidence_basis = compute_confidence(
            evidence_count=evidence_count,
            independent_revalidations=independent_revalidations,
            unique_instruments=unique_instruments,
            consistency_ratio=consistency_ratio,
            total_trades=total_trades,
            contradiction_count=contradiction_count,
            methodology_versions=methodology_versions,
        )

        # --- Determine status ---
        status = FindingStatus.ACTIVE
        if finding_type == FindingType.CONTRADICTION:
            status = FindingStatus.CONTESTED
        elif confidence == ConfidenceLevel.INSUFFICIENT:
            status = FindingStatus.INSUFFICIENT

        # --- Build statement ---
        statement = _build_statement(
            finding_type, scope, metric_summary,
            consistency_ratio, evidence_count, total_trades,
        )

        # --- Build evidence refs ---
        evidence_refs = []
        for obs in group:
            evidence_refs.append(EvidenceRef(
                observation_id=obs.get("experiment_instance_id", ""),
                run_id=obs.get("run_id", ""),
                config_key=obs.get("config_key", ""),
                experiment_family_id=obs.get("experiment_family_id", ""),
                classification=obs.get("classification", ""),
                status=obs.get("status", ""),
                eligible=bool(obs.get("eligible")),
                metrics_json=obs.get("metrics_json", "{}"),
            ).to_dict())

        # --- Compute finding ID ---
        strategy = scope.get("strategies", [""])[0] if scope.get("strategies") else ""
        instrument = scope.get("instruments", [""])[0] if scope.get("instruments") else ""
        subject = f"{strategy}:{instrument}" if instrument else strategy
        methodology = group[0].get("validation_version", "default") if group else "default"

        finding_id = compute_finding_id(finding_type, subject, scope, methodology)

        # --- Timestamps ---
        first_at = min(o.get("indexed_at", "") for o in group if o.get("indexed_at")) or ""
        last_at = max(o.get("indexed_at", "") for o in group if o.get("indexed_at")) or ""

        finding = ResearchFinding(
            finding_id=finding_id,
            finding_type=finding_type,
            subject=subject,
            scope=scope,
            statement=statement,
            status=status,
            confidence=confidence,
            confidence_basis=confidence_basis,
            evidence_refs=evidence_refs,
            supporting_observations=total - contradiction_count,
            contradicting_observations=contradiction_count,
            first_observed_at=first_at,
            last_updated_at=datetime.now(timezone.utc).isoformat(),
            last_evidence_at=last_at,
            distiller_version=DISTILLER_VERSION,
            schema_version=KNOWLEDGE_SCHEMA_VERSION,
            knowledge_build_id=build_id,
        )

        findings.append(finding)

        # --- Generate open questions ---
        question = _generate_open_question(finding, group, build_id)
        if question is not None:
            questions.append(question)

    # Return the primary finding (or first if multiple groups)
    primary_finding = findings[0] if findings else None
    primary_question = questions[0] if questions else None

    return primary_finding, primary_question


def _generate_open_question(
    finding: ResearchFinding,
    observations: List[Dict[str, Any]],
    build_id: str,
) -> Optional[OpenQuestion]:
    """Generate structured open questions when evidence supports them."""
    reason = ""
    priority = ""

    if finding.confidence == ConfidenceLevel.INSUFFICIENT:
        reason = "INSUFFICIENT_REVALIDATION"
        priority = "medium"
    elif finding.status == FindingStatus.CONTESTED:
        reason = "CONTRADICTORY_RESULTS"
        priority = "high"
    elif finding.confidence == ConfidenceLevel.LOW:
        reason = "INSUFFICIENT_REVALIDATION"
        priority = "low"
    else:
        return None

    # Check for cost sensitivity gap
    cost_hashes = set(o.get("cost_model_hash", "") for o in observations)
    if len(cost_hashes) <= 1:
        reason = "MISSING_COST_SENSITIVITY"
        priority = "low"

    question_id = compute_question_id(finding.subject, reason, finding.scope)

    return OpenQuestion(
        question_id=question_id,
        subject=finding.subject,
        reason=reason,
        evidence_refs=finding.evidence_refs[:3],  # subset
        priority_hint=priority,
        created_at=datetime.now(timezone.utc).isoformat(),
        status="OPEN",
    )


def _generate_build_id(started_at: str) -> str:
    """Generate a deterministic build ID from timestamp."""
    h = hashlib.sha256(started_at.encode("utf-8")).hexdigest()[:12]
    return f"kb_{h}"


# ---------------------------------------------------------------------------
# Knowledge Store (SQLite)
# ---------------------------------------------------------------------------

class KnowledgeStore:
    """Canonical knowledge store backed by SQLite.

    Store: state/research_knowledge.db (separate from experiment_memory.db).

    Supports:
    - Deterministic findings with stable identity
    - Evidence provenance chain
    - Finding history (material changes preserved)
    - Confidence with explicit basis
    - Contradiction tracking
    - Open questions
    - Build provenance (knowledge_build_id)
    - Idempotent rebuild
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or Path("state") / KNOWLEDGE_DB_NAME
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path),
                timeout=10,
                isolation_level="DEFERRED",
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def _ensure_schema(self) -> None:
        conn = self._connect()
        conn.executescript(_KNOWLEDGE_SCHEMA_SQL)
        conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Internal: upsert finding
    # ------------------------------------------------------------------

    def _upsert_finding(self, finding: ResearchFinding) -> None:
        """Insert or update a finding."""
        conn = self._connect()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT OR REPLACE INTO research_findings
               (finding_id, finding_type, subject, scope_json,
                statement, status, confidence, confidence_basis,
                evidence_refs_json, supporting_observations,
                contradicting_observations,
                first_observed_at, last_updated_at, last_evidence_at,
                distiller_version, schema_version, knowledge_build_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                finding.finding_id,
                finding.finding_type.value,
                finding.subject,
                json.dumps(finding.scope, ensure_ascii=False),
                finding.statement,
                finding.status.value,
                finding.confidence.value,
                finding.confidence_basis,
                json.dumps(finding.evidence_refs, ensure_ascii=False),
                finding.supporting_observations,
                finding.contradicting_observations,
                finding.first_observed_at,
                finding.last_updated_at,
                finding.last_evidence_at,
                finding.distiller_version,
                finding.schema_version,
                finding.knowledge_build_id,
            ),
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Internal: record history
    # ------------------------------------------------------------------

    def _record_history(
        self,
        old_finding: Dict[str, Any],
        new_finding: ResearchFinding,
        build_id: str,
    ) -> None:
        """Record a material finding change in history."""
        conn = self._connect()
        conn.execute(
            """INSERT INTO finding_history
               (finding_id, previous_status, new_status,
                previous_confidence, new_confidence,
                previous_statement, new_statement,
                changed_at, knowledge_build_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                old_finding["finding_id"],
                old_finding["status"],
                new_finding.status.value,
                old_finding["confidence"],
                new_finding.confidence.value,
                old_finding["statement"],
                new_finding.statement,
                datetime.now(timezone.utc).isoformat(),
                build_id,
            ),
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Internal: upsert open question
    # ------------------------------------------------------------------

    def _upsert_question(self, question: OpenQuestion) -> None:
        conn = self._connect()
        conn.execute(
            """INSERT OR REPLACE INTO open_questions
               (question_id, subject, reason, evidence_refs_json,
                priority_hint, created_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                question.question_id,
                question.subject,
                question.reason,
                json.dumps(question.evidence_refs, ensure_ascii=False),
                question.priority_hint,
                question.created_at,
                question.status,
            ),
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Internal: record build
    # ------------------------------------------------------------------

    def _record_build(self, build: KnowledgeBuild) -> None:
        conn = self._connect()
        conn.execute(
            """INSERT OR REPLACE INTO knowledge_builds
               (knowledge_build_id, started_at, completed_at,
                distiller_version, source_memory_version,
                findings_created, findings_updated, findings_unchanged,
                open_questions_created, errors_json, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                build.knowledge_build_id,
                build.started_at,
                build.completed_at,
                build.distiller_version,
                build.source_memory_version,
                build.findings_created,
                build.findings_updated,
                build.findings_unchanged,
                build.open_questions_created,
                json.dumps(build.errors, ensure_ascii=False),
                build.status,
            ),
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def find_finding(self, finding_id: str) -> Optional[Dict[str, Any]]:
        """Find one finding by ID."""
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM research_findings WHERE finding_id = ?",
            (finding_id,),
        ).fetchone()
        return dict(row) if row else None

    def find_findings(
        self,
        subject: Optional[str] = None,
        finding_type: Optional[str] = None,
        status: Optional[str] = None,
        instrument: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Find findings with optional filters."""
        conn = self._connect()
        conditions = []
        params: List[Any] = []

        if subject:
            conditions.append("subject LIKE ?")
            params.append(f"%{subject}%")
        if finding_type:
            conditions.append("finding_type = ?")
            params.append(finding_type)
        if status:
            conditions.append("status = ?")
            params.append(status)
        if instrument:
            conditions.append("scope_json LIKE ?")
            params.append(f"%{instrument.upper()}%")

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"SELECT * FROM research_findings {where} ORDER BY last_updated_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def find_strategy_knowledge(
        self,
        strategy_family: str,
        instrument: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Find all knowledge about a strategy family."""
        conn = self._connect()
        conditions = ["subject LIKE ?"]
        params: List[Any] = [f"%{strategy_family.lower()}%"]

        if instrument:
            conditions.append("scope_json LIKE ?")
            params.append(f"%{instrument.upper()}%")

        where = f"WHERE {' AND '.join(conditions)}"
        query = f"SELECT * FROM research_findings {where} ORDER BY last_updated_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def find_contested(self) -> List[Dict[str, Any]]:
        """Find all contested/contradicted findings."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM research_findings WHERE status = 'CONTESTED' ORDER BY last_updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def find_insufficient(self) -> List[Dict[str, Any]]:
        """Find all findings with insufficient evidence."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM research_findings WHERE confidence = 'INSUFFICIENT' OR status = 'INSUFFICIENT' ORDER BY last_updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def explain_finding(self, finding_id: str) -> Dict[str, Any]:
        """Explain a finding with full provenance.

        Returns finding + evidence chain + build provenance.
        Does NOT return prose — returns structured evidence.
        """
        finding = self.find_finding(finding_id)
        if finding is None:
            return {"error": "FINDING_NOT_FOUND", "finding_id": finding_id}

        # Parse evidence refs
        evidence_refs = []
        try:
            evidence_refs = json.loads(finding.get("evidence_refs_json", "[]"))
        except (json.JSONDecodeError, TypeError):
            pass

        # Find related history
        conn = self._connect()
        history_rows = conn.execute(
            "SELECT * FROM finding_history WHERE finding_id = ? ORDER BY changed_at ASC",
            (finding_id,),
        ).fetchall()
        history = [dict(r) for r in history_rows]

        # Find related build
        build_id = finding.get("knowledge_build_id", "")
        build = None
        if build_id:
            row = conn.execute(
                "SELECT * FROM knowledge_builds WHERE knowledge_build_id = ?",
                (build_id,),
            ).fetchone()
            build = dict(row) if row else None

        return {
            "finding": finding,
            "evidence_chain": evidence_refs,
            "history": history,
            "build_provenance": build,
        }

    def get_evidence(self, finding_id: str) -> List[Dict[str, Any]]:
        """Get the evidence chain for a finding."""
        finding = self.find_finding(finding_id)
        if finding is None:
            return []
        try:
            return json.loads(finding.get("evidence_refs_json", "[]"))
        except (json.JSONDecodeError, TypeError):
            return []

    def get_open_questions(
        self,
        status: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get open knowledge questions."""
        conn = self._connect()
        conditions = []
        params: List[Any] = []
        if status:
            conditions.append("status = ?")
            params.append(status)
        if reason:
            conditions.append("reason = ?")
            params.append(reason)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"SELECT * FROM open_questions {where} ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_build_history(self) -> List[Dict[str, Any]]:
        """Get all knowledge build provenance records."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM knowledge_builds ORDER BY started_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def summary(self) -> Dict[str, Any]:
        """Generate a derived summary report (not source of truth)."""
        conn = self._connect()
        total_findings = conn.execute(
            "SELECT COUNT(*) as cnt FROM research_findings"
        ).fetchone()["cnt"]

        type_rows = conn.execute(
            "SELECT finding_type, COUNT(*) as cnt FROM research_findings GROUP BY finding_type"
        ).fetchall()
        by_type = {r["finding_type"]: r["cnt"] for r in type_rows}

        status_rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM research_findings GROUP BY status"
        ).fetchall()
        by_status = {r["status"]: r["cnt"] for r in status_rows}

        confidence_rows = conn.execute(
            "SELECT confidence, COUNT(*) as cnt FROM research_findings GROUP BY confidence"
        ).fetchall()
        by_confidence = {r["confidence"]: r["cnt"] for r in confidence_rows}

        contested = conn.execute(
            "SELECT COUNT(*) as cnt FROM research_findings WHERE status = 'CONTESTED'"
        ).fetchone()["cnt"]

        insufficient = conn.execute(
            "SELECT COUNT(*) as cnt FROM research_findings WHERE confidence = 'INSUFFICIENT'"
        ).fetchone()["cnt"]

        open_q = conn.execute(
            "SELECT COUNT(*) as cnt FROM open_questions WHERE status = 'OPEN'"
        ).fetchone()["cnt"]

        builds = conn.execute(
            "SELECT COUNT(*) as cnt FROM knowledge_builds"
        ).fetchone()["cnt"]

        return {
            "schema_version": KNOWLEDGE_SCHEMA_VERSION,
            "distiller_version": DISTILLER_VERSION,
            "total_findings": total_findings,
            "findings_by_type": by_type,
            "findings_by_status": by_status,
            "findings_by_confidence": by_confidence,
            "contested_findings": contested,
            "insufficient_evidence_findings": insufficient,
            "open_questions": open_q,
            "total_builds": builds,
        }


# ---------------------------------------------------------------------------
# Derived report writer
# ---------------------------------------------------------------------------

def write_knowledge_report(
    knowledge_store: KnowledgeStore,
    output_dir: Path,
) -> Dict[str, Any]:
    """Write human-readable derived knowledge report.

    These are views, not source of truth.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = knowledge_store.summary()

    # Markdown report
    md_lines = [
        "# Research Knowledge Summary",
        f"\n**Generated:** {datetime.now(timezone.utc).isoformat()}",
        f"**Schema version:** {summary['schema_version']}",
        f"**Distiller version:** {summary['distiller_version']}",
        "",
        "## What We Know",
        "",
        f"- **Total findings:** {summary['total_findings']}",
        f"- **Contested findings:** {summary['contested_findings']}",
        f"- **Insufficient evidence:** {summary['insufficient_evidence_findings']}",
        f"- **Open questions:** {summary['open_questions']}",
        "",
        "### By Type",
        "",
    ]
    for ftype, cnt in summary["findings_by_type"].items():
        md_lines.append(f"- {ftype}: {cnt}")

    md_lines.extend(["", "### By Confidence", ""])
    for conf, cnt in summary["findings_by_confidence"].items():
        md_lines.append(f"- {conf}: {cnt}")

    md_lines.extend(["", "### By Status", ""])
    for stat, cnt in summary["findings_by_status"].items():
        md_lines.append(f"- {stat}: {cnt}")

    # What is contested
    contested = knowledge_store.find_contested()
    if contested:
        md_lines.extend(["", "## What Is Contradicted", ""])
        for f in contested:
            md_lines.append(f"- **{f['finding_id']}**: {f['statement']}")

    # What lacks evidence
    insufficient = knowledge_store.find_insufficient()
    if insufficient:
        md_lines.extend(["", "## What Lacks Evidence", ""])
        for f in insufficient:
            md_lines.append(f"- **{f['finding_id']}**: {f['statement']}")

    # Open questions
    questions = knowledge_store.get_open_questions(status="OPEN")
    if questions:
        md_lines.extend(["", "## Open Questions", ""])
        for q in questions:
            md_lines.append(f"- **{q['question_id']}** [{q['priority_hint']}]: {q['reason']} — {q['subject']}")

    md_content = "\n".join(md_lines)
    md_path = output_dir / "latest_summary.md"
    md_path.write_text(md_content, encoding="utf-8")

    # JSON report
    json_path = output_dir / "latest_summary.json"
    json_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return {"md_path": str(md_path), "json_path": str(json_path)}


# ---------------------------------------------------------------------------
# SQLite Schema
# ---------------------------------------------------------------------------

_KNOWLEDGE_SCHEMA_SQL = """
-- Research Knowledge Layer schema v1.0.0 (Iteration 09)
-- Separate from experiment_memory.db and analytics.db.

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_meta (key, value)
VALUES ('schema_version', '1.0.0'),
       ('created_at', datetime('now'));

-- Research findings: canonical evidence-backed statements.
CREATE TABLE IF NOT EXISTS research_findings (
    finding_id               TEXT PRIMARY KEY,
    finding_type             TEXT NOT NULL,
    subject                  TEXT NOT NULL,
    scope_json               TEXT NOT NULL DEFAULT '{}',
    statement                TEXT NOT NULL,

    status                   TEXT NOT NULL DEFAULT 'ACTIVE',
    confidence               TEXT NOT NULL DEFAULT 'INSUFFICIENT',
    confidence_basis         TEXT NOT NULL DEFAULT '',

    evidence_refs_json       TEXT NOT NULL DEFAULT '[]',
    supporting_observations  INTEGER NOT NULL DEFAULT 0,
    contradicting_observations INTEGER NOT NULL DEFAULT 0,

    first_observed_at        TEXT NOT NULL DEFAULT '',
    last_updated_at          TEXT NOT NULL DEFAULT '',
    last_evidence_at         TEXT NOT NULL DEFAULT '',

    distiller_version        TEXT NOT NULL DEFAULT '1.0.0',
    schema_version           TEXT NOT NULL DEFAULT '1.0.0',
    knowledge_build_id       TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_finding_type ON research_findings(finding_type);
CREATE INDEX IF NOT EXISTS idx_finding_status ON research_findings(status);
CREATE INDEX IF NOT EXISTS idx_finding_confidence ON research_findings(confidence);
CREATE INDEX IF NOT EXISTS idx_finding_subject ON research_findings(subject);
CREATE INDEX IF NOT EXISTS idx_finding_build ON research_findings(knowledge_build_id);

-- Finding history: material changes to findings are preserved.
CREATE TABLE IF NOT EXISTS finding_history (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_id               TEXT NOT NULL,
    previous_status          TEXT NOT NULL,
    new_status               TEXT NOT NULL,
    previous_confidence      TEXT NOT NULL,
    new_confidence           TEXT NOT NULL,
    previous_statement       TEXT NOT NULL,
    new_statement            TEXT NOT NULL,
    changed_at               TEXT NOT NULL,
    knowledge_build_id       TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_history_finding ON finding_history(finding_id);

-- Knowledge builds: provenance for each distillation execution.
CREATE TABLE IF NOT EXISTS knowledge_builds (
    knowledge_build_id       TEXT PRIMARY KEY,
    started_at               TEXT NOT NULL,
    completed_at             TEXT NOT NULL,
    distiller_version        TEXT NOT NULL DEFAULT '1.0.0',
    source_memory_version    TEXT NOT NULL DEFAULT '1.0.0',
    findings_created         INTEGER NOT NULL DEFAULT 0,
    findings_updated         INTEGER NOT NULL DEFAULT 0,
    findings_unchanged       INTEGER NOT NULL DEFAULT 0,
    open_questions_created   INTEGER NOT NULL DEFAULT 0,
    errors_json              TEXT NOT NULL DEFAULT '[]',
    status                   TEXT NOT NULL DEFAULT 'COMPLETED'
);

-- Open questions: unresolved knowledge gaps.
CREATE TABLE IF NOT EXISTS open_questions (
    question_id              TEXT PRIMARY KEY,
    subject                  TEXT NOT NULL,
    reason                   TEXT NOT NULL,
    evidence_refs_json       TEXT NOT NULL DEFAULT '[]',
    priority_hint            TEXT NOT NULL DEFAULT '',
    created_at               TEXT NOT NULL,
    status                   TEXT NOT NULL DEFAULT 'OPEN'
);

CREATE INDEX IF NOT EXISTS idx_question_status ON open_questions(status);
CREATE INDEX IF NOT EXISTS idx_question_reason ON open_questions(reason);
"""

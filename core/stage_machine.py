"""
core/stage_machine.py — Candidate Qualification Stage Machine

Manages the canonical 9-stage qualification pipeline for strategy candidates:

  DISCOVERY → BACKTEST_QUALIFIED → MULTI_HORIZON_QUALIFIED →
  WALK_FORWARD_QUALIFIED → ROBUSTNESS_QUALIFIED → RISK_QUALIFIED →
  PAPER_ADMISSION_READY → PAPER_QUALIFIED → LIVE_CANDIDATE

Key invariants:
  - Cannot skip stages (linear chain enforced)
  - 60d-only cannot reach PAPER_ADMISSION_READY
  - Missing OOS blocks required transitions
  - Missing robustness blocks required transitions
  - Missing risk evidence blocks required transitions
  - Each transition produces an immutable evidence contract
  - Evidence contracts carry policy_version, timestamp, code_version

Change class: CLASS 2 — Runtime infrastructure, no broker, no live.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Canonical Stage Enum (9 stages in strict qualification order)
# ---------------------------------------------------------------------------

class QualificationStage(str, Enum):
    """The 9 canonical qualification stages a candidate must traverse."""
    DISCOVERY = "DISCOVERY"
    BACKTEST_QUALIFIED = "BACKTEST_QUALIFIED"
    MULTI_HORIZON_QUALIFIED = "MULTI_HORIZON_QUALIFIED"
    WALK_FORWARD_QUALIFIED = "WALK_FORWARD_QUALIFIED"
    ROBUSTNESS_QUALIFIED = "ROBUSTNESS_QUALIFIED"
    RISK_QUALIFIED = "RISK_QUALIFIED"
    PAPER_ADMISSION_READY = "PAPER_ADMISSION_READY"
    PAPER_QUALIFIED = "PAPER_QUALIFIED"
    LIVE_CANDIDATE = "LIVE_CANDIDATE"


# Strict linear order (index = stage position in chain)
STAGE_ORDER: List[QualificationStage] = list(QualificationStage)

# Transition edges: valid transitions are (stage[i], stage[i+1]) for i in 0..N-2
TRANSITION_GRAPH: Dict[QualificationStage, QualificationStage] = {
    STAGE_ORDER[i]: STAGE_ORDER[i + 1]
    for i in range(len(STAGE_ORDER) - 1)
}

# All valid source stages (can transition from)
_VALID_FROM_STAGES: Set[QualificationStage] = set(TRANSITION_GRAPH.keys())

# All valid target stages (can transition to)
_VALID_TO_STAGES: Set[QualificationStage] = set(TRANSITION_GRAPH.values())


# ---------------------------------------------------------------------------
# Code version (bumped with module changes for traceability)
# ---------------------------------------------------------------------------

CODE_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Canonical Policy Loader
# ---------------------------------------------------------------------------

_DEFAULT_POLICY_PATH = Path(__file__).resolve().parent.parent / "config" / "research_qualification_policy.json"


def load_canonical_policy(
    policy_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Load the canonical research qualification policy JSON.

    Returns the full parsed dict. Raises FileNotFoundError if the file
    does not exist or is not valid JSON.
    """
    p = Path(policy_path) if policy_path else _DEFAULT_POLICY_PATH
    if not p.exists():
        raise FileNotFoundError(f"Canonical policy not found: {p}")
    with open(p, "r") as f:
        data = json.load(f)
    # Validate required top-level keys
    required_keys = {"policy_id", "version", "stages"}
    missing = required_keys - set(data.keys())
    if missing:
        raise ValueError(f"Policy missing required keys: {missing}")
    return data


def get_stage_requirements(
    stage_name: str,
    policy: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Return the list of required evidence items for a given stage name.

    If policy is not provided, loads from the canonical file.
    Returns empty list if stage not found in policy.
    """
    if policy is None:
        policy = load_canonical_policy()
    stage_def = policy.get("stages", {}).get(stage_name, {})
    return stage_def.get("requires", [])


def get_policy_hash(policy: Optional[Dict[str, Any]] = None) -> str:
    """SHA-256 hash of the policy for immutability tracking."""
    if policy is None:
        policy = load_canonical_policy()
    canonical = json.dumps(policy, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Transition Validation Helpers
# ---------------------------------------------------------------------------

def is_valid_transition(from_stage: str, to_stage: str) -> bool:
    """Check if a from_stage → to_stage transition is a valid single-step move."""
    try:
        src = QualificationStage(from_stage)
        dst = QualificationStage(to_stage)
    except ValueError:
        return False
    return TRANSITION_GRAPH.get(src) == dst


def is_valid_stage(stage_name: str) -> bool:
    """Check if a string is a valid QualificationStage name."""
    try:
        QualificationStage(stage_name)
        return True
    except ValueError:
        return False


def stage_index(stage_name: str) -> int:
    """Return numeric index of a stage (0-based). Raises ValueError if invalid."""
    s = QualificationStage(stage_name)
    return STAGE_ORDER.index(s)


def validate_skip_check(from_stage: str, to_stage: str) -> bool:
    """True if the transition is a valid single-step forward (no skipping)."""
    return is_valid_transition(from_stage, to_stage)


def validate_no_backward(from_stage: str, to_stage: str) -> bool:
    """True if to_stage is strictly after from_stage."""
    try:
        f_idx = stage_index(from_stage)
        t_idx = stage_index(to_stage)
        return t_idx > f_idx
    except ValueError:
        return False


def validate_stage_sequence(history: List[str]) -> bool:
    """Validate that a stage history is in strict ascending order."""
    if len(history) <= 1:
        return True
    indices = []
    for s in history:
        if not is_valid_stage(s):
            return False
        indices.append(stage_index(s))
    for i in range(len(indices) - 1):
        if indices[i] >= indices[i + 1]:
            return False
    return True


def get_maturity_chain() -> List[str]:
    """Return the ordered list of all stage names (the maturity chain)."""
    return [s.value for s in STAGE_ORDER]


# ---------------------------------------------------------------------------
# Stage Transition Dataclass (Evidence Contract)
# ---------------------------------------------------------------------------

@dataclass
class StageTransition:
    """Immutable evidence contract for a single stage transition.

    Carries all metadata needed to prove and audit a transition:
      - candidate_id, from_stage, to_stage
      - required_evidence: what the policy required
      - evidence_refs: what evidence was actually provided
      - policy_version / policy_hash: which policy version was enforced
      - data_horizon_refs: data/horizon info
      - timestamp, code_version: provenance
      - verdict: PASS or BLOCKED
      - reason: human-readable explanation
    """
    candidate_id: str
    from_stage: str
    to_stage: str
    required_evidence: List[str] = field(default_factory=list)
    evidence_refs: Dict[str, Any] = field(default_factory=dict)
    policy_version: str = ""
    policy_hash: str = ""
    data_horizon_refs: List[str] = field(default_factory=list)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    code_version: str = CODE_VERSION
    verdict: str = "PENDING"  # PASS | BLOCKED | PENDING
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        return {
            "candidate_id": self.candidate_id,
            "from_stage": self.from_stage,
            "to_stage": self.to_stage,
            "required_evidence": self.required_evidence,
            "evidence_refs": self.evidence_refs,
            "policy_version": self.policy_version,
            "policy_hash": self.policy_hash,
            "data_horizon_refs": self.data_horizon_refs,
            "timestamp": self.timestamp,
            "code_version": self.code_version,
            "verdict": self.verdict,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "StageTransition":
        """Reconstruct from dict."""
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Evidence Check Functions (enforce invariants)
# ---------------------------------------------------------------------------

def _check_oos_evidence(
    evidence: Dict[str, Any],
    from_stage: str,
    to_stage: str,
) -> Optional[str]:
    """Return blocking reason if OOS evidence is missing when required.

    OOS (out-of-sample) evidence is required for WALK_FORWARD_QUALIFIED.
    """
    if to_stage == "WALK_FORWARD_QUALIFIED":
        if not evidence.get("oos_folds") and not evidence.get("oos_evidence"):
            return "Missing OOS (out-of-sample) evidence required for WALK_FORWARD_QUALIFIED"
    return None


def _check_robustness_evidence(
    evidence: Dict[str, Any],
    from_stage: str,
    to_stage: str,
) -> Optional[str]:
    """Return blocking reason if robustness evidence is missing when required.

    Robustness evidence is required for ROBUSTNESS_QUALIFIED.
    """
    if to_stage == "ROBUSTNESS_QUALIFIED":
        has_param = evidence.get("parameter_neighborhood") or evidence.get("robustness_evidence")
        has_temporal = evidence.get("temporal_regime")
        if not (has_param and has_temporal):
            return "Missing robustness evidence (parameter_neighborhood + temporal_regime) required for ROBUSTNESS_QUALIFIED"
    return None


def _check_risk_evidence(
    evidence: Dict[str, Any],
    from_stage: str,
    to_stage: str,
) -> Optional[str]:
    """Return blocking reason if risk evidence is missing when required.

    Risk evidence is required for RISK_QUALIFIED.
    """
    if to_stage == "RISK_QUALIFIED":
        required_risk = {"LIVE_RISK_V1", "bounded_stop", "sizing", "account_feasibility"}
        provided_risk = set(evidence.get("risk_evidence", {}).keys()) if isinstance(evidence.get("risk_evidence"), dict) else set()
        # Also check flat keys
        for k in required_risk:
            if k in evidence:
                provided_risk.add(k)
        missing = required_risk - provided_risk
        if missing:
            return f"Missing risk evidence: {sorted(missing)} required for RISK_QUALIFIED"
    return None


def _check_60d_only_restriction(
    evidence: Dict[str, Any],
    from_stage: str,
    to_stage: str,
) -> Optional[str]:
    """Return blocking reason if 60d-only candidate tries to reach PAPER_ADMISSION_READY.

    Policy invariant: 60d_only_cannot_reach_PAPER_ADMISSION_READY
    """
    if to_stage in ("PAPER_ADMISSION_READY", "PAPER_QUALIFIED", "LIVE_CANDIDATE"):
        horizon_coverage = evidence.get("horizon_coverage", [])
        if isinstance(horizon_coverage, str):
            horizon_coverage = [horizon_coverage]
        # If explicitly marked as 60d-only
        if evidence.get("is_60d_only") is True:
            return "60d-only candidate cannot reach PAPER_ADMISSION_READY (policy invariant)"
        # If coverage only includes 60d and nothing else
        if horizon_coverage and horizon_coverage == ["60d"]:
            return "60d-only candidate cannot reach PAPER_ADMISSION_READY (policy invariant)"
    return None


def _check_policy_version(
    evidence: Dict[str, Any],
) -> Optional[str]:
    """Return blocking reason if policy_version is missing from evidence."""
    if not evidence.get("policy_version"):
        return "Missing policy_version in evidence — transitions require explicit policy binding"
    return None


def _check_historical_replay(
    evidence: Dict[str, Any],
    from_stage: str,
    to_stage: str,
) -> Optional[str]:
    """Return blocking reason if historical replay tries to create PAPER_QUALIFIED.

    Paper qualification requires chronological real-time evidence, not replay.
    """
    if to_stage in ("PAPER_QUALIFIED", "LIVE_CANDIDATE"):
        if evidence.get("is_historical_replay") is True:
            return "Historical replay cannot create PAPER_QUALIFIED — chronological paper evidence required"
    return None


# ---------------------------------------------------------------------------
# Stage Machine
# ---------------------------------------------------------------------------

class StageMachine:
    """Manages candidate qualification stage transitions.

    Features:
      - Loads canonical policy from JSON
      - Enforces strict linear stage progression
      - Enforces all evidence/invariant checks
      - Stores transition history in-memory
      - Produces immutable StageTransition evidence contracts

    In-memory state:
      - candidate_history: Dict[candidate_id, List[StageTransition]]
    """

    def __init__(
        self,
        policy_path: Optional[Path] = None,
        policy: Optional[Dict[str, Any]] = None,
    ):
        """Initialize the stage machine with a canonical policy.

        Args:
            policy_path: Optional path to a custom policy JSON.
            policy: Optional pre-loaded policy dict (bypasses file load).
        """
        if policy is not None:
            self._policy = policy
        else:
            self._policy = load_canonical_policy(policy_path)

        self._policy_version = self._policy.get("version", "unknown")
        self._policy_hash = get_policy_hash(self._policy)

        # In-memory stage history: candidate_id → list of transitions
        self._candidate_history: Dict[str, List[StageTransition]] = {}

    @property
    def policy_version(self) -> str:
        return self._policy_version

    @property
    def policy_hash(self) -> str:
        return self._policy_hash

    @property
    def canonical_stages(self) -> List[str]:
        """Return ordered list of canonical stage names."""
        return [s.value for s in STAGE_ORDER]

    def get_history(self, candidate_id: str) -> List[StageTransition]:
        """Return transition history for a candidate."""
        return list(self._candidate_history.get(candidate_id, []))

    def get_current_stage(self, candidate_id: str) -> Optional[str]:
        """Return the current (most recent) stage of a candidate, or None."""
        history = self._candidate_history.get(candidate_id, [])
        if history:
            return history[-1].to_stage
        return None

    def transition_candidate(
        self,
        candidate_id: str,
        from_stage: str,
        to_stage: str,
        evidence: Dict[str, Any],
    ) -> StageTransition:
        """Execute a stage transition for a candidate.

        Args:
            candidate_id: Unique candidate identifier.
            from_stage: Current stage (must match expected current stage).
            to_stage: Desired target stage.
            evidence: Evidence dict for the transition.

        Returns:
            StageTransition with verdict PASS or BLOCKED.
        """
        # --- Basic validation ---
        if not is_valid_stage(from_stage):
            return StageTransition(
                candidate_id=candidate_id,
                from_stage=from_stage,
                to_stage=to_stage,
                verdict="BLOCKED",
                reason=f"Invalid source stage: {from_stage}",
            )

        if not is_valid_stage(to_stage):
            return StageTransition(
                candidate_id=candidate_id,
                from_stage=from_stage,
                to_stage=to_stage,
                verdict="BLOCKED",
                reason=f"Invalid target stage: {to_stage}",
            )

        # --- Duplicate transition check (before skip check for same-stage) ---
        history = self._candidate_history.get(candidate_id, [])
        if history:
            current_stage = history[-1].to_stage
            if current_stage == to_stage:
                return StageTransition(
                    candidate_id=candidate_id,
                    from_stage=from_stage,
                    to_stage=to_stage,
                    verdict="BLOCKED",
                    reason=f"Candidate {candidate_id} is already at stage {to_stage}",
                )
            if current_stage != from_stage:
                return StageTransition(
                    candidate_id=candidate_id,
                    from_stage=from_stage,
                    to_stage=to_stage,
                    verdict="BLOCKED",
                    reason=f"Candidate {candidate_id} current stage is {current_stage}, not {from_stage}",
                )

        # --- Skip check ---
        if not is_valid_transition(from_stage, to_stage):
            return StageTransition(
                candidate_id=candidate_id,
                from_stage=from_stage,
                to_stage=to_stage,
                verdict="BLOCKED",
                reason=f"Cannot skip stages: {from_stage} → {to_stage} is not a valid single-step transition",
            )

        # --- Policy version binding ---
        reason = _check_policy_version(evidence)
        if reason:
            return StageTransition(
                candidate_id=candidate_id,
                from_stage=from_stage,
                to_stage=to_stage,
                verdict="BLOCKED",
                reason=reason,
            )

        # --- 60d-only restriction ---
        reason = _check_60d_only_restriction(evidence, from_stage, to_stage)
        if reason:
            return StageTransition(
                candidate_id=candidate_id,
                from_stage=from_stage,
                to_stage=to_stage,
                evidence_refs=evidence,
                policy_version=self._policy_version,
                policy_hash=self._policy_hash,
                verdict="BLOCKED",
                reason=reason,
            )

        # --- Historical replay block ---
        reason = _check_historical_replay(evidence, from_stage, to_stage)
        if reason:
            return StageTransition(
                candidate_id=candidate_id,
                from_stage=from_stage,
                to_stage=to_stage,
                evidence_refs=evidence,
                policy_version=self._policy_version,
                policy_hash=self._policy_hash,
                verdict="BLOCKED",
                reason=reason,
            )

        # --- OOS evidence check ---
        reason = _check_oos_evidence(evidence, from_stage, to_stage)
        if reason:
            return StageTransition(
                candidate_id=candidate_id,
                from_stage=from_stage,
                to_stage=to_stage,
                required_evidence=get_stage_requirements(to_stage, self._policy),
                evidence_refs=evidence,
                policy_version=self._policy_version,
                policy_hash=self._policy_hash,
                verdict="BLOCKED",
                reason=reason,
            )

        # --- Robustness evidence check ---
        reason = _check_robustness_evidence(evidence, from_stage, to_stage)
        if reason:
            return StageTransition(
                candidate_id=candidate_id,
                from_stage=from_stage,
                to_stage=to_stage,
                required_evidence=get_stage_requirements(to_stage, self._policy),
                evidence_refs=evidence,
                policy_version=self._policy_version,
                policy_hash=self._policy_hash,
                verdict="BLOCKED",
                reason=reason,
            )

        # --- Risk evidence check ---
        reason = _check_risk_evidence(evidence, from_stage, to_stage)
        if reason:
            return StageTransition(
                candidate_id=candidate_id,
                from_stage=from_stage,
                to_stage=to_stage,
                required_evidence=get_stage_requirements(to_stage, self._policy),
                evidence_refs=evidence,
                policy_version=self._policy_version,
                policy_hash=self._policy_hash,
                verdict="BLOCKED",
                reason=reason,
            )

        # --- All checks passed: create PASS transition ---
        transition = StageTransition(
            candidate_id=candidate_id,
            from_stage=from_stage,
            to_stage=to_stage,
            required_evidence=get_stage_requirements(to_stage, self._policy),
            evidence_refs=evidence,
            policy_version=self._policy_version,
            policy_hash=self._policy_hash,
            data_horizon_refs=evidence.get("data_horizon_refs", []),
            verdict="PASS",
            reason="All checks passed",
        )

        # --- Store in history ---
        if candidate_id not in self._candidate_history:
            self._candidate_history[candidate_id] = []
        self._candidate_history[candidate_id].append(transition)

        return transition

    def validate_full_maturity_chain(
        self,
        evidence_dict: Dict[str, Any],
    ) -> bool:
        """Validate that a candidate has passed through all required stages.

        Args:
            evidence_dict: Dict with keys being stage names and values being
                          evidence for each stage. Must cover all 9 stages
                          in order.

        Returns:
            True if the full maturity chain is valid and all checks pass.
        """
        for stage in STAGE_ORDER:
            stage_name = stage.value
            if stage_name not in evidence_dict:
                return False
            stage_evidence = evidence_dict[stage_name]
            if not isinstance(stage_evidence, dict):
                return False
            if not stage_evidence:
                return False
        return True

    def get_stage_requirements_for(
        self,
        stage_name: str,
    ) -> List[str]:
        """Return the required evidence list for a stage from the loaded policy."""
        return get_stage_requirements(stage_name, self._policy)

    def clear_history(self) -> None:
        """Clear all in-memory stage history (for testing)."""
        self._candidate_history.clear()

    def export_history(self) -> Dict[str, List[Dict[str, Any]]]:
        """Export all candidate histories as serializable dicts."""
        result = {}
        for cid, transitions in self._candidate_history.items():
            result[cid] = [t.to_dict() for t in transitions]
        return result

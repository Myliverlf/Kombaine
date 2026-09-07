"""Novelty Gate & Duplicate Suppression — Iteration 08.

Implements the first narrow compute-efficiency control authority for the
research system.  Before expensive backtest execution, each planned candidate
is classified against experiment memory.  Only evidence-proven EXACT_DUPLICATE
is auto-skipped.  Everything else RUNS.

CLASS 1 ONLY: research-control policy / no broker / no live / no strategy/risk changes.

Default policy:
  NEW                → RUN
  REVALIDATION       → RUN
  METHODOLOGY_CHANGE → RUN
  CODE_CHANGE        → RUN
  COST_MODEL_CHANGE  → RUN
  INCOMPARABLE       → RUN
  EXACT_DUPLICATE    → SKIP
  memory unavailable → RUN + warning  (fail-open for research)

Forced reproduction: --force-reproduction flag or plan-level force_reproduction=true.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NOVELTY_POLICY_VERSION = 1

# Default policy table: classification → action
# Only EXACT_DUPLICATE is auto-skipped.  Everything else RUNS.
DEFAULT_POLICY_TABLE: Dict[str, str] = {
    "NEW": "RUN",
    "REVALIDATION": "RUN",
    "METHODOLOGY_CHANGE": "RUN",
    "CODE_CHANGE": "RUN",
    "COST_MODEL_CHANGE": "RUN",
    "INCOMPARABLE": "RUN",
    "EXACT_DUPLICATE": "SKIP",
}

# Valid novelty decisions
NOVELTY_DECISIONS = frozenset({
    "RUN",
    "SKIP_EXACT_DUPLICATE",
    "RUN_FORCED_REPRODUCTION",
})

# Valid skip reasons
SKIP_REASONS = frozenset({
    "EXACT_DUPLICATE",
})

# Terminal candidate ledger states for skipped / forced candidates
SKIPPED_EXACT_DUPLICATE_STATE = "skipped_exact_duplicate"
FORCED_REPRODUCTION_STATE = "forced_reproduction"


# ---------------------------------------------------------------------------
# NoveltyDecision dataclass
# ---------------------------------------------------------------------------

@dataclass
class NoveltyDecision:
    """Canonical novelty decision for one planned experiment candidate.

    Every planned candidate receives exactly one NoveltyDecision.
    """
    experiment_family_id: str
    experiment_instance_id: str
    memory_classification: str
    decision: str  # RUN | SKIP_EXACT_DUPLICATE | RUN_FORCED_REPRODUCTION
    reason_code: str
    reason_text: str
    matched_prior_instance_id: Optional[str] = None
    matched_prior_run_id: Optional[str] = None
    matched_prior_config_key: Optional[str] = None
    forced: bool = False
    policy_version: int = NOVELTY_POLICY_VERSION
    decided_at: str = ""

    def __post_init__(self):
        if not self.decided_at:
            self.decided_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# NoveltyPolicy
# ---------------------------------------------------------------------------

class NoveltyPolicy:
    """Explicit policy table for novelty gate decisions.

    Default: only EXACT_DUPLICATE is auto-skipped.
    Unknown classifications default to RUN (fail-open).
    """

    def __init__(
        self,
        policy_table: Optional[Dict[str, str]] = None,
        version: int = NOVELTY_POLICY_VERSION,
    ):
        self.table = dict(policy_table or DEFAULT_POLICY_TABLE)
        self.version = version

    def action_for(self, classification: str) -> str:
        """Return the action for a given classification.

        Unknown classification → RUN (fail-open for research).
        """
        return self.table.get(classification, "RUN")

    def is_skip(self, classification: str) -> bool:
        """Check if this classification triggers an automatic skip."""
        return self.action_for(classification) == "SKIP"


# ---------------------------------------------------------------------------
# Novelty artifact writer
# ---------------------------------------------------------------------------

class NoveltyArtifactWriter:
    """Writes novelty_decisions.jsonl per-run artifact.

    Append-safe: one JSON object per line.  Every planned candidate
    receives exactly one decision record.
    """

    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self._path = run_dir / "novelty_decisions.jsonl"

    def write(self, decision: NoveltyDecision) -> None:
        """Append one novelty decision to the artifact."""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(decision.to_dict(), ensure_ascii=False) + "\n")

    def read_all(self) -> List[Dict[str, Any]]:
        """Read all decisions from the artifact."""
        if not self._path.exists():
            return []
        results = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return results

    @property
    def path(self) -> Path:
        return self._path


# ---------------------------------------------------------------------------
# Skip accounting
# ---------------------------------------------------------------------------

@dataclass
class NoveltyAccounting:
    """Run-level novelty accounting.

    Tracks planned/executed/skipped/forced/failed/eligible counts.
    """

    planned: int = 0
    executed: int = 0
    skipped_exact_duplicate: int = 0
    forced_reproduction: int = 0
    failed: int = 0
    eligible: int = 0
    lookup_errors: int = 0
    memory_available: bool = True
    decisions: List[NoveltyDecision] = field(default_factory=list)

    def record_decision(self, decision: NoveltyDecision) -> None:
        """Record a novelty decision and update accounting."""
        self.decisions.append(decision)
        if decision.decision == "SKIP_EXACT_DUPLICATE":
            self.skipped_exact_duplicate += 1
        elif decision.decision == "RUN_FORCED_REPRODUCTION":
            self.forced_reproduction += 1
        # RUN decisions are counted as executed after backtest completes

    def record_executed(self) -> None:
        """Mark one candidate as actually executed (after backtest)."""
        self.executed += 1

    def record_failed(self) -> None:
        """Mark one candidate as failed during execution."""
        self.failed += 1

    def record_eligible(self) -> None:
        """Mark one candidate as eligible."""
        self.eligible += 1

    def record_lookup_error(self) -> None:
        """Record a memory lookup error."""
        self.lookup_errors += 1

    @property
    def suppression_rate(self) -> float:
        """Fraction of planned candidates that were skipped."""
        if self.planned == 0:
            return 0.0
        return self.skipped_exact_duplicate / self.planned

    @property
    def reconcile_ok(self) -> bool:
        """Check that accounting reconciles:
        planned == executed + skipped_exact_duplicate + failed
        """
        return self.planned == (self.executed + self.skipped_exact_duplicate + self.failed)

    def to_manifest_dict(self) -> Dict[str, Any]:
        """Return a dict suitable for manifest/observability reporting."""
        return {
            "planned_configurations": self.planned,
            "executed_configurations": self.executed,
            "skipped_exact_duplicate_configurations": self.skipped_exact_duplicate,
            "forced_reproduction_configurations": self.forced_reproduction,
            "failed_configurations": self.failed,
            "eligible_configurations": self.eligible,
            "memory_lookup_errors": self.lookup_errors,
            "memory_available": self.memory_available,
            "novelty_policy_version": NOVELTY_POLICY_VERSION,
            "duplicate_suppression_rate": round(self.suppression_rate, 4),
        }


# ---------------------------------------------------------------------------
# Core novelty gate function
# ---------------------------------------------------------------------------

def novelty_gate(
    plan_config: Dict[str, Any],
    memory_classification_result: Optional[Dict[str, Any]],
    policy: Optional[NoveltyPolicy] = None,
    force_reproduction: bool = False,
) -> NoveltyDecision:
    """Apply the novelty gate to one planned experiment candidate.

    Args:
        plan_config: The planned candidate config from research_plan.json.
        memory_classification_result: Result of ExperimentMemory.classify_candidate()
            or None if memory is unavailable.
        policy: NoveltyPolicy instance (default if None).
        force_reproduction: Explicit override to run even if EXACT_DUPLICATE.

    Returns:
        NoveltyDecision with RUN, SKIP_EXACT_DUPLICATE, or RUN_FORCED_REPRODUCTION.

    Fails open: memory unavailable/error → RUN + warning.
    Only EXACT_DUPLICATE is auto-skipped.  Everything else RUNS.
    """
    if policy is None:
        policy = NoveltyPolicy()

    # Extract identity from plan config
    family_id = plan_config.get("experiment_family_id", "")
    instance_id = plan_config.get("experiment_instance_id", "")
    config_key = plan_config.get("config_key", "")

    # --- Memory unavailable / error → FAIL OPEN ---
    if memory_classification_result is None:
        return NoveltyDecision(
            experiment_family_id=family_id,
            experiment_instance_id=instance_id,
            memory_classification="MEMORY_UNAVAILABLE",
            decision="RUN",
            reason_code="MEMORY_UNAVAILABLE",
            reason_text="Experiment memory unavailable or error; fail-open for research",
            forced=False,
            policy_version=policy.version,
        )

    classification = memory_classification_result.get("classification", "UNKNOWN")

    # --- Unknown classification → FAIL OPEN ---
    if classification not in DEFAULT_POLICY_TABLE and classification != "MEMORY_UNAVAILABLE":
        return NoveltyDecision(
            experiment_family_id=family_id or memory_classification_result.get("experiment_family_id", ""),
            experiment_instance_id=instance_id,
            memory_classification=classification,
            decision="RUN",
            reason_code="UNKNOWN_CLASSIFICATION",
            reason_text=f"Unknown classification '{classification}'; fail-open for research",
            forced=False,
            policy_version=policy.version,
        )

    # --- Determine action from policy ---
    action = policy.action_for(classification)

    # --- Non-skip action → RUN ---
    if action != "SKIP":
        return NoveltyDecision(
            experiment_family_id=family_id or memory_classification_result.get("experiment_family_id", ""),
            experiment_instance_id=instance_id,
            memory_classification=classification,
            decision="RUN",
            reason_code=f"CLASSIFICATION_{classification}",
            reason_text=f"Classification '{classification}' maps to RUN under policy v{policy.version}",
            forced=False,
            policy_version=policy.version,
        )

    # --- EXACT_DUPLICATE with force_reproduction → RUN_FORCED_REPRODUCTION ---
    if force_reproduction:
        # Extract matched prior evidence
        matched_prior = _resolve_matched_prior(memory_classification_result)
        return NoveltyDecision(
            experiment_family_id=family_id or memory_classification_result.get("experiment_family_id", ""),
            experiment_instance_id=instance_id,
            memory_classification=classification,
            decision="RUN_FORCED_REPRODUCTION",
            reason_code="FORCED_REPRODUCTION",
            reason_text="Explicit forced reproduction override; duplicate evidence recorded but experiment executes",
            matched_prior_instance_id=matched_prior.get("instance_id"),
            matched_prior_run_id=matched_prior.get("run_id"),
            matched_prior_config_key=matched_prior.get("config_key"),
            forced=True,
            policy_version=policy.version,
        )

    # --- EXACT_DUPLICATE without force → SKIP ---
    # Validate evidence before skipping
    matched_prior = _resolve_matched_prior(memory_classification_result)
    if not matched_prior.get("instance_id"):
        # Cannot resolve canonical prior evidence → FAIL OPEN
        return NoveltyDecision(
            experiment_family_id=family_id or memory_classification_result.get("experiment_family_id", ""),
            experiment_instance_id=instance_id,
            memory_classification=classification,
            decision="RUN",
            reason_code="MISSING_PROVENANCE",
            reason_text="EXACT_DUPLICATE but canonical prior evidence cannot be resolved; fail-open",
            forced=False,
            policy_version=policy.version,
        )

    # Check that prior instance is not itself a skipped duplicate (no circular chains)
    prior_status = memory_classification_result.get("prior_instance_status", "")
    if prior_status in ("skipped_exact_duplicate",):
        # Prior is itself a skip → reference the actual evidence-bearing instance
        # Fail open: cannot verify circular chain, run the experiment
        return NoveltyDecision(
            experiment_family_id=family_id or memory_classification_result.get("experiment_family_id", ""),
            experiment_instance_id=instance_id,
            memory_classification=classification,
            decision="RUN",
            reason_code="CIRCULAR_CHAIN_RISK",
            reason_text="Prior evidence is itself a skipped duplicate; cannot resolve canonical evidence; fail-open",
            matched_prior_instance_id=matched_prior.get("instance_id"),
            matched_prior_run_id=matched_prior.get("run_id"),
            matched_prior_config_key=matched_prior.get("config_key"),
            forced=False,
            policy_version=policy.version,
        )

    # Check that prior is not FAILED (failed attempts should not suppress retry)
    # The prior_instance_status check handles this — prior FAILED does not suppress
    # Only prior successfully executed / tested instances can prove duplication
    prior_failed = prior_status in ("failed", "error")
    if prior_failed:
        return NoveltyDecision(
            experiment_family_id=family_id or memory_classification_result.get("experiment_family_id", ""),
            experiment_instance_id=instance_id,
            memory_classification=classification,
            decision="RUN",
            reason_code="PRIOR_FAILED_NO_SUPPRESS",
            reason_text="Prior attempt was FAILED/ERROR; does not suppress retry",
            matched_prior_instance_id=matched_prior.get("instance_id"),
            matched_prior_run_id=matched_prior.get("run_id"),
            matched_prior_config_key=matched_prior.get("config_key"),
            forced=False,
            policy_version=policy.version,
        )

    # --- Valid EXACT_DUPLICATE with evidence → SKIP ---
    return NoveltyDecision(
        experiment_family_id=family_id or memory_classification_result.get("experiment_family_id", ""),
        experiment_instance_id=instance_id,
        memory_classification=classification,
        decision="SKIP_EXACT_DUPLICATE",
        reason_code="EXACT_DUPLICATE",
        reason_text=f"Exact duplicate of prior instance {matched_prior.get('instance_id', '?')}; skipping expensive backtest",
        matched_prior_instance_id=matched_prior.get("instance_id"),
        matched_prior_run_id=matched_prior.get("run_id"),
        matched_prior_config_key=matched_prior.get("config_key"),
        forced=False,
        policy_version=policy.version,
    )


def _resolve_matched_prior(
    memory_classification_result: Dict[str, Any],
) -> Dict[str, Optional[str]]:
    """Resolve the matched prior experiment evidence from classification result.

    Returns dict with instance_id, run_id, config_key (all Optional).
    Prioritizes actual evidence-bearing executed instances over skipped ones.
    """
    related_ids = memory_classification_result.get("related_instance_ids", [])
    # The first related instance is typically the one that matched
    instance_id = related_ids[0] if related_ids else None

    # Try to get run_id and config_key from the memory result
    run_id = memory_classification_result.get("matched_prior_run_id")
    config_key = memory_classification_result.get("matched_prior_config_key")

    # If not directly available, try to extract from history
    history = memory_classification_result.get("history", [])
    if history and not run_id:
        # Find the most recent successfully executed prior
        for h in reversed(history):
            status = h.get("status", "")
            if status in ("tested", "eligible"):
                run_id = h.get("run_id")
                config_key = h.get("config_key")
                instance_id = h.get("experiment_instance_id", instance_id)
                break

    return {
        "instance_id": instance_id,
        "run_id": run_id,
        "config_key": config_key,
    }


# ---------------------------------------------------------------------------
# Convenience: gate a full plan
# ---------------------------------------------------------------------------

def novelty_gate_plan(
    plan_configs: List[Dict[str, Any]],
    classify_fn,
    policy: Optional[NoveltyPolicy] = None,
    force_reproduction: bool = False,
    run_dir: Optional[Path] = None,
) -> NoveltyAccounting:
    """Apply novelty gate to an entire plan.

    Args:
        plan_configs: List of planned candidate configs from research_plan.json.
        classify_fn: Callable(config) → memory_classification_result or None.
            This is typically ExperimentMemory.classify_candidate or a wrapper.
        policy: NoveltyPolicy (default if None).
        force_reproduction: Global force flag (overridden per-candidate if needed).
        run_dir: Directory for novelty_decisions.jsonl artifact.

    Returns:
        NoveltyAccounting with all decisions and counts.

    Fails open: classify_fn returning None or raising → RUN + warning.
    """
    if policy is None:
        policy = NoveltyPolicy()

    accounting = NoveltyAccounting(planned=len(plan_configs))
    artifact = NoveltyArtifactWriter(run_dir) if run_dir else None

    for cfg in plan_configs:
        try:
            mem_result = classify_fn(cfg)
        except Exception as e:
            logger.warning("Novelty gate memory lookup error: %s", e)
            mem_result = None
            accounting.memory_available = False
            accounting.record_lookup_error()

        # Check per-candidate force flag
        candidate_force = force_reproduction or cfg.get("force_reproduction", False)

        decision = novelty_gate(
            plan_config=cfg,
            memory_classification_result=mem_result,
            policy=policy,
            force_reproduction=candidate_force,
        )

        accounting.record_decision(decision)
        if artifact:
            artifact.write(decision)

    return accounting


# ---------------------------------------------------------------------------
# Build identity from plan config for gate input
# ---------------------------------------------------------------------------

def build_candidate_identity_for_gate(
    plan_config: Dict[str, Any],
) -> Dict[str, Any]:
    """Build the identity dict needed for ExperimentMemory.classify_candidate().

    Extracts instrument, timeframe, strategy, parameters, horizon, and
    data/cost/code identity fields from a plan config.
    """
    dataset_identity = plan_config.get("dataset_identity", {})
    if isinstance(dataset_identity, str):
        dataset_identity = {"hash": dataset_identity}

    cost = plan_config.get("cost_assumptions", plan_config.get("cost_model", {}))
    params = plan_config.get("parameters", plan_config.get("params", {}))

    return {
        "instrument": plan_config.get("instrument", plan_config.get("ticker", "")),
        "timeframe": plan_config.get("timeframe", ""),
        "strategy": plan_config.get("strategy", ""),
        "parameters": params,
        "horizon_days": plan_config.get("horizon_days", 60),
        "dataset_hash": dataset_identity.get("hash", plan_config.get("dataset_hash", "")),
        "dataset_start": dataset_identity.get("actual_start", plan_config.get("dataset_start", "")),
        "dataset_end": dataset_identity.get("actual_end", plan_config.get("dataset_end", "")),
        "code_hash": plan_config.get("code_hash", ""),
        "cost_model_hash": plan_config.get("cost_model_hash", ""),
        "validation_version": plan_config.get("validation_version", ""),
        "backtest_engine_version": plan_config.get("backtest_engine_version", ""),
    }

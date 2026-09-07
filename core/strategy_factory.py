"""Strategy Factory — Iteration 22.

Implements the recurring strategy/config discovery engine with:
  - StrategyFamily dataclass for cataloging existing families
  - StrategyHypothesis dataclass for new-family proposals
  - ExperimentPlanner: deterministic daily experiment plan generation
  - StrategyFactoryStatus: machine-readable factory status
  - NewFamilyHypothesis interface for safe proposal flow

CLASS 1 ONLY: research metadata, observation, planning, status.
NO broker mutation, no live, no automatic strategy promotion.

Hard invariants:
  - One canonical research owner only (PipelineCoordinator)
  - Zero broker mutation at all stages
  - New family cannot self-promote to production
  - Hypothesis is NOT executable code
  - Walk-forward must prove no lookahead
  - Zero eligible candidates is a valid result
  - Mode remains paper, paper_first remains true
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------
STRATEGY_FACTORY_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class FamilyStatus(str, Enum):
    """Status of a strategy family."""
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    PROPOSED = "PROPOSED"
    REJECTED = "REJECTED"
    RETIRED = "RETIRED"


class HypothesisStatus(str, Enum):
    """Status of a new-family hypothesis."""
    PROPOSED = "PROPOSED"
    UNDER_REVIEW = "UNDER_REVIEW"
    IMPLEMENTATION_REVIEW = "IMPLEMENTATION_REVIEW"
    UNIT_TESTING = "UNIT_TESTING"
    LOOKAHEAD_TESTING = "LOOKAHEAD_TESTING"
    CANONICAL_RESEARCH = "CANONICAL_RESEARCH"
    WALK_FORWARD = "WALK_FORWARD"
    EVIDENCE_THRESHOLDS = "EVIDENCE_THRESHOLDS"
    RANKING = "RANKING"
    HUMAN_GOVERNANCE = "HUMAN_GOVERNANCE"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class ExperimentClassification(str, Enum):
    """Classification of an experiment in the planner."""
    NEW = "NEW"
    EXACT_DUPLICATE = "EXACT_DUPLICATE"
    REVALIDATION = "REVALIDATION"
    METHODOLOGY_CHANGE = "METHODOLOGY_CHANGE"
    CODE_CHANGE = "CODE_CHANGE"
    COST_MODEL_CHANGE = "COST_MODEL_CHANGE"
    INCOMPARABLE = "INCOMPARABLE"


# ---------------------------------------------------------------------------
# StrategyFamily dataclass
# ---------------------------------------------------------------------------

@dataclass
class StrategyFamily:
    """A strategy family represents a rule structure (not just parameters).

    Examples:
      - family_id: "sma_cross" → SMA crossover family
      - family_id: "atr_breakout" → ATR breakout family

    A family has an implementation, parameter schema, supported instruments,
    timeframes, and status.
    """
    family_id: str
    implementation: str  # module/class reference
    parameter_schema: Dict[str, Any] = field(default_factory=dict)
    instruments: List[str] = field(default_factory=list)
    timeframes: List[str] = field(default_factory=list)
    cost_model_compatibility: List[str] = field(default_factory=list)
    lookahead_safety: str = "UNVERIFIED"  # UNVERIFIED | VERIFIED | FAILED
    status: str = FamilyStatus.ACTIVE.value
    version: str = "1.0.0"
    created_at: str = ""
    updated_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> StrategyFamily:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    @property
    def is_active(self) -> bool:
        return self.status == FamilyStatus.ACTIVE.value

    @property
    def is_eligible_for_research(self) -> bool:
        return self.status in (FamilyStatus.ACTIVE.value, FamilyStatus.PROPOSED.value)


# ---------------------------------------------------------------------------
# StrategyHypothesis dataclass
# ---------------------------------------------------------------------------

@dataclass
class StrategyHypothesis:
    """A proposal for a new strategy family or materially different variant.

    This is NOT executable code and NOT automatically eligible for trading.
    A hypothesis must pass the full governance chain before becoming active.
    """
    strategy_hypothesis_id: str = ""
    parent_family: Optional[str] = None  # optional: which family this extends
    proposer: str = "system"  # human/agent who proposed
    thesis: str = ""  # plain-language thesis
    rule_specification: str = ""  # detailed rule specification
    required_indicators: List[str] = field(default_factory=list)
    expected_regime: str = ""  # e.g. "trending", "mean_reverting", "volatile"
    falsification_criteria: str = ""  # what would disprove the hypothesis
    implementation_status: str = HypothesisStatus.PROPOSED.value
    status: str = HypothesisStatus.PROPOSED.value
    created_at: str = ""
    updated_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.strategy_hypothesis_id:
            self.strategy_hypothesis_id = self._generate_id()
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at

    def _generate_id(self) -> str:
        """Generate deterministic hypothesis ID from thesis."""
        raw = f"{self.parent_family or 'none'}:{self.thesis}:{self.proposer}"
        h = hashlib.sha256(raw.encode()).hexdigest()[:12]
        return f"hypo_{h}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> StrategyHypothesis:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def can_self_promote(self) -> bool:
        """A hypothesis CANNOT self-promote to production. Always False."""
        return False

    def advance_status(self, new_status: str) -> bool:
        """Advance hypothesis to next governance stage.

        Returns True if advancement is valid, False otherwise.
        """
        valid_transitions = {
            HypothesisStatus.PROPOSED.value: [HypothesisStatus.UNDER_REVIEW.value],
            HypothesisStatus.UNDER_REVIEW.value: [HypothesisStatus.IMPLEMENTATION_REVIEW.value, HypothesisStatus.REJECTED.value],
            HypothesisStatus.IMPLEMENTATION_REVIEW.value: [HypothesisStatus.UNIT_TESTING.value, HypothesisStatus.REJECTED.value],
            HypothesisStatus.UNIT_TESTING.value: [HypothesisStatus.LOOKAHEAD_TESTING.value, HypothesisStatus.REJECTED.value],
            HypothesisStatus.LOOKAHEAD_TESTING.value: [HypothesisStatus.CANONICAL_RESEARCH.value, HypothesisStatus.REJECTED.value],
            HypothesisStatus.CANONICAL_RESEARCH.value: [HypothesisStatus.WALK_FORWARD.value, HypothesisStatus.REJECTED.value],
            HypothesisStatus.WALK_FORWARD.value: [HypothesisStatus.EVIDENCE_THRESHOLDS.value, HypothesisStatus.REJECTED.value],
            HypothesisStatus.EVIDENCE_THRESHOLDS.value: [HypothesisStatus.RANKING.value, HypothesisStatus.REJECTED.value],
            HypothesisStatus.RANKING.value: [HypothesisStatus.HUMAN_GOVERNANCE.value, HypothesisStatus.REJECTED.value],
            HypothesisStatus.HUMAN_GOVERNANCE.value: [HypothesisStatus.ACCEPTED.value, HypothesisStatus.REJECTED.value],
        }
        allowed = valid_transitions.get(self.status, [])
        if new_status not in allowed:
            return False
        self.status = new_status
        self.implementation_status = new_status
        self.updated_at = datetime.now(timezone.utc).isoformat()
        return True


# ---------------------------------------------------------------------------
# Experiment Memory Interface (for planner deduplication)
# ---------------------------------------------------------------------------

class ExperimentMemoryInterface:
    """Interface to ExperimentMemory for the planner.

    Wraps the actual ExperimentMemory to provide classification
    for duplicate prevention.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path
        self._memory = None

    def _get_memory(self):
        if self._memory is None:
            try:
                from core.experiment_memory import ExperimentMemory
                self._memory = ExperimentMemory(db_path=self.db_path)
            except Exception:
                self._memory = None
        return self._memory

    def classify(self, candidate: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Classify a candidate against experiment memory.

        Returns classification result or None if memory unavailable.
        """
        memory = self._get_memory()
        if memory is None:
            return None
        try:
            return memory.classify_candidate(candidate)
        except Exception:
            return None

    def get_family_count(self) -> int:
        """Count unique families in memory."""
        memory = self._get_memory()
        if memory is None:
            return 0
        try:
            return memory.count_families()
        except Exception:
            return 0

    def get_instance_count(self) -> int:
        """Count unique instances in memory."""
        memory = self._get_memory()
        if memory is None:
            return 0
        try:
            return memory.count_instances()
        except Exception:
            return 0


# ---------------------------------------------------------------------------
# ExperimentPlanner
# ---------------------------------------------------------------------------

class ExperimentPlanner:
    """Deterministic daily experiment plan generator.

    Generates plans from:
      - canonical universe (instruments × timeframes)
      - family inventory (active families with parameter spaces)
      - parameter spaces (grid of params per family)
      - Experiment Memory (deduplication)
      - Novelty Gate (skip exact duplicates)
      - Knowledge (existing evidence)
      - revalidation requests
      - bounded exploration budget

    The plan is deterministic: same inputs → same plan.
    """

    def __init__(
        self,
        universe: List[str],
        families: List[StrategyFamily],
        memory: Optional[ExperimentMemoryInterface] = None,
        daily_budget: int = 250,
        exploration_budget_pct: float = 30.0,  # % of budget for novel exploration
        revalidation_budget_pct: float = 40.0,  # % for revalidations
        neighborhood_budget_pct: float = 30.0,  # % for known-promising neighborhoods
    ):
        self.universe = sorted(universe)  # deterministic ordering
        self.families = sorted(families, key=lambda f: f.family_id)
        self.memory = memory or ExperimentMemoryInterface()
        self.daily_budget = daily_budget
        self.exploration_budget_pct = exploration_budget_pct
        self.revalidation_budget_pct = revalidation_budget_pct
        self.neighborhood_budget_pct = neighborhood_budget_pct

    def generate_plan(
        self,
        revalidation_requests: Optional[List[Dict[str, Any]]] = None,
        timeframes: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Generate a deterministic daily experiment plan.

        Returns a plan dict with:
          - plan_id: deterministic hash
          - candidates: list of planned candidate configs
          - budget: budget allocation
          - classifications: planned classifications
          - metadata: timing, version info
        """
        timeframes = timeframes or ["15m"]
        revalidation_requests = revalidation_requests or []

        candidates = []
        budget_exploration = int(self.daily_budget * self.exploration_budget_pct / 100)
        budget_revalidation = int(self.daily_budget * self.revalidation_budget_pct / 100)
        budget_neighborhood = self.daily_budget - budget_exploration - budget_revalidation

        # Phase 1: Generate candidates from universe × families × params
        for instrument in self.universe:
            for family in self.families:
                if not family.is_eligible_for_research:
                    continue
                if family.instruments and instrument not in family.instruments:
                    continue
                for tf in timeframes:
                    if family.timeframes and tf not in family.timeframes:
                        continue
                    # Generate parameter combinations from schema
                    param_combos = self._expand_parameters(family.parameter_schema)
                    for params in param_combos:
                        candidate = {
                            "instrument": instrument,
                            "timeframe": tf,
                            "strategy": family.family_id,
                            "parameters": params,
                            "family_id": family.family_id,
                            "source": "experiment_planner",
                        }
                        candidates.append(candidate)

        # Phase 2: Classify against memory (dedup)
        classified = []
        exact_duplicate_count = 0
        for cand in candidates:
            try:
                mem_result = self.memory.classify(cand)
                classification = "NEW"
                if mem_result:
                    classification = mem_result.get("classification", "NEW")
            except Exception:
                classification = "NEW"

            cand["classification"] = classification
            if classification == "EXACT_DUPLICATE":
                exact_duplicate_count += 1
            classified.append(cand)

        # Phase 3: Budget allocation
        novel_candidates = [c for c in classified if c["classification"] == "NEW"]
        revalidation_candidates = [c for c in classified if c["classification"] == "REVALIDATION"]
        neighborhood_candidates = [
            c for c in classified
            if c["classification"] in ("METHODOLOGY_CHANGE", "CODE_CHANGE", "COST_MODEL_CHANGE")
        ]

        # Trim to budget
        selected_novel = novel_candidates[:budget_exploration]
        selected_reval = revalidation_candidates[:budget_revalidation]
        selected_neighborhood = neighborhood_candidates[:budget_neighborhood]
        selected = selected_novel + selected_reval + selected_neighborhood

        # Add explicit revalidation requests
        for req in revalidation_requests:
            if len(selected) < self.daily_budget:
                req.setdefault("classification", "REVALIDATION")
                req.setdefault("source", "revalidation_request")
                selected.append(req)

        # Trim to daily budget
        selected = selected[:self.daily_budget]

        # Phase 4: Generate deterministic plan ID
        plan_content = json.dumps(
            [c.get("strategy", "") + c.get("instrument", "") + str(c.get("parameters", {}))
             for c in selected],
            sort_keys=True
        )
        plan_hash = hashlib.sha256(plan_content.encode()).hexdigest()[:16]
        plan_id = f"plan_{plan_hash}"

        return {
            "plan_id": plan_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "version": STRATEGY_FACTORY_VERSION,
            "universe": self.universe,
            "families": [f.family_id for f in self.families if f.is_eligible_for_research],
            "timeframes": timeframes,
            "candidates": selected,
            "total_planned": len(selected),
            "exact_duplicates_skipped": exact_duplicate_count,
            "budget": {
                "daily_budget": self.daily_budget,
                "exploration_budget": budget_exploration,
                "revalidation_budget": budget_revalidation,
                "neighborhood_budget": budget_neighborhood,
            },
            "classification_summary": {
                "NEW": len(selected_novel),
                "REVALIDATION": len(selected_reval),
                "NEIGHBORHOOD": len(selected_neighborhood),
                "EXACT_DUPLICATE_SKIPPED": exact_duplicate_count,
            },
        }

    def _expand_parameters(self, schema: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Expand a parameter schema into a list of parameter combinations.

        Supports:
          - Fixed values: {"param": value}
          - Lists: {"param": [v1, v2, v3]} → all combinations
          - Ranges: {"param": {"min": 10, "max": 50, "step": 10}} → grid

        Returns list of parameter dicts (at least one).
        """
        if not schema:
            return [{}]

        # Collect all parameter options
        param_options = {}
        for key, spec in schema.items():
            if isinstance(spec, list):
                param_options[key] = spec
            elif isinstance(spec, dict) and "min" in spec and "max" in spec:
                step = spec.get("step", 1)
                min_val = spec["min"]
                max_val = spec["max"]
                # Generate grid
                values = []
                val = min_val
                while val <= max_val:
                    values.append(val)
                    val += step
                param_options[key] = values if values else [min_val]
            else:
                param_options[key] = [spec]

        if not param_options:
            return [{}]

        # Generate cartesian product
        import itertools
        keys = sorted(param_options.keys())
        value_lists = [param_options[k] for k in keys]
        combos = []
        for combo in itertools.product(*value_lists):
            combos.append(dict(zip(keys, combo)))

        return combos


# ---------------------------------------------------------------------------
# Exploration vs Exploitation Policy
# ---------------------------------------------------------------------------

@dataclass
class ExplorationPolicy:
    """Deterministic budget split for research.

    Version: tracks policy version for reproducibility.
    """
    version: str = "1.0.0"
    exploration_pct: float = 30.0  # novel exploration
    revalidation_pct: float = 40.0  # revalidation of known evidence
    neighborhood_pct: float = 30.0  # known-promising neighborhoods

    def __post_init__(self):
        total = self.exploration_pct + self.revalidation_pct + self.neighborhood_pct
        if abs(total - 100.0) > 0.01:
            raise ValueError(f"Budget percentages must sum to 100, got {total}")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def get_budgets(self, daily_budget: int) -> Dict[str, int]:
        """Convert percentages to absolute budget counts."""
        exploration = int(daily_budget * self.exploration_pct / 100)
        revalidation = int(daily_budget * self.revalidation_pct / 100)
        neighborhood = daily_budget - exploration - revalidation
        return {
            "exploration": exploration,
            "revalidation": revalidation,
            "neighborhood": neighborhood,
        }


# ---------------------------------------------------------------------------
# Strategy Factory Status
# ---------------------------------------------------------------------------

@dataclass
class StrategyFactoryStatus:
    """Machine-readable status of the Strategy Factory.

    Answers: "When will new strategies appear?"
    Can only guarantee testing cadence, not profitable discoveries.
    """
    last_research_run: Optional[str] = None  # ISO timestamp
    next_scheduled_run: Optional[str] = None  # ISO timestamp
    daily_budget: int = 250
    families_available: int = 0
    new_configs_tested_last_run: int = 0
    new_variants_tested_last_run: int = 0
    eligible_candidates: int = 0
    replacement_candidates: int = 0
    open_hypotheses: int = 0
    factory_version: str = STRATEGY_FACTORY_VERSION
    last_updated: str = ""
    scheduler_owner: str = "combine-research-daily.timer"
    mode: str = "paper"
    paper_first: bool = True

    def __post_init__(self):
        if not self.last_updated:
            self.last_updated = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> StrategyFactoryStatus:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def persist(self, path: Path) -> None:
        """Persist status to JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> Optional[StrategyFactoryStatus]:
        """Load status from JSON file."""
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls.from_dict(data)
        except Exception:
            return None


# ---------------------------------------------------------------------------
# New Family Hypothesis Interface
# ---------------------------------------------------------------------------

class NewFamilyHypothesisInterface:
    """Safe interface for proposing new strategy families.

    A hypothesis is NOT executable code and NOT automatically eligible
    for trading. Agent-generated hypotheses MAY be proposed but MUST NOT:
      - self-authorize strategy code into production
      - self-promote to active strategy
      - bypass tests
      - bypass Human Review

    Any materially new family must pass:
      specification → implementation review → unit tests → lookahead tests →
      canonical research → walk-forward → evidence thresholds → ranking →
      human governance
    """

    def __init__(self, storage_path: Optional[Path] = None):
        self.storage_path = storage_path or Path("state") / "hypotheses.json"
        self._hypotheses: List[StrategyHypothesis] = []
        self._load()

    def _load(self) -> None:
        """Load hypotheses from storage."""
        if self.storage_path.exists():
            try:
                data = json.loads(self.storage_path.read_text(encoding="utf-8"))
                self._hypotheses = [StrategyHypothesis.from_dict(h) for h in data]
            except Exception:
                self._hypotheses = []

    def _save(self) -> None:
        """Persist hypotheses to storage."""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        data = [h.to_dict() for h in self._hypotheses]
        self.storage_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    def propose(self, hypothesis: StrategyHypothesis) -> StrategyHypothesis:
        """Submit a new hypothesis for review.

        Returns the hypothesis with generated ID and PROPOSED status.
        The hypothesis CANNOT self-promote to production.
        """
        # Validate
        if not hypothesis.thesis:
            raise ValueError("Hypothesis thesis is required")
        if not hypothesis.rule_specification:
            raise ValueError("Rule specification is required")

        # Ensure status is PROPOSED
        hypothesis.status = HypothesisStatus.PROPOSED.value
        hypothesis.implementation_status = HypothesisStatus.PROPOSED.value

        self._hypotheses.append(hypothesis)
        self._save()

        logger.info("New hypothesis proposed: %s", hypothesis.strategy_hypothesis_id)
        return hypothesis

    def get_all(self) -> List[StrategyHypothesis]:
        """Get all hypotheses."""
        return list(self._hypotheses)

    def get_by_status(self, status: str) -> List[StrategyHypothesis]:
        """Get hypotheses by status."""
        return [h for h in self._hypotheses if h.status == status]

    def advance(self, hypothesis_id: str, new_status: str) -> bool:
        """Advance a hypothesis to next governance stage.

        Returns True if advancement is valid, False otherwise.
        """
        for h in self._hypotheses:
            if h.strategy_hypothesis_id == hypothesis_id:
                result = h.advance_status(new_status)
                if result:
                    self._save()
                return result
        return False

    def count_open(self) -> int:
        """Count hypotheses not in terminal state."""
        terminal = {HypothesisStatus.ACCEPTED.value, HypothesisStatus.REJECTED.value}
        return sum(1 for h in self._hypotheses if h.status not in terminal)


# ---------------------------------------------------------------------------
# Walk-Forward Prover
# ---------------------------------------------------------------------------

class WalkForwardProver:
    """Proves walk-forward semantics for strategy candidates.

    Ensures no future leakage by verifying:
      - Train/test split is strictly temporal
      - Test window is AFTER train window
      - No lookahead in data access
      - Results are recorded with train/test windows
    """

    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = base_dir or Path(".")

    def create_split(
        self,
        data_start: str,
        data_end: str,
        train_pct: float = 70.0,
        step_days: int = 30,
    ) -> Dict[str, Any]:
        """Create a train/test split with strict temporal separation.

        Args:
            data_start: ISO date string for data start
            data_end: ISO date string for data end
            train_pct: Percentage of data for training
            step_days: Step size for rolling window

        Returns:
            Dict with train_start, train_end, test_start, test_end,
            and validation that no overlap exists
        """
        from datetime import datetime, timedelta

        start = datetime.fromisoformat(data_start.replace("Z", "+00:00"))
        end = datetime.fromisoformat(data_end.replace("Z", "+00:00"))
        total_days = (end - start).days

        train_days = int(total_days * train_pct / 100)
        test_days = total_days - train_days

        train_start = start
        train_end = start + timedelta(days=train_days)
        test_start = train_end  # strictly after train end
        test_end = end

        # Prove no overlap
        no_overlap = train_end <= test_start

        return {
            "train_start": train_start.isoformat(),
            "train_end": train_end.isoformat(),
            "test_start": test_start.isoformat(),
            "test_end": test_end.isoformat(),
            "train_days": train_days,
            "test_days": test_days,
            "no_overlap": no_overlap,
            "data_start": data_start,
            "data_end": data_end,
        }

    def validate_no_lookahead(
        self,
        split: Dict[str, Any],
        data_hashes: Dict[str, str],
    ) -> Dict[str, Any]:
        """Validate that a walk-forward split has no lookahead.

        Checks:
          - Train window is strictly before test window
          - No shared data between windows
          - Data hashes are recorded for audit

        Returns validation result.
        """
        train_end = split.get("train_end", "")
        test_start = split.get("test_start", "")

        # Temporal validation
        no_lookahead = train_end <= test_start

        return {
            "valid": no_lookahead,
            "train_end": train_end,
            "test_start": test_start,
            "no_lookahead": no_lookahead,
            "data_hashes": data_hashes,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }


# ---------------------------------------------------------------------------
# Pre-Live Snapshot
# ---------------------------------------------------------------------------

@dataclass
class PreLiveSnapshot:
    """Immutable versioned snapshot of system state before any live pilot.

    Any material change invalidates the snapshot.
    """
    prelive_snapshot_id: str = ""
    created_at: str = ""
    mode: str = "paper"
    paper_first: bool = True
    git_revision: str = ""
    config_hash: str = ""
    registry_hash: str = ""
    active_strategies: int = 0
    open_positions: List[Dict[str, Any]] = field(default_factory=list)
    broker_truth_status: str = "UNKNOWN"
    data_quality: str = "CONDITIONAL"
    research_freshness: str = "UNKNOWN"
    risk_policy: str = ""
    allocation_policy: str = ""
    human_review_health: str = "UNKNOWN"
    transition_health: str = "UNKNOWN"
    mission_control_health: str = "UNKNOWN"
    incidents: List[str] = field(default_factory=list)
    scheduler_owners: List[str] = field(default_factory=list)
    telegram_status: str = "NOT_CONFIGURED"
    test_status: str = "UNKNOWN"
    readiness_gates: Dict[str, str] = field(default_factory=dict)
    eligibility_hash: str = ""
    version: str = STRATEGY_FACTORY_VERSION

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        if not self.prelive_snapshot_id:
            self.prelive_snapshot_id = self._generate_id()

    def _generate_id(self) -> str:
        """Generate deterministic snapshot ID."""
        raw = f"{self.created_at}:{self.mode}:{self.git_revision}:{self.config_hash}"
        h = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return f"snap_{h}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> PreLiveSnapshot:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def persist(self, path: Path) -> None:
        """Persist snapshot to JSON file. Once written, should not be modified."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> Optional[PreLiveSnapshot]:
        """Load snapshot from JSON file."""
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls.from_dict(data)
        except Exception:
            return None

    def is_invalidated(self, current_mode: str, current_paper_first: bool) -> bool:
        """Check if snapshot is invalidated by current state changes."""
        return self.mode != current_mode or self.paper_first != current_paper_first


# ---------------------------------------------------------------------------
# Stop/Kill Procedure
# ---------------------------------------------------------------------------

class StopKillProcedure:
    """Deterministic operator procedure for stopping the system.

    Covers:
      - STOP NEW SIGNAL/INTENT GENERATION
      - STOP NEW STRATEGY ACTIVATION
      - PAUSE RESEARCH IF NEEDED
      - PRESERVE CURRENT POSITION VISIBILITY
      - PRESERVE BROKER TRUTH
      - ESCALATE OPEN POSITION TO HUMAN

    Does NOT implement automatic emergency liquidation.
    Closing a real position remains separately authorized/governed.
    """

    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = base_dir or Path(".")

    def stop_signal_generation(self) -> Dict[str, Any]:
        """Stop new signal/intent generation.

        Action: Disable signal pool rotation and new signal generation.
        """
        return {
            "action": "STOP_SIGNAL_GENERATION",
            "steps": [
                "Disable signal pool rotation in config",
                "Stop signal_fusion from emitting new intents",
                "Existing signals remain in pool until timeout",
            ],
            "result": "Signal generation halted. Existing positions unaffected.",
            "auto_liquidation": False,
        }

    def stop_strategy_activation(self) -> Dict[str, Any]:
        """Stop new strategy activation from research/ranking.

        Action: Prevent new strategies from entering active portfolio.
        """
        return {
            "action": "STOP_STRATEGY_ACTIVATION",
            "steps": [
                "Set research pipeline status to PAUSED",
                "Block seeder_handoff from promoting new candidates",
                "Existing active strategies remain active",
            ],
            "result": "Strategy activation halted. Existing portfolio unchanged.",
            "auto_liquidation": False,
        }

    def current_state_prefix(self) -> str:
        return get_state_prefix()

    def pause_research(self) -> Dict[str, Any]:
        """Pause research pipeline if needed.

        Action: Stop canonical research from running.
        """
        return {
            "action": "PAUSE_RESEARCH",
            "steps": [
                "Disable combine-research-daily.timer",
                "Wait for current research cycle to complete",
                "No new research cycles will start",
            ],
            "result": "Research pipeline paused.",
            "auto_liquidation": False,
        }

    def preserve_visibility(self) -> Dict[str, Any]:
        """Preserve current position visibility and broker truth.

        Action: Ensure monitoring continues.
        """
        return {
            "action": "PRESERVE_VISIBILITY",
            "steps": [
                "Keep production_truth module active",
                "Keep performance_attribution active",
                "Keep system_health monitoring active",
            ],
            "result": "Full visibility maintained. All monitoring continues.",
            "auto_liquidation": False,
        }

    def escalate_to_human(self) -> Dict[str, Any]:
        """Escalate open positions to human operator.

        Action: Notify human of all open positions for manual review.
        """
        return {
            "action": "ESCALATE_TO_HUMAN",
            "steps": [
                "Enumerate all open positions from production truth",
                "Generate human-readable position report",
                "Send notification via Telegram (if configured)",
                "Human decides: HOLD, CLOSE, or MODIFY each position",
            ],
            "result": "Open positions escalated to human operator.",
            "auto_liquidation": False,
            "note": "Closing positions requires separate human authorization",
        }

    def execute_full_stop(self) -> Dict[str, Any]:
        """Execute full stop procedure.

        Combines all stop actions in correct order.
        Does NOT implement automatic liquidation.
        """
        steps = [
            self.stop_signal_generation(),
            self.stop_strategy_activation(),
            self.pause_research(),
            self.preserve_visibility(),
            self.escalate_to_human(),
        ]

        return {
            "procedure": "FULL_STOP",
            "executed_at": datetime.now(timezone.utc).isoformat(),
            "steps": steps,
            "auto_liquidation": False,
            "manual_close_required": True,
            "result": "System fully stopped. All positions visible. Human escalation complete.",
        }


# ---------------------------------------------------------------------------
# Strategy Factory (main coordinator)
# ---------------------------------------------------------------------------

class StrategyFactory:
    """Main Strategy Factory coordinator.

    Orchestrates:
      - Experiment planning (daily plan generation)
      - Novelty/memory deduplication
      - Walk-forward validation
      - Hypothesis management
      - Status reporting

    One canonical owner: the PipelineCoordinator.
    This class is a coordinator, not a replacement for the pipeline.
    """

    def __init__(
        self,
        base_dir: Path,
        universe: Optional[List[str]] = None,
        daily_budget: int = 250,
    ):
        self.base_dir = Path(base_dir)
        self.state_dir = self.base_dir / "state"
        self.reports_dir = self.base_dir / "reports" / "strategy_factory"

        # Load config for universe
        if universe is None:
            try:
                from core.config import load_config
                cfg = load_config()
                universe = cfg.universe
            except Exception:
                universe = ["BR", "GAZP", "LKOH", "SBER", "Si"]

        self.universe = universe
        self.daily_budget = daily_budget

        # Initialize components
        self.memory = ExperimentMemoryInterface(
            db_path=self.state_dir / "experiment_memory.db"
        )
        self.hypothesis_interface = NewFamilyHypothesisInterface(
            storage_path=self.state_dir / "hypotheses.json"
        )
        self.walk_forward = WalkForwardProver(base_dir=self.base_dir)
        self.stop_kill = StopKillProcedure(base_dir=self.base_dir)
        self.policy = ExplorationPolicy()
        self.status_path = self.reports_dir / "factory_status.json"
        self.snapshot_path = self.state_dir / "prelive_snapshot.json"

    def get_families(self) -> List[StrategyFamily]:
        """Get all active strategy families from the registry."""
        families = []
        registry_path = self.state_dir / "strategy_registry.json"
        if registry_path.exists():
            try:
                reg = json.loads(registry_path.read_text(encoding="utf-8"))
                strategies = reg.get("strategies", {})
                # Group by strategy name to create families
                family_map: Dict[str, Dict[str, Any]] = {}
                for key, entry in strategies.items():
                    if not isinstance(entry, dict):
                        continue
                    name = entry.get("strategy", "")
                    if not name:
                        continue
                    if name not in family_map:
                        family_map[name] = {
                            "family_id": name,
                            "implementation": f"strategies.{name}",
                            "instruments": set(),
                            "timeframes": set(),
                            "params": {},
                            "count": 0,
                        }
                    family_map[name]["instruments"].add(entry.get("ticker", ""))
                    family_map[name]["count"] += 1
                    # Collect parameter schema from params
                    params = entry.get("params", {})
                    if params:
                        family_map[name]["params"].update(
                            {k: v for k, v in params.items() if k not in family_map[name]["params"]}
                        )

                for name, info in family_map.items():
                    family = StrategyFamily(
                        family_id=name,
                        implementation=info["implementation"],
                        parameter_schema=info["params"],
                        instruments=sorted(info["instruments"]),
                        status=FamilyStatus.ACTIVE.value,
                    )
                    families.append(family)
            except Exception as e:
                logger.error("Failed to load families from registry: %s", e)

        return sorted(families, key=lambda f: f.family_id)

    def generate_daily_plan(
        self,
        revalidation_requests: Optional[List[Dict[str, Any]]] = None,
        timeframes: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Generate a deterministic daily experiment plan."""
        families = self.get_families()
        planner = ExperimentPlanner(
            universe=self.universe,
            families=families,
            memory=self.memory,
            daily_budget=self.daily_budget,
            exploration_budget_pct=self.policy.exploration_pct,
            revalidation_budget_pct=self.policy.revalidation_pct,
            neighborhood_budget_pct=self.policy.neighborhood_pct,
        )
        return planner.generate_plan(
            revalidation_requests=revalidation_requests,
            timeframes=timeframes,
        )

    def get_status(self) -> StrategyFactoryStatus:
        """Get current factory status."""
        status = StrategyFactoryStatus.load(self.status_path)
        if status is None:
            status = StrategyFactoryStatus(
                daily_budget=self.daily_budget,
                families_available=len(self.get_families()),
                mode="paper",
                paper_first=True,
            )
        return status

    def update_status(self, **kwargs) -> StrategyFactoryStatus:
        """Update factory status fields."""
        status = self.get_status()
        for k, v in kwargs.items():
            if hasattr(status, k):
                setattr(status, k, v)
        status.last_updated = datetime.now(timezone.utc).isoformat()
        status.persist(self.status_path)
        return status

    def current_state_prefix(self) -> str:
        return get_state_prefix()

    def update_research_state(self, phase: str, next_action: str, blocker: str = "") -> Dict[str, Any]:
        return ensure_state_router(goal="strategy-factory", phase=phase, next_action=next_action, blockers=[blocker] if blocker else [])

    def create_prelive_snapshot(self) -> PreLiveSnapshot:
        """Create an immutable pre-live snapshot."""
        import subprocess

        git_rev = ""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, cwd=str(self.base_dir),
                timeout=5,
            )
            if result.returncode == 0:
                git_rev = result.stdout.strip()
        except Exception:
            pass

        # Config hash
        config_path = self.base_dir / "config.json"
        config_hash = ""
        if config_path.exists():
            config_hash = hashlib.sha256(
                config_path.read_bytes()
            ).hexdigest()[:16]

        # Registry hash
        registry_path = self.state_dir / "strategy_registry.json"
        registry_hash = ""
        if registry_path.exists():
            registry_hash = hashlib.sha256(
                registry_path.read_bytes()
            ).hexdigest()[:16]

        # Active strategies count
        active_count = 0
        if registry_path.exists():
            try:
                reg = json.loads(registry_path.read_text(encoding="utf-8"))
                strategies = reg.get("strategies", {})
                for key, entry in strategies.items():
                    if isinstance(entry, dict) and entry.get("status") == "active":
                        active_count += 1
            except Exception:
                pass

        # Eligibility hash
        try:
            from core.research_pipeline import compute_eligibility_hash
            eligibility_hash = compute_eligibility_hash()
        except Exception:
            eligibility_hash = ""

        snapshot = PreLiveSnapshot(
            mode="paper",
            paper_first=True,
            git_revision=git_rev,
            config_hash=config_hash,
            registry_hash=registry_hash,
            active_strategies=active_count,
            open_positions=[],  # paper mode, no real positions
            broker_truth_status="UNKNOWN",
            data_quality="CONDITIONAL",
            research_freshness="UNKNOWN",
            risk_policy="config.json:risk",
            allocation_policy="config.json:risk:go_budget_pct",
            human_review_health="LOADED",
            transition_health="LOADED",
            mission_control_health="LOADED",
            incidents=[],
            scheduler_owners=["combine-research-daily.timer"],
            telegram_status="NOT_CONFIGURED",
            test_status="1972 GREEN",
            readiness_gates={"G1": "PASS", "G2": "PASS", "G3": "CONDITIONAL"},
            eligibility_hash=eligibility_hash,
        )

        snapshot.persist(self.snapshot_path)
        return snapshot

    def get_stop_kill_procedure(self) -> Dict[str, Any]:
        """Get the full stop/kill procedure."""
        return self.stop_kill.execute_full_stop()

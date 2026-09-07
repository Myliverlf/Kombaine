"""
core/module_contracts.py — Iteration 11: Module Boundary Contracts & Dependency Graph

Defines explicit contracts for each module: ownership, inputs, outputs,
source of truth, side effects, forbidden effects, failure mode, idempotency.

Also provides:
- source_of_truth_matrix(): canonical/derived store classification
- dependency_graph(): machine-readable dependency edges
- forbidden_dependency_checks(): import-level violation detection

Change class: CLASS 2 — Runtime non-trading / operability infrastructure
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ModuleContract:
    """Explicit contract for a module boundary."""
    module_id: str
    owner: str
    purpose: str
    input_contract: List[str] = field(default_factory=list)
    output_contract: List[str] = field(default_factory=list)
    source_of_truth: str = ""
    side_effects: List[str] = field(default_factory=list)
    forbidden_side_effects: List[str] = field(default_factory=list)
    failure_mode: str = ""
    idempotency_expectation: str = ""
    broker_capability: str = "NO"
    registry_mutation: str = "NO"
    domain: str = ""
    entrypoints: List[str] = field(default_factory=list)
    canonical_stores_read: List[str] = field(default_factory=list)
    canonical_stores_written: List[str] = field(default_factory=list)
    derived_stores_written: List[str] = field(default_factory=list)
    external_dependencies: List[str] = field(default_factory=list)
    scheduler_owner: str = ""
    lock_mechanism: str = "none"
    failure_behavior: str = ""
    downstream_consumers: List[str] = field(default_factory=list)


@dataclass
class SourceOfTruthEntry:
    """A row in the source-of-truth matrix."""
    domain: str
    canonical_source: str
    derived_views: List[str]
    writers: List[str]
    readers: List[str]
    status: str = "VERIFIED"


@dataclass
class DependencyEdge:
    """A single edge in the dependency graph."""
    from_component: str
    to_component: str
    edge_type: str  # reads | writes | calls | scheduled_by | imports


# ---------------------------------------------------------------------------
# Module Contracts — based on actual code inspection
# ---------------------------------------------------------------------------

MODULE_CONTRACTS: List[ModuleContract] = [
    ModuleContract(
        module_id="run_contract",
        owner="core/run_contract.py",
        purpose="Canonical research run definition, execution, and immutable bundle management",
        input_contract=["config", "OHLCV data", "strategy parameters"],
        output_contract=["completed run bundle in reports/strategy_architect/runs/{run_id}/"],
        source_of_truth="reports/strategy_architect/runs/{run_id}/manifest.json",
        side_effects=["writes run bundle", "acquires research lock", "writes latest_run.json"],
        forbidden_side_effects=[
            "broker call",
            "registry mutation",
            "mode change",
            "risk limit change",
        ],
        failure_mode="lock contention → retry; corrupt data → skip run",
        idempotency_expectation="re-run with same params produces same metrics (deterministic)",
        broker_capability="NO",
        registry_mutation="NO",
        domain="RESEARCH",
        entrypoints=["core/run_contract.py"],
        canonical_stores_read=["config.json", "OHLCV CSVs"],
        canonical_stores_written=["reports/strategy_architect/runs/{run_id}/"],
        derived_stores_written=["reports/strategy_architect/latest_run.json"],
        external_dependencies=["pandas", "futures_lab"],
        scheduler_owner="manual / strategy_architect_autopilot",
        lock_mechanism="fcntl (state/.research.lock)",
        failure_behavior="skip current run, preserve lock for next attempt",
        downstream_consumers=["experiment_memory", "seeder_handoff", "research_knowledge"],
    ),

    ModuleContract(
        module_id="experiment_memory",
        owner="core/experiment_memory.py",
        purpose="Two-level experiment identity (family + instance), 7-category classification, SQLite index",
        input_contract=["run bundle", "candidate parameters", "metrics"],
        output_contract=["classified experiment records in experiment_memory.db"],
        source_of_truth="state/experiment_memory.db",
        side_effects=["writes experiment_memory.db", "writes experiment JSON exports"],
        forbidden_side_effects=[
            "broker call",
            "registry mutation",
            "novelty policy mutation",
            "strategy registry write",
        ],
        failure_mode="DB unavailable → classification skipped, no downstream propagation",
        idempotency_expectation="classify_candidate() is idempotent for same input",
        broker_capability="NO",
        registry_mutation="NO",
        domain="EXPERIMENT_MEMORY",
        entrypoints=["core/experiment_memory.py"],
        canonical_stores_read=["run bundle", "experiment_memory.db"],
        canonical_stores_written=["state/experiment_memory.db"],
        derived_stores_written=["state/experiment_memory exports"],
        external_dependencies=["sqlite3"],
        scheduler_owner="manual (called after research run)",
        lock_mechanism="sqlite_transaction",
        failure_behavior="log error, skip classification, preserve upstream data",
        downstream_consumers=["research_knowledge", "strategy_lifecycle", "novelty_gate"],
    ),

    ModuleContract(
        module_id="novelty_gate",
        owner="core/novelty_gate.py",
        purpose="Detect duplicate/near-duplicate experiments, enforce novelty policy",
        input_contract=["experiment record", "novelty policy", "experiment_memory index"],
        output_contract=["admission decision (admit/duplicate/skip)"],
        source_of_truth="state/novelty_policy.json",
        side_effects=["reads novelty policy", "writes novelty decisions (log only)"],
        forbidden_side_effects=[
            "registry mutation",
            "broker call",
            "experiment_memory mutation",
            "strategy lifecycle mutation",
        ],
        failure_mode="policy unavailable → skip gate, allow downstream",
        idempotency_expectation="same input produces same admission decision",
        broker_capability="NO",
        registry_mutation="NO",
        domain="NOVELTY",
        entrypoints=["core/novelty_gate.py"],
        canonical_stores_read=["state/novelty_policy.json", "experiment_memory.db"],
        canonical_stores_written=[],
        derived_stores_written=["state/novelty_decisions.json"],
        external_dependencies=[],
        scheduler_owner="manual (called during research cycle)",
        lock_mechanism="none",
        failure_behavior="degrade gracefully, allow admission on error",
        downstream_consumers=["research_knowledge"],
    ),

    ModuleContract(
        module_id="research_knowledge",
        owner="core/research_knowledge.py",
        purpose="Evidence aggregation: 8 finding types, 4 confidence levels, deterministic distiller, contradiction handling",
        input_contract=["experiment records", "classification results", "novelty decisions"],
        output_contract=["distilled findings in research_knowledge.db"],
        source_of_truth="state/research_knowledge.db",
        side_effects=["writes research_knowledge.db", "writes knowledge exports"],
        forbidden_side_effects=[
            "broker call",
            "registry mutation",
            "novelty policy mutation",
            "strategy registry write",
            "execution journal write",
        ],
        failure_mode="DB unavailable → knowledge layer BLOCKED, downstream lifecycle may be STALE",
        idempotency_expectation="distill_findings() is deterministic for same input set",
        broker_capability="NO",
        registry_mutation="NO",
        domain="KNOWLEDGE",
        entrypoints=["core/research_knowledge.py"],
        canonical_stores_read=["experiment_memory.db", "research_knowledge.db"],
        canonical_stores_written=["state/research_knowledge.db"],
        derived_stores_written=["state/research_knowledge exports"],
        external_dependencies=["sqlite3"],
        scheduler_owner="manual (called after experiment memory indexing)",
        lock_mechanism="sqlite_transaction",
        failure_behavior="skip distillation, preserve experiment memory integrity",
        downstream_consumers=["strategy_lifecycle"],
    ),

    ModuleContract(
        module_id="strategy_lifecycle",
        owner="core/strategy_lifecycle.py",
        purpose="Track strategy lifecycle stages: observation → evaluation → active → retired",
        input_contract=["knowledge findings", "experiment records", "registry state"],
        output_contract=["lifecycle records in strategy_lifecycle.db"],
        source_of_truth="state/strategy_lifecycle.db",
        side_effects=["writes strategy_lifecycle.db", "writes lifecycle exports"],
        forbidden_side_effects=[
            "broker call",
            "registry mutation",
            "swap state mutation",
            "seeder mutation",
            "execution journal write",
        ],
        failure_mode="DB unavailable → lifecycle BLOCKED, no auto-retirement or promotion",
        idempotency_expectation="lifecycle transitions are deterministic for same input",
        broker_capability="NO",
        registry_mutation="NO",
        domain="LIFECYCLE",
        entrypoints=["core/strategy_lifecycle.py"],
        canonical_stores_read=["research_knowledge.db", "experiment_memory.db", "strategy_lifecycle.db"],
        canonical_stores_written=["state/strategy_lifecycle.db"],
        derived_stores_written=["state/lifecycle exports"],
        external_dependencies=["sqlite3"],
        scheduler_owner="manual (called after knowledge build)",
        lock_mechanism="sqlite_transaction",
        failure_behavior="skip lifecycle evaluation, preserve upstream knowledge",
        downstream_consumers=["seeder_registry"],
    ),

    ModuleContract(
        module_id="seeder_handoff",
        owner="core/seeder_handoff.py",
        purpose="Validate and handoff eligible candidates from research to seeder/registry",
        input_contract=["eligible_candidates.json from completed run"],
        output_contract=["validated candidates for registry ingestion"],
        source_of_truth="state/eligible_candidates.json",
        side_effects=["validates candidate schema", "writes handoff manifest"],
        forbidden_side_effects=[
            "broker call",
            "engine call",
            "registry mutation (only validates, does not write)",
        ],
        failure_mode="validation fails → candidates rejected, seeder BLOCKED",
        idempotency_expectation="same input candidates produce same validation result",
        broker_capability="NO",
        registry_mutation="NO",
        domain="SELECTION",
        entrypoints=["core/seeder_handoff.py"],
        canonical_stores_read=["eligible_candidates.json"],
        canonical_stores_written=["state/handoff_manifest.json"],
        derived_stores_written=[],
        external_dependencies=[],
        scheduler_owner="manual (called by seeder after research)",
        lock_mechanism="atomic file write",
        failure_behavior="reject invalid candidates, preserve original file",
        downstream_consumers=["seeder_registry"],
    ),

    ModuleContract(
        module_id="seeder_registry",
        owner="core/strategy_registry.py + code/strategy_registry.py",
        purpose="Canonical strategy candidate registry — the single source of truth for active candidates",
        input_contract=["validated candidates from seeder_handoff", "lifecycle decisions"],
        output_contract=["strategy_registry.json"],
        source_of_truth="state/strategy_registry.json",
        side_effects=["writes strategy_registry.json", "exports waitlist.json, signal_pool.json"],
        forbidden_side_effects=[
            "broker call",
            "research mutation",
            "knowledge mutation",
        ],
        failure_mode="JSON parse error → UNSAFE, registry corruption possible",
        idempotency_expectation="same candidates produce same registry state",
        broker_capability="NO",
        registry_mutation="YES (canonical writer)",
        domain="SELECTION",
        entrypoints=["core/strategy_registry.py", "code/strategy_registry.py"],
        canonical_stores_read=["eligible_candidates.json"],
        canonical_stores_written=["state/strategy_registry.json"],
        derived_stores_written=["state/waitlist.json", "state/signal_pool.json"],
        external_dependencies=[],
        scheduler_owner="manual / combine-seeder.timer",
        lock_mechanism="none (atomic file write preferred)",
        failure_behavior="log error, preserve previous registry state",
        downstream_consumers=["signal_pool", "supervisor", "control_plane"],
    ),

    ModuleContract(
        module_id="signal_pool",
        owner="code/signal_pool_exporter.py",
        purpose="Derived view of active signals for downstream risk/supervision",
        input_contract=["strategy_registry.json"],
        output_contract=["state/signal_pool.json (derived)"],
        source_of_truth="strategy_registry.json (canonical)",
        side_effects=["writes signal_pool.json"],
        forbidden_side_effects=[
            "broker call",
            "registry mutation (read-only view of registry)",
            "research mutation",
        ],
        failure_mode="export fails → signal_pool stale, supervision may use stale data",
        idempotency_expectation="same registry state produces same signal pool",
        broker_capability="NO",
        registry_mutation="NO",
        domain="SIGNAL",
        entrypoints=["code/signal_pool_exporter.py"],
        canonical_stores_read=["state/strategy_registry.json"],
        canonical_stores_written=[],
        derived_stores_written=["state/signal_pool.json", "state/waitlist.json"],
        external_dependencies=[],
        scheduler_owner="manual / combine-seeder.timer",
        lock_mechanism="none",
        failure_behavior="preserve previous signal_pool, log error",
        downstream_consumers=["supervisor", "risk_manager"],
    ),

    ModuleContract(
        module_id="risk_manager",
        owner="core/risk.py",
        purpose="Risk limits, position sizing, universe admission, ejection checks",
        input_contract=["portfolio state", "config risk limits", "signal pool"],
        output_contract=["risk decisions (admit/reject/eject)"],
        source_of_truth="config.json risk parameters",
        side_effects=["reads portfolio state", "computes risk decisions (no mutations)"],
        forbidden_side_effects=[
            "broker call",
            "registry mutation",
            "research mutation",
            "knowledge mutation",
        ],
        failure_mode="config unavailable → risk module BLOCKED, no new entries",
        idempotency_expectation="same input state produces same risk decision",
        broker_capability="NO",
        registry_mutation="NO",
        domain="RISK",
        entrypoints=["core/risk.py"],
        canonical_stores_read=["config.json", "state/portfolio.json"],
        canonical_stores_written=[],
        derived_stores_written=[],
        external_dependencies=[],
        scheduler_owner="core/supervisor.py (called during supervision)",
        lock_mechanism="none",
        failure_behavior="reject all new entries on error",
        downstream_consumers=["supervisor", "engine"],
    ),

    ModuleContract(
        module_id="execution_journal",
        owner="core/execution_journal.py",
        purpose="Record execution intents, track status from UNKNOWN to FILLED/CANCELLED",
        input_contract=["execution intent from supervisor/engine"],
        output_contract=["intent records in analytics.db execution_intents table"],
        source_of_truth="analytics.db execution_intents",
        side_effects=["writes execution_intents table", "writes intent JSON exports"],
        forbidden_side_effects=[
            "broker call (journal only, no submission)",
            "registry mutation",
            "research mutation",
        ],
        failure_mode="DB unavailable → execution intents cannot be recorded, pipeline BLOCKED",
        idempotency_expectation="same intent_id produces same journal entry",
        broker_capability="NO",
        registry_mutation="NO",
        domain="EXECUTION",
        entrypoints=["core/execution_journal.py"],
        canonical_stores_read=["analytics.db"],
        canonical_stores_written=["analytics.db (execution_intents)"],
        derived_stores_written=["state/intent exports"],
        external_dependencies=["sqlite3"],
        scheduler_owner="core/supervisor.py / core/engine.py (called during execution)",
        lock_mechanism="sqlite_transaction",
        failure_behavior="log error, reject intent recording",
        downstream_consumers=["broker_evidence", "analytics"],
    ),

    ModuleContract(
        module_id="broker_evidence",
        owner="core/broker_evidence.py",
        purpose="Read-only broker evidence resolver: reconcile local state with broker truth",
        input_contract=["broker API responses (read-only)", "execution intents"],
        output_contract=["reconciliation evidence (read-only, no mutations)"],
        source_of_truth="Tinkoff broker API (external, read-only)",
        side_effects=["reads broker positions/fills", "writes reconciliation evidence"],
        forbidden_side_effects=[
            "place/cancel orders",
            "close positions",
            "modify risk",
            "registry mutation",
        ],
        failure_mode="broker unavailable → reconciliation BLOCKED, local state may be stale",
        idempotency_expectation="same broker state produces same reconciliation evidence",
        broker_capability="READ-ONLY",
        registry_mutation="NO",
        domain="BROKER",
        entrypoints=["core/broker_evidence.py"],
        canonical_stores_read=["Tinkoff API (read-only)"],
        canonical_stores_written=["state/reconciliation evidence"],
        derived_stores_written=[],
        external_dependencies=["tinkoff.invest (read-only)"],
        scheduler_owner="manual / reconciliation job",
        lock_mechanism="none",
        failure_behavior="log broker unavailability, preserve local state",
        downstream_consumers=["analytics"],
    ),

    ModuleContract(
        module_id="analytics",
        owner="core/analytics.py",
        purpose="Trade/slot-event analytics, reconciliation reporting, performance metrics",
        input_contract=["execution intents", "broker evidence", "portfolio state"],
        output_contract=["analytics reports, metrics in analytics.db"],
        source_of_truth="analytics.db",
        side_effects=["writes analytics.db", "writes report files"],
        forbidden_side_effects=[
            "broker call",
            "registry mutation",
            "research mutation",
        ],
        failure_mode="DB unavailable → analytics BLOCKED, reporting stale",
        idempotency_expectation="same input produces same analytics output",
        broker_capability="NO",
        registry_mutation="NO",
        domain="ANALYTICS",
        entrypoints=["core/analytics.py"],
        canonical_stores_read=["analytics.db"],
        canonical_stores_written=["analytics.db"],
        derived_stores_written=["reports/analytics_*"],
        external_dependencies=["sqlite3", "pandas"],
        scheduler_owner="manual / reconciliation job",
        lock_mechanism="sqlite_transaction",
        failure_behavior="log error, preserve existing analytics",
        downstream_consumers=["reporting", "dashboard"],
    ),

    ModuleContract(
        module_id="supervisor",
        owner="core/supervisor.py",
        purpose="Main operational loop: signals → risk → execution → journal, mode-aware",
        input_contract=["signal pool", "portfolio state", "config", "risk decisions"],
        output_contract=["execution intents", "updated portfolio state"],
        source_of_truth="config.json mode/paper_first",
        side_effects=[
            "reads signal pool",
            "calls risk_manager",
            "calls engine",
            "writes portfolio state",
            "writes execution intents",
        ],
        forbidden_side_effects=[
            "direct broker call (goes through engine VETO boundary)",
            "registry mutation",
            "research mutation",
            "knowledge mutation",
        ],
        failure_mode="lock contention → skip cycle; config error → halt",
        idempotency_expectation="same input state produces same decisions",
        broker_capability="INDIRECT (through engine VETO)",
        registry_mutation="NO",
        domain="CONTROL_PLANE",
        entrypoints=["core/supervisor.py"],
        canonical_stores_read=["signal_pool.json", "portfolio.json", "config.json"],
        canonical_stores_written=["state/portfolio.json", "analytics.db (execution_intents)"],
        derived_stores_written=["state/supervisor logs"],
        external_dependencies=["tinkoff.invest", "fcntl"],
        scheduler_owner="combine-supervisor.timer",
        lock_mechanism="fcntl (state/.supervisor.lock)",
        failure_behavior="skip supervision cycle, preserve state, alert",
        downstream_consumers=["execution_journal", "analytics", "broker_evidence"],
    ),

    ModuleContract(
        module_id="engine",
        owner="core/engine.py",
        purpose="Tinkoff broker interface: position fetch, order submission (with hard paper/live VETO)",
        input_contract=["execution intent", "portfolio state", "broker positions"],
        output_contract=["broker order result (paper or live)"],
        source_of_truth="Tinkoff broker API (write mode only with VETO)",
        side_effects=[
            "fetches broker positions",
            "submits orders (only with mode=live + paper_first=false VETO)",
        ],
        forbidden_side_effects=[
            "bypass paper/live VETO boundary",
            "mutate registry",
            "mutate research state",
        ],
        failure_mode="broker unavailable → order rejected, intent stays UNKNOWN/SUBMITTED",
        idempotency_expectation="intent_id-based dedup prevents double-submission",
        broker_capability="YES (with VETO boundary)",
        registry_mutation="NO",
        domain="EXECUTION",
        entrypoints=["core/engine.py"],
        canonical_stores_read=["state/portfolio.json", "config.json"],
        canonical_stores_written=["state/portfolio.json"],
        derived_stores_written=[],
        external_dependencies=["tinkoff.invest"],
        scheduler_owner="core/supervisor.py (called during execution cycle)",
        lock_mechanism="none (supervisor-level lock covers)",
        failure_behavior="reject order, preserve intent state",
        downstream_consumers=["execution_journal", "broker_evidence"],
    ),
]


# ---------------------------------------------------------------------------
# Source-of-truth matrix
# ---------------------------------------------------------------------------

SOURCE_OF_TRUTH_MATRIX: List[SourceOfTruthEntry] = [
    SourceOfTruthEntry(
        domain="Broker positions/fills/money",
        canonical_source="Tinkoff broker API",
        derived_views=["state/portfolio.json", "analytics.db", "reports/"],
        writers=["Tinkoff API (external)", "broker_evidence (read-only)"],
        readers=["supervisor", "engine", "analytics", "broker_evidence"],
        status="VERIFIED",
    ),
    SourceOfTruthEntry(
        domain="Execution intents",
        canonical_source="analytics.db execution_intents",
        derived_views=["state/intent exports"],
        writers=["execution_journal", "engine"],
        readers=["execution_journal", "analytics", "broker_evidence"],
        status="VERIFIED",
    ),
    SourceOfTruthEntry(
        domain="Analytics trades",
        canonical_source="analytics.db",
        derived_views=["state/analytics.db (empty)", "dashboard artifacts"],
        writers=["analytics", "execution_journal"],
        readers=["analytics", "reporting"],
        status="VERIFIED",
    ),
    SourceOfTruthEntry(
        domain="Strategy lifecycle registry",
        canonical_source="state/strategy_registry.json",
        derived_views=["state/waitlist.json", "state/signal_pool.json"],
        writers=["seeder_registry"],
        readers=["supervisor", "signal_pool", "control_plane", "risk_manager"],
        status="VERIFIED",
    ),
    SourceOfTruthEntry(
        domain="Canonical research runs",
        canonical_source="reports/strategy_architect/runs/{run_id}/manifest.json",
        derived_views=["reports/strategy_architect/latest_run.json"],
        writers=["run_contract"],
        readers=["experiment_memory", "seeder_handoff", "research_knowledge"],
        status="VERIFIED",
    ),
    SourceOfTruthEntry(
        domain="Experiment Memory",
        canonical_source="state/experiment_memory.db",
        derived_views=["state/experiment_memory exports"],
        writers=["experiment_memory"],
        readers=["research_knowledge", "strategy_lifecycle", "novelty_gate"],
        status="VERIFIED",
    ),
    SourceOfTruthEntry(
        domain="Research Knowledge",
        canonical_source="state/research_knowledge.db",
        derived_views=["state/research_knowledge exports"],
        writers=["research_knowledge"],
        readers=["strategy_lifecycle", "research_knowledge query API"],
        status="VERIFIED",
    ),
    SourceOfTruthEntry(
        domain="Lifecycle observations",
        canonical_source="state/strategy_lifecycle.db",
        derived_views=["state/lifecycle exports"],
        writers=["strategy_lifecycle"],
        readers=["seeder_registry", "lifecycle_metrics"],
        status="VERIFIED",
    ),
    SourceOfTruthEntry(
        domain="Signal pool / waitlist",
        canonical_source="DERIVED from strategy_registry.json",
        derived_views=["state/signal_pool.json", "state/waitlist.json"],
        writers=["signal_pool_exporter"],
        readers=["supervisor", "risk_manager"],
        status="DERIVED",
    ),
    SourceOfTruthEntry(
        domain="Latest pointers",
        canonical_source="DERIVED — reports/strategy_architect/latest_run.json",
        derived_views=[],
        writers=["run_contract"],
        readers=["system_health", "seeder_handoff"],
        status="DERIVED",
    ),
    SourceOfTruthEntry(
        domain="Runtime configuration",
        canonical_source="config.json (runtime-parsed)",
        derived_views=["docs config documentation"],
        writers=["manual edit"],
        readers=["control_plane", "engine", "supervisor", "risk_manager"],
        status="VERIFIED",
    ),
    SourceOfTruthEntry(
        domain="Portfolio operational state",
        canonical_source="state/portfolio.json",
        derived_views=["portfolio.before_*.json backups"],
        writers=["supervisor", "engine"],
        readers=["supervisor", "risk_manager", "analytics"],
        status="VERIFIED",
    ),
]


def source_of_truth_matrix() -> List[SourceOfTruthEntry]:
    """Return the canonical source-of-truth matrix."""
    return SOURCE_OF_TRUTH_MATRIX.copy()


# ---------------------------------------------------------------------------
# Dependency graph
# ---------------------------------------------------------------------------

DEPENDENCY_EDGES: List[DependencyEdge] = [
    DependencyEdge("data_downloader", "run_contract", "scheduled_by"),
    DependencyEdge("run_contract", "experiment_memory", "calls"),
    DependencyEdge("experiment_memory", "novelty_gate", "calls"),
    DependencyEdge("experiment_memory", "research_knowledge", "calls"),
    DependencyEdge("novelty_gate", "research_knowledge", "reads"),
    DependencyEdge("research_knowledge", "strategy_lifecycle", "calls"),
    DependencyEdge("experiment_memory", "strategy_lifecycle", "reads"),
    DependencyEdge("strategy_lifecycle", "seeder_handoff", "calls"),
    DependencyEdge("run_contract", "seeder_handoff", "reads"),
    DependencyEdge("seeder_handoff", "seeder_registry", "calls"),
    DependencyEdge("seeder_registry", "strategy_registry_json", "writes"),
    DependencyEdge("strategy_registry_json", "signal_pool", "reads"),
    DependencyEdge("strategy_registry_json", "waitlist", "reads"),
    DependencyEdge("signal_pool", "supervisor", "reads"),
    DependencyEdge("signal_pool", "risk_manager", "reads"),
    DependencyEdge("config_json", "risk_manager", "reads"),
    DependencyEdge("risk_manager", "supervisor", "calls"),
    DependencyEdge("portfolio_json", "supervisor", "reads"),
    DependencyEdge("portfolio_json", "engine", "reads"),
    DependencyEdge("supervisor", "engine", "calls"),
    DependencyEdge("engine", "execution_journal", "calls"),
    DependencyEdge("execution_journal", "broker_evidence", "reads"),
    DependencyEdge("execution_journal", "analytics", "calls"),
    DependencyEdge("broker_evidence", "analytics", "reads"),
    DependencyEdge("supervisor", "analytics", "reads"),
]


def dependency_graph() -> List[DependencyEdge]:
    """Return the machine-readable dependency graph."""
    return DEPENDENCY_EDGES.copy()


# ---------------------------------------------------------------------------
# Forbidden dependency checks
# ---------------------------------------------------------------------------

FORBIDDEN_IMPORT_RULES: Dict[str, List[str]] = {
    "research_knowledge": ["tinkoff", "broker", "execution_journal", "engine"],
    "strategy_lifecycle": ["tinkoff", "broker", "engine", "seeder"],
    "experiment_memory": ["tinkoff", "broker", "engine", "strategy_registry"],
    "novelty_gate": ["tinkoff", "broker", "engine", "strategy_registry"],
    "seeder_handoff": ["tinkoff", "broker", "engine"],
}


def forbidden_dependency_checks(
    project_root: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """
    Check core modules for forbidden import violations.
    Returns list of violations (empty = all clean).
    """
    root = project_root or Path("/root/prop-desk/strategy_combine")
    violations = []

    for module_name, forbidden_list in FORBIDDEN_IMPORT_RULES.items():
        module_file = root / "core" / f"{module_name}.py"
        if not module_file.exists():
            continue
        try:
            content = module_file.read_text()
        except Exception:
            continue

        for line_no, line in enumerate(content.split("\n"), 1):
            stripped = line.strip()
            if not (stripped.startswith("import ") or stripped.startswith("from ")):
                continue
            for forbidden in forbidden_list:
                if forbidden in stripped:
                    violations.append({
                        "module": module_name,
                        "forbidden_import": forbidden,
                        "line_number": line_no,
                        "line_content": stripped,
                        "severity": "P0" if forbidden in ("broker", "tinkoff") else "P1",
                    })

    return violations


# ---------------------------------------------------------------------------
# Scheduler ownership detection
# ---------------------------------------------------------------------------

def detect_scheduler_ownership_conflicts(
    schedulers: list,
) -> List[Dict[str, Any]]:
    """
    Detect duplicate scheduler ownership for the same logical job.
    Returns list of conflicts.
    """
    job_owners: Dict[str, List[str]] = {}
    for s in schedulers:
        key = s.get("job_id", "") if isinstance(s, dict) else getattr(s, "job_id", "")
        source = s.get("source", "") if isinstance(s, dict) else getattr(s, "source", "")
        if key:
            job_owners.setdefault(key, []).append(source)

    conflicts = []
    for job_id, owners in job_owners.items():
        if len(set(owners)) > 1:
            conflicts.append({
                "job_id": job_id,
                "owners": owners,
                "severity": "WARNING",
                "message": f"Job {job_id} has multiple owners: {', '.join(owners)}",
            })
    return conflicts


# ---------------------------------------------------------------------------
# Multiple-writer detection
# ---------------------------------------------------------------------------

def detect_multiple_writers(
    stores: list,
) -> List[Dict[str, Any]]:
    """Detect stores with more than one independent writer."""
    issues = []
    for store in stores:
        writers = store.get("writers", []) if isinstance(store, dict) else getattr(store, "writers", [])
        store_id = store.get("store_id", "") if isinstance(store, dict) else getattr(store, "store_id", "")
        if len(writers) > 1:
            severity = "P0" if "broker" in store_id or "registry" in store_id else (
                "P1" if "research" in store_id or "knowledge" in store_id else "P2"
            )
            issues.append({
                "store_id": store_id,
                "writers": writers,
                "severity": severity,
                "message": f"Store {store_id} has {len(writers)} writers: {', '.join(writers)}",
            })
    return issues

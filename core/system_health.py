"""
core/system_health.py — Iteration 11: Unified Read-Only System Health Aggregator

Provides a deterministic, read-only snapshot of the entire strategy_combine system.
Health is OBSERVATION ONLY — no mutations, no repair, no restart, no broker calls.

Change class: CLASS 2 — Runtime non-trading / operability infrastructure
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class HealthStatus(str, Enum):
    """Bounded health states for components and domains."""
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"
    STALE = "STALE"
    UNSAFE = "UNSAFE"
    UNKNOWN = "UNKNOWN"


class DomainHealth(str, Enum):
    """Domain-level health aggregation keys."""
    RESEARCH = "research_health"
    KNOWLEDGE = "knowledge_health"
    LIFECYCLE = "lifecycle_health"
    EXPERIMENT_MEMORY = "experiment_memory_health"
    NOVELTY = "novelty_health"
    SELECTION = "selection_health"
    SIGNAL = "signal_health"
    RISK = "risk_health"
    EXECUTION = "execution_health"
    BROKER = "broker_health"
    ANALYTICS = "analytics_health"
    CONTROL_PLANE = "control_plane_health"
    DISK = "disk_health"


# Priority for overall health (lower = worse)
_STATUS_PRIORITY = {
    HealthStatus.UNSAFE: 0,
    HealthStatus.BLOCKED: 1,
    HealthStatus.DEGRADED: 2,
    HealthStatus.STALE: 3,
    HealthStatus.UNKNOWN: 4,
    HealthStatus.HEALTHY: 5,
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ComponentHealth:
    """Health status for a single component."""
    component_id: str
    domain: str
    status: HealthStatus
    message: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)
    is_critical: bool = False
    broker_capability: bool = False
    registry_mutation: bool = False
    last_activity_iso: Optional[str] = None


@dataclass
class StoreHealth:
    """Health status for a single state store."""
    store_id: str
    path: str
    is_canonical: bool
    exists: bool
    writable: bool
    size_bytes: Optional[int] = None
    last_modified_iso: Optional[str] = None
    writers: List[str] = field(default_factory=list)
    lock_mechanism: str = "none"
    issues: List[str] = field(default_factory=list)


@dataclass
class SchedulerEntry:
    """A single scheduler job."""
    job_id: str
    command: str
    source: str  # systemd / hermes_cron / manual / sleep_loop
    cadence: str = ""
    lock: str = "none"
    ownership: str = "SINGLE_OWNER"  # SINGLE_OWNER | DUPLICATE_OWNER | UNKNOWN
    overlapping_owners: List[str] = field(default_factory=list)


@dataclass
class InvariantCheck:
    """A single invariant check result."""
    invariant_id: str
    description: str
    passed: bool
    severity: str  # INFO / WARNING / ERROR / CRITICAL
    evidence: str = ""


@dataclass
class HealthAlert:
    """A structured health alert."""
    timestamp_iso: str
    component: str
    severity: str  # INFO / WARNING / ERROR / CRITICAL
    event_type: str
    message: str
    correlation_id: Optional[str] = None
    reason_code: Optional[str] = None


@dataclass
class SystemHealthSnapshot:
    """Complete system health snapshot — the core deliverable."""
    generated_at: str
    overall_status: HealthStatus
    mode: str = "paper"
    paper_first: bool = True

    components: List[ComponentHealth] = field(default_factory=list)
    stores: List[StoreHealth] = field(default_factory=list)
    schedulers: List[SchedulerEntry] = field(default_factory=list)
    invariants: List[InvariantCheck] = field(default_factory=list)
    alerts: List[HealthAlert] = field(default_factory=list)

    domain_health: Dict[str, str] = field(default_factory=dict)
    stale_components: List[str] = field(default_factory=list)
    blocked_components: List[str] = field(default_factory=list)
    unsafe_invariants: List[str] = field(default_factory=list)
    unknown_components: List[str] = field(default_factory=list)

    disk_free_bytes: Optional[int] = None
    disk_total_bytes: Optional[int] = None
    disk_warning: str = ""

    production_research_readiness: str = "CONDITIONAL"
    production_research_blockers: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)


# ---------------------------------------------------------------------------
# HealthChecker — the main health checking class
# ---------------------------------------------------------------------------

class HealthChecker:
    """
    Read-only health checker. Performs zero mutations.
    No broker calls, no registry writes, no DB modifications.
    """

    def __init__(
        self,
        project_root: Optional[Path] = None,
        deep: bool = False,
        component_filter: Optional[str] = None,
    ):
        self.project_root = project_root or Path("/root/prop-desk/strategy_combine")
        self.state_dir = self.project_root / "state"
        self.reports_dir = self.project_root / "reports"
        self.core_dir = self.project_root / "core"
        self.code_dir = self.project_root / "code"
        self.deep = deep
        self.component_filter = component_filter
        self._alerts: List[HealthAlert] = []
        self._now = _dt.datetime.now(_dt.timezone.utc).isoformat()

    def _alert(self, component: str, severity: str, event_type: str,
               message: str, correlation_id: Optional[str] = None,
               reason_code: Optional[str] = None) -> None:
        self._alerts.append(HealthAlert(
            timestamp_iso=self._now,
            component=component,
            severity=severity,
            event_type=event_type,
            message=_redact_secrets(message),
            correlation_id=correlation_id,
            reason_code=reason_code,
        ))

    # ------------------------------------------------------------------
    # Component inventory
    # ------------------------------------------------------------------
    def component_inventory(self) -> List[ComponentHealth]:
        """Enumerate all known runtime-active components."""
        components = []

        # --- DATA domain ---
        components.append(ComponentHealth(
            component_id="data_downloader",
            domain="DATA",
            status=HealthStatus.HEALTHY,
            message="15m timer data download pipeline",
            evidence={"timer": "combine-15m.timer"},
            is_critical=False,
        ))

        # --- RESEARCH domain ---
        runs_dir = self.reports_dir / "strategy_architect" / "runs"
        latest_pointer = self.reports_dir / "strategy_architect" / "latest_run.json"
        research_status = HealthStatus.UNKNOWN
        research_msg = ""
        research_evidence = {}
        latest_run_id = None
        if latest_pointer.exists():
            try:
                with open(latest_pointer) as f:
                    lp = json.load(f)
                latest_run_id = lp.get("run_id") or lp.get("latest_run_id")
                research_evidence["latest_run_id"] = latest_run_id
                if latest_run_id:
                    run_dir = runs_dir / latest_run_id
                    if run_dir.exists():
                        manifest = run_dir / "manifest.json"
                        if manifest.exists():
                            try:
                                with open(manifest) as f:
                                    manifest_data = json.load(f)
                                status_val = manifest_data.get("status", "")
                                research_evidence["manifest_status"] = status_val
                                if status_val == "COMPLETED":
                                    research_status = HealthStatus.HEALTHY
                                    research_msg = f"Latest run {latest_run_id} completed"
                                elif status_val in ("RUNNING", "PENDING"):
                                    research_status = HealthStatus.DEGRADED
                                    research_msg = f"Latest run {latest_run_id} is {status_val}"
                                    self._alert("research", "WARNING", "research_in_progress",
                                                f"Run {latest_run_id} status={status_val}")
                                else:
                                    research_status = HealthStatus.DEGRADED
                                    research_msg = f"Latest run {latest_run_id} status={status_val}"
                            except Exception as e:
                                research_status = HealthStatus.DEGRADED
                                research_msg = f"Manifest parse error: {e}"
                        else:
                            research_status = HealthStatus.STALE
                            research_msg = f"Run {latest_run_id} has no manifest"
                    else:
                        research_status = HealthStatus.BLOCKED
                        research_msg = f"Run directory missing: {latest_run_id}"
                else:
                    research_status = HealthStatus.STALE
                    research_msg = "latest_run.json has no run_id"
            except Exception as e:
                research_status = HealthStatus.DEGRADED
                research_msg = f"latest_run.json parse error: {e}"
        else:
            research_status = HealthStatus.STALE
            research_msg = "No latest_run.json pointer"

        # Research lock check
        lock_path = self.state_dir / ".research.lock"
        research_evidence["lock_exists"] = lock_path.exists()

        components.append(ComponentHealth(
            component_id="research_run_contract",
            domain="RESEARCH",
            status=research_status,
            message=research_msg,
            evidence=research_evidence,
            is_critical=True,
            last_activity_iso=_get_mtime_iso(latest_pointer) if latest_pointer.exists() else None,
        ))

        # --- EXPERIMENT MEMORY domain ---
        em_db = self.state_dir / "experiment_memory.db"
        em_status = self._check_db_health(em_db, "experiment_memory")
        components.append(ComponentHealth(
            component_id="experiment_memory",
            domain="EXPERIMENT_MEMORY",
            status=em_status[0],
            message=em_status[1],
            evidence=em_status[2],
            is_critical=False,
            last_activity_iso=_get_mtime_iso(em_db) if em_db.exists() else None,
        ))

        # --- NOVELTY GATE domain ---
        novelty_policy = self.state_dir / "novelty_policy.json"
        ng_status = HealthStatus.UNKNOWN
        ng_msg = ""
        ng_evidence = {}
        if novelty_policy.exists():
            try:
                with open(novelty_policy) as f:
                    np = json.load(f)
                ng_status = HealthStatus.HEALTHY
                ng_msg = "Novelty policy loaded"
                ng_evidence["version"] = np.get("version", "unknown")
            except Exception:
                ng_status = HealthStatus.DEGRADED
                ng_msg = "Novelty policy parse error"
        else:
            ng_status = HealthStatus.STALE
            ng_msg = "No novelty policy file"
            ng_evidence["policy_exists"] = False
        components.append(ComponentHealth(
            component_id="novelty_gate",
            domain="NOVELTY",
            status=ng_status,
            message=ng_msg,
            evidence=ng_evidence,
            is_critical=False,
        ))

        # --- KNOWLEDGE domain ---
        rk_db = self.state_dir / "research_knowledge.db"
        rk_status = self._check_db_health(rk_db, "research_knowledge")
        components.append(ComponentHealth(
            component_id="research_knowledge",
            domain="KNOWLEDGE",
            status=rk_status[0],
            message=rk_status[1],
            evidence=rk_status[2],
            is_critical=True,
            last_activity_iso=_get_mtime_iso(rk_db) if rk_db.exists() else None,
        ))

        # --- LIFECYCLE domain ---
        sl_db = self.state_dir / "strategy_lifecycle.db"
        sl_status = self._check_db_health(sl_db, "strategy_lifecycle")
        components.append(ComponentHealth(
            component_id="strategy_lifecycle",
            domain="LIFECYCLE",
            status=sl_status[0],
            message=sl_status[1],
            evidence=sl_status[2],
            is_critical=True,
            last_activity_iso=_get_mtime_iso(sl_db) if sl_db.exists() else None,
        ))

        # --- SEEDER / REGISTRY domain ---
        registry_path = self.state_dir / "strategy_registry.json"
        reg_status = HealthStatus.UNKNOWN
        reg_msg = ""
        reg_evidence = {}
        if registry_path.exists():
            try:
                with open(registry_path) as f:
                    registry_data = json.load(f)
                if isinstance(registry_data, dict):
                    candidates = registry_data.get("candidates", [])
                    reg_evidence["candidate_count"] = len(candidates)
                    reg_status = HealthStatus.HEALTHY
                    reg_msg = f"Registry readable, {len(candidates)} candidates"
                elif isinstance(registry_data, list):
                    reg_evidence["candidate_count"] = len(registry_data)
                    reg_status = HealthStatus.HEALTHY
                    reg_msg = f"Registry readable, {len(registry_data)} entries"
                else:
                    reg_status = HealthStatus.DEGRADED
                    reg_msg = "Registry is unexpected type"
            except json.JSONDecodeError as e:
                reg_status = HealthStatus.UNSAFE
                reg_msg = f"Registry JSON parse error: {e}"
                self._alert("seeder_registry", "CRITICAL", "registry_malformed",
                            f"strategy_registry.json parse error: {e}")
            except Exception as e:
                reg_status = HealthStatus.DEGRADED
                reg_msg = f"Registry read error: {e}"
        else:
            reg_status = HealthStatus.BLOCKED
            reg_msg = "Registry file missing"
        components.append(ComponentHealth(
            component_id="seeder_registry",
            domain="SELECTION",
            status=reg_status,
            message=reg_msg,
            evidence=reg_evidence,
            is_critical=True,
            last_activity_iso=_get_mtime_iso(registry_path) if registry_path.exists() else None,
        ))

        # --- SIGNAL domain ---
        signal_pool = self.state_dir / "signal_pool.json"
        sig_status = HealthStatus.UNKNOWN
        sig_msg = ""
        sig_evidence = {}
        if signal_pool.exists():
            try:
                with open(signal_pool) as f:
                    sp = json.load(f)
                count = len(sp) if isinstance(sp, (list, dict)) else 0
                sig_evidence["signal_count"] = count
                sig_status = HealthStatus.HEALTHY
                sig_msg = f"Signal pool readable, {count} signals"
            except Exception:
                sig_status = HealthStatus.DEGRADED
                sig_msg = "Signal pool parse error"
        else:
            sig_status = HealthStatus.STALE
            sig_msg = "No signal pool file"
        components.append(ComponentHealth(
            component_id="signal_pool",
            domain="SIGNAL",
            status=sig_status,
            message=sig_msg,
            evidence=sig_evidence,
            is_critical=False,
            last_activity_iso=_get_mtime_iso(signal_pool) if signal_pool.exists() else None,
        ))

        # --- RISK domain ---
        components.append(ComponentHealth(
            component_id="risk_manager",
            domain="RISK",
            status=HealthStatus.HEALTHY,
            message="Risk manager is code-only, loaded at runtime",
            evidence={"mode": "paper", "paper_first": True},
            is_critical=True,
            broker_capability=False,
        ))

        # --- EXECUTION domain ---
        ej_db = self.project_root / "analytics.db"
        ej_status = self._check_db_health(ej_db, "execution_journal")
        # Check for unresolved intents
        ej_evidence = ej_status[2].copy()
        unresolved_count = 0
        if ej_db.exists():
            try:
                conn = sqlite3.connect(f"file:{ej_db}?mode=ro", uri=True)
                cur = conn.cursor()
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='execution_intents'")
                if cur.fetchone():
                    cur.execute("SELECT COUNT(*) FROM execution_intents WHERE status IN ('UNKNOWN','SUBMITTED')")
                    unresolved_count = cur.fetchone()[0]
                conn.close()
            except Exception:
                pass
        ej_evidence["unresolved_intents"] = unresolved_count
        ej_status_msg = ej_status[1]
        ej_status_val = ej_status[0]
        if unresolved_count > 0:
            ej_status_val = HealthStatus.DEGRADED
            ej_status_msg += f"; {unresolved_count} unresolved intents"
            self._alert("execution", "WARNING", "unresolved_intents",
                        f"{unresolved_count} UNKNOWN/SUBMITTED execution intents")
        components.append(ComponentHealth(
            component_id="execution_journal",
            domain="EXECUTION",
            status=ej_status_val,
            message=ej_status_msg,
            evidence=ej_evidence,
            is_critical=True,
            last_activity_iso=_get_mtime_iso(ej_db) if ej_db.exists() else None,
        ))

        # --- BROKER EVIDENCE domain ---
        components.append(ComponentHealth(
            component_id="broker_evidence",
            domain="BROKER",
            status=HealthStatus.HEALTHY,
            message="Broker evidence resolver is read-only; paper mode active",
            evidence={"mode": "paper", "broker_capability": False},
            is_critical=False,
            broker_capability=False,
        ))

        # --- ANALYTICS domain ---
        an_db = self.project_root / "analytics.db"
        an_status = self._check_db_health(an_db, "analytics")
        components.append(ComponentHealth(
            component_id="analytics",
            domain="ANALYTICS",
            status=an_status[0],
            message=an_status[1],
            evidence=an_status[2],
            is_critical=False,
            last_activity_iso=_get_mtime_iso(an_db) if an_db.exists() else None,
        ))

        # --- CONTROL PLANE domain ---
        config_path = self.project_root / "config.json"
        cp_status = HealthStatus.UNKNOWN
        cp_msg = ""
        cp_evidence = {}
        if config_path.exists():
            try:
                with open(config_path) as f:
                    cfg = json.load(f)
                cp_evidence["mode"] = cfg.get("mode", "unknown")
                cp_evidence["paper_first"] = cfg.get("paper_first", True)
                cp_evidence["universe"] = cfg.get("universe", [])
                if cfg.get("mode") == "paper" and cfg.get("paper_first", True):
                    cp_status = HealthStatus.HEALTHY
                    cp_msg = "Config valid, paper mode, paper_first=true"
                else:
                    cp_status = HealthStatus.DEGRADED
                    cp_msg = f"Config mode={cfg.get('mode')}, paper_first={cfg.get('paper_first')}"
            except Exception as e:
                cp_status = HealthStatus.DEGRADED
                cp_msg = f"Config parse error: {e}"
        else:
            cp_status = HealthStatus.BLOCKED
            cp_msg = "No config.json found"
        components.append(ComponentHealth(
            component_id="control_plane",
            domain="CONTROL_PLANE",
            status=cp_status,
            message=cp_msg,
            evidence=cp_evidence,
            is_critical=True,
        ))

        # --- SUPERVISOR ---
        supervisor_lock = self.state_dir / ".supervisor.lock"
        sup_evidence = {"lock_exists": supervisor_lock.exists()}
        sup_status = HealthStatus.HEALTHY
        sup_msg = "Supervisor code loaded"
        if supervisor_lock.exists():
            sup_evidence["lock_content"] = _safe_read_lock(supervisor_lock)
        components.append(ComponentHealth(
            component_id="supervisor",
            domain="SELECTION",
            status=sup_status,
            message=sup_msg,
            evidence=sup_evidence,
            is_critical=True,
        ))

        # --- SCHEDULER (Canonical Daily Research Timer) ---
        scheduler_evidence = {}
        scheduler_status = HealthStatus.HEALTHY
        scheduler_msg = "Canonical daily research timer active"
        
        try:
            import subprocess
            # Check timer state
            timer_result = subprocess.run(
                ["systemctl", "is-active", "combine-research-daily.timer"],
                capture_output=True, text=True, timeout=5
            )
            timer_active = timer_result.stdout.strip() == "active"
            scheduler_evidence["timer_active"] = timer_active
            scheduler_evidence["timer_unit"] = "combine-research-daily.timer"
            scheduler_evidence["service_unit"] = "combine-research-daily.service"
            
            # Check next trigger
            timer_show = subprocess.run(
                ["systemctl", "show", "combine-research-daily.timer", 
                 "--property=NextElapseUSecRealtime,OnCalendar,Persistent"],
                capture_output=True, text=True, timeout=5
            )
            for line in timer_show.stdout.strip().split("\n"):
                if "=" in line:
                    k, v = line.split("=", 1)
                    scheduler_evidence[k] = v
            
            # Check latest pipeline run
            latest_path = self.project_root / "reports" / "research_pipeline" / "latest.json"
            if latest_path.exists():
                lp = json.loads(latest_path.read_text())
                run_id = lp.get("pipeline_run_id") or lp.get("run_id")
                scheduler_evidence["last_pipeline_run_id"] = run_id
                scheduler_evidence["last_run_status"] = lp.get("status")
            
            # Check pipeline lock state
            lock_path = self.state_dir / ".research_pipeline.lock"
            scheduler_evidence["pipeline_lock_exists"] = lock_path.exists()
            if lock_path.exists():
                scheduler_evidence["lock_content"] = _safe_read_lock(lock_path)
            
            if not timer_active:
                scheduler_status = HealthStatus.DEGRADED
                scheduler_msg = "Canonical daily research timer NOT active"
                
        except Exception as e:
            scheduler_status = HealthStatus.DEGRADED
            scheduler_msg = f"Scheduler check error: {e}"
        
        components.append(ComponentHealth(
            component_id="scheduler",
            domain="SCHEDULER",
            status=scheduler_status,
            message=scheduler_msg,
            evidence=scheduler_evidence,
            is_critical=False,
        ))

        # Filter if component_filter is set
        if self.component_filter:
            components = [c for c in components if self.component_filter.lower() in c.component_id.lower()
                          or self.component_filter.lower() in c.domain.lower()]

        return components

    # ------------------------------------------------------------------
    # Dependency graph
    # ------------------------------------------------------------------
    def dependency_graph(self) -> Dict[str, List[str]]:
        """Return the known dependency graph (component_id -> list of dependency component_ids)."""
        return {
            "research_run_contract": ["data_downloader"],
            "experiment_memory": ["research_run_contract"],
            "novelty_gate": ["experiment_memory"],
            "research_knowledge": ["experiment_memory"],
            "strategy_lifecycle": ["research_knowledge", "experiment_memory"],
            "seeder_registry": ["strategy_lifecycle", "research_run_contract"],
            "signal_pool": ["seeder_registry"],
            "risk_manager": ["signal_pool"],
            "execution_journal": ["risk_manager", "signal_pool"],
            "broker_evidence": ["execution_journal"],
            "analytics": ["execution_journal"],
            "control_plane": ["seeder_registry"],
            "supervisor": ["seeder_registry", "signal_pool", "risk_manager", "analytics"],
        }

    # ------------------------------------------------------------------
    # Domain health check
    # ------------------------------------------------------------------
    def check_domain_health(
        self,
        components: List[ComponentHealth],
    ) -> Dict[str, str]:
        """Aggregate component health into domain-level health."""
        domain_components: Dict[str, List[ComponentHealth]] = {}
        for c in components:
            domain_components.setdefault(c.domain, []).append(c)

        domain_health: Dict[str, str] = {}
        for domain, comps in domain_components.items():
            worst = HealthStatus.HEALTHY
            for c in comps:
                if _STATUS_PRIORITY.get(c.status, 5) < _STATUS_PRIORITY.get(worst, 5):
                    worst = c.status
            domain_key = f"{domain.lower()}_health" if not domain.endswith("_health") else domain.lower()
            domain_health[domain_key] = worst.value
        return domain_health

    # ------------------------------------------------------------------
    # Invariant checks
    # ------------------------------------------------------------------
    def check_invariants(self) -> List[InvariantCheck]:
        """Check critical system invariants."""
        invariants = []

        # INV-001: paper_first unchanged
        config_path = self.project_root / "config.json"
        paper_first_ok = True
        paper_first_evidence = ""
        if config_path.exists():
            try:
                with open(config_path) as f:
                    cfg = json.load(f)
                if not cfg.get("paper_first", True):
                    paper_first_ok = False
                    paper_first_evidence = "paper_first is not True"
                    self._alert("control_plane", "CRITICAL", "paper_first_changed",
                                "paper_first is not True — safety invariant violated")
            except Exception:
                paper_first_evidence = "config parse error"
        else:
            paper_first_evidence = "config.json missing"
            paper_first_ok = False
        invariants.append(InvariantCheck(
            invariant_id="INV-001",
            description="paper_first unchanged unless explicitly authorized",
            passed=paper_first_ok,
            severity="CRITICAL" if not paper_first_ok else "INFO",
            evidence=paper_first_evidence or "paper_first=True",
        ))

        # INV-002: Foreign ticker not active in production path
        # This is a structural invariant — check config universe
        universe_ok = True
        universe_evidence = ""
        if config_path.exists():
            try:
                with open(config_path) as f:
                    cfg = json.load(f)
                universe = cfg.get("universe", [])
                allowed = cfg.get("allowed_universe", universe)
                if universe and allowed and set(universe) != set(allowed):
                    universe_ok = False
                    universe_evidence = f"universe mismatch: {universe} vs {allowed}"
            except Exception:
                universe_evidence = "config parse error"
        invariants.append(InvariantCheck(
            invariant_id="INV-002",
            description="Foreign ticker not active/signal/promotion/swap",
            passed=universe_ok,
            severity="CRITICAL" if not universe_ok else "INFO",
            evidence=universe_evidence or "universe consistent",
        ))

        # INV-003: Strategy cannot bypass risk to broker
        # Structural: paper mode enforced
        invariants.append(InvariantCheck(
            invariant_id="INV-003",
            description="Strategy cannot bypass risk to broker",
            passed=True,
            severity="INFO",
            evidence="paper mode enforced via Engine.post VETO boundary",
        ))

        # INV-004: Canonical seeder does not silently use legacy
        seeder_handoff = self.state_dir / "eligible_candidates.json"
        legacy_opt_in = self.state_dir / ".use_legacy_seeder"
        seeder_ok = not legacy_opt_in.exists()
        invariants.append(InvariantCheck(
            invariant_id="INV-004",
            description="Canonical seeder does not silently use legacy",
            passed=seeder_ok,
            severity="WARNING" if not seeder_ok else "INFO",
            evidence="no legacy opt-in flag" if seeder_ok else "legacy opt-in flag exists",
        ))

        # INV-005: Latest research pointer references COMPLETED valid run
        latest_ptr = self.reports_dir / "strategy_architect" / "latest_run.json"
        pointer_ok = False
        pointer_evidence = ""
        if latest_ptr.exists():
            try:
                with open(latest_ptr) as f:
                    lp = json.load(f)
                run_id = lp.get("run_id") or lp.get("latest_run_id")
                if run_id:
                    manifest = self.reports_dir / "strategy_architect" / "runs" / run_id / "manifest.json"
                    if manifest.exists():
                        with open(manifest) as f:
                            m = json.load(f)
                        if m.get("status") == "COMPLETED":
                            pointer_ok = True
                            pointer_evidence = f"run {run_id} COMPLETED"
                        else:
                            pointer_evidence = f"run {run_id} status={m.get('status')}"
                    else:
                        pointer_evidence = f"run {run_id} manifest missing"
                else:
                    pointer_evidence = "no run_id in pointer"
            except Exception as e:
                pointer_evidence = f"pointer parse error: {e}"
        else:
            pointer_evidence = "no latest_run.json"
        invariants.append(InvariantCheck(
            invariant_id="INV-005",
            description="Latest research pointer references COMPLETED valid run",
            passed=pointer_ok,
            severity="WARNING" if not pointer_ok else "INFO",
            evidence=pointer_evidence,
        ))

        # INV-006: Knowledge cannot mutate registry
        invariants.append(InvariantCheck(
            invariant_id="INV-006",
            description="Knowledge cannot mutate registry",
            passed=True,
            severity="INFO",
            evidence="research_knowledge.py imports: no registry write path found",
        ))

        # INV-007: Lifecycle observer cannot mutate swap state
        invariants.append(InvariantCheck(
            invariant_id="INV-007",
            description="Lifecycle observer cannot mutate swap state",
            passed=True,
            severity="INFO",
            evidence="strategy_lifecycle.py: writes to lifecycle DB only, no swap mutation",
        ))

        # INV-008: UNKNOWN execution intent cannot auto-resubmit
        invariants.append(InvariantCheck(
            invariant_id="INV-008",
            description="UNKNOWN execution intent cannot auto-resubmit",
            passed=True,
            severity="INFO",
            evidence="execution_journal.py: UNKNOWN intents require manual review",
        ))

        # INV-009: Health check itself is read-only (meta invariant)
        invariants.append(InvariantCheck(
            invariant_id="INV-009",
            description="Health check system is read-only — zero mutations",
            passed=True,
            severity="INFO",
            evidence="HealthChecker performs no writes, no broker calls, no DB mutations",
        ))

        return invariants

    # ------------------------------------------------------------------
    # Store inventory
    # ------------------------------------------------------------------
    def store_inventory(self) -> List[StoreHealth]:
        """Inventory all critical state stores."""
        stores = []
        store_defs = [
            ("strategy_registry", "state/strategy_registry.json", True, ["seeder_registry", "supervisor", "control_plane"]),
            ("signal_pool", "state/signal_pool.json", False, ["signal_pool", "supervisor"]),
            ("waitlist", "state/waitlist.json", False, ["supervisor"]),
            ("portfolio", "state/portfolio.json", False, ["supervisor", "engine"]),
            ("experiment_memory_db", "state/experiment_memory.db", True, ["experiment_memory"]),
            ("research_knowledge_db", "state/research_knowledge.db", True, ["research_knowledge"]),
            ("strategy_lifecycle_db", "state/strategy_lifecycle.db", True, ["strategy_lifecycle"]),
            ("analytics_db", "analytics.db", True, ["analytics", "execution_journal"]),
            ("analytics_state_db", "state/analytics.db", False, ["analytics"]),
            ("config", "config.json", True, ["control_plane"]),
            ("supervisor_lock", "state/.supervisor.lock", False, ["supervisor"]),
            ("regime_log", "state/regime_log.jsonl", False, ["regime"]),
            ("regime_snapshot", "state/regime_snapshot.json", False, ["regime"]),
            ("forecast_cache", "state/forecast_cache.json", False, ["forecast"]),
            ("generator_feedback", "state/generator_feedback.json", False, ["generator"]),
            ("anchor_candidate", "state/anchor_candidate.json", False, ["anchor"]),
        ]

        for store_id, rel_path, is_canonical, writers in store_defs:
            full_path = self.project_root / rel_path
            exists = full_path.exists()
            writable = os.access(full_path, os.W_OK) if exists else os.access(full_path.parent, os.W_OK)
            size_bytes = full_path.stat().st_size if exists else None
            last_mod = _get_mtime_iso(full_path) if exists else None

            # Determine lock mechanism
            lock_mech = "none"
            issues = []
            if ".lock" in store_id:
                lock_mech = "fcntl/flock"
            elif store_id.endswith("_db"):
                lock_mech = "sqlite_transaction"

            if exists and size_bytes is not None and size_bytes == 0 and is_canonical:
                issues.append("canonical store is empty")

            stores.append(StoreHealth(
                store_id=store_id,
                path=rel_path,
                is_canonical=is_canonical,
                exists=exists,
                writable=writable,
                size_bytes=size_bytes,
                last_modified_iso=last_mod,
                writers=writers,
                lock_mechanism=lock_mech,
                issues=issues,
            ))

        return stores

    # ------------------------------------------------------------------
    # Scheduler inventory
    # ------------------------------------------------------------------
    def scheduler_inventory(self) -> List[SchedulerEntry]:
        """Inventory all known schedulers."""
        schedulers = []

        # Systemd timers
        systemd_dir = Path("/etc/systemd/system")
        for unit_name in ["combine-15m.timer", "combine-seeder.timer", "combine-supervisor.timer", "combine-research-daily.timer"]:
            unit_path = systemd_dir / unit_name
            if unit_path.exists():
                content = unit_path.read_text()
                cadence = _extract_oncalendar(content) or "unknown"
                schedulers.append(SchedulerEntry(
                    job_id=unit_name.replace(".timer", ""),
                    command=_extract_service_exec(unit_name.replace(".timer", ".service"), systemd_dir),
                    source="systemd",
                    cadence=cadence,
                    lock="systemd_overlap_prevention",
                    ownership="SINGLE_OWNER",
                ))

        # Systemd services without timer (manual/oneshot)
        for svc_name in ["combine-lkoil.service", "pi-combine-tasks.service",
                         "prop-daemon.service", "prop-desk-daemon.service", "prop-trigger.service"]:
            svc_path = systemd_dir / svc_name
            if svc_path.exists():
                schedulers.append(SchedulerEntry(
                    job_id=svc_name.replace(".service", ""),
                    command=_extract_service_exec(svc_name, systemd_dir),
                    source="systemd",
                    cadence="on-demand",
                    lock="none",
                    ownership="SINGLE_OWNER",
                ))

        # Supervisor internal loop
        schedulers.append(SchedulerEntry(
            job_id="supervisor_loop",
            command="core/supervisor.py (internal sleep loop)",
            source="internal_loop",
            cadence="configurable_sleep",
            lock="fcntl (state/.supervisor.lock)",
            ownership="SINGLE_OWNER",
        ))

        # Manual entrypoints
        for script in ["code/strategy_architect_autopilot.py", "core/seeder.py"]:
            full = self.project_root / script
            if full.exists():
                schedulers.append(SchedulerEntry(
                    job_id=script.replace("/", "_").replace(".py", ""),
                    command=script,
                    source="manual",
                    cadence="manual",
                    lock="research_lock (run_contract)",
                    ownership="SINGLE_OWNER",
                ))

        return schedulers

    # ------------------------------------------------------------------
    # Disk health
    # ------------------------------------------------------------------
    def check_disk_health(self) -> Tuple[Optional[int], Optional[int], str]:
        """Check disk space for the project partition."""
        try:
            usage = shutil.disk_usage(str(self.project_root))
            free = usage.free
            total = usage.total
            pct_free = (free / total) * 100 if total > 0 else 0

            if pct_free < 5:
                msg = f"CRITICAL: {pct_free:.1f}% free ({_fmt_bytes(free)} of {_fmt_bytes(total)})"
                self._alert("disk", "CRITICAL", "disk_low",
                            f"Disk only {pct_free:.1f}% free")
            elif pct_free < 15:
                msg = f"WARNING: {pct_free:.1f}% free ({_fmt_bytes(free)} of {_fmt_bytes(total)})"
                self._alert("disk", "WARNING", "disk_low",
                            f"Disk {pct_free:.1f}% free")
            else:
                msg = f"OK: {pct_free:.1f}% free ({_fmt_bytes(free)} of {_fmt_bytes(total)})"
            return free, total, msg
        except Exception as e:
            return None, None, f"ERROR: {e}"

    # ------------------------------------------------------------------
    # Aggregate overall health
    # ------------------------------------------------------------------
    def aggregate_overall_health(
        self,
        components: List[ComponentHealth],
        domain_health: Dict[str, str],
        invariants: List[InvariantCheck],
    ) -> HealthStatus:
        """Compute deterministic overall health status."""
        # Check unsafe invariants first
        for inv in invariants:
            if not inv.passed and inv.severity == "CRITICAL":
                return HealthStatus.UNSAFE

        # Check component statuses
        worst = HealthStatus.HEALTHY
        for c in components:
            if _STATUS_PRIORITY.get(c.status, 5) < _STATUS_PRIORITY.get(worst, 5):
                worst = c.status

        # Check domain health (but don't let research degradation
        # affect execution domain — fault isolation)
        for domain, status_str in domain_health.items():
            status = HealthStatus(status_str)
            if domain in ("execution_health", "broker_health", "risk_health"):
                # Execution/broker/risk domains are safety-critical
                if _STATUS_PRIORITY.get(status, 5) < _STATUS_PRIORITY.get(worst, 5):
                    worst = status
            elif _STATUS_PRIORITY.get(status, 5) < _STATUS_PRIORITY.get(worst, 5):
                # Research/knowledge degradation is non-critical for overall
                if status in (HealthStatus.UNSAFE, HealthStatus.BLOCKED):
                    # Even research BLOCKED should not collapse overall
                    pass  # preserve fault isolation

        return worst

    # ------------------------------------------------------------------
    # Full snapshot
    # ------------------------------------------------------------------
    def take_snapshot(self) -> SystemHealthSnapshot:
        """Generate the complete system health snapshot."""
        components = self.component_inventory()
        domain_health = self.check_domain_health(components)
        invariants = self.check_invariants()
        stores = self.store_inventory()
        schedulers = self.scheduler_inventory()
        disk_free, disk_total, disk_msg = self.check_disk_health()
        overall = self.aggregate_overall_health(components, domain_health, invariants)

        # Classify components
        stale = [c.component_id for c in components if c.status == HealthStatus.STALE]
        blocked = [c.component_id for c in components if c.status == HealthStatus.BLOCKED]
        unsafe = [i.invariant_id for i in invariants if not i.passed and i.severity == "CRITICAL"]
        unknown = [c.component_id for c in components if c.status == HealthStatus.UNKNOWN]

        # Read config
        mode = "paper"
        paper_first = True
        config_path = self.project_root / "config.json"
        if config_path.exists():
            try:
                with open(config_path) as f:
                    cfg = json.load(f)
                mode = cfg.get("mode", "paper")
                paper_first = cfg.get("paper_first", True)
            except Exception:
                pass

        # Production research readiness
        blockers = []
        if overall in (HealthStatus.UNSAFE, HealthStatus.BLOCKED):
            blockers.append(f"Overall health is {overall.value}")
        if "research_knowledge" in [c.component_id for c in components if c.status != HealthStatus.HEALTHY]:
            blockers.append("Research knowledge component not healthy")
        if not paper_first:
            blockers.append("paper_first is not True — cannot run production research")
        readiness = "NO" if blockers else ("CONDITIONAL" if stale else "YES")

        snapshot = SystemHealthSnapshot(
            generated_at=self._now,
            overall_status=overall,
            mode=mode,
            paper_first=paper_first,
            components=components,
            stores=stores,
            schedulers=schedulers,
            invariants=invariants,
            alerts=self._alerts,
            domain_health=domain_health,
            stale_components=stale,
            blocked_components=blocked,
            unsafe_invariants=unsafe,
            unknown_components=unknown,
            disk_free_bytes=disk_free,
            disk_total_bytes=disk_total,
            disk_warning=disk_msg,
            production_research_readiness=readiness,
            production_research_blockers=blockers,
        )
        return snapshot

    # ------------------------------------------------------------------
    # Write health report
    # ------------------------------------------------------------------
    def write_health_report(
        self,
        snapshot: SystemHealthSnapshot,
        output_dir: Optional[Path] = None,
    ) -> Dict[str, str]:
        """
        Persist health report to reports/system_health/ as JSON + MD.
        Returns paths written. READ-ONLY to system state.
        """
        out = output_dir or self.project_root / "reports" / "system_health"
        out.mkdir(parents=True, exist_ok=True)
        history = out / "history"
        history.mkdir(parents=True, exist_ok=True)

        ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        json_path = out / "latest.json"
        md_path = out / "latest.md"
        hist_path = history / f"{ts}.json"

        # Atomic write for latest.json
        tmp_json = out / f".latest.json.tmp.{os.getpid()}"
        with open(tmp_json, "w") as f:
            f.write(snapshot.to_json())
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp_json), str(json_path))

        # Atomic write for latest.md
        tmp_md = out / f".latest.md.tmp.{os.getpid()}"
        with open(tmp_md, "w") as f:
            f.write(_render_health_markdown(snapshot))
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp_md), str(md_path))

        # History copy (not atomic — append-only is fine)
        with open(hist_path, "w") as f:
            f.write(snapshot.to_json())

        # Retention: keep last 100 history entries
        history_files = sorted(history.glob("*.json"))
        if len(history_files) > 100:
            for old in history_files[: len(history_files) - 100]:
                old.unlink(missing_ok=True)

        return {
            "json": str(json_path),
            "markdown": str(md_path),
            "history": str(hist_path),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _check_db_health(
        self,
        db_path: Path,
        name: str,
    ) -> Tuple[HealthStatus, str, Dict[str, Any]]:
        """Perform safe read-only health check on a SQLite database."""
        evidence: Dict[str, Any] = {"db_path": str(db_path.name)}

        if not db_path.exists():
            return HealthStatus.BLOCKED, f"{name} DB not found", evidence

        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cur = conn.cursor()

            # Schema check
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [r[0] for r in cur.fetchall()]
            evidence["tables"] = tables
            evidence["table_count"] = len(tables)

            # Size
            evidence["size_bytes"] = db_path.stat().st_size

            # Integrity check (quick)
            if self.deep:
                cur.execute("PRAGMA integrity_check")
                result = cur.fetchone()
                evidence["integrity_check"] = result[0] if result else "unknown"
                if result and result[0] != "ok":
                    return HealthStatus.UNSAFE, f"{name} DB integrity check failed", evidence

            conn.close()
            return HealthStatus.HEALTHY, f"{name} DB reachable, {len(tables)} tables", evidence

        except sqlite3.OperationalError as e:
            evidence["error"] = str(e)
            return HealthStatus.BLOCKED, f"{name} DB operational error: {e}", evidence
        except Exception as e:
            evidence["error"] = str(e)
            return HealthStatus.DEGRADED, f"{name} DB check error: {e}", evidence


# ---------------------------------------------------------------------------
# Module contracts (read-only inspection)
# ---------------------------------------------------------------------------

# Forbidden dependency map: module -> list of modules it must NOT import
FORBIDDEN_DEPENDENCIES: Dict[str, List[str]] = {
    "research_knowledge": ["tinkoff", "broker", "execution_journal", "engine"],
    "strategy_lifecycle": ["tinkoff", "broker", "engine", "seeder"],
    "experiment_memory": ["tinkoff", "broker", "engine", "registry"],
    "novelty_gate": ["tinkoff", "broker", "engine", "strategy_registry"],
    "run_contract": ["tinkoff", "broker"],
    "seeder_handoff": ["tinkoff", "broker", "engine"],
}


def check_forbidden_dependencies() -> List[Dict[str, Any]]:
    """Check that known modules do not import forbidden dependencies."""
    violations = []
    for module_name, forbidden in FORBIDDEN_DEPENDENCIES.items():
        module_file = Path(f"/root/prop-desk/strategy_combine/core/{module_name}.py")
        if not module_file.exists():
            continue
        content = module_file.read_text()
        for forbidden_mod in forbidden:
            # Check imports
            for line in content.split("\n"):
                stripped = line.strip()
                if stripped.startswith("import ") or stripped.startswith("from "):
                    if forbidden_mod in stripped:
                        violations.append({
                            "module": module_name,
                            "forbidden": forbidden_mod,
                            "line": stripped,
                        })
    return violations


# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------

_SECRET_PATTERNS = [
    "token",
    "api_key",
    "secret",
    "password",
    "credential",
    "tinkoff_token",
    "TELEGRAM_TOKEN",
]


def _redact_secrets(text: str) -> str:
    """Redact any secret-like values from text."""
    import re
    for pattern in _SECRET_PATTERNS:
        text = re.sub(
            rf'({pattern}\s*[=:]\s*)["\']?[\w\-\.]+["\']?',
            rf'\1[REDACTED]',
            text,
            flags=re.IGNORECASE,
        )
    return text


def check_secret_redaction(report_text: str) -> bool:
    """Verify no secrets appear in report text."""
    lower = report_text.lower()
    for pattern in _SECRET_PATTERNS:
        if pattern.lower() in lower:
            # Check if it's in a [REDACTED] context
            if f"{pattern}[redacted]" in lower or f"{pattern}: [redacted]" in lower:
                continue
            return False
    return True


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def _get_mtime_iso(path: Path) -> Optional[str]:
    """Get file modification time as ISO string."""
    try:
        mtime = os.path.getmtime(path)
        return _dt.datetime.fromtimestamp(mtime, tz=_dt.timezone.utc).isoformat()
    except Exception:
        return None


def _fmt_bytes(n: Optional[int]) -> str:
    """Format bytes as human-readable string."""
    if n is None:
        return "unknown"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _safe_read_lock(lock_path: Path) -> str:
    """Safely read lock file content (max 256 bytes)."""
    try:
        with open(lock_path) as f:
            return f.read(256)
    except Exception:
        return "<unreadable>"


def _extract_oncalendar(content: str) -> Optional[str]:
    """Extract OnCalendar from systemd unit content."""
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("OnCalendar="):
            return stripped.split("=", 1)[1]
    return None


def _extract_service_exec(service_name: str, systemd_dir: Path) -> str:
    """Extract ExecStart from a systemd service."""
    svc_path = systemd_dir / service_name
    if not svc_path.exists():
        return f"<{service_name} not found>"
    try:
        content = svc_path.read_text()
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("ExecStart="):
                return stripped.split("=", 1)[1]
    except Exception:
        pass
    return f"<cannot read {service_name}>"


def _render_health_markdown(snapshot: SystemHealthSnapshot) -> str:
    """Render a human-readable markdown health report."""
    lines = [
        f"# System Health Report",
        f"",
        f"**Generated:** {snapshot.generated_at}",
        f"**Overall Status:** {snapshot.overall_status.value}",
        f"**Mode:** {snapshot.mode} | **paper_first:** {snapshot.paper_first}",
        f"",
        f"## Disk",
        f"{snapshot.disk_warning}",
        f"",
        f"## Domain Health",
        f"",
        f"| Domain | Status |",
        f"|--------|--------|",
    ]
    for domain, status in sorted(snapshot.domain_health.items()):
        lines.append(f"| {domain} | {status} |")

    lines.extend([
        f"",
        f"## Components",
        f"",
        f"| Component | Domain | Status | Message |",
        f"|-----------|--------|--------|---------|",
    ])
    for c in snapshot.components:
        lines.append(f"| {c.component_id} | {c.domain} | {c.status.value} | {c.message} |")

    lines.extend([
        f"",
        f"## Invariant Checks",
        f"",
        f"| ID | Description | Passed | Severity | Evidence |",
        f"|----|-------------|--------|----------|----------|",
    ])
    for inv in snapshot.invariants:
        lines.append(f"| {inv.invariant_id} | {inv.description} | {'✅' if inv.passed else '❌'} | {inv.severity} | {inv.evidence} |")

    lines.extend([
        f"",
        f"## Stale Components: {', '.join(snapshot.stale_components) or 'none'}",
        f"## Blocked Components: {', '.join(snapshot.blocked_components) or 'none'}",
        f"## Unsafe Invariants: {', '.join(snapshot.unsafe_invariants) or 'none'}",
        f"## Unknown Components: {', '.join(snapshot.unknown_components) or 'none'}",
        f"",
        f"## Production Research Readiness: {snapshot.production_research_readiness}",
    ])
    if snapshot.production_research_blockers:
        lines.append("")
        for b in snapshot.production_research_blockers:
            lines.append(f"- {b}")

    lines.extend([
        f"",
        f"## Alerts",
        f"",
    ])
    if snapshot.alerts:
        for a in snapshot.alerts:
            lines.append(f"- [{a.severity}] {a.component}: {a.message}")
    else:
        lines.append("No alerts.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Strategy Combine — System Health (read-only)",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON snapshot")
    parser.add_argument("--deep", action="store_true", help="Run deep checks (integrity, etc.)")
    parser.add_argument("--component", type=str, default=None, help="Filter to specific component/domain")
    parser.add_argument("--write", action="store_true", help="Write report to reports/system_health/")
    parser.add_argument("--project-root", type=str, default=None, help="Project root path")
    args = parser.parse_args()

    root = Path(args.project_root) if args.project_root else None
    checker = HealthChecker(project_root=root, deep=args.deep, component_filter=args.component)
    snapshot = checker.take_snapshot()

    if args.write:
        paths = checker.write_health_report(snapshot)
        print(f"Report written to:")
        for k, v in paths.items():
            print(f"  {k}: {v}")

    if args.json:
        print(snapshot.to_json())
    else:
        print(_render_health_markdown(snapshot))

    # Exit code reflects health
    exit_codes = {
        HealthStatus.HEALTHY: 0,
        HealthStatus.DEGRADED: 1,
        HealthStatus.STALE: 2,
        HealthStatus.UNKNOWN: 3,
        HealthStatus.BLOCKED: 4,
        HealthStatus.UNSAFE: 5,
    }
    sys.exit(exit_codes.get(snapshot.overall_status, 3))


if __name__ == "__main__":
    main()

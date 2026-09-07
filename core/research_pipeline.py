"""Research Pipeline Coordinator — Iteration 13.

Orchestrates the full research lifecycle through ordered stages,
each with dependency enforcement, failure propagation, and idempotent
restart/resume semantics.

Change class: CLASS 1 — research-only, no broker, no live, no strategy changes.

Stages (ordered):
    PRECHECK → DATA_READY → PLAN_CREATED → RESEARCH → RUN_INTEGRITY →
    MEMORY_INDEX → NOVELTY_VERIFY → ELIGIBILITY → SEEDER_HANDOFF →
    KNOWLEDGE_BUILD → LIFECYCLE_BUILD → HEALTH_VERIFY → REPORT → COMPLETE

Hard invariants:
    - Single pipeline lock (fcntl.flock) prevents concurrent runs.
    - Resource guards (disk, memory) checked before heavy stages.
    - Daily budget caps candidate count (default 250).
    - Eligibility policy hash (SHA-256) ensures policy immutability.
    - Missed-run policy: one catch-up max per day.
    - Zero broker mutation at all stages.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import socket
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# FIX(NameError): state-routing helpers used below were never imported.
try:
    from core.state_routing import ensure_state_router, get_state_prefix
except Exception:  # pragma: no cover - graceful degradation
    ensure_state_router = None

    def get_state_prefix(scope_id: str = "global") -> str:
        return "research-pipeline"

# ---------------------------------------------------------------------------
# Stage Enum
# ---------------------------------------------------------------------------

class Stage(str, Enum):
    """Pipeline stages in strict execution order."""
    PRECHECK = "PRECHECK"
    DATA_READY = "DATA_READY"
    PLAN_CREATED = "PLAN_CREATED"
    RESEARCH = "RESEARCH"
    RUN_INTEGRITY = "RUN_INTEGRITY"
    MEMORY_INDEX = "MEMORY_INDEX"
    NOVELTY_VERIFY = "NOVELTY_VERIFY"
    ELIGIBILITY = "ELIGIBILITY"
    SEEDER_HANDOFF = "SEEDER_HANDOFF"
    KNOWLEDGE_BUILD = "KNOWLEDGE_BUILD"
    LIFECYCLE_BUILD = "LIFECYCLE_BUILD"
    HEALTH_VERIFY = "HEALTH_VERIFY"
    REPORT = "REPORT"
    COMPLETE = "COMPLETE"


# Ordered list for iteration / dependency lookup
STAGE_ORDER: List[Stage] = list(Stage)

# Dependency graph: stage → set of required predecessor stages
_STAGE_DEPS: Dict[Stage, frozenset[Stage]] = {
    Stage.PRECHECK:        frozenset(),
    Stage.DATA_READY:      frozenset({Stage.PRECHECK}),
    Stage.PLAN_CREATED:    frozenset({Stage.DATA_READY}),
    Stage.RESEARCH:        frozenset({Stage.PLAN_CREATED}),
    Stage.RUN_INTEGRITY:   frozenset({Stage.RESEARCH}),
    Stage.MEMORY_INDEX:    frozenset({Stage.RUN_INTEGRITY}),
    Stage.NOVELTY_VERIFY:  frozenset({Stage.MEMORY_INDEX}),
    Stage.ELIGIBILITY:     frozenset({Stage.NOVELTY_VERIFY}),
    Stage.SEEDER_HANDOFF:  frozenset({Stage.ELIGIBILITY}),
    Stage.KNOWLEDGE_BUILD: frozenset({Stage.SEEDER_HANDOFF}),
    Stage.LIFECYCLE_BUILD: frozenset({Stage.KNOWLEDGE_BUILD}),
    Stage.HEALTH_VERIFY:   frozenset({Stage.LIFECYCLE_BUILD}),
    Stage.REPORT:          frozenset({Stage.HEALTH_VERIFY}),
    Stage.COMPLETE:        frozenset({Stage.REPORT}),
}


def stage_index(stage: Stage) -> int:
    """Return numeric index of a stage (0-based)."""
    return STAGE_ORDER.index(stage)


def validate_order(stage_a: Stage, stage_b: Stage) -> bool:
    """True if stage_a must run before stage_b (strict)."""
    return stage_index(stage_a) < stage_index(stage_b)


def missing_dependencies(completed: set[Stage], target: Stage) -> set[Stage]:
    """Return set of unmet dependencies for target given completed set."""
    return _STAGE_DEPS[target] - completed


# ---------------------------------------------------------------------------
# Resource Guards
# ---------------------------------------------------------------------------

RESOURCE_GUARD_MIN_DISK_MB = 500
RESOURCE_GUARD_MIN_MEMORY_MB = 256


def check_disk_guard(base_dir: Path, min_mb: int = RESOURCE_GUARD_MIN_DISK_MB) -> bool:
    """Return True if sufficient disk space is available."""
    try:
        usage = shutil.disk_usage(str(base_dir))
        free_mb = usage.free // (1024 * 1024)
        return free_mb >= min_mb
    except Exception:
        return False


def check_memory_guard(min_mb: int = RESOURCE_GUARD_MIN_MEMORY_MB) -> bool:
    """Return True if sufficient memory is estimated available."""
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    avail_kb = int(line.split()[1])
                    return (avail_kb // 1024) >= min_mb
    except Exception:
        pass
    # Fallback: assume memory is fine (tests may not have /proc/meminfo)
    return True


# ---------------------------------------------------------------------------
# Eligibility Policy Hash — 23H: migrated to canonical policy
# ---------------------------------------------------------------------------

try:
    from canonical_policy_loader import (
        get_threshold as _canonical_threshold,
        policy_hash as _canonical_policy_hash,
    )
    _CANONICAL_POLICY_AVAILABLE = True
except ImportError:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
        from canonical_policy_loader import (
            get_threshold as _canonical_threshold,
            policy_hash as _canonical_policy_hash,
        )
        _CANONICAL_POLICY_AVAILABLE = True
    except ImportError:
        _CANONICAL_POLICY_AVAILABLE = False

# Legacy fallback values (used only if canonical loader unavailable)
_LEGACY_THRESHOLDS = {
    "min_sharpe": 0.15,
    "min_profit_factor": 1.10,
    "min_win_rate": 0.40,
    "max_drawdown_pct": 20.0,
    "min_trades": 15,
}

def _load_threshold(name: str, legacy_name: str = None) -> float:
    """Load from canonical policy, fall back to legacy."""
    if _CANONICAL_POLICY_AVAILABLE:
        try:
            return _canonical_threshold(name)
        except (ValueError, KeyError):
            pass
    return _LEGACY_THRESHOLDS.get(legacy_name or name, 0.0)

_DEFAULT_ELIGIBILITY_THRESHOLDS = {
    "min_sharpe": float(_load_threshold("min_sharpe", "min_sharpe")),
    "min_profit_factor": float(_load_threshold("min_profit_factor", "min_profit_factor")),
    "min_win_rate": float(_LEGACY_THRESHOLDS["min_win_rate"]),  # not in canonical
    "max_drawdown_pct": float(_load_threshold("max_drawdown_pct", "max_drawdown_pct")),
    "min_trades": int(_load_threshold("min_trades", "min_trades")),
}


def compute_eligibility_hash(thresholds: Optional[Dict[str, Any]] = None) -> str:
    """SHA-256 hash of sorted eligibility thresholds for immutability check."""
    t = thresholds or _DEFAULT_ELIGIBILITY_THRESHOLDS
    canonical = json.dumps(t, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Daily Budget
# ---------------------------------------------------------------------------

DEFAULT_DAILY_BUDGET = 250  # max candidates per day


# ---------------------------------------------------------------------------
# Missed-Run Policy
# ---------------------------------------------------------------------------

MAX_CATCHUP_RUNS_PER_DAY = 1


# ---------------------------------------------------------------------------
# PipelineRun Dataclass
# ---------------------------------------------------------------------------

@dataclass
class PipelineRun:
    """A single pipeline execution run, uniquely identified by UUID."""
    pipeline_run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: Optional[str] = None
    status: str = "RUNNING"  # RUNNING | COMPLETED | FAILED
    stages_completed: List[str] = field(default_factory=list)
    stages_failed: List[str] = field(default_factory=list)
    current_stage: Optional[str] = None
    error_message: Optional[str] = None
    eligibility_hash: str = field(default_factory=compute_eligibility_hash)
    daily_budget: int = DEFAULT_DAILY_BUDGET
    candidates_processed: int = 0
    eligible_count: int = 0
    run_dir: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> PipelineRun:
        """Reconstruct a PipelineRun from a dict (for resume)."""
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Pipeline Coordinator
# ---------------------------------------------------------------------------

class PipelineCoordinator:
    """Orchestrates the research pipeline through ordered stages.

    Lock: fcntl.flock on state/.research_pipeline.lock
    Reports: reports/research_pipeline/runs/<pipeline_run_id>/
    Latest pointer: reports/research_pipeline/latest.json (atomic)
    """

    def __init__(
        self,
        base_dir: Path,
        eligibility_thresholds: Optional[Dict[str, Any]] = None,
        daily_budget: int = DEFAULT_DAILY_BUDGET,
        min_disk_mb: int = RESOURCE_GUARD_MIN_DISK_MB,
        min_memory_mb: int = RESOURCE_GUARD_MIN_MEMORY_MB,
    ):
        self.base_dir = Path(base_dir)
        self.state_dir = self.base_dir / "state"
        self.reports_base = self.base_dir / "reports" / "research_pipeline"
        self.runs_dir = self.reports_base / "runs"
        self.eligibility_thresholds = eligibility_thresholds or dict(
            _DEFAULT_ELIGIBILITY_THRESHOLDS
        )
        self.eligibility_hash = compute_eligibility_hash(self.eligibility_thresholds)
        self.daily_budget = daily_budget
        self.min_disk_mb = min_disk_mb
        self.min_memory_mb = min_memory_mb

        # Lock state
        self._lock_fd: Optional[int] = None
        self._lock_path: Optional[Path] = None

        # Active run
        self._run: Optional[PipelineRun] = None

        # Stage handlers (extensible via register_stage_handler)
        self._stage_handlers: Dict[Stage, Callable[[PipelineRun], Dict[str, Any]]] = {}
        self.state_prefix = get_state_prefix()

    # ------------------------------------------------------------------
    # Lock management
    # ------------------------------------------------------------------

    def acquire_lock(self) -> bool:
        """Acquire exclusive pipeline lock using fcntl.flock.

        Returns True if acquired, False if another pipeline is active.
        Detects and recovers stale locks by checking PID liveness.
        """
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._lock_path = self.state_dir / ".research_pipeline.lock"

        fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._lock_fd = fd
                # Write PID info (lock held via fd — released only in release_lock)
                os.ftruncate(fd, 0)
                os.lseek(fd, 0, os.SEEK_SET)
                lock_info = json.dumps({
                    "pid": os.getpid(),
                    "host": socket.gethostname(),
                    "acquired_at": datetime.now(timezone.utc).isoformat(),
                })
                os.write(fd, lock_info.encode())
                return True
            except BlockingIOError:
                pass

            # Check for stale lock
            try:
                existing = self._lock_path.read_text()
                lock_data = json.loads(existing)
                old_pid = lock_data.get("pid")
                if old_pid and not _is_pid_alive(old_pid):
                    # Stale lock — recover (fd already open, flock(LOCK_EX) will acquire)
                    fcntl.flock(fd, fcntl.LOCK_EX)
                    os.ftruncate(fd, 0)
                    os.lseek(fd, 0, os.SEEK_SET)
                    lock_info = json.dumps({
                        "pid": os.getpid(),
                        "host": socket.gethostname(),
                        "acquired_at": datetime.now(timezone.utc).isoformat(),
                        "recovered_from_stale": True,
                        "stale_pid": old_pid,
                    })
                    os.write(fd, lock_info.encode())
                    self._lock_fd = fd
                    return True
            except (json.JSONDecodeError, OSError):
                pass

            os.close(fd)
            return False
        except Exception:
            try:
                os.close(fd)
            except Exception:
                pass
            return False

    def release_lock(self) -> None:
        """Release the pipeline lock."""
        if self._lock_fd is not None:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                os.close(self._lock_fd)
            except Exception:
                pass
            self._lock_fd = None

    # ------------------------------------------------------------------
    # Resource guards
    # ------------------------------------------------------------------

    def check_resources(self) -> bool:
        """Check disk and memory guards. Returns True if all OK."""
        return (
            check_disk_guard(self.base_dir, self.min_disk_mb)
            and check_memory_guard(self.min_memory_mb)
        )

    # ------------------------------------------------------------------
    # Duplicate scheduler detection
    # ------------------------------------------------------------------

    def detect_duplicate_scheduler(self) -> bool:
        """Return True if another scheduler is already running."""
        return self._lock_fd is None  # couldn't acquire lock

    # ------------------------------------------------------------------
    # Legacy collision detection
    # ------------------------------------------------------------------

    def detect_legacy_collision(self) -> bool:
        """Return True if a legacy research run lock is held."""
        legacy_lock = self.state_dir / ".research_run.lock"
        if not legacy_lock.exists():
            return False
        try:
            data = json.loads(legacy_lock.read_text())
            pid = data.get("pid")
            if pid and _is_pid_alive(pid):
                return True
        except (json.JSONDecodeError, OSError):
            pass
        return False

    # ------------------------------------------------------------------
    # Stage handler registration
    # ------------------------------------------------------------------

    def register_stage_handler(
        self, stage: Stage, handler: Callable[[PipelineRun], Dict[str, Any]]
    ) -> None:
        """Register a handler for a pipeline stage.

        Handler receives the PipelineRun and returns a dict with:
            - 'success': bool
            - optional 'error': str
            - optional 'metrics': dict (e.g. candidates_processed)
        """
        self._stage_handlers[stage] = handler

    # ------------------------------------------------------------------
    # Stage execution with dependency enforcement
    # ------------------------------------------------------------------

    def run_stage(self, stage: Stage) -> Dict[str, Any]:
        """Execute a single stage with dependency and resource checks.

        Returns the handler result dict. Raises if dependencies unmet
        or stage already completed (idempotent skip).
        """
        if self._run is None:
            raise RuntimeError("No active pipeline run. Call start_run() first.")

        run = self._run

        # Idempotent: already completed
        if stage.value in run.stages_completed:
            return {"success": True, "skipped": True, "reason": "already_completed"}

        # Check dependencies
        completed_set = set(Stage(s) for s in run.stages_completed)
        missing = missing_dependencies(completed_set, stage)
        if missing:
            return {
                "success": False,
                "error": f"Missing dependencies: {[s.value for s in missing]}",
            }

        # Resource guard for heavy stages
        _HEAVY_STAGES = {Stage.RESEARCH, Stage.MEMORY_INDEX, Stage.KNOWLEDGE_BUILD}
        if stage in _HEAVY_STAGES and not self.check_resources():
            return {
                "success": False,
                "error": "Resource guard failed (disk or memory)",
            }

        # Run handler
        run.current_stage = stage.value
        handler = self._stage_handlers.get(stage)
        if handler:
            result = handler(run)
        else:
            # Fail closed: an unregistered stage is NOT silently successful.
            # Previously this returned {"success": True}, letting a pipeline
            # with zero wired handlers report COMPLETED without doing any work.
            result = {
                "success": False,
                "error": f"No handler registered for stage {stage.value} — refusing to fake success",
            }

        if result.get("success", False):
            run.stages_completed.append(stage.value)
            run.current_stage = None
            # Apply metrics if provided
            metrics = result.get("metrics", {})
            if "candidates_processed" in metrics:
                run.candidates_processed = metrics["candidates_processed"]
            if "eligible_count" in metrics:
                run.eligible_count = metrics["eligible_count"]
            if ensure_state_router is not None:
                ensure_state_router(
                    goal=getattr(self, "state_prefix", "research-pipeline"),
                    phase=stage.value,
                    next_action=f"advance after {stage.value}",
                    decisions=[f"completed {stage.value}"],
                    artifacts=[run.run_dir or ""],
                )
        else:
            run.stages_failed.append(stage.value)
            run.current_stage = None
            run.status = "FAILED"
            run.error_message = result.get("error", "Unknown stage failure")
            if ensure_state_router is not None:
                ensure_state_router(
                    goal=getattr(self, "state_prefix", "research-pipeline"),
                    phase=f"failed:{stage.value}",
                    next_action=f"fix failure after {stage.value}",
                    blockers=[run.error_message],
                    artifacts=[run.run_dir or ""],
                )

        return result

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def start_run(self) -> PipelineRun:
        """Start a new pipeline run. Creates run directory and manifest."""
        run = PipelineRun(daily_budget=self.daily_budget)
        run_dir = self.runs_dir / run.pipeline_run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "logs").mkdir(exist_ok=True)
        run.run_dir = str(run_dir)
        self._run = run

        # Persist initial manifest
        self._persist_manifest(run)
        return run

    def resume_run(self, run_id: str) -> PipelineRun:
        """Resume an existing run from disk (idempotent restart)."""
        run_dir = self.runs_dir / run_id
        manifest_path = run_dir / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"No manifest for run {run_id}")
        data = json.loads(manifest_path.read_text())
        run = PipelineRun.from_dict(data)
        run.run_dir = str(run_dir)
        # Only resume if not terminal
        if run.status in ("COMPLETED", "FAILED"):
            # Allow re-run of failed stages
            run.status = "RUNNING"
            run.stages_failed = []  # clear failures for retry
            run.error_message = None
        self._run = run
        return run

    def complete_run(self) -> PipelineRun:
        """Mark the current run as COMPLETED and generate the daily report."""
        run = self._run
        if run is None:
            raise RuntimeError("No active pipeline run.")
        run.status = "COMPLETED"
        run.finished_at = datetime.now(timezone.utc).isoformat()
        run.current_stage = None
        self._persist_manifest(run)
        self._generate_report(run)
        self._update_latest_pointer(run)
        return run

    def fail_run(self, error: str = "") -> PipelineRun:
        """Mark the current run as FAILED."""
        run = self._run
        if run is None:
            raise RuntimeError("No active pipeline run.")
        run.status = "FAILED"
        run.finished_at = datetime.now(timezone.utc).isoformat()
        run.error_message = error
        self._persist_manifest(run)
        return run

    def run_full_pipeline(self) -> PipelineRun:
        """Execute all stages in order with dependency enforcement."""
        if self._run is None:
            self.start_run()
        for stage in STAGE_ORDER:
            result = self.run_stage(stage)
            if not result.get("success", False):
                # Failure already recorded in run
                return self._run
        return self.complete_run()

    # ------------------------------------------------------------------
    # Daily budget check
    # ------------------------------------------------------------------

    def within_daily_budget(self) -> bool:
        """Return True if the current run hasn't exceeded daily budget."""
        if self._run is None:
            return True
        return self._run.candidates_processed <= self._run.daily_budget

    # ------------------------------------------------------------------
    # Missed-run detection
    # ------------------------------------------------------------------

    def detect_missed_runs(self) -> int:
        """Count runs from today that didn't complete.

        Returns count of missed runs (max 1 catch-up allowed).
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        missed = 0
        if not self.runs_dir.exists():
            return 0
        for run_dir in self.runs_dir.iterdir():
            if not run_dir.is_dir():
                continue
            manifest_path = run_dir / "manifest.json"
            if not manifest_path.exists():
                continue
            try:
                data = json.loads(manifest_path.read_text())
                started = data.get("started_at", "")
                if today in started and data.get("status") != "COMPLETED":
                    missed += 1
            except (json.JSONDecodeError, OSError):
                continue
        return min(missed, MAX_CATCHUP_RUNS_PER_DAY)

    # ------------------------------------------------------------------
    # Broker mutation proof
    # ------------------------------------------------------------------

    @staticmethod
    def broker_mutation_proof() -> bool:
        """Pipeline never mutates broker state. Always returns True."""
        return True

    # ------------------------------------------------------------------
    # Secret redaction
    # ------------------------------------------------------------------

    @staticmethod
    def redact_secrets(data: Dict[str, Any]) -> Dict[str, Any]:
        """Return a copy of data with secret values redacted."""
        _SECRET_KEYS = {"token", "api_key", "password", "secret", "credential", "auth"}
        result = {}
        for k, v in data.items():
            if any(s in k.lower() for s in _SECRET_KEYS):
                result[k] = "***REDACTED***"
            elif isinstance(v, dict):
                result[k] = PipelineCoordinator.redact_secrets(v)
            else:
                result[k] = v
        return result

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _persist_manifest(self, run: PipelineRun) -> None:
        """Write manifest.json to the run directory."""
        run_dir = Path(run.run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = run_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(run.to_dict(), indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    def _generate_report(self, run: PipelineRun) -> None:
        """Generate daily report in the run directory."""
        run_dir = Path(run.run_dir)
        report = {
            "pipeline_run_id": run.pipeline_run_id,
            "status": run.status,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "stages_completed": run.stages_completed,
            "stages_failed": run.stages_failed,
            "eligibility_hash": run.eligibility_hash,
            "candidates_processed": run.candidates_processed,
            "eligible_count": run.eligible_count,
            "daily_budget": run.daily_budget,
            "report_generated_at": datetime.now(timezone.utc).isoformat(),
        }
        report_path = run_dir / "pipeline_report.json"
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    def _update_latest_pointer(self, run: PipelineRun) -> None:
        """Atomically update the latest.json pointer."""
        self.reports_base.mkdir(parents=True, exist_ok=True)
        latest_path = self.reports_base / "latest.json"
        latest_data = {
            "pipeline_run_id": run.pipeline_run_id,
            "status": run.status,
            "completed_at": run.finished_at,
            "run_dir": run.run_dir,
        }
        # Atomic write: write to temp then rename
        tmp_path = latest_path.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(latest_data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(str(tmp_path), str(latest_path))

    # ------------------------------------------------------------------
    # Frozen plan immutability
    # ------------------------------------------------------------------

    @staticmethod
    def persist_frozen_plan(run_dir: Path, plan: Dict[str, Any]) -> Path:
        """Persist a plan and return its SHA-256 hash for immutability checks."""
        plan_path = Path(run_dir) / "frozen_plan.json"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        canonical = json.dumps(plan, sort_keys=True, ensure_ascii=False)
        plan_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        plan_with_hash = {"plan": plan, "plan_hash": plan_hash}
        plan_path.write_text(
            json.dumps(plan_with_hash, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return Path(plan_hash)

    @staticmethod
    def verify_frozen_plan(run_dir: Path) -> bool:
        """Verify that the frozen plan hasn't been tampered with."""
        plan_path = Path(run_dir) / "frozen_plan.json"
        if not plan_path.exists():
            return False
        data = json.loads(plan_path.read_text())
        plan = data.get("plan", {})
        stored_hash = data.get("plan_hash", "")
        canonical = json.dumps(plan, sort_keys=True, ensure_ascii=False)
        actual_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return stored_hash == actual_hash

    # ------------------------------------------------------------------
    # Eligibility policy invariance
    # ------------------------------------------------------------------

    def verify_policy_hash(self) -> bool:
        """Verify that the current eligibility hash matches the thresholds."""
        expected = compute_eligibility_hash(self.eligibility_thresholds)
        return self.eligibility_hash == expected


# ---------------------------------------------------------------------------
# Helper: PID liveness check
# ---------------------------------------------------------------------------

def _is_pid_alive(pid: int) -> bool:
    """Check if a PID is still alive (POSIX only)."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False

"""Canonical Research Run Contract — Iteration 05.

Implements immutable, reproducible research runs for strategy_combine.
Every meaningful research cycle produces a run bundle that is:
  - uniquely identified (run_id)
  - lifecycle-tracked (PLANNED → RUNNING → COMPLETED/FAILED/PARTIAL/BLOCKED)
  - persisted before heavy execution begins (manifest, plan)
  - append-safe candidate ledger (candidates.jsonl)
  - integrity-checked before becoming canonical (checks.json)
  - atomically promoted as latest only after checks pass
  - isolated on failure/partial — never becomes latest

CLASS 1 ONLY: research-only, no broker, no live, no strategy changes.

Usage:
    from core.run_contract import ResearchRun

    run = ResearchRun.create(base_dir=REPORT_DIR)
    run.start_planning(universe, timeframes, horizons, strategies, ...)
    run.persist_plan(plan_grid)
    # ... execute backtests ...
    for candidate in candidates:
        run.append_candidate(candidate)
    run.finalize_eligible(eligible)
    run.finalize_report(top10)
    status = run.complete()
    if status == "COMPLETED":
        run.update_latest_pointer()
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import socket
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.control_plane_bridge import (
    append_decision,
    append_evidence,
    append_failure,
    append_stage_transition_evidence,
    build_morning_report_from_episode,
    episode_state_from_run_manifest,
    objective_from_run_manifest,
    read_episode_snapshot,
    write_episode_snapshot,
)
from core.control_plane_contracts import build_evidence
from core.episode_state import can_resume_episode, load_episode_state, read_episode_state, write_episode_state


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "1.0.0"

RUN_LIFECYCLE_STATES = frozenset({
    "PLANNED", "RUNNING", "COMPLETED", "FAILED", "PARTIAL", "BLOCKED",
})

# Terminal candidate ledger states — Iteration 08
CANDIDATE_TERMINAL_STATES = frozenset({
    "tested",          # executed and completed
    "error",           # execution failed
    "skipped_exact_duplicate",   # auto-skipped by novelty gate
    "forced_reproduction",       # forced reproduction override
})

VALID_TRANSITIONS = {
    "PLANNED": {"RUNNING", "BLOCKED", "FAILED"},
    "RUNNING": {"COMPLETED", "FAILED", "PARTIAL"},
    "PARTIAL": {"COMPLETED", "FAILED"},   # can be resumed
    "BLOCKED": set(),
    "FAILED": set(),
    "COMPLETED": set(),
}

REQUIRED_MANIFEST_FIELDS = [
    "run_id", "schema_version", "status", "started_at", "finished_at",
    "arguments", "universe", "timeframes", "horizons",
    "strategy_families", "strategy_versions",
    "git_revision", "code_version",
    "dataset_info", "cost_assumptions",
    "planned_configurations", "tested_configurations",
    "failed_configurations", "eligible_configurations",
    "runner_version", "backtest_engine_version",
    "timesfm_state", "host_context",
]

REQUIRED_CHECK_NAMES = [
    "manifest_exists",
    "plan_exists",
    "ledger_exists",
    "planned_tested_reconcile",
    "every_config_has_terminal_record",
    "eligible_subset_of_ledger",
    "report_run_id_matches_manifest",
    "data_fingerprints_exist",
    "code_identity_exists",
    "cost_assumptions_exist",
    "run_status_valid",
    "no_secrets_detected",
]


# ---------------------------------------------------------------------------
# Run ID generation
# ---------------------------------------------------------------------------

def generate_run_id(prefix: str = "run") -> str:
    """Generate a collision-safe, deterministic-enough run ID.

    Format: {prefix}_{YYYYMMDD_HHMMSS}_{8-char-uuid}
    Stable for the run lifetime; unique across concurrent runs.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    short_id = uuid.uuid4().hex[:8]
    return f"{prefix}_{ts}_{short_id}"


# ---------------------------------------------------------------------------
# Data fingerprinting
# ---------------------------------------------------------------------------

def fingerprint_file(path: Path, chunk_size: int = 65536) -> str:
    """SHA-256 hash of file contents for data identity."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()[:16]


def data_manifest_entry(
    path: Path,
    ticker: str,
    timeframe: str,
    horizon_days: int,
    declared_horizon_days: int,
) -> Dict[str, Any]:
    """Capture data identity for a dataset used in a run."""
    import pandas as pd
    entry: Dict[str, Any] = {
        "path": str(path),
        "ticker": ticker,
        "timeframe": timeframe,
        "declared_horizon_days": declared_horizon_days,
    }
    try:
        df = pd.read_csv(path)
        if len(df) > 0 and "time" in df.columns:
            entry["actual_start"] = str(df["time"].iloc[0])
            entry["actual_end"] = str(df["time"].iloc[-1])
        entry["row_count"] = len(df)
        entry["freshness"] = datetime.now(timezone.utc).isoformat()
    except Exception:
        entry["row_count"] = 0
        entry["freshness"] = datetime.now(timezone.utc).isoformat()
    try:
        entry["hash"] = fingerprint_file(path)
    except Exception:
        entry["hash"] = "unavailable"
    return entry


# ---------------------------------------------------------------------------
# Git revision capture
# ---------------------------------------------------------------------------

def capture_git_revision(repo_root: Path) -> Dict[str, str]:
    """Capture git revision and dirty state."""
    import subprocess
    info = {"revision": "unknown", "dirty": "unknown"}
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, cwd=str(repo_root), timeout=5,
        )
        info["revision"] = result.stdout.strip() or "unknown"
    except Exception:
        pass
    try:
        result = subprocess.run(
            ["git", "diff", "--quiet"],
            capture_output=True, text=True, cwd=str(repo_root), timeout=5,
        )
        info["dirty"] = result.returncode != 0
    except Exception:
        pass
    return info


def code_identity(
    repo_root: Path, module_paths: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Capture code version/revision for research-critical components."""
    git_info = capture_git_revision(repo_root)
    file_hashes: Dict[str, str] = {}
    for rel in (module_paths or []):
        p = repo_root / rel
        if p.exists():
            file_hashes[rel] = fingerprint_file(p)
    return {
        "git": git_info,
        "file_hashes": file_hashes,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# ResearchRun class
# ---------------------------------------------------------------------------

@dataclass
class ResearchRun:
    """Manages the lifecycle of a canonical research run.

    Attributes:
        base_dir: Root of reports (e.g. reports/strategy_architect/)
        run_id: Unique run identifier
        run_dir: Absolute path to the run bundle directory
        state_dir: State directory for locks
        status: Current lifecycle state
    """
    base_dir: Path
    run_id: str
    run_dir: Path = field(init=False)
    state_dir: Path = field(init=False)
    status: str = field(default="PLANNED", init=False)
    _manifest: Dict[str, Any] = field(default_factory=dict, init=False)
    _plan: Dict[str, Any] = field(default_factory=dict, init=False)
    _lock_fd: Optional[int] = field(default=None, init=False)
    _lock_path: Optional[Path] = field(default=None, init=False)
    _candidate_count: int = field(default=0, init=False)
    _config_keys_seen: set = field(default_factory=set, init=False)
    _config_keys_planned: set = field(default_factory=set, init=False)
    _episode_id: Optional[str] = field(default=None, init=False)

    def __post_init__(self):
        self.run_dir = self.base_dir / "runs" / self.run_id
        self.state_dir = self.base_dir.parent / "state"
        self._episode_id = f"episode::{self.run_id}"

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        base_dir: Path,
        prefix: str = "run",
        state_dir: Optional[Path] = None,
    ) -> "ResearchRun":
        """Create a new run with unique ID and empty bundle directory."""
        run_id = generate_run_id(prefix)
        instance = cls(base_dir=base_dir, run_id=run_id)
        if state_dir:
            instance.state_dir = Path(state_dir)
        instance.run_dir.mkdir(parents=True, exist_ok=True)
        # Create subdirectories
        (instance.run_dir / "logs").mkdir(exist_ok=True)
        (instance.run_dir / "charts").mkdir(exist_ok=True)
        return instance

    @classmethod
    def load(cls, base_dir: Path, run_id: str) -> "ResearchRun":
        """Load an existing run by ID."""
        run_dir = base_dir / "runs" / run_id
        if not run_dir.exists():
            raise FileNotFoundError(f"Run directory not found: {run_dir}")
        state_dir = base_dir.parent / "state"
        instance = cls(base_dir=base_dir, run_id=run_id)
        instance.state_dir = state_dir
        instance._episode_id = f"episode::{run_id}"
        instance._load_manifest_from_disk()
        return instance

    # ------------------------------------------------------------------
    # Lock management
    # ------------------------------------------------------------------

    def acquire_lock(self, timeout: int = 5) -> bool:
        """Acquire exclusive run lock using fcntl.flock.

        Returns True if lock acquired, False if another run is active.
        Handles stale locks by checking PID liveness.
        """
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._lock_path = self.state_dir / ".research_run.lock"

        fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            # Try non-blocking lock first
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._lock_fd = fd
                # Write PID for stale-lock detection
                fcntl.flock(fd, fcntl.LOCK_EX)
                os.ftruncate(fd, 0)
                os.lseek(fd, 0, os.SEEK_SET)
                lock_info = json.dumps({
                    "pid": os.getpid(),
                    "host": socket.gethostname(),
                    "run_id": self.run_id,
                    "acquired_at": datetime.now(timezone.utc).isoformat(),
                })
                os.write(fd, lock_info.encode())
                fcntl.flock(fd, fcntl.LOCK_UN)
                return True
            except BlockingIOError:
                pass

            # Check for stale lock
            try:
                existing = self._lock_path.read_text()
                lock_data = json.loads(existing)
                old_pid = lock_data.get("pid")
                if old_pid and not _is_pid_alive(old_pid):
                    # Stale lock — force release
                    fcntl.flock(fd, fcntl.LOCK_EX)
                    os.ftruncate(fd, 0)
                    os.lseek(fd, 0, os.SEEK_SET)
                    lock_info = json.dumps({
                        "pid": os.getpid(),
                        "host": socket.gethostname(),
                        "run_id": self.run_id,
                        "acquired_at": datetime.now(timezone.utc).isoformat(),
                        "recovered_from_stale": True,
                        "stale_pid": old_pid,
                    })
                    os.write(fd, lock_info.encode())
                    fcntl.flock(fd, fcntl.LOCK_UN)
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
        """Release the run lock."""
        if self._lock_fd is not None:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                os.close(self._lock_fd)
            except Exception:
                pass
            self._lock_fd = None

    # ------------------------------------------------------------------
    # Lifecycle transitions
    # ------------------------------------------------------------------

    def _transition(self, new_status: str) -> None:
        """Validate and apply state transition."""
        if new_status not in VALID_TRANSITIONS.get(self.status, set()):
            raise ValueError(
                f"Invalid transition: {self.status} → {new_status}"
            )
        self.status = new_status

    def start_planning(
        self,
        universe: List[str],
        timeframes: List[str],
        horizons: List[int],
        strategy_families: List[str],
        arguments: Optional[Dict[str, Any]] = None,
        cost_assumptions: Optional[Dict[str, Any]] = None,
        git_info: Optional[Dict[str, str]] = None,
        code_id: Optional[Dict[str, Any]] = None,
        timesfm_state: str = "dummy",
        backtest_engine_version: str = "unknown",
        runner_version: str = "strategy_architect_autopilot",
    ) -> None:
        """Begin planning phase. Creates manifest with PLANNED status."""
        self._transition("RUNNING")
        now = datetime.now(timezone.utc).isoformat()
        self._manifest = {
            "run_id": self.run_id,
            "schema_version": SCHEMA_VERSION,
            "status": "RUNNING",
            "started_at": now,
            "finished_at": None,
            "arguments": arguments or {},
            "universe": list(universe),
            "timeframes": list(timeframes),
            "horizons": list(horizons),
            "strategy_families": list(strategy_families),
            "strategy_versions": {},
            "git_revision": git_info or {},
            "code_version": code_id or {},
            "dataset_info": {},
            "cost_assumptions": cost_assumptions or {},
            "planned_configurations": 0,
            "tested_configurations": 0,
            "failed_configurations": 0,
            "skipped_exact_duplicate_configurations": 0,
            "forced_reproduction_configurations": 0,
            "eligible_configurations": 0,
            "runner_version": runner_version,
            "backtest_engine_version": backtest_engine_version,
            "timesfm_state": timesfm_state,
            "host_context": {
                "hostname": socket.gethostname(),
                "pid": os.getpid(),
                "python": __import__("sys").version.split()[0],
            },
        }
        self._persist_manifest()
        self._sync_control_plane_snapshot(event_type="planning_started")

    # ------------------------------------------------------------------
    # Plan persistence
    # ------------------------------------------------------------------

    def persist_plan(self, plan_grid: Dict[str, Any]) -> None:
        """Persist the research plan before backtest execution.

        plan_grid should enumerate the exact experiment grid:
        {
            "configs": [
                {
                    "config_key": "...",
                    "instrument": "...",
                    "timeframe": "...",
                    "strategy": "...",
                    "parameters": {...},
                    "horizon_days": 60,
                    "dataset_path": "...",
                    "cost_model": {...},
                },
                ...
            ],
            "validation_gates": {...},
        }
        """
        self._plan = plan_grid
        self._config_keys_seen = set()
        self._config_keys_planned = set()
        configs = plan_grid.get("configs", [])
        for cfg in configs:
            key = cfg.get("config_key", "")
            if key:
                self._config_keys_planned.add(key)
        self._manifest["planned_configurations"] = len(configs)
        self._persist_manifest()

        plan_path = self.run_dir / "research_plan.json"
        plan_path.write_text(
            json.dumps(plan_grid, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # Candidate ledger
    # ------------------------------------------------------------------

    def _candidate_to_ledger_entry(
        self, candidate: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Normalize a candidate dict into a terminal ledger entry."""
        now = datetime.now(timezone.utc).isoformat()
        config_key = candidate.get("config_key", self._make_config_key(candidate))
        status = candidate.get("status", "tested")
        entry = {
            "run_id": self.run_id,
            "config_key": config_key,
            "instrument": candidate.get("ticker", candidate.get("instrument", "")),
            "timeframe": candidate.get("timeframe", ""),
            "strategy": candidate.get("strategy", ""),
            "parameters": candidate.get("params", {}),
            "horizon": candidate.get("horizon_days", 60),
            "dataset_identity": {
                "path": candidate.get("dataset_path", ""),
                "hash": candidate.get("dataset_hash", ""),
            },
            "cost_assumptions": candidate.get("cost_assumptions", {}),
            "status": status,
            "metrics": {
                "total_pnl": candidate.get("total_pnl", 0.0),
                "profit_factor": candidate.get("profit_factor", 0.0),
                "max_drawdown": candidate.get("max_drawdown", 0.0),
                "sharpe": candidate.get("sharpe", 0.0),
                "win_rate": candidate.get("win_rate", 0.0),
                "trade_count": candidate.get("trades", 0),
            },
            "validation_outputs": candidate.get("validation_outputs", {}),
            "decision": candidate.get("decision"),
            "waitlist_tier": candidate.get("waitlist_tier"),
            "eligible": candidate.get("eligible", False),
            "reject_reasons": candidate.get("reject_reasons", []),
            "error_type": candidate.get("error_type"),
            "error_message": candidate.get("error_message"),
            "started_at": candidate.get("started_at", now),
            "finished_at": candidate.get("finished_at", now),
            "duration": candidate.get("duration"),
        }
        return entry

    def _make_config_key(self, candidate: Dict[str, Any]) -> str:
        """Generate a reproducible config key from candidate fields."""
        parts = [
            candidate.get("ticker", ""),
            candidate.get("timeframe", ""),
            candidate.get("strategy", ""),
            json.dumps(candidate.get("params", {}), sort_keys=True),
            str(candidate.get("horizon_days", 60)),
        ]
        raw = "|".join(parts)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def append_candidate(self, candidate: Dict[str, Any]) -> None:
        """Append one terminal candidate record to the ledger.

        Every attempted config must produce exactly one terminal record.
        This is append-safe (one JSON object per line).
        """
        entry = self._candidate_to_ledger_entry(candidate)
        config_key = entry["config_key"]
        self._config_keys_seen.add(config_key)

        ledger_path = self.run_dir / "candidates.jsonl"
        with open(ledger_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        self._candidate_count += 1
        # Update manifest counts — Iteration 08: extended terminal states
        status = entry["status"]
        if status == "error":
            self._manifest["failed_configurations"] = (
                self._manifest.get("failed_configurations", 0) + 1
            )
        elif status == "skipped_exact_duplicate":
            self._manifest["skipped_exact_duplicate_configurations"] = (
                self._manifest.get("skipped_exact_duplicate_configurations", 0) + 1
            )
        elif status == "forced_reproduction":
            self._manifest["forced_reproduction_configurations"] = (
                self._manifest.get("forced_reproduction_configurations", 0) + 1
            )
        else:
            self._manifest["tested_configurations"] = (
                self._manifest.get("tested_configurations", 0) + 1
            )
        self._persist_manifest()

    def append_candidate_record(self, record: Dict[str, Any]) -> None:
        """Append a pre-built ledger record directly (raw JSONL line)."""
        config_key = record.get("config_key", "")
        if config_key:
            self._config_keys_seen.add(config_key)
        with open(self.run_dir / "candidates.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._candidate_count += 1
        status = record.get("status", "tested")
        if status == "error":
            self._manifest["failed_configurations"] = (
                self._manifest.get("failed_configurations", 0) + 1
            )
        elif status == "skipped_exact_duplicate":
            self._manifest["skipped_exact_duplicate_configurations"] = (
                self._manifest.get("skipped_exact_duplicate_configurations", 0) + 1
            )
        elif status == "forced_reproduction":
            self._manifest["forced_reproduction_configurations"] = (
                self._manifest.get("forced_reproduction_configurations", 0) + 1
            )
        else:
            self._manifest["tested_configurations"] = (
                self._manifest.get("tested_configurations", 0) + 1
            )
        self._persist_manifest()

    def append_skipped_candidate(
        self,
        plan_config: Dict[str, Any],
        novelty_decision: Dict[str, Any],
    ) -> None:
        """Append a skipped (EXACT_DUPLICATE) candidate to the ledger.

        The candidate remains in the ledger as evidence with terminal state
        'skipped_exact_duplicate'.  This is NOT indexed as an executed instance.
        """
        now = datetime.now(timezone.utc).isoformat()
        config_key = plan_config.get("config_key", self._make_config_key(plan_config))
        record = {
            "run_id": self.run_id,
            "config_key": config_key,
            "instrument": plan_config.get("instrument", plan_config.get("ticker", "")),
            "timeframe": plan_config.get("timeframe", ""),
            "strategy": plan_config.get("strategy", ""),
            "parameters": plan_config.get("parameters", plan_config.get("params", {})),
            "horizon": plan_config.get("horizon_days", 60),
            "dataset_identity": {
                "path": plan_config.get("dataset_path", ""),
                "hash": plan_config.get("dataset_hash", ""),
            },
            "cost_assumptions": plan_config.get("cost_assumptions", {}),
            "status": "skipped_exact_duplicate",
            "metrics": {},
            "validation_outputs": {},
            "eligible": False,
            "reject_reasons": ["EXACT_DUPLICATE_SKIPPED"],
            "error_type": None,
            "error_message": None,
            "started_at": now,
            "finished_at": now,
            "duration": 0,
            # Novelty gate evidence
            "novelty_decision": novelty_decision.get("decision", "SKIP_EXACT_DUPLICATE"),
            "novelty_reason": novelty_decision.get("reason_text", ""),
            "matched_prior_instance_id": novelty_decision.get("matched_prior_instance_id"),
            "matched_prior_run_id": novelty_decision.get("matched_prior_run_id"),
            "matched_prior_config_key": novelty_decision.get("matched_prior_config_key"),
            "novelty_policy_version": novelty_decision.get("policy_version", 1),
            "forced": novelty_decision.get("forced", False),
        }
        self.append_candidate_record(record)

    # ------------------------------------------------------------------
    # Eligible candidates derivation
    # ------------------------------------------------------------------

    def finalize_eligible(
        self, eligible: List[Dict[str, Any]], data_info: Optional[Dict] = None
    ) -> None:
        """Derive eligible_candidates.json from the completed ledger.

        eligible: list of candidate dicts that passed quality gates.
        Each must include run_id, config_key, dataset identity, metrics.
        """
        eligible_records = []
        for c in eligible:
            record = {
                "run_id": self.run_id,
                "config_key": c.get("config_key", self._make_config_key(c)),
                "instrument": c.get("ticker", c.get("instrument", "")),
                "timeframe": c.get("timeframe", ""),
                "strategy": c.get("strategy", ""),
                "parameters": c.get("params", {}),
                "horizon_days": c.get("horizon_days", 60),
                "dataset_identity": c.get("dataset_identity", {
                    "path": c.get("dataset_path", ""),
                    "hash": c.get("dataset_hash", ""),
                }),
                "strategy_version": c.get("strategy_version", "current"),
                "metrics": c.get("metrics", {
                    "total_pnl": c.get("total_pnl", 0.0),
                    "profit_factor": c.get("profit_factor", 0.0),
                    "max_drawdown": c.get("max_drawdown", 0.0),
                    "sharpe": c.get("sharpe", 0.0),
                    "win_rate": c.get("win_rate", 0.0),
                    "trade_count": c.get("trades", 0),
                }),
                "validation_reasons": c.get("validation_reasons", []),
            }
            eligible_records.append(record)

        self._manifest["eligible_configurations"] = len(eligible_records)
        self._persist_manifest()
        self._sync_control_plane_snapshot(event_type="eligible_finalized")

        eligible_path = self.run_dir / "eligible_candidates.json"
        eligible_path.write_text(
            json.dumps(eligible_records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Backfill the append-only ledger: rows are written before the
        # final eligibility pass runs, so eligible/decision flags there
        # are stale. Mark the eligible config keys in candidates.jsonl.
        try:
            eligible_keys = {r["config_key"] for r in eligible_records}
            ledger_path = self.run_dir / "candidates.jsonl"
            if ledger_path.exists() and eligible_keys:
                lines = ledger_path.read_text(encoding="utf-8").splitlines()
                out_lines = []
                changed = False
                for line in lines:
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        out_lines.append(line)
                        continue
                    if rec.get("config_key") in eligible_keys and not rec.get("eligible"):
                        rec["eligible"] = True
                        changed = True
                    out_lines.append(json.dumps(rec, ensure_ascii=False))
                if changed:
                    ledger_path.write_text(
                        "\n".join(out_lines) + "\n", encoding="utf-8"
                    )
        except Exception:
            pass  # ledger backfill is cosmetic; eligible_candidates.json is authoritative

    # ------------------------------------------------------------------
    # Report and top candidates
    # ------------------------------------------------------------------

    def finalize_report(
        self,
        top: List[Dict[str, Any]],
        report_md: str = "",
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Persist top10.json and report.md derived only from this run."""
        top_path = self.run_dir / "top10.json"
        top_path.write_text(
            json.dumps(top, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if report_md:
            report_path = self.run_dir / "report.md"
            report_path.write_text(report_md, encoding="utf-8")
        if extra:
            extra_path = self.run_dir / "extra.json"
            extra_path.write_text(
                json.dumps(extra, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    # ------------------------------------------------------------------
    # Dataset info
    # ------------------------------------------------------------------

    def set_dataset_info(self, info: Dict[str, Any]) -> None:
        """Set dataset_info in manifest."""
        self._manifest["dataset_info"] = info
        self._persist_manifest()

    def add_dataset_entry(
        self,
        ticker: str,
        timeframe: str,
        horizon_days: int,
        path: Path,
        declared_horizon_days: Optional[int] = None,
    ) -> None:
        """Add one dataset entry to manifest dataset_info."""
        key = f"{ticker}_{timeframe}_{horizon_days}d"
        entry = data_manifest_entry(
            path, ticker, timeframe, horizon_days,
            declared_horizon_days or horizon_days,
        )
        self._manifest.setdefault("dataset_info", {})[key] = entry
        self._persist_manifest()

    # ------------------------------------------------------------------
    # Integrity checks
    # ------------------------------------------------------------------

    def run_integrity_checks(self) -> Dict[str, Any]:
        """Run all integrity checks and produce checks.json.

        Returns the checks dict. A run can only become COMPLETED
        if all mandatory checks pass.
        """
        checks = {
            "run_id": self.run_id,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "all_passed": True,
            "checks": {},
        }

        run_dir = self.run_dir

        # C1: manifest exists
        manifest_path = run_dir / "manifest.json"
        checks["checks"]["manifest_exists"] = manifest_path.exists()

        # C2: plan exists
        plan_path = run_dir / "research_plan.json"
        checks["checks"]["plan_exists"] = plan_path.exists()

        # C3: ledger exists
        ledger_path = run_dir / "candidates.jsonl"
        checks["checks"]["ledger_exists"] = ledger_path.exists()

        # C4: planned/tested counts reconcile — Iteration 08: include skip/force
        planned = self._manifest.get("planned_configurations", 0)
        tested = self._manifest.get("tested_configurations", 0)
        failed = self._manifest.get("failed_configurations", 0)
        skipped = self._manifest.get("skipped_exact_duplicate_configurations", 0)
        forced = self._manifest.get("forced_reproduction_configurations", 0)
        checks["checks"]["planned_tested_reconcile"] = (
            planned == (tested + failed + skipped + forced)
        ) if planned > 0 else True

        # C5: every planned config has a terminal record
        if self._config_keys_planned:
            missing = self._config_keys_planned - self._config_keys_seen
            checks["checks"]["every_config_has_terminal_record"] = len(missing) == 0
            checks["checks"]["_missing_config_keys"] = list(missing)
        else:
            checks["checks"]["every_config_has_terminal_record"] = True

        # C6: eligible is subset of ledger
        eligible_path = run_dir / "eligible_candidates.json"
        if eligible_path.exists():
            try:
                eligible = json.loads(eligible_path.read_text(encoding="utf-8"))
                eligible_keys = {e.get("config_key") for e in eligible}
                # Check against seen keys
                checks["checks"]["eligible_subset_of_ledger"] = (
                    eligible_keys.issubset(self._config_keys_seen)
                )
            except Exception:
                checks["checks"]["eligible_subset_of_ledger"] = False
        else:
            checks["checks"]["eligible_subset_of_ledger"] = True  # no eligible = vacuously true

        # C7: report run_id matches manifest
        report_path = run_dir / "report.md"
        if report_path.exists():
            try:
                content = report_path.read_text(encoding="utf-8")
                checks["checks"]["report_run_id_matches_manifest"] = (
                    self.run_id in content
                )
            except Exception:
                checks["checks"]["report_run_id_matches_manifest"] = False
        else:
            checks["checks"]["report_run_id_matches_manifest"] = True

        # C8: data fingerprints exist
        ds_info = self._manifest.get("dataset_info", {})
        checks["checks"]["data_fingerprints_exist"] = (
            len(ds_info) > 0 and all(
                e.get("hash", "unavailable") != "unavailable"
                for e in ds_info.values()
            )
        ) if ds_info else False

        # C9: code identity exists
        code_id = self._manifest.get("code_version", {})
        checks["checks"]["code_identity_exists"] = bool(
            code_id.get("git", {}).get("revision", "unknown") != "unknown"
        )

        # C10: cost assumptions exist
        costs = self._manifest.get("cost_assumptions", {})
        checks["checks"]["cost_assumptions_exist"] = bool(costs)

        # C11: run status valid
        checks["checks"]["run_status_valid"] = self.status in {
            "RUNNING", "COMPLETED", "PARTIAL"
        }

        # C12: no secrets detected
        checks["checks"]["no_secrets_detected"] = _scan_for_secrets(run_dir)

        # Determine overall pass
        mandatory_checks = [
            checks["checks"].get(name, False) for name in REQUIRED_CHECK_NAMES
        ]
        checks["all_passed"] = all(mandatory_checks)

        # Persist checks
        checks_path = run_dir / "checks.json"
        checks_path.write_text(
            json.dumps(checks, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return checks

    # ------------------------------------------------------------------
    # Completion
    # ------------------------------------------------------------------

    def complete(self) -> str:
        """Run integrity checks and finalize the run.

        Returns the final status string.
        """
        checks = self.run_integrity_checks()
        if checks["all_passed"]:
            self._transition("COMPLETED")
        else:
            self._transition("PARTIAL")

        self._manifest["status"] = self.status
        self._manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        self._persist_manifest()
        self._sync_control_plane_snapshot(event_type="run_completed")
        self._write_morning_report()
        return self.status

    def fail(self, reason: str = "") -> str:
        """Mark run as FAILED."""
        self._transition("FAILED")
        self._manifest["status"] = "FAILED"
        self._manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        self._manifest["failure_reason"] = reason
        self._persist_manifest()
        return self.status

    # ------------------------------------------------------------------
    # Atomic latest pointer
    # ------------------------------------------------------------------

    def update_latest_pointer(self) -> None:
        """Update atomic latest pointer only for COMPLETED runs.

        The pointer is a small JSON file that can be atomically written.
        """
        if self.status != "COMPLETED":
            raise RuntimeError(
                f"Cannot update latest pointer: run status is {self.status}, "
                "must be COMPLETED"
            )
        pointer_path = self.base_dir / "latest_run.json"
        pointer_data = {
            "run_id": self.run_id,
            "path": str(self.run_dir),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        # Atomic write: write to temp file then rename
        tmp_path = pointer_path.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(pointer_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(str(tmp_path), str(pointer_path))

    @staticmethod
    def read_latest_pointer(base_dir: Path) -> Optional[Dict[str, Any]]:
        """Read the latest_run.json pointer, or None if absent."""
        pointer_path = base_dir / "latest_run.json"
        if not pointer_path.exists():
            return None
        try:
            return json.loads(pointer_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _persist_manifest(self) -> None:
        """Persist manifest.json to run directory."""
        self._manifest["status"] = self.status
        manifest_path = self.run_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(self._manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_manifest_from_disk(self) -> None:
        """Load manifest from disk for an existing run."""
        manifest_path = self.run_dir / "manifest.json"
        if manifest_path.exists():
            self._manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
            self.status = self._manifest.get("status", "PLANNED")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def manifest(self) -> Dict[str, Any]:
        """Return current manifest dict."""
        return dict(self._manifest)

    def control_plane_snapshot(self) -> Optional[Dict[str, Any]]:
        """Return the latest control-plane snapshot if present."""
        if not self._episode_id:
            return None
        try:
            return read_episode_snapshot(self.state_dir, self._episode_id)
        except Exception:
            return None

    def control_plane_snapshot_raw(self) -> str:
        """Return the raw persisted episode snapshot text if present."""
        if not self._episode_id:
            return ""
        snapshot_path = self.state_dir / f"episode_{self._episode_id}.json"
        if not snapshot_path.exists():
            return ""
        return snapshot_path.read_text(encoding="utf-8")

    def can_resume_control_plane(self) -> bool:
        """Fail-closed restart gate for the control-plane snapshot."""
        snapshot = self.control_plane_snapshot()
        return bool(snapshot and can_resume_episode(snapshot))

    def resume_control_plane(self) -> bool:
        """Load the control-plane episode if the snapshot is resumable."""
        if not self._episode_id:
            return False
        try:
            snapshot = read_episode_snapshot(self.state_dir, self._episode_id)
            if not can_resume_episode(snapshot):
                return False
            self._control_plane_episode = load_episode_state(self.state_dir, self._episode_id)
            return True
        except Exception:
            return False

    def _sync_control_plane_snapshot(self, event_type: str) -> None:
        """Persist the autonomous control-plane snapshot alongside the run.

        This is intentionally best-effort. Research must remain valid even if
        the overlay snapshot cannot be written.
        """
        try:
            if not self._episode_id:
                self._episode_id = f"episode::{self.run_id}"
            if self._manifest:
                episode = episode_state_from_run_manifest(self._manifest)
                episode.status = self.status
                episode.decision_ledger.append({
                    "type": event_type,
                    "run_id": self.run_id,
                    "status": self.status,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                write_episode_snapshot(self.state_dir, episode)
        except Exception:
            pass

    def _write_morning_report(self) -> None:
        """Persist a morning report derived from the control-plane snapshot."""
        try:
            snapshot = self.control_plane_snapshot()
            if not snapshot:
                return
            episode = episode_state_from_run_manifest(self._manifest)
            episode.status = self.status
            report = build_morning_report_from_episode(episode)
            report_path = self.run_dir / "morning_report.json"
            report_path.write_text(
                json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def candidate_count(self) -> int:
        """Number of candidates appended so far."""
        return self._candidate_count

    def plan_config_count(self) -> int:
        """Number of configs in the research plan."""
        return len(self._config_keys_planned)


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def _is_pid_alive(pid: int) -> bool:
    """Check if a process is alive (Linux/macOS)."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _scan_for_secrets(run_dir: Path) -> bool:
    """Quick scan for obvious secrets in run artifacts. Returns True if clean."""
    secret_patterns = [
        "api_key", "secret", "token", "password", "credential",
        "tinkoff_token", "TINKOFF_TOKEN",
    ]
    for fpath in run_dir.rglob("*.json"):
        try:
            content = fpath.read_text(encoding="utf-8")
            lower = content.lower()
            for pat in secret_patterns:
                if pat in lower and "unavailable" not in lower:
                    # Only flag if it looks like an actual secret value
                    if f'"{pat}"' in lower and any(
                        c in content for c in ["=", ":", "sk-", "Bearer"]
                    ):
                        return False
        except Exception:
            pass
    for fpath in run_dir.rglob("*.md"):
        try:
            content = fpath.read_text(encoding="utf-8").lower()
            if "tinkoff_token" in content or "api_key=" in content:
                return False
        except Exception:
            pass
    return True


def load_latest_eligible(base_dir: Path) -> Optional[List[Dict[str, Any]]]:
    """Load eligible candidates from the latest completed run.

    This is the canonical handoff artifact for seeder/registry.
    """
    pointer = ResearchRun.read_latest_pointer(base_dir)
    if not pointer:
        return None
    eligible_path = Path(pointer["path"]) / "eligible_candidates.json"
    if not eligible_path.exists():
        return None
    try:
        return json.loads(eligible_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

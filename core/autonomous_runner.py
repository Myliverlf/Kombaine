"""Persistent autonomous runner for Mission Control + layered agent runtime.

This is the final outer loop: Mission Control chooses to run canonical research,
and the layered runtime iterates until convergence/stop policy ends the episode.
"""
from __future__ import annotations

import json
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from core.mission_control import MissionControlOrchestrator, MCAction
from core.layered_agent_runtime import LayeredAgentRuntime


RUNNER_VERSION = "1.0.0"
DEFAULT_MAX_RUNS = 1
DEFAULT_ITERATIONS_PER_RUN = 3
DEFAULT_SCOPE_ID = "autonomous-runner"


@dataclass
class AutonomousRunRecord:
    run_id: str
    cycle_id: Optional[str]
    scope_id: str
    started_at: str
    finished_at: Optional[str] = None
    mission_control_result: Dict[str, Any] = field(default_factory=dict)
    layered_runtime_result: Dict[str, Any] = field(default_factory=dict)
    status: str = "RUNNING"

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["runner_version"] = RUNNER_VERSION
        return payload


class AutonomousRunner:
    def __init__(self, project_root: Path | str, scope_id: str = DEFAULT_SCOPE_ID):
        self.project_root = Path(project_root)
        self.scope_id = scope_id
        self.mc = MissionControlOrchestrator(self.project_root)
        self.state_dir = Path(tempfile.mkdtemp(prefix=f"hermes_autonomous_runs_{self.scope_id}_"))

    def _run_dir(self, run_id: str) -> Path:
        run_dir = self.state_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def run_once(self, goal: str, iterations: int = DEFAULT_ITERATIONS_PER_RUN) -> Dict[str, Any]:
        run_id = f"ar_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}"
        record = AutonomousRunRecord(
            run_id=run_id,
            cycle_id=None,
            scope_id=self.scope_id,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        mc_result = self.mc.run_cycle()
        record.cycle_id = mc_result.get("cycle_id")
        record.mission_control_result = mc_result
        if mc_result.get("primary_action") == MCAction.RUN_CANONICAL_RESEARCH.value:
            runtime = LayeredAgentRuntime(goal=goal, scope_id=self.scope_id)
            layered = runtime.run_iterative(max_cycles=iterations)
            record.layered_runtime_result = {
                "report": runtime.report(),
                "snapshot": layered,
            }
            record.status = "COMPLETED"
        else:
            record.status = "SKIPPED"
            record.layered_runtime_result = {"reason": "Mission Control chose non-research action"}
        record.finished_at = datetime.now(timezone.utc).isoformat()
        out_dir = self._run_dir(run_id)
        out_path = out_dir / "run.json"
        payload = record.to_dict()
        payload["artifact_root"] = str(self.state_dir)
        payload["artifact_path"] = str(out_path)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    def run_forever(self, goal: str, iterations: int = DEFAULT_ITERATIONS_PER_RUN, max_runs: int = DEFAULT_MAX_RUNS) -> Dict[str, Any]:
        latest: Dict[str, Any] = {}
        for _ in range(max_runs):
            latest = self.run_once(goal=goal, iterations=iterations)
            if latest.get("status") != "COMPLETED":
                break
        return latest

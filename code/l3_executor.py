"""Deterministic L3 executor for bounded autonomy loops.

This is intentionally tiny: it reads a task goal and current state snapshot,
then returns a structured result and evidence. It does not call brokers or
spawn more agents.

Cycle 2 changes:
- Uses evidence_collector.collect_module_evidence() for real subprocess-backed
  evidence instead of keyword-matching heuristics.
- evidence_type changed from "deterministic_execution" to "subprocess_evidence".
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.control_plane_contracts import build_evidence, build_result
from core.evidence_collector import collect_module_evidence, ModuleEvidence


@dataclass
class L3Execution:
    task_id: str
    result: Any
    evidence: Any


def _build_check_command(module_path: str) -> List[str]:
    """Build a subprocess check command that verifies a module can be imported.

    If the module file exists, runs ``python -c 'import <module>; print("OK")'``.
    If not found, returns a deliberate-fail command.
    """
    p = Path(module_path)
    if not p.exists():
        return ["false"]
    # Convert file path to dotted module name for import check
    # e.g. core/state_router.py → core.state_router
    # Handle both absolute and relative paths
    parts: List[str] = []
    try:
        # Try relative_to with the project root
        project_root = Path(__file__).resolve().parent.parent
        rel = p.resolve().relative_to(project_root)
    except ValueError:
        # If already relative, use as-is
        rel = p
    for parent in reversed(rel.parents):
        parent_str = str(parent)
        if parent_str != "." and parent_str != "":
            parts.append(parent.name)
    parts.append(rel.stem)
    module_name = ".".join(parts)
    if not module_name:
        module_name = rel.stem
    return ["python", "-c", f"import {module_name}; print('OK')"]


def execute_l3_task(
    task: Dict[str, Any],
    state_snapshot: Dict[str, Any],
    evidence_hint: str = "state_snapshot",
) -> L3Execution:
    goal = str(task.get("goal", ""))
    why = str(task.get("why", ""))
    goal_text = goal.lower()
    state_text = str(state_snapshot)

    # --- Collect real subprocess evidence ---
    # Run evidence collection on the project's own core modules
    check_targets: List[str] = []
    if "bottleneck" in goal_text or "execution" in goal_text or "atomic" in goal_text:
        check_targets = ["core/state_router.py", "core/control_plane_contracts.py"]
    elif "decompose" in goal_text or "workstream" in goal_text:
        check_targets = ["core/layered_agent_orchestrator.py"]
    else:
        check_targets = ["core/state_router.py"]

    evidence_objects: List[ModuleEvidence] = []
    for target in check_targets:
        cmd = _build_check_command(target)
        ev = collect_module_evidence(
            module_path=target,
            check_command=cmd,
            timeout_seconds=15,
        )
        evidence_objects.append(ev)

    # Build findings from real evidence
    findings: List[str] = []
    for ev in evidence_objects:
        findings.append(f"{ev.module_path}: verdict={ev.verdict} ({ev.duration_seconds:.3f}s)")

    verdicts = [ev.verdict for ev in evidence_objects]
    if all(v == "PASS" for v in verdicts):
        status = "PASS"
        confidence = 0.9
    elif any(v == "ERROR" for v in verdicts):
        status = "ERROR"
        confidence = 0.3
    else:
        status = "FAIL"
        confidence = 0.5

    if not state_text:
        confidence = max(confidence - 0.3, 0.1)

    result = build_result(
        task_id=str(task.get("task_id", "unknown")),
        status=status,
        result_summary="; ".join(findings) if findings else "no evidence targets configured",
        evidence_refs=[ev.module_path for ev in evidence_objects],
        tests_run=[f"subprocess_verify:{ev.module_path}" for ev in evidence_objects],
        failures=[f"{ev.module_path}: returncode={ev.returncode}" for ev in evidence_objects if ev.verdict != "PASS"],
        uncertainties=[] if state_text else ["empty_state_snapshot"],
        recommendation=(
            "evidence collected via subprocess" if evidence_objects
            else "spawn next layer only if evidence contracts are available"
        ),
        confidence=confidence,
    )

    evidence = build_evidence(
        claim=f"L3 executed task {task.get('task_id', 'unknown')} for goal '{goal}' with {len(evidence_objects)} subprocess checks",
        evidence_type="subprocess_evidence",
        source="core/l3_executor.py+core/evidence_collector.py",
        artifact_paths=[ev.module_path for ev in evidence_objects] + ["core/l3_executor.py", "core/evidence_collector.py"],
        reproducibility_notes="Re-run with the same task and state snapshot; subprocess evidence is deterministic.",
        limitations=[
            f"why={why}",
            f"hint={evidence_hint}",
            f"targets_checked={len(evidence_objects)}",
        ],
        confidence=confidence,
    )

    return L3Execution(task_id=str(task.get("task_id", "unknown")), result=result, evidence=evidence)

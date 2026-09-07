"""Bridge between the autonomous control plane and existing combine contracts.

This module adapts existing canonical run/stage artifacts into control-plane
state snapshots and evidence records.

No trading logic. No broker calls. No registry mutation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.control_plane_contracts import (
    EpisodeState,
    EvidenceContract,
    ObjectiveContract,
    ResultContract,
    TaskContract,
    build_evidence,
    build_objective,
    build_result,
    build_task,
)
from core.control_plane_reporting import MorningReport
from core.episode_state import read_episode_state, write_episode_state


CONTROL_PLANE_VERSION = "1.0.0"


def objective_from_run_manifest(manifest: Dict[str, Any], title: str = "Autonomous combine improvement", target_system: str = "strategy_combine") -> ObjectiveContract:
    scope = ["research", "selection", "evidence", "state", "governance"]
    constraints: List[str] = [
        "paper-only",
        "evidence-first",
        "no broker mutation",
        "no second orchestrator",
        "bounded episodes only",
    ]
    stop_conditions: List[str] = []
    if manifest.get("status") and manifest.get("status") != "COMPLETED":
        stop_conditions.append("current run is not completed")
    return build_objective(
        objective_id=f"objective::{manifest.get('run_id', 'unknown')}",
        title=title,
        target_system=target_system,
        scope=scope,
        out_of_scope=["live trading changes", "broker mutation"],
        constraints=constraints,
        stop_conditions=stop_conditions,
        quality_bar=["fresh evidence", "independent verification", "bounded work"],
        priority="high",
        allowed_actions=["observe", "hypothesize", "plan", "decompose", "test", "critique", "measure"],
        forbidden_actions=["broker call", "registry mutation", "unsafe live execution"],
        revision=1,
    )


def episode_state_from_run_manifest(manifest: Dict[str, Any]) -> EpisodeState:
    objective = objective_from_run_manifest(manifest)
    budgets = {
        "tokens": int(manifest.get("planned_configurations", 0)) * 10,
        "iterations": 12,
        "wall_time_seconds": 6 * 3600,
        "parallel_agents": 3,
        "model_calls": 120,
        "failed_attempts": 5,
    }
    episode = EpisodeState(
        episode_id=f"episode::{manifest.get('run_id', 'unknown')}",
        objective=objective,
        status="PLANNED",
        budgets=budgets,
        current_task_ids=[],
        evidence_ledger=[],
        decision_ledger=[],
        failure_ledger=[],
    )
    episode.decision_ledger.append({
        "type": "episode_initialized",
        "run_id": manifest.get("run_id"),
        "status": manifest.get("status"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    return episode


def task_from_goal(
    task_id: str,
    objective: ObjectiveContract,
    role: str,
    goal: str,
    why: str,
    inputs: List[str],
    expected_output: List[str],
    acceptance_criteria: List[str],
    failure_criteria: List[str],
    budget: Dict[str, Any],
    **kwargs: Any,
) -> TaskContract:
    return build_task(
        task_id=task_id,
        objective_id=objective.objective_id,
        role=role,
        goal=goal,
        why=why,
        inputs=inputs,
        expected_output=expected_output,
        acceptance_criteria=acceptance_criteria,
        failure_criteria=failure_criteria,
        budget=budget,
        **kwargs,
    )


def result_with_evidence(
    task_id: str,
    status: str,
    result_summary: str,
    evidence_claims: List[EvidenceContract],
    **kwargs: Any,
) -> ResultContract:
    evidence_refs = [e.claim for e in evidence_claims]
    return build_result(
        task_id=task_id,
        status=status,
        result_summary=result_summary,
        evidence_refs=evidence_refs,
        **kwargs,
    )


def append_evidence(episode: EpisodeState, evidence: EvidenceContract) -> EpisodeState:
    payload = evidence.to_dict()
    episode.evidence_ledger.append(payload)
    episode.updated_at = datetime.now(timezone.utc).isoformat()
    episode.version += 1
    return episode


def append_decision(episode: EpisodeState, decision: Dict[str, Any]) -> EpisodeState:
    entry = dict(decision)
    entry.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    episode.decision_ledger.append(entry)
    episode.updated_at = datetime.now(timezone.utc).isoformat()
    episode.version += 1
    return episode


def append_failure(episode: EpisodeState, failure: Dict[str, Any]) -> EpisodeState:
    entry = dict(failure)
    entry.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    episode.failure_ledger.append(entry)
    episode.updated_at = datetime.now(timezone.utc).isoformat()
    episode.version += 1
    return episode


def episode_snapshot_path(state_dir: Path, episode_id: str) -> Path:
    return Path(state_dir) / f"snapshot_{episode_id}.json"


def write_episode_snapshot(state_dir: Path, episode: EpisodeState) -> Path:
    return write_episode_state(state_dir, episode)


def read_episode_snapshot(state_dir: Path, episode_id: str) -> Dict[str, Any]:
    return read_episode_state(state_dir, episode_id)


def control_plane_evidence_from_stage_transition(stage_transition: Dict[str, Any]) -> EvidenceContract:
    claim = f"stage transition {stage_transition.get('from_stage')} → {stage_transition.get('to_stage')}"
    evidence_type = "stage_transition"
    source = "core/stage_machine.py"
    artifact_paths = ["core/stage_machine.py"]
    limitations = []
    if stage_transition.get("verdict") != "PASS":
        limitations.append("transition blocked")
    return build_evidence(
        claim=claim,
        evidence_type=evidence_type,
        source=source,
        artifact_paths=artifact_paths,
        reproducibility_notes="Re-run the same stage transition with identical evidence and policy.",
        limitations=limitations,
        confidence=1.0 if stage_transition.get("verdict") == "PASS" else 0.25,
    )


def append_stage_transition_evidence(episode: EpisodeState, stage_transition: Dict[str, Any]) -> EpisodeState:
    evidence = control_plane_evidence_from_stage_transition(stage_transition)
    episode.evidence_ledger.append(evidence.to_dict())
    episode.decision_ledger.append({
        "type": "stage_transition",
        "from_stage": stage_transition.get("from_stage"),
        "to_stage": stage_transition.get("to_stage"),
        "verdict": stage_transition.get("verdict"),
        "reason": stage_transition.get("reason", ""),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    if stage_transition.get("verdict") != "PASS":
        episode.failure_ledger.append({
            "type": "stage_transition_blocked",
            "from_stage": stage_transition.get("from_stage"),
            "to_stage": stage_transition.get("to_stage"),
            "reason": stage_transition.get("reason", ""),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    episode.updated_at = datetime.now(timezone.utc).isoformat()
    episode.version += 1
    return episode


def build_morning_report_from_episode(episode: EpisodeState) -> MorningReport:
    what_changed = [str(item.get("type", "")) for item in episode.decision_ledger if item.get("type")]
    what_failed = [str(item.get("reason", item.get("type", ""))) for item in episode.failure_ledger]
    what_was_rejected = [str(item.get("reason", item.get("type", ""))) for item in episode.decision_ledger if str(item.get("verdict", "")).upper() == "BLOCKED"]
    current_state = episode.to_dict()
    new_risks = []
    if episode.failure_ledger:
        new_risks.append("repeat failure class present")
    if episode.status != "COMPLETED":
        new_risks.append(f"episode status is {episode.status}")
    next_best_actions = [
        "re-evaluate bottleneck",
        "tighten evidence or budget",
        "run discriminating test",
    ]
    return MorningReport(
        objective_id=episode.objective.objective_id,
        what_changed=what_changed,
        why=[episode.objective.title],
        what_was_proven=[str(item.get("claim", item.get("type", ""))) for item in episode.evidence_ledger],
        what_failed=what_failed,
        what_was_rejected=what_was_rejected,
        what_improved=[item for item in what_changed if item != "episode_initialized"],
        current_state=current_state,
        new_risks=new_risks,
        next_best_actions=next_best_actions,
    )

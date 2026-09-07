"""Canonical episode state for the autonomous Hermes control plane.

This module intentionally keeps state handling tiny and explicit.
It does not execute work, route models, or mutate trading state.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict

from core.control_plane_contracts import EpisodeState

EPISODE_STATE_VERSION = "1.0.0"


def episode_state_path(state_dir: Path, episode_id: str) -> Path:
    return Path(state_dir) / f"episode_{episode_id}.json"


def write_episode_state(state_dir: Path, episode_state: EpisodeState) -> Path:
    path = episode_state_path(state_dir, episode_state.episode_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = episode_state.to_dict()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path


def read_episode_state(state_dir: Path, episode_id: str) -> Dict[str, Any]:
    path = episode_state_path(state_dir, episode_id)
    return json.loads(path.read_text(encoding="utf-8"))


def load_episode_state(state_dir: Path, episode_id: str) -> EpisodeState:
    payload = read_episode_state(state_dir, episode_id)
    objective_payload = payload.get("objective", {})
    objective = _objective_from_dict(objective_payload)
    return EpisodeState(
        episode_id=payload["episode_id"],
        objective=objective,
        status=payload.get("status", "PLANNED"),
        budgets=payload.get("budgets", {}),
        version=int(payload.get("version", 1)),
        current_task_ids=list(payload.get("current_task_ids", [])),
        evidence_ledger=list(payload.get("evidence_ledger", [])),
        decision_ledger=list(payload.get("decision_ledger", [])),
        failure_ledger=list(payload.get("failure_ledger", [])),
        created_at=payload.get("created_at", ""),
        updated_at=payload.get("updated_at", ""),
    )


def can_resume_episode(payload: Dict[str, Any]) -> bool:
    """Fail-closed resume check for crash/restart recovery."""
    if not isinstance(payload, dict):
        return False
    objective = payload.get("objective")
    if not isinstance(objective, dict):
        return False
    if not payload.get("episode_id"):
        return False
    status = payload.get("status", "")
    if status not in {"PLANNED", "RUNNING", "PARTIAL", "COMPLETED", "FAILED", "BLOCKED"}:
        return False
    if status in {"RUNNING", "PARTIAL"}:
        if not payload.get("decision_ledger"):
            return False
        if not payload.get("updated_at"):
            return False
    return True


def bump_episode_state_version(payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(payload)
    payload["version"] = int(payload.get("version", 0)) + 1
    return payload


def state_from_dict(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Opaque helper for strict state snapshots.

    The canonical state is intentionally JSON-shaped so Hermes can compare,
    diff, and version it without extra serialization tricks.
    """
    return dict(payload)


def _objective_from_dict(payload: Dict[str, Any]):
    from core.control_plane_contracts import ObjectiveContract

    return ObjectiveContract(
        objective_id=payload.get("objective_id", "unknown"),
        title=payload.get("title", ""),
        target_system=payload.get("target_system", "strategy_combine"),
        scope=list(payload.get("scope", [])),
        out_of_scope=list(payload.get("out_of_scope", [])),
        constraints=list(payload.get("constraints", [])),
        stop_conditions=list(payload.get("stop_conditions", [])),
        quality_bar=list(payload.get("quality_bar", [])),
        priority=payload.get("priority", "normal"),
        allowed_actions=list(payload.get("allowed_actions", [])),
        forbidden_actions=list(payload.get("forbidden_actions", [])),
        revision=int(payload.get("revision", 1)),
        created_at=payload.get("created_at", ""),
    )

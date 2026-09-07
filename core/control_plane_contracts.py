"""Canonical contracts for the autonomous Hermes control plane.

This module is intentionally small and strict. It defines only the minimal
schemas needed to route bounded episodes through the existing combine.

No trading logic. No broker calls. No registry mutation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

CONTRACT_VERSION = "1.0.1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ObjectiveContract:
    objective_id: str
    title: str
    target_system: str
    scope: List[str]
    out_of_scope: List[str] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    stop_conditions: List[str] = field(default_factory=list)
    quality_bar: List[str] = field(default_factory=list)
    priority: str = "normal"
    allowed_actions: List[str] = field(default_factory=list)
    forbidden_actions: List[str] = field(default_factory=list)
    revision: int = 1
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["contract_version"] = CONTRACT_VERSION
        return payload


@dataclass
class TaskContract:
    task_id: str
    objective_id: str
    role: str
    goal: str
    why: str
    inputs: List[str]
    expected_output: List[str]
    acceptance_criteria: List[str]
    failure_criteria: List[str]
    budget: Dict[str, Any]
    allowed_tools: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    parent_task_id: Optional[str] = None
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["contract_version"] = CONTRACT_VERSION
        return payload


@dataclass
class ResultContract:
    task_id: str
    status: str
    result_summary: str
    evidence_refs: List[str] = field(default_factory=list)
    files_changed: List[str] = field(default_factory=list)
    tests_run: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    uncertainties: List[str] = field(default_factory=list)
    recommendation: str = ""
    confidence: float = 0.0
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["contract_version"] = CONTRACT_VERSION
        return payload


@dataclass
class EvidenceContract:
    claim: str
    evidence_type: str
    source: str
    artifact_paths: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=_now)
    reproducibility_notes: str = ""
    limitations: List[str] = field(default_factory=list)
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["contract_version"] = CONTRACT_VERSION
        return payload


@dataclass
class EpisodeState:
    episode_id: str
    objective: ObjectiveContract
    status: str = "PLANNED"
    budgets: Dict[str, Any] = field(default_factory=dict)
    version: int = 1
    current_task_ids: List[str] = field(default_factory=list)
    evidence_ledger: List[Dict[str, Any]] = field(default_factory=list)
    decision_ledger: List[Dict[str, Any]] = field(default_factory=list)
    failure_ledger: List[Dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract_version": CONTRACT_VERSION,
            "episode_id": self.episode_id,
            "objective": self.objective.to_dict(),
            "status": self.status,
            "budgets": self.budgets,
            "version": self.version,
            "current_task_ids": self.current_task_ids,
            "evidence_ledger": self.evidence_ledger,
            "decision_ledger": self.decision_ledger,
            "failure_ledger": self.failure_ledger,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def validate_non_empty_strings(values: List[str], label: str) -> None:
    missing = [v for v in values if not isinstance(v, str) or not v.strip()]
    if missing:
        raise ValueError(f"{label} must contain only non-empty strings")


def _trim(text: Any) -> str:
    return " ".join(str(text or "").split()).strip()


def build_objective(
    objective_id: str,
    title: str,
    target_system: str,
    scope: List[str],
    **kwargs: Any,
) -> ObjectiveContract:
    validate_non_empty_strings(scope, "scope")
    return ObjectiveContract(
        objective_id=objective_id,
        title=title,
        target_system=target_system,
        scope=scope,
        out_of_scope=kwargs.get("out_of_scope", []),
        constraints=kwargs.get("constraints", []),
        stop_conditions=kwargs.get("stop_conditions", []),
        quality_bar=kwargs.get("quality_bar", []),
        priority=kwargs.get("priority", "normal"),
        allowed_actions=kwargs.get("allowed_actions", []),
        forbidden_actions=kwargs.get("forbidden_actions", []),
        revision=kwargs.get("revision", 1),
    )


def build_task(
    task_id: str,
    objective_id: str,
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
    return TaskContract(
        task_id=task_id,
        objective_id=objective_id,
        role=role,
        goal=goal,
        why=why,
        inputs=inputs,
        expected_output=expected_output,
        acceptance_criteria=acceptance_criteria,
        failure_criteria=failure_criteria,
        budget=budget,
        allowed_tools=kwargs.get("allowed_tools", []),
        dependencies=kwargs.get("dependencies", []),
        parent_task_id=kwargs.get("parent_task_id"),
    )


def build_result(
    task_id: str,
    status: str,
    result_summary: str,
    **kwargs: Any,
) -> ResultContract:
    cleaned_summary = _trim(result_summary)
    if not cleaned_summary:
        raise ValueError("result_summary must be non-empty")
    return ResultContract(
        task_id=task_id,
        status=status,
        result_summary=cleaned_summary,
        evidence_refs=kwargs.get("evidence_refs", []),
        files_changed=kwargs.get("files_changed", []),
        tests_run=kwargs.get("tests_run", []),
        failures=kwargs.get("failures", []),
        uncertainties=kwargs.get("uncertainties", []),
        recommendation=kwargs.get("recommendation", ""),
        confidence=kwargs.get("confidence", 0.0),
    )


def build_evidence(
    claim: str,
    evidence_type: str,
    source: str,
    **kwargs: Any,
) -> EvidenceContract:
    cleaned_claim = _trim(claim)
    if not cleaned_claim:
        raise ValueError("claim must be non-empty")
    return EvidenceContract(
        claim=cleaned_claim,
        evidence_type=evidence_type,
        source=source,
        artifact_paths=kwargs.get("artifact_paths", []),
        reproducibility_notes=kwargs.get("reproducibility_notes", ""),
        limitations=kwargs.get("limitations", []),
        confidence=kwargs.get("confidence", 0.0),
    )

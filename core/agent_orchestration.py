"""L1/L2/L3 orchestration contracts for Hermes.

This module defines the orchestration layer on top of the existing control plane.
It does not execute work itself; it structures and routes bounded work.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

ORCHESTRATION_VERSION = "1.0.0"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SpawnRequest:
    spawn_id: str
    parent_id: str
    parent_role: str
    child_role: str
    objective_id: str
    task_goal: str
    why: str
    child_node_id: str
    constraints: List[str] = field(default_factory=list)
    evidence_required: List[str] = field(default_factory=list)
    budget: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["orchestration_version"] = ORCHESTRATION_VERSION
        return payload


@dataclass
class AgentNode:
    node_id: str
    role: str
    parent_id: Optional[str]
    objective_id: str
    status: str = "PLANNED"
    children: List[str] = field(default_factory=list)
    tasks: List[Dict[str, Any]] = field(default_factory=list)
    results: List[Dict[str, Any]] = field(default_factory=list)
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["orchestration_version"] = ORCHESTRATION_VERSION
        return payload


@dataclass
class OrchestrationReport:
    root_objective_id: str
    active_nodes: List[str] = field(default_factory=list)
    completed_nodes: List[str] = field(default_factory=list)
    blocked_nodes: List[str] = field(default_factory=list)
    evidence_count: int = 0
    confidence: str = "UNKNOWN"
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["orchestration_version"] = ORCHESTRATION_VERSION
        return payload


class OrchestrationGraph:
    def __init__(self, root_objective_id: str):
        self.root_objective_id = root_objective_id
        self.nodes: Dict[str, AgentNode] = {}
        self.spawns: List[SpawnRequest] = []
        self._counter = 0

    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}-{self._counter:04d}"

    def add_root(self, role: str = "Hermes") -> AgentNode:
        node = AgentNode(node_id=self._next_id("node"), role=role, parent_id=None, objective_id=self.root_objective_id)
        self.nodes[node.node_id] = node
        return node

    def spawn_child(
        self,
        parent_id: str,
        child_role: str,
        task_goal: str,
        why: str,
        constraints: Optional[List[str]] = None,
        evidence_required: Optional[List[str]] = None,
        budget: Optional[Dict[str, Any]] = None,
    ) -> SpawnRequest:
        parent = self.nodes[parent_id]
        child_id = self._next_id("node")
        child = AgentNode(
            node_id=child_id,
            role=child_role,
            parent_id=parent_id,
            objective_id=parent.objective_id,
        )
        self.nodes[child_id] = child
        parent.children.append(child_id)
        parent.updated_at = _now()
        req = SpawnRequest(
            spawn_id=self._next_id("spawn"),
            parent_id=parent_id,
            parent_role=parent.role,
            child_role=child_role,
            objective_id=parent.objective_id,
            task_goal=task_goal,
            why=why,
            child_node_id=child_id,
            constraints=list(constraints or []),
            evidence_required=list(evidence_required or []),
            budget=dict(budget or {}),
        )
        self.spawns.append(req)
        return req

    def attach_task(self, node_id: str, task: Dict[str, Any]) -> None:
        self.nodes[node_id].tasks.append(dict(task))
        self.nodes[node_id].updated_at = _now()

    def attach_result(self, node_id: str, result: Dict[str, Any]) -> None:
        self.nodes[node_id].results.append(dict(result))
        self.nodes[node_id].updated_at = _now()
        status = str(result.get("status", "")).upper()
        if status in {"PASS", "DONE", "COMPLETED"}:
            self.nodes[node_id].status = "COMPLETED"
        elif status in {"BLOCKED", "FAILED", "REJECTED"}:
            self.nodes[node_id].status = "BLOCKED"

    def attach_evidence(self, node_id: str, evidence: Dict[str, Any]) -> None:
        self.nodes[node_id].evidence.append(dict(evidence))
        self.nodes[node_id].updated_at = _now()

    def report(self) -> OrchestrationReport:
        active = [n.node_id for n in self.nodes.values() if n.status not in {"COMPLETED", "BLOCKED"}]
        completed = [n.node_id for n in self.nodes.values() if n.status == "COMPLETED"]
        blocked = [n.node_id for n in self.nodes.values() if n.status == "BLOCKED"]
        evidence_count = sum(len(n.evidence) for n in self.nodes.values())
        confidence = "HIGH" if completed and not blocked else ("MEDIUM" if evidence_count else "LOW")
        notes = []
        if blocked:
            notes.append("At least one node is blocked; review evidence chain")
        if not self.spawns:
            notes.append("No child nodes spawned yet")
        return OrchestrationReport(
            root_objective_id=self.root_objective_id,
            active_nodes=active,
            completed_nodes=completed,
            blocked_nodes=blocked,
            evidence_count=evidence_count,
            confidence=confidence,
            notes=notes,
        )

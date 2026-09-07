"""L1/L2/L3 orchestration adapter for Hermes.

This is the minimal bridge from existing Mission Control / State Router /
Production Truth into a layered agent orchestration model.

It does not execute tasks itself. It creates a structured orchestration
plan that higher layers can persist and then a runtime can execute.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.agent_orchestration import OrchestrationGraph
from core.acceptance_gateway import AcceptanceGateway
from core.evidence_ledger import EvidenceLedger
from core.control_plane_contracts import ObjectiveContract, TaskContract, ResultContract, EvidenceContract


LAYERED_ORCHESTRATION_VERSION = "1.0.0"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class LayeredPlan:
    plan_id: str
    objective_id: str
    root_node_id: str
    state_scope: str
    l1_nodes: List[str] = field(default_factory=list)
    l2_nodes: List[str] = field(default_factory=list)
    l3_nodes: List[str] = field(default_factory=list)
    spawn_requests: List[Dict[str, Any]] = field(default_factory=list)
    tasks: List[Dict[str, Any]] = field(default_factory=list)
    results: List[Dict[str, Any]] = field(default_factory=list)
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["version"] = LAYERED_ORCHESTRATION_VERSION
        return payload


class LayeredAgentOrchestrator:
    """Build a layered Hermes agent graph on top of the existing control plane."""

    def __init__(self, objective: ObjectiveContract, state_scope: str = "global"):
        self.objective = objective
        self.graph = OrchestrationGraph(root_objective_id=objective.objective_id)
        self.plan = LayeredPlan(
            plan_id=f"plan::{objective.objective_id}",
            objective_id=objective.objective_id,
            root_node_id="",
            state_scope=state_scope,
        )
        root = self.graph.add_root(role="Hermes")
        self.plan.root_node_id = root.node_id
        self.acceptance = AcceptanceGateway(min_confidence=0.5)
        self.evidence_ledger = EvidenceLedger(Path('/root/prop-desk/strategy_combine/state/evidence_ledger'))
        self.graph.attach_task(root.node_id, {
            "task_id": f"task::{objective.objective_id}::root",
            "role": "Hermes",
            "goal": objective.title,
            "why": "own the control plane and spawn layered roles",
        })

    def add_l1(self, goal: str, why: str, budget: Optional[Dict[str, Any]] = None) -> str:
        req = self.graph.spawn_child(
            parent_id=self.plan.root_node_id,
            child_role="L1",
            task_goal=goal,
            why=why,
            constraints=list(self.objective.constraints),
            evidence_required=list(self.objective.quality_bar),
            budget=budget or {"tokens": 4000, "iterations": 2},
        )
        self.plan.l1_nodes.append(req.child_node_id)
        self.plan.spawn_requests.append(req.to_dict())
        self.plan.updated_at = _now()
        return req.child_node_id

    def add_l2(self, parent_node_id: str, goal: str, why: str, budget: Optional[Dict[str, Any]] = None) -> str:
        req = self.graph.spawn_child(
            parent_id=parent_node_id,
            child_role="L2",
            task_goal=goal,
            why=why,
            constraints=list(self.objective.constraints),
            evidence_required=list(self.objective.quality_bar),
            budget=budget or {"tokens": 2000, "iterations": 2},
        )
        self.plan.l2_nodes.append(req.child_node_id)
        self.plan.spawn_requests.append(req.to_dict())
        self.plan.updated_at = _now()
        return req.child_node_id

    def add_l3(self, parent_node_id: str, goal: str, why: str, budget: Optional[Dict[str, Any]] = None) -> str:
        req = self.graph.spawn_child(
            parent_id=parent_node_id,
            child_role="L3",
            task_goal=goal,
            why=why,
            constraints=list(self.objective.constraints),
            evidence_required=list(self.objective.quality_bar),
            budget=budget or {"tokens": 800, "iterations": 1},
        )
        self.plan.l3_nodes.append(req.child_node_id)
        self.plan.spawn_requests.append(req.to_dict())
        self.plan.updated_at = _now()
        return req.child_node_id

    def attach_task(self, node_id: str, task: TaskContract) -> None:
        payload = task.to_dict()
        self.graph.attach_task(node_id, payload)
        self.plan.tasks.append(payload)
        self.plan.updated_at = _now()

    def attach_result(self, node_id: str, result: ResultContract, task: TaskContract | None = None, evidence: EvidenceContract | None = None) -> None:
        payload = result.to_dict()
        if task is not None and evidence is not None:
            decision = self.acceptance.evaluate(task, result, evidence)
            payload["accepted"] = decision.accepted
            payload["acceptance_reason"] = decision.reason
            payload["acceptance_score"] = decision.score
            if decision.accepted:
                self.evidence_ledger.append(self.objective.objective_id, evidence.to_dict() if hasattr(evidence, 'to_dict') else dict(evidence))
            else:
                payload["status"] = "BLOCKED"
                payload["failures"] = payload.get("failures", []) + [decision.reason]
        self.graph.attach_result(node_id, payload)
        self.plan.results.append(payload)
        self.plan.updated_at = _now()

    def attach_evidence(self, node_id: str, evidence: EvidenceContract) -> None:
        payload = evidence.to_dict()
        self.graph.attach_evidence(node_id, payload)
        self.plan.evidence.append(payload)
        self.plan.updated_at = _now()

    def snapshot(self) -> Dict[str, Any]:
        return {
            "plan": self.plan.to_dict(),
            "graph_report": self.graph.report().to_dict(),
            "nodes": {node_id: node.to_dict() for node_id, node in self.graph.nodes.items()},
        }

    def report(self) -> Dict[str, Any]:
        return self.snapshot()

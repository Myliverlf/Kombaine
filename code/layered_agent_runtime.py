"""Runtime helpers for executing a bounded L1/L2/L3 orchestration episode.

This module is intentionally thin: it bridges Mission Control decisions into a
layered agent plan and records the resulting orchestration snapshot.

Cycle 2 changes:
- loop_breaker integration: before each spawn cycle, checks convergence status.
  If STUCK → skips spawn, adds decision, terminates loop.
  If CONVERGED → finalizes episode, returns.
- Convergence-aware termination in run_iterative().
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from core.agent_process import AgentProcess, build_role_prompt, normalize_external_payload
from core.control_plane_contracts import ObjectiveContract, build_result, build_task, build_evidence
from core.layered_agent_orchestrator import LayeredAgentOrchestrator
from core.loop_breaker import should_spawn as loop_should_spawn
from core.convergence import Status as ConvergenceStatus
from core.state_router import get_router


RUNTIME_VERSION = "1.1.0"


def build_orchestration_objective(goal: str, scope_id: str = "global") -> ObjectiveContract:
    return ObjectiveContract(
        objective_id=f"orch::{scope_id}::{int(datetime.now(timezone.utc).timestamp())}",
        title=goal,
        target_system="strategy_combine",
        scope=[goal],
        out_of_scope=["broker mutation", "live trading change"],
        constraints=["paper-only", "evidence-first", "bounded episodes"],
        stop_conditions=["budget exhausted", "no progress", "stale state"],
        quality_bar=["independent evidence", "bounded tasks", "review before accept"],
        priority="high",
        allowed_actions=["spawn", "delegate", "attach_task", "attach_result", "attach_evidence"],
        forbidden_actions=["direct broker call", "registry mutation"],
        revision=1,
    )


class LayeredAgentRuntime:
    """Bounded orchestration runtime for Hermes agent layers."""

    def __init__(self, goal: str, scope_id: str = "global"):
        self.scope_id = scope_id
        self.router = get_router(scope_id=scope_id)
        self.router.set_goal(goal)
        self.router.set_phase("orchestrating")
        self.objective = build_orchestration_objective(goal, scope_id=scope_id)
        self.orch = LayeredAgentOrchestrator(self.objective, state_scope=scope_id)
        self.snapshot_path = Path(self.router.path).with_suffix(".orchestration.json")
        self.episode_id = self.objective.objective_id
        self.ledger = self.orch.evidence_ledger

    def _seed_graph(self):
        l1 = self.orch.add_l1(goal="find current bottleneck", why="identify next leverage point")
        l2 = self.orch.add_l2(parent_node_id=l1, goal="decompose bottleneck", why="derive a bounded workstream")
        l3 = self.orch.add_l3(parent_node_id=l2, goal="run atomic execution", why="collect evidence")
        state_text = self.router.state.compact_text()

        l1_task = build_task(
            task_id=f"task::{l1}",
            objective_id=self.objective.objective_id,
            role="L1",
            goal="find current bottleneck",
            why="identify next leverage point",
            inputs=[state_text],
            expected_output=["bottleneck identified"],
            acceptance_criteria=["bounded bottleneck"],
            failure_criteria=["unbounded scope"],
            budget={"tokens": 4000, "iterations": 2},
        )
        l2_task = build_task(
            task_id=f"task::{l2}",
            objective_id=self.objective.objective_id,
            role="L2",
            goal="decompose bottleneck",
            why="derive a bounded workstream",
            inputs=[state_text],
            expected_output=["decomposition"],
            acceptance_criteria=["bounded workstream"],
            failure_criteria=["unbounded decomposition"],
            budget={"tokens": 2000, "iterations": 2},
        )
        l3_task = build_task(
            task_id=f"task::{l3}",
            objective_id=self.objective.objective_id,
            role="L3",
            goal="run atomic execution",
            why="collect evidence",
            inputs=[state_text],
            expected_output=["evidence"],
            acceptance_criteria=["evidence present"],
            failure_criteria=["no evidence"],
            budget={"tokens": 800, "iterations": 1},
        )
        self.orch.attach_task(l1, l1_task)
        self.orch.attach_task(l2, l2_task)
        self.orch.attach_task(l3, l3_task)
        return l1, l2, l3, l1_task, l2_task, l3_task, state_text

    def run_external_cycle(self) -> Dict[str, Any]:
        """Run one real external agent cycle via subprocess-backed pi agents."""
        return self.run_iterative(max_cycles=1)

    def _check_convergence(self) -> Dict[str, Any]:
        """Check convergence using loop_breaker. Returns spawn decision dict."""
        decisions = list(self.router.state.decisions)
        results = self.router.state.results if hasattr(self.router.state, 'results') else []
        decision = loop_should_spawn(decisions, results)
        return decision.to_dict()

    def run_iterative(self, max_cycles: int = 3) -> Dict[str, Any]:
        """Run iterative external cycles where each round can feed the next.

        Cycle 2: Before each spawn, checks convergence via loop_breaker.
        - STUCK → terminates loop, records decision.
        - CONVERGED → finalizes episode, returns snapshot.
        - PROGRESS/UNKNOWN → continues spawning.
        """
        snapshot: Dict[str, Any] = {}
        followup_tasks: list[dict[str, Any]] = []
        for cycle in range(max_cycles):
            # --- Cycle 2: convergence gate before spawn ---
            convergence = self._check_convergence()
            convergence_status = convergence.get("convergence_status", "UNKNOWN")
            should = convergence.get("should_spawn", True)

            if convergence_status == ConvergenceStatus.STUCK.value or not should:
                self.router.add_decision(
                    f"cycle={cycle + 1} convergence_gate=STUCK reason={convergence.get('reason', 'unknown')}"
                )
                self.router.set_next_action(
                    f"episode stopped: convergence={convergence_status}, "
                    f"recommendation={convergence.get('recommendation', 'stop')}"
                )
                self.router.set_phase("converged" if convergence_status == ConvergenceStatus.CONVERGED.value else "stuck")
                snapshot = self.orch.snapshot()
                self.snapshot_path.write_text(
                    json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                self.router.add_artifact(str(self.snapshot_path))
                self.ledger.append(self.episode_id, {
                    "cycle": cycle + 1,
                    "convergence_gate": convergence,
                    "termination_reason": convergence_status,
                })
                break

            if convergence_status == ConvergenceStatus.CONVERGED.value:
                self.router.add_decision(
                    f"cycle={cycle + 1} convergence_gate=CONVERGED reason={convergence.get('reason', 'unknown')}"
                )
                self.router.set_next_action(
                    f"episode converged: {convergence.get('reason', 'done')}"
                )
                self.router.set_phase("converged")
                snapshot = self.orch.snapshot()
                self.snapshot_path.write_text(
                    json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                self.router.add_artifact(str(self.snapshot_path))
                self.ledger.append(self.episode_id, {
                    "cycle": cycle + 1,
                    "convergence_gate": convergence,
                    "termination_reason": "CONVERGED",
                })
                break

            # PROGRESS or UNKNOWN — proceed with spawn
            l1, l2, l3, l1_task, l2_task, l3_task, _state_text = self._seed_graph()
            ledger_context = self.ledger.load(self.episode_id)
            agent_context = {
                "cycle": cycle + 1,
                "state": self.router.state.snapshot(),
                "objective": self.objective.to_dict(),
                "episode_id": self.episode_id,
                "evidence_ledger": ledger_context,
                "task": l3_task.to_dict(),
                "hierarchy": {"l1": l1, "l2": l2, "l3": l3},
                "prior_decisions": list(self.router.state.decisions),
                "followup_tasks": followup_tasks,
            }
            l1_proc = AgentProcess("L1", "/root/.pi/profiles/analyst.md", timeout_seconds=900, model="gpt-5.4-mini")
            l2_proc = AgentProcess("L2", "/root/.pi/profiles/architect.md", timeout_seconds=900)
            l3_proc = AgentProcess("L3", "/root/.pi/profiles/coder.md", timeout_seconds=1800)

            l1_res = normalize_external_payload(l1_proc.run(build_role_prompt("L1", agent_context)).payload, "L1")
            l2_res = normalize_external_payload(l2_proc.run(build_role_prompt("L2", agent_context)).payload, "L2")
            l3_res = normalize_external_payload(l3_proc.run(build_role_prompt("L3", agent_context)).payload, "L3")

            self._attach_external_results(l1, l2, l3, l1_res, l2_res, l3_res)
            followup_tasks = self._collect_followup_tasks(l1_res, l2_res, l3_res)
            if followup_tasks:
                self.router.set_goal(followup_tasks[0].get("goal", self.router.state.goal))
                self.router.add_decision(f"cycle={cycle + 1} proposed_next_tasks={len(followup_tasks)}")
                self.router.add_decision(f"cycle={cycle + 1} next_goal={followup_tasks[0].get('goal', '')}")
            self.router.add_decision(f"cycle={cycle + 1} spawned L1={l1} L2={l2} L3={l3}")
            self.router.add_decision(f"cycle={cycle + 1} L1={l1_res.get('summary', '')}")
            self.router.add_decision(f"cycle={cycle + 1} L2={l2_res.get('summary', '')}")
            self.router.add_decision(f"cycle={cycle + 1} L3={l3_res.get('summary', '')}")
            self.router.set_next_action("continue iterative improvement" if cycle + 1 < max_cycles else "collect outputs from layered runtime")
            snapshot = self.orch.snapshot()
            self.snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
            self.router.add_artifact(str(self.snapshot_path))
            self.ledger.append(self.episode_id, {
                "cycle": cycle + 1,
                "next_tasks": followup_tasks,
                "l1": l1_res,
                "l2": l2_res,
                "l3": l3_res,
            })
        return snapshot

    def run_simulated_cycle(self) -> Dict[str, Any]:
        """Fast in-process cycle used only for lightweight tests."""
        l1, l2, l3, l1_task, l2_task, l3_task, state_text = self._seed_graph()
        self.orch.attach_result(l1, build_result(
            task_id=f"task::{l1}",
            status="PASS",
            result_summary="L1 bottleneck identified",
            evidence_refs=[],
            tests_run=["state_scan"],
            failures=[],
            uncertainties=[],
            recommendation="decompose bottleneck",
            confidence=0.7,
        ))
        self.orch.attach_evidence(l1, build_evidence(
            claim="L1 bottleneck identified",
            evidence_type="state_router",
            source="core/layered_agent_runtime.py",
            artifact_paths=[str(self.router.path)],
            reproducibility_notes="The same state snapshot should produce the same bottleneck identification.",
            limitations=["state snapshot only"],
            confidence=0.7,
        ))
        self.orch.attach_result(l2, build_result(
            task_id=f"task::{l2}",
            status="PASS",
            result_summary="L2 decomposition complete",
            evidence_refs=[],
            tests_run=["state_decomposition"],
            failures=[],
            uncertainties=[],
            recommendation="advance to atomic execution",
            confidence=0.7,
        ))
        self.orch.attach_evidence(l2, build_evidence(
            claim="L2 decomposition grounded in state snapshot",
            evidence_type="state_router",
            source="core/layered_agent_runtime.py",
            artifact_paths=[str(self.router.path)],
            reproducibility_notes="The same state snapshot should produce the same decomposition.",
            limitations=["state snapshot only"],
            confidence=0.7,
        ))
        self.orch.attach_result(l3, build_result(
            task_id=f"task::{l3}",
            status="PASS",
            result_summary="Need an atomic executor with deterministic output",
            evidence_refs=[],
            tests_run=["deterministic_state_scan"],
            failures=[],
            uncertainties=[],
            recommendation="spawn next layer only if evidence contracts are available",
            confidence=0.6,
        ))
        self.orch.attach_evidence(l3, build_evidence(
            claim=f"L3 executed task {l3_task.to_dict().get('task_id', 'unknown')} for goal '{l3_task.goal}'",
            evidence_type="deterministic_execution",
            source="core/l3_executor.py",
            artifact_paths=["core/l3_executor.py"],
            reproducibility_notes="Re-run with the same task and state snapshot; output should stay deterministic.",
            limitations=["heuristic state scan only", f"why={l3_task.why}", "hint=state_snapshot"],
            confidence=0.6,
        ))
        self.router.add_decision(f"spawned L1={l1} L2={l2} L3={l3}")
        self.router.add_decision("simulated cycle complete")
        self.router.set_next_action("collect outputs from layered runtime")
        snapshot = self.orch.snapshot()
        self.snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        self.router.add_artifact(str(self.snapshot_path))
        return snapshot

    def _collect_followup_tasks(self, l1_res: Dict[str, Any], l2_res: Dict[str, Any], l3_res: Dict[str, Any]) -> list[dict[str, Any]]:
        raw = l1_res.get("next_tasks") or l2_res.get("next_tasks") or l3_res.get("next_tasks") or []
        tasks: list[dict[str, Any]] = []
        for item in raw:
            if isinstance(item, dict) and item.get("goal"):
                tasks.append({
                    "goal": str(item.get("goal", "")),
                    "why": str(item.get("why", "")),
                    "role": str(item.get("role", "L2")),
                    "inputs": list(item.get("inputs", [])),
                    "expected_output": list(item.get("expected_output", [])),
                })
        return tasks[:5]

    def _attach_external_results(self, l1, l2, l3, l1_res, l2_res, l3_res):
        self.orch.attach_result(l1, build_result(
            task_id=f"task::{l1}",
            status="PASS",
            result_summary=str(l1_res.get("summary", "L1 done")),
            evidence_refs=[],
            tests_run=["external_agent"],
            failures=[],
            uncertainties=[],
            recommendation=str(l1_res.get("next_action", "")),
            confidence=0.7,
        ))
        self.orch.attach_evidence(l1, build_evidence(
            claim=str(l1_res.get("summary", "L1 done")),
            evidence_type="external_agent",
            source="pi/L1",
            artifact_paths=[str(self.router.path)],
            reproducibility_notes="Produced by separate pi process.",
            limitations=list(l1_res.get("blockers", [])),
            confidence=0.7,
        ))
        self.orch.attach_result(l2, build_result(
            task_id=f"task::{l2}",
            status="PASS",
            result_summary=str(l2_res.get("summary", "L2 done")),
            evidence_refs=[],
            tests_run=["external_agent"],
            failures=[],
            uncertainties=[],
            recommendation=str(l2_res.get("next_action", "")),
            confidence=0.7,
        ))
        self.orch.attach_evidence(l2, build_evidence(
            claim=str(l2_res.get("summary", "L2 done")),
            evidence_type="external_agent",
            source="pi/L2",
            artifact_paths=[str(self.router.path)],
            reproducibility_notes="Produced by separate pi process.",
            limitations=list(l2_res.get("blockers", [])),
            confidence=0.7,
        ))
        self.orch.attach_result(l3, build_result(
            task_id=f"task::{l3}",
            status="PASS",
            result_summary=str(l3_res.get("summary", "L3 done")),
            evidence_refs=[],
            tests_run=["external_agent"],
            failures=[],
            uncertainties=[],
            recommendation=str(l3_res.get("next_action", "")),
            confidence=0.7,
        ))
        self.orch.attach_evidence(l3, build_evidence(
            claim=str(l3_res.get("summary", "L3 done")),
            evidence_type="external_agent",
            source="pi/L3",
            artifact_paths=[str(self.snapshot_path)],
            reproducibility_notes="Produced by separate pi process.",
            limitations=list(l3_res.get("blockers", [])),
            confidence=0.7,
        ))

    def persist_snapshot(self) -> Path:
        snapshot = self.orch.snapshot()
        self.snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.snapshot_path

    def report(self) -> Dict[str, Any]:
        return {
            "scope_id": self.scope_id,
            "goal": self.router.state.goal,
            "state_router": self.router.state.snapshot(),
            "orchestration": self.orch.report(),
            "snapshot_path": str(self.snapshot_path),
        }

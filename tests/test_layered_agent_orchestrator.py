from core.control_plane_contracts import build_evidence, build_objective, build_result, build_task
from core.layered_agent_orchestrator import LayeredAgentOrchestrator


def test_layered_agent_orchestrator_builds_hierarchy():
    objective = build_objective(
        objective_id="obj-1",
        title="Improve combine autonomy",
        target_system="strategy_combine",
        scope=["L1", "L2", "L3"],
        constraints=["paper-only"],
        quality_bar=["evidence-first"],
    )
    orch = LayeredAgentOrchestrator(objective)
    l1 = orch.add_l1("find bottleneck", "need strategy")
    l2 = orch.add_l2(l1, "decompose bottleneck", "need workstream")
    l3 = orch.add_l3(l2, "run atomic check", "need execution")

    task = build_task(
        task_id="task-1",
        objective_id=objective.objective_id,
        role="L3",
        goal="run atomic check",
        why="verify",
        inputs=["state"],
        expected_output=["evidence"],
        acceptance_criteria=["evidence present"],
        failure_criteria=["no evidence"],
        budget={"tokens": 100},
    )
    result = build_result(task_id="task-1", status="PASS", result_summary="ok")
    evidence = build_evidence("evidence present", "test_output", "unit_test", artifact_paths=["tests/test_layered_agent_orchestrator.py"])

    orch.attach_task(l3, task)
    orch.attach_result(l3, result)
    orch.attach_evidence(l3, evidence)
    snap = orch.snapshot()
    assert snap["plan"]["objective_id"] == objective.objective_id
    assert snap["graph_report"]["root_objective_id"] == objective.objective_id
    assert snap["graph_report"]["evidence_count"] == 1
    assert l1 in snap["nodes"] and l2 in snap["nodes"] and l3 in snap["nodes"]
    assert snap["plan"]["l1_nodes"] == [l1]
    assert snap["plan"]["l2_nodes"] == [l2]
    assert snap["plan"]["l3_nodes"] == [l3]
    assert snap["nodes"][l1]["children"]
    assert snap["nodes"][l2]["children"]

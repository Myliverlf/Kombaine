from core.control_plane_contracts import (
    CONTRACT_VERSION,
    build_evidence,
    build_objective,
    build_result,
    build_task,
)


def test_objective_contract_serializes():
    obj = build_objective(
        objective_id="obj-1",
        title="Improve autonomy",
        target_system="strategy_combine",
        scope=["autonomous control plane"],
        constraints=["paper-only"],
        stop_conditions=["budget exhausted"],
        quality_bar=["evidence-first"],
        allowed_actions=["observe", "plan"],
        forbidden_actions=["broker mutation"],
    )
    data = obj.to_dict()
    assert data["objective_id"] == "obj-1"
    assert data["contract_version"] == CONTRACT_VERSION


def test_task_result_evidence_roundtrip():
    task = build_task(
        task_id="task-1",
        objective_id="obj-1",
        role="planner",
        goal="define next bottleneck",
        why="reduce uncertainty",
        inputs=["current state"],
        expected_output=["bounded plan"],
        acceptance_criteria=["plan is bounded"],
        failure_criteria=["unbounded scope"],
        budget={"tokens": 1000},
        allowed_tools=["read_file"],
    )
    result = build_result(
        task_id=task.task_id,
        status="PASS",
        result_summary="Plan produced",
        evidence_refs=["evidence-1"],
        confidence=0.9,
    )
    evidence = build_evidence(
        claim="plan is bounded",
        evidence_type="test_output",
        source="unit_test",
        artifact_paths=["tests/test_control_plane_contracts.py"],
        confidence=1.0,
    )
    assert task.to_dict()["task_id"] == "task-1"
    assert result.to_dict()["status"] == "PASS"
    assert evidence.to_dict()["source"] == "unit_test"

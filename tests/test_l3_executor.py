from core.l3_executor import execute_l3_task


def test_l3_executor_produces_result_and_evidence():
    task = {"task_id": "task-1", "goal": "decompose bottleneck", "why": "need workstream"}
    state = {"goal": "Improve autonomy", "phase": "orchestrating", "risks": ["stale state"]}
    out = execute_l3_task(task, state)
    assert out.task_id == "task-1"
    assert out.result.status == "PASS"
    assert out.result.result_summary
    assert out.evidence.claim.startswith("L3 executed task")
    assert out.evidence.source == "core/l3_executor.py+core/evidence_collector.py"

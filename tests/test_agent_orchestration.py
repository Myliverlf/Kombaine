from core.agent_orchestration import OrchestrationGraph


def test_orchestration_graph_spawns_and_reports():
    g = OrchestrationGraph(root_objective_id="obj-1")
    root = g.add_root()
    req = g.spawn_child(
        parent_id=root.node_id,
        child_role="L1",
        task_goal="find bottleneck",
        why="need strategy",
        constraints=["paper-only"],
        evidence_required=["evidence"],
        budget={"tokens": 100},
    )
    assert req.parent_id == root.node_id
    assert len(g.nodes[root.node_id].children) == 1
    child_id = g.nodes[root.node_id].children[0]
    g.attach_task(child_id, {"task_id": "t1", "goal": "analyze"})
    g.attach_evidence(child_id, {"claim": "analyzed", "source": "unit_test"})
    g.attach_result(child_id, {"status": "PASS", "result_summary": "done"})
    report = g.report()
    assert report.root_objective_id == "obj-1"
    assert report.completed_nodes
    assert report.evidence_count == 1
    assert report.confidence in {"HIGH", "MEDIUM"}

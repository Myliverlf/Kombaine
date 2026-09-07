from core.layered_agent_runtime import LayeredAgentRuntime


def test_layered_agent_runtime_runs_single_cycle(tmp_path):
    runtime = LayeredAgentRuntime(goal="Improve combine autonomy", scope_id="test-scope")
    snapshot = runtime.run_simulated_cycle()
    assert snapshot["plan"]["objective_id"].startswith("orch::test-scope::")
    assert snapshot["graph_report"]["root_objective_id"] == snapshot["plan"]["objective_id"]
    assert runtime.snapshot_path.exists()
    report = runtime.report()
    assert report["goal"] == "Improve combine autonomy"
    assert report["orchestration"]["graph_report"]["evidence_count"] == 3
    assert all(node["status"] == "COMPLETED" for node in report["orchestration"]["nodes"].values() if node["role"] != "Hermes")

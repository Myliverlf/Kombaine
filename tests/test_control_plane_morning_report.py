from core.control_plane_bridge import (
    append_stage_transition_evidence,
    build_morning_report_from_episode,
    episode_state_from_run_manifest,
)


def test_stage_transition_appends_evidence_and_decision():
    episode = episode_state_from_run_manifest({"run_id": "run-1", "planned_configurations": 2, "status": "RUNNING"})
    ep2 = append_stage_transition_evidence(
        episode,
        {
            "from_stage": "DISCOVERY",
            "to_stage": "BACKTEST_QUALIFIED",
            "verdict": "PASS",
            "reason": "ok",
        },
    )
    assert ep2.evidence_ledger
    assert ep2.decision_ledger[-1]["type"] == "stage_transition"
    assert ep2.decision_ledger[-1]["verdict"] == "PASS"


def test_morning_report_from_episode_contains_state():
    episode = episode_state_from_run_manifest({"run_id": "run-2", "planned_configurations": 1, "status": "RUNNING"})
    episode = append_stage_transition_evidence(
        episode,
        {"from_stage": "DISCOVERY", "to_stage": "BACKTEST_QUALIFIED", "verdict": "BLOCKED", "reason": "missing OOS"},
    )
    episode = append_stage_transition_evidence(
        episode,
        {"from_stage": "BACKTEST_QUALIFIED", "to_stage": "MULTI_HORIZON_QUALIFIED", "verdict": "PASS", "reason": "ok"},
    )
    report = build_morning_report_from_episode(episode)
    assert report.objective_id == episode.objective.objective_id
    assert report.what_changed
    assert report.what_failed or report.what_was_rejected
    assert report.current_state["status"] == episode.status

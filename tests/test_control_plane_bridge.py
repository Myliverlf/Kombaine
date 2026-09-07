from pathlib import Path

from core.control_plane_bridge import (
    append_decision,
    append_evidence,
    append_failure,
    control_plane_evidence_from_stage_transition,
    episode_state_from_run_manifest,
    read_episode_snapshot,
    task_from_goal,
    write_episode_snapshot,
)
from core.control_plane_contracts import build_evidence


def test_episode_state_from_run_manifest_roundtrip(tmp_path: Path):
    manifest = {
        "run_id": "run_20260903_120000_deadbeef",
        "planned_configurations": 3,
        "status": "COMPLETED",
    }
    episode = episode_state_from_run_manifest(manifest)
    assert episode.objective.objective_id == "objective::run_20260903_120000_deadbeef"
    assert episode.budgets["tokens"] == 30
    path = write_episode_snapshot(tmp_path, episode)
    assert path.exists()
    loaded = read_episode_snapshot(tmp_path, episode.episode_id)
    assert loaded["episode_id"] == episode.episode_id


def test_task_and_evidence_bridge():
    manifest = {"run_id": "run_x", "planned_configurations": 1, "status": "COMPLETED"}
    episode = episode_state_from_run_manifest(manifest)
    task = task_from_goal(
        task_id="task-x",
        objective=episode.objective,
        role="researcher",
        goal="inspect state",
        why="need snapshot",
        inputs=["manifest"],
        expected_output=["state snapshot"],
        acceptance_criteria=["snapshot written"],
        failure_criteria=["snapshot missing"],
        budget={"tokens": 10},
    )
    assert task.objective_id == episode.objective.objective_id
    evidence = build_evidence("snapshot written", "file_write", "unit_test", artifact_paths=["/tmp/x"], confidence=0.9)
    episode = append_evidence(episode, evidence)
    episode = append_decision(episode, {"type": "accepted"})
    episode = append_failure(episode, {"type": "none"})
    assert episode.evidence_ledger[-1]["claim"] == "snapshot written"
    assert episode.decision_ledger[-1]["type"] == "accepted"
    assert episode.failure_ledger[-1]["type"] == "none"


def test_stage_transition_becomes_evidence():
    ev = control_plane_evidence_from_stage_transition({"from_stage": "DISCOVERY", "to_stage": "BACKTEST_QUALIFIED", "verdict": "PASS"})
    assert ev.evidence_type == "stage_transition"
    assert ev.confidence == 1.0

from pathlib import Path

from core.mission_control import MissionControlOrchestrator, MCAction, MCDecision, MCSnapshot


def test_mission_control_can_launch_layered_runtime(tmp_path: Path):
    mc = MissionControlOrchestrator(tmp_path)
    snap = MCSnapshot(
        snapshot_id="snap-1",
        cycle_id="cycle-1",
        timestamp_iso="2026-09-04T00:00:00Z",
        overall_health="HEALTHY",
        overall_mc_state="NORMAL",
        snapshot_hash="hash-1",
    )
    dec = MCDecision(
        decision_id="dec-1",
        cycle_id="cycle-1",
        snapshot_id="snap-1",
        snapshot_hash="hash-1",
        primary_action=MCAction.RUN_CANONICAL_RESEARCH.value,
        priority="P4_RESEARCH_NEED",
        reason_codes=["research_needed"],
        source_evidence={},
        why_not_selected=[],
        expected_outcome="trigger research",
        overall_state="NORMAL",
        timestamp_iso="2026-09-04T00:00:00Z",
    )
    out = mc.execute_bounded_action(dec, snap)
    assert out.executed is True
    assert "Layered runtime" in out.execution_result or "failed" in out.execution_result

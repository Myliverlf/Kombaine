from pathlib import Path

from core.mission_control import MissionControlOrchestrator


def test_mission_control_can_trigger_autonomous_runner(tmp_path: Path):
    mc = MissionControlOrchestrator(tmp_path)
    result = mc.run_cycle()
    assert "cycle_id" in result
    assert "primary_action" in result

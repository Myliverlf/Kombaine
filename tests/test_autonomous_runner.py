from pathlib import Path

from core.autonomous_runner import AutonomousRunner


def test_autonomous_runner_writes_run_record(tmp_path: Path):
    runner = AutonomousRunner(tmp_path, scope_id="runner-scope")
    result = runner.run_once(goal="Improve combine autonomy", iterations=1)
    assert result["run_id"].startswith("ar_")
    assert "mission_control_result" in result
    assert result["status"] in {"COMPLETED", "SKIPPED"}
    files = list(Path(result["artifact_root"]).rglob("run.json"))
    assert files, "runner must persist a durable record"

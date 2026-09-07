from pathlib import Path

from core.run_contract import ResearchRun


def test_research_run_writes_control_plane_snapshot(tmp_path: Path):
    base = tmp_path / "strategy_architect"
    run = ResearchRun.create(base_dir=base)
    run.start_planning(["LKOH"], ["1h"], [60], ["ichimoku"])
    snapshot = run.control_plane_snapshot()
    assert snapshot is not None
    assert snapshot["objective"]["target_system"] == "strategy_combine"
    assert snapshot["status"] == "RUNNING"
    assert run.can_resume_control_plane() is True
    assert run.resume_control_plane() is True


def test_research_run_snapshot_updates_on_complete(tmp_path: Path):
    base = tmp_path / "strategy_architect"
    run = ResearchRun.create(base_dir=base)
    run.start_planning(["LKOH"], ["1h"], [60], ["ichimoku"])
    run.finalize_report([])
    status = run.complete()
    assert status in {"COMPLETED", "PARTIAL"}
    snapshot = run.control_plane_snapshot()
    assert snapshot is not None
    assert snapshot["status"] == status
    assert snapshot["decision_ledger"]


def test_research_run_fail_closed_on_corrupt_snapshot(tmp_path: Path):
    base = tmp_path / "strategy_architect"
    run = ResearchRun.create(base_dir=base)
    run.start_planning(["LKOH"], ["1h"], [60], ["ichimoku"])
    snapshot_path = run.state_dir / f"episode_{run._episode_id}.json"
    snapshot_path.write_text('{"episode_id": "x", "status": "RUNNING"}', encoding='utf-8')
    assert run.can_resume_control_plane() is False
    assert run.resume_control_plane() is False


def test_research_run_crash_restart_resume_drill(tmp_path: Path):
    base = tmp_path / "strategy_architect"
    run1 = ResearchRun.create(base_dir=base)
    run1.start_planning(["LKOH"], ["1h"], [60], ["ichimoku"])
    run1.finalize_report([])
    state_dir = run1.state_dir
    episode_id = run1._episode_id
    raw = run1.control_plane_snapshot_raw()
    assert raw

    # simulate crash by corrupting the persisted snapshot
    state_path = state_dir / f"episode_{episode_id}.json"
    state_path.write_text('{"episode_id": "broken", "status": "RUNNING"}', encoding='utf-8')

    crashed = ResearchRun.load(base_dir=base, run_id=run1.run_id)
    assert crashed.can_resume_control_plane() is False
    assert crashed.resume_control_plane() is False

    # restore the good snapshot and resume again
    state_path.write_text(raw, encoding='utf-8')
    recovered = ResearchRun.load(base_dir=base, run_id=run1.run_id)
    assert recovered.can_resume_control_plane() is True
    assert recovered.resume_control_plane() is True
    status = recovered.complete()
    assert status in {"COMPLETED", "PARTIAL"}
    assert recovered.control_plane_snapshot()["status"] == status
    report_path = recovered.run_dir / "morning_report.json"
    assert report_path.exists()

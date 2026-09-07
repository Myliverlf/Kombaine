from core.control_plane_loop import LoopDetector, LoopObservation, signature_from_payload
from core.resource_governor import Budget, ResourceGovernor
from core.control_plane_review import ReviewFinding, build_review_report


def test_loop_detector_triggers_meta_review():
    detector = LoopDetector(max_repeats=1, max_no_progress=2)
    r1 = detector.observe(LoopObservation(signature="sig-a", hypothesis="h1", status="fail", progress_delta=0))
    r2 = detector.observe(LoopObservation(signature="sig-a", hypothesis="h1", status="fail", progress_delta=0))
    assert r2.meta_review_required is True
    assert r2.no_progress_count >= 2
    assert "repeated signatures detected" in r2.reasons or "no-progress threshold reached" in r2.reasons
    assert signature_from_payload({"objective_id": "o", "task_id": "t", "role": "r"}) == "o|t|r|||"


def test_resource_governor_bounded_episode():
    governor = ResourceGovernor(Budget(max_tokens=10, max_iterations=2, max_wall_time_seconds=5, max_parallel_agents=1, max_model_calls=3, max_failed_attempts=1))
    assert governor.can_continue() is True
    governor.consume(tokens=5, iteration=1, wall_time_seconds=2, model_calls=1)
    assert governor.can_continue() is True
    governor.consume(tokens=6, iteration=1)
    assert governor.should_escalate() is True


def test_review_requires_evidence_for_pass():
    report = build_review_report("PASS", [ReviewFinding(claim="evidence exists", verdict="PASS", evidence=["tests/test_control_plane_runtime.py"])])
    assert report.has_pass() is True

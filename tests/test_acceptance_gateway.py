from core.acceptance_gateway import AcceptanceGateway
from core.control_plane_contracts import build_evidence, build_result, build_task


def test_acceptance_gateway_accepts_good_result():
    gate = AcceptanceGateway(min_confidence=0.5)
    task = build_task("t1", "obj1", "L3", "goal", "why", ["ctx"], ["out"], ["ok"], ["fail"], {"tokens": 1})
    result = build_result("t1", "PASS", "done", confidence=0.8)
    evidence = build_evidence("claim", "type", "source", artifact_paths=["a.py"], confidence=0.8)
    decision = gate.evaluate(task, result, evidence)
    assert decision.accepted is True


def test_acceptance_gateway_rejects_missing_evidence():
    gate = AcceptanceGateway(min_confidence=0.5)
    task = build_task("t1", "obj1", "L3", "goal", "why", ["ctx"], ["out"], ["ok"], ["fail"], {"tokens": 1})
    result = build_result("t1", "PASS", "done", confidence=0.8)
    evidence = build_evidence("", "type", "", artifact_paths=[], confidence=0.2)
    decision = gate.evaluate(task, result, evidence)
    assert decision.accepted is False

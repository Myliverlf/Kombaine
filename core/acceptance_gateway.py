"""Acceptance gate for orchestration results.

Rejects self-attested or weak results before they enter the graph as completed
work.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List

from core.control_plane_contracts import ResultContract, TaskContract, EvidenceContract


@dataclass
class AcceptanceDecision:
    accepted: bool
    reason: str
    missing: List[str]
    score: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AcceptanceGateway:
    def __init__(self, min_confidence: float = 0.5):
        self.min_confidence = min_confidence

    def evaluate(self, task: TaskContract, result: ResultContract, evidence: EvidenceContract | Dict[str, Any]) -> AcceptanceDecision:
        evidence_dict = evidence.to_dict() if hasattr(evidence, "to_dict") else dict(evidence)
        missing: List[str] = []
        score = 0.0

        if result.status.upper() not in {"PASS", "DONE", "COMPLETED"}:
            missing.append("status_not_pass")
        else:
            score += 0.3

        if result.confidence < self.min_confidence:
            missing.append("confidence_too_low")
        else:
            score += 0.3

        if not evidence_dict.get("claim"):
            missing.append("missing_claim")
        else:
            score += 0.2

        if not evidence_dict.get("source"):
            missing.append("missing_source")
        else:
            score += 0.1

        if not evidence_dict.get("artifact_paths"):
            missing.append("missing_artifacts")
        else:
            score += 0.1

        accepted = not missing and score >= 0.8
        reason = "accepted" if accepted else ", ".join(missing) or "insufficient_evidence"
        return AcceptanceDecision(accepted=accepted, reason=reason, missing=missing, score=score)

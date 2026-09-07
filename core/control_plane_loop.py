"""Loop control for the autonomous Hermes control plane.

This module detects repetition, no-progress runs, and meta-review triggers.
It does not execute tasks; it only reasons about loop state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple


@dataclass
class LoopObservation:
    signature: str
    hypothesis: str
    status: str
    evidence_strength: float = 0.0
    progress_delta: float = 0.0
    notes: str = ""


@dataclass
class LoopReport:
    repeated_signatures: List[str] = field(default_factory=list)
    no_progress_count: int = 0
    meta_review_required: bool = False
    reasons: List[str] = field(default_factory=list)


class LoopDetector:
    def __init__(self, max_repeats: int = 2, max_no_progress: int = 3):
        self.max_repeats = max_repeats
        self.max_no_progress = max_no_progress
        self._signatures: List[str] = []
        self._no_progress = 0

    def observe(self, observation: LoopObservation) -> LoopReport:
        self._signatures.append(observation.signature)
        if observation.progress_delta <= 0:
            self._no_progress += 1
        else:
            self._no_progress = 0

        repeated = [sig for sig in set(self._signatures) if self._signatures.count(sig) > self.max_repeats]
        reasons: List[str] = []
        if repeated:
            reasons.append("repeated signatures detected")
        if self._no_progress >= self.max_no_progress:
            reasons.append("no-progress threshold reached")
        return LoopReport(
            repeated_signatures=sorted(repeated),
            no_progress_count=self._no_progress,
            meta_review_required=bool(reasons),
            reasons=reasons,
        )


def signature_from_payload(payload: Dict[str, Any]) -> str:
    keys = ["objective_id", "task_id", "role", "goal", "status", "result_summary"]
    parts = [str(payload.get(k, "")) for k in keys]
    return "|".join(parts)

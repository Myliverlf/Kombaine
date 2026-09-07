"""Morning and episode reporting helpers for Hermes autonomy."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class MorningReport:
    objective_id: str
    what_changed: List[str] = field(default_factory=list)
    why: List[str] = field(default_factory=list)
    what_was_proven: List[str] = field(default_factory=list)
    what_failed: List[str] = field(default_factory=list)
    what_was_rejected: List[str] = field(default_factory=list)
    what_improved: List[str] = field(default_factory=list)
    current_state: Dict[str, Any] = field(default_factory=dict)
    new_risks: List[str] = field(default_factory=list)
    next_best_actions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "objective_id": self.objective_id,
            "what_changed": self.what_changed,
            "why": self.why,
            "what_was_proven": self.what_was_proven,
            "what_failed": self.what_failed,
            "what_was_rejected": self.what_was_rejected,
            "what_improved": self.what_improved,
            "current_state": self.current_state,
            "new_risks": self.new_risks,
            "next_best_actions": self.next_best_actions,
        }

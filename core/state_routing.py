
"""Shared helper for compact state routing in long-running project workflows."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict
import json

from core.state_router import get_router


def ensure_state_router(goal: str, phase: str, next_action: str, scope_id: str = "global", **kwargs: Any) -> Dict[str, Any]:
    router = get_router(scope_id=scope_id)
    if goal:
        router.set_goal(goal)
    if phase:
        router.set_phase(phase)
    if next_action:
        router.set_next_action(next_action)
    for key, value in kwargs.items():
        if hasattr(router.state, key):
            setattr(router.state, key, value)
    router.save()
    return router.state.snapshot()


def get_state_prefix(scope_id: str = "global") -> str:
    return get_router(scope_id=scope_id).build_prompt_prefix()

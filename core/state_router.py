
"""Minimal structured state router for long-running strategy_combine tasks."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional
import json
import time

STATE_DIR = Path('/root/prop-desk/strategy_combine/state')
STATE_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class TaskState:
    goal: str = ''
    constraints: List[str] = field(default_factory=list)
    decisions: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    current_phase: str = 'init'
    next_action: str = ''
    artifacts: List[str] = field(default_factory=list)
    capital_rub: float = 0.0
    risk_pct: float = 0.0
    scope_id: str = 'global'
    updated_at: float = field(default_factory=time.time)

    def snapshot(self) -> Dict[str, Any]:
        data = asdict(self)
        data['updated_at'] = self.updated_at
        return data

    def compact_text(self) -> str:
        return json.dumps(self.snapshot(), ensure_ascii=False, sort_keys=True)


class StateRouter:
    def __init__(self, path: Optional[Path] = None, scope_id: str = 'global'):
        self.scope_id = scope_id
        self.path = path or (STATE_DIR / f'task_state_router_{scope_id}.json')
        self.state = TaskState(scope_id=scope_id)
        self.load()

    def load(self) -> TaskState:
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding='utf-8'))
                self.state = TaskState(**{**self.state.snapshot(), **raw})
            except Exception:
                pass
        return self.state

    def save(self) -> None:
        self.state.updated_at = time.time()
        self.path.write_text(json.dumps(self.state.snapshot(), ensure_ascii=False, indent=2), encoding='utf-8')

    def update(self, **kwargs: Any) -> TaskState:
        for k, v in kwargs.items():
            if hasattr(self.state, k):
                setattr(self.state, k, v)
        self.save()
        return self.state

    def add_decision(self, text: str) -> None:
        self.state.decisions.append(text)
        self.save()

    def add_risk(self, text: str) -> None:
        self.state.risks.append(text)
        self.save()

    def add_blocker(self, text: str) -> None:
        self.state.blockers.append(text)
        self.save()

    def add_artifact(self, text: str) -> None:
        self.state.artifacts.append(text)
        self.save()

    def set_goal(self, text: str) -> None:
        self.state.goal = text
        self.save()

    def set_phase(self, text: str) -> None:
        self.state.current_phase = text
        self.save()

    def set_next_action(self, text: str) -> None:
        self.state.next_action = text
        self.save()

    def set_capital_context(self, capital_rub: float, risk_pct: float) -> None:
        self.state.capital_rub = float(capital_rub)
        self.state.risk_pct = float(risk_pct)
        self.save()

    def build_prompt_prefix(self) -> str:
        s = self.state
        return '\n'.join([
            '[STATE]',
            f'goal={s.goal}',
            f'phase={s.current_phase}',
            f'next={s.next_action}',
            f'capital_rub={s.capital_rub:.2f}',
            f'risk_pct={s.risk_pct:.2f}',
            f'constraints={"; ".join(s.constraints)}',
            f'decisions={"; ".join(s.decisions[-8:])}',
            f'risks={"; ".join(s.risks[-8:])}',
            f'blockers={"; ".join(s.blockers[-8:])}',
            f'artifacts={"; ".join(s.artifacts[-8:])}',
            '[/STATE]',
        ]) + '\n'


def get_router(scope_id: str = 'global') -> StateRouter:
    return StateRouter(scope_id=scope_id)

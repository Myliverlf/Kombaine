"""Resource governor for autonomous Hermes episodes.

Keeps bounded episodes bounded. No silent budget creep.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class Budget:
    max_tokens: int = 0
    max_iterations: int = 0
    max_wall_time_seconds: int = 0
    max_parallel_agents: int = 0
    max_model_calls: int = 0
    max_failed_attempts: int = 0
    spent_tokens: int = 0
    iterations: int = 0
    wall_time_seconds: int = 0
    parallel_agents: int = 0
    model_calls: int = 0
    failed_attempts: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


class ResourceGovernor:
    def __init__(self, budget: Budget):
        self.budget = budget

    def can_continue(self) -> bool:
        return (
            self.budget.spent_tokens < self.budget.max_tokens
            and self.budget.iterations < self.budget.max_iterations
            and self.budget.wall_time_seconds < self.budget.max_wall_time_seconds
            and self.budget.parallel_agents <= self.budget.max_parallel_agents
            and self.budget.model_calls < self.budget.max_model_calls
            and self.budget.failed_attempts < self.budget.max_failed_attempts
        )

    def consume(self, *, tokens: int = 0, iteration: int = 0, wall_time_seconds: int = 0, model_calls: int = 0, failed_attempts: int = 0) -> None:
        self.budget.spent_tokens += max(0, tokens)
        self.budget.iterations += max(0, iteration)
        self.budget.wall_time_seconds += max(0, wall_time_seconds)
        self.budget.model_calls += max(0, model_calls)
        self.budget.failed_attempts += max(0, failed_attempts)

    def should_escalate(self) -> bool:
        if self.budget.failed_attempts >= self.budget.max_failed_attempts:
            return True
        return not self.can_continue()

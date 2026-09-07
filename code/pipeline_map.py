"""Read-only pipeline map for strategy_combine.

This artifact documents the current analytics → list → pool → risk → live path
and hard-codes safe-mode boundaries:
- no live broker calls
- no live orders
- live stage is read-only / dry-run only
- signal_pool is only for proven candidates

The module is intentionally stdlib-only and side-effect free.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List

PIPELINE_ORDER = ("analytics", "list", "pool", "risk", "live")
SAFE_MODES = {"dry-run", "fixtures"}
LIVE_ORDERS_ALLOWED = False
MAX_LIVE_SLOTS = 3
MAX_CONTRACTS_PER_ENTRY = 1
TARGET_UNIVERSE_SIZE = 20
REQUIRED_STRATEGY_INDEPENDENCE = True
REQUIRED_SIGNAL_POOL_PROVEN = True
EXCLUDED_TICKERS = ("RI",)


@dataclass(frozen=True)
class StagePolicy:
    stage: str
    purpose: str
    allowed_modes: List[str]
    live_orders_allowed: bool
    requires_proven_candidates: bool
    notes: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


PIPELINE_MAP: Dict[str, StagePolicy] = {
    "analytics": StagePolicy(
        stage="analytics",
        purpose="derive candidates and metrics from fixtures/backtests",
        allowed_modes=["dry-run", "fixtures"],
        live_orders_allowed=False,
        requires_proven_candidates=False,
        notes=["read-only analytics", "no broker imports"],
    ),
    "list": StagePolicy(
        stage="list",
        purpose="enumerate candidate ideas and keep only quality-gated records",
        allowed_modes=["dry-run", "fixtures"],
        live_orders_allowed=False,
        requires_proven_candidates=True,
        notes=["quality gate must stay strict", "RI excluded"],
    ),
    "pool": StagePolicy(
        stage="pool",
        purpose="signal_pool holds only proven candidates",
        allowed_modes=["dry-run", "fixtures"],
        live_orders_allowed=False,
        requires_proven_candidates=True,
        notes=["no speculative pool entries", "deduplicate independent strategies"],
    ),
    "risk": StagePolicy(
        stage="risk",
        purpose="score allocator/risk and enforce max slots and 1 contract",
        allowed_modes=["dry-run", "fixtures"],
        live_orders_allowed=False,
        requires_proven_candidates=True,
        notes=["PnL↑/risk↓ scorecard", "max live slots ≤3", "1 contract max per entry"],
    ),
    "live": StagePolicy(
        stage="live",
        purpose="read-only live checkpoint; no broker mutation in this artifact",
        allowed_modes=["dry-run", "fixtures"],
        live_orders_allowed=False,
        requires_proven_candidates=True,
        notes=["live orders forbidden", "verification only"],
    ),
}


def build_pipeline_map() -> Dict[str, Any]:
    """Return the canonical read-only pipeline map."""
    return {
        "order": list(PIPELINE_ORDER),
        "safe_modes": sorted(SAFE_MODES),
        "live_orders_allowed": LIVE_ORDERS_ALLOWED,
        "limits": {
            "max_live_slots": MAX_LIVE_SLOTS,
            "max_contracts_per_entry": MAX_CONTRACTS_PER_ENTRY,
            "target_universe_size": TARGET_UNIVERSE_SIZE,
        },
        "guardrails": {
            "quality_gate_strict": True,
            "required_strategy_independence": REQUIRED_STRATEGY_INDEPENDENCE,
            "required_signal_pool_proven": REQUIRED_SIGNAL_POOL_PROVEN,
            "excluded_tickers": list(EXCLUDED_TICKERS),
        },
        "stages": {name: policy.to_dict() for name, policy in PIPELINE_MAP.items()},
    }


def stage_policy(stage: str) -> Dict[str, Any]:
    """Return policy for a stage or an empty policy if unknown."""
    policy = PIPELINE_MAP.get(stage)
    return policy.to_dict() if policy is not None else {
        "stage": stage,
        "purpose": "unknown",
        "allowed_modes": [],
        "live_orders_allowed": False,
        "requires_proven_candidates": False,
        "notes": ["unknown stage"],
    }


def stage_is_safe(stage: str, mode: str) -> bool:
    """True only when the stage is allowed in the requested mode.

    The live stage is never executable for live orders in this artifact.
    """
    policy = PIPELINE_MAP.get(stage)
    if policy is None:
        return False
    return mode in policy.allowed_modes and not policy.live_orders_allowed


def validate_no_live_orders(stage: str, mode: str) -> Dict[str, Any]:
    """Explain why live orders are rejected."""
    policy = stage_policy(stage)
    safe = stage_is_safe(stage, mode)
    issues: List[str] = []
    if mode not in SAFE_MODES:
        issues.append(f"mode={mode!r} is not a safe dry-run mode")
    if policy.get("live_orders_allowed"):
        issues.append("live orders are enabled")
    if stage == "live" and mode not in SAFE_MODES:
        issues.append("live stage is read-only in this artifact")
    return {
        "ok": safe and not issues,
        "stage": stage,
        "mode": mode,
        "issues": issues,
        "policy": policy,
    }


def render_pipeline_map() -> str:
    """Render a compact human-readable pipeline map."""
    lines = [
        "analytics → list → pool → risk → live",
        f"safe_modes: {', '.join(sorted(SAFE_MODES))}",
        f"live_orders_allowed: {LIVE_ORDERS_ALLOWED}",
        f"max_live_slots: {MAX_LIVE_SLOTS}",
        f"max_contracts_per_entry: {MAX_CONTRACTS_PER_ENTRY}",
        f"target_universe_size: {TARGET_UNIVERSE_SIZE}",
        f"excluded_tickers: {', '.join(EXCLUDED_TICKERS)}",
    ]
    for stage in PIPELINE_ORDER:
        policy = PIPELINE_MAP[stage]
        lines.append(f"- {stage}: {policy.purpose}")
    return "\n".join(lines)


__all__ = [
    "PIPELINE_ORDER",
    "SAFE_MODES",
    "LIVE_ORDERS_ALLOWED",
    "MAX_LIVE_SLOTS",
    "MAX_CONTRACTS_PER_ENTRY",
    "TARGET_UNIVERSE_SIZE",
    "REQUIRED_STRATEGY_INDEPENDENCE",
    "REQUIRED_SIGNAL_POOL_PROVEN",
    "EXCLUDED_TICKERS",
    "StagePolicy",
    "PIPELINE_MAP",
    "build_pipeline_map",
    "stage_policy",
    "stage_is_safe",
    "validate_no_live_orders",
    "render_pipeline_map",
]

"""Supervisor wiring for adaptive strategy management.

This module bridges the persistent strategy registry with the legacy waitlist /
signal-pool / engine chain. It is intentionally pure and side-effect free except
for operating on the supplied registry object.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

try:
    from .strategy_replacement_policy import (
        PortfolioState,
        ReplacementDecision,
        adaptive_threshold,
        evaluate_candidate,
        portfolio_aware_score,
    )
    from .strategy_registry import (
        STATUS_ACTIVE_SIGNAL_POOL,
        STATUS_ACTIVE_WATCHLIST,
        STATUS_CONFLICTED,
        STATUS_EXPIRED,
        STATUS_REJECTED,
        STATUS_ROTATED_OUT,
        STATUS_WAITLIST,
        StrategyRecord,
        StrategyRegistry,
    )
except ImportError:  # pragma: no cover - support direct top-level imports from code/
    from strategy_replacement_policy import (  # type: ignore
        PortfolioState,
        ReplacementDecision,
        adaptive_threshold,
        evaluate_candidate,
        portfolio_aware_score,
    )
    from strategy_registry import (  # type: ignore
        STATUS_ACTIVE_SIGNAL_POOL,
        STATUS_ACTIVE_WATCHLIST,
        STATUS_CONFLICTED,
        STATUS_EXPIRED,
        STATUS_REJECTED,
        STATUS_ROTATED_OUT,
        STATUS_WAITLIST,
        StrategyRecord,
        StrategyRegistry,
    )


@dataclass
class SupervisorSnapshot:
    registry_size: int = 0
    candidate_stream_size: int = 0
    active_watchlist_size: int = 0
    active_signal_pool_size: int = 0
    rejected_size: int = 0
    rotated_out_size: int = 0
    conflicted_size: int = 0
    expired_size: int = 0
    waitlist_size: int = 0
    promotions: list[dict[str, Any]] = field(default_factory=list)
    replacements: list[dict[str, Any]] = field(default_factory=list)


def _normalize_candidate(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "strategy_id": entry.get("strategy_id") or entry.get("id") or f'{entry.get("ticker", "")}_{entry.get("strategy", "")}',
        "ticker": entry["ticker"],
        "strategy": entry["strategy"],
        "params": dict(entry.get("params", {})),
        "metrics": dict(entry.get("metrics", {})),
        "portfolio_context": dict(entry.get("portfolio_context", {})),
        "quality_gate": dict(entry.get("quality_gate", {})),
        "generation_batch_id": entry.get("generation_batch_id", ""),
        "source": entry.get("source", "generator"),
        "status": entry.get("status", STATUS_WAITLIST),
    }


def build_portfolio_state(portfolio: dict[str, Any], cfg: Any | None = None, regime_bias: str = "neutral") -> PortfolioState:
    slots = portfolio.get("slots", {}) or {}
    open_tickers = {slot.get("ticker") for slot in slots.values() if slot.get("open_position")}
    allowed = set(portfolio.get("allowed_tickers", []) or [])
    balance = float(portfolio.get("balance_rub", portfolio.get("equity_rub", 0.0)) or portfolio.get("peak_equity", 0.0) or 0.0)
    if cfg is not None:
        go_budget = float(getattr(cfg, "go_budget_rub", 0.0))
        max_slots = int(getattr(getattr(cfg, "risk", None), "max_slots", portfolio.get("max_slots", 10)) or portfolio.get("max_slots", 10))
        used_go = float(portfolio.get("used_go_rub", 0.0) or 0.0)
        risk_limit_pct = float(getattr(getattr(cfg, "risk", None), "delta_band_pct", 0.0) or 0.0)
    else:
        go_budget = float(portfolio.get("go_budget_rub", 0.0) or 0.0)
        max_slots = int(portfolio.get("max_slots", 10) or 10)
        used_go = float(portfolio.get("used_go_rub", 0.0) or 0.0)
        risk_limit_pct = float(portfolio.get("risk_limit_pct", 0.0) or 0.0)
    return PortfolioState(
        balance_rub=balance,
        go_budget_rub=go_budget,
        used_go_rub=used_go,
        max_slots=max_slots,
        open_tickers=open_tickers,
        allowed_tickers=allowed,
        regime_bias=regime_bias,
        risk_limit_pct=risk_limit_pct,
        stale_signal_ids=set(portfolio.get("stale_signal_ids", []) or []),
        no_free_slots=bool(portfolio.get("no_free_slots", False)),
        existing_watchlist_size=int(portfolio.get("existing_watchlist_size", 0) or 0),
        active_slots=sum(1 for slot in slots.values() if slot.get("open_position")),
        signal_pool_size=int(portfolio.get("signal_pool_size", 0) or 0),
    )


def ingest_generated_strategies(
    registry: StrategyRegistry,
    generated: Iterable[dict[str, Any]],
    portfolio: dict[str, Any],
    *,
    regime_bias: str = "neutral",
    batch_id: str = "",
) -> list[StrategyRecord]:
    """Persist every generated strategy in the registry and classify it.

    All generated strategies are stored permanently as registry candidates.
    Strategies that fail the quality gate become rejected; the rest enter the
    candidate/waitlist stream.
    """
    state = build_portfolio_state(portfolio, regime_bias=regime_bias)
    out: list[StrategyRecord] = []
    for raw in generated:
        item = _normalize_candidate(raw)
        item["generation_batch_id"] = batch_id or item["generation_batch_id"]
        decision = evaluate_candidate(item, registry.active_watchlist(), state)
        record = registry.record_generation(
            strategy_id=item["strategy_id"],
            ticker=item["ticker"],
            strategy=item["strategy"],
            params=item.get("params"),
            metrics=item.get("metrics"),
            portfolio_context=item.get("portfolio_context"),
            quality_gate=item.get("quality_gate"),
            source=item.get("source", "generator"),
            generation_batch_id=item.get("generation_batch_id", batch_id),
            status=STATUS_WAITLIST if decision.quality_gate_passed else STATUS_REJECTED,
            note="candidate_ingested",
            payload={"decision": decision.__dict__},
        )
        record.quality_gate.setdefault("adaptive_threshold", decision.effective_threshold)
        record.quality_gate.setdefault("promotion_candidate_score", decision.candidate_score)
        if decision.promoted:
            registry.mark_active_watchlist(record.strategy_id, score=decision.candidate_score)
        elif decision.status == STATUS_CONFLICTED:
            registry.mark_conflicted(record.strategy_id, reason=decision.rejected_reason)
        elif decision.status == STATUS_REJECTED:
            registry.mark_rejected(record.strategy_id, reason=decision.rejected_reason)
        else:
            registry.mark_waitlist(record.strategy_id, reason=decision.rejected_reason)
        out.append(registry.get(record.strategy_id) or record)
    registry.prune_history()
    return out


def build_legacy_views(registry: StrategyRegistry) -> tuple[dict[str, Any], dict[str, Any]]:
    return registry.export_legacy_waitlist(), registry.export_legacy_signal_pool()


def refresh_active_watchlist(
    registry: StrategyRegistry,
    portfolio: dict[str, Any],
    *,
    limit: int = 10,
    regime_bias: str = "neutral",
) -> dict[str, Any]:
    """Apply performance-based replacement to the active watchlist."""
    current = registry.active_watchlist(limit=limit)
    state = build_portfolio_state(portfolio, regime_bias=regime_bias)
    state.max_slots = limit
    snapshot = SupervisorSnapshot(
        registry_size=len(registry.records()),
        candidate_stream_size=len(registry.candidate_stream()),
        active_watchlist_size=len(current),
        active_signal_pool_size=len(registry.active_signal_pool(limit=limit)),
        rejected_size=len(registry.active_by_status([STATUS_REJECTED])),
        rotated_out_size=len(registry.active_by_status([STATUS_ROTATED_OUT])),
        conflicted_size=len(registry.active_by_status([STATUS_CONFLICTED])),
        expired_size=len(registry.active_by_status([STATUS_EXPIRED])),
        waitlist_size=len([r for r in registry.records() if r.status == STATUS_WAITLIST]),
    )

    candidates = sorted(
        [r for r in registry.records() if r.status in {STATUS_WAITLIST, STATUS_ACTIVE_SIGNAL_POOL, STATUS_ACTIVE_WATCHLIST}],
        key=lambda r: portfolio_aware_score(r, state),
        reverse=True,
    )
    active_ids = {r.strategy_id for r in current}
    for cand in candidates:
        decision = evaluate_candidate(cand, current, state)
        if decision.promoted and cand.strategy_id not in active_ids:
            if len(current) < limit:
                registry.mark_active_watchlist(cand.strategy_id, score=decision.candidate_score)
                current = registry.active_watchlist(limit=limit)
                active_ids = {r.strategy_id for r in current}
                snapshot.promotions.append({"candidate": cand.strategy_id, "replaced": None, "score": decision.candidate_score})
                continue
            if decision.replacement_id:
                registry.mark_rotated_out(decision.replacement_id, reason="performance_based_replacement")
                registry.mark_active_watchlist(cand.strategy_id, score=decision.candidate_score)
                current = registry.active_watchlist(limit=limit)
                active_ids = {r.strategy_id for r in current}
                snapshot.replacements.append({"candidate": cand.strategy_id, "replaced": decision.replacement_id, "score": decision.candidate_score, "worst_score": decision.worst_score})
    registry.save()
    return {
        "snapshot": snapshot,
        "watchlist": [r.to_dict() for r in registry.active_watchlist(limit=limit)],
        "signal_pool": [r.to_dict() for r in registry.active_signal_pool(limit=limit)],
    }


def sync_legacy_state(registry: StrategyRegistry, waitlist: dict[str, Any], signal_pool: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Merge legacy state with the persistent registry views."""
    reg_waitlist = registry.export_legacy_waitlist()
    reg_signal_pool = registry.export_legacy_signal_pool()

    merged_waitlist = {"candidates": {**waitlist.get("candidates", {}), **reg_waitlist.get("candidates", {})}}
    merged_signal_pool = {
        "strategies": {**signal_pool.get("strategies", {}), **reg_signal_pool.get("strategies", {})},
        "last_rotation_ts": max(signal_pool.get("last_rotation_ts", 0.0), reg_signal_pool.get("last_rotation_ts", 0.0)),
    }
    return merged_waitlist, merged_signal_pool


def classify_registry(registry: StrategyRegistry) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {
        STATUS_WAITLIST: [],
        STATUS_ACTIVE_WATCHLIST: [],
        STATUS_ACTIVE_SIGNAL_POOL: [],
        STATUS_REJECTED: [],
        STATUS_ROTATED_OUT: [],
        STATUS_CONFLICTED: [],
        STATUS_EXPIRED: [],
    }
    for record in registry.records():
        grouped.setdefault(record.status, []).append(record.strategy_id)
    return grouped

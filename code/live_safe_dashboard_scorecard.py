#!/usr/bin/env python3
from __future__ import annotations

from typing import Any

from live_safe_dashboard_candidates import build_candidate_waitlist
from live_safe_dashboard_data import load_dashboard_bundle
from live_safe_dashboard_metrics import build_allocator_scorecard, build_metrics_summary
from live_safe_dashboard_risk import build_risk_gates
from live_safe_dashboard_positions import build_positions, position_summary


def build_dashboard_scorecard(bundle: dict[str, Any], waitlist_limit: int = 10) -> dict[str, Any]:
    portfolio = bundle["portfolio"]
    broker_positions = bundle["broker_positions"]
    positions = build_positions(portfolio, broker_positions)
    risk = build_risk_gates(portfolio, positions, broker_positions, bundle["systemd_timers"])
    waitlist = build_candidate_waitlist(bundle["strategy_registry"], bundle.get("waitlist"), limit=waitlist_limit)
    metrics = build_metrics_summary(bundle["strategy_registry"], portfolio)
    allocator = build_allocator_scorecard(metrics, {
        "max_live_slots": 3,
        "max_contracts_per_entry": 1,
    })

    payload = {
        "sources": {
            "dry_run_dir": str(bundle["dry_run_dir"]),
            "state_dir": str(bundle["state_dir"]),
        },
        "pipeline": {
            "order": ["analytics", "list", "pool", "risk", "live"],
            "safe_modes": ["dry-run", "fixtures"],
            "live_orders_allowed": False,
            "max_live_slots": 3,
            "max_contracts_per_entry": 1,
            "ri_excluded": True,
        },
        "portfolio": {
            "halted": bool(portfolio.get("halted")),
            "halt_reason": portfolio.get("halt_reason"),
            "halt_message": risk["halt_message"],
            "peak_equity": portfolio.get("peak_equity"),
            "deposit_rub": portfolio.get("deposit_rub"),
        },
        "positions": positions,
        "position_summary": position_summary(positions),
        "risk_gates": risk["gates"],
        "risk_overall": risk["overall"],
        "slot_reasons": risk["slot_reasons"],
        "waitlist": waitlist,
        "metrics": metrics,
        "allocator": allocator,
        "decision_totals": {
            "keep": metrics["decisions"]["keep"],
            "drop": metrics["decisions"]["drop"],
            "retest": metrics["decisions"]["retest"],
        },
    }
    return payload


def build_dashboard_from_fixture(dry_run_dir=None, state_dir=None, waitlist_limit: int = 10) -> dict[str, Any]:
    bundle = load_dashboard_bundle(dry_run_dir, state_dir)
    return build_dashboard_scorecard(bundle, waitlist_limit=waitlist_limit)

#!/usr/bin/env python3
"""Initialize canonical strategy_registry.json from legacy signal_pool/portfolio.

Creates a StrategyRegistry-compatible file with statuses/history and exports
legacy waitlist/signal_pool derived views. Safe: no broker imports, no orders.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = Path(_HERE).parent
_STATE_DIR = _PROJECT_ROOT / "state"
_REGISTRY_PATH = _STATE_DIR / "strategy_registry.json"
_SIGNAL_POOL_PATH = _STATE_DIR / "signal_pool.json"
_PORTFOLIO_PATH = _STATE_DIR / "portfolio.json"

if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from strategy_registry import (  # noqa: E402
    STATUS_ACTIVE_SIGNAL_POOL,
    STATUS_ACTIVE_WATCHLIST,
    StrategyRegistry,
)


def _load_json(path: Path) -> Dict[str, Any]:
    """Load JSON file, return empty dict on error."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _build_registry_from_signal_pool(sp: Dict[str, Any]) -> Dict[str, Any]:
    """Build normalized strategy entries from signal_pool.json."""
    strategies: Dict[str, Any] = {}
    if isinstance(sp, dict) and isinstance(sp.get("strategies"), dict):
        iterable = sp["strategies"].items()
    elif isinstance(sp, dict) and isinstance(sp.get("signals"), list):
        iterable = (("", item) for item in sp["signals"])
    else:
        iterable = []

    for key_hint, sig in iterable:
        if not isinstance(sig, dict):
            continue
        ticker = sig.get("ticker", sig.get("symbol", ""))
        strategy = sig.get("strategy", sig.get("strategy_name", ""))
        if not ticker or not strategy:
            continue
        strategy_id = str(key_hint) if key_hint else f"{ticker}__{strategy}"
        metrics = dict(sig.get("metrics") or {})
        strategies[strategy_id] = {
            "strategy_id": strategy_id,
            "ticker": ticker,
            "strategy": strategy,
            "direction": sig.get("direction", metrics.get("direction", "")),
            "source": "signal_pool",
            "params": dict(sig.get("params") or {}),
            "metrics": metrics,
            "rank_score": float(sig.get("rank_score", metrics.get("rank_score", 0.0)) or 0.0),
            "status": sig.get("status", STATUS_ACTIVE_SIGNAL_POOL),
            "go_rub": sig.get("go_rub", 0.0),
        }
    return strategies


def _build_registry_from_portfolio(pf: Dict[str, Any]) -> Dict[str, Any]:
    """Build normalized strategy entries from portfolio.json slots."""
    strategies: Dict[str, Any] = {}
    raw_slots = pf.get("slots", {}) if isinstance(pf, dict) else {}
    if isinstance(raw_slots, dict):
        slots = [(slot_id, slot) for slot_id, slot in raw_slots.items()]
    elif isinstance(raw_slots, list):
        slots = [(slot.get("slot_id", slot.get("id", "")), slot) for slot in raw_slots if isinstance(slot, dict)]
    else:
        slots = []

    for slot_id, slot in slots:
        if not isinstance(slot, dict):
            continue
        ticker = slot.get("ticker", "")
        strategy = slot.get("strategy", slot.get("strategy_name", ""))
        if not ticker or not strategy:
            continue
        open_pos = slot.get("open_position") or {}
        strategy_id = f"{ticker}_{strategy}"
        metrics = {
            "pnl": slot.get("pnl_rub", 0.0),
            "trades": slot.get("n_trades", 0),
            "rank_score": slot.get("rank_score", slot.get("pnl_rub", 0.0)),
        }
        direction = open_pos.get("direction", slot.get("direction", ""))
        strategies[strategy_id] = {
            "strategy_id": strategy_id,
            "ticker": ticker,
            "strategy": strategy,
            "direction": direction,
            "source": "portfolio",
            "slot_id": slot_id,
            "params": dict(slot.get("params") or {}),
            "metrics": metrics,
            "rank_score": float(metrics.get("rank_score") or 0.0),
            "status": STATUS_ACTIVE_SIGNAL_POOL if slot.get("open_position") else STATUS_ACTIVE_WATCHLIST,
            "go_rub": slot.get("go_rub", 0.0),
        }
    return strategies


def init_registry(force: bool = False) -> Dict[str, Any]:
    """Initialize canonical strategy_registry.json and derived legacy views."""
    existing = _load_json(_REGISTRY_PATH)
    if existing and not force:
        n = len(existing.get("strategies", {}))
        print(f"strategy_registry.json already exists with {n} strategies. Use --force to overwrite.")
        return existing

    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    sp_data = _load_json(_SIGNAL_POOL_PATH)
    pf_data = _load_json(_PORTFOLIO_PATH)

    normalized: Dict[str, Dict[str, Any]] = {}
    normalized.update(_build_registry_from_signal_pool(sp_data))
    normalized.update(_build_registry_from_portfolio(pf_data))

    registry = StrategyRegistry(_REGISTRY_PATH)
    registry._data = registry._default_data()  # force a canonical blank store

    for item in normalized.values():
        record = registry.record_generation(
            strategy_id=item["strategy_id"],
            ticker=item["ticker"],
            strategy=item["strategy"],
            params=item.get("params"),
            metrics=item.get("metrics"),
            portfolio_context={"go_rub": item.get("go_rub", 0.0), "source_slot_id": item.get("slot_id", "")},
            quality_gate={"signals_generated": 0},
            source=item.get("source", "migration"),
            status=item.get("status", STATUS_ACTIVE_WATCHLIST),
            note="initialized_from_legacy_state",
            payload={"direction": item.get("direction", "")},
        )
        record.active_rank = float(item.get("rank_score", 0.0) or 0.0)
        if item.get("direction"):
            record.metrics.setdefault("direction", item.get("direction"))
        registry._store_record(record)

    registry.save()
    registry.export_legacy_state_files()

    result = registry.as_dict()
    n_total = len(result.get("strategies", {}))
    print(f"Canonical registry initialized: {n_total} strategies")
    print(f"Written to: {_REGISTRY_PATH}")
    print("Derived waitlist/signal_pool exported")
    return result


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Initialize canonical strategy_registry.json")
    parser.add_argument("--force", action="store_true", help="Overwrite existing registry")
    args = parser.parse_args()
    init_registry(force=args.force)


if __name__ == "__main__":
    main()

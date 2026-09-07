#!/usr/bin/env python3
"""Safe dry-run environment helpers for strategy_combine.

Purpose
-------
Provide a local-only config/source of truth for staged expansion tooling.
This module intentionally avoids reading the root config.json as the primary
source, because the project root config is live-mode oriented.

The helpers below prefer local dry-run fixtures when they exist and otherwise
fall back to a synthesized paper-safe config that never enables live orders.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DRYRUN_FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "dry-run"
REPORT_DIR = PROJECT_ROOT / "reports" / "strategy_architect"
UNIVERSE_DISCOVERY_JSON = REPORT_DIR / "universe_discovery.json"
ROOT_CONFIG_JSON = PROJECT_ROOT / "config.json"

DEFAULT_UNIVERSE = ["BR", "GAZP", "SBER", "CNY", "EURRUB", "USDRUB", "IMOEX", "NG"]
DEFAULT_EXCLUDED = ["RI"]
DEFAULT_TIMEFRAMES = ["15m", "1h"]
DEFAULT_MIN_ROWS = 300
DEFAULT_TARGET_SIZE = 20


def read_json(path: Path, default: Any = None) -> Any:
    """Read JSON from *path* or return *default* when the file is absent."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if default is not None:
            return default
        raise
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON in {path}: {exc}") from exc


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def default_safe_config() -> Dict[str, Any]:
    """Build a deterministic paper-safe config.

    The config is intentionally conservative and is suitable for dry-run
    smoke tooling and report generation.
    """
    discovery = read_json(UNIVERSE_DISCOVERY_JSON, default={})
    ready_roots = discovery.get("ready_roots") or []
    universe = list(ready_roots[:DEFAULT_TARGET_SIZE]) or list(DEFAULT_UNIVERSE)
    excluded = sorted(set(DEFAULT_EXCLUDED) | {str(x) for x in discovery.get("excluded", [])})
    return {
        "mode": "paper",
        "paper_first": True,
        "deposit_rub": 100000,
        "risk": {
            "risk_per_trade_pct": 2.7,
            "go_budget_pct": 50,
            "max_slots": 3,
            "portfolio_stop_drawdown_pct": 25,
            "slot_eject_pf": 0.9,
            "slot_eject_window_trades": 20,
            "slot_eject_streak_stops": 3,
            "slot_eject_slot_drawdown_pct": 15,
            "slot_eject_silent_days": 5,
            "promotion_margin_pct": 10,
            "waitlist_ttl_days": 7,
            "waitlist_max": 20,
            "delta_band_pct": 30,
            "min_reserve_pct": 30,
            "signal_pool_max": 10,
            "signal_rotation_days": 3,
            "signal_min_rank": 100,
            "signal_max_age_minutes": 16,
            "max_contracts_per_entry": 1,
        },
        "universe": universe,
        "excluded": excluded,
        "engine": {
            "sl_atr_mult": 2.0,
            "tp_atr_mult": 3.0,
            "atr_period": 14,
            "force_exit_hours": 48,
            "interval": "15m",
        },
        "source": "synthesized_dryrun",
    }


def fixture_path(*parts: str) -> Path:
    """Resolve a path under tests/fixtures/dry-run."""
    return DRYRUN_FIXTURE_ROOT.joinpath(*parts)


def load_local_dryrun_config() -> Dict[str, Any]:
    """Load the most local-safe config available.

    Priority:
      1. tests/fixtures/dry-run/config.json (if present)
      2. synthesized paper-safe config

    The root config.json is never used as the primary source.
    """
    candidates = [
        fixture_path("config.json"),
        fixture_path("strategy_combine_config.json"),
        fixture_path("paper_config.json"),
    ]
    for candidate in candidates:
        if candidate.exists():
            cfg = read_json(candidate)
            if isinstance(cfg, dict):
                cfg = dict(cfg)
                cfg["mode"] = "paper"
                cfg["paper_first"] = True
                cfg["source"] = str(candidate)
                return cfg
    cfg = default_safe_config()
    cfg["source"] = "default_safe_config"
    return cfg


def load_root_config_snapshot() -> Dict[str, Any]:
    """Return the root config snapshot for comparison/reporting only."""
    if not ROOT_CONFIG_JSON.exists():
        return {"exists": False, "path": str(ROOT_CONFIG_JSON)}
    cfg = read_json(ROOT_CONFIG_JSON, default={})
    if not isinstance(cfg, dict):
        raise RuntimeError(f"root config is not an object: {ROOT_CONFIG_JSON}")
    return {"exists": True, "path": str(ROOT_CONFIG_JSON), "config": cfg}


def available_fixture_files() -> List[str]:
    """List local dry-run fixture files used by staged expansion tooling."""
    if not DRYRUN_FIXTURE_ROOT.exists():
        return []
    files: List[str] = []
    for path in sorted(DRYRUN_FIXTURE_ROOT.rglob("*")):
        if path.is_file():
            files.append(str(path.relative_to(PROJECT_ROOT)))
    return files


def merge_safe_config(overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return a safe config merged with *overrides*.

    Nested dictionaries are merged shallowly on purpose to keep the function
    predictable and free of live-mode side effects.
    """
    cfg = default_safe_config()
    if overrides:
        for key, value in overrides.items():
            if key == "risk" and isinstance(value, dict):
                merged = dict(cfg.get("risk", {}))
                merged.update(value)
                cfg["risk"] = merged
            elif key == "engine" and isinstance(value, dict):
                merged = dict(cfg.get("engine", {}))
                merged.update(value)
                cfg["engine"] = merged
            else:
                cfg[key] = value
    cfg["mode"] = "paper"
    cfg["paper_first"] = True
    return cfg


def summarize_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Compact config summary for reports."""
    risk = cfg.get("risk", {}) if isinstance(cfg.get("risk"), dict) else {}
    return {
        "mode": cfg.get("mode"),
        "paper_first": bool(cfg.get("paper_first", False)),
        "universe_size": len(cfg.get("universe", []) or []),
        "excluded": list(cfg.get("excluded", []) or []),
        "max_slots": _safe_int(risk.get("max_slots", 0), 0),
        "max_contracts_per_entry": _safe_int(risk.get("max_contracts_per_entry", 0), 0),
        "signal_max_age_minutes": _safe_int(risk.get("signal_max_age_minutes", 0), 0),
        "source": cfg.get("source", "unknown"),
    }


def local_only_banner() -> str:
    """Human-readable safety banner."""
    return "local-only dry-run: fixtures/reports/data files; no live orders, no broker mutations"


if __name__ == "__main__":
    payload = {
        "banner": local_only_banner(),
        "config": summarize_config(load_local_dryrun_config()),
        "fixture_files": available_fixture_files()[:20],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))

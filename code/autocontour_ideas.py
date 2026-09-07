"""Autocontour Ideas — dry-run generation of backtest candidates.

This module bridges the current combine pipeline (analytics → list → pool → risk)
with a small, checkable idea-generation layer. It stays read-only:
- no broker/client imports
- no network calls
- no live orders
- contracts per entry are always capped at 1
- RI is excluded by default

Public API:
  - generate_autocontour_candidates(...)
  - score_fixture_ideas(...)
  - write_candidates_report(...)

The implementation reuses:
  - strategy_ideas.py for template suggestion and scoring
  - oss_shortlist.py for OSS inspiration summary and reject/adopt split
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

_CODE_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _CODE_DIR.parent
_FIXTURES_DIR = _PROJECT_DIR / "tests" / "fixtures"
_DEFAULT_FIXTURE = _FIXTURES_DIR / "strategy_ideas_fixture.json"
_DEFAULT_OUTPUT = _PROJECT_DIR / "autocontour_candidates.json"

if str(_CODE_DIR) not in sys.path:
    sys.path.insert(0, str(_CODE_DIR))

from oss_shortlist import summary as shortlist_summary
from strategy_ideas import build_idea, score_ideas_batch, suggest_templates

DEFAULT_LIMITS: Dict[str, Any] = {
    "max_slots": 3,
    "max_contracts_per_entry": 1,
    "excluded": ["RI"],
}


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def _merge_limits(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged = {
        "max_slots": DEFAULT_LIMITS["max_slots"],
        "max_contracts_per_entry": DEFAULT_LIMITS["max_contracts_per_entry"],
        "excluded": list(DEFAULT_LIMITS["excluded"]),
    }
    if not config:
        return merged

    risk = config.get("risk", {}) if isinstance(config, dict) else {}
    merged["max_slots"] = int(risk.get("max_slots", merged["max_slots"]))
    merged["max_contracts_per_entry"] = int(
        risk.get("max_contracts_per_entry", merged["max_contracts_per_entry"])
    )

    excluded = config.get("excluded", merged["excluded"])
    if isinstance(excluded, list):
        merged["excluded"] = [str(item) for item in excluded]
    return merged


def _candidate_seed_from_fixture(fixture: Dict[str, Any]) -> List[Dict[str, Any]]:
    ideas = fixture.get("ideas", [])
    if not isinstance(ideas, list):
        return []
    seeds: List[Dict[str, Any]] = []
    for raw in ideas:
        if not isinstance(raw, dict):
            continue
        seeds.append({
            "ticker": str(raw.get("ticker", "")),
            "strategy_name": str(raw.get("strategy_name", raw.get("strategy", ""))),
            "direction": raw.get("direction"),
            "win_rate": float(raw.get("win_rate", 0.5)),
            "avg_win": float(raw.get("avg_win", 100.0)),
            "avg_loss": float(raw.get("avg_loss", 100.0)),
            "drawdown_pct": float(raw.get("drawdown_pct", 0.0)),
            "volatility": float(raw.get("volatility", 0.0)),
            "description": raw.get("description", ""),
            "source": "fixture",
        })
    return seeds


def _candidate_seed_from_regime(regime_snapshot: Dict[str, Any], config: Dict[str, Any]) -> List[Dict[str, Any]]:
    seeds: List[Dict[str, Any]] = []
    tickers = regime_snapshot.get("tickers", {}) if isinstance(regime_snapshot, dict) else {}
    if not isinstance(tickers, dict):
        return seeds

    universe = list(tickers.keys())
    if not universe and isinstance(config.get("universe"), list):
        universe = [str(item) for item in config.get("universe", [])]

    for ticker in universe:
        ticker_data = tickers.get(ticker, {})
        regime = str(ticker_data.get("regime", "mixed"))
        direction = ticker_data.get("direction") or regime_snapshot.get("bias") or "neutral"
        templates = suggest_templates(regime_snapshot, direction_hint=direction)
        if not templates:
            templates = ["ema_cross"]
        template = templates[0]
        base_win_rate = 0.58 if regime == "trend" else 0.47
        avg_win = 180.0 if regime == "trend" else 110.0
        avg_loss = 75.0 if regime == "trend" else 95.0
        drawdown_pct = 4.0 if regime == "trend" else 7.0
        volatility = 3.0 if regime == "trend" else 5.5
        seeds.append({
            "ticker": ticker,
            "strategy_name": template,
            "direction": "LONG" if str(direction).lower() in {"up", "long", "bull"} else "SHORT",
            "win_rate": base_win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "drawdown_pct": drawdown_pct,
            "volatility": volatility,
            "description": f"generated from regime={regime}",
            "source": "regime",
        })

    return seeds


def _build_scored_candidates(
    seeds: Iterable[Dict[str, Any]],
    regime_snapshot: Optional[Dict[str, Any]],
    feedback: Optional[Dict[str, Any]],
    config: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    limits = _merge_limits(config)
    cfg = {
        "excluded": limits["excluded"],
        "max_contracts_per_entry": limits["max_contracts_per_entry"],
    }
    idea_inputs: List[Dict[str, Any]] = []
    for seed in seeds:
        ticker = str(seed.get("ticker", ""))
        if not ticker:
            continue
        if ticker.upper() in {item.upper() for item in limits["excluded"]}:
            continue
        idea_inputs.append(
            build_idea(
                ticker=ticker,
                strategy_name=str(seed.get("strategy_name", "unknown")),
                params_hint={
                    "source": seed.get("source", "generated"),
                    "description": seed.get("description", ""),
                },
            )
        )
        idea_inputs[-1].update({
            "direction": seed.get("direction"),
            "win_rate": float(seed.get("win_rate", 0.5)),
            "avg_win": float(seed.get("avg_win", 100.0)),
            "avg_loss": float(seed.get("avg_loss", 100.0)),
            "drawdown_pct": float(seed.get("drawdown_pct", 0.0)),
            "volatility": float(seed.get("volatility", 0.0)),
        })

    scored = score_ideas_batch(idea_inputs, regime_snapshot, feedback, cfg)
    return scored


def generate_autocontour_candidates(
    regime_snapshot: Optional[Dict[str, Any]] = None,
    feedback: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
    idea_fixture: Optional[Dict[str, Any]] = None,
    max_candidates: Optional[int] = None,
) -> Dict[str, Any]:
    """Generate dry-run candidate ideas for the backtest loop.

    The output is intentionally small and predictable:
      - shortlist/OSS summary is attached for traceability
      - RI is excluded by default
      - all ideas have contracts=1
      - selected ideas are capped by max_slots (default 3)
    """
    limits = _merge_limits(config)
    fixture = idea_fixture or _load_json(_DEFAULT_FIXTURE)
    if regime_snapshot is None:
        regime_snapshot = fixture.get("regime_snapshot", {}) if isinstance(fixture, dict) else {}
    if feedback is None:
        feedback = fixture.get("feedback", {}) if isinstance(fixture, dict) else {}

    fixture_seeds = _candidate_seed_from_fixture(fixture)
    regime_seeds = _candidate_seed_from_regime(regime_snapshot, config or {})

    seeds: List[Dict[str, Any]] = []
    seen = set()
    for seed in fixture_seeds + regime_seeds:
        key = (seed.get("ticker", ""), seed.get("strategy_name", ""))
        if key in seen:
            continue
        seen.add(key)
        seeds.append(seed)

    scored = _build_scored_candidates(seeds, regime_snapshot, feedback, config)
    scored = [dict(item, contracts=1) for item in scored]
    scored.sort(key=lambda item: item.get("score", float("-inf")), reverse=True)

    selected_cap = limits["max_slots"]
    if max_candidates is not None:
        selected_cap = min(selected_cap, int(max_candidates))
    selected = scored[:selected_cap]

    report = {
        "pipeline": ["analytics", "list", "pool", "risk"],
        "mode": "dry_run",
        "limits": limits,
        "shortlist": shortlist_summary(),
        "idea_sources": {
            "fixture_ideas": len(fixture_seeds),
            "regime_generated": len(regime_seeds),
            "selected": len(selected),
            "total_scored": len(scored),
        },
        "candidate_pool": scored,
        "selected": selected,
        "feedback_seed": {
            "best_hours": list(feedback.get("best_hours", [])) if isinstance(feedback, dict) else [],
            "directional_pnl": {
                str(k): (v.get("pnl") if isinstance(v, dict) else v)
                for k, v in (feedback.get("LONG", {}) and {"LONG": feedback.get("LONG", {})}).items()
            } if isinstance(feedback, dict) else {},
        },
    }
    return report


def score_fixture_ideas(
    fixture_path: Optional[Path] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Convenience wrapper that scores the bundled strategy ideas fixture."""
    path = fixture_path or _DEFAULT_FIXTURE
    fixture = _load_json(path)
    return generate_autocontour_candidates(
        regime_snapshot=fixture.get("regime_snapshot", {}),
        feedback=fixture.get("feedback", {}),
        config={
            **(fixture.get("config", {}) if isinstance(fixture.get("config", {}), dict) else {}),
            **(config or {}),
        },
        idea_fixture=fixture,
    )


def write_candidates_report(report: Dict[str, Any], output_path: Optional[Path] = None) -> Path:
    """Persist a dry-run candidate report to a local artifact file."""
    path = output_path or _DEFAULT_OUTPUT
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return path


def main() -> int:
    report = score_fixture_ideas()
    path = write_candidates_report(report)
    print(json.dumps({
        "output": str(path),
        "selected": len(report.get("selected", [])),
        "total_scored": len(report.get("candidate_pool", [])),
        "shortlist_adopt": report.get("shortlist", {}).get("adopt_count", 0),
        "shortlist_reject": report.get("shortlist", {}).get("reject_count", 0),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

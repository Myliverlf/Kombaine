"""Autocontour Orchestrator — dry-run glue for analytics → list → pool → risk → live.

The orchestrator composes the read-only autocontour steps into one artifact:
- generate candidates from fixtures / regime snapshot
- run fixture-driven backtest
- merge generator feedback from the backtest scorecard
- validate safety gates
- persist a small offline report

No broker calls, no live orders, no network access.
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
_DEFAULT_IDEA_FIXTURE = _FIXTURES_DIR / "strategy_ideas_fixture.json"
_DEFAULT_BACKTEST_FIXTURE = _FIXTURES_DIR / "backtest_feedback_fixture.json"
_DEFAULT_OUTPUT = _PROJECT_DIR / "autocontour_orchestrator.json"

if str(_CODE_DIR) not in sys.path:
    sys.path.insert(0, str(_CODE_DIR))

from autocontour_backtest import run_autocontour_backtest
from autocontour_feedback import build_feedback_payload
from autocontour_ideas import generate_autocontour_candidates
from autocontour_safety import validate_autocontour_artifact
from pipeline_map import build_pipeline_map


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _safe_config(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    merged = {
        "mode": "paper",
        "paper_first": True,
        "excluded": ["RI"],
        "risk": {
            "max_slots": 3,
            "max_contracts_per_entry": 1,
        },
    }
    if not isinstance(config, dict):
        return merged

    # Preserve caller-provided metadata, but keep the safety gates hard-coded.
    for key, value in config.items():
        if key == "risk" and isinstance(value, dict):
            merged_risk = dict(merged["risk"])
            merged_risk.update(value)
            merged["risk"] = {
                "max_slots": min(3, int(merged_risk.get("max_slots", 3))),
                "max_contracts_per_entry": 1,
            }
        elif key == "excluded" and isinstance(value, list):
            excluded = {str(item) for item in value}
            excluded.add("RI")
            merged["excluded"] = sorted(excluded)
        elif key in {"mode", "paper_first"}:
            # Keep the orchestrator paper-first and dry-run safe.
            continue
        else:
            merged[key] = value
    return merged


def _source_paths() -> List[Path]:
    return [
        _CODE_DIR / "autocontour_ideas.py",
        _CODE_DIR / "autocontour_backtest.py",
        _CODE_DIR / "autocontour_feedback.py",
        _CODE_DIR / "autocontour_safety.py",
        _CODE_DIR / "pipeline_map.py",
    ]


def build_autocontour_orchestrator(
    regime_snapshot: Optional[Dict[str, Any]] = None,
    feedback: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
    idea_fixture: Optional[Dict[str, Any]] = None,
    backtest_fixture: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a single dry-run artifact for the autocontour loop."""
    idea_fixture_data = idea_fixture or _load_json(_DEFAULT_IDEA_FIXTURE)
    backtest_fixture_data = backtest_fixture or _load_json(_DEFAULT_BACKTEST_FIXTURE)
    pipeline = build_pipeline_map()
    safe_config = _safe_config(config or idea_fixture_data.get("config", {}))

    if regime_snapshot is None and isinstance(idea_fixture_data, dict):
        regime_snapshot = idea_fixture_data.get("regime_snapshot", {})
    if feedback is None and isinstance(idea_fixture_data, dict):
        feedback = idea_fixture_data.get("feedback", {})

    candidate_report = generate_autocontour_candidates(
        regime_snapshot=regime_snapshot,
        feedback=feedback,
        config=safe_config,
        idea_fixture=idea_fixture_data,
    )
    selected_candidates = list(candidate_report.get("selected", []))

    backtest_artifact = run_autocontour_backtest(
        fixture=backtest_fixture_data,
        selected_candidates=selected_candidates,
    )

    feedback_payload = build_feedback_payload(
        existing_feedback=feedback or idea_fixture_data.get("feedback", {}),
        backtest_artifact=backtest_artifact,
    )

    safety = validate_autocontour_artifact(
        config=safe_config,
        backtest_artifact=backtest_artifact,
        source_paths=_source_paths(),
    )

    scorecard = backtest_artifact.get("scorecard", {}) if isinstance(backtest_artifact, dict) else {}
    ab_compare = backtest_artifact.get("ab_compare", {}) if isinstance(backtest_artifact, dict) else {}
    healthy_vs_mixed = ab_compare.get("healthy_vs_mixed", {}) if isinstance(ab_compare, dict) else {}

    report = {
        "pipeline": pipeline.get("order", []),
        "pipeline_map": pipeline,
        "mode": "dry_run",
        "config": safe_config,
        "candidate_report": candidate_report,
        "backtest_artifact": backtest_artifact,
        "feedback_payload": feedback_payload,
        "safety": safety,
        "scorecard": scorecard,
        "metrics": {
            "pnl_up": bool(healthy_vs_mixed.get("pnl_up", False)),
            "risk_down": bool(healthy_vs_mixed.get("risk_down", False)),
            "composite_score": float(scorecard.get("composite_score", 0.0)) if isinstance(scorecard, dict) else 0.0,
            "max_slots": int(backtest_artifact.get("limits", {}).get("max_slots", 3)),
            "max_contracts_per_entry": int(backtest_artifact.get("limits", {}).get("max_contracts_per_entry", 1)),
            "ri_excluded": bool(backtest_artifact.get("limits", {}).get("ri_excluded", True)),
        },
    }

    report["summary"] = {
        "selected_candidates": len(selected_candidates),
        "total_scored_candidates": len(candidate_report.get("candidate_pool", [])),
        "safety_passed": bool(safety.get("passed", False)),
        "healthy_composite": backtest_artifact.get("stats", {}).get("healthy_composite", 0.0),
        "healthy_mdd": backtest_artifact.get("stats", {}).get("healthy_mdd", 0.0),
    }

    return report


def persist_orchestrator_report(report: Dict[str, Any], output_path: Optional[Path] = None) -> Path:
    path = output_path or _DEFAULT_OUTPUT
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path


def run_dry_run_autocontour(
    regime_snapshot: Optional[Dict[str, Any]] = None,
    feedback: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
    idea_fixture: Optional[Dict[str, Any]] = None,
    backtest_fixture: Optional[Dict[str, Any]] = None,
    output_path: Optional[Path] = None,
) -> Dict[str, Any]:
    report = build_autocontour_orchestrator(
        regime_snapshot=regime_snapshot,
        feedback=feedback,
        config=config,
        idea_fixture=idea_fixture,
        backtest_fixture=backtest_fixture,
    )
    report["output_path"] = str(persist_orchestrator_report(report, output_path))
    return report


def main() -> int:
    report = run_dry_run_autocontour()
    print(json.dumps({
        "output": report.get("output_path"),
        "selected": report.get("summary", {}).get("selected_candidates", 0),
        "pnl_up": report.get("metrics", {}).get("pnl_up", False),
        "risk_down": report.get("metrics", {}).get("risk_down", False),
        "composite_score": report.get("metrics", {}).get("composite_score", 0.0),
        "safety_passed": report.get("summary", {}).get("safety_passed", False),
        "ri_excluded": report.get("metrics", {}).get("ri_excluded", False),
        "max_slots": report.get("metrics", {}).get("max_slots", 0),
        "max_contracts_per_entry": report.get("metrics", {}).get("max_contracts_per_entry", 0),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

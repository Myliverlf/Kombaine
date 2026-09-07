"""Autocontour Feedback — read-only generator feedback update.

Takes dry-run backtest artifacts and returns an updated feedback payload
compatible with the existing combine bridge. No live orders, no broker calls,
no state writes.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

_CODE_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _CODE_DIR.parent
_DEFAULT_OUTPUT = _PROJECT_DIR / "autocontour_feedback.json"

from backtest_feedback import extend_generator_feedback


def build_feedback_payload(
    existing_feedback: Optional[Dict[str, Any]],
    backtest_artifact: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge current generator feedback with backtest scorecard output."""
    seed = existing_feedback or {}
    scorecard = backtest_artifact.get("scorecard", {})
    merged = extend_generator_feedback(seed, scorecard)

    merged["autocontour"] = {
        "mode": backtest_artifact.get("mode", "dry_run"),
        "ri_excluded": bool(backtest_artifact.get("limits", {}).get("ri_excluded", True)),
        "max_slots": int(backtest_artifact.get("limits", {}).get("max_slots", 3)),
        "max_contracts_per_entry": int(
            backtest_artifact.get("limits", {}).get("max_contracts_per_entry", 1)
        ),
        "pnl_up": backtest_artifact.get("ab_compare", {}).get("healthy_vs_mixed", {}).get("pnl_up", False),
        "risk_down": backtest_artifact.get("ab_compare", {}).get("healthy_vs_mixed", {}).get("risk_down", False),
        "selected_count": len(backtest_artifact.get("candidate_summary", [])),
    }

    if "generator_feedback" in backtest_artifact and isinstance(backtest_artifact["generator_feedback"], dict):
        merged["generator_feedback"] = backtest_artifact["generator_feedback"]

    return merged


def persist_feedback_payload(payload: Dict[str, Any], output_path: Optional[Path] = None) -> Path:
    path = output_path or _DEFAULT_OUTPUT
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return path


def main() -> int:
    example_artifact = {
        "mode": "dry_run",
        "limits": {"ri_excluded": True, "max_slots": 3, "max_contracts_per_entry": 1},
        "ab_compare": {"healthy_vs_mixed": {"pnl_up": True, "risk_down": True}},
        "candidate_summary": [],
        "scorecard": {"composite_score": 0.5, "equity_metrics": {"equity_r2": 0.8}, "drawdown_metrics": {"max_drawdown": -0.1}, "stability_score": 0.7, "recommendations": [], "degradation": {"health_status": "healthy"}},
    }
    payload = build_feedback_payload({}, example_artifact)
    path = persist_feedback_payload(payload)
    print(json.dumps({"output": str(path), "autocontour": payload.get("autocontour", {})}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

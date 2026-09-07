"""Autocontour Backtest — fixture-driven dry-run statistics.

This module turns the candidate ideas from autocontour_ideas into a tiny
backtest artifact with no broker/orders and no live dependencies.
It reuses the pure feedback layer to produce:
  - PnL↑ / risk↓ scorecard
  - per-candidate metrics
  - local JSON artifact for offline inspection

Public API:
  - run_autocontour_backtest(...)
  - build_backtest_artifact(...)
  - persist_backtest_artifact(...)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

_CODE_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _CODE_DIR.parent
_FIXTURES_DIR = _PROJECT_DIR / "tests" / "fixtures"
_DEFAULT_FIXTURE = _FIXTURES_DIR / "backtest_feedback_fixture.json"
_DEFAULT_OUTPUT = _PROJECT_DIR / "autocontour_backtest.json"

if str(_CODE_DIR) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(_CODE_DIR))

from backtest_feedback import build_feedback_scorecard, extend_generator_feedback
from scorecard_metrics import ab_compare, max_drawdown, sharpe_ratio


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def _scenario_names(fixture: Dict[str, Any]) -> List[str]:
    scenarios = fixture.get("scenarios", {})
    if not isinstance(scenarios, dict):
        return []
    return list(scenarios.keys())


def _scenario_payload(fixture: Dict[str, Any], name: str) -> Dict[str, Any]:
    scenarios = fixture.get("scenarios", {})
    if not isinstance(scenarios, dict):
        return {}
    payload = scenarios.get(name, {})
    return payload if isinstance(payload, dict) else {}


def _equity_to_returns(equity_curve: List[float]) -> List[float]:
    if len(equity_curve) < 2:
        return []
    returns: List[float] = []
    for idx in range(1, len(equity_curve)):
        prev = float(equity_curve[idx - 1])
        curr = float(equity_curve[idx])
        if prev == 0.0:
            returns.append(0.0)
        else:
            returns.append((curr - prev) / abs(prev))
    return returns


def _candidate_returns_from_scorecard(scorecard: Dict[str, Any]) -> List[float]:
    eq = scorecard.get("equity_curve", [])
    if isinstance(eq, list):
        return _equity_to_returns([float(x) for x in eq])
    return []


def _compare_against_baseline(returns: List[float], baseline_returns: List[float]) -> Dict[str, Any]:
    result = ab_compare(baseline_returns, returns)
    result["candidate_sharpe"] = round(sharpe_ratio(returns), 6) if returns else 0.0
    result["candidate_mdd"] = round(max_drawdown(returns), 6) if returns else 0.0
    return result


def build_backtest_artifact(
    fixture: Optional[Dict[str, Any]] = None,
    selected_candidates: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build a read-only backtest artifact from fixture scenarios.

    The artifact contains enough statistics to support dry-run acceptance:
      - composite scorecard
      - A/B comparison on PnL↑/risk↓
      - max_slots <= 3
      - max_contracts_per_entry = 1
      - RI excluded via the candidate selection stage
    """
    data = fixture or _load_json(_DEFAULT_FIXTURE)
    scenarios = data.get("scenarios", {}) if isinstance(data, dict) else {}
    if not isinstance(scenarios, dict):
        scenarios = {}

    healthy = _scenario_payload(data, "healthy")
    degraded = _scenario_payload(data, "degraded")
    mixed = _scenario_payload(data, "mixed")
    single_trade = _scenario_payload(data, "single_trade")
    flat_equity = _scenario_payload(data, "flat_equity")

    baseline_returns = _equity_to_returns([float(v) for v in healthy.get("equity_curve", [])])
    degraded_returns = _equity_to_returns([float(v) for v in degraded.get("equity_curve", [])])
    mixed_returns = _equity_to_returns([float(v) for v in mixed.get("equity_curve", [])])
    single_returns = _equity_to_returns([float(v) for v in single_trade.get("equity_curve", [])])
    flat_returns = _equity_to_returns([float(v) for v in flat_equity.get("equity_curve", [])])

    scenario_scorecards: Dict[str, Any] = {}
    for name, payload in scenarios.items():
        if not isinstance(payload, dict):
            continue
        scorecard = build_feedback_scorecard(
            [float(v) for v in payload.get("equity_curve", [])],
            [float(v) for v in payload.get("trades", [])],
            payload.get("per_strategy_stats", {}),
        )
        scenario_scorecards[name] = scorecard

    selected = selected_candidates or []
    candidate_summary = []
    for candidate in selected:
        candidate_summary.append({
            "ticker": candidate.get("ticker", ""),
            "strategy_name": candidate.get("strategy_name", ""),
            "score": candidate.get("score", 0.0),
            "contracts": int(candidate.get("contracts", 1)),
            "direction": candidate.get("direction"),
        })

    compare_map = {
        "healthy_vs_degraded": _compare_against_baseline(degraded_returns, baseline_returns),
        "healthy_vs_mixed": _compare_against_baseline(mixed_returns, baseline_returns),
        "healthy_vs_single": _compare_against_baseline(single_returns, baseline_returns),
        "healthy_vs_flat": _compare_against_baseline(flat_returns, baseline_returns),
    }

    aggregate = scenario_scorecards.get("healthy", {})
    feedback = extend_generator_feedback(
        data.get("feedback", {}) if isinstance(data, dict) else {},
        aggregate,
    ) if aggregate else {}

    artifact = {
        "mode": "dry_run",
        "source_fixture": str(_DEFAULT_FIXTURE),
        "limits": {
            "max_slots": 3,
            "max_contracts_per_entry": 1,
            "ri_excluded": True,
        },
        "candidate_summary": candidate_summary,
        "scenario_names": _scenario_names(data),
        "scenario_scorecards": scenario_scorecards,
        "ab_compare": compare_map,
        "scorecard": aggregate,
        "generator_feedback": feedback,
        "stats": {
            "selected_count": len(candidate_summary),
            "healthy_composite": aggregate.get("composite_score", 0.0),
            "healthy_r2": aggregate.get("equity_metrics", {}).get("equity_r2", 0.0),
            "healthy_mdd": aggregate.get("drawdown_metrics", {}).get("max_drawdown", 0.0),
        },
    }

    if candidate_summary:
        best = max(candidate_summary, key=lambda item: item.get("score", float("-inf")))
        artifact["best_candidate"] = best

    return artifact


def run_autocontour_backtest(
    fixture: Optional[Dict[str, Any]] = None,
    selected_candidates: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    return build_backtest_artifact(fixture=fixture, selected_candidates=selected_candidates)


def persist_backtest_artifact(
    artifact: Dict[str, Any],
    output_path: Optional[Path] = None,
) -> Path:
    path = output_path or _DEFAULT_OUTPUT
    path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True))
    return path


def main() -> int:
    fixture = _load_json(_DEFAULT_FIXTURE)
    artifact = build_backtest_artifact(fixture)
    path = persist_backtest_artifact(artifact)
    print(json.dumps({
        "output": str(path),
        "scenarios": artifact.get("scenario_names", []),
        "healthy_composite": artifact.get("stats", {}).get("healthy_composite", 0.0),
        "healthy_r2": artifact.get("stats", {}).get("healthy_r2", 0.0),
        "healthy_mdd": artifact.get("stats", {}).get("healthy_mdd", 0.0),
        "ri_excluded": artifact.get("limits", {}).get("ri_excluded", False),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

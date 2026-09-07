"""E2E Scorecard Report — JSON-отчёт с risk/allocator scorecard и дифференциацией.

Генерирует JSON-отчёт:
  - risk/allocator scorecard с распределением по тикерам
  - PnL expectancy, sharpe, risk_score, verdict
  - Дифференциация кандидатов (не все одинаковые)

Безопасный: mode=paper, нет broker imports, live orders запрещены.
"""
from __future__ import annotations

import ast
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from synthetic_fixtures import (
    make_safe_config,
    make_realistic_ideas,
    make_synthetic_returns_map,
    make_synthetic_regime_snapshot,
    make_synthetic_candidates,
    make_synthetic_returns,
)


# ─── Safety: AST-guard ─────────────────────────────────────────────────

def _assert_no_broker() -> None:
    """AST-guard: ensures no broker imports in this module."""
    import inspect
    source = inspect.getsource(inspect.getmodule(_assert_no_broker))
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                lower = alias.name.lower()
                if "tinkoff" in lower or "broker" in lower:
                    raise RuntimeError(f"Broker import detected: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                lower = node.module.lower()
                if "tinkoff" in lower or "broker" in lower:
                    raise RuntimeError(f"Broker import detected: from {node.module}")


_assert_no_broker()


# ─── Scorecard generation ──────────────────────────────────────────────

def generate_scorecard_report() -> Dict[str, Any]:
    """Generate full scorecard report with differentiation.

    Returns:
        Dict with structure:
        {
            "report_type": "e2e_scorecard",
            "config": {...},
            "quality_gate": {...},
            "allocator_scorecard": {...},
            "risk_scorecard": {...},
            "pnl_summary": {...},
            "differentiation": {...},
            "timestamp": float,
        }
    """
    config = make_safe_config()
    ideas = make_realistic_ideas()
    returns_map = make_synthetic_returns_map(ideas)
    regime_snapshot = make_synthetic_regime_snapshot()
    candidates = make_synthetic_candidates()

    # ── Quality Gate results ──
    from strategy_ideas import idea_score
    from quality_gate import run_quality_gate

    scored = []
    for idea in ideas:
        score = idea_score(idea, regime_snapshot, feedback=None, config=config)
        scored.append({**idea, "score": score})

    qg_result = run_quality_gate(
        ideas=scored,
        regime_snapshot=regime_snapshot,
        feedback=None,
        config=config,
        returns_map=returns_map,
    )

    # ── Allocator scorecard (per-candidate composite scoring) ──
    from risk_allocator_scorecard_v2 import compute_composite

    risk_per_trade_rub = config["deposit_rub"] * config["risk"]["risk_per_trade_pct"] / 100.0
    allocator_entries = []
    for c in candidates:
        composite = compute_composite(
            c, risk_per_trade=risk_per_trade_rub, regime_snapshot=regime_snapshot,
        )
        allocator_entries.append({
            "ticker": c["ticker"],
            "strategy": c.get("strategy", ""),
            "direction": c.get("direction", ""),
            "win_rate": c.get("win_rate", 0.0),
            "avg_win": c.get("avg_win", 0.0),
            "avg_loss": c.get("avg_loss", 0.0),
            **composite,
        })

    # Sort by composite_score descending
    allocator_entries.sort(key=lambda x: x["composite_score"], reverse=True)

    # ── Risk scorecard from pipeline ──
    from pipeline_ranker import run_pipeline

    synthetic_returns = make_synthetic_returns()
    now_ts = time.time()
    pipeline_result = run_pipeline(
        config=config,
        candidates=candidates,
        regime_snapshot=regime_snapshot,
        returns=synthetic_returns,
        now_ts=now_ts,
    )

    risk_sc = pipeline_result.get("scorecard") or {}

    # ── PnL summary ──
    selected = pipeline_result.get("selected", [])
    # Build full-data lookup from candidates (pipeline output lacks win_rate etc.)
    candidates_lookup = {c["ticker"]: c for c in candidates}
    candidates_detail = []
    for s in selected:
        ticker = s.get("ticker", "")
        full = candidates_lookup.get(ticker, s)
        win_rate = full.get("win_rate", 0.0)
        avg_win = full.get("avg_win", 0.0)
        avg_loss = full.get("avg_loss", 0.0)
        expectancy = win_rate * avg_win - (1.0 - win_rate) * avg_loss
        candidates_detail.append({
            "ticker": ticker,
            "strategy": full.get("strategy", s.get("strategy", "")),
            "direction": full.get("direction", s.get("direction", "")),
            "win_rate": win_rate,
            "expectancy_rub": round(expectancy, 2),
            "allocator_score": s.get("score", 0.0),
            "contracts": s.get("contracts", 1),
        })

    avg_expectancy = 0.0
    if candidates_detail:
        avg_expectancy = sum(d["expectancy_rub"] for d in candidates_detail) / len(candidates_detail)

    # Sharpe from synthetic returns
    sharpe = 0.0
    all_returns: List[float] = []
    for rets in returns_map.values():
        all_returns.extend(rets)
    if all_returns and len(all_returns) >= 2:
        mean_r = sum(all_returns) / len(all_returns)
        var_r = sum((r - mean_r) ** 2 for r in all_returns) / (len(all_returns) - 1)
        std_r = math.sqrt(var_r) if var_r > 0 else 1.0
        sharpe = round((mean_r / std_r) * math.sqrt(252), 3)

    # ── Differentiation metrics ──
    composite_scores = [e["composite_score"] for e in allocator_entries]
    score_variance = 0.0
    if len(composite_scores) >= 2:
        mean_cs = sum(composite_scores) / len(composite_scores)
        score_variance = sum((s - mean_cs) ** 2 for s in composite_scores) / len(composite_scores)

    risk_scores = []
    for e in allocator_entries:
        # Compute per-ticker risk using risk_penalty
        from allocator_metrics import risk_penalty, expectancy_r, regime_bonus
        r_pen = risk_penalty({
            "drawdown_pct": e.get("drawdown_pct"),
            "volatility": e.get("volatility"),
        })
        risk_scores.append(r_pen)

    risk_variance = 0.0
    if len(risk_scores) >= 2:
        mean_rs = sum(risk_scores) / len(risk_scores)
        risk_variance = sum((s - mean_rs) ** 2 for s in risk_scores) / len(risk_scores)

    report = {
        "report_type": "e2e_scorecard",
        "config": {
            "mode": config.get("mode"),
            "paper_first": config.get("paper_first"),
            "deposit_rub": config.get("deposit_rub"),
            "max_slots": config["risk"]["max_slots"],
            "max_contracts_per_entry": config["risk"]["max_contracts_per_entry"],
            "excluded": config.get("excluded", []),
        },
        "quality_gate": {
            "n_total": qg_result.get("meta", {}).get("n_total", 0),
            "n_passed": qg_result.get("meta", {}).get("n_passed", 0),
            "n_rejected": len(qg_result.get("rejected", [])),
            "rejected_reasons": [
                r.get("_reject_reason", "unknown") for r in qg_result.get("rejected", [])
            ],
        },
        "allocator_scorecard": {
            "n_candidates": len(allocator_entries),
            "entries": allocator_entries,
            "score_variance": round(score_variance, 6),
            "has_differentiation": score_variance > 0.001,
        },
        "risk_scorecard": {
            "risk_score": risk_sc.get("risk_score", -1),
            "verdict": risk_sc.get("verdict", "UNKNOWN"),
            "n_components": len(risk_sc.get("components", {})),
            "components": {
                k: {
                    "value": v.get("value", 0),
                    "weight": v.get("weight", 0),
                    "contribution": v.get("contribution", 0),
                }
                for k, v in (risk_sc.get("components") or {}).items()
                if isinstance(v, dict)
            },
            "per_ticker_risk_variance": round(risk_variance, 6),
        },
        "pnl_summary": {
            "n_selected": len(selected),
            "selected_tickers": [s.get("ticker") for s in selected],
            "avg_expectancy_rub": round(avg_expectancy, 2),
            "synthetic_sharpe": sharpe,
            "candidates_detail": candidates_detail,
        },
        "differentiation": {
            "composite_score_variance": round(score_variance, 6),
            "risk_score_variance": round(risk_variance, 6),
            "has_score_differentiation": score_variance > 0.001,
            "n_unique_composite_scores": len(set(composite_scores)),
        },
        "safety": {
            "mode": config.get("mode"),
            "live_orders": 0,
            "broker_imports": 0,
            "ri_excluded": "RI" in config.get("excluded", []),
        },
        "timestamp": time.time(),
    }

    return report


def _main() -> None:
    """CLI entry point — generates and prints the scorecard report."""
    report = generate_scorecard_report()

    # Pretty print
    print("=" * 70)
    print("E2E SCORECARD REPORT — strategy_combine")
    print("=" * 70)

    print("\n--- Quality Gate ---")
    qg = report["quality_gate"]
    print(f"  Total ideas: {qg['n_total']}")
    print(f"  Passed: {qg['n_passed']}")
    print(f"  Rejected: {qg['n_rejected']}")
    print(f"  Rejected reasons: {qg['rejected_reasons']}")

    print("\n--- Allocator Scorecard ---")
    asc = report["allocator_scorecard"]
    print(f"  Candidates: {asc['n_candidates']}")
    print(f"  Score variance: {asc['score_variance']}")
    print(f"  Has differentiation: {asc['has_differentiation']}")
    for entry in asc["entries"]:
        print(f"    {entry['ticker']:5s} composite={entry['composite_score']:.4f} "
              f"E[R]={entry['expectancy_r']:.4f} risk={entry['risk_penalty']:.4f} "
              f"regime={entry['regime_bonus']:.3f}")

    print("\n--- Risk Scorecard ---")
    rs = report["risk_scorecard"]
    print(f"  Risk score: {rs['risk_score']}")
    print(f"  Verdict: {rs['verdict']}")
    print(f"  Components: {rs['n_components']}")

    print("\n--- PnL Summary ---")
    ps = report["pnl_summary"]
    print(f"  Selected: {ps['n_selected']} ({ps['selected_tickers']})")
    print(f"  Avg expectancy: {ps['avg_expectancy_rub']} RUB")
    print(f"  Synthetic Sharpe: {ps['synthetic_sharpe']}")
    for cd in ps["candidates_detail"]:
        print(f"    {cd['ticker']:5s} {cd['direction']:5s} WR={cd['win_rate']:.2f} "
              f"E[RUB]={cd['expectancy_rub']:>8.1f} Score={cd['allocator_score']:.3f}")

    print("\n--- Differentiation ---")
    diff = report["differentiation"]
    print(f"  Composite score variance: {diff['composite_score_variance']}")
    print(f"  Risk score variance: {diff['risk_score_variance']}")
    print(f"  Has score differentiation: {diff['has_score_differentiation']}")
    print(f"  Unique composite scores: {diff['n_unique_composite_scores']}")

    print("\n--- Safety ---")
    safety = report["safety"]
    print(f"  Mode: {safety['mode']}")
    print(f"  Live orders: {safety['live_orders']}")
    print(f"  Broker imports: {safety['broker_imports']}")
    print(f"  RI excluded: {safety['ri_excluded']}")

    print("=" * 70)

    # Write JSON artifact
    try:
        output_path = Path(_HERE) / "e2e_scorecard_report.json"
        project_root = Path(_HERE).parent
        if (project_root / "config.json").exists():
            output_path = project_root / "e2e_scorecard_report.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False, default=str)
        print(f"\nReport written: {output_path}")
    except OSError as exc:
        print(f"\nWarning: could not write report: {exc}", file=sys.stderr)


if __name__ == "__main__":
    _main()

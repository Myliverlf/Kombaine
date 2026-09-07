"""Scorecard Apostle Report — читаемый текстовый отчёт для Апостола.

Генерирует markdown-отчёт из результатов:
  - risk_scorecard_bridge.build_scorecard()
  - pipeline_ranker.run_pipeline()

Содержит:
  - таблицу слотов (ticker, score, direction, risk_status)
  - summary (risk_score, lifecycle verdict, risk verdict)
  - guard checks (RI excluded, max_slots, contracts)
  - PnL metrics (expectancy, risk_penalty)

Без live-ордеров, без network, без broker.

Usage:
    python3 code/scorecard_apostle_report.py --dry-run
    python3 code/scorecard_apostle_report.py --output verdict.md
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def _status_icon(status: str) -> str:
    """Convert status to visual icon."""
    return {"ok": "✅", "warn": "⚠️", "veto": "❌"}.get(status, "❓")


def _verdict_icon(verdict: str) -> str:
    """Convert verdict to visual icon."""
    return {"ALLOW": "🟢", "REDUCE": "🟡", "VETO": "🔴"}.get(verdict, "⚪")


def generate_report(
    scorecard: Dict[str, Any],
    selected: Optional[List[Dict[str, Any]]] = None,
    lifecycle_verdict: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> str:
    """Generate markdown report from scorecard results.

    Args:
        scorecard: result from build_scorecard() or run_pipeline()["scorecard"]
        selected: list of selected slots from pipeline (optional)
        lifecycle_verdict: lifecycle verdict string (optional)
        meta: pipeline meta dict (optional)

    Returns:
        Markdown string with readable report for Apostle.
    """
    lines: List[str] = []
    risk_score = scorecard.get("risk_score", 0.0)
    verdict = scorecard.get("verdict", "UNKNOWN")
    components = scorecard.get("components", {})
    n_slots = scorecard.get("n_slots", 0)
    tickers = scorecard.get("tickers", [])
    overall = scorecard.get("overall", {})
    constraints = scorecard.get("constraints", {})

    # ── Header ──
    lines.append("# 📊 Risk/Allocator Scorecard Report")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append("| **Risk Score** | **%.1f / 100** %s |" % (risk_score, _verdict_icon(verdict)))
    lines.append("| **Risk Verdict** | **%s** |" % verdict)
    if lifecycle_verdict:
        lines.append("| **Lifecycle Verdict** | **%s** |" % lifecycle_verdict)
    lines.append("| Slots Scored | %d |" % n_slots)
    lines.append("| Tickers | %s |" % ", ".join(tickers) if tickers else "| Tickers | — |")
    lines.append("")

    # ── Guard Checks ──
    lines.append("## Guard Checks")
    lines.append("")
    lines.append("| Check | Status | Detail |")
    lines.append("|-------|--------|--------|")
    for name, comp in components.items():
        icon = _status_icon(comp.get("status", "veto"))
        detail = comp.get("detail", "")
        lines.append("| %s | %s %s | %s |" % (
            name, icon, comp.get("status", "?").upper(), detail
        ))
    lines.append("")

    # ── PnL Metrics ──
    avg_composite = overall.get("avg_composite", 0.0)
    n_scored = overall.get("n_scored", 0)
    lines.append("## PnL Metrics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append("| Avg Composite (E[R]) | %.4f |" % avg_composite)
    lines.append("| Candidates Scored | %d |" % n_scored)
    lines.append("")

    # ── Constraints ──
    if constraints:
        lines.append("## Constraints")
        lines.append("")
        lines.append("| Constraint | Value |")
        lines.append("|------------|-------|")
        lines.append("| Max Slots | %s |" % constraints.get("max_slots", "?"))
        lines.append("| Max Contracts/Entry | %s |" % constraints.get("max_contracts_per_entry", "?"))
        lines.append("| Excluded | %s |" % ", ".join(constraints.get("excluded", [])))
        lines.append("")

    # ── Slot Details (if selected provided) ──
    if selected:
        lines.append("## Selected Slots")
        lines.append("")
        lines.append("| # | Ticker | Direction | Contracts | Score | Expectancy R | Risk Penalty |")
        lines.append("|---|--------|-----------|-----------|-------|--------------|--------------|")
        for i, s in enumerate(selected, 1):
            lines.append("| %d | %s | %s | %d | %.4f | %.4f | %.4f |" % (
                i,
                s.get("ticker", "?"),
                s.get("direction", "?"),
                s.get("contracts", 1),
                s.get("score", 0.0),
                s.get("expectancy_r", 0.0),
                s.get("risk_penalty", 0.0),
            ))
        lines.append("")

    # ── Meta ──
    if meta:
        lines.append("## Pipeline Meta")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append("| Candidates In | %s |" % meta.get("n_candidates", "?"))
        lines.append("| Excluded | %s |" % meta.get("n_excluded", "?"))
        lines.append("| Gated | %s |" % meta.get("n_gated", "?"))
        lines.append("| Selected | %s |" % meta.get("n_selected", "?"))
        lines.append("| Has Forecast | %s |" % meta.get("has_forecast", False))
        lines.append("")

    # ── Footer ──
    lines.append("---")
    lines.append("*Generated by scorecard_apostle_report.py — dry-run only, no live orders.*")
    lines.append("")

    return "\n".join(lines)


def _dry_run() -> str:
    """Demo: generate report from synthetic data."""
    from risk_scorecard_bridge import build_scorecard

    now_ts = 1700000000.0
    deposit = 100000

    slots_dict = {
        "slot_LKOH_1234": {
            "ticker": "LKOH",
            "strategy": "vwap_reversion",
            "contracts": 1,
            "go_rub": deposit * 0.027,
            "open_position": {
                "direction": "LONG", "qty": 1,
                "entry_price": 18000.0, "entry_atr": 1.0,
                "entry_ts": now_ts,
            },
            "pnl_rub": 500.0,
            "peak_pnl_rub": 600.0,
            "stop_streak": 0,
            "last_signal_ts": now_ts,
            "n_trades": 15,
        },
        "slot_GAZP_5678": {
            "ticker": "GAZP",
            "strategy": "mean_reversion",
            "contracts": 1,
            "go_rub": deposit * 0.027,
            "open_position": {
                "direction": "SHORT", "qty": 1,
                "entry_price": 180.0, "entry_atr": 1.0,
                "entry_ts": now_ts,
            },
            "pnl_rub": -200.0,
            "peak_pnl_rub": 100.0,
            "stop_streak": 2,
            "last_signal_ts": now_ts - 300.0,
            "n_trades": 10,
        },
    }

    config = {
        "go_budget_rub": deposit * 0.5,
        "delta_band_pct": 30,
        "deposit_rub": deposit,
        "max_slots": 3,
        "max_contracts_per_entry": 1,
        "excluded": ["RI"],
        "signal_max_age_minutes": 16,
    }

    scorecard = build_scorecard(slots_dict, config, now_ts=now_ts)

    selected = [
        {
            "ticker": "LKOH", "direction": "LONG", "contracts": 1,
            "score": 0.75, "expectancy_r": 0.42, "risk_penalty": 0.15,
        },
        {
            "ticker": "GAZP", "direction": "SHORT", "contracts": 1,
            "score": 0.55, "expectancy_r": 0.28, "risk_penalty": 0.35,
        },
    ]

    meta = {
        "n_candidates": 5,
        "n_excluded": 1,
        "n_gated": 0,
        "n_selected": 2,
        "has_forecast": False,
    }

    report = generate_report(
        scorecard=scorecard,
        selected=selected,
        lifecycle_verdict="ALLOW",
        meta=meta,
    )
    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Scorecard Apostle Report")
    parser.add_argument("--dry-run", action="store_true", help="Run with synthetic data")
    parser.add_argument("--output", type=str, default=None, help="Output file path")
    args = parser.parse_args()

    if args.dry_run:
        report = _dry_run()
    else:
        # Load from pipeline result if available
        result_path = Path(__file__).resolve().parent.parent / "state" / "pipeline_result.json"
        if result_path.exists():
            data = json.loads(result_path.read_text())
            report = generate_report(
                scorecard=data.get("scorecard", {}),
                selected=data.get("selected"),
                lifecycle_verdict=data.get("lifecycle_verdict"),
                meta=data.get("meta"),
            )
        else:
            print("ERROR: no pipeline result found. Use --dry-run or provide state/pipeline_result.json")
            sys.exit(1)

    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
        print("Report written to %s" % args.output)
    else:
        print(report)

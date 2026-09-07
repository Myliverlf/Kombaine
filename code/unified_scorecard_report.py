#!/usr/bin/env python3
"""Unified Scorecard Report — CLI-отчёт: pipeline_ranker + slot_scorecard + risk scorecard.

Формат: консольный + markdown.
CLI: python3 code/unified_scorecard_report.py [--config config.json] [--state state/]
Read-only, без ордеров.
"""
import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from pipeline_ranker import run_pipeline
from slot_scorecard import slot_scorecard
from risk_scorecard import (
    build_scorecard, STATUS_OK, STATUS_WARN, STATUS_VETO,
)


def _load_json(path):
    """Load JSON file, return {} on missing."""
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        return json.load(f)


def _verdict_icon(verdict):
    """Map verdict to icon."""
    return {
        "ALLOW": "✅",
        "WARN": "⚠️",
        "VETO": "🚫",
        "REDUCE": "🔻",
    }.get(verdict, "?")


def _format_component(name, comp):
    """Format one risk component for console."""
    icon = {"OK": "✅", "WARN": "⚠️", "VETO": "🚫",
            "NEUTRAL": "—", "REDUCE": "🔻"}.get(comp.get("status", ""), "?")
    return "  %s %s: %s (%s)" % (icon, name, comp.get("detail", ""), comp.get("status", ""))


def generate_report(config, state_dir, output_format="console"):
    """Generate unified scorecard report.

    config: config.json dict.
    state_dir: path to state/ directory.
    output_format: "console" or "markdown".
    """
    # ── Load state ──
    portfolio = _load_json(os.path.join(state_dir, "portfolio.json"))
    regime = _load_json(os.path.join(state_dir, "regime_snapshot.json"))

    # ── Build candidates from portfolio + synthetic ──
    candidates = []
    for slot_id, slot in portfolio.get("slots", {}).items():
        pos = slot.get("open_position")
        candidates.append({
            "ticker": slot.get("ticker", ""),
            "direction": pos.get("direction") if pos else None,
            "win_rate": slot.get("win_rate", 0.5),
            "avg_win": slot.get("avg_win", 100.0),
            "avg_loss": slot.get("avg_loss", 50.0),
            "contracts_requested": slot.get("contracts", 1),
        })

    # Add synthetic to fill ranking
    existing = {c["ticker"] for c in candidates}
    for synth in [
        {"ticker": "SBER", "direction": "LONG",
         "win_rate": 0.65, "avg_win": 120, "avg_loss": 80, "contracts_requested": 1},
        {"ticker": "BR", "direction": "SHORT",
         "win_rate": 0.55, "avg_win": 90, "avg_loss": 60, "contracts_requested": 1},
        {"ticker": "RI", "direction": "LONG",
         "win_rate": 0.95, "avg_win": 600, "avg_loss": 5, "contracts_requested": 1},
    ]:
        if synth["ticker"] not in existing:
            candidates.append(synth)
            existing.add(synth["ticker"])

    # ── Synthetic returns ──
    returns = [
        0.01, -0.005, 0.02, -0.01, 0.015, -0.002, 0.008,
        0.003, -0.001, 0.012, 0.005, -0.003, 0.009, 0.001,
    ]

    # ── Run pipeline ──
    result = run_pipeline(config, candidates, regime, returns=returns, now_ts=time.time())

    if output_format == "markdown":
        return _format_markdown(result, config, portfolio)
    return _format_console(result, config, portfolio)


def _format_console(result, config, portfolio):
    """Console format."""
    lines = []
    lines.append("=" * 60)
    lines.append("UNIFIED SCORECARD REPORT")
    lines.append("=" * 60)
    meta = result["meta"]
    lines.append("Candidates: %d | Excluded: %d | Gated: %d | Selected: %d" % (
        meta["n_candidates"], meta["n_excluded"], meta["n_gated"], meta["n_selected"]))
    lines.append("")

    # ── Selected slots ──
    lines.append("── SELECTED SLOTS (≤3) ──")
    for i, s in enumerate(result["selected"]):
        lines.append("  [%d] %s %s  score=%.4f" % (
            i + 1, s["ticker"], s.get("direction", "?"), s["score"]))
    lines.append("")

    # ── Per-slot scores ──
    lines.append("── PER-SLOT SCORECARD ──")
    for ps in result["per_slot_scores"]:
        lc = ps.get("lifecycle") or {}
        lines.append("  %s %s:" % (ps["ticker"], ps.get("direction", "")))
        lines.append("    expectancy_r=%.4f  risk_penalty=%.4f  regime_bonus=%.3f  score=%.4f" % (
            ps["expectancy_r"], ps["risk_penalty"], ps["regime_bonus"], ps["score"]))
        if lc:
            lines.append("    hit_rate=%.2f  stability=%s  decay=%s" % (
                lc.get("hit_rate", 0.0),
                "%.2f" % lc["stability"] if lc.get("stability") is not None else "N/A",
                "%.3f" % lc["decay"] if lc.get("decay") is not None else "N/A"))
    lines.append("")

    # ── Risk scorecard ──
    sc = result.get("scorecard")
    if sc:
        lines.append("── RISK SCORECARD ──")
        lines.append("  risk_score=%.1f/100  verdict=%s %s" % (
            sc["risk_score"], _verdict_icon(sc["verdict"]), sc["verdict"]))
        lines.append("  equity=%.0f  peak_equity=%.0f" % (
            sc.get("equity", 0), sc.get("peak_equity", 0)))
        for name, comp in sc.get("components", {}).items():
            lines.append(_format_component(name, comp))
        lines.append("")

    # ── Lifecycle ──
    lc_section = result.get("lifecycle")
    if lc_section:
        lines.append("── LIFECYCLE METRICS ──")
        lines.append("  verdict: %s %s" % (
            _verdict_icon(result["lifecycle_verdict"]), result["lifecycle_verdict"]))
        for name, comp in lc_section.items():
            lines.append(_format_component(name, comp))
        lines.append("")

    # ── Summary ──
    lines.append("=" * 60)
    lines.append("DRY-RUN: no live orders, no broker calls")
    lines.append("MAX_SLOTS=%d  EXCLUDED=%s" % (
        config.get("risk", {}).get("max_slots", 3),
        config.get("excluded", [])))
    lines.append("=" * 60)
    return "\n".join(lines)


def _format_markdown(result, config, portfolio):
    """Markdown format."""
    lines = []
    lines.append("# Unified Scorecard Report\n")
    meta = result["meta"]
    lines.append("Candidates: %d | Excluded: %d | Gated: %d | Selected: %d\n" % (
        meta["n_candidates"], meta["n_excluded"], meta["n_gated"], meta["n_selected"]))

    lines.append("## Selected Slots\n")
    lines.append("| # | Ticker | Direction | Score |")
    lines.append("|---|--------|-----------|-------|")
    for i, s in enumerate(result["selected"]):
        lines.append("| %d | %s | %s | %.4f |" % (
            i + 1, s["ticker"], s.get("direction", "?"), s["score"]))

    lines.append("\n## Per-Slot Scores\n")
    lines.append("| Ticker | Dir | E[R] | Risk Pen | Regime | Score | Hit Rate | Stability |")
    lines.append("|--------|-----|------|----------|--------|-------|----------|-----------|")
    for ps in result["per_slot_scores"]:
        lc = ps.get("lifecycle") or {}
        lines.append("| %s | %s | %.4f | %.4f | %.3f | %.4f | %.2f | %s |" % (
            ps["ticker"], ps.get("direction", "?"),
            ps["expectancy_r"], ps["risk_penalty"], ps["regime_bonus"],
            ps["score"],
            lc.get("hit_rate", 0.0),
            "%.2f" % lc["stability"] if lc.get("stability") is not None else "N/A"))

    sc = result.get("scorecard")
    if sc:
        lines.append("\n## Risk Scorecard\n")
        lines.append("**risk_score=%.1f/100  verdict=%s**\n" % (
            sc["risk_score"], sc["verdict"]))
        lines.append("| Component | Value | Status | Detail |")
        lines.append("|-----------|-------|--------|--------|")
        for name, comp in sc.get("components", {}).items():
            lines.append("| %s | %.4f | %s | %s |" % (
                name, comp["value"], comp["status"], comp.get("detail", "")))

    lc = result.get("lifecycle")
    if lc:
        lines.append("\n## Lifecycle Metrics\n")
        lines.append("**verdict=%s**\n" % result["lifecycle_verdict"])
        lines.append("| Metric | Value | Status | Detail |")
        lines.append("|--------|-------|--------|--------|")
        for name, comp in lc.items():
            lines.append("| %s | %.4f | %s | %s |" % (
                name, comp.get("value", 0), comp.get("status", ""),
                comp.get("detail", "")))

    lines.append("\n---\n*DRY-RUN: no live orders, no broker calls*")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Unified Scorecard Report")
    parser.add_argument("--config", default=os.path.join(_HERE, "..", "config.json"),
                        help="Path to config.json")
    parser.add_argument("--state", default=os.path.join(_HERE, "..", "state"),
                        help="Path to state/ directory")
    parser.add_argument("--format", choices=["console", "markdown"], default="console",
                        help="Output format")
    args = parser.parse_args()

    config = _load_json(args.config)
    if not config:
        print("[FAIL] Cannot load config from %s" % args.config)
        return 1

    report = generate_report(config, args.state, args.format)
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())

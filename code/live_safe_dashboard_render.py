#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        if float(value).is_integer():
            return f"{float(value):.0f}"
        return f"{float(value):.2f}"
    return str(value)


def render_markdown(payload: dict[str, Any]) -> str:
    counts = payload["metrics"]["counts"]
    perf = payload["metrics"]["performance"]
    risk = payload["metrics"]["risk"]
    decisions = payload["metrics"]["decisions"]
    allocator = payload["allocator"]
    lines: list[str] = []
    lines.append("# Live Safe Dashboard Scorecard")
    lines.append("")
    lines.append("## KPI")
    lines.append(f"- assets: {_fmt(counts['assets'])}")
    lines.append(f"- strategies: {_fmt(counts['strategies'])}")
    lines.append(f"- trades: {_fmt(counts['trades'])}")
    lines.append(f"- win/loss: {_fmt(counts['wins'])}/{_fmt(counts['losses'])}")
    lines.append(f"- PnL/PF/DD: {_fmt(perf['PnL'])}/{_fmt(perf['PF'])}/{_fmt(perf['DD'])}")
    lines.append(f"- risk: {_fmt(risk['overall'])}")
    lines.append(f"- PnL↑/risk↓: {_fmt(allocator['direction']['pnl_up'])}/{_fmt(allocator['direction']['risk_down'])}")
    lines.append("")
    lines.append("## Decisions keep/drop/retest")
    lines.append(f"- keep: {_fmt(decisions['keep'])}")
    lines.append(f"- drop: {_fmt(decisions['drop'])}")
    lines.append(f"- retest: {_fmt(decisions['retest'])}")
    lines.append(f"- allocator_score: {_fmt(allocator['composite_score'])}")
    lines.append("")
    lines.append("## Guardrails")
    lines.append(f"- live_orders_allowed: {_fmt(payload['pipeline']['live_orders_allowed'])}")
    lines.append(f"- max_live_slots: {_fmt(payload['pipeline']['max_live_slots'])}")
    lines.append(f"- max_contracts_per_entry: {_fmt(payload['pipeline']['max_contracts_per_entry'])}")
    lines.append(f"- RI excluded: {_fmt(payload['pipeline']['ri_excluded'])}")
    lines.append("")
    lines.append("## Strategy decisions")
    lines.append("| strategy_id | ticker | strategy | decision | rank | PnL | trades | reason |")
    lines.append("|---|---|---|---|---:|---:|---:|---|")
    for row in decisions["items"]:
        lines.append(
            f"| {row['strategy_id']} | {_fmt(row['ticker'])} | {_fmt(row['strategy'])} | {_fmt(row['decision'])} | {_fmt(row['rank_score'])} | {_fmt(row['pnl'])} | {_fmt(row['trades'])} | {_fmt(row['reason'])} |"
        )
    if not decisions["items"]:
        lines.append("| — | — | — | — | — | — | — | нет кандидатов |")
    lines.append("")
    return "\n".join(lines)


def render_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def write_dashboard_reports(payload: dict[str, Any], report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    md_path = report_dir / "latest.md"
    json_path = report_dir / "latest.json"
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    json_path.write_text(render_json(payload), encoding="utf-8")
    return md_path, json_path

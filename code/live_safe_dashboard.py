#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from live_safe_dashboard_candidates import build_candidate_waitlist, humanize_warning
from live_safe_dashboard_data import DEFAULT_DRY_RUN_DIR, DEFAULT_STATE_DIR, load_dashboard_bundle
from live_safe_dashboard_metrics import build_allocator_scorecard, build_metrics_summary
from live_safe_dashboard_positions import build_positions, position_summary
from live_safe_dashboard_render import render_markdown
from live_safe_dashboard_risk import build_risk_gates
from pipeline_map import MAX_CONTRACTS_PER_ENTRY, MAX_LIVE_SLOTS, EXCLUDED_TICKERS

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "live_dashboard"
REPORT_DIR.mkdir(parents=True, exist_ok=True)


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


def _fmt_price(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def build_dashboard(bundle: dict[str, Any], waitlist_limit: int = 10) -> dict[str, Any]:
    portfolio = bundle["portfolio"]
    broker_positions = bundle["broker_positions"]
    positions = build_positions(portfolio, broker_positions)
    risk = build_risk_gates(portfolio, positions, broker_positions, bundle["systemd_timers"])
    candidate_waitlist = build_candidate_waitlist(bundle["strategy_registry"], bundle.get("waitlist"), limit=waitlist_limit)
    metrics = build_metrics_summary(bundle["strategy_registry"], portfolio)
    allocator = build_allocator_scorecard(metrics, {
        "max_live_slots": MAX_LIVE_SLOTS,
        "max_contracts_per_entry": MAX_CONTRACTS_PER_ENTRY,
    })

    warnings = []
    warnings.extend(risk["warnings"])
    if candidate_waitlist["empty"]:
        warnings.append("В waitlist нет кандидатов — продвижение остановилось на уровне входного потока")
    else:
        warnings.append(f"В waitlist доступно {candidate_waitlist['count']} кандидатов")

    dataset_registry = bundle.get("dataset_registry") or {}
    research_latest = bundle.get("research_latest") or {}
    live_dashboard_latest = bundle.get("live_dashboard_latest") or {}

    dataset_total = len((dataset_registry.get("datasets") or {}))
    dataset_verified = int(dataset_registry.get("verified_count") or 0)
    registry_total = len((bundle["strategy_registry"].get("strategies") or {}))
    registry_candidates = sum(1 for rec in (bundle["strategy_registry"].get("strategies") or {}).values() if str(rec.get("status") or "") in {"registry/candidate", "waitlist"})
    research_status = str(research_latest.get("status") or "UNKNOWN")
    live_ts = live_dashboard_latest.get("ts")

    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "sources": {
            "dry_run_dir": str(bundle["dry_run_dir"]),
            "state_dir": str(bundle["state_dir"]),
        },
        "live_orders": 0,
        "pipeline": {
            "max_live_slots": MAX_LIVE_SLOTS,
            "max_contracts_per_entry": MAX_CONTRACTS_PER_ENTRY,
            "ri_excluded": True,
            "excluded_tickers": list(EXCLUDED_TICKERS),
        },
        "contours": {
            "live": {
                "status": "pass" if not portfolio.get("halted") else "block",
                "open_positions": sum(1 for row in positions if row.get("open_position")),
                "last_dashboard_ts": live_ts,
            },
            "registry": {
                "status": "pass" if registry_total >= 0 else "block",
                "strategies": registry_total,
                "candidates": registry_candidates,
                "datasets_total": dataset_total,
                "datasets_verified": dataset_verified,
            },
            "research": {
                "status": research_status,
                "latest_run_id": research_latest.get("pipeline_run_id"),
                "latest_run_dir": research_latest.get("run_dir"),
            },
        },
        "portfolio": {
            "halted": bool(portfolio.get("halted")),
            "halt_reason": portfolio.get("halt_reason"),
            "halt_message": risk["halt_message"],
            "peak_equity": portfolio.get("peak_equity"),
            "deposit_rub": portfolio.get("deposit_rub"),
            "slots": len(portfolio.get("slots") or {}),
            "open_positions": sum(1 for row in positions if row.get("open_position")),
        },
        "positions": positions,
        "position_summary": position_summary(positions),
        "risk_gates": risk["gates"],
        "risk_overall": risk["overall"],
        "slot_reasons": risk["slot_reasons"],
        "candidate_waitlist": candidate_waitlist,
        "metrics": metrics,
        "allocator": allocator,
        "warnings": [humanize_warning(item) for item in warnings],
        "broker_positions": broker_positions,
        "analytics_open_trades": bundle.get("analytics_open_trades", []),
        "systemd_timers": bundle.get("systemd_timers", {}),
    }
    return payload


def render_markdown(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Live Safe Dashboard")
    lines.append("")
    lines.append(f"- ts: {payload['ts']}")
    lines.append(f"- live_orders: {payload.get('live_orders', 0)}")
    lines.append(f"- portfolio: {_fmt(payload['portfolio']['halt_message'])}")
    lines.append(f"- positions/open: {payload['position_summary']['total_positions']}/{payload['position_summary']['open_positions']}")
    lines.append(f"- candidate_waitlist: {_fmt(payload['candidate_waitlist']['count'])}")
    lines.append(f"- risk_overall: {_fmt(payload['risk_overall'])}")
    lines.append(f"- assets: {_fmt(payload['metrics']['counts']['assets'])}")
    lines.append(f"- strategies: {_fmt(payload['metrics']['counts']['strategies'])}")
    lines.append(f"- trades: {_fmt(payload['metrics']['counts']['trades'])}")
    lines.append(f"- win/loss: {_fmt(payload['metrics']['counts']['wins'])}/{_fmt(payload['metrics']['counts']['losses'])}")
    lines.append(f"- PnL/PF/DD: {_fmt(payload['metrics']['performance']['PnL'])}/{_fmt(payload['metrics']['performance']['PF'])}/{_fmt(payload['metrics']['performance']['DD'])}")
    lines.append(f"- risk_score: {_fmt(payload['allocator']['composite_score'])}")
    lines.append(f"- decisions keep/drop/retest: {_fmt(payload['metrics']['decisions']['keep'])}/{_fmt(payload['metrics']['decisions']['drop'])}/{_fmt(payload['metrics']['decisions']['retest'])}")
    lines.append("")

    lines.extend(["## Positions", "| slot | ticker | strategy | direction | qty | entry | current | SL | TP | PnL RUB |", "|---|---|---|---|---:|---:|---:|---:|---:|---:|"])
    for row in payload["positions"]:
        lines.append(
            f"| {row['slot_id']} | {row['ticker']} | {row['strategy']} | {_fmt(row['direction'])} | {_fmt(row['qty'])} | {_fmt_price(row['entry_price'])} | {_fmt_price(row['current_price'])} | {_fmt_price(row['sl_price'])} | {_fmt_price(row['tp_price'])} | {_fmt(row['pnl_rub'])} |"
        )
    lines.append("")

    lines.extend(["## Risk gates"])
    for gate in payload["risk_gates"]:
        lines.append(f"- [{gate['status']}] {gate['message']}")
        details = gate.get("details")
        if details:
            lines.append(f"  - details: {json.dumps(details, ensure_ascii=False)}")
    lines.append("")

    lines.extend(["## Slot reasons"])
    for slot_id, reasons in payload["slot_reasons"].items():
        if not reasons:
            continue
        lines.append(f"- {slot_id}")
        for reason in reasons:
            lines.append(f"  - {reason}")
    if all(not reasons for reasons in payload["slot_reasons"].values()):
        lines.append("- нет блокирующих причин")
    lines.append("")

    lines.extend(["## Candidate waitlist", "| strategy_id | ticker | strategy | status | rank | age | reason |", "|---|---|---|---|---:|---|---|"])
    for row in payload["candidate_waitlist"]["items"]:
        lines.append(
            f"| {row['strategy_id']} | {_fmt(row['ticker'])} | {_fmt(row['strategy'])} | {_fmt(row['status'])} | {_fmt(row['rank_score'])} | {_fmt(row['retested'])} | {row['reason']} |"
        )
    if not payload["candidate_waitlist"]["items"]:
        lines.append("| — | — | — | — | — | — | кандидатов нет |")
    lines.append("")

    lines.append("## Warnings")
    if payload["warnings"]:
        for warning in payload["warnings"]:
            lines.append(f"- {warning}")
    else:
        lines.append("- warnings отсутствуют")
    lines.append("")

    return "\n".join(lines)


def write_dashboard_artifacts(payload: dict[str, Any], report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "latest.json"
    md_path = report_dir / "latest.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only live safe dashboard for strategy_combine")
    parser.add_argument("--dry-run-dir", type=Path, default=DEFAULT_DRY_RUN_DIR, help="Fixture directory with local dry-run JSON files")
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR, help="Local state directory for registry/waitlist snapshots")
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR, help="Output directory for latest.json/latest.md")
    parser.add_argument("--waitlist-limit", type=int, default=10, help="Max candidate waitlist rows in dashboard")
    args = parser.parse_args()

    bundle = load_dashboard_bundle(args.dry_run_dir, args.state_dir)
    payload = build_dashboard(bundle, waitlist_limit=args.waitlist_limit)
    json_path, md_path = write_dashboard_artifacts(payload, args.report_dir.resolve())
    print(render_markdown(payload))
    print(f"\n[dashboard written] {json_path}")
    print(f"[dashboard written] {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

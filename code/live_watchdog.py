#!/usr/bin/env python3
"""Dry-run watchdog for strategy_combine.

The watchdog is intentionally fixture-driven: it reads local JSON snapshots for
broker positions, portfolio slots, analytics open trades, and systemd timers.
That keeps the check read-only and avoids any broker mutations or live orders.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_DIR = ROOT / "tests" / "fixtures" / "dry-run" / "watchdog" / "pass"
REPORT = ROOT / "reports" / "live_watchdog"
REPORT.mkdir(parents=True, exist_ok=True)

FIXTURE_FILENAMES = {
    "portfolio": "portfolio.json",
    "broker_positions": "broker_positions.json",
    "analytics_open_trades": "analytics_open_trades.json",
    "systemd_timers": "systemd_timers.json",
}


def read_json_file(path: pathlib.Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_price(price: float | int | None, reference_price: float | int | None) -> float | None:
    """Normalize broker prices that arrive in a 100x larger scale.

    Mirrors the production engine heuristic: if the quote looks like a cents/kopeks
    value while the reference is in rubles, repeatedly divide/multiply by 100 until
    the scales are comparable.
    """
    if price is None:
        return None
    try:
        normalized = float(price)
        reference = float(reference_price) if reference_price is not None else 0.0
    except (TypeError, ValueError):
        return None
    if normalized <= 0 or reference <= 0:
        return normalized
    while normalized > reference * 20.0:
        normalized /= 100.0
    while normalized < reference / 20.0:
        normalized *= 100.0
    return normalized


def load_fixture_bundle(base_dir: pathlib.Path) -> dict[str, Any]:
    """Load a watchdog fixture bundle from a directory.

    Missing files are treated as empty snapshots so tests can override only the
    pieces they care about.
    """
    base_dir = base_dir.resolve()
    return {
        "base_dir": base_dir,
        "portfolio": read_json_file(base_dir / FIXTURE_FILENAMES["portfolio"], {"slots": {}}),
        "broker_positions": read_json_file(base_dir / FIXTURE_FILENAMES["broker_positions"], {}),
        "analytics_open_trades": read_json_file(base_dir / FIXTURE_FILENAMES["analytics_open_trades"], []),
        "systemd_timers": read_json_file(base_dir / FIXTURE_FILENAMES["systemd_timers"], {}),
    }


def summarize_slot(slot_id: str, slot: dict[str, Any]) -> dict[str, Any]:
    pos = slot.get("open_position") or None
    return {
        "slot_id": slot_id,
        "ticker": slot.get("ticker"),
        "strategy": slot.get("strategy"),
        "direction": None if not pos else pos.get("direction"),
        "qty": None if not pos else pos.get("qty"),
        "entry_price": None if not pos else pos.get("entry_price"),
        "sl_px": slot.get("sl_px"),
        "tp_px": slot.get("tp_px"),
        "go_rub": slot.get("go_rub"),
        "open_position": bool(pos),
        "n_trades": slot.get("n_trades", 0),
        "pnl_rub": slot.get("pnl_rub", 0.0),
    }


def _timer_state(timer_value: Any) -> dict[str, Any]:
    if isinstance(timer_value, dict):
        active = timer_value.get("active", timer_value.get("status", "unknown"))
        enabled = timer_value.get("enabled")
        last_trigger = timer_value.get("last_trigger")
    else:
        active = timer_value
        enabled = None
        last_trigger = None
    is_active = str(active).lower() == "active" or active is True
    return {
        "active": active,
        "ok": is_active,
        "enabled": enabled,
        "last_trigger": last_trigger,
    }


def audit_watchdog(bundle: dict[str, Any], report_dir: pathlib.Path = REPORT) -> dict[str, Any]:
    portfolio = bundle.get("portfolio") or {"slots": {}}
    broker_positions = bundle.get("broker_positions") or {}
    analytics_open_trades = bundle.get("analytics_open_trades") or []
    timers_raw = bundle.get("systemd_timers") or {}

    issues: list[str] = []
    warnings: list[str] = []

    slots = portfolio.get("slots") or {}
    slot_summaries: dict[str, dict[str, Any]] = {}
    open_slots_by_ticker: dict[str, list[str]] = defaultdict(list)
    slot_ids = set(slots.keys())

    halted = bool(portfolio.get("halted"))
    halt_reason = portfolio.get("halt_reason")
    peak_equity = portfolio.get("peak_equity")
    deposit_rub = float(portfolio.get("deposit_rub") or 0.0)

    # Portfolio slot sanity.
    for slot_id, slot in slots.items():
        summary = summarize_slot(slot_id, slot)
        slot_summaries[slot_id] = summary
        ticker = summary["ticker"]
        if ticker and summary["open_position"]:
            open_slots_by_ticker[ticker].append(slot_id)
        if float(slot.get("go_rub") or 0.0) <= 0:
            issues.append(f"{slot_id}:go_rub_non_positive")
        if summary["open_position"]:
            if summary["entry_price"] is None:
                issues.append(f"{slot_id}:missing_entry_price")
            entry_price = float(summary["entry_price"] or 0.0)
            sl_px = slot.get("sl_px")
            tp_px = slot.get("tp_px")
            direction = summary["direction"]
            if direction == "LONG" and not (float(sl_px or 0.0) < entry_price < float(tp_px or math.inf)):
                issues.append(f"{slot_id}:long_sltp_invalid")
            if direction == "SHORT" and not (float(sl_px or math.inf) > entry_price > float(tp_px or 0.0)):
                issues.append(f"{slot_id}:short_sltp_invalid")

            # Scale sanity: compare against broker and normalized broker prices.
            broker = broker_positions.get(ticker)
            if broker:
                broker_avg = broker.get("avg_price")
                normalized = normalize_price(entry_price, broker_avg)
                if broker_avg is not None and normalized is not None:
                    broker_ref = float(broker_avg)
                    rel_err = abs(float(normalized) - broker_ref) / max(abs(broker_ref), 1.0)
                    raw_rel_err = abs(float(entry_price) - broker_ref) / max(abs(broker_ref), 1.0)
                    if raw_rel_err > 20.0:
                        issues.append(f"{slot_id}:scale_price_mismatch:{entry_price}->{normalized} vs broker={broker_avg}")
                        warnings.append(f"{slot_id}:normalized_price={normalized}")
                    elif rel_err > 0.02:
                        issues.append(f"{slot_id}:price_mismatch:{entry_price}->{normalized} vs broker={broker_avg}")
                    elif abs(float(normalized) - float(entry_price)) > 1e-9:
                        warnings.append(f"{slot_id}:normalized_price={normalized}")
        else:
            stale_keys = [k for k in ("sl_px", "tp_px", "entry_price", "trade_id", "entry_ts", "entry_bar") if slot.get(k) is not None]
            if stale_keys:
                warnings.append(f"{slot_id}:stale_empty_slot:{','.join(stale_keys)}")
                if slot.get("n_trades", 0) == 0 and float(slot.get("pnl_rub") or 0.0) == 0.0:
                    warnings.append(f"{slot_id}:stale_empty_slot_empty_trade_history")

    duplicate_tickers = {ticker: ids for ticker, ids in open_slots_by_ticker.items() if len(ids) > 1}
    if duplicate_tickers:
        issues.append("duplicate_ticker_slots:" + ";".join(f"{ticker}={','.join(ids)}" for ticker, ids in sorted(duplicate_tickers.items())))

    # Analytics open trades vs portfolio slots.
    open_trades = [trade for trade in analytics_open_trades if str(trade.get("status", "open")) == "open"]
    orphan_trades = [trade for trade in open_trades if trade.get("slot_id") not in slot_ids]
    if orphan_trades:
        issues.append(f"orphan_open_trades:{len(orphan_trades)}")

    slot_trade_ids = defaultdict(list)
    for trade in open_trades:
        slot_trade_ids[str(trade.get("slot_id"))].append(trade)
    multi_open_trades = [slot_id for slot_id, trades in slot_trade_ids.items() if slot_id != "None" and len(trades) > 1]
    if multi_open_trades:
        issues.append("duplicate_open_trades:" + ",".join(sorted(multi_open_trades)))

    trade_ticker_map = {str(trade.get("ticker")): trade for trade in open_trades if trade.get("ticker")}
    for ticker, slot_ids_for_ticker in open_slots_by_ticker.items():
        if ticker in broker_positions and ticker not in trade_ticker_map:
            issues.append(f"analytics_missing_trade:{ticker}")
        if ticker not in broker_positions and ticker in trade_ticker_map:
            issues.append(f"analytics_trade_without_broker:{ticker}")
        if ticker in broker_positions and ticker in trade_ticker_map:
            broker = broker_positions[ticker]
            trade = trade_ticker_map[ticker]
            broker_qty = int(broker.get("qty") or 0)
            trade_qty = int(trade.get("qty") or trade.get("contracts") or 0)
            if abs(broker_qty) != abs(trade_qty):
                issues.append(f"qty_mismatch:{ticker}:broker={broker_qty}:trade={trade_qty}")
            if str(broker.get("direction")) != str(trade.get("direction")):
                issues.append(f"direction_mismatch:{ticker}:broker={broker.get('direction')}:trade={trade.get('direction')}")
            broker_avg = broker.get("avg_price")
            trade_px = trade.get("entry_price")
            normalized = normalize_price(trade_px, broker_avg)
            if broker_avg is not None and trade_px is not None and normalized is not None:
                broker_ref = float(broker_avg)
                rel_err = abs(float(normalized) - broker_ref) / max(abs(broker_ref), 1.0)
                if rel_err > 0.02:
                    issues.append(f"trade_price_mismatch:{ticker}:{trade_px}->{normalized} vs broker={broker_avg}")

    # Broker-only positions are suspicious because portfolio should own every open position.
    portfolio_tickers = set(open_slots_by_ticker)
    broker_tickers = set(broker_positions)
    for ticker in sorted(broker_tickers - portfolio_tickers):
        issues.append(f"broker_position_without_portfolio_slot:{ticker}")
    for ticker in sorted(portfolio_tickers - broker_tickers):
        issues.append(f"portfolio_slot_without_broker_position:{ticker}")

    # Timer health.
    timers = {unit: _timer_state(value) for unit, value in timers_raw.items()}
    inactive_timers = [unit for unit, payload in timers.items() if not payload["ok"]]
    for unit in inactive_timers:
        issues.append(f"timer_inactive:{unit}:{timers[unit]['active']}")

    # Halt / peak equity sanity.
    if halted and not halt_reason:
        issues.append("halted_without_reason")
    if not halted and halt_reason:
        issues.append("halt_reason_without_halt")
    try:
        peak_value = float(peak_equity)
    except (TypeError, ValueError):
        peak_value = -1.0
    if peak_value <= 0:
        issues.append("peak_equity_non_positive")
    elif deposit_rub > 0 and peak_value < deposit_rub * 0.95:
        warnings.append(f"peak_equity_below_deposit:{peak_value}<{deposit_rub}")

    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "ok": not issues,
        "issues": issues,
        "warnings": warnings,
        "live_orders": 0,
        "sources": {
            "mode": "dry-run",
            "base_dir": str(bundle.get("base_dir") or ""),
        },
        "broker_positions": broker_positions,
        "portfolio": {
            "halted": halted,
            "halt_reason": halt_reason,
            "peak_equity": peak_equity,
            "deposit_rub": deposit_rub,
            "slots": len(slots),
            "open_slots": sum(1 for slot in slots.values() if slot.get("open_position")),
        },
        "portfolio_slots": slot_summaries,
        "analytics_open_trades": open_trades,
        "systemd_timers": timers,
        "checks": {
            "duplicate_ticker_slots": duplicate_tickers,
            "stale_empty_slots": [slot_id for slot_id, slot in slots.items() if not slot.get("open_position") and any(slot.get(k) is not None for k in ("sl_px", "tp_px", "entry_price", "trade_id", "entry_ts", "entry_bar"))],
            "orphan_open_trades": orphan_trades,
            "inactive_timers": inactive_timers,
        },
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (report_dir / "latest.md").write_text(render_markdown(payload), encoding="utf-8")
    return payload


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Live Watchdog",
        "",
        f"- ok: {payload['ok']}",
        f"- live_orders: {payload.get('live_orders', 0)}",
        f"- issues: {len(payload['issues'])}",
        f"- warnings: {len(payload['warnings'])}",
        f"- base_dir: {payload.get('sources', {}).get('base_dir', '')}",
        "",
    ]
    if payload["issues"]:
        lines += ["## Issues"] + [f"- {item}" for item in payload["issues"]] + [""]
    if payload["warnings"]:
        lines += ["## Warnings"] + [f"- {item}" for item in payload["warnings"]] + [""]
    lines += ["## Portfolio", "```json", json.dumps(payload["portfolio"], ensure_ascii=False, indent=2, default=str), "```", ""]
    lines += ["## Broker positions", "```json", json.dumps(payload["broker_positions"], ensure_ascii=False, indent=2, default=str), "```", ""]
    lines += ["## Analytics open trades", "```json", json.dumps(payload["analytics_open_trades"], ensure_ascii=False, indent=2, default=str), "```", ""]
    lines += ["## Systemd timers", "```json", json.dumps(payload["systemd_timers"], ensure_ascii=False, indent=2, default=str), "```", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run live safety watchdog for strategy_combine")
    parser.add_argument(
        "--dry-run-dir",
        type=pathlib.Path,
        default=DEFAULT_FIXTURE_DIR,
        help="Directory with watchdog JSON fixtures (default: tests/fixtures/dry-run/watchdog/pass)",
    )
    parser.add_argument(
        "--report-dir",
        type=pathlib.Path,
        default=REPORT,
        help="Directory where latest.json/latest.md are written",
    )
    args = parser.parse_args()

    bundle = load_fixture_bundle(args.dry_run_dir)
    payload = audit_watchdog(bundle, args.report_dir.resolve())
    print(json.dumps({"ok": payload["ok"], "issues": payload["issues"], "warnings": payload["warnings"], "live_orders": 0}, ensure_ascii=False))
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

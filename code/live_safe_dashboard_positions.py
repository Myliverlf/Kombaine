#!/usr/bin/env python3
from __future__ import annotations

from typing import Any

POINT_VALUE_BY_TICKER = {
    "GAZP": 100.0,
    "LKOH": 1.0,
    "SBER": 1.0,
    "BR": 1.0,
    "Si": 1.0,
    "IMOEX": 1.0,
    "RI": 1.0,
}


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _fmt_number(value: Any) -> str:
    if value is None:
        return "—"
    try:
        if float(value).is_integer():
            return f"{float(value):.0f}"
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def resolve_point_value(ticker: str, broker_position: dict[str, Any] | None = None) -> float:
    if broker_position and broker_position.get("point_value") is not None:
        try:
            return float(broker_position["point_value"])
        except (TypeError, ValueError):
            broker_position = None
    return float(POINT_VALUE_BY_TICKER.get(ticker, 1.0))


def normalize_price(price: float | int | None, reference_price: float | int | None) -> float | None:
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


def build_positions(portfolio: dict[str, Any], broker_positions: dict[str, Any]) -> list[dict[str, Any]]:
    slots = portfolio.get("slots") or {}
    rows: list[dict[str, Any]] = []
    for slot_id, slot in slots.items():
        open_position = slot.get("open_position") or None
        ticker = slot.get("ticker")
        broker = broker_positions.get(ticker) or {}
        direction = None if not open_position else open_position.get("direction")
        qty = None if not open_position else _int(open_position.get("qty"))
        entry_price = None if not open_position else _float(open_position.get("entry_price"))
        current_price = _float(broker.get("last_price"))
        if current_price is None:
            current_price = _float(broker.get("avg_price"))
        if current_price is None and entry_price is not None:
            current_price = entry_price
        current_price = normalize_price(current_price, entry_price or _float(broker.get("avg_price")) or current_price)
        sl_price = _float(slot.get("sl_px"))
        tp_price = _float(slot.get("tp_px"))
        point_value = resolve_point_value(ticker, broker)
        pnl_rub = None
        pnl_pct = None
        if open_position and entry_price is not None and current_price is not None:
            signed_qty = qty or 0
            if (direction or "").upper() == "SHORT":
                signed_qty *= -1
            pnl_rub = round(signed_qty * (current_price - entry_price) * point_value, 2)
            if entry_price:
                pnl_pct = round((current_price - entry_price) / entry_price * 100.0 * ((-1) if (direction or "").upper() == "SHORT" else 1), 4)
        row = {
            "slot_id": slot_id,
            "ticker": ticker,
            "strategy": slot.get("strategy"),
            "direction": direction,
            "qty": qty,
            "entry_price": entry_price,
            "current_price": current_price,
            "sl_price": sl_price,
            "tp_price": tp_price,
            "pnl_rub": pnl_rub,
            "pnl_pct": pnl_pct,
            "point_value": point_value,
            "go_rub": _float(slot.get("go_rub")),
            "open_position": bool(open_position),
            "trade_id": None if not open_position else open_position.get("trade_id"),
            "status": "open" if open_position else "empty",
        }
        rows.append(row)
    return rows


def position_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    open_positions = [row for row in rows if row.get("open_position")]
    pnl_values = [float(row["pnl_rub"]) for row in open_positions if row.get("pnl_rub") is not None]
    return {
        "total_positions": len(rows),
        "open_positions": len(open_positions),
        "closed_positions": len(rows) - len(open_positions),
        "open_tickers": sorted({str(row.get("ticker")) for row in open_positions if row.get("ticker")}),
        "total_pnl_rub": round(sum(pnl_values), 2),
    }

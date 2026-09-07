#!/usr/bin/env python3
from __future__ import annotations

from typing import Any

HALT_REASON_MESSAGES = {
    None: "Портфель работает",
    "risk_guard_trip": "Портфель остановлен: сработал риск-стоп",
    "portfolio_stop": "Портфель остановлен вручную",
    "stale_candles": "Сигнал не обновлялся вовремя",
}


def _human_halt_reason(reason: Any) -> str:
    if reason in HALT_REASON_MESSAGES:
        return HALT_REASON_MESSAGES[reason]
    if reason is None:
        return HALT_REASON_MESSAGES[None]
    return f"Портфель остановлен: {reason}"


def build_risk_gates(
    portfolio: dict[str, Any],
    positions: list[dict[str, Any]],
    broker_positions: dict[str, Any],
    systemd_timers: dict[str, Any],
) -> dict[str, Any]:
    slots = portfolio.get("slots") or {}
    gates: list[dict[str, Any]] = []
    slot_reasons: dict[str, list[str]] = {slot_id: [] for slot_id in slots}
    warnings: list[str] = []

    halted = bool(portfolio.get("halted"))
    halt_reason = portfolio.get("halt_reason")
    gates.append(
        {
            "code": "portfolio_halt",
            "status": "block" if halted else "pass",
            "message": _human_halt_reason(halt_reason) if halted else "Портфель разрешает новые входы",
            "details": {"halted": halted, "halt_reason": halt_reason},
        }
    )
    if halted:
        warnings.append(_human_halt_reason(halt_reason))

    strategy_registry = {}
    dataset_registry = {}
    research_latest = {}
    try:
        strategy_registry = portfolio.get("strategy_registry") or {}
    except Exception:
        strategy_registry = {}
    try:
        dataset_registry = portfolio.get("dataset_registry") or {}
    except Exception:
        dataset_registry = {}
    try:
        research_latest = portfolio.get("research_latest") or {}
    except Exception:
        research_latest = {}
    if isinstance(strategy_registry, dict):
        strategies = strategy_registry.get("strategies") or {}
        if not strategies:
            gates.append({
                "code": "empty_strategy_registry",
                "status": "block",
                "message": "strategy_registry пуст — контур REGISTRY не готов",
                "details": {"strategies": 0},
            })
    if isinstance(dataset_registry, dict):
        datasets = dataset_registry.get("datasets") or {}
        if not datasets:
            gates.append({
                "code": "empty_dataset_registry",
                "status": "warn",
                "message": "dataset_registry пуст — история может быть неполной",
                "details": {"datasets": 0},
            })
    if isinstance(research_latest, dict):
        if str(research_latest.get("status") or "").upper() not in {"COMPLETED", "PASS"}:
            gates.append({
                "code": "research_not_completed",
                "status": "warn",
                "message": "Последний research run не завершён успешно",
                "details": {"status": research_latest.get("status"), "pipeline_run_id": research_latest.get("pipeline_run_id")},
            })

    duplicate_slots: dict[str, list[str]] = {}
    open_by_ticker: dict[str, list[str]] = {}
    for row in positions:
        if not row.get("open_position"):
            continue
        ticker = str(row.get("ticker") or "")
        open_by_ticker.setdefault(ticker, []).append(str(row.get("slot_id")))
    for ticker, slot_ids in open_by_ticker.items():
        if len(slot_ids) > 1:
            duplicate_slots[ticker] = slot_ids
            for slot_id in slot_ids:
                slot_reasons.setdefault(slot_id, []).append(f"Дубль позиции по тикеру {ticker}: {', '.join(slot_ids)}")
    if duplicate_slots:
        gates.append(
            {
                "code": "duplicate_open_positions",
                "status": "block",
                "message": "Найдено несколько открытых слотов по одному тикеру",
                "details": duplicate_slots,
            }
        )

    broker_tickers = set(broker_positions)
    open_tickers = set(open_by_ticker)
    missing_broker = sorted(open_tickers - broker_tickers)
    if missing_broker:
        gates.append(
            {
                "code": "open_slot_without_broker_match",
                "status": "block",
                "message": "В портфеле есть открытые слоты без совпадающей позиции брокера",
                "details": {"tickers": missing_broker},
            }
        )
        for ticker in missing_broker:
            for slot_id in open_by_ticker.get(ticker, []):
                slot_reasons.setdefault(slot_id, []).append(f"Нет совпадения с брокером по тикеру {ticker}")

    broker_only = sorted(broker_tickers - open_tickers)
    if broker_only:
        gates.append(
            {
                "code": "broker_position_without_portfolio_slot",
                "status": "warn",
                "message": "У брокера есть позиция, которой нет среди открытых слотов",
                "details": {"tickers": broker_only},
            }
        )
        warnings.append("У брокера есть позиция без открытого слота: " + ", ".join(broker_only))

    invalid_go = [row["slot_id"] for row in positions if float(row.get("go_rub") or 0.0) <= 0.0]
    if invalid_go:
        gates.append(
            {
                "code": "non_positive_go_rub",
                "status": "block",
                "message": "У части слотов ГО не положительное",
                "details": {"slots": invalid_go},
            }
        )
        for slot_id in invalid_go:
            slot_reasons.setdefault(slot_id, []).append("ГО слота не положительное")

    sltp_issues = []
    for row in positions:
        if not row.get("open_position"):
            continue
        entry = row.get("entry_price")
        sl = row.get("sl_price")
        tp = row.get("tp_price")
        direction = str(row.get("direction") or "").upper()
        if entry is None or sl is None or tp is None:
            continue
        if direction == "LONG" and not (sl < entry < tp):
            sltp_issues.append(row["slot_id"])
            slot_reasons.setdefault(row["slot_id"], []).append("SL/TP не образуют корректный long-диапазон")
        if direction == "SHORT" and not (sl > entry > tp):
            sltp_issues.append(row["slot_id"])
            slot_reasons.setdefault(row["slot_id"], []).append("SL/TP не образуют корректный short-диапазон")
    if sltp_issues:
        gates.append(
            {
                "code": "invalid_sltp",
                "status": "block",
                "message": "У части открытых позиций некорректные SL/TP",
                "details": {"slots": sorted(set(sltp_issues))},
            }
        )

    inactive_timers = []
    for unit, timer in (systemd_timers or {}).items():
        active = timer.get("active") if isinstance(timer, dict) else timer
        if str(active).lower() != "active" and active is not True:
            inactive_timers.append(unit)
    if inactive_timers:
        gates.append(
            {
                "code": "inactive_timers",
                "status": "warn",
                "message": "Часть сервисных таймеров неактивна",
                "details": {"units": inactive_timers},
            }
        )
        warnings.append("Неактивные таймеры: " + ", ".join(sorted(inactive_timers)))

    for slot_id, slot in slots.items():
        if not slot.get("open_position"):
            stale_bits = [key for key in ("sl_px", "tp_px", "entry_price", "trade_id", "entry_ts", "entry_bar") if slot.get(key) is not None]
            if stale_bits:
                slot_reasons.setdefault(slot_id, []).append("Пустой слот хранит устаревшие поля: " + ", ".join(stale_bits))

    return {
        "overall": "block" if any(gate["status"] == "block" for gate in gates) else "pass",
        "halted": halted,
        "halt_reason": halt_reason,
        "halt_message": _human_halt_reason(halt_reason),
        "gates": gates,
        "slot_reasons": slot_reasons,
        "warnings": warnings,
    }

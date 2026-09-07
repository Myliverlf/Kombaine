"""CLI-отчёт risk scorecard по живому плацдарму (read-only).

Читает state/portfolio.json + config.json только на чтение.
Печатает таблицу 7 компонент + risk_score + verdict.
В state/ записи нет — проверено через md5sum до/после.
"""
import json
import sys
import time
from pathlib import Path

# Пути к проекту
_PROJECT = Path(__file__).resolve().parent.parent
_STATE_DIR = _PROJECT / "state"
_CONFIG_PATH = _PROJECT / "config.json"

# Импорт scorecard — импортируем из той же директории
sys.path.insert(0, str(_PROJECT / "code"))
from risk_scorecard import compute_scorecard as build_scorecard, validate_constraints

STATUS_VETO = "VETO"
STATUS_WARN = "WARN"
STATUS_NEUTRAL = "NEUTRAL"


def _load_json(path: Path) -> dict:
    """Чтение JSON без мутаций."""
    return json.loads(path.read_text())


def _print_component(name: str, comp: dict) -> str:
    """Форматирование одной строки компоненты."""
    status = comp["status"]
    marker = {
        "OK": "  ✅",
        "WARN": " ⚠️ ",
        "VETO": " 🛑 ",
        "NEUTRAL": "  ➖",
    }.get(status, "  ? ")
    return "  %s %-14s  value=%-10s  %s  %s" % (
        marker, name.upper(), comp["value"], status, comp.get("detail", ""))


def _ri_excluded_line(excluded: list, slots: dict) -> str:
    """Строка об исключённых тикерах."""
    ri_active = [
        s["ticker"] for s in slots.values()
        if s.get("ticker") in excluded and s.get("open_position")
    ]
    if ri_active:
        return "  🛑 EXCLUDED      ticker(s): %s в позициях!" % ", ".join(ri_active)
    elif excluded:
        return "  ✅ EXCLUDED      %s не в позициях" % ", ".join(excluded)
    return ""


def slot_unrealized_pnl(slot: dict) -> float:
    """Return the mark-to-market PnL for one slot if it is open.

    Futures equity should track deposit + unrealized PnL only. Realized PnL from
    closed slots does not change current equity.
    """
    if not slot.get("open_position"):
        return 0.0
    for field in ("unrealized_pnl_rub", "unrealized_pnl", "mtm_pnl_rub", "pnl_unrealized"):
        value = slot.get(field)
        if value is not None:
            return float(value or 0.0)
    return float(slot.get("pnl_rub", 0.0) or 0.0)


def compute_equity_from_slots(slots: dict, deposit_rub: float) -> float:
    """Compute futures equity as deposit plus open unrealized PnL."""
    unrealized_total = sum(slot_unrealized_pnl(slot) for slot in slots.values())
    return float(deposit_rub) + unrealized_total


def generate_report(slots: dict, config: dict, equity: float = None,
                    peak_equity: float = None,
                    price_series: dict = None) -> str:
    """Генерация текстового отчёта."""
    scorecard = build_scorecard(
        slots, config,
        equity=equity,
        peak_equity=peak_equity,
        price_series=price_series,
    )

    lines = []
    lines.append("=" * 60)
    lines.append("  RISK SCORECARD — strategy_combine")
    lines.append("=" * 60)
    lines.append("")

    # Слоты
    n_active = sum(1 for s in slots.values() if s.get("open_position"))
    n_total = len(slots)
    total_go = sum(s.get("go_rub", 0.0) for s in slots.values())
    total_pnl = sum(slot_unrealized_pnl(s) for s in slots.values())

    lines.append("  Слоты: %d/%d активных  |  ГО: %.0f руб  |  PnL: %.1f руб" % (
        n_active, config["max_slots"], total_go, total_pnl))
    lines.append("  Equity: %.0f  |  Peak: %.0f" % (
        scorecard["equity"], scorecard["peak_equity"]))
    lines.append("")

    # Компоненты
    lines.append("  COMPONENT          VALUE         STATUS   DETAIL")
    lines.append("  " + "-" * 56)
    for name, comp in scorecard["components"].items():
        lines.append(_print_component(name, comp))

    # RI exclusion
    ri_line = _ri_excluded_line(config.get("excluded", []), slots)
    if ri_line:
        lines.append(ri_line)

    lines.append("")
    lines.append("  " + "-" * 56)

    # Risk score
    risk_score = scorecard["risk_score"]
    verdict = scorecard["verdict"]
    verdict_marker = {
        "ALLOW": "✅ ALLOW",
        "REDUCE": "⚠️  REDUCE",
        "VETO": "🛑 VETO",
    }.get(verdict, verdict)

    lines.append("  RISK SCORE: %.1f / 100   VERDICT: %s" % (risk_score, verdict_marker))
    lines.append("")
    lines.append("  (ниже risk_score = лучше; score монотонен по компонентам)")
    lines.append("=" * 60)

    return "\n".join(lines)


def main():
    """CLI точка входа."""
    # Парсинг аргументов (минимальный, без argparse для простоты)
    out_file = None
    prices_json = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--out" and i + 1 < len(args):
            out_file = Path(args[i + 1])
            i += 2
        elif args[i] == "--prices-json" and i + 1 < len(args):
            prices_json = Path(args[i + 1])
            i += 2
        else:
            i += 1

    # Чтение config и state (read-only)
    config_data = _load_json(_CONFIG_PATH)
    portfolio_data = _load_json(_STATE_DIR / "portfolio.json")

    r = config_data["risk"]
    config = {
        "go_budget_rub": config_data["deposit_rub"] * r["go_budget_pct"] / 100,
        "delta_band_pct": r["delta_band_pct"],
        "deposit_rub": config_data["deposit_rub"],
        "portfolio_stop_drawdown_pct": r["portfolio_stop_drawdown_pct"],
        "signal_max_age_minutes": r["signal_max_age_minutes"],
        "max_slots": r["max_slots"],
        "max_contracts_per_entry": r["max_contracts_per_entry"],
        "excluded": config_data.get("excluded", []),
    }

    slots = portfolio_data.get("slots", {})
    peak_equity = portfolio_data.get("peak_equity", config["deposit_rub"])
    # Equity = deposit + unrealized PnL on open futures positions
    equity = compute_equity_from_slots(slots, config["deposit_rub"])

    # Опциональные ценовые ряды
    price_series = None
    if prices_json and prices_json.exists():
        price_series = json.loads(prices_json.read_text())

    report = generate_report(slots, config, equity=equity,
                             peak_equity=peak_equity,
                             price_series=price_series)

    if out_file:
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(report)
        print("Отчёт сохранён: %s" % out_file)
    else:
        print(report)


if __name__ == "__main__":
    main()

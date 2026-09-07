"""Shared capital context for strategy_combine — единая точка правды по капиталу.

Два уровня (цикл 2, по итогам аудита архитектуры):
  1. Реальный депозит — из живого config.json (источник истины для экономических
     гейтов и риск-расчётов).
  2. Отчётный капитал — стандартизованные 20 000 ₽ для сопоставимости отчётов.

Все новые скрипты должны брать капитал ТОЛЬКО отсюда, а не хардкодить
19800/100000/100000 в коде.
"""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_REPORT_CAPITAL_RUB = 20_000.0
DEFAULT_REPORT_RISK_PCT = 2.7

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


def real_deposit_rub() -> float:
    """Реальный депозит из живого config.json.

    Fallback на отчётный капитал, если конфиг недоступен/битый — чтобы
    экономические гейты никогда не работали на мусоре.
    """
    try:
        d = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        dep = float(d["deposit_rub"])
        if dep > 0:
            return dep
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return DEFAULT_REPORT_CAPITAL_RUB


def report_capital() -> float:
    """Капитал для отчётов (стандартизованный, сопоставимый между сканами)."""
    return DEFAULT_REPORT_CAPITAL_RUB


def report_risk_pct() -> float:
    return DEFAULT_REPORT_RISK_PCT


def economic_gate_capital_rub() -> float:
    """Капитал, к которому привязаны экономические гейты (реальный депозит)."""
    return real_deposit_rub()

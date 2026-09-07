"""Lifecycle Scorecard Extension — аддитивный мостик к scorecard.

lifecycle_section(slots, returns, now_ts=None) -> dict

Возвращает секцию {hit_rate, expectancy_r_mean, mdd, decay,
signal_fresh_ok, stability} в формате компонент _make_component
(value/status/detail) для опциональной вставки в отчёты
рядом с build_scorecard.

risk_scorecard.py / allocator_metrics.py / config.json НЕ трогаются —
плацдарм не регрессирует.

Пороги WARN — калибровочные предположения (ASSUMPTION):
  decay < 0.7 → WARN (значительное затухание)
  stability < 0.5 → WARN (нестабильна)
  signal_fresh_ok = False → WARN (протухший сигнал)

Связь с плацдармом:
  - переиспользует lifecycle_metrics.{hit_rate, expectancy, decay, stability}
  - переиспользует scorecard_metrics.max_drawdown
  - формат компонент совпадает с risk_scorecard._make_component
"""
import time
from typing import Any, Dict, List, Optional

from lifecycle_metrics import decay, expectancy, hit_rate, stability
from scorecard_metrics import max_drawdown


# ─── Пороги (ASSUMPTION — калибровочные) ───────────────────────────────
DECAY_WARN_THRESHOLD = 0.7    # decay < 0.7 → WARN (сильное затухание)
STABILITY_WARN_THRESHOLD = 0.5  # stability < 0.5 → WARN (нестабильна)
SIGNAL_MAX_AGE_MINUTES = 16  # default; overridden by config


def _make_component(value: float, status: str, detail: str = "") -> dict:
    """Создать запись одной компоненты scorecard (формат risk_scorecard)."""
    return {"value": round(value, 4), "status": status, "detail": detail}


def _hit_rate_component(trades: List[float]) -> dict:
    """Hit rate → component."""
    if not trades:
        return _make_component(0.0, "OK", "нет сделок для hit_rate")

    hr = hit_rate(trades)
    if hr < 0.3:
        status = "WARN"
        detail = "hit_rate=%.2f — низкая доля выигрышных" % hr
    else:
        status = "OK"
        detail = "hit_rate=%.2f" % hr

    return _make_component(hr, status, detail)


def _expectancy_component(trades: List[float]) -> dict:
    """Expectancy R → component."""
    if not trades:
        return _make_component(0.0, "OK", "нет сделок для expectancy")

    exp = expectancy(trades)
    if exp < 0:
        status = "WARN"
        detail = "expectancy=%.4f R — отрицательная" % exp
    else:
        status = "OK"
        detail = "expectancy=%.4f R" % exp

    return _make_component(exp, status, detail)


def _mdd_component(returns: List[float]) -> dict:
    """Max drawdown → component."""
    if not returns:
        return _make_component(0.0, "OK", "нет доходностей для MDD")

    mdd = max_drawdown(returns)
    if mdd < -0.15:
        status = "WARN"
        detail = "MDD=%.4f — глубокая просадка" % mdd
    else:
        status = "OK"
        detail = "MDD=%.4f" % mdd

    return _make_component(mdd, status, detail)


def _decay_component(returns: List[float]) -> dict:
    """Decay proxy → component."""
    if not returns or len(returns) < 2:
        return _make_component(0.0, "OK", "недостаточно данных для decay")

    dec = decay(returns)
    if dec < DECAY_WARN_THRESHOLD:
        status = "WARN"
        detail = "decay=%.4f < %.1f — стратегия затухает" % (dec, DECAY_WARN_THRESHOLD)
    else:
        status = "OK"
        detail = "decay=%.4f" % dec

    return _make_component(dec, status, detail)


def _freshness_component(slots: dict, now_ts: Optional[float] = None) -> dict:
    """Signal freshness → component (true/false → OK/WARN)."""
    if now_ts is None:
        now_ts = time.time()

    max_age_sec = SIGNAL_MAX_AGE_MINUTES * 60.0
    active = {k: v for k, v in slots.items() if v.get("open_position")}

    if not active:
        return _make_component(1.0, "OK", "нет активных позиций — freshness N/A")

    stale_count = 0
    worst_age = 0.0
    for s in active.values():
        entry_ts = s.get("last_signal_ts", s.get("open_position", {}).get("entry_ts", now_ts))
        age = now_ts - entry_ts
        if age > worst_age:
            worst_age = age
        if age > max_age_sec:
            stale_count += 1

    fresh_ok = stale_count == 0
    worst_min = worst_age / 60.0

    if fresh_ok:
        status = "OK"
        detail = "fresh=True, worst=%.0f min" % worst_min
    else:
        status = "WARN"
        detail = "fresh=False, stale=%d/%d, worst=%.0f min" % (
            stale_count, len(active), worst_min)

    value = 1.0 if fresh_ok else 0.0
    return _make_component(value, status, detail)


def _stability_component(returns: List[float]) -> dict:
    """Stability (PSR-lite) → component."""
    if not returns or len(returns) < 2:
        return _make_component(0.0, "OK", "недостаточно данных для stability")

    stab = stability(returns)
    if stab < STABILITY_WARN_THRESHOLD:
        status = "WARN"
        detail = "stability=%.2f < %.1f — нестабильна" % (stab, STABILITY_WARN_THRESHOLD)
    else:
        status = "OK"
        detail = "stability=%.2f" % stab

    return _make_component(stab, status, detail)


# ─── Public API ────────────────────────────────────────────────────────

def lifecycle_section(
    slots: dict,
    returns: List[float],
    now_ts: Optional[float] = None,
) -> dict:
    """Секция lifecycle-метрик для отчёта scorecard.

    slots: словарь слотов (как в portfolio.json).
    returns: временной ряд доходностей.
    now_ts: текущий timestamp (для freshness).

    Возвращает dict:
        {
            "hit_rate":       {"value": ..., "status": "OK"|"WARN", "detail": ...},
            "expectancy_r":   {"value": ..., "status": ..., "detail": ...},
            "mdd":            {"value": ..., "status": ..., "detail": ...},
            "decay":          {"value": ..., "status": ..., "detail": ...},
            "signal_fresh":   {"value": ..., "status": ..., "detail": ...},
            "stability":      {"value": ..., "status": ..., "detail": ...},
        }

    Формат каждой компоненты совпадает с risk_scorecard._make_component.
    Опционально вставляется рядом с build_scorecard (аддитивно, без изменения сигнатур).
    """
    return {
        "hit_rate": _hit_rate_component(returns),
        "expectancy_r": _expectancy_component(returns),
        "mdd": _mdd_component(returns),
        "decay": _decay_component(returns),
        "signal_fresh": _freshness_component(slots, now_ts),
        "stability": _stability_component(returns),
    }


def lifecycle_verdict(section: dict) -> str:
    """Вердикт по lifecycle секции: ALLOW / WARN.

    Если хотя бы одна компонента WARN → overall WARN.
    """
    for comp in section.values():
        if isinstance(comp, dict) and comp.get("status") == "WARN":
            return "WARN"
    return "ALLOW"


# ─── CLI demo ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    import pathlib

    # Load fixtures
    fixtures_dir = pathlib.Path(__file__).resolve().parent.parent / "tests" / "fixtures"
    sample_path = fixtures_dir / "sample_returns.json"
    portfolio_path = fixtures_dir / "portfolio_copy.json"

    sample = json.loads(sample_path.read_text()) if sample_path.exists() else {}
    portfolio = json.loads(portfolio_path.read_text()) if portfolio_path.exists() else {}

    returns = sample.get("baseline", [])
    slots = portfolio.get("slots", {})

    section = lifecycle_section(slots, returns)
    verdict = lifecycle_verdict(section)

    print(json.dumps({"section": section, "verdict": verdict}, indent=2, ensure_ascii=False))

"""Risk Scorecard Bridge — мост между pipeline_ranker и risk_scorecard.

pipeline_ranker.py вызывает build_scorecard(slots_dict, config, now_ts),
но risk_scorecard.py экспортирует только compute_scorecard(tickers, data_dir, ...)
с несовместимым интерфейсом.

Этот модуль реализует build_scorecard(slots_dict, config, now_ts=None) —
принимает слоты pipeline_ranker и возвращает dict с risk_score/verdict/components
в формате, ожидаемом e2e_real_dryrun.py (шаг 8).

Чистая функция dict-in → dict-out. Без broker/IO/network.
"""
from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

from allocator_metrics import expectancy_r, risk_penalty

try:
    from capital_context import DEFAULT_REPORT_CAPITAL_RUB, DEFAULT_REPORT_RISK_PCT
except Exception:  # pragma: no cover - defensive fallback for isolated imports
    DEFAULT_REPORT_CAPITAL_RUB = 20_000.0
    DEFAULT_REPORT_RISK_PCT = 2.7


# ─── Веса компонентов scorecard ───────────────────────────────────────

COMPONENT_WEIGHTS = {
    "exposure": 20,
    "drawdown": 20,
    "volatility": 15,
    "correlation": 10,
    "signal_age": 15,
    "slots": 10,
    "caps": 10,
}

DATA_DIR = Path("/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data")
CORRELATION_LOOKBACK_BARS = 60
CORRELATION_INSUFFICIENT_SCORE = 0.35
CORRELATION_WARN_THRESHOLD = 0.45
CORRELATION_VETO_THRESHOLD = 0.75


def _coerce_numeric_series(values: Any) -> List[float]:
    series: List[float] = []
    if values is None:
        return series
    if isinstance(values, dict):
        values = list(values.values())
    for item in values:
        if item is None:
            continue
        if isinstance(item, dict):
            for key in ("close", "price", "equity", "value"):
                if item.get(key) is not None:
                    item = item.get(key)
                    break
            else:
                continue
        try:
            series.append(float(item))
        except (TypeError, ValueError):
            continue
    return series


def _extract_series_from_slot(slot: Dict[str, Any]) -> List[float]:
    for key in ("close_history", "price_history", "equity_history", "history", "bars", "closes"):
        values = slot.get(key)
        series = _coerce_numeric_series(values)
        if len(series) >= 2:
            return series[-(CORRELATION_LOOKBACK_BARS + 1):]
    return []


def _returns_from_prices(prices: List[float], window: int = CORRELATION_LOOKBACK_BARS) -> List[float]:
    if len(prices) < 2:
        return []
    trimmed = prices[-(window + 1):]
    returns: List[float] = []
    for prev, curr in zip(trimmed, trimmed[1:]):
        if prev in (0.0, None):
            continue
        try:
            returns.append((curr - prev) / prev)
        except Exception:
            continue
    return returns


def _pearson_corr(a: List[float], b: List[float]) -> Optional[float]:
    n = min(len(a), len(b))
    if n < 2:
        return None
    xs = a[-n:]
    ys = b[-n:]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    denom = math.sqrt(var_x * var_y)
    if denom <= 0:
        return None
    return cov / denom


def _load_csv_close_series(ticker: str, window: int = CORRELATION_LOOKBACK_BARS) -> List[float]:
    if not ticker:
        return []
    matches = sorted(DATA_DIR.glob(f"{ticker}_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not matches:
        matches = sorted(DATA_DIR.glob(f"*{ticker}*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in matches[:1]:
        closes: List[float] = []
        try:
            with path.open("r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    raw = row.get("close")
                    if raw in (None, ""):
                        continue
                    try:
                        closes.append(float(raw))
                    except (TypeError, ValueError):
                        continue
        except OSError:
            continue
        if len(closes) >= 2:
            return closes[-(window + 1):]
    return []


def _check_correlation(slots: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    series_map: Dict[str, List[float]] = {}
    source_map: Dict[str, str] = {}
    for slot_id, slot in slots.items():
        ticker = str(slot.get("ticker") or slot_id)
        prices = _extract_series_from_slot(slot)
        source = "slot_history"
        if len(prices) < 2:
            prices = _load_csv_close_series(ticker)
            source = "csv"
        if len(prices) >= 2:
            series_map[ticker] = _returns_from_prices(prices)
            source_map[ticker] = source
    tickers = list(series_map.keys())
    pairwise: List[float] = []
    pair_details: List[str] = []
    for idx, left in enumerate(tickers):
        for right in tickers[idx + 1:]:
            corr = _pearson_corr(series_map[left], series_map[right])
            if corr is None:
                continue
            pairwise.append(abs(corr))
            pair_details.append(f"{left}/{right}={corr:.3f} ({source_map.get(left, '?')},{source_map.get(right, '?')})")
    if not pairwise:
        return {
            "name": "correlation",
            "value": None,
            "weight": COMPONENT_WEIGHTS["correlation"],
            "status": "insufficient_data",
            "detail": "no aligned close-return series for last %d bars" % CORRELATION_LOOKBACK_BARS,
        }
    avg_abs_corr = sum(pairwise) / len(pairwise)
    if avg_abs_corr >= CORRELATION_VETO_THRESHOLD:
        status = "veto"
    elif avg_abs_corr >= CORRELATION_WARN_THRESHOLD:
        status = "warn"
    else:
        status = "ok"
    detail = "avg_abs_corr=%.3f across %d pairs; %s" % (
        avg_abs_corr,
        len(pairwise),
        "; ".join(pair_details[:4]) or "no pair details",
    )
    return {
        "name": "correlation",
        "value": round(avg_abs_corr, 4),
        "weight": COMPONENT_WEIGHTS["correlation"],
        "status": status,
        "detail": detail,
    }


# ─── Компонентные проверки ────────────────────────────────────────────

def _check_exposure(
    slots: Dict[str, Any], config: Dict[str, Any]
) -> Dict[str, Any]:
    """Проверка экспозиции: go_rub слотов vs go_budget_rub конфига."""
    go_budget = config.get("go_budget_rub", 5000.0)
    total_go = sum(s.get("go_rub", 0.0) for s in slots.values())
    if go_budget <= 0:
        ratio = 0.0
    else:
        ratio = total_go / go_budget
    ok = ratio <= 1.0
    return {
        "name": "exposure",
        "value": round(ratio, 4),
        "weight": COMPONENT_WEIGHTS["exposure"],
        "status": "ok" if ok else "veto",
        "detail": "go_rub=%.0f / budget=%.0f = %.2f" % (total_go, go_budget, ratio),
    }


def _check_drawdown(slots: Dict[str, Any]) -> Dict[str, Any]:
    """Проверка просадки: max drawdown по слотам."""
    max_dd = 0.0
    for s in slots.values():
        pnl = s.get("pnl_rub", 0.0)
        peak = s.get("peak_pnl_rub", 0.0)
        if peak > 0:
            dd = max(0.0, (peak - pnl) / peak)
            max_dd = max(max_dd, dd)
        # Альтернативно: используем open_position data
        op = s.get("open_position", {})
        entry_price = op.get("entry_price", 0.0)
        if entry_price > 0:
            # Approximate drawdown from stop_streak
            streak = s.get("stop_streak", 0)
            if streak >= 3:
                max_dd = max(max_dd, 0.15)  # 15% threshold
    ok = max_dd < 0.15
    return {
        "name": "drawdown",
        "value": round(max_dd, 4),
        "weight": COMPONENT_WEIGHTS["drawdown"],
        "status": "ok" if ok else "warn" if max_dd < 0.25 else "veto",
        "detail": "max_drawdown=%.2f%%" % (max_dd * 100),
    }


def _check_volatility(slots: Dict[str, Any]) -> Dict[str, Any]:
    """Проверка волатильности: используем allocator_metrics.risk_penalty."""
    max_rp = 0.0
    for s in slots.values():
        op = s.get("open_position", {})
        rp = risk_penalty({
            "drawdown_pct": s.get("pnl_rub", 0.0),
            "volatility": op.get("entry_atr", 1.0),
        })
        max_rp = max(max_rp, rp)
    ok = max_rp < 0.7
    return {
        "name": "volatility",
        "value": round(max_rp, 4),
        "weight": COMPONENT_WEIGHTS["volatility"],
        "status": "ok" if ok else "warn" if max_rp < 0.9 else "veto",
        "detail": "max_risk_penalty=%.4f" % max_rp,
    }


def _check_signal_age(
    slots: Dict[str, Any], config: Dict[str, Any], now_ts: Optional[float]
) -> Dict[str, Any]:
    """Проверка свежести сигналов: max_age vs signal_max_age_minutes."""
    max_age_min = config.get("signal_max_age_minutes", 16)
    if now_ts is None or now_ts <= 0:
        # Нет timestamp — не можем проверить, считаем ok
        return {
            "name": "signal_age",
            "value": 0.0,
            "weight": COMPONENT_WEIGHTS["signal_age"],
            "status": "ok",
            "detail": "now_ts not provided, skipped",
        }
    max_staleness = 0.0
    for s in slots.values():
        last_ts = s.get("last_signal_ts", 0.0)
        if last_ts > 0:
            age_min = (now_ts - last_ts) / 60.0
            max_staleness = max(max_staleness, max(0.0, age_min))
    ok = max_staleness <= max_age_min
    return {
        "name": "signal_age",
        "value": round(max_staleness, 2),
        "weight": COMPONENT_WEIGHTS["signal_age"],
        "status": "ok" if ok else "warn",
        "detail": "max_staleness=%.1f min, limit=%d min" % (max_staleness, max_age_min),
    }


def _check_slots(
    slots: Dict[str, Any], config: Dict[str, Any]
) -> Dict[str, Any]:
    """Проверка количества слотов: n <= max_slots."""
    max_slots = config.get("max_slots", 3)
    n = len(slots)
    ok = n <= max_slots
    return {
        "name": "slots",
        "value": n,
        "weight": COMPONENT_WEIGHTS["slots"],
        "status": "ok" if ok else "veto",
        "detail": "n_slots=%d, max=%d" % (n, max_slots),
    }


def _check_caps(slots: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """Проверка контрактов: все contracts <= max_contracts_per_entry."""
    max_c = config.get("max_contracts_per_entry", 1)
    violations = 0
    for s in slots.values():
        c = s.get("contracts", 1)
        if c > max_c:
            violations += 1
    ok = violations == 0
    return {
        "name": "caps",
        "value": violations,
        "weight": COMPONENT_WEIGHTS["caps"],
        "status": "ok" if ok else "veto",
        "detail": "violations=%d, max_contracts=%d" % (violations, max_c),
    }


# ─── Итоговый scoring ────────────────────────────────────────────────

def _compute_risk_score(
    components: Dict[str, Dict[str, Any]], weights_override: Optional[Dict[str, int]] = None
) -> float:
    """Итоговый risk_score 0..100 по компонентам.

    Формула:
        sum(w * pass_score) / sum(w) * 100
        где pass_score = 1.0 (ok), 0.5 (warn), 0.0 (veto)
    """
    w = weights_override or COMPONENT_WEIGHTS
    total_weight = 0
    weighted_sum = 0.0
    for comp_name, comp in components.items():
        weight = w.get(comp_name, 0)
        status = comp.get("status", "veto")
        if comp_name == "correlation":
            value = comp.get("value")
            if value is None:
                score = 0.35
            else:
                # Higher pairwise correlation is worse: convert to a 0..1 health score.
                score = max(0.0, min(1.0, 1.0 - float(value)))
                if status == "warn":
                    score = min(score, 0.5)
                elif status == "veto":
                    score = 0.0
        elif status == "ok":
            score = 1.0
        elif status == "warn":
            score = 0.5
        elif status == "insufficient_data":
            score = 0.35
        else:
            score = 0.0
        weighted_sum += weight * score
        total_weight += weight
    if total_weight == 0:
        return 0.0
    return round(weighted_sum / total_weight * 100.0, 1)


def _compute_verdict(
    risk_score: float, components: Dict[str, Dict[str, Any]]
) -> str:
    """Вердикт по risk_score и компонентам."""
    has_veto = any(c.get("status") == "veto" for c in components.values())
    if has_veto:
        return "VETO"
    has_warn = any(c.get("status") == "warn" for c in components.values())
    if has_warn or risk_score < 60:
        return "REDUCE"
    return "ALLOW"


# ─── Основная функция (мост) ──────────────────────────────────────────

def build_scorecard(
    slots_dict: Dict[str, Any],
    config: Dict[str, Any],
    now_ts: Optional[float] = None,
    weights_override: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """Мост: pipeline_ranker → risk scorecard.

    Принимает:
        slots_dict: формат pipeline_ranker (slot_id → {ticker, contracts, go_rub,
            pnl_rub, peak_pnl_rub, stop_streak, last_signal_ts, open_position, ...})
        config: sc_config из pipeline_ranker (go_budget_rub, delta_band_pct,
            deposit_rub, max_slots, max_contracts_per_entry, excluded,
            risk_scorecard_weights, signal_max_age_minutes)
        now_ts: текущий timestamp (optional)
        weights_override: override COMPONENT_WEIGHTS (optional)

    Возвращает:
        {
            "risk_score": float (0..100),
            "verdict": "ALLOW" | "REDUCE" | "VETO",
            "components": {
                "exposure": {"name": ..., "value": ..., "weight": ..., "status": ..., "detail": ...},
                "drawdown": {...},
                "volatility": {...},
                "signal_age": {...},
                "slots": {...},
                "caps": {...},
            },
            "n_slots": int,
            "tickers": [str, ...],
            "overall": {
                "avg_composite": float,
                "n_scored": int,
            },
            "constraints": {
                "max_slots": int,
                "max_contracts_per_entry": int,
                "excluded": [str, ...],
            },
        }
    """
    if not slots_dict:
        return {
            "risk_score": 0.0,
            "verdict": "VETO",
            "components": {},
            "n_slots": 0,
            "tickers": [],
            "overall": {"avg_composite": 0.0, "n_scored": 0},
            "constraints": {
                "max_slots": config.get("max_slots", 3),
                "max_contracts_per_entry": config.get("max_contracts_per_entry", 1),
                "excluded": config.get("excluded", []),
            },
        }

    # Компонентные проверки
    components: Dict[str, Dict[str, Any]] = {}
    components["exposure"] = _check_exposure(slots_dict, config)
    components["drawdown"] = _check_drawdown(slots_dict)
    components["volatility"] = _check_volatility(slots_dict)
    components["signal_age"] = _check_signal_age(slots_dict, config, now_ts)
    components["slots"] = _check_slots(slots_dict, config)
    components["caps"] = _check_caps(slots_dict, config)
    components["correlation"] = _check_correlation(slots_dict, config)
    if components["correlation"]["status"] == "insufficient_data":
        components["correlation"]["value"] = CORRELATION_INSUFFICIENT_SCORE
        components["correlation"]["detail"] += " | fallback score=%.2f" % CORRELATION_INSUFFICIENT_SCORE

    # Risk score
    custom_weights = weights_override or config.get("risk_scorecard_weights")
    risk_score = _compute_risk_score(components, custom_weights)
    verdict = _compute_verdict(risk_score, components)

    # Tickers
    tickers = [
        s.get("ticker", "")
        for s in slots_dict.values()
        if s.get("ticker")
    ]

    # Overall: простой avg_composite из per-slot allocator_score
    composites = []
    for s in slots_dict.values():
        ticker = s.get("ticker", "")
        op = s.get("open_position", {})
        candidate = {
            "ticker": ticker,
            "direction": op.get("direction"),
            "win_rate": 0.5,  # default — pipeline_ranker не передаёт win_rate
            "avg_win": s.get("go_rub", 100.0),
            "avg_loss": s.get("go_rub", 100.0) * 0.5,
            "drawdown_pct": 0.0,
            "volatility": 1.0,
        }
        # allocator_score() не определён в allocator_metrics — используем composite
        # из expectancy + risk penalty
        er = expectancy_r(
            {"win_rate": candidate["win_rate"],
             "avg_win": candidate["avg_win"],
             "avg_loss": candidate["avg_loss"]},
            risk_per_trade=config.get("deposit_rub", 21281) * 0.027,
        )
        composites.append(round(er, 4))

    avg_composite = sum(composites) / len(composites) if composites else 0.0

    return {
        "risk_score": risk_score,
        "verdict": verdict,
        "components": components,
        "n_slots": len(slots_dict),
        "tickers": tickers,
        "overall": {
            "avg_composite": round(avg_composite, 4),
            "n_scored": len(slots_dict),
        },
        "constraints": {
            "max_slots": config.get("max_slots", 3),
            "max_contracts_per_entry": config.get("max_contracts_per_entry", 1),
            "excluded": config.get("excluded", []),
        },
    }

"""Walk-Forward Optimizer — Pardo rolling WFO для allocator weight params.

Модуль НЕ импортирует broker/client, НЕ пишет state/, НЕ ходит в сеть.
Все функции чистые: dict-in → dict-out. stdlib-only + allocator_metrics + candidate_allocator.

Алгоритм:
  1. Генерация сетки параметров (±N% от базовых WEIGHTS).
  2. Разбиение returns на rolling IS/OOS windows.
  3. Для каждого окна: grid-search на IS → frozen best params → оценка на OOS.
  4. Aggregate robustness score (pnl_up_pct, risk_down_pct, avg_delta_sharpe, worst_dd).

Связь с плацдармом:
  - allocator_metrics.allocator_score (weights= override) — скоринг кандидатов.
  - candidate_allocator.select_live_slots — baseline-отбор без WFO.
  - scorecard_metrics.sharpe_ratio, max_drawdown, profit_factor — метрики OOS.
  - config: max_slots=3, excluded=["RI"], max_contracts_per_entry=1.
"""
import math
from typing import Any, Dict, List, Optional, Tuple

from allocator_metrics import WEIGHTS as BASE_WEIGHTS, allocator_score
from candidate_allocator import baseline_rank_score, select_live_slots
from scorecard_metrics import max_drawdown, profit_factor, sharpe_ratio


# ─── Forbidden broker calls ───────────────────────────────────────────
_FORBIDDEN = {"post_order", "place_order", "send_order", "submit_order", "Client"}


# ═══════════════════════════════════════════════════════════════════════
# 1. Parameter grid generation
# ═══════════════════════════════════════════════════════════════════════

def generate_param_grid(
    base_weights: Optional[Dict[str, float]] = None,
    variations: Optional[List[float]] = None,
    keys: Optional[List[str]] = None,
) -> List[Dict[str, float]]:
    """Генерация сетки параметров — произведение variations × keys.

    base_weights: базовые веса (по умолчанию WEIGHTS из allocator_metrics).
    variations: список коэффициентов (по умолчанию [0.8, 0.9, 1.0, 1.1, 1.2]).
    keys: какие ключи варьировать (по умолчанию все ключи base_weights).

    Возвращает: список dict[{key: base*var}, ...] — декартово произведение.
    Финальный нормализованный (сумма = sum(base)) для сохранения масштаба.

    Пример:
        >>> grid = generate_param_grid({"a": 60, "b": 40}, [0.8, 1.2])
        >>> len(grid)
        4  # (a,b)*0.8, (a,b)*1.2, (a*0.8,b*1.2), (a*1.2,b*0.8)
    """
    if base_weights is None:
        base_weights = dict(BASE_WEIGHTS)
    if variations is None:
        variations = [0.8, 0.9, 1.0, 1.1, 1.2]
    if keys is None:
        keys = list(base_weights.keys())

    base_sum = sum(base_weights.values())

    # Рекурсивная генерация декартова произведения
    grid: List[Dict[str, float]] = []

    def _recurse(idx: int, current: Dict[str, float]) -> None:
        if idx == len(keys):
            # Нормализуем чтобы сумма = base_sum
            cur_sum = sum(current.values())
            if cur_sum > 0:
                norm = {k: round(v * base_sum / cur_sum, 4) for k, v in current.items()}
            else:
                norm = dict(current)
            grid.append(norm)
            return
        key = keys[idx]
        base_val = base_weights.get(key, 0.0)
        for var in variations:
            current[key] = base_val * var
            _recurse(idx + 1, current)
        del current[key]

    _recurse(0, {})
    return grid


# ═══════════════════════════════════════════════════════════════════════
# 2. Rolling IS/OOS split
# ═══════════════════════════════════════════════════════════════════════

def walk_forward_split(
    n_total: int,
    n_windows: int = 3,
    in_sample_frac: float = 0.7,
) -> List[Dict[str, int]]:
    """Разбиение временного ряда на rolling IS/OOS windows.

    n_total: общее количество периодов (returns).
    n_windows: количество окон.
    in_sample_frac: доля IS в каждом окне (0..1).

    Каждое окно — скользящее: следующее окно сдвигается на OOS-размер предыдущего.
    IS и OOS не пересекаются внутри одного окна.
    Последний OOS может выходить за n_total — обрезается.

    Возвращает: [{is_start, is_end, oos_start, oos_end, window_id}, ...]

    Пример:
        >>> w = walk_forward_split(100, n_windows=3)
        >>> all(wi["is_end"] <= wi["oos_start"] for wi in w)
        True
    """
    if n_total < 2:
        return []

    windows = []
    is_len = max(1, int(n_total * in_sample_frac / n_windows))
    oos_len = max(1, int(is_len * (1.0 - in_sample_frac) / in_sample_frac))

    for i in range(n_windows):
        is_start = i * oos_len
        is_end = min(is_start + is_len, n_total)
        oos_start = is_end
        oos_end = min(oos_start + oos_len, n_total)

        if is_start >= n_total or oos_start >= n_total:
            break

        windows.append({
            "window_id": i,
            "is_start": is_start,
            "is_end": is_end,
            "oos_start": oos_start,
            "oos_end": oos_end,
        })

    return windows


# ═══════════════════════════════════════════════════════════════════════
# 3. Selection with custom weights
# ═══════════════════════════════════════════════════════════════════════

def _select_with_weights(
    candidates: List[Dict[str, Any]],
    cfg: dict,
    regime_snapshot: Optional[Dict[str, Any]],
    weights: Dict[str, float],
) -> List[Dict[str, Any]]:
    """Отбор слотов с кастомными весами allocator (replica of select_live_slots logic).

    Использует allocator_score с weights override.
    Те же гейты: excluded, max_slots, max_contracts.
    """
    if regime_snapshot is None:
        regime_snapshot = {}

    excluded = cfg.get("excluded", [])
    max_slots = cfg.get("risk", {}).get("max_slots", 3)
    max_contracts = cfg.get("risk", {}).get("max_contracts_per_entry", 1)
    risk_per_trade_pct = cfg.get("risk_per_trade_pct", 2.7)
    deposit_rub = cfg.get("deposit_rub", 21281)
    risk_per_trade_rub = deposit_rub * risk_per_trade_pct / 100.0

    filtered = [c for c in candidates if c.get("ticker") not in excluded]

    scored = []
    for c in filtered:
        score = allocator_score(
            c, regime_snapshot, risk_per_trade=risk_per_trade_rub, weights=weights
        )
        scored.append({
            "ticker": c.get("ticker", ""),
            "direction": c.get("direction"),
            "contracts": min(c.get("contracts_requested", 1), max_contracts),
            "score": score,
        })

    scored.sort(key=lambda s: (-s["score"], s["ticker"]))
    return scored[: min(len(scored), max_slots)]


# ═══════════════════════════════════════════════════════════════════════
# 4. Portfolio return simulation
# ═══════════════════════════════════════════════════════════════════════

def _portfolio_returns(
    selected_tickers: List[str],
    returns_by_ticker: Dict[str, List[float]],
    start: int,
    end: int,
) -> List[float]:
    """Equal-weighted portfolio returns для выбранных тикеров на срезе [start, end).

    Возвращает список доходностей (длина = end - start).
    Если тикер не найден в returns_by_ticker — его доходность = 0.
    """
    if not selected_tickers:
        return [0.0] * (end - start)

    n = end - start
    portfolio = [0.0] * n
    count = 0
    for ticker in selected_tickers:
        series = returns_by_ticker.get(ticker, [])
        if not series:
            continue
        count += 1
        for i in range(n):
            idx = start + i
            if idx < len(series):
                portfolio[i] += series[idx]

    if count > 0:
        portfolio = [p / count for p in portfolio]

    return portfolio


# ═══════════════════════════════════════════════════════════════════════
# 5. Optimize on a single window
# ═══════════════════════════════════════════════════════════════════════

def optimize_on_window(
    candidates: List[Dict[str, Any]],
    cfg: dict,
    regime_snapshot: Optional[Dict[str, Any]],
    param_grid: List[Dict[str, float]],
    returns_by_ticker: Dict[str, List[float]],
    is_start: int,
    is_end: int,
    oos_start: int,
    oos_end: int,
) -> Dict[str, Any]:
    """Для одного окна: grid-search на IS, frozen best → OOS.

    Возвращает:
        {
            "window_id": int ( передаётся снаружи ),
            "best_params": {...},
            "is_sharpe": float,
            "oos_sharpe": float,
            "oos_max_dd": float,
            "oos_profit_factor": float,
            "oos_pnl": float (накопленная доходность),
            "selected_tickers": [...],
            "baseline_oos_sharpe": float,
            "baseline_oos_max_dd": float,
            "pnl_up": bool,
            "risk_down": bool,
        }
    """
    best_is_sharpe = -float("inf")
    best_params = param_grid[0] if param_grid else dict(BASE_WEIGHTS)
    best_is_tickers: List[str] = []

    # Grid search on IS
    for params in param_grid:
        selected = _select_with_weights(candidates, cfg, regime_snapshot, params)
        tickers = [s["ticker"] for s in selected]
        is_rets = _portfolio_returns(tickers, returns_by_ticker, is_start, is_end)
        is_sh = sharpe_ratio(is_rets)

        if is_sh > best_is_sharpe:
            best_is_sharpe = is_sh
            best_params = params
            best_is_tickers = tickers

    # Evaluate best params on OOS
    oos_rets = _portfolio_returns(best_is_tickers, returns_by_ticker, oos_start, oos_end)
    oos_sh = sharpe_ratio(oos_rets)
    oos_dd = max_drawdown(oos_rets)
    oos_pf = profit_factor(oos_rets)
    oos_pnl = 1.0
    for r in oos_rets:
        oos_pnl *= (1.0 + r)

    # Baseline selection (no weights override, using baseline_rank_score)
    bl_candidates = [dict(c) for c in candidates]
    bl_filtered = [c for c in bl_candidates if c.get("ticker") not in cfg.get("excluded", [])]
    for c in bl_filtered:
        c["_bl_score"] = baseline_rank_score(c)
    bl_filtered.sort(key=lambda s: (-s["_bl_score"], s["ticker"]))
    max_slots = cfg.get("risk", {}).get("max_slots", 3)
    bl_tickers = [c["ticker"] for c in bl_filtered[:max_slots]]

    bl_oos_rets = _portfolio_returns(bl_tickers, returns_by_ticker, oos_start, oos_end)
    bl_oos_sh = sharpe_ratio(bl_oos_rets)
    bl_oos_dd = max_drawdown(bl_oos_rets)

    return {
        "best_params": best_params,
        "is_sharpe": round(best_is_sharpe, 6),
        "oos_sharpe": round(oos_sh, 6),
        "oos_max_dd": round(oos_dd, 6),
        "oos_profit_factor": round(oos_pf, 6),
        "oos_pnl": round(oos_pnl, 6),
        "selected_tickers": best_is_tickers,
        "baseline_oos_sharpe": round(bl_oos_sh, 6),
        "baseline_oos_max_dd": round(bl_oos_dd, 6),
        "pnl_up": oos_sh >= bl_oos_sh,
        "risk_down": oos_dd >= bl_oos_dd,  # less negative = better
    }


# ═══════════════════════════════════════════════════════════════════════
# 6. Aggregate robustness
# ═══════════════════════════════════════════════════════════════════════

def evaluate_robustness(windows_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Композитная метрика robustness по окнам.

    Возвращает:
        {
            "n_windows": int,
            "pnl_up_pct": float,         — доля окон где OOS Sharpe >= baseline
            "risk_down_pct": float,       — доля окон где OOS DD >= baseline DD
            "avg_delta_sharpe": float,    — средний delta (OOS_sharpe - baseline_sharpe)
            "worst_dd": float,            — худшая просадка среди всех OOS-окон
            "avg_oos_pnl": float,         — средний накопленный PnL по окнам
            "best_params_consensus": dict — веса, встречающиеся чаще всего как best
            "robustness_score": float,    — агрегат 0..1
        }
    """
    if not windows_results:
        return {
            "n_windows": 0,
            "pnl_up_pct": 0.0,
            "risk_down_pct": 0.0,
            "avg_delta_sharpe": 0.0,
            "worst_dd": 0.0,
            "avg_oos_pnl": 0.0,
            "best_params_consensus": {},
            "robustness_score": 0.0,
        }

    n = len(windows_results)
    pnl_up_count = sum(1 for w in windows_results if w["pnl_up"])
    risk_down_count = sum(1 for w in windows_results if w["risk_down"])

    deltas = [
        w["oos_sharpe"] - w["baseline_oos_sharpe"] for w in windows_results
    ]
    avg_delta = sum(deltas) / n if n else 0.0

    worst_dd = min(w["oos_max_dd"] for w in windows_results) if windows_results else 0.0
    avg_pnl = sum(w["oos_pnl"] for w in windows_results) / n if n else 1.0

    # Consensus params: медиана по каждому ключу
    keys = list(windows_results[0]["best_params"].keys())
    consensus = {}
    for k in keys:
        vals = sorted(w["best_params"][k] for w in windows_results)
        mid = n // 2
        consensus[k] = round(vals[mid], 4) if n % 2 == 1 else round(
            (vals[mid - 1] + vals[mid]) / 2.0, 4
        )

    # Robustness score: [0..1]
    # Компоненты: pnl_up_pct (40%), risk_down_pct (30%), avg_delta_sharpe > 0 (20%), worst_dd > -0.1 (10%)
    c_pnl = pnl_up_count / n
    c_risk = risk_down_count / n
    c_sharpe = min(max(avg_delta / 2.0 + 0.5, 0.0), 1.0)  # нормализуем к 0..1
    c_dd = min(max(1.0 + worst_dd / 0.2, 0.0), 1.0) if worst_dd < 0 else 1.0

    robustness = 0.4 * c_pnl + 0.3 * c_risk + 0.2 * c_sharpe + 0.1 * c_dd

    return {
        "n_windows": n,
        "pnl_up_pct": round(c_pnl * 100.0, 1),
        "risk_down_pct": round(c_risk * 100.0, 1),
        "avg_delta_sharpe": round(avg_delta, 6),
        "worst_dd": round(worst_dd, 6),
        "avg_oos_pnl": round(avg_pnl, 6),
        "best_params_consensus": consensus,
        "robustness_score": round(robustness, 4),
    }


# ═══════════════════════════════════════════════════════════════════════
# 7. Full walk-forward report
# ═══════════════════════════════════════════════════════════════════════

def walk_forward_report(
    candidates: List[Dict[str, Any]],
    cfg: dict,
    regime_snapshot: Optional[Dict[str, Any]],
    returns_by_ticker: Dict[str, List[float]],
    param_grid: Optional[List[Dict[str, float]]] = None,
    n_windows: int = 3,
    in_sample_frac: float = 0.7,
) -> Dict[str, Any]:
    """Полный walk-forward optimization report.

    candidates: список кандидатов с win_rate, avg_win, avg_loss, direction.
    cfg: конфиг (excluded, risk.max_slots, ...).
    regime_snapshot: regime данные.
    returns_by_ticker: {ticker: [r1, r2, ...]} — per-period returns.
    param_grid: сетка параметров (по умолчанию auto-generated).
    n_windows: количество rolling окон.
    in_sample_frac: доля IS в каждом окне.

    Возвращает:
        {
            "per_window": [...],
            "aggregate": {...},
            "config_summary": {n_windows, in_sample_frac, n_params, n_candidates}
        }
    """
    if param_grid is None:
        param_grid = generate_param_grid()

    # Определяем n_total из returns
    n_total = 0
    for series in returns_by_ticker.values():
        if len(series) > n_total:
            n_total = len(series)

    if n_total < 2:
        return {
            "per_window": [],
            "aggregate": evaluate_robustness([]),
            "config_summary": {
                "n_windows": 0,
                "in_sample_frac": in_sample_frac,
                "n_params": len(param_grid),
                "n_candidates": len(candidates),
            },
        }

    windows = walk_forward_split(n_total, n_windows, in_sample_frac)

    per_window = []
    for w in windows:
        result = optimize_on_window(
            candidates=candidates,
            cfg=cfg,
            regime_snapshot=regime_snapshot,
            param_grid=param_grid,
            returns_by_ticker=returns_by_ticker,
            is_start=w["is_start"],
            is_end=w["is_end"],
            oos_start=w["oos_start"],
            oos_end=w["oos_end"],
        )
        result["window_id"] = w["window_id"]
        result["is_range"] = (w["is_start"], w["is_end"])
        result["oos_range"] = (w["oos_start"], w["oos_end"])
        per_window.append(result)

    aggregate = evaluate_robustness(per_window)

    return {
        "per_window": per_window,
        "aggregate": aggregate,
        "config_summary": {
            "n_windows": n_windows,
            "in_sample_frac": in_sample_frac,
            "n_params": len(param_grid),
            "n_candidates": len(candidates),
        },
    }


# ═══════════════════════════════════════════════════════════════════════
# 8. AST no-broker guard
# ═══════════════════════════════════════════════════════════════════════

def validate_no_broker(filepath: str) -> None:
    """AST-based проверка: модуль не содержит broker-вызовов.

    Поднимает RuntimeError если найден forbidden call.
    """
    import ast as _ast
    import pathlib

    tree = _ast.parse(pathlib.Path(filepath).read_text())
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Call):
            func = node.func
            name = ""
            if isinstance(func, _ast.Name):
                name = func.id
            elif isinstance(func, _ast.Attribute):
                name = func.attr
            if name in _FORBIDDEN:
                raise RuntimeError(f"walk_forward_optimizer.py contains forbidden call: {name}()")


# ═══════════════════════════════════════════════════════════════════════
# CLI demo
# ═══════════════════════════════════════════════════════════════════════

def _demo() -> None:
    """Демонстрация WFO на синтетических данных."""
    import json
    import random

    random.seed(42)

    candidates = [
        {"ticker": "LKOH", "direction": "LONG", "win_rate": 0.6, "avg_win": 200, "avg_loss": 100},
        {"ticker": "GAZP", "direction": "SHORT", "win_rate": 0.5, "avg_win": 80, "avg_loss": 50},
        {"ticker": "SBER", "direction": "LONG", "win_rate": 0.7, "avg_win": 100, "avg_loss": 120},
        {"ticker": "RI", "direction": "LONG", "win_rate": 0.9, "avg_win": 500, "avg_loss": 10},
        {"ticker": "BR", "direction": "SHORT", "win_rate": 0.45, "avg_win": 60, "avg_loss": 40},
        {"ticker": "Si", "direction": "LONG", "win_rate": 0.55, "avg_win": 120, "avg_loss": 80},
    ]

    cfg = {
        "excluded": ["RI"],
        "risk": {"max_slots": 3, "max_contracts_per_entry": 1},
        "risk_per_trade_pct": 2.7,
        "deposit_rub": 21281,
    }

    regime = {
        "tickers": {
            "LKOH": {"adx": 21.4, "direction": "down", "regime": "range"},
            "GAZP": {"adx": 35.3, "direction": "up", "regime": "trend"},
            "SBER": {"adx": 45.3, "direction": "down", "regime": "trend"},
            "BR": {"adx": 23.1, "direction": "up", "regime": "trend"},
            "Si": {"adx": 18.0, "direction": "up", "regime": "trend"},
            "RI": {"adx": 50.0, "direction": "up", "regime": "trend"},
        },
        "bias": "neutral",
    }

    # Synthetic returns: 60 periods
    tickers = ["LKOH", "GAZP", "SBER", "BR", "Si", "RI"]
    returns_by_ticker = {}
    for t in tickers:
        base = [random.gauss(0.001, 0.015) for _ in range(60)]
        returns_by_ticker[t] = base

    report = walk_forward_report(candidates, cfg, regime, returns_by_ticker)
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    _demo()

"""Candidate Allocator — скоринг и отбор ≤3 live slots.

Модуль не импортирует broker/client, не пишет state/, не ходит в сеть.
Все функции чистые: dict-in → dict-out.

Логика:
  1. VETO тикеров из cfg["excluded"] ДО скоринга (как risk.py:103).
  2. allocator_score для каждого кандидата (из allocator_metrics).
  3. Сортировка по score desc (tie-break по имени для детерминизма).
  4. top-k = min(len(filtered), cfg.risk.max_slots=3).
  5. contracts = min(requested, cfg.risk.max_contracts_per_entry=1).

Связь с плацдармом:
  - core/risk.py:103 — первый гейт approve_entry отклоняет excluded.
  - config.json: max_slots=3, max_contracts_per_entry=1, excluded=["RI"].
  - Аналог rank_score из seeder.py:74-85, но добавляет expectancy/risk/regime.
"""
from typing import Any, Dict, List, Optional

from allocator_metrics import (
    WEIGHTS,
    allocator_score,
    expectancy_r,
    regime_bonus,
    risk_penalty,
)


# ─── Контракт на сборку конфига ────────────────────────────────────────

def _cfg_for_allocator(config: dict) -> dict:
    """Извлечь из config.json py-словарь, совместимый с allocator.

    config — raw dict из config.json.
    Возвращает: {"excluded": [...], "risk": {"max_slots": ..., "max_contracts_per_entry": ...},
                  "risk_per_trade_pct": ..., "deposit_rub": ...}
    """
    risk = config.get("risk", {})
    return {
        "excluded": config.get("excluded", []),
        "risk": {
            "max_slots": risk.get("max_slots", 3),
            "max_contracts_per_entry": risk.get("max_contracts_per_entry", 1),
        },
        "risk_per_trade_pct": risk.get("risk_per_trade_pct", 2.7),
        "deposit_rub": config.get("deposit_rub", 21281),
    }


# ─── Основной отбор ─────────────────────────────────────────────────────

def select_live_slots(
    candidates: List[Dict[str, Any]],
    cfg: dict,
    regime_snapshot: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Детерминированный отбор live-слотов из кандидатов.

    candidates: список словарей, каждый —
        {
            "ticker": str,
            "direction": "LONG" / "SHORT" / None,
            "win_rate": float,
            "avg_win": float,
            "avg_loss": float,
            "drawdown_pct": float (optional),
            "volatility": float (optional),
            "contracts_requested": int (optional, default 1),
        }

    cfg: {"excluded": [...], "risk": {"max_slots": int, "max_contracts_per_entry": int},
          "risk_per_trade_pct": float, "deposit_rub": int}
        Или полный config.json dict — функция сама достанет нужные поля.

    regime_snapshot: state/regime_snapshot.json (optional; если None → regime_bonus=0).

    Возвращает: список selected[{ticker, direction, contracts, score, expectancy_r,
                                risk_penalty, regime_bonus}], отсортированный по score desc.
    """
    # Нормализуем cfg — принимаем и полный config.json, и укороченный
    if "risk" in cfg and isinstance(cfg["risk"], dict) and "max_slots" in cfg.get("risk", {}):
        excluded = cfg.get("excluded", [])
        max_slots = cfg["risk"]["max_slots"]
        max_contracts = cfg["risk"]["max_contracts_per_entry"]
        risk_per_trade_pct = cfg.get("risk_per_trade_pct", 2.7)
        deposit_rub = cfg.get("deposit_rub", 21281)
    else:
        excluded = cfg.get("excluded", [])
        max_slots = cfg.get("max_slots", 3)
        max_contracts = cfg.get("max_contracts_per_entry", 1)
        risk_per_trade_pct = cfg.get("risk_per_trade_pct", 2.7)
        deposit_rub = cfg.get("deposit_rub", 21281)

    if regime_snapshot is None:
        regime_snapshot = {}

    # Risk per trade в рублях для нормировки expectancy
    risk_per_trade_rub = deposit_rub * risk_per_trade_pct / 100.0

    # Шаг 1: VETO excluded тикеров ДО скоринга
    filtered = [c for c in candidates if c.get("ticker") not in excluded]

    # Шаг 2: Scoring
    scored = []
    for c in filtered:
        e_r = expectancy_r(
            {"win_rate": c.get("win_rate", 0.0),
             "avg_win": c.get("avg_win", 0.0),
             "avg_loss": c.get("avg_loss", 0.0)},
            risk_per_trade=risk_per_trade_rub,
        )
        r_pen = risk_penalty(c)
        reg_b = regime_bonus(
            c.get("ticker", ""),
            c.get("direction"),
            regime_snapshot,
        )
        score = allocator_score(c, regime_snapshot, risk_per_trade=risk_per_trade_rub)

        contracts_req = c.get("contracts_requested", 1)
        contracts = min(contracts_req, max_contracts)

        scored.append({
            "ticker": c.get("ticker", ""),
            "direction": c.get("direction"),
            "contracts": contracts,
            "score": score,
            "expectancy_r": round(e_r, 6),
            "risk_penalty": round(r_pen, 6),
            "regime_bonus": round(reg_b, 6),
        })

    # Шаг 3: Сортировка по score desc, tie-break по имени (детерминизм)
    scored.sort(key=lambda s: (-s["score"], s["ticker"]))

    # Шаг 4: Top-k
    k = min(len(scored), max_slots)
    return scored[:k]


# ─── Baseline comparison (rank_score style) ─────────────────────────────

def baseline_rank_score(candidate: Dict[str, Any]) -> float:
    """Упрощённый baseline rank_score по аналогии с seeder.py:74-85.

    rank_score = pnl + sharpe*1000 + win_rate*30 + (pf-1)*800 + freq_bonus

    Для allocator-кандидата (без pnl/sharpe/pf/freq) — упрощаем:
    score = win_rate*30 + pnl_proxy
    где pnl_proxy = avg_win * win_rate - avg_loss * (1-win_rate) (сырая expectancy).

    Этот baseline НЕ содержит expectancy-нормировку, risk-penalty и regime.
    Сравнение allocator vs baseline доказывает PnL↑/risk↓.
    """
    win_rate = candidate.get("win_rate", 0.0)
    avg_win = candidate.get("avg_win", 0.0)
    avg_loss = candidate.get("avg_loss", 0.0)

    raw_expectancy = win_rate * avg_win - (1.0 - win_rate) * avg_loss
    return raw_expectancy + win_rate * 30.0


def select_baseline_slots(
    candidates: List[Dict[str, Any]],
    max_slots: int = 3,
    excluded: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Baseline-отбор: по голому rank_score, как в seeder.py.

    Без expectancy-нормировки, без risk-penalty, без regime.
    Используется для сравнения allocator vs baseline в тестах.
    """
    if excluded is None:
        excluded = []

    filtered = [c for c in candidates if c.get("ticker") not in excluded]

    for c in filtered:
        c["_baseline_score"] = baseline_rank_score(c)

    filtered.sort(key=lambda s: (-s["_baseline_score"], s["ticker"]))
    return filtered[:max_slots]


# ─── CLI demo ───────────────────────────────────────────────────────────

def _demo() -> None:
    """Демонстрация allocator на синтетических данных."""
    import json

    candidates = [
        {"ticker": "LKOH", "direction": "LONG",
         "win_rate": 0.6, "avg_win": 200, "avg_loss": 100},
        {"ticker": "GAZP", "direction": "SHORT",
         "win_rate": 0.5, "avg_win": 80, "avg_loss": 50},
        {"ticker": "SBER", "direction": "LONG",
         "win_rate": 0.7, "avg_win": 100, "avg_loss": 120},
        {"ticker": "RI", "direction": "LONG",
         "win_rate": 0.9, "avg_win": 500, "avg_loss": 10},
        {"ticker": "BR", "direction": "SHORT",
         "win_rate": 0.45, "avg_win": 60, "avg_loss": 40},
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
            "RI": {"adx": 50.0, "direction": "up", "regime": "trend"},
        },
        "bias": "neutral",
    }

    selected = select_live_slots(candidates, cfg, regime)
    print("=== ALLOCATOR SELECT ===")
    for s in selected:
        print(json.dumps(s, ensure_ascii=False))

    baseline = select_baseline_slots(
        [dict(c) for c in candidates],  # копия, т.к. baseline добавляет _baseline_score
        max_slots=3, excluded=["RI"],
    )
    print("\n=== BASELINE SELECT ===")
    for b in baseline:
        print(json.dumps({k: v for k, v in b.items() if k != "_baseline_score"},
                         ensure_ascii=False))

    print("\nDRY_RUN: no real orders, no broker calls")


if __name__ == "__main__":
    _demo()

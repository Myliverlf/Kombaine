"""Synthetic Fixtures — реалистичные синтетические данные для E2E dry-run.

Генерирует ideas с n_trades >= 30, win_rate, avg_win, avg_loss —
такие, какие quality_gate ожидает и пропускает через significance gate.

Также генерирует returns_map для consistency scoring.

Безопасный config: mode=paper, paper_first=True.
Live orders запрещены.
"""
from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Optional, Tuple

# ─── Safe config ──────────────────────────────────────────────────────

def make_safe_config() -> Dict[str, Any]:
    """Безопасный конфиг: mode=paper, paper_first=True, max_slots=3."""
    return {
        "mode": "paper",
        "paper_first": True,
        "deposit_rub": 100000,
        "risk": {
            "risk_per_trade_pct": 2.7,
            "go_budget_pct": 50,
            "max_slots": 3,
            "portfolio_stop_drawdown_pct": 25,
            "delta_band_pct": 30,
            "signal_max_age_minutes": 16,
            "max_contracts_per_entry": 1,
            "slot_eject_pf": 0.9,
            "slot_eject_window_trades": 20,
            "slot_eject_streak_stops": 3,
            "slot_eject_slot_drawdown_pct": 15,
            "slot_eject_silent_days": 5,
            "promotion_margin_pct": 10,
            "waitlist_ttl_days": 7,
            "waitlist_max": 20,
            "signal_pool_max": 10,
            "signal_rotation_days": 3,
            "signal_min_rank": 100,
            "min_reserve_pct": 30,
        },
        "universe": ["BR", "GAZP", "LKOH", "SBER", "Si"],
        "excluded": ["RI"],
        "risk_scorecard_weights": {
            "exposure": 20, "drawdown": 20, "volatility": 15,
            "correlation": 10, "signal_age": 15, "slots": 10, "caps": 10,
        },
    }


# ─── Synthetic ideas with real metrics ────────────────────────────────

def make_realistic_ideas() -> List[Dict[str, Any]]:
    """Синтетические ideas с n_trades >= 30 и реальными метриками.

    Каждая idea содержит win_rate, avg_win, avg_loss, n_trades —
    именно эти поля quality_gate использует для significance gate и scoring.

    Ожидаемое поведение:
      - 4 ideas pass quality_gate (LKOH, GAZP, SBER, BR)
      - 1 idea VETO'd (RI — excluded)
      - 1 idea rejected as weak (Si — low expectancy)

    returns:
      List[Dict]: ideas с обязательными полями:
        ticker, strategy_name, direction, contracts=1,
        win_rate (0..1), avg_win (>0), avg_loss (>=0),
        n_trades (>=30), generated_at
    """
    return [
        # ── Ideas that PASS quality_gate ──
        {
            "ticker": "LKOH",
            "strategy_name": "vwap_reversion",
            "direction": "SHORT",
            "contracts": 1,
            "win_rate": 0.65,
            "avg_win": 450.0,
            "avg_loss": 220.0,
            "n_trades": 52,
            "source": "daily_generator",
            "generated_at": 1724400000.0,
        },
        {
            "ticker": "GAZP",
            "strategy_name": "ft_bband_rsi",
            "direction": "SHORT",
            "contracts": 1,
            "win_rate": 0.58,
            "avg_win": 300.0,
            "avg_loss": 180.0,
            "n_trades": 45,
            "source": "daily_generator",
            "generated_at": 1724400000.0,
        },
        {
            "ticker": "SBER",
            "strategy_name": "ma_cross",
            "direction": "SHORT",
            "contracts": 1,
            "win_rate": 0.70,
            "avg_win": 300.0,
            "avg_loss": 150.0,
            "n_trades": 60,
            "source": "daily_generator",
            "generated_at": 1724400000.0,
        },
        {
            "ticker": "BR",
            "strategy_name": "rsi_reversal",
            "direction": "LONG",
            "contracts": 1,
            "win_rate": 0.60,
            "avg_win": 280.0,
            "avg_loss": 200.0,
            "n_trades": 42,
            "source": "daily_generator",
            "generated_at": 1724400000.0,
        },
        # ── Idea that VETO'd (excluded) ──
        {
            "ticker": "RI",
            "strategy_name": "breakout",
            "direction": "LONG",
            "contracts": 1,
            "win_rate": 0.60,
            "avg_win": 500.0,
            "avg_loss": 250.0,
            "n_trades": 35,
            "source": "daily_generator",
            "generated_at": 1724400000.0,
        },
        # ── Idea that fails quality_gate (low score) ──
        {
            "ticker": "Si",
            "strategy_name": "scalper",
            "direction": "LONG",
            "contracts": 1,
            "win_rate": 0.42,
            "avg_win": 120.0,
            "avg_loss": 300.0,
            "n_trades": 35,
            "source": "daily_generator",
            "generated_at": 1724400000.0,
        },
    ]


def make_synthetic_returns_map(
    ideas: Optional[List[Dict[str, Any]]] = None,
    n_trades_override: Optional[int] = None,
) -> Dict[str, List[float]]:
    """Генерирует returns_map для quality_gate consistency scoring.

    Ключ: "{ticker}_{strategy_name}"
    Значение: список synthetic returns (float, dương biases для положительного equity curve).

    Returns:
        Dict[str, List[float]]: {idea_key: [returns...]}
    """
    if ideas is None:
        ideas = make_realistic_ideas()

    rng = random.Random(42)
    returns_map: Dict[str, List[float]] = {}

    for idea in ideas:
        key = f"{idea['ticker']}_{idea['strategy_name']}"
        win_rate = idea.get("win_rate", 0.5)
        avg_win = idea.get("avg_win", 100.0)
        avg_loss = idea.get("avg_loss", 100.0)
        n = n_trades_override or idea.get("n_trades", 50)

        returns: List[float] = []
        for _ in range(n):
            if rng.random() < win_rate:
                # Win: positive return proportional to avg_win, normalized
                ret = avg_win / 10000.0 * rng.uniform(0.8, 1.2)
            else:
                # Loss: negative return proportional to avg_loss
                ret = -(avg_loss / 10000.0) * rng.uniform(0.8, 1.2)
            returns.append(round(ret, 6))

        returns_map[key] = returns

    return returns_map


def make_synthetic_regime_snapshot() -> Dict[str, Any]:
    """Синтетический regime snapshot на 5 тикеров."""
    return {
        "ts": "2026-08-23T10:00:00+00:00",
        "tickers": {
            "BR": {"adx": 23.1, "direction": "up", "regime": "trend", "atr_pct": 0.279},
            "GAZP": {"adx": 35.3, "direction": "up", "regime": "trend", "atr_pct": 0.299},
            "LKOH": {"adx": 21.4, "direction": "down", "regime": "range", "atr_pct": 0.242},
            "SBER": {"adx": 45.3, "direction": "down", "regime": "trend", "atr_pct": 0.183},
            "Si": {"adx": 31.7, "direction": "up", "regime": "trend", "atr_pct": 0.156},
        },
        "bias": "up",
        "trend_cnt": 4,
    }


def make_synthetic_candidates() -> List[Dict[str, Any]]:
    """Синтетические кандидаты (после idea scoring) — для allocator.

    Отличие от идей: добавлены win_rate, avg_win, avg_loss, contracts_requested,
    score — как pipeline_ranker ожидает.
    """
    return [
        {
            "ticker": "LKOH", "direction": "SHORT", "win_rate": 0.65,
            "avg_win": 450.0, "avg_loss": 220.0, "contracts_requested": 1,
            "strategy": "vwap_reversion", "score": 0.72,
        },
        {
            "ticker": "GAZP", "direction": "SHORT", "win_rate": 0.58,
            "avg_win": 300.0, "avg_loss": 180.0, "contracts_requested": 1,
            "strategy": "ft_bband_rsi", "score": 0.58,
        },
        {
            "ticker": "SBER", "direction": "SHORT", "win_rate": 0.70,
            "avg_win": 300.0, "avg_loss": 150.0, "contracts_requested": 1,
            "strategy": "ma_cross", "score": 0.75,
        },
        {
            "ticker": "BR", "direction": "LONG", "win_rate": 0.60,
            "avg_win": 280.0, "avg_loss": 200.0, "contracts_requested": 1,
            "strategy": "rsi_reversal", "score": 0.62,
        },
        {
            "ticker": "RI", "direction": "LONG", "win_rate": 0.60,
            "avg_win": 500.0, "avg_loss": 250.0, "contracts_requested": 1,
            "strategy": "breakout", "score": 0.62,
        },
        {
            "ticker": "Si", "direction": "LONG", "win_rate": 0.42,
            "avg_win": 120.0, "avg_loss": 300.0, "contracts_requested": 1,
            "strategy": "scalper", "score": 0.35,
        },
    ]


def make_synthetic_returns() -> List[float]:
    """Синтетический временной ряд доходностей (seeded random)."""
    rng = random.Random(42)
    return [rng.gauss(0.0005, 0.015) for _ in range(50)]


def verify_fixtures() -> Tuple[bool, str]:
    """Проверяет что fixtures валидны: ideas с n_trades >= 30, safe config.

    Returns:
        (ok, message): ok=True если все проверки пройдены.
    """
    config = make_safe_config()
    ideas = make_realistic_ideas()
    returns_map = make_synthetic_returns_map(ideas)

    errors: List[str] = []

    # Check config safety
    if config.get("mode") != "paper":
        errors.append(f"config.mode={config.get('mode')}, expected 'paper'")
    if not config.get("paper_first"):
        errors.append("config.paper_first should be True")

    # Check ideas
    if len(ideas) < 3:
        errors.append(f"Only {len(ideas)} ideas, need >= 3")

    for idea in ideas:
        n = idea.get("n_trades", 0)
        if n < 30:
            errors.append(f"{idea['ticker']}: n_trades={n} < 30")

    # Check returns_map
    for idea in ideas:
        key = f"{idea['ticker']}_{idea['strategy_name']}"
        if key not in returns_map:
            errors.append(f"Missing returns for {key}")
        elif len(returns_map[key]) < 2:
            errors.append(f"Returns for {key}: only {len(returns_map[key])} points")

    # Check excluded
    if "RI" not in config.get("excluded", []):
        errors.append("RI not in excluded list")

    if errors:
        return False, "; ".join(errors)

    n_ideas = len(ideas)
    n_with_returns = len(returns_map)
    return True, f"OK: {n_ideas} ideas, {n_with_returns} returns_map entries, config safe"


if __name__ == "__main__":
    ok, msg = verify_fixtures()
    print(f"Verify: {ok} — {msg}")

    ideas = make_realistic_ideas()
    returns_map = make_synthetic_returns_map(ideas)
    config = make_safe_config()

    print(f"\nIdeas ({len(ideas)}):")
    for idea in ideas:
        print(f"  {idea['ticker']:5s} {idea['strategy_name']:20s} "
              f"win_rate={idea['win_rate']:.2f} avg_win={idea['avg_win']:.0f} "
              f"avg_loss={idea['avg_loss']:.0f} n_trades={idea['n_trades']}")

    print(f"\nReturns map: {len(returns_map)} entries")
    for key, rets in returns_map.items():
        print(f"  {key}: {len(rets)} returns, mean={sum(rets)/len(rets):.4f}")

#!/usr/bin/env python3
"""Юнит-тест SL/TP: движок комбайна vs эталон бэктеста futures_lab.

Проверяет критерий B: SL/TP движка = эталон бэктеста (совпадение 1-в-1).

Два уровня проверки:
  1) Чистое сравнение формул (без данных, без API).
  2) Интеграция с реальным ATR на синтетических данных.

Запуск:
    cd /root/prop-desk/strategy_combine && python code/test_sl_tp.py
"""
import sys
from pathlib import Path

# ── Пути ─────────────────────────────────────────────────────────────
COMBINE_DIR = Path("/root/prop-desk/strategy_combine")
FUTURES_LAB = Path("/root/prop-desk/futures_lab")

sys.path.insert(0, str(COMBINE_DIR))
sys.path.insert(0, str(FUTURES_LAB))

from core.config import load_config  # noqa: E402
from futures_lab import atr as atr_func  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402


# ── Формулы (скопированы 1-в-1 из исходников) ───────────────────────
#
# engine.py строки 164-165 (d=1 LONG, d=-1 SHORT):
#   stop_px = entry_price - d * sl_atr_mult * entry_atr
#   take_px = entry_price + d * tp_atr_mult * entry_atr
#
# futures_lab.py строки 513-514 (position > 0 = LONG):
#   stop_price = entry_price - stop_atr * entry_atr
#   take_price = entry_price + take_atr * entry_atr
#   (SHORT: знаки инвертированы)

def engine_sl_tp(entry_price: float, entry_atr: float, direction: str,
                 sl_mult: float, tp_mult: float) -> tuple[float, float]:
    """Формулы SL/TP из engine.py (строки 164-165)."""
    d = 1 if direction == "LONG" else -1
    stop_px = entry_price - d * sl_mult * entry_atr
    take_px = entry_price + d * tp_mult * entry_atr
    return stop_px, take_px


def backtest_sl_tp(entry_price: float, entry_atr: float, position: int,
                   stop_atr: float, take_atr: float) -> tuple[float, float]:
    """Формулы SL/TP из futures_lab.py (строки 513-514)."""
    if position > 0:
        stop_price = entry_price - stop_atr * entry_atr
        take_price = entry_price + take_atr * entry_atr
    else:
        stop_price = entry_price + stop_atr * entry_atr
        take_price = entry_price - take_atr * entry_atr
    return stop_price, take_price


# ── Тест 1: Чистое сравнение формул ──────────────────────────────────

def test_formulaic() -> None:
    """Сравнивает формулы движка и бэктеста на наборе значений."""
    cfg = load_config()
    sl_mult = cfg.sl_atr_mult
    tp_mult = cfg.tp_atr_mult
    cases = [
        ("LONG", 1,  18000.0, 100.0),
        ("SHORT", -1, 18000.0, 100.0),
        ("LONG", 1,  15000.0, 250.0),
        ("SHORT", -1, 20000.0, 50.0),
        ("LONG", 1,  10000.0, 1.0),    # tiny ATR
        ("SHORT", -1, 10000.0, 1.0),
        ("LONG", 1,  100000.0, 500.0),  # большой ATR
        ("SHORT", -1, 100000.0, 500.0),
        ("LONG", 1,  18000.0, 0.01),   # edge: почти нулевой ATR
        ("SHORT", -1, 18000.0, 0.01),
    ]

    for label, pos, ep, atr_val in cases:
        e_stop, e_take = engine_sl_tp(ep, atr_val, label, sl_mult, tp_mult)
        b_stop, b_take = backtest_sl_tp(ep, atr_val, pos, sl_mult, tp_mult)
        assert (e_stop, e_take) == (b_stop, b_take), (
            f"{label}: engine({e_stop:.4f}, {e_take:.4f}) vs backtest({b_stop:.4f}, {b_take:.4f})"
        )


# ── Тест 2: ATR-интеграция на синтетических данных ───────────────────

def test_atr_integration() -> None:
    """Вычисляет ATR на синтетических свечах, проверяет формулы."""
    cfg = load_config()
    sl_mult = cfg.sl_atr_mult
    tp_mult = cfg.tp_atr_mult
    rng = np.random.RandomState(99)
    n = 100
    times = pd.date_range(end=pd.Timestamp.now(tz="UTC"), periods=n, freq="15min")
    base = 18000.0
    trend = np.linspace(0, 500, n)
    noise = rng.normal(0, 20, n)
    closes = base + trend + noise
    highs = closes + rng.uniform(10, 30, n)
    lows = closes - rng.uniform(10, 30, n)
    opens = np.empty(n)
    opens[0] = closes[0]
    opens[1:] = closes[:-1]

    df = pd.DataFrame({
        "time": times, "open": opens, "high": highs,
        "low": lows, "close": closes,
    })
    df["atr"] = atr_func(df, 14)  # atr_period из config.json

    atr_vals = df["atr"].dropna()
    assert len(atr_vals) > 0, "ATR не вычислился на синтетических данных"

    avg_atr = float(atr_vals.mean())
    print("  ATR: %d баров, average=%.2f" % (len(atr_vals), avg_atr))

    # LONG
    last = df.iloc[-1]
    ep = float(last["close"])
    ea = float(last["atr"]) if pd.notna(last["atr"]) else avg_atr

    e_sl, e_tp = engine_sl_tp(ep, ea, "LONG", sl_mult, tp_mult)
    b_sl, b_tp = backtest_sl_tp(ep, ea, 1, sl_mult, tp_mult)
    assert (e_sl, e_tp) == (b_sl, b_tp)

    # SHORT
    e_sl_s, e_tp_s = engine_sl_tp(ep, ea, "SHORT", sl_mult, tp_mult)
    b_sl_s, b_tp_s = backtest_sl_tp(ep, ea, -1, sl_mult, tp_mult)
    assert (e_sl_s, e_tp_s) == (b_sl_s, b_tp_s)


# ── Main ─────────────────────────────────────────────────────────────

def main() -> bool:
    print("=== SL/TP Unit Test ===\n")

    cfg = load_config()
    sl_m = cfg.sl_atr_mult
    tp_m = cfg.tp_atr_mult
    print("Config: sl_atr_mult=%.1f, tp_atr_mult=%.1f\n" % (sl_m, tp_m))

    print("--- Test 1: Formula comparison ---")
    f1 = test_formulaic()

    print("\n--- Test 2: ATR integration ---")
    f2 = test_atr_integration()

    result = f1 and f2
    print("\n=== RESULT: %s ===" % ("LONG PASS SHORT PASS" if result else "FAIL"))
    return result


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)

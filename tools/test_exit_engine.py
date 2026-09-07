#!/usr/bin/env python3
"""Регресс-тест: пустые exits в exit_engine.run_loop должны дать ТОЧНО
те же цифры, что futures_lab.run_backtest (тот же вход/выход/экономика).

FIX(audit 2026-09-06): раньше тест был привязан к жёстко зашитому геному
ge_87ee193413 — когда тот пропадал из state/engine_candidates.json, тест
падал с "Unknown strategy" вместо проверки паритета. Теперь геном берётся
динамически из приёмника кандидатов (первый genetic с genome).

Запуск: PYTHONPATH=... python3 tools/test_exit_engine.py
"""
import sys, json
import numpy as np
import pandas as pd
from pathlib import Path

SC = Path("/root/prop-desk/strategy_combine"); FL = Path("/root/prop-desk/futures_lab")
sys.path.insert(0, str(SC)); sys.path.insert(0, str(FL)); sys.path.insert(0, str(SC / "tools"))
from futures_lab import run_backtest, build_signal, atr, _synthetic_spec_for_file  # noqa: E402
from engines.register import register_genomes  # noqa: E402
from engines.exit_engine import make_arr, run_loop  # noqa: E402
register_genomes()

# динамический выбор генома из приёмника (первый genetic-кандидат с genome)
cands_path = SC / "state" / "engine_candidates.json"
name = ticker = None
genome = None
if cands_path.exists():
    for c in json.loads(cands_path.read_text()).get("candidates", []):
        if c.get("engine") == "genetic" and c.get("genome") and not (c.get("exits") or {}):
            # нужен геном БЕЗ exit-генов: только тогда run_backtest и run_loop обязаны совпасть бит-в-бит
            name, ticker, genome = c["name"], c["ticker"], c["genome"]
            break
    if name is None:
        # fallback: любой genetic-геном, exits игнорируем (в run_loop передаём пустые)
        for c in json.loads(cands_path.read_text()).get("candidates", []):
            if c.get("engine") == "genetic" and c.get("genome"):
                name, ticker, genome = c["name"], c["ticker"], c["genome"]
                break
if name is None:
    print("SKIP: в state/engine_candidates.json нет genetic-геномов для регресса")
    sys.exit(0)

csv_path = FL / f"artifacts/tinkoff_futures_data/{ticker}_365d_1h_continuous.csv"
if not csv_path.exists():
    print(f"SKIP: нет данных {csv_path}")
    sys.exit(0)
df0 = pd.read_csv(csv_path).reset_index(drop=True)
spec = _synthetic_spec_for_file(ticker)
CAP, COMM, SLIP = 20000.0, 5.0, 1.0

data = df0.copy().reset_index(drop=True)
data["atr"] = atr(data, 14)
sig = build_signal(data, name, {}).values
print(f"parity-check: {ticker} {name} (геном из приёмника, exits пустые)")

m1, t1, e1 = run_backtest(data.drop(columns=["atr"]), spec, name, {},
    initial_cash=CAP, contracts=1, max_contracts=1, stop_atr=2.0, take_atr=3.0,
    max_hold_bars=48, commission_per_contract=COMM, slippage_bps=SLIP, debug_only=True)

arr = make_arr(data)
m2, t2, e2 = run_loop(arr, sig, {"stop_atr": 2.0, "take_atr": 3.0, "max_hold": 48}, {},
    CAP, 1, float(spec.point_value), COMM, SLIP / 10000.0, margin=float(spec.active_margin or 0.0))

ok = True
def cmp(k, a, b, tol=1e-6):
    global ok
    same = abs(a - b) <= tol * max(1.0, abs(a))
    ok &= same
    print(f"  {k}: run_backtest={a:.4f} exit_engine={b:.4f} {'OK' if same else 'MISMATCH'}")

cmp("total_pnl", m1["total_pnl"], m2["total_pnl"])
cmp("max_drawdown", m1["max_drawdown"], m2["max_drawdown"])
cmp("trade_count", m1["trade_count"], m2["trade_count"])
cmp("win_rate", m1["win_rate_pct"], m2["win_rate_pct"])
cmp("sharpe", m1["sharpe"], m2["sharpe"], tol=1e-3)
cmp("final_equity_last", float(e1.iloc[-1]), float(e2.iloc[-1]))
cmp("equity_len", len(e1), len(e2))
# сделки поштучно
if len(t1) == len(t2):
    bad = sum(1 for a, b in zip(t1, t2) if abs(a.pnl - b.pnl) > 1e-6 or a.reason != b.reason)
    print(f"  per-trade mismatches: {bad}")
    ok &= bad == 0
else:
    print("  trade count differs"); ok = False
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)

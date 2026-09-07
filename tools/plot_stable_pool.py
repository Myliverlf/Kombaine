#!/usr/bin/env python3
"""Plot stable_pool portfolio equity (paper-only).

СТИЛЬ ГРАФИКОВ: lieflat-charts (Mono) через tools/lieflat_render.py — ОБЯЗАТЕЛЬНО.
Не рисовать графики голым matplotlib: любая следующая модель должна получать
единый стиль. Подробнее: START.md раздел ГРАФИКИ.
"""
import json, sys
import numpy as np
import pandas as pd
from pathlib import Path

SC = Path("/root/prop-desk/strategy_combine")
FL = Path("/root/prop-desk/futures_lab")
sys.path.insert(0, str(SC)); sys.path.insert(0, str(FL)); sys.path.insert(0, str(SC / "tools"))
from futures_lab import run_backtest, _synthetic_spec_for_file  # noqa: E402
from engines.register import register_genomes  # noqa: E402
from lieflat_render import render_equity_report  # noqa: E402
register_genomes()

DATA = FL / "artifacts" / "tinkoff_futures_data"
CAP = 20000.0

pool = json.loads((SC / "state/stable_pool.json").read_text())
series = []
for s in pool["selected"]:
    t, strat, params = s["ticker"], s["strategy"], s.get("params") or {}
    p = DATA / f"{t}_365d_1h_continuous.csv"
    df = pd.read_csv(p).reset_index(drop=True)
    spec = _synthetic_spec_for_file(t)
    n = int(s.get("contracts") or 1)
    # единый роутер v3: exit-гены учитываются честно
    from engines.exit_engine import run_candidate
    cand = {"ticker": t, "strategy": strat, "params": s.get("params") or {},
            "risk": s.get("risk") or {}, "exits": s.get("exits") or {}}
    metrics, trades, eq = run_candidate(df, spec, cand, n, cap=CAP, comm=5.0, slip_bps=1.0)
    eqs = pd.Series(eq.values, index=pd.to_datetime(eq.index))
    daily = eqs.resample("D").last().ffill().dropna()
    series.append({"name": f"{t} {strat}", "contracts": n,
                   "points": list(zip(daily.index, (daily - CAP).values))})

frame = pd.concat([pd.Series([p[1] for p in s["points"]],
                             index=[p[0] for p in s["points"]]) for s in series],
                  axis=1, join="inner").dropna()
comb = frame.sum(axis=1)
dd = float((comb.cummax() - comb).max())
combined = {"name": f"Stable portfolio 365d",
            "sub": f"maxDD {dd:,.0f} ₽ · calmar {pool['portfolio_calmar']} · медиана месяца {pool.get('portfolio_median_month', 0):,.0f} ₽",
            "points": list(zip(comb.index, comb.values))}

out = SC / "reports" / "stable_pool_equity.png"
render_equity_report(series, combined, "Боевой пул — equity кривые (paper, капитал 20k)", out)
print("SAVED", out)
print(f"combined pnl={comb.iloc[-1]:+.0f} dd={dd:.0f} calmar={pool['portfolio_calmar']}")

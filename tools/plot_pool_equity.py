#!/usr/bin/env python3
"""Rebuild equity curves for the current active signal pool. Paper-only.
Runs the real futures_lab backtester on the exact pool params/datasets,
plots per-strategy equity + combined portfolio equity."""
import json, sys
from pathlib import Path
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

FL = Path("/root/prop-desk/futures_lab")
SC = Path("/root/prop-desk/strategy_combine")
sys.path.insert(0, str(SC)); sys.path.insert(0, str(FL))
from futures_lab import run_backtest, _synthetic_spec_for_file  # noqa: E402

DATA = FL / "artifacts" / "tinkoff_futures_data"
CAP = 20000.0
COMM = 5.0
SLIP = 1.0

pool = json.load(open(SC / "state/signal_pool.json"))["strategies"]
active = [(k, v) for k, v in pool.items() if v.get("status") == "active_signal_pool"]

fig, axes = plt.subplots(4, 2, figsize=(15, 16), sharex=False)
axes = axes.flatten()
combined = None
summary = []

for i, (key, v) in enumerate(active[:7]):
    ticker = v["ticker"]; strat = v["strategy"]; params = v.get("params") or {}
    tf = key.split("__")[2]
    # longest available dataset for this ticker/tf
    df = None; used_days = None
    for days in (365, 60):
        p = DATA / f"{ticker}_{days}d_{tf}_continuous.csv"
        if p.exists():
            df = pd.read_csv(p); used_days = days
            if len(df) > 200: break
    if df is None:
        print(f"NO DATA {key}"); continue
    spec = _synthetic_spec_for_file(ticker)
    metrics, trades, eq = run_backtest(df, spec, strat, params,
        initial_cash=CAP, contracts=1, max_contracts=1,
        commission_per_contract=COMM, slippage_bps=SLIP,
        debug_only=True)
    if isinstance(eq, pd.DataFrame):
        eq_s = eq["equity"] if "equity" in eq.columns else eq.iloc[:, -1]
    else:
        eq_s = pd.Series(eq)
    ax = axes[i]
    ax.plot(eq_s.values, lw=1.4, color="#1565c0")
    ax.axhline(CAP, color="gray", ls="--", lw=0.8)
    ax.set_title(f"{ticker} {strat} ({tf}, {used_days}d)  pnl={metrics.get('total_pnl',0):+.0f}₽  sh={metrics.get('sharpe',0):.2f}  tr={metrics.get('trade_count',0)}", fontsize=10)
    ax.grid(alpha=0.3)
    summary.append((ticker, strat, used_days, metrics.get('total_pnl',0), metrics.get('sharpe',0)))
    # align to common 365-point grid (15m vs 1h curves differ in length)
    import numpy as np
    x_src = np.linspace(0, 1, len(eq_s))
    x_dst = np.linspace(0, 1, 365)
    norm = np.interp(x_dst, x_src, eq_s.values - CAP)
    combined = norm if combined is None else combined + norm

axes[7].plot(combined, lw=1.6, color="#2e7d32")
axes[7].axhline(0, color="gray", ls="--", lw=0.8)
axes[7].set_title(f"COMBINED (sum of 7, equal 1 contract each)  final={combined[-1]:+.0f}₽  peak={combined.max():+.0f}₽  trough={combined.min():+.0f}₽", fontsize=10)
axes[7].grid(alpha=0.3)

plt.suptitle("Signal pool equity curves — paper only, 20k capital, commission 5₽/contract (2026-09-05)", fontsize=12)
plt.tight_layout(rect=[0,0,1,0.98])
out = SC / "reports" / "pool_equity_curves.png"
plt.savefig(out, dpi=110)
print("SAVED", out)
for s in summary: print(s)
print("COMBINED final:", round(combined[-1]), "max:", round(combined.max()), "min:", round(combined.min()))

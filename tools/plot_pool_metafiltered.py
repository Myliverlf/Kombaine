#!/usr/bin/env python3
"""Plot RAW vs META-FILTERED equity curves for the active pool. Paper-only."""
import json, sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

SC = Path("/root/prop-desk/strategy_combine")
FL = Path("/root/prop-desk/futures_lab")
sys.path.insert(0, str(SC)); sys.path.insert(0, str(SC / "tools"))
from meta_labeling_v2 import collect  # noqa: E402
from meta_filter import FCOLS  # noqa: E402

CACHE = json.loads((SC / "state" / "meta_filter_cache.json").read_text())
art = json.loads((SC / "state" / "meta_filter_model.json").read_text())
thr = art["threshold"]

df, n = collect()
df = df.sort_values(["sid", "order"]).reset_index(drop=True)
sc = StandardScaler().fit(df[FCOLS].values)
model = Ridge(alpha=CACHE["_model"]["alpha"]).fit(sc.transform(df[FCOLS].values), df["pnl"].values)
df["pred"] = model.predict(sc.transform(df[FCOLS].values))
df["keep"] = df["pred"] > thr

fig, axes = plt.subplots(4, 2, figsize=(15, 16))
axes = axes.flatten()
comb_raw = np.zeros(365); comb_f = np.zeros(365)
rows = []
for i, ((t, s), g) in enumerate(df.groupby(["ticker", "strategy"])):
    g = g.sort_values(["sid", "order"])
    raw = g["pnl"].cumsum().values
    filt = g.loc[g["keep"], "pnl"].cumsum().values
    # align filt onto raw trade index: filtered equity as step function over all trades
    filt_full = np.zeros(len(g))
    acc = 0.0
    for j, (_, r) in enumerate(g.iterrows()):
        if r["keep"]: acc += r["pnl"]
        filt_full[j] = acc
    x = np.linspace(0, 1, 365)
    raw_i = np.interp(x, np.linspace(0, 1, len(raw)), raw)
    fil_i = np.interp(x, np.linspace(0, 1, len(filt_full)), filt_full)
    comb_raw += raw_i; comb_f += fil_i
    ax = axes[i]
    ax.plot(raw_i, lw=1.3, color="#9e9e9e", label="raw")
    ax.plot(fil_i, lw=1.6, color="#1565c0", label="meta-filtered")
    ax.axhline(0, color="gray", ls="--", lw=0.7)
    ax.set_title(f"{t} {s}: raw {raw[-1]:+.0f}₽ -> filtered {filt_full[-1]:+.0f}₽  (kept {int(g['keep'].sum())}/{len(g)})", fontsize=10)
    ax.legend(fontsize=8); ax.grid(alpha=0.3)
    rows.append((t, s, round(raw[-1]), round(filt_full[-1])))

axes[7].plot(comb_raw, lw=1.4, color="#9e9e9e", label="raw sum")
axes[7].plot(comb_f, lw=1.8, color="#2e7d32", label="meta-filtered sum")
axes[7].axhline(0, color="gray", ls="--", lw=0.7)
axes[7].set_title(f"COMBINED 365d: raw {comb_raw[-1]:+.0f}₽ -> meta-filtered {comb_f[-1]:+.0f}₽", fontsize=11)
axes[7].legend(fontsize=9); axes[7].grid(alpha=0.3)

plt.suptitle("Pool equity: raw vs ML meta-filter (Ridge, thr=20₽) — paper only, 20k capital (2026-09-05)", fontsize=12)
plt.tight_layout(rect=[0, 0, 1, 0.98])
out = SC / "reports" / "pool_equity_metafiltered.png"
plt.savefig(out, dpi=110)
print("SAVED", out)
for r in rows: print(r)
print("COMBINED raw:", round(comb_raw[-1]), "filtered:", round(comb_f[-1]))

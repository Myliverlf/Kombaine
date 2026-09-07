#!/usr/bin/env python3
"""Why is test_delta negative on the expanded registry? Per-strategy test breakdown."""
import sys
import numpy as np
from pathlib import Path
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
SC = Path("/root/prop-desk/strategy_combine")
sys.path.insert(0, str(SC)); sys.path.insert(0, str(SC / "tools"))
from meta_filter import collect_trades, FCOLS, TRAIN_FRAC  # noqa: E402

df, n = collect_trades()
print("trades:", len(df), "strats:", n)
tr, te = [], []
for sid, g in df.groupby("sid"):
    s = int(len(g) * TRAIN_FRAC)
    tr += list(g.index[:s]); te += list(g.index[s:])
Xtr = df.loc[tr, FCOLS].values; ytr = df.loc[tr, "pnl"].values
Xte = df.loc[te, FCOLS].values; yte = df.loc[te, "pnl"].values
sc = StandardScaler().fit(Xtr)
m = Ridge(alpha=300.0).fit(sc.transform(Xtr), ytr)
p = m.predict(sc.transform(Xte))
keep = p > 20
te_df = df.loc[te].copy(); te_df["pred"] = p; te_df["keep"] = keep
print(f"TOTAL test: raw={yte.sum():+.0f} filt={yte[keep].sum():+.0f}")
rows = []
for (t, s), g in te_df.groupby(["ticker", "strategy"]):
    raw = g["pnl"].sum(); filt = g.loc[g["keep"], "pnl"].sum()
    rows.append((t, s, len(g), round(raw), round(filt), round(filt - raw)))
rows.sort(key=lambda r: r[5])
print(f"{'ticker':<8}{'strategy':<26}{'n':>5}{'raw':>9}{'filt':>9}{'delta':>9}")
for r in rows:
    print(f"{r[0]:<8}{r[1]:<26}{r[2]:>5}{r[3]:>+9}{r[4]:>+9}{r[5]:>+9}")

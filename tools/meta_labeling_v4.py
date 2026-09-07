#!/usr/bin/env python3
"""Meta-labeling v4: robustness check of the Ridge regressor from v3.
Tests: multiple train/test splits (different fractions), per-strategy breakdown,
shuffle-seed stability, and a naive-baseline comparison."""
import json, sys
import numpy as np
import pandas as pd
from pathlib import Path
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

SC = Path("/root/prop-desk/strategy_combine")
sys.path.insert(0, str(SC)); sys.path.insert(0, str(SC / "tools"))
from meta_labeling_v2 import collect  # noqa: E402

FCOLS = ["rsi14","atr_pct","trend","vol20","mom5","mom20","zscore20","hour_sin","hour_cos","side","sid"]


def split_by_frac(df, frac):
    tr, te = [], []
    for sid, g in df.groupby("sid"):
        s = int(len(g) * frac)
        tr += list(g.index[:s]); te += list(g.index[s:])
    return tr, te


def run_split(df, frac, alpha=10.0, thr=20.0):
    tr, te = split_by_frac(df, frac)
    Xtr = df.loc[tr, FCOLS].values; ytr = df.loc[tr, "pnl"].values
    Xte = df.loc[te, FCOLS].values; yte = df.loc[te, "pnl"].values
    sc = StandardScaler().fit(Xtr)
    m = Ridge(alpha=alpha).fit(sc.transform(Xtr), ytr)
    p = m.predict(sc.transform(Xte))
    keep = p > thr
    return dict(n_test=len(te), raw=round(yte.sum()), kept=int(keep.sum()),
                filt=round(yte[keep].sum() if keep.any() else 0.0))


def main():
    df, n_strats = collect()
    df = df.sort_values(["sid","order"]).reset_index(drop=True)
    print(f"pooled: {len(df)} trades, {n_strats} strategies\n")

    print("=== A) train-fraction robustness (thr=20) ===")
    for frac in (0.5, 0.6, 0.7, 0.8):
        r = run_split(df, frac)
        print(f"frac={frac}: test={r['n_test']} raw={r['raw']:+} kept={r['kept']} filt={r['filt']:+} delta={r['filt']-r['raw']:+}")

    print("\n=== B) alpha robustness (frac=0.7) ===")
    for a in (1.0, 10.0, 100.0):
        r = run_split(df, 0.7, alpha=a)
        print(f"alpha={a}: raw={r['raw']:+} kept={r['kept']} filt={r['filt']:+} delta={r['filt']-r['raw']:+}")

    print("\n=== C) per-strategy breakdown (frac=0.7, thr=20) ===")
    tr, te = split_by_frac(df, 0.7)
    Xtr = df.loc[tr, FCOLS].values; ytr = df.loc[tr, "pnl"].values
    Xte = df.loc[te, FCOLS].values; yte = df.loc[te, "pnl"].values
    sc = StandardScaler().fit(Xtr)
    m = Ridge(alpha=10.0).fit(sc.transform(Xtr), ytr)
    p = m.predict(sc.transform(Xte))
    keep = p > 20
    te_df = df.loc[te].copy(); te_df["pred"] = p; te_df["keep"] = keep
    for (t, s), g in te_df.groupby(["ticker","strategy"]):
        raw = g["pnl"].sum(); filt = g.loc[g["keep"], "pnl"].sum()
        print(f"{t:<7} {s:<20} n={len(g):>4} kept={int(g['keep'].sum()):>4} raw={raw:>+8.0f} filt={filt:>+8.0f} delta={filt-raw:>+8.0f}")

    print("\n=== D) shuffle-seed stability (5 seeds, frac=0.7) ===")
    rng = np.random.default_rng(0)
    deltas = []
    for seed in range(5):
        idx = rng.permutation(len(df))
        d2 = df.iloc[idx].sort_values(["sid","order"]).reset_index(drop=True)
        r = run_split(d2, 0.7)
        deltas.append(r["filt"] - r["raw"])
        print(f"seed={seed}: raw={r['raw']:+} filt={r['filt']:+} delta={r['filt']-r['raw']:+}")
    print(f"delta mean={np.mean(deltas):+.0f} std={np.std(deltas):.0f}")

    json.dump({"note": "v4 robustness"}, open(SC/"reports"/"meta_labeling_v4.json","w"))


if __name__ == "__main__":
    main()

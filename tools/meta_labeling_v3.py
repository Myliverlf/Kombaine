#!/usr/bin/env python3
"""Meta-labeling v3: predict trade P&L in RUB (regression), not win/loss.
Fixes v2 flaw: trend strategies earn from rare big winners; a win-rate
classifier cuts those tails. Regressor keeps trades with positive expected ₽."""
import json, sys
import numpy as np
import pandas as pd
from pathlib import Path
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

SC = Path("/root/prop-desk/strategy_combine")
sys.path.insert(0, str(SC))
sys.path.insert(0, str(SC / "tools"))
from meta_labeling_v2 import collect, TRAIN_FRAC  # noqa: E402


def main():
    df, n_strats = collect()
    print(f"pooled trades: {len(df)} from {n_strats} strategies")
    fcols = ["rsi14","atr_pct","trend","vol20","mom5","mom20","zscore20","hour_sin","hour_cos","side","sid"]
    df = df.sort_values(["sid","order"]).reset_index(drop=True)
    train_idx, test_idx = [], []
    for sid, g in df.groupby("sid"):
        n = len(g); s = int(n*TRAIN_FRAC)
        train_idx += list(g.index[:s]); test_idx += list(g.index[s:])
    Xtr = df.loc[train_idx, fcols].values; ytr = df.loc[train_idx, "pnl"].values
    Xte = df.loc[test_idx, fcols].values; yte = df.loc[test_idx, "pnl"].values
    print(f"train={len(Xtr)} test={len(Xte)} raw_test_pnl={yte.sum():+.0f}")

    mR = HistGradientBoostingRegressor(max_iter=200, max_depth=4, learning_rate=0.05,
                                       min_samples_leaf=30, l2_regularization=1.0, random_state=42)
    mR.fit(Xtr, ytr)
    pR = mR.predict(Xte)

    sc = StandardScaler().fit(Xtr)
    mG = Ridge(alpha=10.0).fit(sc.transform(Xtr), ytr)
    pG = mG.predict(sc.transform(Xte))

    raw = yte.sum()
    for name, p in (("HGB-reg", pR), ("Ridge-reg", pG)):
        for thr in (0, 20, 50, 100):
            keep = p > thr
            fp = yte[keep].sum() if keep.any() else 0.0
            n_kept = int(keep.sum())
            avg_kept = fp / n_kept if n_kept else 0
            print(f"{name} thr>{thr}₽: kept={n_kept}/{len(p)} pnl={fp:+.0f} (raw {raw:+.0f}) avg/trade={avg_kept:+.1f} delta={fp-raw:+.0f}")
    json.dump({"raw_test": round(raw),
               "hgb_reg": {str(t): {"kept": int((pR>t).sum()), "pnl": round(float(yte[pR>t].sum() if (pR>t).any() else 0))} for t in (0,20,50,100)},
               "ridge": {str(t): {"kept": int((pG>t).sum()), "pnl": round(float(yte[pG>t].sum() if (pG>t).any() else 0))} for t in (0,20,50,100)}},
              open(SC/"reports"/"meta_labeling_v3.json","w"), indent=2)


if __name__ == "__main__":
    main()

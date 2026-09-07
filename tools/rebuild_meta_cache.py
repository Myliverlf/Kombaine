#!/usr/bin/env python3
"""rebuild_meta_cache.py — refresh state/meta_filter_cache.json from the current
active signal pool. Run daily BEFORE rerank_signal_pool.py so pool scoring uses
fresh meta-filter uplift numbers.

Protocol (honest):
  - model = Ridge(alpha) on expected trade P&L, alpha chosen by TimeSeries CV
    on TRAIN (first 70% of each strategy's trades, chronological);
  - cache stores per-strategy raw/filtered P&L over ALL trades using that model.
Paper-only. No broker calls.
"""
import json, sys
import numpy as np
from pathlib import Path

SC = Path("/root/prop-desk/strategy_combine")
sys.path.insert(0, str(SC)); sys.path.insert(0, str(SC / "tools"))

from meta_filter import collect_trades, fit_and_evaluate, FCOLS  # noqa: E402
from sklearn.linear_model import Ridge  # noqa: E402
from sklearn.preprocessing import StandardScaler  # noqa: E402


def main():
    df, n = collect_trades()
    if df.empty:
        print("NO_TRADES — cache not touched"); return
    res = fit_and_evaluate(df)
    sc = StandardScaler().fit(df[FCOLS].values)  # note: fit on all for cache predictions
    # refit final model on ALL pooled trades (cache = current-state scoring aid)
    model = Ridge(alpha=res["alpha"]).fit(sc.transform(df[FCOLS].values), df["pnl"].values)
    pred = model.predict(sc.transform(df[FCOLS].values))
    keep = pred > res["threshold"]
    df = df.assign(keep=keep)
    out = {}
    for (t, s), g in df.groupby(["ticker", "strategy"]):
        raw = float(g["pnl"].sum()); filt = float(g.loc[g["keep"], "pnl"].sum())
        out[f"{t}::{s}"] = {"n": len(g), "kept": int(g["keep"].sum()),
                            "raw": round(raw), "filt": round(filt),
                            "delta": round(filt - raw)}
    out["_model"] = {"alpha": res["alpha"], "threshold": res["threshold"],
                     "test_delta": res["delta"], "n_trades": len(df), "n_strats": n,
                     "rebuilt_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ")}
    from state_io import atomic_write_json  # FIX(audit): атомарная запись кэша
    atomic_write_json(SC / "state" / "meta_filter_cache.json", out)
    print(f"META_CACHE rebuilt: {n} strategies, {len(df)} trades, test_delta={res['delta']:+}₽")


if __name__ == "__main__":
    main()

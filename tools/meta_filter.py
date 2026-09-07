#!/usr/bin/env python3
"""meta_filter.py — production meta-labeling filter (Ridge on expected trade P&L).

Honest protocol:
  - alpha chosen by TimeSeries CV on the TRAIN portion only;
  - test segment evaluated exactly once with the chosen model;
  - model persisted to state/meta_filter_model.json for reuse in scoring.

Paper-only. No broker calls.
"""
import json, sys
import numpy as np
import pandas as pd
from pathlib import Path
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import TimeSeriesSplit, cross_val_score

SC = Path("/root/prop-desk/strategy_combine")
sys.path.insert(0, str(SC)); sys.path.insert(0, str(SC / "tools"))

FCOLS = ["rsi14","atr_pct","trend","vol20","mom5","mom20","zscore20","hour_sin","hour_cos","side","sid"]
MODEL_PATH = SC / "state" / "meta_filter_model.json"
TRAIN_FRAC = 0.70
THR = 20.0


def collect_trades():
    # 2026-09-05: use the FULL registry (waitlist+watchlist+pool) so new
    # candidates get meta-filter scores too, not just the incumbent pool.
    from meta_labeling_v2 import collect_from_registry, collect
    try:
        df, n = collect_from_registry()
        if df.empty:
            raise ValueError("empty")
    except Exception:
        df, n = collect()
    df = df.sort_values(["sid","order"]).reset_index(drop=True)
    return df, n


def fit_and_evaluate(df):
    tr, te = [], []
    for sid, g in df.groupby("sid"):
        s = int(len(g) * TRAIN_FRAC)
        tr += list(g.index[:s]); te += list(g.index[s:])
    Xtr = df.loc[tr, FCOLS].values; ytr = df.loc[tr, "pnl"].values
    Xte = df.loc[te, FCOLS].values; yte = df.loc[te, "pnl"].values

    # alpha via TimeSeries CV on TRAIN ONLY
    sc = StandardScaler().fit(Xtr)
    Xtr_s = sc.transform(Xtr)
    best_alpha, best_score = None, -np.inf
    for a in (1.0, 3.0, 10.0, 30.0, 100.0, 300.0):
        tscv = TimeSeriesSplit(n_splits=4)
        scores = cross_val_score(Ridge(alpha=a), Xtr_s, ytr, cv=tscv, scoring="neg_mean_squared_error")
        ms = scores.mean()
        if ms > best_score:
            best_score, best_alpha = ms, a
    model = Ridge(alpha=best_alpha).fit(Xtr_s, ytr)

    p = model.predict(sc.transform(Xte))
    keep = p > THR
    raw = float(yte.sum()); filt = float(yte[keep].sum()) if keep.any() else 0.0
    return dict(alpha=best_alpha, n_train=len(tr), n_test=len(te),
                kept=int(keep.sum()), raw=round(raw), filt=round(filt),
                delta=round(filt - raw),
                scaler_mean=sc.mean_.tolist(), scaler_scale=sc.scale_.tolist(),
                coef=model.coef_.tolist(), intercept=float(model.intercept_),
                threshold=THR, features=FCOLS)


def save_model(res):
    from state_io import atomic_write_json  # FIX(audit): атомарная запись модели
    atomic_write_json(MODEL_PATH, res)
    print("model saved:", MODEL_PATH)


def load_model():
    if not MODEL_PATH.exists():
        return None
    return json.loads(MODEL_PATH.read_text())


def predict_entry_pnl(model_art, feat_dict):
    """Runtime filter: expected P&L for one candidate entry. feat_dict has FCOLS keys."""
    x = np.array([[feat_dict[c] for c in model_art["features"]]])
    x = (x - np.array(model_art["scaler_mean"])) / np.array(model_art["scaler_scale"])
    return float(np.dot(x, model_art["coef"]) + model_art["intercept"])


if __name__ == "__main__":
    df, n = collect_trades()
    print(f"pooled: {len(df)} trades, {n} strategies")
    res = fit_and_evaluate(df)
    # карта sid: forward_test обязан использовать ТО ЖЕ значение sid, что обучение
    # (раньше forward брал hash((t, strat)) % 100 — признак не совпадал с обучающим)
    res["sid_map"] = {f"{t}::{s}": int(sid) for t, s, sid in
                      df[["ticker", "strategy", "sid"]].drop_duplicates().itertuples(index=False, name=None)}
    print(json.dumps({k: v for k, v in res.items() if k not in ("coef","scaler_mean","scaler_scale","features","sid_map")}, indent=2))
    save_model(res)

#!/usr/bin/env python3
"""Meta-labeling v2: POOLED model across all strategies + walk-forward retrain.
v1 lesson: per-strategy models overfit (70-230 trades each). Pool everything,
add strategy identity features, retrain rolling, compare honestly on test."""
import json, sys
import numpy as np
import pandas as pd
from pathlib import Path
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

FL = Path("/root/prop-desk/futures_lab")
SC = Path("/root/prop-desk/strategy_combine")
sys.path.insert(0, str(SC)); sys.path.insert(0, str(FL))
from futures_lab import run_backtest, _synthetic_spec_for_file, atr  # noqa: E402

DATA = FL / "artifacts" / "tinkoff_futures_data"
CAP, COMM, SLIP = 20000.0, 5.0, 1.0
TRAIN_FRAC = 0.70


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + (up / dn.replace(0, np.nan)))


def build_features(df):
    c = df["close"]
    f = pd.DataFrame(index=df.index)
    f["rsi14"] = rsi(c)
    f["atr_pct"] = atr(df, 14) / c
    f["trend"] = (ema(c, 20) - ema(c, 50)) / c
    logret = np.log(c / c.shift(1))
    f["vol20"] = logret.rolling(20).std()
    f["mom5"] = c / c.shift(5) - 1
    f["mom20"] = c / c.shift(20) - 1
    m20 = c.rolling(20).mean(); s20 = c.rolling(20).std()
    f["zscore20"] = (c - m20) / s20
    t = pd.to_datetime(df["time"])
    f["hour_sin"] = np.sin(2*np.pi*t.dt.hour/24)
    f["hour_cos"] = np.cos(2*np.pi*t.dt.hour/24)
    return f


def collect():
    pool = json.load(open(SC / "state/signal_pool.json"))["strategies"]
    active = [(k, v) for k, v in pool.items() if v.get("status") == "active_signal_pool"]
    return _collect_from(active)


def collect_from_registry():
    """All live-ish registry records (waitlist + watchlist + active pool),
    so meta-filter scoring covers NEW candidates too, not just the old pool."""
    sys.path.insert(0, str(SC / "code"))
    from strategy_registry import StrategyRegistry, STATUS_WAITLIST, STATUS_ACTIVE_WATCHLIST, STATUS_ACTIVE_SIGNAL_POOL  # noqa: E402
    reg = StrategyRegistry(SC / "state/strategy_registry.json")
    pairs = []
    seen = set()
    for rec in reg.records():
        if rec.status not in (STATUS_WAITLIST, STATUS_ACTIVE_WATCHLIST, STATUS_ACTIVE_SIGNAL_POOL):
            continue
        key = (rec.ticker, rec.strategy, json.dumps(rec.params, sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        pairs.append((f"{rec.ticker}__x__1h", {"ticker": rec.ticker, "strategy": rec.strategy, "params": rec.params or {}}))
    return _collect_from(pairs)


def _collect_from(active):
    all_rows = []
    strat_ids = {}
    for key, v in active:
        ticker, strat, params = v["ticker"], v["strategy"], v.get("params") or {}
        p = DATA / f"{ticker}_365d_1h_continuous.csv"
        if not p.exists():
            p = DATA / f"{ticker}_60d_1h_continuous.csv"
            if not p.exists(): continue
        df = pd.read_csv(p).reset_index(drop=True)
        spec = _synthetic_spec_for_file(ticker)
        metrics, trades, eq = run_backtest(df, spec, strat, params,
            initial_cash=CAP, contracts=1, max_contracts=1,
            commission_per_contract=COMM, slippage_bps=SLIP, debug_only=True)
        if len(trades) < 30: continue
        feats = build_features(df)
        tidx = {str(t): i for i, t in enumerate(df["time"])}
        sid = strat_ids.setdefault((ticker, strat), len(strat_ids))
        for tr in trades:
            i = tidx.get(str(tr.entry_time))
            if i is None or i < 1: continue
            fv = feats.iloc[i-1]
            if fv.isna().any(): continue
            r = fv.to_dict()
            r["side"] = 1 if tr.side == "long" else 0
            r["sid"] = sid
            r["pnl"] = tr.pnl
            r["order"] = i / len(df)  # chronological position within dataset
            r["ticker"] = ticker; r["strategy"] = strat
            all_rows.append(r)
    return pd.DataFrame(all_rows), len(strat_ids)


def main():
    df, n_strats = collect()
    print(f"pooled trades: {len(df)} from {n_strats} strategies")
    fcols = ["rsi14","atr_pct","trend","vol20","mom5","mom20","zscore20","hour_sin","hour_cos","side","sid"]
    df = df.sort_values(["sid","order"]).reset_index(drop=True)

    # walk-forward: within each strategy's trade sequence, train on first 70%, test on last 30%
    train_mask = df.groupby("sid").cumcount() < (df.groupby("sid")["sid"].transform("size") * TRAIN_FRAC).astype(int)
    # simpler robust split
    train_idx, test_idx = [], []
    for sid, g in df.groupby("sid"):
        n = len(g); s = int(n*TRAIN_FRAC)
        train_idx += list(g.index[:s]); test_idx += list(g.index[s:])
    Xtr = df.loc[train_idx, fcols].values; ytr = (df.loc[train_idx, "pnl"] > 0).astype(int).values
    Xte = df.loc[test_idx, fcols].values; yte = (df.loc[test_idx, "pnl"] > 0).astype(int).values
    pnl_te = df.loc[test_idx, "pnl"].values
    print(f"train={len(Xtr)} test={len(Xte)} base_winrate={yte.mean()*100:.1f}%")

    results = {}
    # Model A: HistGradientBoosting (handles small data better than GBM v1)
    mA = HistGradientBoostingClassifier(max_iter=150, max_depth=4, learning_rate=0.05,
                                        min_samples_leaf=30, l2_regularization=1.0, random_state=42)
    mA.fit(Xtr, ytr)
    pA = mA.predict_proba(Xte)[:, 1]
    # Model B: logistic regression (baseline, low variance)
    sc = StandardScaler().fit(Xtr)
    mB = LogisticRegression(C=0.3, max_iter=2000)
    mB.fit(sc.transform(Xtr), ytr)
    pB = mB.predict_proba(sc.transform(Xte))[:, 1]

    raw = pnl_te.sum()
    results["raw"] = raw
    for name, p in (("HGB", pA), ("LogReg", pB)):
        for thr in (0.5, 0.55, 0.6):
            keep = p > thr
            fp = pnl_te[keep].sum() if keep.any() else 0.0
            wr = yte[keep].mean()*100 if keep.any() else 0.0
            results[f"{name}@{thr}"] = (int(keep.sum()), round(fp), round(wr,1), round(fp-raw))
            print(f"{name} thr={thr}: kept={keep.sum()}/{len(p)} pnl={fp:+.0f} (raw {raw:+.0f}) wr={wr:.1f}% (base {yte.mean()*100:.1f}%) delta={fp-raw:+.0f}")
    json.dump({"raw": round(raw), "results": {k: v for k, v in results.items() if k != "raw"}},
              open(SC/"reports"/"meta_labeling_v2.json","w"), indent=2)
    print(f"\nRAW test total: {raw:+.0f}₽")


if __name__ == "__main__":
    main()

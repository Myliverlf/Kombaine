#!/usr/bin/env python3
"""Meta-labeling experiment (Lopez de Prado style). Paper-only, no live orders.

For each active pool strategy:
  1. Run real backtest on 365d data -> trades.
  2. Build entry-bar features (only past data, no leakage).
  3. Train GradientBoostingClassifier on first 70% of trades (chronological),
     label = trade PnL > 0.
  4. On held-out last 30%: compare raw signal vs model-filtered signal
     (keep trade only if P(win) > threshold).

Honest evaluation: model never sees test trades; features never see future bars.
"""
import json, sys
import numpy as np
import pandas as pd
from pathlib import Path
from sklearn.ensemble import GradientBoostingClassifier

FL = Path("/root/prop-desk/futures_lab")
SC = Path("/root/prop-desk/strategy_combine")
sys.path.insert(0, str(SC)); sys.path.insert(0, str(FL))
from futures_lab import run_backtest, build_signal, _synthetic_spec_for_file, atr  # noqa: E402

DATA = FL / "artifacts" / "tinkoff_futures_data"
CAP = 20000.0
COMM = 5.0
SLIP = 1.0
THRESHOLD = 0.55
TRAIN_FRAC = 0.70


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def build_features(df):
    """Feature matrix aligned to df index; every value uses data up to bar i only."""
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
    f["hour"] = t.dt.hour
    f["dow"] = t.dt.dayofweek
    return f


def main():
    pool = json.load(open(SC / "state/signal_pool.json"))["strategies"]
    active = [(k, v) for k, v in pool.items() if v.get("status") == "active_signal_pool"]

    feat_cols = ["rsi14", "atr_pct", "trend", "vol20", "mom5", "mom20", "zscore20", "hour", "dow", "side"]
    results = []
    for key, v in active:
        ticker, strat, params = v["ticker"], v["strategy"], v.get("params") or {}
        tf = key.split("__")[2]
        p = DATA / f"{ticker}_365d_{tf}_continuous.csv"
        if not p.exists():
            p60 = DATA / f"{ticker}_60d_{tf}_continuous.csv"
            if not p60.exists():
                results.append((ticker, strat, "NO_DATA", None)); continue
            p = p60
        df = pd.read_csv(p).reset_index(drop=True)
        spec = _synthetic_spec_for_file(ticker)
        metrics, trades, eq = run_backtest(df, spec, strat, params,
            initial_cash=CAP, contracts=1, max_contracts=1,
            commission_per_contract=COMM, slippage_bps=SLIP, debug_only=True)
        if len(trades) < 30:
            results.append((ticker, strat, f"TOO_FEW_TRADES({len(trades)})", None)); continue

        feats = build_features(df)
        time_idx = {str(t): i for i, t in enumerate(df["time"])}
        rows = []
        for tr in trades:
            i = time_idx.get(str(tr.entry_time))
            if i is None or i < 1:
                continue
            fv = feats.iloc[i - 1]  # bar BEFORE entry — no leakage
            if fv.isna().any():
                continue
            r = fv.to_dict()
            r["side"] = 1 if tr.side == "long" else 0
            r["pnl"] = tr.pnl
            rows.append(r)
        if len(rows) < 30:
            results.append((ticker, strat, f"TOO_FEW_FEAT({len(rows)})", None)); continue

        X = pd.DataFrame(rows)[feat_cols].values
        y = np.array([1 if r["pnl"] > 0 else 0 for r in rows])
        pnl = np.array([r["pnl"] for r in rows])
        n = len(rows); split = int(n * TRAIN_FRAC)
        Xtr, ytr = X[:split], y[:split]
        Xte = X[split:]; pnl_te = pnl[split:]; y_te = y[split:]

        if ytr.sum() < 5 or (1 - ytr).sum() < 5:
            results.append((ticker, strat, "DEGENERATE_LABELS", None)); continue

        model = GradientBoostingClassifier(n_estimators=100, max_depth=3,
                                           learning_rate=0.05, random_state=42)
        model.fit(Xtr, ytr)
        proba = model.predict_proba(Xte)[:, 1]
        keep = proba > THRESHOLD

        raw_pnl = pnl_te.sum()
        filt_pnl = pnl_te[keep].sum() if keep.any() else 0.0
        raw_wr = y_te.mean() * 100
        filt_wr = (y_te[keep].mean() * 100) if keep.any() else 0.0
        results.append((ticker, strat, "OK", {
            "test_trades": len(pnl_te), "kept": int(keep.sum()),
            "raw_pnl": round(raw_pnl), "filt_pnl": round(filt_pnl),
            "raw_wr": round(raw_wr, 1), "filt_wr": round(filt_wr, 1),
            "improve": round(filt_pnl - raw_pnl),
        }))

    print(f"{'TICKER':<7} {'STRATEGY':<20} {'STATUS':<16} testTr kept rawPnL filtPnL rawWR% filtWR% delta")
    tot_raw = tot_filt = 0
    for t, s, st, d in results:
        if d:
            print(f"{t:<7} {s:<20} {st:<16} {d['test_trades']:>6} {d['kept']:>4} {d['raw_pnl']:>7} {d['filt_pnl']:>7} {d['raw_wr']:>6} {d['filt_wr']:>7} {d['improve']:>+7}")
            tot_raw += d["raw_pnl"]; tot_filt += d["filt_pnl"]
        else:
            print(f"{t:<7} {s:<20} {st:<16}")
    print(f"\nTOTAL test-segment: raw={tot_raw}₽  meta-filtered={tot_filt}₽  delta={tot_filt-tot_raw:+}₽")
    json.dump([{"ticker": t, "strategy": s, "status": st, "detail": d} for t, s, st, d in results],
              open(SC / "reports" / "meta_labeling_result.json", "w"), ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()

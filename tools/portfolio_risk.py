#!/usr/bin/env python3
"""tools/portfolio_risk.py — портфельный риск-движок (hardening v2 §10).

«5 стратегий не считаются диверсифицированными просто потому, что имеют разные имена.»

Для каждой стратегии строит RISK FINGERPRINT:
- side exposure (long/short доля сделок)
- volatility exposure (реализованная вола дневных доходностей, аннуализированная)
- trend/reversion (корреляция дневного PnL с дневным импульсом рынка)
- market beta (регрессия дневных доходностей на IMOEX = прокси широкого рынка)
- drawdown overlap (доля дней, когда обе стратегии в просадке одновременно)
- tail-loss overlap (условная вероятность: худшие 5% дней рынка → сколько своих худших)
- regime dependency (распределение PnL по режимам trend/flat × high/low vol)
- margin usage, liquidity dependency (объём баров входа), trading-hour exposure

Портфельный уровень:
- risk contribution каждой стратегии (компонентная декомпозиция портфельной волатильности)
- концентрации: инструментальная, маржинальная, режимная, хвостовая
- VERDICT: diversified / concentrated

Paper-only, детерминированно, брокера не зовёт.
Тесты: tests/test_portfolio_risk.py
"""
from __future__ import annotations
import json, sys
from pathlib import Path

import numpy as np
import pandas as pd

SC = Path("/root/prop-desk/strategy_combine")
FL = Path("/root/prop-desk/futures_lab")
sys.path.insert(0, str(SC)); sys.path.insert(0, str(FL)); sys.path.insert(0, str(SC / "tools"))

CAP, COMM, SLIP = 20000.0, 5.0, 1.0
DATA = FL / "artifacts/tinkoff_futures_data"
SCHEMA_VERSION = "portfolio-risk-v1"
BENCH = "IMOEX"          # прокси широкого рынка FORTS
TAIL_Q = 0.05            # хвост = худшие 5% дней
REGIME_COLS = ("trend_state", "vol_state")


def _daily_returns_for(cand: dict, n: int, cutoff_days=0):
    """Дневные ДОХОДНОСТИ (в долях капитала) кандидата + его сделки."""
    from futures_lab import _synthetic_spec_for_file
    from engines.register import register_genomes
    from engines.exit_engine import run_candidate
    register_genomes()
    p = DATA / f"{cand['ticker']}_365d_1h_continuous.csv"
    if not p.exists():
        return None, None, None
    df = pd.read_csv(p).reset_index(drop=True)
    if cutoff_days:
        t = pd.to_datetime(df["time"])
        df = df[t < t.max() - pd.Timedelta(days=cutoff_days)].reset_index(drop=True)
    cc = dict(cand); cc["strategy"] = cc.get("strategy") or cc.get("name")
    spec = _synthetic_spec_for_file(cc["ticker"])
    _, trades, eq = run_candidate(df, spec, cc, n, cap=CAP, comm=COMM, slip_bps=SLIP)
    if eq is None or len(trades) < 5:
        return None, None, None
    daily_eq = pd.Series(eq.values, index=pd.to_datetime(eq.index)).resample("D").last().ffill().dropna()
    rets = (daily_eq.diff().fillna(daily_eq.iloc[0] - CAP) / CAP)
    return rets, trades, df


def _bench_returns():
    from futures_lab import add_market_regimes
    p = DATA / f"{BENCH}_365d_1h_continuous.csv"
    if not p.exists():
        return None, None
    df = pd.read_csv(p).reset_index(drop=True)
    df = add_market_regimes(df)
    daily = pd.Series(df["close"].values, index=pd.to_datetime(df["time"])).resample("D").last().ffill().dropna()
    rets = daily.pct_change().dropna()
    regimes = df.set_index(pd.to_datetime(df["time"]))[[*REGIME_COLS]].resample("D").last().ffill()
    return rets, regimes


def fingerprint(cand: dict, n: int, cutoff_days=0) -> dict:
    rets, trades, df = _daily_returns_for(cand, n, cutoff_days)
    if rets is None:
        return {"error": "insufficient data", "ticker": cand.get("ticker")}
    bench, regimes = _bench_returns()

    # side exposure
    longs = sum(1 for t in trades if str(getattr(t, "side", "")).lower().startswith("long"))
    fp = {"schema_version": SCHEMA_VERSION,
          "ticker": cand["ticker"], "strategy": cand.get("strategy") or cand.get("name"),
          "contracts": n, "n_trades": len(trades),
          "long_frac": round(longs / max(1, len(trades)), 3),
          "short_frac": round(1 - longs / max(1, len(trades)), 3),
          "vol_ann": round(float(rets.std(ddof=1) * np.sqrt(252)), 4),
          "pnl_sum": round(float(rets.sum() * CAP))}

    # market beta + trend/reversion
    if bench is not None:
        idx = rets.index.intersection(bench.index)
        if len(idx) > 30:
            x = bench.loc[idx].values; y = rets.loc[idx].values
            var = float(np.var(x, ddof=1))
            fp["market_beta"] = round(float(np.cov(x, y, ddof=1)[0, 1] / var), 3) if var > 1e-15 else 0.0
            fp["bench_corr"] = round(float(np.corrcoef(x, y)[0, 1]), 3) if var > 1e-15 else 0.0
            # trend/reversion: корреляция PnL с НАКОПЛЕННЫМ импульсом рынка за 5 дней
            mom5 = pd.Series(x, index=idx).rolling(5).sum().shift(1).dropna()
            yy = pd.Series(y, index=idx).loc[mom5.index]
            if len(mom5) > 30 and mom5.std() > 1e-12 and yy.std() > 1e-12:
                r = float(np.corrcoef(mom5.values, yy.values)[0, 1])
                fp["style"] = "trend" if r > 0.15 else ("reversion" if r < -0.15 else "neutral")
                fp["style_corr"] = round(r, 3)
            # tail overlap с рынком
            tail_days = bench.loc[idx] <= np.quantile(bench.loc[idx], TAIL_Q)
            own_tail = rets.loc[idx] <= np.quantile(rets.loc[idx], TAIL_Q)
            if tail_days.sum() > 0:
                fp["tail_hit_rate"] = round(float((own_tail & tail_days).sum() / tail_days.sum()), 3)

    # regime dependency (PnL по режимам рынка)
    if regimes is not None:
        idx2 = rets.index.intersection(regimes.index)
        if len(idx2) > 30:
            pnl_by = {}
            for ts in regimes.loc[idx2, "trend_state"].unique():
                for vs in regimes.loc[idx2, "vol_state"].unique():
                    m = (regimes.loc[idx2, "trend_state"] == ts) & (regimes.loc[idx2, "vol_state"] == vs)
                    if m.sum() > 3:
                        pnl_by[f"{ts}_{vs}"] = round(float(rets.loc[idx2][m].sum() * CAP))
            fp["pnl_by_regime"] = pnl_by
            if pnl_by:
                tot = sum(abs(v) for v in pnl_by.values()) or 1.0
                fp["regime_concentration"] = round(max(abs(v) for v in pnl_by.values()) / tot, 3)

    # liquidity/hour exposure
    if df is not None and "time" in df.columns:
        entry_hours = pd.DatetimeIndex(pd.to_datetime(
            [getattr(t, "entry_time", None) for t in trades]).dropna())
        if len(entry_hours):
            vc = pd.Series(entry_hours.hour).value_counts(normalize=True)
            fp["top_hour_frac"] = round(float(vc.iloc[0]), 3)
    return fp


def portfolio_risk(pool_path=None, cutoff_days=0) -> dict:
    pool = json.loads(Path(pool_path or SC / "state/stable_pool.json").read_text())
    sel = pool.get("selected") or []
    fps, rets_map = [], {}
    for s in sel:
        cand = {"ticker": s["ticker"], "strategy": s.get("strategy") or s.get("name")}
        fp = fingerprint(cand, int(s.get("contracts", 1)), cutoff_days)
        fps.append(fp)
        r, _, _ = _daily_returns_for(cand, int(s.get("contracts", 1)), cutoff_days)
        if r is not None:
            rets_map[cand["strategy"]] = r

    out = {"schema_version": SCHEMA_VERSION, "ts": pd.Timestamp.utcnow().isoformat(),
           "fingerprints": fps}

    # ── портфельные агрегаты ──
    if len(rets_map) >= 2:
        mat = pd.DataFrame(rets_map).dropna()
        if len(mat) > 30:
            # корреляционная матрица + drawdown/tail overlap попарно
            corr = mat.corr().round(3)
            out["corr_matrix"] = json.loads(corr.to_json())
            pairs = []
            names = list(mat.columns)
            for i in range(len(names)):
                for j in range(i + 1, len(names)):
                    a, b = mat[names[i]], mat[names[j]]
                    # drawdown overlap: обе ниже своего кум-максимума одновременно
                    dda = a.cumsum() < a.cumsum().cummax()
                    ddb = b.cumsum() < b.cumsum().cummax()
                    dd_ov = float((dda & ddb).sum() / max(1, (dda | ddb).sum()))
                    # tail-loss overlap: P(b в худших 5% | a в худших 5%)
                    ta = a <= a.quantile(TAIL_Q); tb = b <= b.quantile(TAIL_Q)
                    tail_ov = float((ta & tb).sum() / max(1, ta.sum()))
                    pairs.append({"pair": [names[i], names[j]],
                                  "corr": float(corr.iloc[i, j]),
                                  "dd_overlap": round(dd_ov, 3),
                                  "tail_overlap": round(tail_ov, 3)})
            out["pairwise"] = pairs

            # risk contribution: компонентная декомпозиция волатильности портфеля
            w = np.ones(len(names)) / len(names)   # равновзвешенный портфель доходностей
            cov = mat.cov().values
            port_var = float(w @ cov @ w)
            if port_var > 1e-18:
                mcr = cov @ w                       # маржинальный вклад
                rc = w * mcr / port_var             # компонентный вклад (сумма = 1)
                out["risk_contribution"] = {n: round(float(v), 3) for n, v in zip(names, rc)}
            out["portfolio_vol_ann"] = round(float(np.sqrt(port_var) * np.sqrt(252)), 4)

    # ── концентрации ──
    tick = {}
    for fp in fps:
        tick[fp.get("ticker")] = tick.get(fp.get("ticker"), 0) + 1
    out["instrument_concentration"] = {
        "per_ticker": tick,
        "max_share": round(max(tick.values()) / max(1, len(fps)), 3) if tick else None}
    mgn = {}
    for s in sel:
        mgn[s["ticker"]] = mgn.get(s["ticker"], 0) + float(s.get("margin_total", 0) or 0)
    tot_mgn = sum(mgn.values()) or 1.0
    out["margin_concentration"] = {k: round(v / tot_mgn, 3) for k, v in mgn.items()}
    regimes = [fp.get("regime_concentration") for fp in fps if fp.get("regime_concentration")]
    out["max_regime_concentration"] = round(max(regimes), 3) if regimes else None
    tails = [fp.get("tail_hit_rate", 0) for fp in fps]
    out["max_tail_hit_rate"] = round(max(tails), 3) if tails else None

    # ── VERDICT ──
    warnings = []
    if out["instrument_concentration"]["max_share"] and out["instrument_concentration"]["max_share"] >= 0.5:
        warnings.append("instrument: >=50% стратегий на одном тикере")
    rc = out.get("risk_contribution") or {}
    if rc and max(rc.values()) >= 0.7:
        warnings.append(f"risk contribution: {max(rc, key=rc.get)} даёт >=70% портфельного риска")
    pw = out.get("pairwise") or []
    if any(p["corr"] >= 0.7 for p in pw):
        warnings.append("correlation: есть пара с corr>=0.7 (анти-клон должен был её отсеять)")
    if any(p["tail_overlap"] >= 0.5 for p in pw):
        warnings.append("tail: стратегии теряют ОДНОВРЕМЕННО (tail overlap>=0.5)")
    if out.get("max_regime_concentration") and out["max_regime_concentration"] >= 0.8:
        warnings.append("regime: >=80% PnL стратегии из одного рыночного режима")
    out["warnings"] = warnings
    out["verdict"] = "CONCENTRATED" if warnings else "DIVERSIFIED"
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default=None)
    ap.add_argument("--cutoff-days", type=int, default=0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    res = portfolio_risk(args.pool, args.cutoff_days)
    print(json.dumps(res, indent=1, ensure_ascii=False, default=str))
    if args.out:
        from state_io import atomic_write_json
        atomic_write_json(Path(args.out), res)

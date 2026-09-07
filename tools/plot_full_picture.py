#!/usr/bin/env python3
"""plot_full_picture.py — ПОЛНАЯ КАРТИНА по стратегиям комбайна.

По запросу Апостола (2026-09-05): топовые стратегии вообще + общий профиль
equity «если всё вложить в один индекс».

Что делает:
  1. Берёт ВСЕХ кандидатов из state/engine_candidates.json (зоопарк/генетика/
     моментум/нейронки) + принятый пул stable_pool.json.
  2. Честный бэктест каждого (run_candidate, комиссии 5₽/контракт, слип 1bp,
     капитал 20k) на последних 365 днях данных.
  3. Топ-8 по calmar (pnl>0, >=15 сделок) — отдельные hairline-карточки.
  4. ОБЩИЙ ПРОФИЛЬ: сумма equity ВСЕХ плюсовых стратегий = «весь комбайн
     как один индекс» + отдельной жирной линией — принятый боевой пул.
  5. Всё через lieflat_render.render_equity_report (стиль lieflat-charts Mono).

Запуск: python3 tools/plot_full_picture.py [--top 8]
"""
from __future__ import annotations
import json, sys, argparse
from pathlib import Path
import numpy as np
import pandas as pd

SC = Path("/root/prop-desk/strategy_combine")
FL = Path("/root/prop-desk/futures_lab")
sys.path.insert(0, str(SC)); sys.path.insert(0, str(FL)); sys.path.insert(0, str(SC / "tools"))

DATA = FL / "artifacts" / "tinkoff_futures_data"
CAP = 20000.0
COMM = 5.0
SLIP = 1.0


def load_365(ticker):
    p = DATA / f"{ticker}_365d_1h_continuous.csv"
    if p.exists():
        return pd.read_csv(p).reset_index(drop=True)
    p2 = DATA / f"{ticker}_1095d_1h_continuous.csv"
    if p2.exists():
        df = pd.read_csv(p2)
        t = pd.to_datetime(df["time"])
        return df[t >= t.max() - pd.Timedelta(days=365)].reset_index(drop=True)
    return None


def equity_of(cand, df, contracts=1):
    from futures_lab import _synthetic_spec_for_file
    from engines.exit_engine import run_candidate
    c = dict(cand)
    c["strategy"] = c.get("strategy") or c.get("name")
    try:
        spec = _synthetic_spec_for_file(c["ticker"])
        m, trades, eq = run_candidate(df, spec, c, contracts, cap=CAP, comm=COMM, slip_bps=SLIP)
    except Exception:
        return None
    if eq is None or len(eq) < 50:
        return None
    eqs = pd.Series(eq.values, index=pd.to_datetime(eq.index))
    daily = (eqs.resample("D").last().ffill().dropna() - CAP)
    pnl = float(daily.iloc[-1]); dd = float((daily.cummax() - daily).max())
    cal = pnl / dd if dd > 0 else (99 if pnl > 0 else 0)
    months = daily.resample("ME").last().diff().dropna()
    pos_m = int((months > 0).sum()); n_m = max(len(months), 1)
    return {"name": f"{c['ticker']} {c['strategy']}", "engine": c.get("engine", "pool"),
            "ticker": c["ticker"], "daily": daily, "pnl": round(pnl), "dd": round(dd),
            "calmar": round(cal, 2), "trades": len(trades), "pos_months": f"{pos_m}/{n_m}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=8)
    args = ap.parse_args()

    from engines.register import register_genomes
    register_genomes()

    results, seen = [], set()
    # принятый пул — с его контрактами
    pool = json.loads((SC / "state/stable_pool.json").read_text())
    for s in pool.get("selected", []):
        key = (s["ticker"], s["strategy"])
        seen.add(key)
        df = load_365(s["ticker"])
        if df is None: continue
        r = equity_of({"ticker": s["ticker"], "strategy": s["strategy"],
                       "params": s.get("params") or {}, "risk": s.get("risk") or {},
                       "exits": s.get("exits") or {}, "engine": "ПУЛ"},
                      df, int(s.get("contracts") or 1))
        if r: r["in_pool"] = True; results.append(r)

    # все кандидаты движков
    cands = json.loads((SC / "state/engine_candidates.json").read_text())["candidates"]
    for c in cands:
        key = (c["ticker"], c.get("strategy") or c["name"])
        if key in seen: continue
        seen.add(key)
        df = load_365(c["ticker"])
        if df is None: continue
        r = equity_of(c, df)
        if r: r["in_pool"] = False; results.append(r)

    pos = [r for r in results if r["pnl"] > 0 and r["trades"] >= 15]
    pos.sort(key=lambda r: (-r["calmar"], -r["pnl"]))
    print(f"всего стратегий с бэктестом: {len(results)}, плюсовых: {len(pos)}\n")

    # ---- топ-N карточки
    top = pos[:args.top]
    series = [{"name": f"{r['name']}  [{r['engine']}] calmar {r['calmar']} · {r['pos_months']} мес+ · {r['trades']} сделок"
               + ("  ★ В ПУЛЕ" if r["in_pool"] else ""),
               "contracts": 1, "src": r["engine"].upper(),
               "points": list(zip(r["daily"].index, r["daily"].values))} for r in top]

    # ---- общий профиль: ВСЕ плюсовые стратегии как один индекс
    frame = pd.concat([r["daily"] for r in pos], axis=1, join="outer").ffill().fillna(0.0)
    all_index = frame.sum(axis=1)
    # только боевой пул жирной линией
    pool_frame = pd.concat([r["daily"] for r in results if r.get("in_pool")], axis=1,
                           join="outer").ffill().fillna(0.0)
    pool_curve = pool_frame.sum(axis=1)
    dd_all = float((all_index.cummax() - all_index).max())
    dd_pool = float((pool_curve.cummax() - pool_curve).max())

    combined = {"name": f"ОБЩИЙ ПРОФИЛЬ — весь комбайн как один индекс ({len(pos)} плюсовых стратегий)",
                "sub": (f"все вместе: {all_index.iloc[-1]:+,.0f} ₽ · maxDD {dd_all:,.0f} ₽ | "
                        f"боевой пул ({len(pool_frame.columns)} страт): {pool_curve.iloc[-1]:+,.0f} ₽ · maxDD {dd_pool:,.0f} ₽ | "
                        f"капитал 20 000 ₽, комиссии учтены"),
                "points": list(zip(all_index.index, all_index.values))}
    # пул — дополнительной серией в конец (отрисуется карточкой)
    series.append({"name": f"БОЕВОЙ ПУЛ (принятые стратегии, их контракты) — {pool_curve.iloc[-1]:+,.0f} ₽/год",
                   "contracts": 1, "src": "STABLE POOL",
                   "points": list(zip(pool_curve.index, pool_curve.values))})

    from lieflat_render import render_equity_report
    out = SC / "reports" / "full_picture.png"
    render_equity_report(series, combined,
                         "Полная картина — все стратегии комбайна (365 дней, paper)", out)
    print("SAVED", out)

    print("\n=== ТОП стратегий по calmar ===")
    for r in top:
        star = "★ПУЛ" if r["in_pool"] else ""
        print(f"  {r['name']:<26} [{r['engine']:<8}] pnl={r['pnl']:>+7d}₽ dd={r['dd']:>6d}₽ "
              f"calmar={r['calmar']:>6.2f} мес+={r['pos_months']} сделок={r['trades']:>4} {star}")
    print(f"\nОБЩИЙ ИНДЕКС (все {len(pos)} плюсовых): {all_index.iloc[-1]:+,.0f} ₽, maxDD {dd_all:,.0f} ₽")
    print(f"БОЕВОЙ ПУЛ: {pool_curve.iloc[-1]:+,.0f} ₽, maxDD {dd_pool:,.0f} ₽")


if __name__ == "__main__":
    main()

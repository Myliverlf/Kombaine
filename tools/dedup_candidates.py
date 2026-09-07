#!/usr/bin/env python3
"""dedup_candidates.py — АНТИ-КЛОН: чистка engine_candidates.json.

Задача Апостола (2026-09-05): комбайн должен искать РАЗНЫЕ стратегии,
не повторяющие equity друг друга, но все примерно одинаково хорошие.
Клоны (близнецы одной генетической линии) — мусор, они забивали пул:
45 кандидатов LKOH = по факту 2-3 разные стратегии.

Как работает:
  1. Бэктест каждого кандидата (честный, run_candidate, 365д, cap 20k).
  2. Кластеризация по КОРРЕЛЯЦИИ дневных доходностей equity:
     жадно — сортировка по calmar, кандидат оставляется только если
     корреляция с УЖЕ ВЗЯТЫМИ < CORR_MAX (0.7). Клон семьи = отброшен,
     остаётся лучший представитель каждой семьи.
  3. Отбраковка слабых: calmar < MIN_CALMAR или прибыльных месяцев < 60%.
  4. Результат: state/engine_candidates.dedup.json + замена основного файла
     (старый сохраняется в engine_candidates.raw_backup.json).

Запуск: python3 tools/dedup_candidates.py [--corr-max 0.7] [--dry-run]
"""
from __future__ import annotations
import json, sys, shutil, argparse
from pathlib import Path
import numpy as np
import pandas as pd

SC = Path("/root/prop-desk/strategy_combine")
FL = Path("/root/prop-desk/futures_lab")
sys.path.insert(0, str(SC)); sys.path.insert(0, str(FL)); sys.path.insert(0, str(SC / "tools"))
from state_io import atomic_write_json  # noqa: E402  # атомарная запись state-файлов

DATA = FL / "artifacts" / "tinkoff_futures_data"
CAP = 20000.0
COMM, SLIP = 5.0, 1.0
MIN_CALMAR = 1.5
MIN_PROFIT_MONTH_RATIO = 0.6


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


def eval_candidate(cand, df):
    from futures_lab import _synthetic_spec_for_file
    from engines.exit_engine import run_candidate
    c = dict(cand)
    c["strategy"] = c.get("strategy") or c.get("name")
    try:
        spec = _synthetic_spec_for_file(c["ticker"])
        _, trades, eq = run_candidate(df, spec, c, 1, cap=CAP, comm=COMM, slip_bps=SLIP)
    except Exception as ex:
        return None
    if eq is None or len(eq) < 100 or len(trades) < 15:
        return None
    daily = pd.Series(eq.values, index=pd.to_datetime(eq.index)).resample("D").last().ffill().dropna()
    pnl = float(daily.iloc[-1] - CAP)
    dd = float((daily.cummax() - daily).max())
    cal = pnl / dd if dd > 0 else (99 if pnl > 0 else 0)
    monthly = daily.resample("ME").last().diff().dropna()
    pm = int((monthly > 0).sum()); nm = max(len(monthly), 1)
    returns = daily.diff().dropna()
    return {"daily_returns": returns, "pnl": pnl, "dd": dd, "calmar": cal,
            "trades": len(trades), "profit_months": pm, "months": nm}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corr-max", type=float, default=0.7)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from engines.register import register_genomes
    register_genomes()

    src = SC / "state/engine_candidates.json"
    data = json.loads(src.read_text())
    cands = data["candidates"]
    print(f"кандидатов на входе: {len(cands)}")

    # 1) бэктест всех
    evaled = []
    dfs = {}
    for c in cands:
        t = c["ticker"]
        if t not in dfs:
            dfs[t] = load_365(t)
        if dfs[t] is None:
            continue
        e = eval_candidate(c, dfs[t])
        if e is None:
            continue
        evaled.append((c, e))
    print(f"с бэктестом: {len(evaled)}")

    # 2) отбраковка слабых
    good = [(c, e) for c, e in evaled
            if e["pnl"] > 0 and e["calmar"] >= MIN_CALMAR
            and e["months"] > 0 and e["profit_months"] / e["months"] >= MIN_PROFIT_MONTH_RATIO]
    good.sort(key=lambda x: (-x[1]["calmar"], -x[1]["pnl"]))
    print(f"плюсовых и качественных: {len(good)}")

    # 3) жадная анти-клон кластеризация
    kept, dropped = [], []
    for c, e in good:
        is_clone = False
        for kc, ke in kept:
            # корреляция дневных доходностей на общем индексе
            idx = e["daily_returns"].index.intersection(ke["daily_returns"].index)
            if len(idx) < 50:
                continue
            r = float(np.corrcoef(e["daily_returns"].loc[idx].values,
                                  ke["daily_returns"].loc[idx].values)[0, 1])
            # FIX(audit): NaN (постоянные доходности) раньше молча проходил как «не клон»
            if np.isnan(r):
                r = 1.0 if (e["daily_returns"].loc[idx].std() == 0 and
                            ke["daily_returns"].loc[idx].std() == 0) else 0.0
            if r >= args.corr_max:
                dropped.append((c, e, kc, r))
                is_clone = True
                break
        if not is_clone:
            kept.append((c, e))

    print(f"\n=== АНТИ-КЛОН ИТОГ: оставлено {len(kept)} РАЗНЫХ стратегий, отброшено {len(dropped)} клонов ===")
    for c, e in kept:
        print(f"  KEEP {c['ticker']:<6} {(c.get('strategy') or c['name']):<18} "
              f"[{c.get('engine','?'):<8}] pnl={e['pnl']:>+8.0f} calmar={e['calmar']:>6.2f} "
              f"мес+={e['profit_months']}/{e['months']} сделок={e['trades']}")
    print("\nотброшенные клоны (лучший из каждой семьи выжил):")
    fams = {}
    for c, e, kc, r in dropped:
        key = kc.get("strategy") or kc["name"]
        fams[key] = fams.get(key, 0) + 1
    for k, v in sorted(fams.items(), key=lambda x: -x[1])[:15]:
        print(f"  семья {k}: {v} клонов отброшено")

    # 4) запись
    out_cands = [c for c, _ in kept]
    if not args.dry_run:
        bak = SC / "state/engine_candidates.raw_backup.json"
        shutil.copy(src, bak)
        atomic_write_json(src, {"ts": data.get("ts"), "dedup": {
            "at": str(pd.Timestamp.now()), "corr_max": args.corr_max,
            "before": len(cands), "after": len(out_cands)},
            "candidates": out_cands})
        dedup_p = SC / "state/engine_candidates.dedup.json"
        atomic_write_json(dedup_p, {"corr_max": args.corr_max,
            "kept": [{"ticker": c["ticker"], "name": c["name"], "engine": c.get("engine"),
                      "pnl": round(e["pnl"]), "calmar": round(e["calmar"], 2),
                      "profit_months": f"{e['profit_months']}/{e['months']}"} for c, e in kept],
            "dropped_clones": len(dropped)})
        print(f"\nSAVED {src} (бэкап старого: {bak.name})")
        print(f"SAVED {dedup_p}")
    else:
        print("\nDRY-RUN: файлы не тронуты")


if __name__ == "__main__":
    main()

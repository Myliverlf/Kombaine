#!/usr/bin/env python3
"""forward_paper_tracker.py — ЖИВОЙ бумажный форвард ПРИНЯТОГО ПУЛА.

v2 (2026-09-05): больше НЕ хардкод старого портфеля. Читает
state/stable_pool.json (принятый пул) + state/engine_candidates.json,
регистрирует геномы и гоняет run_candidate на СВЕЖИХ данных.
Точка заморозки = ts пула: всё после неё = чистый форвард, параметры не трогались.

Никаких живых ордеров. Только бумага, только наблюдение.
"""
from __future__ import annotations
import json, sys, subprocess
from pathlib import Path
from datetime import datetime, timezone

SC = Path("/root/prop-desk/strategy_combine")
FL = Path("/root/prop-desk/futures_lab")
sys.path.insert(0, str(SC)); sys.path.insert(0, str(FL)); sys.path.insert(0, str(SC / "tools"))

DATA = FL / "artifacts" / "tinkoff_futures_data"
LEDGER = SC / "state" / "forward_paper_ledger_v2.json"
CAP = 20000.0
COMM, SLIP = 5.0, 1.0


def refresh(ticker, days=90):
    csv = DATA / f"{ticker}_{days}d_1h_continuous.csv"
    cmd = [sys.executable, "futures_lab.py", "download", "--ticker", ticker,
           "--days", str(days), "--interval", "1h", "--continuous", "--out", str(csv)]
    r = subprocess.run(cmd, cwd=str(FL), capture_output=True, text=True, timeout=600)
    return r.returncode == 0 and csv.exists(), csv


def run_forward(cand, contracts, freeze):
    import pandas as pd
    from futures_lab import _synthetic_spec_for_file
    from engines.exit_engine import run_candidate
    t = cand["ticker"]
    ok, csv = refresh(t)
    if not ok:
        return {"ticker": t, "strategy": cand["name"], "error": "refresh failed"}
    df = pd.read_csv(csv).reset_index(drop=True)
    spec = _synthetic_spec_for_file(t)
    c = dict(cand)
    c["strategy"] = c.get("strategy") or c.get("name")
    m, trades, eq = run_candidate(df, spec, c, contracts, cap=CAP, comm=COMM, slip_bps=SLIP)
    times = pd.to_datetime(df["time"])
    fwd_mask = times > pd.Timestamp(freeze)
    n_fwd = int(fwd_mask.sum())
    fwd_trades = [tr for tr in trades
                  if pd.Timestamp(getattr(tr, "entry_time", None)) > pd.Timestamp(freeze)]
    fwd_pnl_tr = sum(getattr(tr, "pnl", 0) or 0 for tr in fwd_trades)
    eq_series = eq
    if n_fwd > 0 and len(eq_series) > 0:
        idx0 = len(df) - n_fwd
        eqv = list(eq_series.values)
        seg = eqv[idx0 - 1] if idx0 > 0 else CAP
        fwd_eq_pnl = float(eqv[-1] - seg)
    else:
        fwd_eq_pnl = 0.0
    full_pnl = float(list(eq_series.values)[-1] - CAP) if len(eq_series) else 0.0
    return {"ticker": t, "strategy": cand["name"], "engine": cand.get("engine"),
            "contracts": contracts, "bars_total": len(df), "bars_forward": n_fwd,
            "forward_trades": len(fwd_trades),
            "forward_pnl_trades": round(fwd_pnl_tr, 1),
            "forward_pnl_equity": round(fwd_eq_pnl, 1),
            "full_window_pnl": round(full_pnl, 1),
            "data_end": str(times.iloc[-1])}


def main():
    from engines.register import register_genomes
    register_genomes()

    pool = json.loads((SC / "state/stable_pool.json").read_text())
    cands = json.loads((SC / "state/engine_candidates.json").read_text())["candidates"]
    by_name = {c["name"]: c for c in cands}
    freeze = pool["ts"]
    winners = pool.get("selected") or pool.get("winners") or []

    ledger = json.loads(LEDGER.read_text()) if LEDGER.exists() else {"runs": []}
    rec = {"run_at": datetime.now(timezone.utc).isoformat(), "freeze": freeze,
           "portfolio_pnl": pool.get("portfolio_pnl_365d"), "entries": []}

    print(f"форвард пула (заморозка {freeze}):")
    total = 0.0
    for w in winners:
        name = w.get("strategy") or w.get("name")
        cand = by_name.get(name)
        if cand is None:
            # пул мог отобрать кандидата до дедупа — ищем в бэкапе
            bak = SC / "state/engine_candidates.raw_backup.json"
            if bak.exists():
                for c in json.loads(bak.read_text())["candidates"]:
                    if c["name"] == name:
                        cand = c
                        break
        if cand is None:
            rec["entries"].append({"ticker": w.get("ticker"), "strategy": name,
                                   "error": "candidate not found"})
            print(f"  {w.get('ticker'):<6} {name:<18} ОШИБКА: кандидат не найден")
            continue
        try:
            r = run_forward(cand, int(w.get("contracts", 1)), freeze)
        except Exception as ex:
            r = {"ticker": cand["ticker"], "strategy": name, "error": str(ex)[:150]}
        rec["entries"].append(r)
        if "error" in r:
            print(f"  {r['ticker']:<6} {name:<18} ОШИБКА: {r['error']}")
        else:
            total += r["forward_pnl_equity"]
            print(f"  {r['ticker']:<6} {name:<18} контрактов={r['contracts']} "
                  f"форвард-баров={r['bars_forward']} сделок={r['forward_trades']} "
                  f"fwd_pnl={r['forward_pnl_equity']:+.0f}₽ конец_данных={str(r['data_end'])[:16]}")
    rec["total_forward_pnl"] = round(total, 1)
    print(f"  ИТОГО форвард портфеля: {total:+.0f}₽")

    ledger["runs"].append(rec)
    ledger["runs"] = ledger["runs"][-120:]
    sys.path.insert(0, str(SC / "tools"))
    from state_io import atomic_write_json  # FIX(audit): атомарная запись леджера
    atomic_write_json(LEDGER, ledger)
    print("ledger:", LEDGER)


if __name__ == "__main__":
    main()

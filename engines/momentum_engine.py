#!/usr/bin/env python3
"""momentum_engine.py — движок E3: кросс-секционный моментум / перенос недельных паттернов.

Другой механизм поиска, чем E1 (зоопарк) и E2 (генетик):
  1. агрегирует 1h-данные в дневные/недельные бары;
  2. ищет устойчивые автокорреляционные паттерны «доходность k дней назад → доходность завтра»
     (momentum/reversal на лагах 1..10 дней) отдельно для каждого тикера;
  3. если паттерн устойчив по знаку на обоих половинах года (стабильность!) —
     строит из него сигнальную стратегию и ссыпает кандидата в общий приёмник.

Всё paper-only, без брокерских вызовов.
"""
import json, sys, time
import numpy as np
import pandas as pd
from pathlib import Path

SC = Path("/root/prop-desk/strategy_combine")
FL = Path("/root/prop-desk/futures_lab")
sys.path.insert(0, str(SC)); sys.path.insert(0, str(FL)); sys.path.insert(0, str(SC / "tools"))
from state_io import atomic_write_json  # noqa: E402  # атомарная запись state-файлов

DATA = FL / "artifacts" / "tinkoff_futures_data"
OUT = SC / "state" / "engine_candidates.json"
TICKERS = ["CNY", "IMOEX", "GAZP", "SBER", "LKOH"]
CAP = 20000.0


def daily(ticker):
    p = DATA / f"{ticker}_365d_1h_continuous.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p)
    df["time"] = pd.to_datetime(df["time"])
    d = df.set_index("time").resample("D").agg({"open": "first", "high": "max", "low": "min",
                                                "close": "last", "volume": "sum"}).dropna()
    return d


def find_patterns(d):
    """Устойчивые лаг-паттерны: ret(t-lag) предсказывает ret(t)."""
    r = d["close"].pct_change()
    out = []
    n = len(r)
    half = n // 2
    for lag in range(1, 11):
        x = r.shift(lag)
        for name, mask in (("mom", (x * r)),):  # совпадение знака = моментум
            corr_all = np.corrcoef(x.dropna(), r.reindex(x.dropna().index))[0, 1]
            c1 = np.corrcoef(x[:half].dropna(), r[:half].reindex(x[:half].dropna().index))[0, 1]
            c2 = np.corrcoef(x[half:].dropna(), r[half:].reindex(x[half:].dropna().index))[0, 1]
        if not (np.isfinite(c1) and np.isfinite(c2)):
            continue
        # устойчивый знак на обеих половинах + сила
        if abs(c1) > 0.06 and abs(c2) > 0.06 and np.sign(c1) == np.sign(c2):
            out.append({"lag": lag, "dir": "mom" if c1 > 0 else "rev",
                        "corr": round(float((c1 + c2) / 2), 4),
                        "c1": round(float(c1), 4), "c2": round(float(c2), 4)})
    out.sort(key=lambda p: -abs(p["corr"]))
    return out[:3]


def simulate(d, pat, thr=0.0):
    """Быстрый дневной симулятор паттерна: long если ret(t-lag)>thr*dir и т.д."""
    lag, direction = pat["lag"], pat["dir"]
    r = d["close"].pct_change()
    x = r.shift(lag)
    if direction == "mom":
        sig = np.where(x > 0, 1, np.where(x < 0, -1, 0))
    else:
        sig = np.where(x < 0, 1, np.where(x > 0, -1, 0))
    sig = pd.Series(sig, index=d.index).shift(1).fillna(0)  # входим на следующий день
    pnl_day = sig * r.fillna(0) * d["close"] / 1000.0  # грубая рублёвая оценка на 1 контракт
    # честнее: point_value из спеков
    try:
        from futures_lab import _synthetic_spec_for_file
        spec = _synthetic_spec_for_file(d.name if hasattr(d, "name") else "CNY")
    except Exception:
        spec = None
    eq = CAP + pnl_day.cumsum()
    dd = float((eq.cummax() - eq).max())
    pnl = float(pnl_day.sum())
    monthly = pnl_day.resample("ME").sum()
    pm = int((monthly > 0).sum())
    return {"pnl": round(pnl), "dd": round(dd),
            "calmar": round(pnl / dd, 2) if dd > 0 else 0,
            "profit_months": f"{pm}/{len(monthly)}"}


def main():
    results = []
    for t in TICKERS:
        d = daily(t)
        if d is None or len(d) < 120:
            continue
        pats = find_patterns(d)
        print(f"[{t}] patterns: {pats}")
        for p in pats:
            st = simulate(d.assign(name=t), p)
            name = f"moe_{t.lower()}_{p['dir']}{p['lag']}"
            results.append({"engine": "momentum", "ticker": t, "name": name,
                            "desc": f"daily {p['dir']} lag={p['lag']} corr={p['corr']}",
                            "pattern": p, "fitness": st["calmar"], "stats": st})
    old = []
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text()).get("candidates", [])
        except Exception:
            old = []
    merged = {c["name"]: c for c in old}
    for c in results:
        merged[c["name"]] = c
    atomic_write_json(OUT, {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "candidates": list(merged.values())})
    print(f"MOMENTUM ENGINE done: {len(results)} candidates, total pool {len(merged)}")
    print(f"SAVED {OUT}")


if __name__ == "__main__":
    main()

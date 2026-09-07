#!/usr/bin/env python3
"""tools/stress_engine.py — стресс-тестирование кандидатов/пула (hardening v2 §9).

Каждый сценарий ПЕРЕГОНЯЕТ бэктест честным run_candidate с ухудшенными условиями:
- комиссия ×1/×2/×3, слиппедж ×1/×2/×5
- delayed execution: вход на +1 бар позже
- signal loss: случайные 10%/20% сигналов выброшены (seed фиксирован — детерминировано)
- margin shock: ГО +50% (влияет на сайзинг контрактов)
- top-trade removal: лучшие 10% сделок по PnL удалены («а вдруг повезло?»)

Метрики: break_even_cost (множитель издержек, при котором PnL=0 — бисекция),
cost_sensitivity (dPnL/dcost), robustness_grade A/B/C/D/F.

Paper-only: брокера не зовёт, данные читает из CSV. Детерминированно (fix-seed).
Проверен: tests/test_stress_engine.py + /root/audits/strategy_combine_hardening_v2/09_stress_report.md
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

SCHEMA_VERSION = "stress-v1"


def _load_df(ticker: str, cutoff_days: int = 0):
    p = DATA / f"{ticker}_365d_1h_continuous.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p).reset_index(drop=True)
    if cutoff_days:
        t = pd.to_datetime(df["time"])
        df = df[t < t.max() - pd.Timedelta(days=cutoff_days)].reset_index(drop=True)
    return df


def _register_stress_wrappers():
    """Регистрирует ОБЁРТКИ стратегий, пост-обрабатывающие СИГНАЛ (delay/drop).
    Сигнал считается внутри замыкания генома, поэтому «сдвинуть/выбросить» его
    можно только обёрткой над strategy-func. Детерминированно, оригиналы не трогаем.
    Возвращает dict (kind, params) -> wrapper_name."""
    from futures_lab import STRATEGY_FUNCS
    made = {}

    def _wrap(base_name, delay_bars, drop_frac, seed):
        wname = f"__stress_{base_name}_d{delay_bars}_x{int(drop_frac*100)}"
        if wname in STRATEGY_FUNCS:
            return wname
        if base_name not in STRATEGY_FUNCS:
            raise KeyError(f"stress wrapper: unknown base strategy {base_name}")
        orig = STRATEGY_FUNCS[base_name]

        def fn(df, **params):
            # build_signal зовёт STRATEGY_FUNCS[name](df, **params) — сигнатура kwargs
            sig = orig(df, **params)
            s = pd.Series(sig).fillna(0).astype(int).to_numpy().copy()
            if delay_bars:
                s = np.roll(s, delay_bars); s[:delay_bars] = 0
            if drop_frac > 0:
                rng = np.random.default_rng(seed)
                idx = np.flatnonzero(s != 0)
                k = int(len(idx) * drop_frac)
                if k:
                    s[rng.choice(idx, size=k, replace=False)] = 0
            return pd.Series(s, index=df.index)

        STRATEGY_FUNCS[wname] = fn
        return wname

    def wrapper_for(base_name, delay_bars=0, drop_frac=0.0, seed=1234):
        key = (base_name, delay_bars, round(drop_frac, 3))
        if key not in made:
            made[key] = _wrap(base_name, delay_bars, drop_frac, seed)
        return made[key]

    return wrapper_for


_WRAPPER = None


def _wrapper():
    global _WRAPPER
    if _WRAPPER is None:
        _WRAPPER = _register_stress_wrappers()
    return _WRAPPER


def _run(cand: dict, df: pd.DataFrame, n: int, comm_mult=1.0, slip_mult=1.0,
         delay_bars=0, drop_frac=0.0, seed=1234):
    """Один стресс-прогон. delay_bars/drop_frac применяются к СИГНАЛУ через обёртку
    strategy-func (сигнал считается внутри замыкания генома — иначе не достать)."""
    from futures_lab import _synthetic_spec_for_file
    from engines.exit_engine import run_candidate
    cc = dict(cand); cc["strategy"] = cc.get("strategy") or cc.get("name")
    if delay_bars or drop_frac:
        cc["strategy"] = _wrapper()(cc["strategy"], delay_bars, drop_frac, seed)
    spec = _synthetic_spec_for_file(cc["ticker"])
    _, trades, eq = run_candidate(df, spec, cc, n, cap=CAP,
                                  comm=COMM * comm_mult, slip_bps=SLIP * slip_mult)
    if eq is None or len(eq) < 50:
        return None, None
    pnl = float(eq.iloc[-1] - CAP)
    dd = float((eq.cummax() - eq).max())
    return pnl, dd


def _pnl_top_removed(cand: dict, df: pd.DataFrame, n: int, remove_frac=0.10, seed=99):
    """Удалить лучшие remove_frac сделок из equity: PnL пересчитывается арифметически
    (сумма PnL остальных сделок минус комиссии) — приближение без перегонки."""
    from futures_lab import _synthetic_spec_for_file
    from engines.exit_engine import run_candidate
    cc = dict(cand); cc["strategy"] = cc.get("strategy") or cc.get("name")
    spec = _synthetic_spec_for_file(cc["ticker"])
    _, trades, eq = run_candidate(df, spec, cc, n, cap=CAP, comm=COMM, slip_bps=SLIP)
    if eq is None or not trades:
        return None
    pnls = sorted((float(t.pnl) for t in trades))
    k = max(1, int(len(pnls) * remove_frac))
    kept = pnls[: len(pnls) - k]
    return float(sum(kept)) if kept else None


def break_even_cost(cand: dict, df: pd.DataFrame, n: int) -> float:
    """Множитель издержек (комиссия И слиппедж одновременно), при котором PnL≈0.
    Бисекция на [1, 20]; если PnL<=0 уже при ×1 → 1.0; если >0 при ×20 → 20.0."""
    def pnl_at(m):
        p, _ = _run(cand, df, n, comm_mult=m, slip_mult=m)
        return p if p is not None else -1e9
    lo, hi = 1.0, 20.0
    if pnl_at(lo) <= 0:
        return 1.0
    if pnl_at(hi) > 0:
        return 20.0
    for _ in range(12):
        mid = (lo + hi) / 2
        if pnl_at(mid) > 0:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 2)


def grade(results: dict) -> str:
    """Robustness grade A–F по стресс-результатам (хрупкость, не «прибыль всегда»)."""
    be = results.get("break_even_cost", 1.0)
    base = results.get("base_pnl") or 0.0
    if base <= 0:
        return "F"
    score = 0
    if be >= 5: score += 2
    elif be >= 3: score += 1
    for p in (results.get("delay1_pnl"), results.get("drop20_pnl"),
              results.get("slip5_pnl"), results.get("comm3_pnl"),
              results.get("margin_shock_pnl")):
        if p is None:
            continue
        if p > 0: score += 1
        elif p > -0.25 * base: pass    # терпимо
        else: score -= 1
    tr = results.get("top10_removed_pnl")
    if tr is not None and tr <= 0:
        score -= 1                     # без лучших 10% сделок стратегия в минусе — хрупкая
    if score >= 6: return "A"
    if score >= 4: return "B"
    if score >= 2: return "C"
    if score >= 0: return "D"
    return "F"


def margin_shock_contracts(ticker: str, n: int, shock=1.5):
    """Сколько контрактов влезет при ГО×shock в лимиты (слот ≤50% капитала).
    Возвращает n_shock (может быть 0 — кандидат нежизнеспособен при шоке ГО)."""
    import json as _json
    specs = _json.loads((FL / "futures_specs.json").read_text()).get("specs", {})
    s = specs.get(str(ticker).upper()) or {}
    m = max(float(s.get("initial_margin_on_buy") or 0), float(s.get("initial_margin_on_sell") or 0))
    if m <= 0:
        return n
    slot_budget = 0.50 * CAP
    return max(0, min(n, int(slot_budget // (m * shock))))


def stress_candidate(cand: dict, n_contracts: int, cutoff_days=0) -> dict:
    from engines.register import register_genomes
    register_genomes()   # геномные кандидаты ge_* должны быть в STRATEGY_FUNCS до обёртки
    df = _load_df(cand["ticker"], cutoff_days)
    if df is None:
        return {"error": "no data", "ticker": cand.get("ticker")}
    out = {"schema_version": SCHEMA_VERSION,
           "ticker": cand["ticker"], "strategy": cand.get("strategy") or cand.get("name"),
           "contracts": n_contracts}
    base, base_dd = _run(cand, df, n_contracts)
    out["base_pnl"] = round(base) if base is not None else None
    out["base_dd"] = round(base_dd) if base_dd is not None else None
    scen = {
        "comm2": lambda: _run(cand, df, n_contracts, comm_mult=2.0)[0],
        "comm3": lambda: _run(cand, df, n_contracts, comm_mult=3.0)[0],
        "slip2": lambda: _run(cand, df, n_contracts, slip_mult=2.0)[0],
        "slip5": lambda: _run(cand, df, n_contracts, slip_mult=5.0)[0],
        "delay1": lambda: _run(cand, df, n_contracts, delay_bars=1)[0],
        "drop10": lambda: _run(cand, df, n_contracts, drop_frac=0.10)[0],
        "drop20": lambda: _run(cand, df, n_contracts, drop_frac=0.20)[0],
    }
    for k, fn in scen.items():
        p = fn()
        out[f"{k}_pnl"] = round(p) if p is not None else None
    tr = _pnl_top_removed(cand, df, n_contracts)
    out["top10_removed_pnl"] = round(tr) if tr is not None else None
    out["break_even_cost"] = break_even_cost(cand, df, n_contracts)
    # margin shock: при ГО×1.5 сколько контрактов влезет → PnL на урезанном сайзинге
    n_shock = margin_shock_contracts(cand["ticker"], n_contracts, shock=1.5)
    out["margin_shock_contracts"] = n_shock
    if n_shock >= 1:
        ms, _ = _run(cand, df, n_shock)
        out["margin_shock_pnl"] = round(ms) if ms is not None else None
    else:
        out["margin_shock_pnl"] = 0   # нежизнеспособен при шоке ГО
    if base and base > 0:
        c2 = out.get("comm2_pnl")
        out["cost_sensitivity"] = round((base - c2) / base, 3) if c2 is not None else None
    out["grade"] = grade(out)
    return out


def stress_pool(pool_path=None, cutoff_days=0) -> dict:
    pool_path = Path(pool_path or SC / "state/stable_pool.json")
    pool = json.loads(pool_path.read_text())
    cands = json.loads((SC / "state/engine_candidates.json").read_text())["candidates"]
    by_name = {}
    for c in cands:
        by_name[c.get("name")] = c
    rows = []
    for s in pool.get("selected", []):
        src = by_name.get(s["strategy"]) or by_name.get(s.get("name"))
        if src is None:
            rows.append({"error": "candidate not found", "strategy": s["strategy"]})
            continue
        src = {**src, "ticker": s["ticker"], "strategy": s["strategy"]}
        rows.append(stress_candidate(src, int(s.get("contracts", 1)), cutoff_days))
    grades = [r.get("grade") for r in rows if r.get("grade")]
    return {"schema_version": SCHEMA_VERSION,
            "ts": pd.Timestamp.utcnow().isoformat(),
            "pool_accepted": pool.get("accepted"),
            "pool_median_month": pool.get("portfolio_median_month"),
            "n_stressed": len(rows),
            "grades": grades,
            "worst_grade": min(grades) if grades else None,
            "rows": rows}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default=None)
    ap.add_argument("--cutoff-days", type=int, default=0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    res = stress_pool(args.pool, args.cutoff_days)
    txt = json.dumps(res, indent=1, ensure_ascii=False, default=str)
    print(txt)
    if args.out:
        from state_io import atomic_write_json
        atomic_write_json(Path(args.out), res)

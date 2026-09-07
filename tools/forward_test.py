#!/usr/bin/env python3
"""forward_test.py — честный forward-экзамен для stable_pool (paper-only).

Идея: модель/отбор видели только историю. Forward-тест берёт ПОСЛЕДНИЕ N дней
данных (по умолчанию 60) как «будущее» и проверяет: дают ли выбранные стратегии
плюс на отрезке, который не участвовал в отборе по стабильности?

Протокол (чтобы не врать себе):
  1. stable_pool.json -> список стратегий.
  2. Для каждой: бэктест на ПОСЛЕДНИХ N днях (это «будущее» относительно
     годового окна отбора — последние 60 дней года).
  3. Считаем: суммарный pnl, calmar, прибыльные месяцы, кривая портфеля.
  4. Сравнение: raw vs meta-filtered (ML-фильтр входа применяется к сделкам).
  5. Пишем reports/forward_test.json + график.

Paper-only. Ноль реальных ордеров.
"""
import json, sys, time
import numpy as np
import pandas as pd
# стиль графиков — tools/lieflat_render.py (lieflat-charts Mono), matplotlib убран
from pathlib import Path
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

SC = Path("/root/prop-desk/strategy_combine")
FL = Path("/root/prop-desk/futures_lab")
sys.path.insert(0, str(SC)); sys.path.insert(0, str(FL)); sys.path.insert(0, str(SC / "tools"))
from futures_lab import run_backtest, _synthetic_spec_for_file, atr  # noqa: E402
from engines.register import register_genomes  # noqa: E402
from meta_labeling_v2 import build_features  # noqa: E402
from meta_filter import FCOLS  # noqa: E402
register_genomes()

DATA = FL / "artifacts/tinkoff_futures_data"
CAP, COMM, SLIP = 20000.0, 5.0, 1.0
FWD_DAYS = 60


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


# Карта sid стратегии из обучающего прогона (сохраняется в meta_filter_model.json).
# Раньше forward использовал hash((t, strat)) % 100 — нестабильно между процессами
# (PYTHONHASHSEED) и не совпадало с sid из обучения => признак sid был мусором.
SID_MAP: dict = {}


def sid_for(t, strat):
    return int(SID_MAP.get(f"{t}::{strat}", 0))


def main():
    pool = json.loads((SC / "state/stable_pool.json").read_text())
    sel = pool.get("selected") or []
    if not sel:
        print("stable_pool пуст — нечего экзаменовать"); return
    print(f"forward test: {len(sel)} strategies, last {FWD_DAYS} days\n")

    # ML meta-filter model (trained on the FULL year per strategy — forward window is the tail)
    art = json.loads((SC / "state/meta_filter_model.json").read_text())
    sc_mean = np.array(art["scaler_mean"]); sc_scale = np.array(art["scaler_scale"])
    coef = np.array(art["coef"]); inter = art["intercept"]; thr = art["threshold"]
    SID_MAP.clear(); SID_MAP.update(art.get("sid_map") or {})

    # АРБИТР ВХОДОВ (tools/entry_arbiter.py): если обучен и включён для стратегии —
    # его решение на невидимых 60 днях показываем колонкой arb=.
    arb_path = SC / "state/entry_arbiter.json"
    arb_all = {}
    if arb_path.exists():
        # FIX(audit): ключ ticker::strategy — раньше lookup только по strategy,
        # одноимённые стратегии разных тикеров брали чужого арбитра
        arb_all = {f"{r.get('ticker','')}::{r['strategy']}": r
                   for r in json.loads(arb_path.read_text()).get("strategies", [])}

    curves = {}
    rows = []
    for s in sel:
        t, strat, params = s["ticker"], s["strategy"], s.get("params") or {}
        p = DATA / f"{t}_365d_1h_continuous.csv"
        if not p.exists():
            continue
        df = pd.read_csv(p).reset_index(drop=True)
        cutoff = pd.to_datetime(df["time"]).max() - pd.Timedelta(days=FWD_DAYS)
        mask = pd.to_datetime(df["time"]) >= cutoff
        fwd = df[mask].reset_index(drop=True)
        if len(fwd) < 200:
            continue
        spec = _synthetic_spec_for_file(t)
        n = int(s.get("contracts") or 1)
        # единый роутер v3: exit-гены учитываются честно, без них = обычный run_backtest
        from engines.exit_engine import run_candidate
        cand = {"ticker": t, "strategy": strat, "params": params,
                "risk": s.get("risk") or {}, "exits": s.get("exits") or {}}
        metrics, trades, eq = run_candidate(fwd, spec, cand, n, cap=CAP, comm=COMM, slip_bps=SLIP)
        if eq is None or len(eq) < 50:
            rows.append((t, strat, n, 0, 0, 0, 0, 0)); continue
        eqs = pd.Series(eq.values, index=pd.to_datetime(eq.index))
        daily = eqs.resample("D").last().ffill().dropna()
        raw_pnl = float(daily.iloc[-1] - CAP)
        # meta-filtered: rebuild trades P&L with entry filter
        feats = build_features(fwd)
        tidx = {str(x): i for i, x in enumerate(fwd["time"])}
        filt_pnl = 0.0; kept = 0
        for tr in trades:
            i = tidx.get(str(tr.entry_time))
            if i is None or i < 1: continue
            fv = feats.iloc[i-1]
            if fv.isna().any(): continue
            fd = fv.to_dict(); fd["side"] = 1 if tr.side == "long" else 0
            fd["sid"] = sid_for(t, strat)  # детерминированный id стратегии (был hash() — нестабилен между процессами и не совпадал с обучающим)
            x = np.array([[fd[c] for c in FCOLS]])
            x = (x - sc_mean) / sc_scale
            pred = float(x @ coef + inter)
            if pred > thr:
                filt_pnl += tr.pnl; kept += 1
        dd = float((daily.cummax() - daily).max())
        cal = raw_pnl / dd if dd > 0 else (10 if raw_pnl > 0 else 0)
        # арбитр входов на невидимых данных
        arb_txt = "—"
        rec = arb_all.get(f"{t}::{strat}")
        if rec and rec.get("enabled"):
            try:
                sys.path.insert(0, str(SC / "tools"))
                from entry_arbiter import trade_features, APPLIERS, FEAT_COLS
                fdf = trade_features(fwd, trades, rec.get("nn_model"))
                if not fdf.empty:
                    keep = APPLIERS[rec["variant"]](fdf, rec.get("model", {}))
                    arb_pnl = float(fdf["pnl"].values[keep].sum())
                    arb_txt = f"{arb_pnl:+.0f}({int(keep.sum())}/{len(fdf)})"
            except Exception as ex:
                arb_txt = f"err:{type(ex).__name__}"
        rows.append((t, strat, n, round(raw_pnl), round(filt_pnl), kept, len(trades), round(cal, 2)))
        curves[f"{t} {strat} (n={n})"] = daily
        print(f"  {t:<7} {strat:<24} n={n} trades={len(trades):>3} kept={kept:>3} raw={raw_pnl:>+8.0f} filt={filt_pnl:>+8.0f} arb={arb_txt:>12} calmar={cal:>5.2f}")

    # portfolio curve (raw)
    if curves:
        frame = pd.concat([c - CAP for c in curves.values()], axis=1, join="inner").dropna()
        if not frame.empty:
            comb = frame.sum(axis=1) + CAP
            comb_pnl = float(comb.iloc[-1] - CAP)
            comb_dd = float((comb.cummax() - comb).max())
            comb_cal = comb_pnl / comb_dd if comb_dd > 0 else 99
            print(f"\nPORTFOLIO forward {FWD_DAYS}d: pnl={comb_pnl:+.0f}₽ dd={comb_dd:.0f}₽ calmar={comb_cal:.2f}")
            # стиль графиков: lieflat-charts (Mono) — ОБЯЗАТЕЛЬНО, см. START.md
            from lieflat_render import render_equity_report
            series = [{"name": name, "points": list(zip(c.index, (c - CAP).values))}
                      for name, c in curves.items()]
            combined = {"name": f"Forward {FWD_DAYS}d — портфель",
                        "sub": f"pnl {comb_pnl:+,.0f} ₽ · maxDD {comb_dd:,.0f} ₽ · calmar {comb_cal:.2f} · экзамен на спрятанных данных",
                        "points": list(zip(comb.index, (comb - CAP).values))}
            out = SC / "reports" / "forward_test.png"
            render_equity_report(series, combined,
                                 f"Forward-экзамен — {FWD_DAYS} дней, которые отбор не видел", out)
            print("SAVED", out)
            from state_io import atomic_write_json  # FIX(audit): атомарная запись отчёта
            atomic_write_json(SC / "reports" / "forward_test.json",
                              {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ"), "fwd_days": FWD_DAYS,
                               "rows": rows, "portfolio": {"pnl": round(comb_pnl), "dd": round(comb_dd), "calmar": round(comb_cal, 2)}})


if __name__ == "__main__":
    main()

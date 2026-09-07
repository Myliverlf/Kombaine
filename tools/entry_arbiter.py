#!/usr/bin/env python3
"""entry_arbiter.py — АРБИТР ВХОДОВ: надстройка над стратегией, а не замена.

Проблема (найдена в forward 2026-09-05): старый meta-фильтр (Ridge на 11
индикаторах, обучен на сделках СТАРОГО пула) на новых геномах режет ВСЁ:
IMOEX ge_3a8b94350b forward 60д: raw=+N ₽, filt=+0 ₽ (kept 0 из 5).
Ровно сценарий, которого боится пользователь: стратегия нашла плюсы,
фильтр их выкинул.

Решение — арбитр с ЖЁСТКОЙ защитой:
  1. Арбитр НЕ генерит сигналы. Стратегия (геном/звериная) решает КОГДА входить.
     Арбитр лишь говорит «доверяю / не доверяю» конкретной сделке.
  2. Арбитр видит ПОТОК ВСЕХ МНЕНИЙ сразу:
       - рыночный контекст входа (rsi, тренд, вола, моментум, час...)
       - мнение НЕЙРОСЕТИ E4 об этом баре (p_long/p_short/p_flat, согласие
         с направлением сделки, уверенность) — та самая «вторая нейронка
         поверх потока сигналов», только честная: она судит не сырые бары,
         а готовые решения стратегии в контексте рынка
       - ожидание старого ridge-метафильтра (как признак)
  3. ВАРИАНТЫ арбитра (на каждую стратегию выбирается свой):
       off       — ничего не режем (baseline, delta=0)
       nn_veto   — вето только при СИЛЬНОМ несогласии нейросети
       arbiter   — Ridge на объединённых признаках
  4. ЧЕСТНАЯ ВАЛИДАЦИЯ: сделка-поток режется по времени 2/3 train | 1/3 val.
     Вариант включается ТОЛЬКО если на val он СТРОГО лучше «off»
     (filt_pnl > raw_pnl) И не режет больше 70% сделок.
     Не доказал пользу → enabled=false, сделки идут как есть.
     «off» — всегда кандидат. Арбитр не может сделать хуже по конструкции.
  5. Holdout 60д арбитр и нейросеть НЕ ВИДЯТ — финальная проверка в
     tools/forward_test.py (колонка arb=).

Артефакт: state/entry_arbiter.json (по стратегиям: вариант, веса, метрики
train/val, имя нейросети, enabled). Инференс: arbiter_decision() — только
сохранённые веса, без обучения на лету.

Запуск:  python3 tools/entry_arbiter.py [--holdout-days 60] [--min-trades 60]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

SC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SC))
sys.path.insert(0, str(SC / "tools"))
sys.path.insert(0, "/root/prop-desk/futures_lab")

CAP = 20000.0
COMM = 5.0
SLIP = 1.0
DATA = Path("/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data")
STATE = SC / "state"
ARBITER_PATH = STATE / "entry_arbiter.json"

FCOLS_MKT = ["rsi14", "atr_pct", "trend", "vol20", "mom5", "mom20",
             "zscore20", "hour_sin", "hour_cos"]


# ---------------------------------------------------------------- данные
def load_train_df(ticker: str, holdout_days: int):
    """Максимально длинный файл, обрезанный ДО holdout (арбитр его не видит)."""
    for pref in (f"{ticker}_1095d_1h_continuous.csv", f"{ticker}_365d_1h_continuous.csv"):
        p = DATA / pref
        if p.exists():
            df = pd.read_csv(p).reset_index(drop=True)
            t = pd.to_datetime(df["time"])
            cutoff = t.max() - pd.Timedelta(days=holdout_days)
            df = df[t < cutoff].reset_index(drop=True)
            if len(df) > 1500:
                return df
    return None


def best_nn_for(ticker: str):
    """Лучшая нейросеть E4 на тикер по лидерборду (eval calmar, pnl>0)."""
    lb_p = STATE / "nn_leaderboard.json"
    if not lb_p.exists():
        return None
    lb = json.loads(lb_p.read_text())
    best = None
    for r in lb.get("runs", []):
        if r.get("ticker") != ticker or not r.get("eval"):
            continue
        e = r["eval"]
        if e.get("pnl", 0) <= 0 or e.get("trades", 0) < 8:
            continue
        key = (e["pnl"] > 0, e.get("calmar", 0))
        if best is None or key > best[0]:
            best = (key, r["name"])
    return best[1] if best else None


def collect_trades(cand, df):
    from futures_lab import _synthetic_spec_for_file
    from engines.exit_engine import run_candidate
    spec = _synthetic_spec_for_file(cand["ticker"])
    c = dict(cand)
    c["strategy"] = c.get("strategy") or c["name"]
    _, trades, _ = run_candidate(df, spec, c, 1, cap=CAP, comm=COMM, slip_bps=SLIP)
    return trades


# ---------------------------------------------------------------- признаки сделки
def trade_features(df, trades, nn_name):
    """Поток мнений на каждый вход сделки: рынок + нейросеть + старый метафильтр."""
    from meta_labeling_v2 import build_features
    mkt = build_features(df)
    tidx = {str(x): i for i, x in enumerate(df["time"])}

    nn_p = None
    if nn_name:
        try:
            from engines.neural_engine import neural_probs
            p, _ = neural_probs(nn_name, df)
            nn_p = p
        except Exception as ex:
            print(f"  [arbiter] nn {nn_name} недоступна ({ex}) — нейронные признаки нейтральны")

    rows = []
    for tr in trades:
        i = tidx.get(str(tr.entry_time))
        if i is None or i >= len(df):
            continue
        f = {"side": 1.0 if tr.side == "long" else 0.0}
        for c in FCOLS_MKT:
            v = mkt[c].iloc[i]
            f[c] = float(v) if np.isfinite(v) else 0.0
        if nn_p is not None:
            ps, pf, pl = nn_p[i]
            same = pl if tr.side == "long" else ps
            opp = ps if tr.side == "long" else pl
            f["nn_same"] = float(same)
            f["nn_opp"] = float(opp)
            f["nn_flat"] = float(pf)
            f["nn_agree"] = float(int((pl > ps) if tr.side == "long" else (ps > pl)))
            f["nn_conf"] = float(max(pl, ps))
        else:
            f.update(nn_same=0.33, nn_opp=0.33, nn_flat=0.34, nn_agree=0.5, nn_conf=0.33)
        # старый ridge-метафильтр как признак
        try:
            from meta_filter import load_model, predict_entry_pnl
            m = load_model()
            if m:
                f["meta_pred"] = predict_entry_pnl(m, f) / 100.0
            else:
                f["meta_pred"] = 0.0
        except Exception:
            f["meta_pred"] = 0.0
        f["pnl"] = float(tr.pnl)
        f["entry_time"] = str(tr.entry_time)
        rows.append(f)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("entry_time").reset_index(drop=True)


FEAT_COLS = FCOLS_MKT + ["side", "nn_same", "nn_opp", "nn_flat", "nn_agree",
                         "nn_conf", "meta_pred"]


# ---------------------------------------------------------------- варианты арбитра
def apply_off(fdf, _model):
    return np.ones(len(fdf), dtype=bool)


def apply_nn_veto(fdf, model):
    veto = model["veto_thr"]
    return (fdf["nn_opp"].values < veto) | (fdf["nn_agree"].values > 0.5)


def apply_ridge(fdf, model):
    X = (fdf[FEAT_COLS].values - np.array(model["scaler_mean"])) / np.array(model["scaler_scale"])
    pred = X @ np.array(model["coef"]) + model["intercept"]
    return pred > model["threshold"]


APPLIERS = {"off": apply_off, "nn_veto": apply_nn_veto, "arbiter": apply_ridge}


def fit_ridge(Xtr, ytr):
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import TimeSeriesSplit, cross_val_score
    sc = StandardScaler().fit(Xtr)
    Xs = sc.transform(Xtr)
    best_a, best_s = 10.0, -np.inf
    for a in (3.0, 10.0, 30.0, 100.0, 300.0):
        tscv = TimeSeriesSplit(n_splits=3)
        s = cross_val_score(Ridge(alpha=a), Xs, ytr, cv=tscv,
                            scoring="neg_mean_squared_error").mean()
        if s > best_s:
            best_s, best_a = s, a
    m = Ridge(alpha=best_a).fit(Xs, ytr)
    return {"scaler_mean": sc.mean_.tolist(), "scaler_scale": sc.scale_.tolist(),
            "coef": m.coef_.tolist(), "intercept": float(m.intercept_),
            "alpha": best_a, "threshold": 0.0}  # режем сделки с отрицательным ожиданием


def evaluate_variant(fdf, keep, part):
    sub = fdf.iloc[part]
    k = keep[part]
    raw = float(sub["pnl"].sum())
    filt = float(sub["pnl"].values[k].sum()) if k.any() else 0.0
    return {"raw": round(raw), "filt": round(filt), "delta": round(filt - raw),
            "kept": int(k.sum()), "n": int(len(sub))}


def train_arbiter_for(cand, df, nn_name, min_trades):
    """Train/val по времени. Возвращает запись арбитра для стратегии."""
    strat = cand.get("strategy") or cand["name"]
    trades = collect_trades(cand, df)
    if len(trades) < 20:
        return {"strategy": strat, "ticker": cand["ticker"], "enabled": False,
                "variant": "off", "reason": f"мало сделок ({len(trades)})"}
    fdf = trade_features(df, trades, nn_name)
    if fdf.empty or len(fdf) < 20:
        return {"strategy": strat, "ticker": cand["ticker"], "enabled": False,
                "variant": "off", "reason": "не удалось собрать признаки"}

    n = len(fdf)
    n_tr = int(n * 2 / 3)
    tr_part = np.arange(n_tr)
    va_part = np.arange(n_tr, n)

    # обучение вариантов на train
    models = {"off": {}}
    keeps = {"off": apply_off(fdf, {})}
    # nn_veto: порог вето = медиана nn_opp прибыльных сделок train (режем то,
    # что сильнее среднего несогласия нейросети на хороших сделках) — но не выше 0.5
    prof = fdf.iloc[tr_part]
    veto_candidates = [0.45, 0.5, 0.55, 0.6]
    if nn_name and len(prof[prof["pnl"] > 0]) >= 5:
        vt = float(np.median(prof.loc[prof["pnl"] > 0, "nn_opp"]))
        veto_candidates = sorted(set([round(min(max(vt, 0.40), 0.60), 2)] + veto_candidates))
    models["nn_veto"] = {"veto_thr": veto_candidates[0], "all_thrs": veto_candidates}
    # ridge — только при достаточном количестве сделок
    if n >= min_trades:
        Xtr = fdf.iloc[tr_part][FEAT_COLS].values
        ytr = fdf.iloc[tr_part]["pnl"].values
        models["arbiter"] = fit_ridge(Xtr, ytr)

    results = {}
    for name, model in models.items():
        keep = APPLIERS[name](fdf, model)
        tr_m = evaluate_variant(fdf, keep, tr_part)
        va_m = evaluate_variant(fdf, keep, va_part)
        results[name] = {"train": tr_m, "val": va_m, "model": model}

    # nn_veto: перебор порогов на train, фиксация одного, оценка на val
    if nn_name and "nn_veto" in results:
        best_v, best_d = None, -np.inf
        for vt in veto_candidates:
            m = {"veto_thr": vt}
            keep = apply_nn_veto(fdf, m)
            d = evaluate_variant(fdf, keep, tr_part)["delta"]
            if d > best_d:
                best_d, best_v = d, vt
        m = {"veto_thr": best_v, "all_thrs": veto_candidates}
        keep = apply_nn_veto(fdf, m)
        results["nn_veto"] = {"train": evaluate_variant(fdf, keep, tr_part),
                              "val": evaluate_variant(fdf, keep, va_part), "model": m}

    # ВЫБОР: строго лучше off на val И не режет >70% сделок на val. off — кандидат.
    off_val = results["off"]["val"]["raw"]
    winner = "off"
    for name in ("arbiter", "nn_veto"):
        if name not in results:
            continue
        v = results[name]["val"]
        if v["n"] == 0:
            continue
        keep_ratio = v["kept"] / v["n"]
        if v["filt"] > off_val and keep_ratio >= 0.3:
            if results[winner]["val"]["filt"] < v["filt"]:
                winner = name
    rec = {"strategy": strat, "ticker": cand["ticker"], "nn_model": nn_name,
           "enabled": winner != "off", "variant": winner,
           "n_trades_total": n, "thresholds": None}
    for name, r in results.items():
        rec[name] = {"train": r["train"], "val": r["val"]}
    if winner != "off":
        rec["model"] = results[winner]["model"]
    return rec


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout-days", type=int, default=60)
    ap.add_argument("--min-trades", type=int, default=60)
    args = ap.parse_args()

    from engines.register import register_genomes
    register_genomes()

    pool = json.loads((STATE / "stable_pool.json").read_text())
    cands = json.loads((STATE / "engine_candidates.json").read_text())["candidates"]
    by_key = {(c["ticker"], c.get("strategy") or c["name"]): c for c in cands}

    out = {"trained_at": str(pd.Timestamp.now()), "holdout_days": args.holdout_days,
           "rule": "вариант включается только если СТРОГО лучше off на val (filt>raw) и kept>=30%",
           "strategies": []}
    print("=== АРБИТР ВХОДОВ: обучение на сделках пула (holdout не виден) ===\n")
    for s in pool.get("selected", []):
        t, strat = s["ticker"], s["strategy"]
        cand = by_key.get((t, strat))
        if cand is None:
            print(f"  {t} {strat}: кандидат не найден — арбитр off")
            out["strategies"].append({"strategy": strat, "ticker": t,
                                      "enabled": False, "variant": "off",
                                      "reason": "нет кандидата"})
            continue
        df = load_train_df(t, args.holdout_days)
        if df is None:
            print(f"  {t} {strat}: нет данных — арбитр off")
            out["strategies"].append({"strategy": strat, "ticker": t,
                                      "enabled": False, "variant": "off",
                                      "reason": "нет данных"})
            continue
        nn = best_nn_for(t)
        rec = train_arbiter_for(cand, df, nn, args.min_trades)
        out["strategies"].append(rec)
        v = rec.get(rec.get("variant", "off"), {})
        val = v.get("val", {}) if isinstance(v, dict) else {}
        off_val = rec.get("off", {}).get("val", {})
        print(f"  {t:<6} {strat:<16} сделок {rec.get('n_trades_total', 0):>4}  nn={nn or '—'}")
        for name in ("off", "nn_veto", "arbiter"):
            if name in rec:
                tv, vv = rec[name]["train"], rec[name]["val"]
                print(f"      {name:<8} train raw={tv['raw']:+7d} filt={tv['filt']:+7d} | "
                      f"val raw={vv['raw']:+7d} filt={vv['filt']:+7d} delta={vv['delta']:+7d} kept={vv['kept']}/{vv['n']}")
        print(f"      → РЕШЕНИЕ: {rec['variant']}"
              f"{' (включён: доказал пользу на val)' if rec['enabled'] else ' (выключен: не доказал пользу — сделки идут как есть)'}\n")

    from state_io import atomic_write_json  # FIX(audit): атомарная запись артефакта арбитра
    atomic_write_json(ARBITER_PATH, out)
    print("SAVED", ARBITER_PATH)
    print("Дальше: python3 tools/forward_test.py  (колонка arb= — арбитр на невидимых 60 днях)")


if __name__ == "__main__":
    main()

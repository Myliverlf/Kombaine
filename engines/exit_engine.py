#!/usr/bin/env python3
"""exit_engine.py — ЭВОЛЮЦИЯ ВЫХОДОВ (E2 v3, 2026-09-05).

Стоп-лосс и тейк-профит — это тоже ДНК. Геном несёт "exits":
  stop_mode: atr (классика: фикс. уровень от ATR входа)
           | trail (трейлинг-стоп: тянется за ценой на stop_atr*ATR)
           | supertrend (индикатор Supertrend: флип против позиции = выход)
  take_mode: atr (фикс. уровень) | bb (противоположная полоса Боллинджера)
           | rsi (RSI выходит из зоны против позиции) | none (пусть тянет трейл/время)
  be_after:  0 = выкл; иначе брейк-ивен: после +be_after*stop_dist стоп на вход
  ema_exit:  0 = выкл; иначе выход по кроссу цены через EMA(p) против позиции
  + непрерывные параметры: st_mult/st_period, bb_p/bb_k, rsi_p/rsi_hi/rsi_lo

Правила честности (никакого подглядывания в будущее):
  - уровневые выходы (stop/take/bb/trail) заполняются ВНУТРИ бара по high/low,
    уровни известны ДО начала бара (по данным i-1);
  - индикаторные выходы (supertrend/ema/rsi/reverse/time) решаются по бару i-1,
    исполняются по open бара i — ровно как reverse в futures_lab.run_backtest;
  - приоритет внутри бара: stop > take > сигнальные выходы по open.

Экономика идентична futures_lab.run_backtest (комиссия/контракт, слиппедж bps,
fill_price, margin cap, mark-to-market по close). Пустой exits = ТОЧНО старое
поведение (проверено тестом tools/test_exit_engine.py).

Paper-only. Никаких брокерских вызовов.
"""
from __future__ import annotations
import json, random
import numpy as np
import pandas as pd
from dataclasses import dataclass, field

STOP_MODES = ["atr", "trail", "supertrend"]
TAKE_MODES = ["atr", "bb", "rsi", "none"]
EXIT_RANGES = {
    "st_mult": (1.5, 4.0), "st_period": (7, 20),
    "bb_p": (20, 100), "bb_k": (1.5, 3.0),
    "rsi_p": (7, 28), "rsi_hi": (58, 90), "rsi_lo": (10, 42),
    "be_after": (0.5, 1.5),
    "ema_exit": (10, 60),
}
DEFAULT_EXITS: dict = {}   # пусто = классика (atr stop/take + time), обратная совместимость


@dataclass
class XTrade:
    side: str; entry_time: object; exit_time: object
    entry_price: float; exit_price: float; pnl: float
    bars_held: int; reason: str; quantity: int


# ---------------- индикаторы (кэшируются на уровень прогона) ----------------
def _atr14(df):
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    tr[0] = 0.0
    return pd.Series(tr).ewm(alpha=1 / 14, adjust=False).mean().values


def _ema(c, p):
    return pd.Series(c).ewm(span=max(2, int(p)), adjust=False).mean().values


def _rsi(c, p):
    s = pd.Series(c); d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / max(2, int(p)), adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / max(2, int(p)), adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50).values


def _bb(c, p, k):
    s = pd.Series(c)
    mid = s.rolling(int(p)).mean(); sd = s.rolling(int(p)).std()
    return (mid + k * sd).values, (mid - k * sd).values


def _supertrend(df, p, m):
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    a = pd.Series(tr).ewm(alpha=1 / max(2, int(p)), adjust=False).mean().values
    hl2 = (h + l) / 2
    ub, lb = hl2 + m * a, hl2 - m * a
    n = len(c); fub = ub.copy(); flb = lb.copy(); dirn = np.ones(n)
    for i in range(1, n):
        fub[i] = ub[i] if (ub[i] < fub[i - 1] or c[i - 1] > fub[i - 1]) else fub[i - 1]
        flb[i] = lb[i] if (lb[i] > flb[i - 1] or c[i - 1] < flb[i - 1]) else flb[i - 1]
        if dirn[i - 1] > 0:
            dirn[i] = -1 if c[i] < flb[i] else 1
        else:
            dirn[i] = 1 if c[i] > fub[i] else -1
    return dirn


def make_arr(df):
    """Базовые массивы + кэш индикаторов (один раз на тикер в прогоне).
    Если df уже содержит колонку 'atr' (как после futures_lab) — берём её,
    чтобы экономика совпадала с run_backtest бит-в-бит."""
    atr_col = df["atr"].values.astype(float) if "atr" in df.columns else _atr14(df)
    return {"df": df, "o": df["open"].values.astype(float), "h": df["high"].values.astype(float),
            "l": df["low"].values.astype(float), "c": df["close"].values.astype(float),
            "atr": atr_col, "time": pd.to_datetime(df["time"]), "ind": {}}


def _ind(arr, kind, params, fn):
    key = (kind,) + tuple(params)
    if key not in arr["ind"]:
        arr["ind"][key] = fn()
    return arr["ind"][key]


# ---------------- ядро: цикл сделки с эволюционными выходами ----------------
def run_loop(arr, sig, risk, exits, cap, qty_fixed, pv, comm, slip, margin=0.0):
    """sig: массив сигналов (+1/-1/0). Возвращает (metrics, trades, equity_series).
    qty_fixed=0 и margin>0 → размер не урезается по марже (быстрый fitness-режим)."""
    o, h, l, c, atr, times = arr["o"], arr["h"], arr["l"], arr["c"], arr["atr"], arr["time"]
    df = arr["df"]
    stop_mult = float(risk.get("stop_atr", 2.0))
    take_mult = float(risk.get("take_atr", 3.0))
    max_hold = int(float(risk.get("max_hold", 48)))
    ex = exits or {}
    stop_mode = ex.get("stop_mode", "atr")
    take_mode = ex.get("take_mode", "atr")
    be_after = float(ex.get("be_after", 0.0) or 0.0)
    ema_p = int(ex.get("ema_exit", 0) or 0)

    st_dir = _ind(arr, "st", (ex.get("st_period", 10), ex.get("st_mult", 2.5)),
                  lambda: _supertrend(df, ex.get("st_period", 10), ex.get("st_mult", 2.5))) if stop_mode == "supertrend" else None
    bb_up = bb_lo = None
    if take_mode == "bb":
        bb_up, bb_lo = _ind(arr, "bb", (ex.get("bb_p", 50), ex.get("bb_k", 2.0)),
                            lambda: _bb(c, ex.get("bb_p", 50), ex.get("bb_k", 2.0)))
    rsi = _ind(arr, "rsi", (ex.get("rsi_p", 14),),
               lambda: _rsi(c, ex.get("rsi_p", 14))) if take_mode == "rsi" else None
    ema = _ind(arr, "ema", (ema_p,), lambda: _ema(c, ema_p)) if ema_p > 0 else None

    equity = cap; pos = 0; qty = 0; entry = 0.0; entry_atr = 0.0; entry_i = -1
    stop = take = 0.0; best = 0.0; init_dist = 0.0
    trades = []; curve_t = []; curve_v = []

    def fill(px, side):  # side: +1 buy, -1 sell
        return px * (1 + slip * side)

    for i in range(1, len(df)):
        if pos == 0:
            if sig[i - 1] != 0 and atr[i - 1] > 0:
                qty = qty_fixed
                if margin > 0:
                    qty = min(qty, int(equity // margin))
                if qty > 0:
                    pos = 1 if sig[i - 1] > 0 else -1
                    entry = fill(o[i], pos)
                    entry_atr = atr[i - 1]; entry_i = i
                    init_dist = stop_mult * entry_atr
                    stop = entry - pos * init_dist
                    take = entry + pos * take_mult * entry_atr if take_mode == "atr" else np.nan
                    best = entry
                    equity -= comm * qty
        else:
            # уровни, известные ДО бара i (trailing/брейк-ивен обновлены по i-1)
            stop_eff = stop
            take_lv = np.nan
            if take_mode == "atr":
                take_lv = take
            elif take_mode == "bb":
                take_lv = bb_up[i - 1] if pos > 0 else bb_lo[i - 1]
            stop_hit = l[i] <= stop_eff if pos > 0 else h[i] >= stop_eff
            take_hit = (not np.isnan(take_lv)) and (h[i] >= take_lv if pos > 0 else l[i] <= take_lv)
            rev = sig[i - 1] != 0 and sig[i - 1] != pos
            tout = (i - entry_i) >= max_hold
            st_flip = st_dir is not None and st_dir[i - 1] != pos
            ema_x = ema is not None and ((c[i - 1] < ema[i - 1]) if pos > 0 else (c[i - 1] > ema[i - 1]))
            rsi_x = rsi is not None and ((rsi[i - 1] > float(ex.get("rsi_hi", 75))) if pos > 0
                                         else (rsi[i - 1] < float(ex.get("rsi_lo", 25))))
            if stop_hit or take_hit or rev or tout or st_flip or ema_x or rsi_x:
                if stop_hit:
                    px, reason = fill(stop_eff, -pos), "stop"
                elif take_hit:
                    px, reason = fill(take_lv, -pos), "take"
                else:
                    px = fill(o[i], -pos)
                    reason = ("reverse" if rev else "time" if tout else
                              "supertrend" if st_flip else "ema" if ema_x else "rsi")
                pnl = pos * (px - entry) * pv * qty - comm * qty
                equity += pnl
                # FIX(audit): в запись сделки включаем и входную комиссию (equity -= comm*qty
                # на входе) — паритет с futures_lab.run_backtest, где Trade.pnl = gross - 2*comm
                trades.append(XTrade("long" if pos > 0 else "short", times.iloc[entry_i], times.iloc[i],
                                     entry, px, pnl - comm * qty, i - entry_i, reason, qty))
                pos = 0; qty = 0; entry = 0.0; entry_atr = 0.0; entry_i = -1
            else:
                # обновление трейла/брейк-ивена ПОСЛЕ бара i (для бара i+1)
                if pos > 0:
                    best = max(best, h[i])
                    if stop_mode == "trail":
                        stop = max(stop, best - stop_mult * atr[i])
                    if be_after > 0 and (best - entry) >= be_after * init_dist:
                        stop = max(stop, entry)
                else:
                    best = min(best, l[i])
                    if stop_mode == "trail":
                        stop = min(stop, best + stop_mult * atr[i])
                    if be_after > 0 and (entry - best) >= be_after * init_dist:
                        stop = min(stop, entry)
                if stop_mode == "supertrend":
                    # у супертренда стоп = его линия, тянется за флипами
                    pass
        mark = equity + (pos * (c[i] - entry) * pv * qty if pos != 0 else 0.0)
        curve_t.append(times.iloc[i]); curve_v.append(mark)

    # как в futures_lab.run_backtest: открытая позиция в конце НЕ закрывается —
    # остаётся в mark-to-market последней точки кривой
    eq = pd.Series(curve_v, index=pd.DatetimeIndex(curve_t), name="equity")
    dd = float((eq.cummax() - eq).max())
    rets = eq.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0)
    std = rets.std(ddof=0)
    try:
        n_days = int(times.dt.date.nunique())
        ann = np.sqrt(float(len(df)) / max(n_days, 1) * 252.0)
    except Exception:
        ann = np.sqrt(252.0)
    sharpe = float(rets.mean() / std * ann) if std and not np.isnan(std) else 0.0
    wins = [t.pnl for t in trades if t.pnl > 0]; losses = [t.pnl for t in trades if t.pnl < 0]
    pf = float(sum(wins) / abs(sum(losses))) if losses else (float("inf") if wins else 0.0)
    metrics = {"final_equity": float(eq.iloc[-1]) if len(eq) else cap,
               "total_pnl": float(eq.iloc[-1] - cap) if len(eq) else 0.0,
               "max_drawdown": dd, "sharpe": sharpe, "profit_factor": pf,
               "win_rate_pct": float(len(wins) / len(trades) * 100) if trades else 0.0,
               "avg_trade": float(np.mean([t.pnl for t in trades])) if trades else 0.0,
               "trade_count": len(trades), "exits": dict(ex or {}),
               "stop_atr": stop_mult, "take_atr": take_mult, "max_hold_bars": max_hold}
    metrics["score"] = metrics["total_pnl"] - dd
    return metrics, trades, eq


# ---------------- генерация/мутация/кроссовер exit-генов ----------------
def rand_exits(rng=None):
    r = rng or random
    ex = {"stop_mode": r.choice(STOP_MODES), "take_mode": r.choice(TAKE_MODES)}
    if ex["stop_mode"] == "supertrend":
        ex["st_period"] = r.randint(*[int(x) for x in EXIT_RANGES["st_period"]])
        ex["st_mult"] = round(r.uniform(*EXIT_RANGES["st_mult"]), 2)
    if ex["take_mode"] == "bb":
        ex["bb_p"] = r.randint(*[int(x) for x in EXIT_RANGES["bb_p"]])
        ex["bb_k"] = round(r.uniform(*EXIT_RANGES["bb_k"]), 2)
    if ex["take_mode"] == "rsi":
        ex["rsi_p"] = r.randint(*[int(x) for x in EXIT_RANGES["rsi_p"]])
        ex["rsi_hi"] = r.randint(*[int(x) for x in EXIT_RANGES["rsi_hi"]])
        ex["rsi_lo"] = r.randint(*[int(x) for x in EXIT_RANGES["rsi_lo"]])
    if r.random() < 0.4:
        ex["be_after"] = round(r.uniform(*EXIT_RANGES["be_after"]), 2)
    if r.random() < 0.35:
        ex["ema_exit"] = r.randint(*[int(x) for x in EXIT_RANGES["ema_exit"]])
    return ex


def mutate_exits(ex, rng=None):
    r = rng or random
    ex = dict(ex or {})
    op = r.random()
    if op < 0.30:
        ex["stop_mode"] = r.choice(STOP_MODES)
        if ex["stop_mode"] == "supertrend" and "st_mult" not in ex:
            ex["st_period"] = r.randint(7, 20); ex["st_mult"] = round(r.uniform(1.5, 4.0), 2)
    elif op < 0.55:
        ex["take_mode"] = r.choice(TAKE_MODES)
        if ex["take_mode"] == "bb" and "bb_p" not in ex:
            ex["bb_p"] = r.randint(20, 100); ex["bb_k"] = round(r.uniform(1.5, 3.0), 2)
        if ex["take_mode"] == "rsi" and "rsi_p" not in ex:
            ex["rsi_p"] = r.randint(7, 28); ex["rsi_hi"] = r.randint(58, 90); ex["rsi_lo"] = r.randint(10, 42)
    elif op < 0.70:
        if r.random() < 0.5 or "be_after" in ex:
            ex["be_after"] = round(r.uniform(*EXIT_RANGES["be_after"]), 2) if r.random() < 0.7 else 0.0
        else:
            ex["ema_exit"] = r.randint(*[int(x) for x in EXIT_RANGES["ema_exit"]])
    else:
        k = r.choice(["st_mult", "bb_k", "bb_p", "rsi_hi", "rsi_lo", "ema_exit", "st_period"])
        if k in ex:
            lo, hi = EXIT_RANGES[k]
            if isinstance(lo, float):
                ex[k] = round(min(hi, max(lo, ex[k] * r.uniform(0.7, 1.4))), 2)
            else:
                ex[k] = int(min(hi, max(lo, ex[k] * r.uniform(0.7, 1.4))))
    return ex


def crossover_exits(a, b, rng=None):
    r = rng or random
    a, b = dict(a or {}), dict(b or {})
    child = {}
    for k in set(a) | set(b):
        src = a if (k in a and (k not in b or r.random() < 0.5)) else b
        child[k] = src[k]
    # согласованность: параметры режима должны существовать
    if child.get("stop_mode") == "supertrend" and "st_mult" not in child:
        child["st_period"] = r.randint(7, 20); child["st_mult"] = round(r.uniform(1.5, 4.0), 2)
    if child.get("take_mode") == "bb" and "bb_p" not in child:
        child["bb_p"] = r.randint(20, 100); child["bb_k"] = round(r.uniform(1.5, 3.0), 2)
    if child.get("take_mode") == "rsi" and "rsi_p" not in child:
        child["rsi_p"] = r.randint(7, 28); child["rsi_hi"] = r.randint(58, 90); child["rsi_lo"] = r.randint(10, 42)
    if child.get("stop_mode") != "supertrend":
        child.pop("st_period", None); child.pop("st_mult", None)
    if child.get("take_mode") != "bb":
        child.pop("bb_p", None); child.pop("bb_k", None)
    if child.get("take_mode") != "rsi":
        child.pop("rsi_p", None); child.pop("rsi_hi", None); child.pop("rsi_lo", None)
    return child


def exits_key(ex):
    """Компактная сигнатура для дедупа/отчётов."""
    ex = ex or {}
    if not ex:
        return "classic"
    return (f"{ex.get('stop_mode','atr')}/{ex.get('take_mode','atr')}"
            + (f"/be{ex['be_after']}" if ex.get("be_after") else "")
            + (f"/ema{ex['ema_exit']}" if ex.get("ema_exit") else ""))


# ---------------- маршрутизатор для честных контуров ----------------
def run_candidate(df, spec, cand, contracts, cap=20000.0, comm=5.0, slip_bps=1.0):
    """Единая точка входа для select_stable_pool/forward_test/plot_stable_pool.
    Без exit-генов → ОБЫЧНЫЙ futures_lab.run_backtest (бит-в-бит старое поведение).
    С exit-генами → честный run_loop с той же экономикой."""
    exits = cand.get("exits") or {}
    risk = cand.get("risk") or {}
    if not exits:
        from futures_lab import run_backtest
        return run_backtest(df, spec, cand["strategy"], cand.get("params") or {},
                            initial_cash=cap, contracts=contracts, max_contracts=contracts,
                            stop_atr=float(risk.get("stop_atr", 2.0)),
                            take_atr=float(risk.get("take_atr", 3.0)),
                            max_hold_bars=int(float(risk.get("max_hold", 48))),
                            commission_per_contract=comm, slippage_bps=slip_bps, debug_only=True)
    from futures_lab import build_signal
    data = df.copy().reset_index(drop=True)
    from futures_lab import atr as fl_atr
    data["atr"] = fl_atr(data, 14)
    sig = build_signal(data, cand["strategy"], cand.get("params") or {}).values
    arr = make_arr(data)
    return run_loop(arr, sig, risk, exits, cap, int(contracts), float(spec.point_value),
                    float(comm), float(slip_bps) / 10000.0, margin=float(spec.active_margin or 0.0))

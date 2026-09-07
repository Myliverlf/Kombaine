#!/usr/bin/env python3
"""neural_engine.py v3 — движок E4: нейросеть, обученная на наших бэктестах.

v3 (2026-09-05, итерация 3) — добавлены практики реального ML:
  1. PURGE границ train/val (Lopez de Prado): метки смотрят вперёд на max_hold
     баров — последние max_hold сэмплов train-сегмента удаляются, чтобы их
     разметка не пересекалась с val-окном. Устраняет реальную утечку.
  2. АНСАМБЛЬ (--ens, default 3): несколько моделей с разными сидами,
     усреднение логитов. Промышленный стандарт против переобучения.
  3. RANDOM SEARCH гиперпараметров: lr, weight_decay, dropout, размеры слоёв
     сэмплируются каждый цикл (детерминированно от сида).
  4. Метрика валидации — macro-F1 + logloss (не голая accuracy: при дисбалансе
     классов accuracy врёт). Ранняя остановка по F1.
  5. КАЛИБРОВКА ТЕМПЕРАТУРОЙ: T подбирается на val минимизацией NLL —
     вероятность 0.6 реально значит 60%.
  6. ВЕСА СЭМПЛОВ: бары с сильным гипотетическим PnL учат сильнее.
  7. КРОСС-РЫНОЧНЫЙ КОНТЕКСТ (fv3): IMOEX как режим рынка для ВСЕХ тикеров
     (ret12/ret48 индекса, RSI индекса, тренд индекса) — merge_asof, только
     прошлые/текущие бары, без будущего.
  8. REGIME-признаки: vol_regime (vol24/vol120), pos240 (позиция в 10-д канале).
  9. LayerNorm на входе + gradient clipping + cosine LR schedule.
 10. TRAIN-VAL GAP пишется в лидерборд — явный индикатор переобучения.
 11. После КАЖДОГО цикла — честный eval-бэктест через общий роутер run_candidate
     и сравнение «ЧТО БЫЛО → ЧТО СТАЛО» (state/nn_leaderboard.json).

Честность окон (жёсткая схема):
  [train .........][val][ eval-окно ][ holdout 60д ]
  train/val — обучение и ранняя остановка; eval — бэктест-сравнение циклов;
  holdout 60д — НЕ ВИДИТ НИКТО, его проверяет только tools/forward_test.py.

Paper-only. Никаких брокерских вызовов.
"""
import argparse, hashlib, json, sys, time
import numpy as np
import pandas as pd
from pathlib import Path

SC = Path("/root/prop-desk/strategy_combine")
FL = Path("/root/prop-desk/futures_lab")
sys.path.insert(0, str(SC)); sys.path.insert(0, str(FL)); sys.path.insert(0, str(SC / "tools"))
from state_io import atomic_write_json  # noqa: E402  # атомарная запись state-файлов

DATA = FL / "artifacts" / "tinkoff_futures_data"
MODELS = SC / "state" / "neural_models"
OUT = SC / "state" / "engine_candidates.json"
LEADERBOARD = SC / "state" / "nn_leaderboard.json"

HOLDOUT_DAYS = 60

# экономики разметки: (stop_atr, take_atr, max_hold_hours) — вариации выходов
ECONOMIES = [(1.5, 2.0, 24), (2.0, 3.0, 48), (3.0, 4.5, 96)]

FEATURES_V1 = ["ret1", "ret3", "ret6", "ret12", "ret24", "vol12", "vol24",
               "rsi14", "atr_ratio", "bb_pos", "ema50_dist", "macd_hist",
               "range_ratio", "body_ratio", "vol_z", "streak", "gap",
               "tod_sin", "tod_cos"]

FEATURES_V2 = FEATURES_V1 + [
    # объёмы (настоящие, из колонки volume)
    "vwap_dist", "vol_trend", "obv_slope", "amihud",
    # контекст рынка: дневной таймфрейм (только ЗАВЕРШЁННЫЕ дни, сдвиг на 1)
    "d_ret1", "d_ret5", "d_rsi14", "d_trend", "d_dist_hi20", "d_dist_lo20", "d_atr",
    # календарь
    "dow_sin", "dow_cos",
    # свечные паттерны
    "wick_up", "wick_dn", "engulf",
]

FEATURES_V3 = FEATURES_V2 + [
    # кросс-рыночный контекст: индекс IMOEX как режим рынка (для всех тикеров)
    "imo_ret12", "imo_ret48", "imo_rsi", "imo_trend",
    # режимы
    "vol_regime", "pos240",
]


def feat_list(fv):
    return FEATURES_V1 if fv == 1 else (FEATURES_V2 if fv == 2 else FEATURES_V3)


def load(ticker):
    """Максимально длинная история тикера: 1095d если есть, иначе 365d."""
    for f in (f"{ticker}_1095d_1h_continuous.csv", f"{ticker}_365d_1h_continuous.csv"):
        p = DATA / f
        if p.exists():
            df = pd.read_csv(p).reset_index(drop=True)
            if len(df) > 1000:
                return df, f
    return None, None


_IMOEX_FEAT = None


def _imoex_features():
    """Признаки индекса IMOEX на его собственной сетке (кэш)."""
    global _IMOEX_FEAT
    if _IMOEX_FEAT is not None:
        return _IMOEX_FEAT
    df, _ = load("IMOEX")
    if df is None:
        _IMOEX_FEAT = pd.DataFrame(columns=["time", "imo_ret12", "imo_ret48", "imo_rsi", "imo_trend"])
        return _IMOEX_FEAT
    c = df["close"]
    t = pd.to_datetime(df["time"])
    out = pd.DataFrame({"time": t.values})
    out["imo_ret12"] = np.log(c / c.shift(12)).values
    out["imo_ret48"] = np.log(c / c.shift(48)).values
    out["imo_rsi"] = _rsi(c).values
    ema50 = c.ewm(span=50, adjust=False).mean()
    out["imo_trend"] = (c / ema50 - 1).values
    out = out.sort_values("time").reset_index(drop=True)
    _IMOEX_FEAT = out
    return out


# ---------------- признаки ----------------
def _rsi(c, n=14):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 * up / (up + dn + 1e-12) / 50 - 1   # [-1, 1]


def features(df: pd.DataFrame, fv: int = 3) -> pd.DataFrame:
    """Причинные признаки бара. Никакого подглядывания в будущее:
    дневные фичи — только из ЗАВЕРШЁННЫХ предыдущих дней (shift(1));
    индексные — merge_asof backward (последний известный бар IMOEX)."""
    from futures_lab import atr as fl_atr
    c, h, l, o = df["close"], df["high"], df["low"], df["open"]
    v = df.get("volume")
    if v is None or v.abs().sum() == 0:
        v = pd.Series(1.0, index=df.index)
    ret = np.log(c / c.shift(1))
    X = pd.DataFrame(index=df.index)
    for k in (1, 3, 6, 12, 24):
        X[f"ret{k}"] = np.log(c / c.shift(k))
    X["vol12"] = ret.rolling(12).std()
    X["vol24"] = ret.rolling(24).std()
    X["rsi14"] = _rsi(c)
    a = fl_atr(df, 14)
    X["atr_ratio"] = a / c
    ma20 = c.rolling(20).mean(); sd20 = c.rolling(20).std()
    X["bb_pos"] = (c - ma20) / (2 * sd20 + 1e-12)
    ema50 = c.ewm(span=50, adjust=False).mean()
    X["ema50_dist"] = c / ema50 - 1
    e12 = c.ewm(span=12, adjust=False).mean(); e26 = c.ewm(span=26, adjust=False).mean()
    macd = e12 - e26
    sig = macd.ewm(span=9, adjust=False).mean()
    X["macd_hist"] = (macd - sig) / (a + 1e-12)
    X["range_ratio"] = (h - l) / (a + 1e-12)
    X["body_ratio"] = (c - o) / (h - l + 1e-12)
    vm = v.rolling(48).mean(); vs = v.rolling(48).std()
    X["vol_z"] = ((v - vm) / (vs + 1e-12)).clip(-3, 3)
    sgn = np.sign(c.diff()).fillna(0).values
    chg = np.r_[True, sgn[1:] != sgn[:-1]]
    run = np.arange(len(sgn)) - (np.r_[0, np.cumsum(chg)[:-1]])
    X["streak"] = np.where(sgn >= 0, run, -run).clip(-6, 6) / 6
    X["gap"] = o / c.shift(1) - 1
    t = pd.to_datetime(df["time"])
    tod = t.dt.hour + t.dt.minute / 60
    X["tod_sin"] = np.sin(2 * np.pi * tod / 24)
    X["tod_cos"] = np.cos(2 * np.pi * tod / 24)
    if fv >= 2:
        # --- объёмы ---
        vwap20 = (v * c).rolling(20).sum() / (v.rolling(20).sum() + 1e-12)
        X["vwap_dist"] = c / vwap20 - 1
        X["vol_trend"] = np.log((v.rolling(12).mean() + 1) / (v.rolling(48).mean() + 1))
        obv = (np.sign(c.diff().fillna(0)) * v).rolling(12).sum()
        X["obv_slope"] = (obv - obv.shift(12)) / (v.rolling(48).sum() + 1e-12)
        X["amihud"] = ((ret.abs() / (v + 1e-12)).rolling(24).mean() * 1e9).clip(0, 10) / 10
        # --- дневной контекст (завершённые дни, shift(1) = без будущего) ---
        d = pd.DataFrame({"date": t.dt.date, "c": c.values, "h": h.values, "l": l.values})
        day = d.groupby("date").agg(c=("c", "last"), h=("h", "max"), l=("l", "min")).sort_index()
        dc = day["c"]
        day["d_ret1"] = dc.pct_change(1)
        day["d_ret5"] = dc.pct_change(5)
        day["d_rsi14"] = _rsi(dc)
        day["d_trend"] = dc / dc.ewm(span=20).mean() - dc / dc.ewm(span=50).mean()
        day["d_dist_hi20"] = dc / dc.rolling(20).max() - 1
        day["d_dist_lo20"] = dc / dc.rolling(20).min() - 1
        day["d_atr"] = (day["h"] - day["l"]) / dc
        day = day.shift(1)  # только ВЧЕРАШНИЙ завершённый день и раньше
        dmap = {k: day[k] for k in ["d_ret1", "d_ret5", "d_rsi14", "d_trend",
                                    "d_dist_hi20", "d_dist_lo20", "d_atr"]}
        idx = t.dt.date.values
        for k, s in dmap.items():
            X[k] = pd.Series(idx, index=df.index).map(s).astype(float)
        # --- календарь ---
        dow = t.dt.dayofweek.values.astype(float)
        X["dow_sin"] = np.sin(2 * np.pi * dow / 7)
        X["dow_cos"] = np.cos(2 * np.pi * dow / 7)
        # --- свечные паттерны ---
        rng = (h - l + 1e-12)
        X["wick_up"] = (h - np.maximum(o, c)) / rng
        X["wick_dn"] = (np.minimum(o, c) - l) / rng
        prev_body = (c.shift(1) - o.shift(1))
        body = c - o
        X["engulf"] = ((np.sign(body) != np.sign(prev_body)) &
                       (body.abs() > prev_body.abs())).astype(float) * np.sign(body)
    if fv >= 3:
        # --- кросс-рыночный контекст: IMOEX как режим рынка (merge_asof, без будущего) ---
        im = _imoex_features()
        if len(im):
            left = pd.DataFrame({"time": t.values, "_i": np.arange(len(t))}).sort_values("time")
            merged = pd.merge_asof(left, im, on="time", direction="backward")
            merged = merged.sort_values("_i")
            for k in ["imo_ret12", "imo_ret48", "imo_rsi", "imo_trend"]:
                X[k] = merged[k].values
        else:
            for k in ["imo_ret12", "imo_ret48", "imo_rsi", "imo_trend"]:
                X[k] = 0.0
        # --- режимы ---
        X["vol_regime"] = (ret.rolling(24).std() / (ret.rolling(120).std() + 1e-12)).clip(0, 5)
        lo240 = l.rolling(240, min_periods=48).min()
        hi240 = h.rolling(240, min_periods=48).max()
        X["pos240"] = ((c - lo240) / (hi240 - lo240 + 1e-12)).clip(-0.5, 1.5)
    return X[feat_list(fv)].replace([np.inf, -np.inf], np.nan)


# ---------------- честная разметка по нашей экономике ----------------
def label_pnl(h, l, c, a, stop_atr, take_atr, max_hold):
    """PnL гипотетического лонга/шорта (в ATR-единицах): стоп/тейк/горизонт.
    Консервативно: внутри бара сначала проверяем стоп."""
    n = len(c)
    lp = np.full(n, np.nan); sp = np.full(n, np.nan)
    for i in range(n - 1):
        ai = a[i]
        if not np.isfinite(ai) or ai <= 0:
            continue
        entry = c[i]
        jend = min(i + 1 + max_hold, n)
        stop, take = entry - stop_atr * ai, entry + take_atr * ai
        res = c[jend - 1] - entry
        for j in range(i + 1, jend):
            if l[j] <= stop:
                res = stop - entry; break
            if h[j] >= take:
                res = take - entry; break
        lp[i] = res / ai
        stop, take = entry + stop_atr * ai, entry - take_atr * ai
        res = entry - c[jend - 1]
        for j in range(i + 1, jend):
            if h[j] >= stop:
                res = entry - stop; break
            if l[j] <= take:
                res = entry - take; break
        sp[i] = res / ai
    return lp, sp


def make_labels(lp, sp):
    y = np.zeros(len(lp), dtype=np.int64)
    ok = np.isfinite(lp) & np.isfinite(sp)
    y[ok & (lp > 0) & (lp >= sp)] = 1
    y[ok & (sp > 0) & (sp > lp)] = 2
    # сила метки для весов сэмплов: |PnL| лучшей стороны, в ATR
    mag = np.where(y == 1, lp, np.where(y == 2, sp, 0.0))
    return y, ok, np.abs(mag)


# ---------------- окна честности ----------------
def windows(df):
    """(train_end_idx, eval_end_idx): eval-окно и holdout модель не видит."""
    t = pd.to_datetime(df["time"])
    tmax = t.max()
    hold_cut = tmax - pd.Timedelta(days=HOLDOUT_DAYS)
    span_days = (tmax - t.min()).days
    if span_days >= 500:           # 1095d: train до (max-365д), eval = 365-60
        train_cut = tmax - pd.Timedelta(days=365)
    else:                          # только 365d: eval = 90д перед holdout
        train_cut = tmax - pd.Timedelta(days=HOLDOUT_DAYS + 90)
    tr_end = int((t <= train_cut).sum())
    ev_end = int((t <= hold_cut).sum())
    return tr_end, ev_end


# ---------------- ML-метрики ----------------
def _softmax_np(z):
    z = z - z.max(1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(1, keepdims=True)


def _macro_f1(y, p):
    f1s = []
    for k in range(3):
        tp = float(((p == k) & (y == k)).sum())
        fp = float(((p == k) & (y != k)).sum())
        fn = float(((p != k) & (y == k)).sum())
        prec = tp / (tp + fp) if tp + fp > 0 else 0.0
        rec = tp / (tp + fn) if tp + fn > 0 else 0.0
        f1s.append(2 * prec * rec / (prec + rec) if prec + rec > 0 else 0.0)
    return float(np.mean(f1s))


def _nll(probs, y):
    return float(-np.log(probs[np.arange(len(y)), y] + 1e-12).mean())


def _fit_temp(logits_va, yva):
    """Температурная калибровка: T минимизирует NLL на val."""
    best_T, best = 1.0, 1e18
    for T in np.arange(0.6, 3.01, 0.1):
        nll = _nll(_softmax_np(logits_va / T), yva)
        if nll < best:
            best, best_T = nll, float(T)
    return round(best_T, 2)


# ---------------- обучение одного цикла (ансамбль) ----------------
def train_cycle(ticker, df, econ, seed, arch, hp, fv=3, ens=3):
    import torch
    import torch.nn as nn
    torch.manual_seed(seed); np.random.seed(seed)
    stop_atr, take_atr, max_hold = econ
    lr, wd, drop = hp["lr"], hp["wd"], hp["drop"]
    h1, h2 = arch

    tr_end, ev_end = windows(df)
    tr = df.iloc[:tr_end]
    if len(tr) < 1200:
        return None
    from futures_lab import atr as fl_atr
    X = features(tr, fv)
    a = fl_atr(tr, 14).values
    lp, sp = label_pnl(tr["high"].values, tr["low"].values, tr["close"].values,
                       a, stop_atr, take_atr, max_hold)
    y, ok, mag = make_labels(lp, sp)
    Xv = X.values.astype(np.float32)
    m = ok & np.isfinite(Xv).all(axis=1)
    Xv, yv, magv = Xv[m], y[m], mag[m]
    if len(yv) < 800:
        return None

    n_val = max(300, int(len(yv) * 0.2))
    if len(yv) - n_val < 400:
        return None
    Xtr, ytr, magtr = Xv[:-n_val], yv[:-n_val], magv[:-n_val]
    Xva, yva = Xv[-n_val:], yv[-n_val:]
    # PURGE (Lopez de Prado): метки последних max_hold баров train-сегмента
    # заглядывают в val-окно — удаляем их, чтобы не было утечки.
    purge = int(max_hold)
    if len(ytr) - purge >= 400:
        Xtr, ytr, magtr = Xtr[:-purge], ytr[:-purge], magtr[:-purge]

    mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-8
    Ztr = ((Xtr - mu) / sd).astype(np.float32)
    Zva = ((Xva - mu) / sd).astype(np.float32)

    cnt = np.bincount(ytr, minlength=3).astype(np.float32)
    cw = (cnt.sum() / (3 * np.maximum(cnt, 1))).astype(np.float32)
    # веса сэмплов: класс-веса × сила метки (1 + min(|PnL|,3)/3)
    sw = (cw[ytr] * (1.0 + np.minimum(magtr, 3.0) / 3.0)).astype(np.float32)

    d_in = Ztr.shape[1]

    def make_net():
        class Net(nn.Module):
            def __init__(s):
                super().__init__()
                s.f = nn.Sequential(nn.LayerNorm(d_in),
                                    nn.Linear(d_in, h1), nn.ReLU(), nn.Dropout(drop),
                                    nn.Linear(h1, h2), nn.ReLU(), nn.Dropout(drop),
                                    nn.Linear(h2, 3))

            def forward(s, x):
                return s.f(x)
        return Net()

    xt = torch.tensor(Ztr); yt = torch.tensor(ytr); wt = torch.tensor(sw)
    xv = torch.tensor(Zva); yv_t = torch.tensor(yva)

    states, val_logits_sum = [], np.zeros((len(Zva), 3), dtype=np.float64)
    train_logits_sum = np.zeros((len(Ztr), 3), dtype=np.float64)
    for ei in range(max(1, ens)):
        torch.manual_seed(seed + ei * 1013)
        net = make_net()
        opt = torch.optim.Adam(net.parameters(), lr=lr, weight_decay=wd)
        epochs = 80
        sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs)
        best_f1, best_state, bad = -1, None, 0
        for ep in range(epochs):
            net.train()
            perm = torch.randperm(len(xt))
            for k in range(0, len(xt), 256):
                idx = perm[k:k + 256]
                opt.zero_grad()
                logits = net(xt[idx])
                ce = nn.functional.cross_entropy(logits, yt[idx], reduction="none")
                loss = (ce * wt[idx]).sum() / wt[idx].sum()
                loss.backward()
                nn.utils.clip_grad_norm_(net.parameters(), 1.0)
                opt.step()
            sched.step()
            net.eval()
            with torch.no_grad():
                pv = net(xv).argmax(1).numpy()
            f1 = _macro_f1(yva, pv)
            if f1 > best_f1 + 1e-5:
                best_f1, bad = f1, 0
                best_state = {k2: v2.clone() for k2, v2 in net.state_dict().items()}
            else:
                bad += 1
                if bad >= 12:
                    break
        net.load_state_dict(best_state); net.eval()
        states.append(best_state)
        with torch.no_grad():
            val_logits_sum += net(xv).numpy().astype(np.float64)
            tl = []
            for k in range(0, len(xt), 2048):
                tl.append(net(xt[k:k + 2048]).numpy())
            train_logits_sum += np.vstack(tl).astype(np.float64)

    ens_va = val_logits_sum / max(1, ens)
    ens_tr = train_logits_sum / max(1, ens)
    T = _fit_temp(ens_va, yva)
    probs_va = _softmax_np(ens_va / T)
    pred_va = probs_va.argmax(1)
    val_f1 = round(_macro_f1(yva, pred_va), 4)
    val_ll = round(_nll(probs_va, yva), 4)
    train_f1 = round(_macro_f1(ytr, _softmax_np(ens_tr / T).argmax(1)), 4)
    gap = round(train_f1 - val_f1, 4)   # индикатор переобучения

    # порог уверенности: 0.5–15% сигналов на val (по КАЛИБРОВАННЫМ вероятностям)
    conf = probs_va.max(1)
    thr = 0.55
    for cand in np.arange(0.40, 0.95, 0.01):
        if 0.005 <= float((conf >= cand).mean()) <= 0.15:
            thr = float(cand); break

    # сигнал на ВСЁМ df (фичи считаются с полной историей — без NaN-мусора)
    Xall = features(df, fv)
    Xall_v = np.nan_to_num(((Xall.values.astype(np.float32) - mu.astype(np.float32))
                            / sd.astype(np.float32)), nan=0.0, posinf=0.0, neginf=0.0)
    logits_all = np.zeros((len(Xall_v), 3), dtype=np.float64)
    with torch.no_grad():
        xa = torch.tensor(Xall_v)
        for st in states:
            net2 = make_net(); net2.load_state_dict(st); net2.eval()
            la = []
            for k in range(0, len(xa), 4096):
                la.append(net2(xa[k:k + 4096]).numpy())
            logits_all += np.vstack(la).astype(np.float64)
    logits_all /= max(1, ens)
    p_all = _softmax_np(logits_all / T)
    cls = p_all.argmax(1); cmax = p_all.max(1)
    sig_all = np.zeros(len(p_all), dtype=int)
    sig_all[(cls == 1) & (cmax >= thr)] = 1
    sig_all[(cls == 2) & (cmax >= thr)] = -1

    name = "nn_" + hashlib.sha256(json.dumps(
        [ticker, seed, list(econ), list(arch), hp["lr"], hp["wd"], fv, ens,
         round(thr, 3), round(T, 2), len(yv)], sort_keys=True).encode()).hexdigest()[:10]
    MODELS.mkdir(parents=True, exist_ok=True)
    torch.save({"state_dicts": states, "mu": mu.tolist(), "sd": sd.tolist(),
                "features": feat_list(fv), "fv": fv, "thr": thr, "temp": T,
                "econ": list(econ), "arch": list(arch),
                "hp": {"lr": lr, "wd": wd, "drop": drop}, "ens": len(states),
                "val_f1": val_f1, "gap": gap}, MODELS / f"{name}.pt")

    return {"name": name, "ticker": ticker, "econ": econ, "arch": arch, "seed": seed,
            "fv": fv, "hp": hp, "ens": len(states), "sig_all": sig_all,
            "tr_end": tr_end, "ev_end": ev_end,
            "val_f1": val_f1, "val_ll": val_ll, "train_f1": train_f1, "gap": gap,
            "temp": T, "thr": round(thr, 3),
            "train_bars": int(len(ytr)), "n_sig_val": int((sig_all[tr_end:ev_end] != 0).sum()),
            "risk": {"stop_atr": stop_atr, "take_atr": take_atr, "max_hold": max_hold}}


# ---------------- честный eval-бэктест цикла ----------------
def eval_cycle(r, df):
    """Бэктест модели на EVAL-ОКНЕ (между train и holdout) через общий роутер
    run_candidate: комиссии, ГО, слип — всё как в воронке. 1 контракт."""
    import futures_lab
    from tools.select_stable_pool import _synthetic_spec_for_file, CAP, COMM, SLIP, equity_stats
    from engines.exit_engine import run_candidate
    spec = _synthetic_spec_for_file(r["ticker"])
    if spec is None:
        return None
    ev = df.iloc[r["tr_end"]:r["ev_end"]].reset_index(drop=True)
    if len(ev) < 300:
        return None
    sig_ev = r["sig_all"][r["tr_end"]:r["ev_end"]]
    if int((sig_ev != 0).sum()) < 5:
        return {"pnl": 0.0, "dd": 0.0, "calmar": 0.0, "median_month": 0.0,
                "trades": int((sig_ev != 0).sum()), "profit_months": 0, "months": 0,
                "eval_days": int((pd.to_datetime(ev['time']).max() - pd.to_datetime(ev['time']).min()).days)}
    cand = {"strategy": r["name"], "ticker": r["ticker"], "params": {},
            "risk": r["risk"], "exits": {}}
    futures_lab.STRATEGY_FUNCS[r["name"]] = (lambda s: lambda d, **kw: pd.Series(s[:len(d)], index=d.index))(sig_ev)
    try:
        _, trades, eq = run_candidate(ev, spec, cand, 1, cap=CAP, comm=COMM, slip_bps=SLIP)
    finally:
        futures_lab.STRATEGY_FUNCS.pop(r["name"], None)
    if eq is None or len(eq) < 100:
        return None
    st = equity_stats(eq)
    cal = st["pnl"] / st["dd"] if st["dd"] > 0 else (10.0 if st["pnl"] > 0 else 0.0)
    return {"pnl": round(st["pnl"]), "dd": round(st["dd"]), "calmar": round(cal, 2),
            "median_month": round(st["median_month"]), "trades": len(trades),
            "profit_months": st["profit_months"], "months": st["months"],
            "eval_days": int((pd.to_datetime(ev['time']).max() - pd.to_datetime(ev['time']).min()).days)}


# ---------------- инференс сохранённых весов (для register.py / воронки) ----------------
_NET_CACHE = {}


def _load_weights(name):
    """Загрузка весов модели/ансамбля в _NET_CACHE (v2 и v3 форматы)."""
    import torch
    import torch.nn as nn
    if name in _NET_CACHE:
        return _NET_CACHE[name]
    p = MODELS / f"{name}.pt"
    if not p.exists():
        raise FileNotFoundError(p)
    art = torch.load(p, weights_only=False)
    fv = int(art.get("fv", 1))
    d = len(art["features"])
    shapes0 = (art.get("state_dicts") or [art["state_dict"]])[0]
    sh = {k: tuple(v.shape) for k, v in shapes0.items()}
    # v3: f.0=LayerNorm, f.1=Linear; v2: f.0=Linear
    if "f.1.weight" in sh:
        h1 = sh["f.1.weight"][0]; h2 = sh["f.4.weight"][0]
        layernorm = True
    else:
        h1 = sh["f.0.weight"][0]; h2 = sh["f.3.weight"][0]
        layernorm = False
    drop = float(art.get("hp", {}).get("drop", 0.2))

    class Net(nn.Module):
        def __init__(s, din):
            super().__init__()
            if layernorm:
                s.f = nn.Sequential(nn.LayerNorm(din),
                                    nn.Linear(din, h1), nn.ReLU(), nn.Dropout(drop),
                                    nn.Linear(h1, h2), nn.ReLU(), nn.Dropout(drop),
                                    nn.Linear(h2, 3))
            else:
                s.f = nn.Sequential(nn.Linear(din, h1), nn.ReLU(), nn.Dropout(drop),
                                    nn.Linear(h1, h2), nn.ReLU(), nn.Linear(h2, 3))

        def forward(s, x):
            return s.f(x)

    nets = []
    for st in (art.get("state_dicts") or [art["state_dict"]]):
        net = Net(d); net.load_state_dict(st); net.eval()
        nets.append(net)
    _NET_CACHE[name] = (nets, np.array(art["mu"], dtype=np.float32),
                        np.array(art["sd"], dtype=np.float32),
                        float(art["thr"]), float(art.get("temp", 1.0)), fv)
    return _NET_CACHE[name]


def neural_probs(name, df):
    """Вероятности ансамбля [p_short, p_flat, p_long] для каждого бара (калиброванные T).
    Никакого дообучения на лету — только сохранённые веса."""
    import torch
    nets, mu, sd, thr, T, fv = _load_weights(name)
    X = features(df, fv)
    Xv = np.nan_to_num((X.values.astype(np.float32) - mu) / sd,
                       nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)
    with torch.no_grad():
        xt = torch.tensor(Xv)
        logits = np.zeros((len(xt), 3), dtype=np.float64)
        for net in nets:
            la = []
            for k in range(0, len(xt), 4096):
                la.append(net(xt[k:k + 4096]).numpy())
            logits += np.vstack(la).astype(np.float64)
        logits /= len(nets)
    return _softmax_np(logits / T), thr


def neural_signal(name, df):
    """Сигнал обученной модели (или ансамбля): -1/0/+1.
    Веса — state/neural_models/<name>.pt. НИКАКОГО дообучения на лету."""
    p, thr = neural_probs(name, df)
    cls = p.argmax(1); cmax = p.max(1)
    s = pd.Series(0, index=df.index, dtype=int)
    s[(cls == 1) & (cmax >= thr)] = 1
    s[(cls == 2) & (cmax >= thr)] = -1
    return s


# ---------------- лидерборд: что было → что стало ----------------
def load_lb():
    if LEADERBOARD.exists():
        try:
            return json.loads(LEADERBOARD.read_text())
        except Exception:
            pass
    return {"runs": [], "best": {}}


def lb_best_per_ticker(entries):
    best = {}
    for e in entries:
        if not e.get("eval") or e["eval"].get("trades", 0) < 5:
            continue
        tk = e["ticker"]
        key = (e["eval"]["pnl"] > 0, e["eval"]["calmar"])
        if tk not in best or key > (best[tk]["eval"]["pnl"] > 0, best[tk]["eval"]["calmar"]):
            best[tk] = e
    return best


def sample_hp(rng):
    """Random search гиперпараметров (детерминированно от сида цикла)."""
    return {"lr": float(10 ** rng.uniform(-3.3, -2.4)),      # ~5e-4 .. 4e-3
            "wd": float(10 ** rng.uniform(-6.0, -3.0)),      # 1e-6 .. 1e-3
            "drop": round(float(rng.uniform(0.10, 0.35)), 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default="CNY,IMOEX,GAZP,SBER,LKOH,Si")
    ap.add_argument("--cycles", type=int, default=50, help="сколько циклов обучения прогнать")
    ap.add_argument("--seed0", type=int, default=1000)
    ap.add_argument("--fv", type=int, default=3, choices=[1, 2, 3])
    ap.add_argument("--ens", type=int, default=3, help="моделей в ансамбле")
    args = ap.parse_args()
    t0 = time.time()

    lb = load_lb()
    prev_best = dict(lb.get("best", {}))

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    data = {}
    for tk in tickers:
        df, fname = load(tk)
        if df is None:
            print(f"  [{tk}] no data, skip"); continue
        tr_end, ev_end = windows(df)
        span = (pd.to_datetime(df['time']).max() - pd.to_datetime(df['time']).min()).days
        print(f"  [{tk}] {fname}: {len(df)} баров ({span}д), train≤{tr_end}, eval {tr_end}..{ev_end}, holdout 60д невидим")
        data[tk] = df

    arches = [(64, 32), (128, 64), (32, 16), (128, 32), (64, 64), (96, 48)]
    entries = []
    cyc = 0
    done = False
    while cyc < args.cycles and not done:
        done = True
        for tk in tickers:
            if tk not in data:
                continue
            for econ in ECONOMIES:
                for ai in range(len(arches)):
                    if cyc >= args.cycles:
                        break
                    seed = args.seed0 + cyc * 17 + ai * 3
                    rng = np.random.default_rng(seed)
                    arch = arches[int(rng.integers(len(arches)))]
                    hp = sample_hp(rng)
                    r = train_cycle(tk, data[tk], econ, seed, arch, hp, args.fv, args.ens)
                    cyc += 1
                    done = False
                    if r is None:
                        print(f"  cycle {cyc}/{args.cycles} [{tk}] econ={econ} — мало данных, skip")
                        continue
                    ev = eval_cycle(r, data[tk])
                    if ev is None:
                        print(f"  cycle {cyc}/{args.cycles} [{tk}] econ={econ} f1={r['val_f1']} — eval не удался")
                        continue
                    e = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ"), "run_id": t0,
                         "ticker": tk, "name": r["name"], "econ": list(econ),
                         "arch": list(r["arch"]), "seed": seed, "fv": args.fv,
                         "ens": r["ens"], "hp": r["hp"],
                         "val_f1": r["val_f1"], "val_ll": r["val_ll"],
                         "train_f1": r["train_f1"], "gap": r["gap"],
                         "temp": r["temp"], "thr": r["thr"],
                         "train_bars": r["train_bars"], "eval": ev}
                    entries.append(e)
                    print(f"  cycle {cyc}/{args.cycles} [{tk}] econ={econ[0]}/{econ[1]}/{econ[2]} "
                          f"f1={r['val_f1']} gap={r['gap']} T={r['temp']} → EVAL {ev['eval_days']}д: "
                          f"pnl={ev['pnl']:+}₽ dd={ev['dd']}₽ calmar={ev['calmar']} trades={ev['trades']} "
                          f"мес+={ev['profit_months']}/{ev['months']}")

    # --- лидерборд: что было → что стало ---
    new_best = lb_best_per_ticker(entries)
    all_entries = lb.get("runs", []) + entries
    all_entries = all_entries[-2000:]
    best_merged = {}
    for src in (prev_best, new_best):
        for tk, e in src.items():
            if not e.get("eval"):
                continue
            cur = best_merged.get(tk)
            key = (e["eval"]["pnl"] > 0, e["eval"]["calmar"])
            if cur is None or key > (cur["eval"]["pnl"] > 0, cur["eval"]["calmar"]):
                best_merged[tk] = e
    atomic_write_json(LEADERBOARD, {"runs": all_entries, "best": best_merged})

    print("\n=== ЧТО БЫЛО → ЧТО СТАЛО (лучшее на тикер, eval-окно) ===")
    for tk in tickers:
        was = prev_best.get(tk); now = new_best.get(tk) or best_merged.get(tk)
        w = f"pnl={was['eval']['pnl']:+}₽ cal={was['eval']['calmar']} ({was['name']})" if was and was.get("eval") else "—"
        n = f"pnl={now['eval']['pnl']:+}₽ cal={now['eval']['calmar']} мес+={now['eval']['profit_months']}/{now['eval']['months']} ({now['name']})" if now and now.get("eval") else "—"
        verdict = ""
        if was and was.get("eval") and now and now.get("eval"):
            verdict = "ЛУЧШЕ" if (now["eval"]["pnl"], now["eval"]["calmar"]) > (was["eval"]["pnl"], was["eval"]["calmar"]) else "хуже/так же"
        print(f"  {tk:7s} было: {w}\n          стало: {n}  {verdict}")

    # --- кандидаты в общий пул: только eval-плюс с calmar>=1 и >=8 сделок ---
    good = [e for e in entries if e["eval"]["pnl"] > 0 and e["eval"]["calmar"] >= 1.0
            and e["eval"]["trades"] >= 8 and e["eval"]["median_month"] > 0]
    good.sort(key=lambda e: -e["eval"]["calmar"])
    good = good[:12]  # не раздувать пул мусором

    old = []
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text()).get("candidates", [])
        except Exception:
            old = []
    merged = {c["name"]: c for c in old}
    for e in good:
        merged[e["name"]] = {
            "engine": "neural", "ticker": e["ticker"], "name": e["name"],
            "desc": (f"MLP-ансамбль x{e['ens']} {e['arch'][0]}-{e['arch'][1]} fv{e['fv']}, экономика "
                     f"stop {e['econ'][0]} ATR / take {e['econ'][1]} ATR / hold {e['econ'][2]}ч, "
                     f"val_f1 {e['val_f1']}, gap {e['gap']}, eval {e['eval']['pnl']:+}₽ cal {e['eval']['calmar']}"),
            "risk": {"stop_atr": e["econ"][0], "take_atr": e["econ"][1], "max_hold": e["econ"][2]},
            "exits": {}, "fv": e["fv"], "stats": {"val_f1": e["val_f1"], "gap": e["gap"],
                                                  "thr": e["thr"], "temp": e["temp"],
                                                  "eval": e["eval"], "train_bars": e["train_bars"]},
            "model_file": str(MODELS / f"{e['name']}.pt")}
    atomic_write_json(OUT, {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "candidates": list(merged.values())})

    print(f"\nNEURAL ENGINE E4 v3: циклов {cyc}, eval-бэктестов {len(entries)}, "
          f"в пул прошло {len(good)}, всего кандидатов {len(merged)}, {time.time()-t0:.0f}s")
    print(f"SAVED {OUT}\nSAVED {LEADERBOARD}")
    print("Дальше: python3 tools/select_stable_pool.py --holdout-days 60 && python3 tools/forward_test.py")


if __name__ == "__main__":
    main()

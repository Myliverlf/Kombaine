"""genome.py — DSL сигналов для генетического движка.

Геном = {"long": [atom, ...], "short": [atom, ...]}
atom = [name, p1, p2] — сериализуемо в JSON, функция строится обратно из генома.
signal = +1 когда ВСЕ long-атомы истинны, -1 когда ВСЕ short-атомы истинны, иначе 0.

Ключевое свойство: геном = стратегия. Его можно сохранить, передать в другой
движок/прогон, восстановить как функцию и зарегистрировать в futures_lab.STRATEGY_FUNCS
под именем "ge_<hash>" — дальше работает ОБЫЧНЫЙ бэктест-контур комбайна.
"""
from __future__ import annotations
import hashlib, json
import numpy as np
import pandas as pd


# ---------- индикаторы ----------
def _ema(s: pd.Series, p: int) -> pd.Series:
    return s.ewm(span=max(2, int(p)), adjust=False).mean()


def _rsi(close: pd.Series, p: int) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1.0 / max(2, int(p)), adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1.0 / max(2, int(p)), adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100.0 - 100.0 / (1.0 + rs)).fillna(50.0)


def _atr_pct(df: pd.DataFrame, p: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return (tr.ewm(alpha=1.0 / max(2, int(p)), adjust=False).mean() / c).replace([np.inf, -np.inf], np.nan)


def _adx(df: pd.DataFrame, p: int = 14) -> pd.Series:
    p = max(2, int(p))
    up, dn = df["high"].diff(), -df["low"].diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / p, adjust=False).mean().replace(0, np.nan)
    pdi = 100 * pd.Series(plus, index=df.index).ewm(alpha=1.0 / p, adjust=False).mean() / atr
    mdi = 100 * pd.Series(minus, index=df.index).ewm(alpha=1.0 / p, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1.0 / p, adjust=False).mean().fillna(0.0)


def _macd_hist(close: pd.Series) -> pd.Series:
    return _ema(close, 12) - _ema(close, 26) - _ema(_ema(close, 12) - _ema(close, 26), 9)


def _cci(df: pd.DataFrame, p: int = 20) -> pd.Series:
    p = max(2, int(p))
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    ma = tp.rolling(p).mean()
    md = (tp - ma).abs().rolling(p).mean().replace(0, np.nan)
    return ((tp - ma) / (0.015 * md)).fillna(0.0)


# ---------- атомы условий (bool series) ----------
ATOMS = {
    "trend_up":    lambda df, p: _ema(df["close"], p) > _ema(df["close"], p * 3),          # p in [8..96]
    "trend_dn":    lambda df, p: _ema(df["close"], p) < _ema(df["close"], p * 3),
    "rsi_lo":      lambda df, p, thr: _rsi(df["close"], p) < thr,                          # p [6..48], thr [15..45]
    "rsi_hi":      lambda df, p, thr: _rsi(df["close"], p) > thr,                          # thr [55..85]
    "brk_hi":      lambda df, p: df["close"] > df["high"].rolling(p).max().shift(1),       # p [12..120]
    "brk_lo":      lambda df, p: df["close"] < df["low"].rolling(p).min().shift(1),
    "adx_hi":      lambda df, p, thr: _adx(df, p) > thr,                                   # thr [18..40]
    "adx_lo":      lambda df, p, thr: _adx(df, p) < thr,
    "vol_hi":      lambda df, p, x: df["volume"] > df["volume"].rolling(p).mean() * x,     # x [1.2..3.0]
    "macd_pos":    lambda df: _macd_hist(df["close"]) > 0,
    "macd_neg":    lambda df: _macd_hist(df["close"]) < 0,
    "cci_hi":      lambda df, p, thr: _cci(df, p) > thr,                                   # thr [80..200]
    "cci_lo":      lambda df, p, thr: _cci(df, p) < -thr,
    "bb_up":       lambda df, p: df["close"] > df["close"].rolling(p).mean() + 2 * df["close"].rolling(p).std(),
    "bb_dn":       lambda df, p: df["close"] < df["close"].rolling(p).mean() - 2 * df["close"].rolling(p).std(),
    "volcalm_hi":  lambda df, p: _atr_pct(df, p) > _atr_pct(df, p).rolling(96).median(),
    "volcalm_lo":  lambda df, p: _atr_pct(df, p) < _atr_pct(df, p).rolling(96).median(),
    # --- v2 атомы (2026-09-05): гэпы, серии, сжатие, импульс N баров ---
    "gap_up":      lambda df, x: df["open"] > df["close"].shift(1) * (1.0 + x),
    "gap_dn":      lambda df, x: df["open"] < df["close"].shift(1) * (1.0 - x),
    "streak_dn":   lambda df, p: (df["close"].diff() < 0).rolling(int(p)).sum() >= p,
    "streak_up":   lambda df, p: (df["close"].diff() > 0).rolling(int(p)).sum() >= p,
    "ret_dn":      lambda df, p, x: df["close"].pct_change(int(p)) < -x,
    "ret_up":      lambda df, p, x: df["close"].pct_change(int(p)) > x,
    "nr_lo":       lambda df, p: (df["high"] - df["low"]) <= (df["high"] - df["low"]).rolling(int(p)).min(),
}

# диапазоны параметров для случайной генерации/мутации: name -> [param ranges]
ATOM_PARAMS = {
    "trend_up":   [(8, 96)], "trend_dn": [(8, 96)],
    "rsi_lo":     [(6, 48), (15, 45)], "rsi_hi": [(6, 48), (55, 85)],
    "brk_hi":     [(12, 120)], "brk_lo": [(12, 120)],
    "adx_hi":     [(7, 28), (18, 40)], "adx_lo": [(7, 28), (10, 22)],
    "vol_hi":     [(12, 96), (1.2, 3.0)],
    "macd_pos":   [], "macd_neg": [],
    "cci_hi":     [(10, 40), (80, 200)], "cci_lo": [(10, 40), (80, 200)],
    "bb_up":      [(20, 120)], "bb_dn": [(20, 120)],
    "volcalm_hi": [(14, 48)], "volcalm_lo": [(14, 48)],
    "gap_up": [(0.001, 0.01)], "gap_dn": [(0.001, 0.01)],
    "streak_dn": [(2, 6)], "streak_up": [(2, 6)],
    "ret_dn": [(6, 72), (0.004, 0.03)], "ret_up": [(6, 72), (0.004, 0.03)],
    "nr_lo": [(12, 120)],
}

LONG_ATOMS = ["trend_up", "rsi_lo", "brk_hi", "adx_hi", "vol_hi", "macd_pos", "cci_hi", "cci_lo", "bb_dn", "volcalm_hi", "volcalm_lo", "gap_up", "streak_dn", "ret_dn", "nr_lo"]
SHORT_ATOMS = ["trend_dn", "rsi_hi", "brk_lo", "adx_hi", "vol_hi", "macd_neg", "cci_lo", "cci_hi", "bb_up", "volcalm_hi", "volcalm_lo", "gap_dn", "streak_up", "ret_up", "nr_lo"]

# --- рисковые гены (v2): стоп/тейк/удержание эволюционируют вместе с сигналами ---
RISK_RANGES = {"stop_atr": (0.8, 3.5), "take_atr": (1.2, 6.0), "max_hold": (8, 160)}
DEFAULT_RISK = {"stop_atr": 2.0, "take_atr": 3.0, "max_hold": 48}


def genome_risk(genome: dict) -> dict:
    r = dict(DEFAULT_RISK)
    r.update(genome.get("risk") or {})
    return r


def _atom_eval(df: pd.DataFrame, atom: list) -> pd.Series:
    name = atom[0]
    args = [float(x) if isinstance(x, float) else int(x) for x in atom[1:]]
    out = ATOMS[name](df, *args)
    return out.fillna(False).astype(bool)


def genome_signal(df: pd.DataFrame, genome: dict) -> pd.Series:
    sig = pd.Series(0, index=df.index, dtype=int)
    longs = genome.get("long") or []
    shorts = genome.get("short") or []
    # v4 (audit 2026-09-06): голосование k-из-N вместо строгого И.
    # vote={"long": k, "short": m} — сигнал когда минимум k/m атомов истинны.
    # Без vote (старые геномы) — прежнее строгое И, бит-в-бит совместимо.
    vote = genome.get("vote") or {}

    def _count(atoms):
        cnt = np.zeros(len(df), dtype=np.int32)
        for a in atoms:
            cnt += _atom_eval(df, a).values.astype(np.int32)
        return cnt

    if longs:
        k = int(vote.get("long", len(longs)))
        k = max(1, min(k, len(longs)))
        if k >= len(longs):
            sig[_count(longs) >= len(longs)] = 1
        else:
            sig[_count(longs) >= k] = 1
    if shorts:
        m = int(vote.get("short", len(shorts)))
        m = max(1, min(m, len(shorts)))
        cnt = _count(shorts) >= m
        # short не перетирает уже выставленный long в том же баре
        sig[cnt & (sig.values == 0)] = -1
    return sig


def genome_name(genome: dict) -> str:
    h = hashlib.sha256(json.dumps(genome, sort_keys=True).encode()).hexdigest()[:10]
    return f"ge_{h}"


def genome_desc(genome: dict) -> str:
    fmt = lambda a: a[0] + "(" + ",".join(f"{x:g}" for x in a[1:]) + ")"
    return "L[" + " & ".join(fmt(a) for a in genome.get("long") or []) + "] S[" + " & ".join(fmt(a) for a in genome.get("short") or []) + "]"

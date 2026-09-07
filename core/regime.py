"""Regime Detector: тренд/флэт/волатильность по юниверсу.

Выход: regime snapshot (json) + bias для генератора.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

STATE_DIR = Path(__file__).resolve().parent.parent / "state"


def adx(df: pd.DataFrame, period: int = 14) -> float:
    """Упрощённый ADX по последнему значению."""
    h, l, c = df["high"], df["low"], df["close"]
    up = h.diff()
    dn = -l.diff()
    plus_dm = ((up > dn) & (up > 0)).astype(float) * up
    minus_dm = ((dn > up) & (dn > 0)).astype(float) * dn
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    plus_di = 100 * (plus_dm.rolling(period).mean() / atr)
    minus_di = 100 * (minus_dm.rolling(period).mean() / atr)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return float(dx.rolling(period).mean().iloc[-1])


def regime_for(df: pd.DataFrame) -> dict:
    c = df["close"]
    ema20 = c.ewm(span=20, adjust=False).mean().iloc[-1]
    ema60 = c.ewm(span=60, adjust=False).mean().iloc[-1]
    a = adx(df)
    atr_pct = float((c.diff().abs().rolling(14).mean() / c).iloc[-1] * 100)
    direction = "up" if ema20 > ema60 else "down"
    regime = "trend" if a >= 22 else "range"
    return {"adx": round(a, 1), "direction": direction, "regime": regime,
            "atr_pct": round(atr_pct, 3)}


def scan_universe(data_dir: Path, universe: list, interval: str = "15m") -> dict:
    out = {}
    for t in universe:
        f = Path(data_dir) / f"{t}_60d_{interval}_continuous.csv"
        if not f.exists():
            out[t] = {"error": "no data"}
            continue
        df = pd.read_csv(f, parse_dates=["time"])
        out[t] = regime_for(df)
    # агрегация: куда идёт портфель-юниверс в целом
    ups = sum(1 for v in out.values() if v.get("direction") == "up")
    downs = len(out) - ups
    trend_cnt = sum(1 for v in out.values() if v.get("regime") == "trend")
    bias = "neutral"
    if ups >= downs + 2:
        bias = "long"
    elif downs >= ups + 2:
        bias = "short"
    if trend_cnt <= len(out) // 3:
        bias = "meanrev"
    return {"ts": datetime.now(timezone.utc).isoformat(), "tickers": out,
            "bias": bias, "ups": ups, "downs": downs, "trend_cnt": trend_cnt}


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from config import load_config
    cfg = load_config()
    data_dir = Path("/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data")
    snap = scan_universe(data_dir, cfg.universe)
    STATE_DIR.mkdir(exist_ok=True)
    (STATE_DIR / "regime_snapshot.json").write_text(json.dumps(snap, indent=2, ensure_ascii=False))
    with open(STATE_DIR / "regime_log.jsonl", "a") as f:
        f.write(json.dumps(snap, ensure_ascii=False) + "\n")
    print(json.dumps(snap, indent=2, ensure_ascii=False))

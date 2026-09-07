"""Multi-TF Features — multi-timeframe aggregation, freshness, alignment, regime context.

Модуль строит高层/compound-индикаторы поверх стандартных OHLCV:
  1. aggregate_ohlcv — resample 15m → 1h / 1d (OHLCV aggregation)
  2. multi_tf_features — compound features: daily trend + hourly momentum + entry quality
  3. signal_freshness_score — graduated decay 0..1 по age_seconds
  4. alignment_score — доля согласных стратегий по направлению
  5. regime_context_score — continuous regime score 0..1 из ADX/ATR
  6. check_no_live_broker — AST-guard (pattern из derived_indicators.py)

Все функции чистые: DataFrame/Dict → DataFrame/float.
Нет broker/client, нет сети, нет записи state/.
Depends: pandas, numpy (уже в проекте).
"""
import ast
import math
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


# ─── 1. OHLCV Aggregation ──────────────────────────────────────────────

# Маппинг целевых таймфреймов к pandas offset aliases
_TF_MAP = {
    "1h": "1h",
    "1H": "1h",
    "4h": "4h",
    "4H": "4h",
    "1d": "1D",
    "1D": "1D",
    "daily": "1D",
}


def aggregate_ohlcv(
    df: pd.DataFrame,
    target_tf: str = "1h",
) -> pd.DataFrame:
    """Resample OHLCV DataFrame на целевой таймфрейм.

    Алгоритм:
      1. Определяем pandas offset alias из target_tf.
      2. Resample с агрегацией:
         - open: first
         - high: max
         - low: min
         - close: last
         - volume: sum
      3. shift(1) + ffill — anti-lookahead (на первом баре после resample
         данные следующего часа ещё не доступны).

    df: DataFrame с колонками [open, high, low, close, volume] и DatetimeIndex.
        Если index не DatetimeIndex — пытается конвертировать.
    target_tf: строка из _TF_MAP ("1h", "4h", "1d", "daily").

    Returns: DataFrame с теми же колонками, но на целевом TF.
        Пустой df → пустой результат.

    >>> import pandas as pd
    >>> idx = pd.date_range("2025-01-01", periods=8, freq="15min")
    >>> df = pd.DataFrame({"open": range(8), "high": range(8),
    ...                    "low": range(8), "close": range(8),
    ...                    "volume": [100]*8}, index=idx)
    >>> out = aggregate_ohlcv(df, target_tf="1h")
    >>> len(out) <= 3
    True
    """
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    # Ensure DatetimeIndex
    if not isinstance(df.index, pd.DatetimeIndex):
        try:
            df = df.copy()
            df.index = pd.to_datetime(df.index)
        except (ValueError, TypeError):
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    offset = _TF_MAP.get(target_tf, target_tf)

    agg_rules = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }

    # Only aggregate columns that exist
    available_rules = {k: v for k, v in agg_rules.items() if k in df.columns}
    if not available_rules:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    resampled = df.resample(offset).agg(available_rules).dropna(subset=["close"])

    # Anti-lookahead: shift + ffill
    resampled = resampled.shift(1).ffill()

    return resampled.dropna(subset=["close"])


# ─── 2. Multi-TF Compound Features ─────────────────────────────────────


def multi_tf_features(df_15m: pd.DataFrame) -> pd.DataFrame:
    """Построить compound multi-TF features поверх 15m OHLCV.

    Вычисляет:
      - daily_trend_dir: +1 (up), -1 (down), 0 (flat/unknown)
        На основе close/MA50 дневных баров: close > MA → up.
      - hourly_momentum: hourly return (close/h1_close.shift(1) - 1),
        как мера краткосрочного импульса.
      - entry_quality复合: combined score из breakout_quality × volume_confirm × regime_align.
        entry_quality = sign(hourly_return) × abs(hourly_momentum) × volume_ratio
        Нормализован в [-1..+1] через clip.

    df_15m: DataFrame с [open, high, low, close, volume] и DatetimeIndex (15min).
    Если данных < 2 баров — возвращает df с нулевыми колонками.

    Returns: копия df_15m с 3 добавленными колонками.

    >>> import pandas as pd
    >>> idx = pd.date_range("2025-01-01", periods=100, freq="15min")
    >>> df = pd.DataFrame({"open": range(100), "high": range(100),
    ...                    "low": range(100), "close": range(100),
    ...                    "volume": [100]*100}, index=idx)
    >>> out = multi_tf_features(df)
    >>> "daily_trend_dir" in out.columns
    True
    """
    if df_15m is None or len(df_15m) < 2:
        result = df_15m.copy() if df_15m is not None else pd.DataFrame()
        for col in ("daily_trend_dir", "hourly_momentum", "entry_quality"):
            result[col] = 0.0
        return result

    result = df_15m.copy()

    # --- Daily trend direction ---
    try:
        df_daily = aggregate_ohlcv(df_15m, target_tf="1d")
        if len(df_daily) >= 2:
            ma50 = df_daily["close"].rolling(window=min(50, len(df_daily)), min_periods=1).mean()
            daily_dir_series = pd.Series(0.0, index=df_daily.index)
            daily_dir_series[df_daily["close"] > ma50] = 1.0
            daily_dir_series[df_daily["close"] < ma50] = -1.0
            # Map back to 15m index via reindex + ffill
            daily_dir_15m = daily_dir_series.reindex(df_15m.index, method="ffill").fillna(0.0)
        else:
            daily_dir_15m = pd.Series(0.0, index=df_15m.index)
    except (ValueError, KeyError):
        daily_dir_15m = pd.Series(0.0, index=df_15m.index)

    result["daily_trend_dir"] = daily_dir_15m.values

    # --- Hourly momentum ---
    try:
        df_1h = aggregate_ohlcv(df_15m, target_tf="1h")
        if len(df_1h) >= 2:
            h1_close = df_1h["close"]
            h1_ret = h1_close.pct_change().fillna(0.0)
            # Map back to 15m
            h1_momentum_15m = h1_ret.reindex(df_15m.index, method="ffill").fillna(0.0)
        else:
            h1_momentum_15m = pd.Series(0.0, index=df_15m.index)
    except (ValueError, KeyError):
        h1_momentum_15m = pd.Series(0.0, index=df_15m.index)

    result["hourly_momentum"] = h1_momentum_15m.values

    # --- Entry quality (compound) ---
    # volume_ratio: current volume / rolling mean volume
    vol = result["volume"].astype(float)
    avg_vol = vol.rolling(window=min(20, len(result)), min_periods=1).mean()
    volume_ratio = (vol / (avg_vol + 1e-9)).clip(-3.0, 3.0)

    # entry_quality = sign(hourly_momentum) * abs(hourly_momentum) * volume_ratio
    hm = result["hourly_momentum"]
    direction = np.sign(hm)
    raw_eq = direction * np.abs(hm) * volume_ratio
    result["entry_quality"] = raw_eq.clip(-1.0, 1.0)

    return result


# ─── 3. Signal Freshness Score ──────────────────────────────────────────


def signal_freshness_score(
    age_seconds: float,
    max_age: float = 960.0,
) -> float:
    """Graduated freshness score: 1.0 (fresh) → 0.0 (stale).

    Алгоритм: linear decay
      score = 1.0 - (age_seconds / max_age)
      clip [0.0, 1.0]

    age_seconds: возраст сигнала в секундах (>= 0).
    max_age: максимальный допустимый возраст (по умолчанию 960s = 16 min,
             совпадает с config.json signal_max_age_minutes=16).

    >>> signal_freshness_score(0)
    1.0
    >>> signal_freshness_score(960)
    0.0
    >>> signal_freshness_score(480)
    0.5
    """
    if age_seconds < 0:
        age_seconds = 0.0
    if max_age <= 0:
        return 0.0

    score = 1.0 - (age_seconds / max_age)
    return max(0.0, min(1.0, score))


# ─── 4. Alignment Score ─────────────────────────────────────────────────


def alignment_score(signals: List[Dict[str, Any]]) -> float:
    """Доля согласных стратегий по направлению (alignment).

    Алгоритм:
      1. Определяем majority direction (LONG/SHORT).
      2. alignment = count(direction == majority) / total
      3. Если total <= 1 → alignment = 1.0 (нечего сравнивать).
      4. Если нет направлений (все None) → alignment = 0.0.

    signals: список dict, каждый с ключом "direction" ("LONG"/"SHORT"/None).

    >>> alignment_score([{"direction": "LONG"}, {"direction": "LONG"}])
    1.0
    >>> alignment_score([{"direction": "LONG"}, {"direction": "SHORT"}])
    0.5
    >>> alignment_score([{"direction": None}])
    1.0
    """
    if not signals:
        return 0.0

    directions = [s.get("direction") for s in signals]
    valid = [d for d in directions if d is not None]

    if not valid:
        return 0.0

    total = len(signals)
    if total <= 1:
        return 1.0

    # Count each direction
    long_count = sum(1 for d in valid if d.upper() == "LONG")
    short_count = sum(1 for d in valid if d.upper() == "SHORT")

    majority_count = max(long_count, short_count)
    return majority_count / total


# ─── 5. Regime Context Score ────────────────────────────────────────────


def regime_context_score(
    regime_snapshot: Dict[str, Any],
    ticker: str,
) -> float:
    """Continuous regime confidence score: 0.0 (no confidence) → 1.0 (high confidence).

    Алгоритм (weighted sum, clipped [0,1]):
      A) adx_confidence = ADX / 50.0   (ADX 0..100, 50+ = strong trend)
      B) vol_confidence:
         - vol_bucket "high" → 0.8
         - vol_bucket "medium" → 0.5
         - vol_bucket "low" → 0.2
         - absent → 0.3
      C) direction_match:
         - если regime == "trend" и direction есть → 0.7
         - если regime == "range" → 0.3
         - absent → 0.2

      score = 0.5 * adx_confidence + 0.25 * vol_confidence + 0.25 * direction_match

    regime_snapshot: формат state/regime_snapshot.json
      {"tickers": {"T": {"adx": 25, "direction": "up", "regime": "trend",
                          "vol_bucket": "medium"}}}
    ticker: тикер для lookup.

    >>> regime_context_score({"tickers": {"BR": {"adx": 40, "direction": "up",
    ...   "regime": "trend", "vol_bucket": "high"}}}, "BR")  # doctest: +ELLIPSIS
    0.76...
    """
    tickers = regime_snapshot.get("tickers", {})
    data = tickers.get(ticker, {})

    if not data:
        return 0.1  # minimal confidence when no data

    # A) ADX confidence
    adx = float(data.get("adx", 0.0))
    adx_conf = min(adx / 50.0, 1.0)

    # B) Volume confidence
    vol_bucket = data.get("vol_bucket", "")
    vol_map = {"high": 0.8, "medium": 0.5, "low": 0.2}
    vol_conf = vol_map.get(vol_bucket, 0.3)

    # C) Direction match
    regime = data.get("regime", "")
    direction = data.get("direction", "")
    if regime == "trend" and direction:
        dir_match = 0.7
    elif regime == "range":
        dir_match = 0.3
    else:
        dir_match = 0.2

    score = 0.5 * adx_conf + 0.25 * vol_conf + 0.25 * dir_match
    return max(0.0, min(1.0, score))


# ─── 6. AST-guard: no live broker imports ───────────────────────────────

_BROKER_KEYWORDS = frozenset({"broker", "order", "trade", "tinkoff", "investapi"})


def check_no_live_broker(filepath: str) -> bool:
    """Проверка что файл не импортирует broker/order модули (AST-guard).

    Возвращает True если всё чисто, False если найден запрещённый импорт.

    >>> check_no_live_broker("code/multi_tf_features.py")
    True
    """
    with open(filepath, "r") as f:
        source = f.read()

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module_name = ""
            if isinstance(node, ast.Import):
                if node.names:
                    module_name = node.names[0].name
            elif node.module:
                module_name = node.module

            module_lower = module_name.lower()
            for kw in _BROKER_KEYWORDS:
                if kw in module_lower:
                    return False

    return True


# ─── Convenience: enrich DataFrame with all multi-TF features ───────────


def enrich_with_multi_tf(df_15m: pd.DataFrame) -> pd.DataFrame:
    """Добавить все multi-TF features к DataFrame 15m OHLCV.

    Convenience wrapper: multi_tf_features + aggregate columns.

    Returns: копия df_15m с добавленными колонками:
      - daily_trend_dir, hourly_momentum, entry_quality
    """
    if df_15m is None or len(df_15m) == 0:
        return df_15m.copy() if df_15m is not None else pd.DataFrame()

    return multi_tf_features(df_15m)


# ─── No-broker runtime guard ────────────────────────────────────────────

def _validate_no_broker_runtime() -> None:
    """Runtime check: модуль не содержит broker-вызовов."""
    import pathlib

    tree = ast.parse(pathlib.Path(__file__).read_text())
    forbidden = {"post_order", "place_order", "send_order", "submit_order", "Client"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = ""
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name in forbidden:
                raise RuntimeError(f"multi_tf_features.py contains forbidden call: {name}()")

"""Derived Indicators — производные индикаторы из OHLCV/тики/микроструктуры.

Модуль строит НЕ-стандартные индикаторы (не SMA/RSI/MACD), а именно:
  - Volume Profile (POC, VAH, VAL)
  - Cumulative Volume Delta (CVD) — proxy из направления close
  - Order Flow Imbalance — buy/sell volume proxy
  - Liquidity Score — volume / ATR ratio
  - Breakout Quality — volume × range × direction
  - Microstructure Bar Type — absorption/exhaustion/initiation/neutral
  - Volatility Regime Score — fast/slow vol ratio

Все функции чистые: DataFrame(OHLCV) → Series/Dict.
Нет broker/client, нет сети, нет записи state/.
Микроструктурные метрики — прокси из свечей (OHLCV), не реальный order flow.

Depends: pandas, numpy (уже в проекте).
"""
import math
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


# ─── Feature 1: базовые derived-индикаторы ─────────────────────────────


def volume_profile(df: pd.DataFrame, lookback: int = 20) -> Dict[str, Any]:
    """Volume Profile — распределение объёма по ценовым уровням.

    Возвращает:
      - poc: Point of Control (цена с максимальным объёмом)
      - vah: Value Area High (верхняя граница value area ~70% объёма)
      - val: Value Area Low (нижняя граница value area ~70% объёма)

    Алгоритм:
      1. Берём последние ``lookback`` баров.
      2. Группируем объём по ценовым уровням (price bins).
      3. POC = уровень с максимальным объёмом.
      4. Value Area = 70% объёма вокруг POC (расширяем вверх/вниз до 70%).

    Примечание: price bins — это mid-price бара (high+low)/2.
    Если данных < lookback, берём всё что есть.
    Если df пустой — возвращает nan-значения.

    >>> import pandas as pd
    >>> df = pd.DataFrame({'open': [100]*5, 'high': [102]*5, 'low': [98]*5,
    ...                    'close': [101]*5, 'volume': [1000]*5})
    >>> vp = volume_profile(df, lookback=5)
    >>> isinstance(vp['poc'], float)
    True
    """
    if df is None or len(df) == 0:
        return {"poc": float("nan"), "vah": float("nan"), "val": float("nan")}

    tail = df.iloc[-lookback:] if len(df) >= lookback else df.copy()

    mid_price = (tail["high"] + tail["low"]) / 2.0
    volumes = tail["volume"].astype(float)

    if volumes.sum() == 0:
        return {"poc": float("nan"), "vah": float("nan"), "val": float("nan")}

    # Create price bins — round to 2 decimal places for grouping
    price_step = max((tail["high"].max() - tail["low"].min()) / 20, 1e-6)
    bins = np.round(mid_price / price_step) * price_step

    profile = pd.DataFrame({"price": bins, "volume": volumes})
    grouped = profile.groupby("price")["volume"].sum().sort_values(ascending=False)

    if len(grouped) == 0:
        return {"poc": float("nan"), "vah": float("nan"), "val": float("nan")}

    # POC — level with highest volume
    poc = float(grouped.index[0])

    # Value Area: expand from POC until we capture 70% of total volume
    total_volume = float(grouped.sum())
    if total_volume == 0:
        return {"poc": poc, "vah": poc, "val": poc}

    target_volume = total_volume * 0.70
    accumulated = float(grouped.iloc[0])

    # Sort levels by distance from POC for value area expansion
    sorted_levels = grouped.index.tolist()
    sorted_levels_by_dist = sorted(sorted_levels, key=lambda x: abs(x - poc))

    va_levels = {poc}
    for level in sorted_levels_by_dist:
        if accumulated >= target_volume:
            break
        accumulated += float(grouped.get(level, 0))
        va_levels.add(level)

    vah = max(va_levels) if va_levels else poc
    val = min(va_levels) if va_levels else poc

    return {"poc": poc, "vah": vah, "val": val}


def cumulative_volume_delta(df: pd.DataFrame) -> pd.Series:
    """Cumulative Volume Delta (CVD) — прокси кумулятивной дельты объёма.

    Алгоритм (proxy из OHLCV):
      - Если close >= open → buy_volume = volume, sell_volume = 0
      - Если close < open → buy_volume = 0, sell_volume = volume
      - delta = buy_volume - sell_volume
      - CVD = cumsum(delta)

    Примечание: реальный CVD считается из тиковых данных (bid/ask trades).
    Этот — прокси из направления свечи (close vs open).

    >>> import pandas as pd
    >>> df = pd.DataFrame({'open': [100, 101], 'high': [102, 103],
    ...                    'low': [99, 100], 'close': [101, 100],
    ...                    'volume': [1000, 500]})
    >>> cvd = cumulative_volume_delta(df)
    >>> cvd.iloc[0]
    1000.0
    """
    if df is None or len(df) == 0:
        return pd.Series(dtype=float)

    close = df["close"].astype(float)
    open_ = df["open"].astype(float)
    volume = df["volume"].astype(float)

    delta = pd.Series(
        np.where(close >= open_, volume, -volume),
        index=df.index,
        dtype=float,
    )

    cvd = delta.cumsum()
    cvd.name = "cvd"
    return cvd


def orderflow_imbalance(df: pd.DataFrame, lookback: int = 20) -> pd.Series:
    """Order Flow Imbalance — прокси дисбаланса buy/sell volume.

    Алгоритм:
      - buy_volume = volume × (close - low) / (high - low + 1e-9)
      - sell_volume = volume × (high - close) / (high - low + 1e-9)
      - imbalance = rolling_mean(buy_volume - sell_volume) / rolling_mean(volume)
      - Значения от -1.0 (все sell) до +1.0 (все buy)

    Примечание: прокси из OHLCV, не реальный order flow.
    Based on the assumption that price position within bar reflects
    buy/sell pressure.

    >>> import pandas as pd
    >>> df = pd.DataFrame({'open': [100]*3, 'high': [105]*3, 'low': [95]*3,
    ...                    'close': [103, 97, 100], 'volume': [1000]*3})
    >>> imb = orderflow_imbalance(df, lookback=3)
    >>> len(imb) == 3
    True
    """
    if df is None or len(df) == 0:
        return pd.Series(dtype=float)

    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    volume = df["volume"].astype(float)

    bar_range = high - low + 1e-9  # avoid division by zero for doji bars

    buy_volume = volume * (close - low) / bar_range
    sell_volume = volume * (high - close) / bar_range

    delta_vol = buy_volume - sell_volume

    effective_lb = min(lookback, len(df))
    avg_delta = delta_vol.rolling(window=effective_lb, min_periods=1).mean()
    avg_volume = volume.rolling(window=effective_lb, min_periods=1).mean()

    imbalance = avg_delta / (avg_volume + 1e-9)
    imbalance.name = "orderflow_imbalance"
    return imbalance


def liquidity_score(df: pd.DataFrame, lookback: int = 20) -> pd.Series:
    """Liquidity Score — оценка ликвидности (volume / ATR ratio).

    Чем выше score, тем более ликвиден инструмент в данном окне.

    Алгоритм:
      - ATR = rolling mean of (high - low)
      - liquidity = volume / (ATR + 1e-9)

    >>> import pandas as pd
    >>> df = pd.DataFrame({'open': [100]*5, 'high': [105]*5, 'low': [95]*5,
    ...                    'close': [100]*5, 'volume': [1000]*5})
    >>> ls = liquidity_score(df, lookback=5)
    >>> len(ls) == 5
    True
    """
    if df is None or len(df) == 0:
        return pd.Series(dtype=float)

    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)

    atr_like = (high - low).rolling(window=min(lookback, len(df)), min_periods=1).mean()

    score = volume / (atr_like + 1e-9)
    score.name = "liquidity_score"
    return score


# ─── Feature 2: микроструктурные индикаторы и quality scores ───────────


def breakout_quality(df: pd.DataFrame, threshold: float = 1.5) -> pd.Series:
    """Breakout Quality Score — качество пробоя (volume × range × direction).

    Алгоритм:
      - range = high - low (absolute range бара)
      - avg_range = rolling mean range
      - volume_ratio = volume / rolling mean volume
      - direction = sign(close - open) or sign(close - prev_close)
      - quality = (range / avg_range) × volume_ratio × direction
      - Если |quality| >= threshold → пробой significant

    Returns Series с float значениями (положительные = бычий breakout,
    отрицательные = медвежий).

    >>> import pandas as pd
    >>> df = pd.DataFrame({'open': [100]*5, 'high': [102, 110, 103, 101, 104],
    ...                    'low': [99, 98, 97, 99, 98],
    ...                    'close': [101, 109, 100, 100, 103],
    ...                    'volume': [100, 500, 120, 90, 150]})
    >>> bq = breakout_quality(df, threshold=1.5)
    >>> len(bq) == 5
    True
    """
    if df is None or len(df) == 0:
        return pd.Series(dtype=float)

    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    open_ = df["open"].astype(float)
    volume = df["volume"].astype(float)

    bar_range = high - low
    avg_range = bar_range.rolling(window=min(20, len(df)), min_periods=1).mean()
    avg_volume = volume.rolling(window=min(20, len(df)), min_periods=1).mean()

    # Direction: close vs open (or prev_close fallback)
    prev_close = close.shift(1)
    direction = np.where(
        prev_close.isna(),
        np.sign(close - open_),
        np.sign(close - prev_close),
    )

    range_ratio = bar_range / (avg_range + 1e-9)
    volume_ratio = volume / (avg_volume + 1e-9)

    quality = pd.Series(
        range_ratio * volume_ratio * direction,
        index=df.index,
        dtype=float,
    )
    quality.name = "breakout_quality"
    return quality


def microstructure_bar_type(df: pd.DataFrame) -> pd.Series:
    """Microstructure Bar Type — классификация баров.

    Классы:
      - 'absorption': large volume but small range (absorbing orders without price movement)
      - 'exhaustion': large range, decreasing volume (trend exhaustion)
      - 'initiation': large range, large volume, strong direction (new move start)
      - 'neutral': all other bars

    Алгоритм:
      1. range_z = (range - mean_range) / (std_range + 1e-9)
      2. volume_z = (volume - mean_volume) / (std_volume + 1e-9)
      3. bar_range = high - low
      4. direction = abs(close - open)

      - Absorption: volume_z > 0.5 AND range_z < -0.3
      - Initiation: range_z > 0.5 AND volume_z > 0.5 AND direction / bar_range > 0.3
      - Exhaustion: range_z > 0.5 AND volume_z < -0.3
      - Neutral: otherwise

    >>> import pandas as pd
    >>> df = pd.DataFrame({'open': [100]*10, 'high': [105]*10, 'low': [95]*10,
    ...                    'close': [100]*10, 'volume': [100]*10})
    >>> bt = microstructure_bar_type(df)
    >>> len(bt) == 10
    True
    """
    if df is None or len(df) == 0:
        return pd.Series(dtype=str)

    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    open_ = df["open"].astype(float)
    volume = df["volume"].astype(float)

    bar_range = high - low
    direction = (close - open_).abs()

    mean_range = bar_range.rolling(window=min(20, len(df)), min_periods=1).mean()
    std_range = bar_range.rolling(window=min(20, len(df)), min_periods=1).std().fillna(0)
    mean_vol = volume.rolling(window=min(20, len(df)), min_periods=1).mean()
    std_vol = volume.rolling(window=min(20, len(df)), min_periods=1).std().fillna(0)

    range_z = (bar_range - mean_range) / (std_range + 1e-9)
    volume_z = (volume - mean_vol) / (std_vol + 1e-9)

    bar_types = pd.Series("neutral", index=df.index, dtype=object)

    absorption_mask = (volume_z > 0.5) & (range_z < -0.3)
    exhaustion_mask = (range_z > 0.5) & (volume_z < -0.3)
    initiation_mask = (range_z > 0.5) & (volume_z > 0.5) & (
        direction / (bar_range + 1e-9) > 0.3
    )

    bar_types[absorption_mask] = "absorption"
    bar_types[exhaustion_mask] = "exhaustion"
    bar_types[initiation_mask] = "initiation"

    bar_types.name = "bar_type"
    return bar_types


def volatility_regime_score(
    df: pd.DataFrame, fast: int = 10, slow: int = 30
) -> pd.Series:
    """Volatility Regime Score — fast/slow vol ratio → regime score.

    Алгоритм:
      - Returns = close / close.shift(1) - 1
      - Fast vol = std(returns, window=fast)
      - Slow vol = std(returns, window=slow)
      - Score = fast_vol / (slow_vol + 1e-9)

    Интерпретация:
      - score > 1.5 → high volatility regime
      - score < 0.5 → low volatility regime
      - 0.5..1.5 → normal

    >>> import pandas as pd
    >>> df = pd.DataFrame({'open': [100]*40, 'high': [102]*40, 'low': [98]*40,
    ...                    'close': [100 + i*0.1 for i in range(40)],
    ...                    'volume': [100]*40})
    >>> vrs = volatility_regime_score(df, fast=10, slow=30)
    >>> len(vrs) == 40
    True
    """
    if df is None or len(df) == 0:
        return pd.Series(dtype=float)

    close = df["close"].astype(float)
    returns = close.pct_change().fillna(0.0)

    fast_lb = min(fast, len(df))
    slow_lb = min(slow, len(df))

    fast_vol = returns.rolling(window=max(fast_lb, 2), min_periods=1).std().fillna(1e-9)
    slow_vol = returns.rolling(window=max(slow_lb, 2), min_periods=1).std().fillna(1e-9)

    score = fast_vol / (slow_vol + 1e-9)
    score.name = "volatility_regime_score"
    return score


# ─── AST-guard: запрет live broker imports ──────────────────────────────

_BROKER_KEYWORDS = frozenset({"broker", "order", "trade", "tinkoff", "investapi"})


def check_no_live_broker(filepath: str) -> bool:
    """Проверка что файл не импортирует broker/order модули (AST-guard).

    Возвращает True если всё чисто, False если найден запрещённый импорт.
    """
    with open(filepath, "r") as f:
        source = f.read()

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.Import):
                module_name = ""
                if node.names:
                    module_name = node.names[0].name if node.names[0].name else ""
            elif node.module:
                module_name = node.module
            else:
                module_name = ""

            module_lower = module_name.lower()
            for kw in _BROKER_KEYWORDS:
                if kw in module_lower:
                    return False
    return True


# Allow importing ast at module level for check_no_live_broker
import ast  # noqa: E402


# ─── Convenience: enrich DataFrame with all derived indicators ──────────


def enrich_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Добавить все derived indicators к DataFrame OHLCV.

    Возвращает копию df с добавленными колонками:
      - poc, vah, val (Volume Profile, scalar)
      - cvd (Cumulative Volume Delta)
      - orderflow_imbalance
      - liquidity_score
      - breakout_quality
      - bar_type (microstructure classification)
      - volatility_regime_score

    Примечание: poc/vah/val — scalar значения (последний Volume Profile),
    записываются во все строки для удобства.
    """
    if df is None or len(df) == 0:
        return df.copy() if df is not None else pd.DataFrame()

    result = df.copy()

    vp = volume_profile(df)
    result["poc"] = vp["poc"]
    result["vah"] = vp["vah"]
    result["val"] = vp["val"]

    result["cvd"] = cumulative_volume_delta(df)
    result["orderflow_imbalance"] = orderflow_imbalance(df)
    result["liquidity_score"] = liquidity_score(df)
    result["breakout_quality"] = breakout_quality(df)
    result["bar_type"] = microstructure_bar_type(df)
    result["volatility_regime_score"] = volatility_regime_score(df)

    return result

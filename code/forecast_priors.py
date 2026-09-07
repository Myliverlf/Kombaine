"""Forecast Priors — 4 чистые функции для интеграции TimesFM в allocator scoring.

Функции (все Pure, Zero Dependencies, fail-open при None/missing):
  1. forecast_expectancy_prior(candidate, forecast_result) → float [-1..+1]
  2. forecast_regime_prior(forecast_result, regime_snapshot, ticker) → float [-1..+1]
  3. signal_freshness(forecast_ts, now_ts, horizon, bar_seconds) → float [0..1]
  4. forecast_decay(elapsed_bars, half_life) → float [0..1]

Философия:
  - Fail-open: None/missing → 0.0 или 1.0 (neuter, не блокировать scoring)
  - RI excluded → 0.0 (как в forecast_scorer.py EXCLUDED_TICKERS)
  - Все float-in → float-out, без side effects
  - Clamp результатов в указанные диапазоны

Связь с плацдармом:
  - allocator_score_with_forecast() (forecast_scorer.py) вызывает эти функции
  - regime_bonus() (allocator_metrics.py) — не модифицируется, priors дополняют
  - ForecastResult (timesfm_adapter.py) — основной вход для direction/confidence
"""
import math
from typing import Any, Dict, Optional


# ─── Исключённые тикеры (дублирует EXCLUDED_TICKERS из forecast_scorer) ──

EXCLUDED_TICKERS = {"RI"}

# Default half-life для forecast decay (в барах)
DEFAULT_DECAY_HALF_LIFE = 20

# Default bar duration в секундах (5 минут для intraday)
DEFAULT_BAR_SECONDS = 300


# ─── 1. Forecast Expectancy Prior ──────────────────────────────────────

def forecast_expectancy_prior(
    candidate: Dict[str, Any],
    forecast_result: Any,
) -> float:
    """Кросс-валидация direction: forecast.direction vs candidate.direction.

    Результат float [-1..+1]:
      +confidence*0.5  — forecast.direction совпадает с направлением кандидата
      -confidence*0.5  — forecast.direction противоречит кандидату
       0.0             — flat/None/missing/dummy/RI

    Логика:
      - candidate.direction = "LONG" → cand_dir = "up"
      - candidate.direction = "SHORT" → cand_dir = "down"
      - forecast.direction = "up"/"down"/"flat"
      - Совпадение: forecast.up + cand.up → позитивный prior
      - Противоречие: forecast.down + cand.up → негативный prior
      - flat → 0.0 (модель не видит direction)

    Для новых стратегий с малым числом трейдов expectancy_prior
    является основным prior, заменяя expectancy_r, который ещё не накоплен.

    Пример:
        >>> forecast_expectancy_prior(
        ...     {"ticker": "SBER", "direction": "LONG"},
        ...     ForecastResult("up", 0.03, 0.8, 10, "timesfm"),
        ... )
        0.4
    """
    # Защиты
    if forecast_result is None:
        return 0.0

    ticker = candidate.get("ticker", "")
    if ticker in EXCLUDED_TICKERS:
        return 0.0

    # Извлечение direction кандидата
    cand_direction = candidate.get("direction")
    if cand_direction is None:
        return 0.0

    cand_dir = "up" if cand_direction.upper() == "LONG" else "down"

    # Извлечение полей forecast (работает с ForecastResult dataclass и dict)
    source = getattr(forecast_result, "source", None) or (
        forecast_result.get("source") if isinstance(forecast_result, dict) else None
    )
    if source == "dummy":
        return 0.0

    direction = getattr(forecast_result, "direction", None) or (
        forecast_result.get("direction", "flat") if isinstance(forecast_result, dict) else "flat"
    )
    confidence = getattr(forecast_result, "confidence", None) or (
        forecast_result.get("confidence", 0.0) if isinstance(forecast_result, dict) else 0.0
    )
    confidence = float(confidence)

    # Flat direction → нет signal
    if direction == "flat":
        return 0.0

    # Совпадение / противоречие
    if direction == cand_dir:
        prior = confidence * 0.5
    else:
        prior = -(confidence * 0.5)

    return round(max(-1.0, min(1.0, prior)), 4)


# ─── 2. Forecast Regime Prior ──────────────────────────────────────────

def forecast_regime_prior(
    forecast_result: Any,
    regime_snapshot: Dict[str, Any],
    ticker: str,
) -> float:
    """Кросс-валидация forecast vs regime: forecast подтверждает/противоречит regime.

    Результат float [-1..+1]:
      +confidence*0.3  — forecast подтверждает regime direction
      -confidence*0.3  — forecast противоречит regime direction
       0.0             — flat regime, flat forecast, dummy, RI, missing

    Логика:
      - regime_snapshot = {"tickers": {"T": {"adx": ..., "direction": "up"/"down", "regime": "trend"/"range"}}}
      - Если regime = "range" → 0.0 (нет directional regime для кросс-валидации)
      - Если regime = "trend" и forecast.direction == regime.direction → подтверждение
      - Если regime = "trend" и forecast.direction != regime.direction → противоречие

    Важно: regime_prior — это ДОПОЛНЕНИЕ к regime_bonus (allocator_metrics),
    НЕ замена. regime_bonus опирается на ADX/direction, regime_prior кросс-валидирует
    с forecast.

    Пример:
        >>> forecast_regime_prior(
        ...     ForecastResult("up", 0.03, 0.8, 10, "timesfm"),
        ...     {"tickers": {"SBER": {"adx": 30.0, "direction": "up", "regime": "trend"}}},
        ...     "SBER",
        ... )
        0.24
    """
    # Защиты
    if forecast_result is None:
        return 0.0

    if ticker in EXCLUDED_TICKERS:
        return 0.0

    # Forecast source check
    source = getattr(forecast_result, "source", None) or (
        forecast_result.get("source") if isinstance(forecast_result, dict) else None
    )
    if source == "dummy":
        return 0.0

    forecast_direction = getattr(forecast_result, "direction", None) or (
        forecast_result.get("direction", "flat") if isinstance(forecast_result, dict) else "flat"
    )
    confidence = getattr(forecast_result, "confidence", None) or (
        forecast_result.get("confidence", 0.0) if isinstance(forecast_result, dict) else 0.0
    )
    confidence = float(confidence)

    if forecast_direction == "flat":
        return 0.0

    # Regime data
    tickers = regime_snapshot.get("tickers", {})
    ticker_data = tickers.get(ticker, {})
    regime = ticker_data.get("regime", "range")
    regime_direction = ticker_data.get("direction", "neutral")

    if regime != "trend":
        return 0.0

    if regime_direction == "neutral":
        return 0.0

    # Кросс-валидация
    if forecast_direction == regime_direction:
        prior = confidence * 0.3
    else:
        prior = -(confidence * 0.3)

    return round(max(-1.0, min(1.0, prior)), 4)


# ─── 3. Signal Freshness ───────────────────────────────────────────────

def signal_freshness(
    forecast_ts: Optional[float],
    now_ts: Optional[float] = None,
    horizon: int = 20,
    bar_seconds: int = DEFAULT_BAR_SECONDS,
) -> float:
    """Нормированная свежесть forecast-сигнала: экспоненциальное затухание по возрасту.

    Результат float [0..1]:
      1.0  — сигнал только что получен (age=0)
      0.5  — сигнал достиг half-life (age = horizon * bar_seconds)
      0.0  — сигнал полностью устарел

    Формула: freshness = e^(-age / half_life), где:
      - age = now_ts - forecast_ts (в секундах)
      - half_life = horizon * bar_seconds

    Fail-open: forecast_ts=None → 1.0 (считаем сигнал свежим, не блокируем scoring)

    Использование:
      - Множитель к forecast_bonus: final_bonus = bonus * freshness
      - Свежий прогноз (10 мин) → full bonus
      - Устаревший прогноз (4 ч) → reduced bonus

    Пример:
        >>> signal_freshness(1000.0, 1000.0, horizon=20, bar_seconds=300)
        1.0
        >>> signal_freshness(1000.0, 1000.0 + 6000.0, horizon=20, bar_seconds=300)  # age=half_life
        0.5
    """
    # Fail-open
    if forecast_ts is None or now_ts is None:
        return 1.0

    age = now_ts - forecast_ts

    # Age <= 0 → свежий (now <= forecast_ts — возможно clock skew)
    if age <= 0:
        return 1.0

    # Half-life: horizon * bar_seconds
    half_life = horizon * bar_seconds
    if half_life <= 0:
        return 1.0

    # Экспоненциальное затухание
    freshness = math.exp(-age / half_life)

    # Clamp [0..1] (数学上 exp всегда [0..1] для age >= 0, но на всякий случай)
    return round(max(0.0, min(1.0, freshness)), 4)


# ─── 4. Forecast Decay ─────────────────────────────────────────────────

def forecast_decay(
    elapsed_bars: int = 0,
    half_life: int = DEFAULT_DECAY_HALF_LIFE,
) -> float:
    """Экспоненциальное затухание forecast-impact по числу прошедших баров.

    Результат float [0..1]:
      1.0  — 0 прошедших баров (прогноз актуален)
      0.5  — elapsed_bars = half_life (половина жизни)
      ~0.0 — elapsed_bars >> half_life (прогноз устарел)

    Формула: decay = 2^(-elapsed_bars / half_life)

    clamp [0..1] — forecast_decay НЕ может усилить signal (>1).

    Использование:
      - Множитель к forecast_bonus: final_bonus = bonus * freshness * decay
      - Чем старше forecast в барах, тем меньше его вклад

    Пример:
        >>> forecast_decay(0, 20)
        1.0
        >>> forecast_decay(20, 20)
        0.5
        >>> forecast_decay(100, 20)  # 5 half-lives → ~0.031
        0.0313
    """
    if elapsed_bars <= 0:
        return 1.0

    if half_life <= 0:
        return 0.0

    decay = math.pow(2.0, -elapsed_bars / half_life)

    return round(max(0.0, min(1.0, decay)), 4)

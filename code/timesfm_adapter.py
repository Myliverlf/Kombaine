"""TimesFM Adapter — обёртка над Google TimesFM для time-series forecasting.

Если TimesFM не установлен — автоматический fallback на DummyTimesFMAdapter
с pre-computed fixture-based прогнозами. Не требует GPU (CPU inference).
Не ходит в сеть, не импортирует broker/client.

ForecastResult:
  - direction: "up" / "down" / "flat" — прогнозируемое направление
  - ci_width: float — ширина confidence interval (q90 - q10), нормировано
    на среднее; узкая = модель уверена, широкая = неуверенна
  - confidence: float 0..1 — общий confidence модели
  - horizon: int — горизонт прогноза (кол-во баров)
  - source: "timesfm" / "dummy" — источник прогноза

Использование:
    adapter = get_adapter({})
    result = adapter.forecast("BR", [1.0]*30, horizon=20)
"""
import json
import math
import os
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

# ─── Data class ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ForecastResult:
    """Результат прогноза TimesFM для одного тикера."""
    direction: str          # "up" / "down" / "flat"
    ci_width: float         # ширина confidence interval (q90 - q10), нормировано
    confidence: float       # общий confidence 0..1
    horizon: int            # горизонт в барах
    source: str             # "timesfm" / "dummy"


# ─── Cache helpers ─────────────────────────────────────────────────────

DEFAULT_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "state"
)
CACHE_FILENAME = "forecast_cache.json"
CACHE_TTL_SECONDS = 4 * 3600  # 4 hours


def _cache_path(cache_dir: Optional[str] = None) -> str:
    """Путь к файлу кэша прогнозов."""
    d = cache_dir or DEFAULT_CACHE_DIR
    return os.path.join(d, CACHE_FILENAME)


def load_cache(cache_dir: Optional[str] = None) -> Dict[str, Any]:
    """Загрузить кэш прогнозов с диска. Возвращает dict {ticker: {result, ts}}."""
    path = _cache_path(cache_dir)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def save_cache(cache: Dict[str, Any], cache_dir: Optional[str] = None) -> None:
    """Сохранить кэш прогнозов на диск."""
    path = _cache_path(cache_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _is_cache_fresh(entry: Dict[str, Any], now: Optional[float] = None) -> bool:
    """Проверить актуальность записи кэша (TTL)."""
    if now is None:
        now = time.time()
    ts = entry.get("ts", 0)
    return (now - ts) < CACHE_TTL_SECONDS


# ─── Real TimesFM adapter ──────────────────────────────────────────────


class TimesFMAdapter:
    """Адаптер реального TimesFM для CPU inference.

    Требует установленный пакет ``timesfm`` (pip install timesfm).
    При отсутствии пакета — вызывающая сторона должна использовать
    get_adapter(), который вернёт DummyTimesFMAdapter.
    """

    def __init__(self, model: Any = None):
        """
        model: предзагруженная TimesFM модель. Если None — загружается
        автоматически при первом forecast().
        """
        self._model = model
        self._cache_dir: Optional[str] = None
        self.source_key = "timesfm"
        self.source_display = "real TimesFM"
        self.fallback_reason: Optional[str] = None

    def set_cache_dir(self, cache_dir: str) -> None:
        """Установить директорию кэша."""
        self._cache_dir = cache_dir

    def forecast(
        self,
        ticker: str,
        bars: List[float],
        horizon: int = 20,
    ) -> ForecastResult:
        """Прогноз для одного тикера.

        ticker: имя тикера (напр. "BR")
        bars: список цен закрытия (последние N баров)
        horizon: горизонт прогноза (кол-во баров, default 20)

        Возвращает ForecastResult с direction, ci_width, confidence.
        """
        # Попытка загрузить модель, если ещё не загружена
        if self._model is None:
            self._model = self._load_model()

        # Проверка кэша
        cache = load_cache(self._cache_dir)
        cache_key = "%s_%d_%d" % (ticker, len(bars), horizon)
        if cache_key in cache and _is_cache_fresh(cache[cache_key]):
            cached = cache[cache_key]["result"]
            return ForecastResult(**cached)

        # Inference через TimesFM
        result = self._infer(ticker, bars, horizon)

        # Сохранение в кэш
        cache[cache_key] = {
            "result": asdict(result),
            "ts": time.time(),
        }
        save_cache(cache, self._cache_dir)

        return result

    def _load_model(self) -> Any:
        """Load real TimesFM 2.5 torch model once (CPU-safe, no torch.compile)."""
        try:
            import timesfm
        except ImportError:
            raise ImportError(
                "TimesFM package not installed. Use .venv-timesfm or install timesfm."
            )

        cache_dir = os.environ.get("TIMESFM_CACHE_DIR", "/root/.cache/timesfm")
        model_id = os.environ.get("TIMESFM_MODEL_ID", "google/timesfm-2.5-200m-pytorch")
        model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(
            model_id,
            cache_dir=cache_dir,
            torch_compile=False,
        )
        model.compile(
            timesfm.ForecastConfig(
                max_context=int(os.environ.get("TIMESFM_MAX_CONTEXT", "128")),
                max_horizon=int(os.environ.get("TIMESFM_MAX_HORIZON", "20")),
                normalize_inputs=True,
                per_core_batch_size=1,
            )
        )
        return model

    def _infer(
        self, ticker: str, bars: List[float], horizon: int
    ) -> ForecastResult:
        """Запуск inference через TimesFM."""
        try:
            import numpy as np

            input_array = np.array(bars, dtype=np.float32)
            point, quantiles = self._model.forecast(horizon=horizon, inputs=[input_array])
            point_arr = np.asarray(point)[0]
            quant_arr = np.asarray(quantiles)[0] if quantiles is not None else None
            if quant_arr is not None and quant_arr.ndim == 2 and quant_arr.shape[-1] >= 2:
                # TimesFM 2.5 returns quantiles as (horizon, n_quantiles).
                q10 = float(np.mean(quant_arr[:, 1] if quant_arr.shape[-1] > 1 else quant_arr[:, 0]))
                q90 = float(np.mean(quant_arr[:, -1]))
            else:
                vol = float(np.std(input_array[-20:])) if len(input_array) >= 20 else 1.0
                q10 = float(np.mean(point_arr)) - 1.28 * vol
                q90 = float(np.mean(point_arr)) + 1.28 * vol

            last_price = float(bars[-1]) if bars else 1.0
            mean_point = float(np.mean(point_arr))

            # Direction
            if mean_point > last_price * 1.001:
                direction = "up"
            elif mean_point < last_price * 0.999:
                direction = "down"
            else:
                direction = "flat"

            # CI width (нормировано на среднее)
            ci_width = abs(q90 - q10) / abs(last_price) if last_price != 0 else 0.0

            # Confidence: обратно пропорционально ширине CI
            # узкая CI (0.01) → confidence ~0.9, широкая (0.1+) → ~0.3
            confidence = max(0.1, min(0.95, 1.0 - ci_width * 8.0))

            return ForecastResult(
                direction=direction,
                ci_width=round(ci_width, 6),
                confidence=round(confidence, 4),
                horizon=horizon,
                source="timesfm",
            )

        except (AttributeError, RuntimeError, ValueError) as exc:
            # Если inference падает — возвращаем dummy
            return DummyTimesFMAdapter().forecast(ticker, bars, horizon)


# ─── Dummy adapter (fallback) ──────────────────────────────────────────


class DummyTimesFMAdapter:
    """Dummy-адаптер: возвращает нейтральные прогнозы без реальной модели.

    Используется когда TimesFM не установлен или inference падает.
    Все ForecastResult.source = "dummy", что даёт forecast_bonus = 0.0
    (fail-open) в forecast_scorer.py.
    """

    def __init__(self, reason: Optional[str] = None):
        self.source_key = "dummy"
        self.source_display = "dummy"
        self.fallback_reason = reason or "TimesFM unavailable or explicitly disabled"

    def forecast(
        self,
        ticker: str,
        bars: List[float],
        horizon: int = 20,
    ) -> ForecastResult:
        """Нейтральный прогноз на основе простых статистик баров.

        Не использует модель — только pca по последним барам.
        """
        if not bars or len(bars) < 2:
            return ForecastResult(
                direction="flat",
                ci_width=0.0,
                confidence=0.0,
                horizon=horizon,
                source="dummy",
            )

        # Простой тренд по последним барам
        recent = bars[-min(len(bars), 10):]
        if len(recent) < 2:
            return ForecastResult(
                direction="flat",
                ci_width=0.0,
                confidence=0.0,
                horizon=horizon,
                source="dummy",
            )

        # Линейный тренд
        n = len(recent)
        x_mean = (n - 1) / 2.0
        y_mean = sum(recent) / n
        num = sum((i - x_mean) * (recent[i] - y_mean) for i in range(n))
        den = sum((i - x_mean) ** 2 for i in range(n))
        slope = num / den if den > 0 else 0.0

        # Direction
        last_price = recent[-1]
        if last_price != 0:
            relative_slope = slope / last_price
        else:
            relative_slope = 0.0

        if relative_slope > 0.001:
            direction = "up"
        elif relative_slope < -0.001:
            direction = "down"
        else:
            direction = "flat"

        # CI width (эвристика: std / mean)
        mean_price = y_mean if y_mean != 0 else 1.0
        variance = sum((r - y_mean) ** 2 for r in recent) / n
        std = math.sqrt(variance)
        ci_width = (std / mean_price) * 2.56  # ~90% CI

        # Confidence: низкий для dummy (0.1..0.3)
        confidence = min(0.3, ci_width * 5.0 + 0.1)

        return ForecastResult(
            direction=direction,
            ci_width=round(ci_width, 6),
            confidence=round(confidence, 4),
            horizon=horizon,
            source="dummy",
        )


# ─── Factory ───────────────────────────────────────────────────────────


def get_adapter(config: Optional[Dict[str, Any]] = None) -> Any:
    """Factory: вернуть TimesFMAdapter если пакет установлен, иначе Dummy.

    config может содержать:
      - "cache_dir": str — путь к кэшу forecast
      - "timesfm_enabled": bool — принудительный вкл/выкл (default: auto-detect)
    """
    if config is None:
        config = {}

    # Принудительный dummy
    if config.get("timesfm_enabled") is False:
        return DummyTimesFMAdapter(reason="timesfm_enabled=false in config")

    # Принудительный real (без проверки)
    if config.get("timesfm_enabled") is True:
        adapter = TimesFMAdapter()
        cache_dir = config.get("cache_dir")
        if cache_dir:
            adapter.set_cache_dir(cache_dir)
        return adapter

    # Auto-detect: попробовать импортировать timesfm
    try:
        import timesfm  # noqa: F401
        adapter = TimesFMAdapter()
        cache_dir = config.get("cache_dir")
        if cache_dir:
            adapter.set_cache_dir(cache_dir)
        return adapter
    except ImportError:
        return DummyTimesFMAdapter(reason="timesfm import failed in auto-detect")


def describe_source(adapter: Any) -> Dict[str, Any]:
    """Return a normalized source description for reports and smoke checks."""
    adapter_class = type(adapter).__name__
    source_key = str(getattr(adapter, "source_key", "dummy" if adapter_class == "DummyTimesFMAdapter" else "timesfm"))
    source_display = str(getattr(adapter, "source_display", "real TimesFM" if source_key == "timesfm" and adapter_class == "TimesFMAdapter" else "dummy"))
    fallback_reason = getattr(adapter, "fallback_reason", None)
    real_timesfm = source_display == "real TimesFM" and source_key == "timesfm" and adapter_class == "TimesFMAdapter"
    return {
        "adapter_class": adapter_class,
        "source_key": source_key,
        "source_display": source_display,
        "real_timesfm": real_timesfm,
        "fallback_reason": fallback_reason,
    }


# ─── CLI demo ──────────────────────────────────────────────────────────


def _demo() -> None:
    """Демонстрация адаптера."""
    adapter = get_adapter({})
    print("Adapter type:", type(adapter).__name__)

    # Синтетические бары для BR
    bars = [100.0 + i * 0.5 + (-1) ** i * 0.2 for i in range(30)]
    result = adapter.forecast("BR", bars, horizon=20)
    print("Forecast:", result)

    # Кэш
    cache = load_cache()
    print("Cache entries:", len(cache))


if __name__ == "__main__":
    _demo()

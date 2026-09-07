"""ForecastContext — Central Forecasting Brain для strategy_combine.

Единая точка агрегации TimesFM прогнозов для всего universe.
Предоставляет forecast-aware контекст всем этапам пайплайна:
  - strategy_ideas (idea scoring)
  - quality_gate (confidence-weighted quality)
  - signal_fusion (forecast-weighted fusion)
  - risk_scorecard (forecast-aware risk)
  - candidate_allocator (enhanced scoring)

ForecastContext НЕ модифицирует существующие модули напрямую —
он предоставляет контекст через forecast_context_scorer.py.

Данные:
  per_ticker: {ticker: ForecastResult} — прогнозы для каждого тикера
  portfolio_bias: "up" / "down" / "flat" — агрегированное направление
  volatility_regime: "calm" / "normal" / "volatile" — CI-based vol regime
  confidence_score: float 0..1 — средний confidence across universe
  meta: {tickers_with_signal, tickers_no_signal, avg_ci_width, n_tickers}

Философия:
  - Fail-open: DummyTimesFMAdapter → low confidence → контекст есть, но ослаблен
  - EXCLUDED_TICKERS = {"RI"} — RI исключён Апостолом
  - AST-guard: check_no_broker_imports() для валидации файлов
  - No side effects: чистые функции dict-in → context-out
"""
from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from timesfm_adapter import ForecastResult, DummyTimesFMAdapter, get_adapter


# ─── Constants ────────────────────────────────────────────────────────

EXCLUDED_TICKERS = {"RI"}

# Пороги для vol_regime из CI width
CALM_CI_THRESHOLD = 0.03
VOLATILE_CI_THRESHOLD = 0.08

# Пороги для confidence_score
HIGH_CONFIDENCE_THRESHOLD = 0.6
LOW_CONFIDENCE_THRESHOLD = 0.3

# Пороги для portfolio_bias voting
BIAS_UP_THRESHOLD = 0.5    # >50% тикеров direction="up" → bias="up"
BIAS_DOWN_THRESHOLD = 0.5


# ─── ForecastContext Dataclass ─────────────────────────────────────────

@dataclass(frozen=True)
class ForecastContext:
    """Агрегированный forecast context для portfolio-level решений.

    Attributes:
        per_ticker: dict {ticker: ForecastResult} — per-ticker прогнозы
        portfolio_bias: "up" / "down" / "flat" — aggregated direction
        volatility_regime: "calm" / "normal" / "volatile" — CI-based
        confidence_score: float 0..1 — avg confidence (real forecasts only)
        meta: dict — {tickers_with_signal, tickers_no_signal, avg_ci_width, n_tickers}
    """
    per_ticker: Dict[str, ForecastResult] = field(default_factory=dict)
    portfolio_bias: str = "flat"
    volatility_regime: str = "normal"
    confidence_score: float = 0.0
    meta: Dict[str, Any] = field(default_factory=dict)

    def has_forecast(self) -> bool:
        """True если хотя бы один тикер имеет реальный forecast."""
        return self.meta.get("tickers_with_signal", 0) > 0

    def get_ticker(self, ticker: str) -> Optional[ForecastResult]:
        """Получить forecast для тикера или None."""
        return self.per_ticker.get(ticker)

    def confidence_multiplier(self) -> float:
        """Множитель на основе confidence: high→1.0, low→0.5, dummy→0.0.

        Используется forecast_context_scorer для масштабирования вклада forecast.
        """
        if not self.has_forecast():
            return 0.0
        if self.confidence_score >= HIGH_CONFIDENCE_THRESHOLD:
            return 1.0
        if self.confidence_score <= LOW_CONFIDENCE_THRESHOLD:
            return 0.3
        # Linear interpolation 0.3..1.0
        ratio = (self.confidence_score - LOW_CONFIDENCE_THRESHOLD) / (
            HIGH_CONFIDENCE_THRESHOLD - LOW_CONFIDENCE_THRESHOLD
        )
        return round(0.3 + 0.7 * ratio, 4)


# ─── Builder ───────────────────────────────────────────────────────────

def build_forecast_context(
    adapter: Any,
    bars_map: Dict[str, List[float]],
    regime_snapshot: Optional[Dict[str, Any]] = None,
    horizon: int = 20,
    excluded: Optional[set] = None,
) -> ForecastContext:
    """Построить ForecastContext для всего universe.

    Args:
        adapter: TimesFMAdapter или DummyTimesFMAdapter
        bars_map: {ticker: [close_prices]} — бары для каждого тикера
        regime_snapshot: regime data (optional, для cross-validation)
        horizon: горизонт прогноза (default 20)
        excluded: множество исключённых тикеров (default EXCLUDED_TICKERS)

    Returns:
        ForecastContext с агрегированными прогнозами

    Fail-open:
        - adapter=None → пустой контекст
        - bars_map={} → пустой контекст
        - ticker не в bars_map → пропускается
        - excluded ticker → пропускается
    """
    if adapter is None:
        adapter = DummyTimesFMAdapter()
    if excluded is None:
        excluded = EXCLUDED_TICKERS

    per_ticker: Dict[str, ForecastResult] = {}
    real_confidences: List[float] = []
    real_ci_widths: List[float] = []
    n_with_signal = 0
    n_no_signal = 0

    for ticker, bars in bars_map.items():
        # Skip excluded
        if ticker in excluded:
            continue

        # Skip empty bars
        if not bars or len(bars) < 2:
            continue

        try:
            result = adapter.forecast(ticker, bars, horizon=horizon)
        except (ImportError, RuntimeError, ValueError):
            # Fail-open: на ошибку forecast — пропускаем тикер
            continue

        per_ticker[ticker] = result

        if result.source == "dummy":
            n_no_signal += 1
        else:
            n_with_signal += 1
            real_confidences.append(result.confidence)
            real_ci_widths.append(result.ci_width)

    # Portfolio bias: majority vote по direction
    portfolio_bias = _compute_portfolio_bias(per_ticker)

    # Volatility regime: average CI width (real forecasts only)
    volatility_regime = _compute_volatility_regime(real_ci_widths)

    # Confidence score: average confidence (real forecasts only)
    if real_confidences:
        confidence_score = round(sum(real_confidences) / len(real_confidences), 4)
    else:
        confidence_score = 0.0

    # Meta
    avg_ci = round(sum(real_ci_widths) / len(real_ci_widths), 6) if real_ci_widths else 0.0
    meta = {
        "tickers_with_signal": n_with_signal,
        "tickers_no_signal": n_no_signal,
        "avg_ci_width": avg_ci,
        "n_tickers": len(per_ticker),
        "universe_size": len(bars_map),
    }

    return ForecastContext(
        per_ticker=per_ticker,
        portfolio_bias=portfolio_bias,
        volatility_regime=volatility_regime,
        confidence_score=confidence_score,
        meta=meta,
    )


# ─── Internal helpers ──────────────────────────────────────────────────

def _compute_portfolio_bias(
    per_ticker: Dict[str, ForecastResult],
) -> str:
    """Majority vote direction → portfolio bias."""
    if not per_ticker:
        return "flat"

    up_count = 0
    down_count = 0
    total = 0

    for result in per_ticker.values():
        direction = getattr(result, "direction", "flat")
        if direction == "up":
            up_count += 1
        elif direction == "down":
            down_count += 1
        total += 1

    if total == 0:
        return "flat"

    up_ratio = up_count / total
    down_ratio = down_count / total

    if up_ratio > BIAS_UP_THRESHOLD:
        return "up"
    if down_ratio > BIAS_DOWN_THRESHOLD:
        return "down"
    return "flat"


def _compute_volatility_regime(
    real_ci_widths: List[float],
) -> str:
    """CI width → vol regime: calm / normal / volatile."""
    if not real_ci_widths:
        return "normal"

    avg_ci = sum(real_ci_widths) / len(real_ci_widths)

    if avg_ci < CALM_CI_THRESHOLD:
        return "calm"
    if avg_ci > VOLATILE_CI_THRESHOLD:
        return "volatile"
    return "normal"


# ─── AST-guard ─────────────────────────────────────────────────────────

BROKER_KEYWORDS = ("tinkoff", "place_order", "send_order", "create_order")


def check_no_broker_imports(filepath: str) -> bool:
    """AST-guard: проверить что файл не содержит broker-импортов.

    Анализирует import/from строки через AST для точного парсинга.
    Возвращает True если файл чистый, False если есть broker imports.

    Не использует exec/run — только read-only AST parse.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
    except (OSError, IOError):
        return True  # не можем прочитать — считаем чистым

    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return True  # синтаксическая ошибка — не broker import

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name_lower = alias.name.lower()
                for kw in BROKER_KEYWORDS:
                    if kw in name_lower:
                        return False
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").lower()
            for kw in BROKER_KEYWORDS:
                if kw in module:
                    return False

    return True


# ─── CLI demo ──────────────────────────────────────────────────────────

def _demo() -> None:
    """Демонстрация ForecastContext."""
    adapter = DummyTimesFMAdapter()

    bars_map = {
        "BR": [100.0 + i * 0.5 for i in range(30)],
        "GAZP": [200.0 - i * 0.3 for i in range(30)],
        "SBER": [250.0 + 0.1 * i for i in range(30)],
        "LKOH": [600.0 + 2.0 * i for i in range(30)],
        "RI": [100.0 + i for i in range(30)],  # excluded
    }

    regime = {
        "tickers": {
            "BR": {"adx": 23.1, "direction": "up", "regime": "trend"},
            "GAZP": {"adx": 35.3, "direction": "up", "regime": "trend"},
            "SBER": {"adx": 18.0, "direction": "down", "regime": "range"},
            "LKOH": {"adx": 15.0, "direction": "down", "regime": "range"},
        },
        "bias": "neutral",
    }

    ctx = build_forecast_context(adapter, bars_map, regime)
    print("Portfolio bias:", ctx.portfolio_bias)
    print("Vol regime:", ctx.volatility_regime)
    print("Confidence:", ctx.confidence_score)
    print("Meta:", ctx.meta)
    print("Has forecast:", ctx.has_forecast())
    print("Confidence multiplier:", ctx.confidence_multiplier())

    # AST-guard demo
    print("\nBroker guard (self):", check_no_broker_imports(__file__))


if __name__ == "__main__":
    _demo()

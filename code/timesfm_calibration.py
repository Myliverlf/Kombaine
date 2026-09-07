"""TimesFM Calibration Metrics — замыкает feedback loop: forecast results → calibration hints.

Stdlib-only модуль. Принимает forecast history (trades + forecast meta) и вычисляет:
  - direction_accuracy: доля прогнозов с правильным direction
  - excess_accuracy: direction_accuracy - base_rate (real signal vs random)
  - ci_coverage: доля actual_pnl внутри forecast CI
  - brier_score: confidence vs realization (Brier-like)
  - miscalibration_slope: linear regression confidence → actual correctness
  - horizon_accuracy_map: accuracy по горизонтам (если horizon field есть)
  - regime_accuracy: accuracy по regime (если regime field есть)

Использование:
    from timesfm_calibration import compute_calibration_report
    report = compute_calibration_report(forecast_history)
    # report.direction_accuracy, report.brier_score, report.retrain_signal

Все функции чистые, без side effects, без broker/tinkoff импортов.
"""
import math
import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ─── Data Structures ──────────────────────────────────────────────────

@dataclass
class ForecastRecord:
    """Один прогноз + его исход. Базовая единица для калибровки."""
    direction: str          # "up" / "down" / "flat"
    confidence: float       # 0..1
    ci_lower: float         # нижняя граница CI
    ci_upper: float         # верхняя граница CI
    actual_pnl: float       # реальный PnL (₽ или %, неважно — знак важен)
    horizon: Optional[int] = None   # горизонт прогноза (bars)
    regime: Optional[str] = None    # "trend" / "range" / ...
    ticker: Optional[str] = None    # для агрегации по тикеру


@dataclass
class CalibrationReport:
    """Результат калибровки forecast quality. Возвращается compute_calibration_report()."""
    n_records: int                      # количество прогнозов для анализа
    direction_accuracy: float           # 0..1, доля правильных direction
    excess_accuracy: float              # direction_accuracy - base_rate, <0 = random worse
    base_rate: float                    # доля самый частый direction в actual
    ci_coverage: float                  # 0..1, доля actual внутри CI
    brier_score: float                  # 0..1, меньше = лучше (0 = perfect)
    miscalibration_slope: float         # >0 = confident промахи, <0 = underconfident
    horizon_accuracy_map: Dict[int, float] = field(default_factory=dict)
    regime_accuracy: Dict[str, float] = field(default_factory=dict)
    regime_counts: Dict[str, int] = field(default_factory=dict)
    retrain_signal: bool = False        # True если accuracy < base_rate или CI < 80%
    scorecard_summary: str = ""         # одна строка-резюме для logging


# ─── Core Functions ───────────────────────────────────────────────────

def _actual_direction(pnl: float) -> str:
    """Реальный direction из PnL: positive → up, negative → down, 0 → flat."""
    if pnl > 0:
        return "up"
    elif pnl < 0:
        return "down"
    return "flat"


def _is_inside_ci(actual: float, ci_lower: float, ci_upper: float) -> bool:
    """Проверка: actual внутри confidence interval."""
    return ci_lower <= actual <= ci_upper


def _brier_single(confidence: float, correct: bool) -> float:
    """Brier score для одного прогноза: (confidence - correct)^2.

    correct=True → (confidence - 1)^2
    correct=False → (confidence - 0)^2 = confidence^2
    """
    target = 1.0 if correct else 0.0
    return (confidence - target) ** 2


def _linear_regression(xs: List[float], ys: List[float]) -> Tuple[float, float]:
    """Простая линейная регрессия (slope, intercept). Stdlib-only.

    Возвращает (slope, intercept). Если данных нет или variance=0 → (0, 0).
    """
    n = len(xs)
    if n < 2:
        return 0.0, 0.0

    x_mean = sum(xs) / n
    y_mean = sum(ys) / n

    num = 0.0
    den = 0.0
    for x, y in zip(xs, ys):
        dx = x - x_mean
        num += dx * (y - y_mean)
        den += dx * dx

    if abs(den) < 1e-15:
        return 0.0, y_mean

    slope = num / den
    intercept = y_mean - slope * x_mean
    return slope, intercept


def compute_calibration_report(
    records: List[ForecastRecord],
) -> CalibrationReport:
    """Главный entry-point: вычислить CalibrationReport по forecast history.

    Args:
        records: список ForecastRecord с direction, confidence, ci_lower/upper, actual_pnl.

    Returns:
        CalibrationReport со всеми метриками.

    Handles:
        - empty records → report с нулевыми метриками, retrain_signal=False
        - single record → direction_accuracy вычисляется, остальные partial
        - flat forecasts → считаются как "correct" если actual_pnl == 0
    """
    if not records:
        return CalibrationReport(
            n_records=0,
            direction_accuracy=0.0,
            excess_accuracy=0.0,
            base_rate=0.0,
            ci_coverage=0.0,
            brier_score=0.0,
            miscalibration_slope=0.0,
            retrain_signal=False,
            scorecard_summary="NO_DATA: no forecast records for calibration",
        )

    n = len(records)

    # ─── Direction Accuracy ───────────────────────────────────────────
    correct_directions = 0
    actual_directions = [_actual_direction(r.actual_pnl) for r in records]
    for rec, actual_dir in zip(records, actual_directions):
        if rec.direction == actual_dir:
            correct_directions += 1
    direction_accuracy = correct_directions / n

    # Base rate: доля самого частого actual direction
    dir_counts: Dict[str, int] = {}
    for d in actual_directions:
        dir_counts[d] = dir_counts.get(d, 0) + 1
    base_rate = max(dir_counts.values()) / n if dir_counts else 0.0
    excess_accuracy = direction_accuracy - base_rate

    # ─── CI Coverage ──────────────────────────────────────────────────
    inside_ci = 0
    for rec in records:
        if _is_inside_ci(rec.actual_pnl, rec.ci_lower, rec.ci_upper):
            inside_ci += 1
    ci_coverage = inside_ci / n

    # ─── Brier Score ──────────────────────────────────────────────────
    brier_scores = []
    for rec, actual_dir in zip(records, actual_directions):
        # correct = forecast direction matches actual
        correct = (rec.direction == actual_dir)
        brier_scores.append(_brier_single(rec.confidence, correct))
    brier_score = statistics.mean(brier_scores) if brier_scores else 0.0

    # ─── Miscalibration Slope (regression: confidence → correctness) ──
    xs = [r.confidence for r in records]
    ys = [1.0 if (r.direction == ad) else 0.0 for r, ad in zip(records, actual_directions)]
    slope, _ = _linear_regression(xs, ys)
    # slope > 0: confident → more correct (good); slope < 0: overconfident

    # ─── Horizon Accuracy Map ─────────────────────────────────────────
    horizon_data: Dict[int, List[bool]] = {}
    for rec, actual_dir in zip(records, actual_directions):
        if rec.horizon is not None:
            correct = (rec.direction == actual_dir)
            horizon_data.setdefault(rec.horizon, []).append(correct)
    horizon_accuracy_map = {
        h: sum(vals) / len(vals) for h, vals in horizon_data.items()
    }

    # ─── Regime Accuracy ──────────────────────────────────────────────
    regime_data: Dict[str, List[bool]] = {}
    for rec, actual_dir in zip(records, actual_directions):
        regime = rec.regime or "unknown"
        correct = (rec.direction == actual_dir)
        regime_data.setdefault(regime, []).append(correct)
    regime_accuracy = {
        r: sum(vals) / len(vals) for r, vals in regime_data.items()
    }
    regime_counts = {r: len(vals) for r, vals in regime_data.items()}

    # ─── Retrain Signal ───────────────────────────────────────────────
    # Retrain если: accuracy < base_rate (хуже random) ИЛИ CI coverage < 80%
    retrain_signal = (direction_accuracy < base_rate) or (ci_coverage < 0.80)

    # ─── Scorecard Summary ────────────────────────────────────────────
    parts = []
    parts.append("n=%d" % n)
    parts.append("dir_acc=%.1f%%" % (direction_accuracy * 100))
    parts.append("excess=%+.1f%%" % (excess_accuracy * 100))
    parts.append("ci_cov=%.1f%%" % (ci_coverage * 100))
    parts.append("brier=%.3f" % brier_score)
    parts.append("slope=%+.3f" % slope)
    if retrain_signal:
        parts.append("⚠RETRAIN")
    else:
        parts.append("OK")
    scorecard_summary = " | ".join(parts)

    return CalibrationReport(
        n_records=n,
        direction_accuracy=round(direction_accuracy, 4),
        excess_accuracy=round(excess_accuracy, 4),
        base_rate=round(base_rate, 4),
        ci_coverage=round(ci_coverage, 4),
        brier_score=round(brier_score, 4),
        miscalibration_slope=round(slope, 4),
        horizon_accuracy_map=horizon_accuracy_map,
        regime_accuracy=regime_accuracy,
        regime_counts=regime_counts,
        retrain_signal=retrain_signal,
        scorecard_summary=scorecard_summary,
    )


# ─── Utility: merge reports ───────────────────────────────────────────

def merge_calibration_reports(
    reports: List[CalibrationReport],
) -> CalibrationReport:
    """Склеить несколько CalibrationReport в один (weighted average по n_records).

    Используется для multi-period агрегации (дневная/недельная калибровка).
    """
    if not reports:
        return compute_calibration_report([])

    total_n = sum(r.n_records for r in reports)
    if total_n == 0:
        return compute_calibration_report([])

    def _wavg(attr: str) -> float:
        weighted = sum(getattr(r, attr) * r.n_records for r in reports)
        return weighted / total_n

    merged = CalibrationReport(
        n_records=total_n,
        direction_accuracy=round(_wavg("direction_accuracy"), 4),
        excess_accuracy=round(_wavg("excess_accuracy"), 4),
        base_rate=round(_wavg("base_rate"), 4),
        ci_coverage=round(_wavg("ci_coverage"), 4),
        brier_score=round(_wavg("brier_score"), 4),
        miscalibration_slope=round(_wavg("miscalibration_slope"), 4),
        retrain_signal=any(r.retrain_signal for r in reports),
    )

    # Merge horizon maps
    all_horizons: Dict[int, List[Tuple[float, int]]] = {}
    for r in reports:
        for h, acc in r.horizon_accuracy_map.items():
            all_horizons.setdefault(h, []).append((acc, r.n_records))
    merged.horizon_accuracy_map = {
        h: round(sum(a * w for a, w in vals) / sum(w for _, w in vals), 4)
        for h, vals in all_horizons.items()
    }

    # Merge regime maps
    all_regimes: Dict[str, List[Tuple[float, int]]] = {}
    for r in reports:
        for regime, acc in r.regime_accuracy.items():
            all_regimes.setdefault(regime, []).append((acc, r.n_records))
    merged.regime_accuracy = {
        rg: round(sum(a * w for a, w in vals) / sum(w for _, w in vals), 4)
        for rg, vals in all_regimes.items()
    }

    # Scorecard
    parts = ["n=%d" % total_n]
    parts.append("dir_acc=%.1f%%" % (merged.direction_accuracy * 100))
    parts.append("ci_cov=%.1f%%" % (merged.ci_coverage * 100))
    parts.append("brier=%.3f" % merged.brier_score)
    parts.append("⚠RETRAIN" if merged.retrain_signal else "OK")
    merged.scorecard_summary = " | ".join(parts)

    return merged


# ─── No-broker guard ──────────────────────────────────────────────────

_BROKER_KEYWORDS = frozenset({
    "tinkoff", "place_order", "send_order", "create_order",
    "futures_lab", "broker_client",
})


def check_no_broker_imports(filepath: Optional[str] = None) -> bool:
    """AST-guard: файл не содержит broker-импортов. Возвращает True если чисто."""
    import ast
    import os
    if filepath is None:
        filepath = os.path.abspath(__file__)
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
    except OSError:
        return True
    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return True
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for kw in _BROKER_KEYWORDS:
                    if kw in alias.name.lower():
                        return False
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").lower()
            for kw in _BROKER_KEYWORDS:
                if kw in module:
                    return False
    return True


# ─── CLI demo ─────────────────────────────────────────────────────────

def _demo() -> None:
    """Демонстрация калибровки на синтетических данных."""
    # Perfect forecasts
    perfect = [
        ForecastRecord(direction="up", confidence=0.9, ci_lower=0.5, ci_upper=2.0, actual_pnl=1.0, regime="trend"),
        ForecastRecord(direction="down", confidence=0.85, ci_lower=-2.0, ci_upper=-0.3, actual_pnl=-1.0, regime="range"),
        ForecastRecord(direction="up", confidence=0.7, ci_lower=0.1, ci_upper=1.5, actual_pnl=0.5, regime="trend"),
    ]
    report = compute_calibration_report(perfect)
    print("Perfect:", report.scorecard_summary)

    # Degraded forecasts
    degraded = [
        ForecastRecord(direction="up", confidence=0.9, ci_lower=0.5, ci_upper=2.0, actual_pnl=-1.0, regime="range"),
        ForecastRecord(direction="up", confidence=0.8, ci_lower=0.1, ci_upper=1.5, actual_pnl=-0.5, regime="range"),
        ForecastRecord(direction="down", confidence=0.7, ci_lower=-2.0, ci_upper=-0.3, actual_pnl=1.0, regime="trend"),
    ]
    report2 = compute_calibration_report(degraded)
    print("Degraded:", report2.scorecard_summary)

    # Empty
    empty = compute_calibration_report([])
    print("Empty:", empty.scorecard_summary)


if __name__ == "__main__":
    _demo()

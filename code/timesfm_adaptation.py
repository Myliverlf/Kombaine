"""TimesFM Adaptation Hints — actionable recommendations layer для TimesFM layer.

Замыкает feedback loop: CalibrationReport → actionable hints:
  - suggest_horizon: оптимальный горизонт по horizon_accuracy_map
  - suggest_confidence_threshold: порог confidence по brier_score
  - regime_overrides: regime-specific forecast adjustments
  - retrain_signal: bool — accuracy < base_rate или CI < 80%
  - config_patch: dict → patched config для TimesFM adapter

Зависимости: timesfm_calibration.CalibrationReport (read-only).
Stdlib-only, без broker/tinkoff импортов.

Использование:
    from timesfm_adaptation import compute_adaptation_hints
    hints = compute_adaptation_hints(calibration_report, current_config)
    # hints.suggest_horizon, hints.config_patch, hints.scorecard_summary
"""
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from timesfm_calibration import CalibrationReport


# ─── Data Structures ──────────────────────────────────────────────────

@dataclass
class AdaptationHints:
    """Actionable hints для TimesFM layer, на основе CalibrationReport."""
    suggest_horizon: Optional[int] = None     # оптимальный горизонт (None = без изменений)
    suggest_confidence_threshold: Optional[float] = None  # 0..1 порог
    regime_overrides: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    retrain_signal: bool = False              # True = нужен fresh retest
    retrain_reason: str = ""                  # причина retrain
    config_patch: Dict[str, Any] = field(default_factory=dict)
    scorecard_summary: str = ""               # одна строка-резюме для logging
    actions: List[str] = field(default_factory=list)  # список конкретных действий


# ─── Constants ────────────────────────────────────────────────────────

# Brier score thresholds
BRIER_GOOD = 0.15       # brier < 0.15 → well-calibrated
BRIER_WARN = 0.30       # brier > 0.30 → poorly calibrated → raise threshold

# CI coverage thresholds
CI_GOOD = 0.90          # 90%+ coverage → CI is fine
CI_WARN = 0.80          # < 80% → CI too narrow or model unreliable
CI_BAD = 0.60           # < 60% → CI is broken

# Excess accuracy thresholds
EXCESS_GOOD = 0.05      # > 5% excess → useful signal
EXCESS_BAD = -0.05      # < -5% → worse than random

# Confidence threshold adjustment scale
CONF_THRESHOLD_LOW = 0.4
CONF_THRESHOLD_HIGH = 0.8


# ─── Core Function ────────────────────────────────────────────────────

def compute_adaptation_hints(
    report: CalibrationReport,
    current_config: Optional[Dict[str, Any]] = None,
) -> AdaptationHints:
    """Вычислить actionable hints на основе CalibrationReport.

    Args:
        report: CalibrationReport от compute_calibration_report()
        current_config: текущий конфиг TimesFM (horizon, confidence_threshold, etc.)
            Опционально — для incremental patching.

    Returns:
        AdaptationHints с рекомендациями.
    """
    if current_config is None:
        current_config = {}

    hints = AdaptationHints()
    actions: List[str] = []

    # ─── No data → no hints ───────────────────────────────────────────
    if report.n_records == 0:
        hints.scorecard_summary = "NO_DATA: нет данных для adaptation"
        hints.actions = actions
        return hints

    # ─── 1. Suggest Horizon ───────────────────────────────────────────
    if report.horizon_accuracy_map:
        best_horizon = None
        best_acc = -1.0
        for horiz, acc in report.horizon_accuracy_map.items():
            if acc > best_acc:
                best_acc = acc
                best_horizon = horiz
        current_horizon = current_config.get("horizon")
        if best_horizon is not None and best_horizon != current_horizon:
            hints.suggest_horizon = best_horizon
            hints.config_patch["horizon"] = best_horizon
            actions.append(
                "horizon: %s → %d (accuracy %.1f%%)" % (
                    current_horizon, best_horizon, best_acc * 100
                )
            )
        elif best_horizon is not None:
            hints.suggest_horizon = best_horizon
            actions.append("horizon: %d (optimal, no change)" % best_horizon)

    # ─── 2. Suggest Confidence Threshold ──────────────────────────────
    brier = report.brier_score
    if brier > BRIER_WARN:
        # Poorly calibrated → raise confidence threshold (be more selective)
        new_threshold = min(CONF_THRESHOLD_HIGH, CONF_THRESHOLD_LOW + 0.2)
        current_threshold = current_config.get("confidence_threshold", CONF_THRESHOLD_LOW)
        if new_threshold > current_threshold:
            hints.suggest_confidence_threshold = new_threshold
            hints.config_patch["confidence_threshold"] = new_threshold
            actions.append(
                "confidence_threshold: %.2f → %.2f (brier=%.3f, poor calibration)" % (
                    current_threshold, new_threshold, brier
                )
            )
    elif brier < BRIER_GOOD:
        # Well-calibrated → can lower threshold slightly (more inclusive)
        new_threshold = max(0.2, CONF_THRESHOLD_LOW - 0.1)
        current_threshold = current_config.get("confidence_threshold", CONF_THRESHOLD_LOW)
        if new_threshold < current_threshold:
            hints.suggest_confidence_threshold = new_threshold
            hints.config_patch["confidence_threshold"] = new_threshold
            actions.append(
                "confidence_threshold: %.2f → %.2f (brier=%.3f, well-calibrated)" % (
                    current_threshold, new_threshold, brier
                )
            )

    # ─── 3. Regime Overrides ──────────────────────────────────────────
    for regime, acc in report.regime_accuracy.items():
        if regime == "unknown":
            continue
        regime_count = report.regime_counts.get(regime, 0)
        if regime_count < 3:
            # Too few samples for reliable regime override
            continue
        if acc > 0.6:
            # Good accuracy in this regime → boost confidence
            hints.regime_overrides[regime] = {
                "action": "boost_confidence",
                "factor": round(min(1.3, 0.8 + acc * 0.5), 3),
                "reason": "accuracy=%.1f%% in %s (n=%d)" % (acc * 100, regime, regime_count),
            }
            actions.append("regime_%s: boost confidence (acc=%.1f%%)" % (regime, acc * 100))
        elif acc < 0.4:
            # Poor accuracy → reduce confidence or skip this regime
            hints.regime_overrides[regime] = {
                "action": "reduce_confidence",
                "factor": round(max(0.5, acc), 3),
                "reason": "accuracy=%.1f%% in %s (n=%d) — unreliable" % (
                    acc * 100, regime, regime_count
                ),
            }
            actions.append("regime_%s: reduce confidence (acc=%.1f%%)" % (regime, acc * 100))

    # ─── 4. Retrain Signal ────────────────────────────────────────────
    retrain_reasons = []

    if report.excess_accuracy < EXCESS_BAD:
        retrain_reasons.append(
            "excess_accuracy=%.1f%% < %.1f%%" % (report.excess_accuracy * 100, EXCESS_BAD * 100)
        )

    if report.ci_coverage < CI_BAD:
        retrain_reasons.append(
            "ci_coverage=%.1f%% < %.1f%%" % (report.ci_coverage * 100, CI_BAD * 100)
        )
    elif report.ci_coverage < CI_WARN and report.brier_score > BRIER_WARN:
        retrain_reasons.append(
            "ci_coverage=%.1f%% + brier=%.3f (combined degradation)" % (
                report.ci_coverage * 100, report.brier_score
            )
        )

    if report.retrain_signal:
        # CalibrationReport already flagged retrain — add reasons
        if report.direction_accuracy < report.base_rate:
            retrain_reasons.append(
                "direction_accuracy=%.1f%% < base_rate=%.1f%%" % (
                    report.direction_accuracy * 100, report.base_rate * 100
                )
            )

    hints.retrain_signal = len(retrain_reasons) > 0
    hints.retrain_reason = "; ".join(retrain_reasons)
    if hints.retrain_signal:
        actions.append("RETRAIN: %s" % hints.retrain_reason)

    # ─── 5. Scorecard Summary ─────────────────────────────────────────
    parts = []
    parts.append("n=%d" % report.n_records)
    parts.append("dir_acc=%.1f%%" % (report.direction_accuracy * 100))
    parts.append("brier=%.3f" % report.brier_score)
    parts.append("ci=%.1f%%" % (report.ci_coverage * 100))
    if hints.suggest_horizon is not None:
        parts.append("horizon→%d" % hints.suggest_horizon)
    if hints.suggest_confidence_threshold is not None:
        parts.append("conf_thresh→%.2f" % hints.suggest_confidence_threshold)
    n_regimes = len(hints.regime_overrides)
    if n_regimes:
        parts.append("regime_overrides=%d" % n_regimes)
    parts.append("⚠RETRAIN" if hints.retrain_signal else "ADAPT_OK")
    hints.scorecard_summary = " | ".join(parts)
    hints.actions = actions

    return hints


# ─── Config Patcher Utility ───────────────────────────────────────────

def apply_hints_to_config(
    base_config: Dict[str, Any],
    hints: AdaptationHints,
) -> Dict[str, Any]:
    """Применить config_patch из hints к base config. Возвращает новый dict.

    Не мутирует base_config.
    """
    patched = dict(base_config)
    patched.update(hints.config_patch)

    # Apply regime_overrides as a nested config
    if hints.regime_overrides:
        existing_overrides = patched.get("regime_overrides", {})
        patched["regime_overrides"] = {**existing_overrides, **hints.regime_overrides}

    return patched


# ─── No-broker guard ──────────────────────────────────────────────────

_BROKER_KEYWORDS = frozenset({
    "tinkoff", "place_order", "send_order", "create_order",
    "futures_lab", "broker_client",
})


def check_no_broker_imports(filepath: Optional[str] = None) -> bool:
    """AST-guard: файл не содержит broker-импортов."""
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
    """Демонстрация adaptation hints."""
    from timesfm_calibration import ForecastRecord, compute_calibration_report

    # Degraded forecasts
    records = [
        ForecastRecord(direction="up", confidence=0.9, ci_lower=0.5, ci_upper=2.0, actual_pnl=-1.0, horizon=20, regime="range"),
        ForecastRecord(direction="up", confidence=0.8, ci_lower=0.1, ci_upper=1.5, actual_pnl=-0.5, horizon=20, regime="range"),
        ForecastRecord(direction="down", confidence=0.7, ci_lower=-2.0, ci_upper=-0.3, actual_pnl=1.0, horizon=10, regime="trend"),
        ForecastRecord(direction="up", confidence=0.6, ci_lower=-0.5, ci_upper=1.0, actual_pnl=-0.3, horizon=10, regime="range"),
    ]
    report = compute_calibration_report(records)
    print("Report:", report.scorecard_summary)

    config = {"horizon": 20, "confidence_threshold": 0.4}
    hints = compute_adaptation_hints(report, config)
    print("Hints:", hints.scorecard_summary)
    for action in hints.actions:
        print("  →", action)

    patched = apply_hints_to_config(config, hints)
    print("Patched config:", patched)


if __name__ == "__main__":
    _demo()

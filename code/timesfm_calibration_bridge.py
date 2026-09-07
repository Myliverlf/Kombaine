"""TimesFM Calibration Bridge — integration bridge: trades + forecasts → feedback.json.

Bridge-модуль:
  - Принимает trades list + forecast history → CalibrationReport → AdaptationHints
  - Формирует расширенную секцию `forecast_calibration` для generator_feedback.json
  - Опционально пишет в analytics.db таблицу `forecast_calibration` (создаёт если нет)
  - Функция build_calibration_feedback(trades, forecasts, config) → dict

Зависимости (read-only):
  - timesfm_calibration (ForecastRecord, compute_calibration_report)
  - timesfm_adaptation (compute_adaptation_hints, apply_hints_to_config)

НЕ мутирует generator_bridge.py напрямую (additive).
Нет broker/tinkoff импортов.
"""
import json
import math
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from timesfm_calibration import ForecastRecord, compute_calibration_report, CalibrationReport
from timesfm_adaptation import compute_adaptation_hints, AdaptationHints, apply_hints_to_config


# ─── Bridge Function ──────────────────────────────────────────────────

def build_calibration_feedback(
    trades: List[Dict[str, Any]],
    forecasts: List[Dict[str, Any]],
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Основной bridge: trades + forecasts → calibration feedback dict.

    Args:
        trades: список dict-трейдов с ключами:
            - ticker, direction ("LONG"/"SHORT"), pnl (float, RUB or %),
            - regime (optional), hour (optional), exit_reason (optional)
        forecasts: список dict-прогнозов с ключами:
            - ticker, direction ("up"/"down"/"flat"), confidence (0..1),
            - ci_lower, ci_upper, horizon (optional), regime (optional)
            FORECASTS[i] соответствует TRADES[i] по индексу (paired).
        config: опциональный конфиг TimesFM (horizon, confidence_threshold, etc.)

    Returns:
        dict с ключом "forecast_calibration" для вставки в generator_feedback.json:
        {
            "forecast_calibration": {
                "timestamp": "...",
                "calibration_report": {...},
                "adaptation_hints": {...},
                "config_patch": {...},
                "actions": [...],
                "scorecard": "..."
            }
        }
    """
    if config is None:
        config = {}

    # ─── Pair trades with forecasts ───────────────────────────────────
    n_pairs = min(len(trades), len(forecasts))
    records: List[ForecastRecord] = []

    for i in range(n_pairs):
        trade = trades[i]
        forecast = forecasts[i]

        # Trade direction → actual PnL sign
        pnl = trade.get("pnl", 0.0)
        if not isinstance(pnl, (int, float)):
            pnl = 0.0

        # Forecast fields
        direction = forecast.get("direction", "flat")
        confidence = _safe_float(forecast.get("confidence", 0.0), 0.0)
        ci_lower = _safe_float(forecast.get("ci_lower", -1.0), -1.0)
        ci_upper = _safe_float(forecast.get("ci_upper", 1.0), 1.0)
        horizon = forecast.get("horizon")
        regime = forecast.get("regime") or trade.get("regime")
        ticker = trade.get("ticker") or forecast.get("ticker")

        record = ForecastRecord(
            direction=direction,
            confidence=confidence,
            ci_lower=ci_lower,
            ci_upper=ci_upper,
            actual_pnl=float(pnl),
            horizon=horizon,
            regime=regime,
            ticker=ticker,
        )
        records.append(record)

    # ─── Compute calibration ──────────────────────────────────────────
    calibration_report = compute_calibration_report(records)

    # ─── Compute adaptation hints ─────────────────────────────────────
    hints = compute_adaptation_hints(calibration_report, config)

    # ─── Build feedback dict ──────────────────────────────────────────
    now_iso = datetime.now(timezone.utc).isoformat()

    feedback_section = {
        "timestamp": now_iso,
        "n_trades": len(trades),
        "n_forecasts": len(forecasts),
        "n_pairs": n_pairs,
        "calibration_report": _report_to_dict(calibration_report),
        "adaptation_hints": _hints_to_dict(hints),
        "config_patch": hints.config_patch,
        "actions": hints.actions,
        "scorecard": hints.scorecard_summary,
        "retrain_signal": hints.retrain_signal,
    }

    return {"forecast_calibration": feedback_section}


def _report_to_dict(report: CalibrationReport) -> Dict[str, Any]:
    """CalibrationReport → dict для JSON serialization."""
    return {
        "n_records": report.n_records,
        "direction_accuracy": report.direction_accuracy,
        "excess_accuracy": report.excess_accuracy,
        "base_rate": report.base_rate,
        "ci_coverage": report.ci_coverage,
        "brier_score": report.brier_score,
        "miscalibration_slope": report.miscalibration_slope,
        "horizon_accuracy_map": {str(k): v for k, v in report.horizon_accuracy_map.items()},
        "regime_accuracy": report.regime_accuracy,
        "regime_counts": report.regime_counts,
        "retrain_signal": report.retrain_signal,
        "scorecard_summary": report.scorecard_summary,
    }


def _hints_to_dict(hints: AdaptationHints) -> Dict[str, Any]:
    """AdaptationHints → dict для JSON serialization."""
    return {
        "suggest_horizon": hints.suggest_horizon,
        "suggest_confidence_threshold": hints.suggest_confidence_threshold,
        "regime_overrides": hints.regime_overrides,
        "retrain_signal": hints.retrain_signal,
        "retrain_reason": hints.retrain_reason,
        "actions": hints.actions,
        "scorecard_summary": hints.scorecard_summary,
    }


def _safe_float(val: Any, default: float) -> float:
    """Безопасное преобразование в float."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


# ─── SQLite Integration (optional) ───────────────────────────────────

def write_calibration_to_db(
    db_path: str,
    feedback_section: Dict[str, Any],
) -> bool:
    """Опционально записать calibration feedback в analytics.db.

    Создаёт таблицу forecast_calibration если её нет (без миграции).
    Возвращает True если запись успешна.

    Не мутирует существующие таблицы.
    """
    if not os.path.exists(db_path):
        return False

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Create table if not exists (без миграции)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS forecast_calibration (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                n_trades INTEGER NOT NULL,
                n_pairs INTEGER NOT NULL,
                direction_accuracy REAL,
                ci_coverage REAL,
                brier_score REAL,
                retrain_signal INTEGER,
                config_patch_json TEXT,
                actions_json TEXT,
                scorecard TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # Insert
        cursor.execute("""
            INSERT INTO forecast_calibration
            (timestamp, n_trades, n_pairs, direction_accuracy, ci_coverage,
             brier_score, retrain_signal, config_patch_json, actions_json, scorecard)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            feedback_section.get("timestamp", ""),
            feedback_section.get("n_trades", 0),
            feedback_section.get("n_pairs", 0),
            feedback_section.get("calibration_report", {}).get("direction_accuracy", 0.0),
            feedback_section.get("calibration_report", {}).get("ci_coverage", 0.0),
            feedback_section.get("calibration_report", {}).get("brier_score", 0.0),
            1 if feedback_section.get("retrain_signal", False) else 0,
            json.dumps(feedback_section.get("config_patch", {}), ensure_ascii=False),
            json.dumps(feedback_section.get("actions", []), ensure_ascii=False),
            feedback_section.get("scorecard", ""),
        ))

        conn.commit()
        conn.close()
        return True

    except (sqlite3.Error, OSError, ValueError):
        return False


# ─── Write to generator_feedback.json ─────────────────────────────────

def write_feedback_json(
    feedback_path: str,
    feedback_section: Dict[str, Any],
) -> bool:
    """Записать/расширить generator_feedback.json секцией forecast_calibration.

    Читает существующий JSON, добавляет/обновляет ключ "forecast_calibration".
    Если файл не существует — создаёт новый. Не мутирует другие ключи.
    Возвращает True если запись успешна.
    """
    existing: Dict[str, Any] = {}

    if os.path.exists(feedback_path):
        try:
            with open(feedback_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                existing = loaded
        except (json.JSONDecodeError, OSError, ValueError):
            existing = {}

    # Merge: forecast_calibration key
    existing["forecast_calibration"] = feedback_section.get("forecast_calibration", {})

    try:
        os.makedirs(os.path.dirname(feedback_path) or ".", exist_ok=True)
        with open(feedback_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        return True
    except (OSError, ValueError):
        return False


# ─── No-broker guard ──────────────────────────────────────────────────

_BROKER_KEYWORDS = frozenset({
    "tinkoff", "place_order", "send_order", "create_order",
    "futures_lab", "broker_client",
})


def check_no_broker_imports(filepath: Optional[str] = None) -> bool:
    """AST-guard: файл не содержит broker-импортов."""
    import ast
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
    """Демонстрация bridge."""
    trades = [
        {"ticker": "BR", "direction": "LONG", "pnl": 150.0, "regime": "trend"},
        {"ticker": "GAZP", "direction": "SHORT", "pnl": -80.0, "regime": "range"},
        {"ticker": "SBER", "direction": "LONG", "pnl": 200.0, "regime": "trend"},
    ]
    forecasts = [
        {"ticker": "BR", "direction": "up", "confidence": 0.75, "ci_lower": 0.5, "ci_upper": 2.0, "horizon": 20, "regime": "trend"},
        {"ticker": "GAZP", "direction": "down", "confidence": 0.6, "ci_lower": -2.0, "ci_upper": -0.2, "horizon": 20, "regime": "range"},
        {"ticker": "SBER", "direction": "up", "confidence": 0.8, "ci_lower": 0.2, "ci_upper": 1.5, "horizon": 10, "regime": "trend"},
    ]
    config = {"horizon": 20, "confidence_threshold": 0.4}

    result = build_calibration_feedback(trades, forecasts, config)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    _demo()

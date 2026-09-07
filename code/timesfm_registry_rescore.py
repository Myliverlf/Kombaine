#!/usr/bin/env python3
"""Re-score existing StrategyRegistry records with real TimesFM advisory.

No broker calls. Uses local CSVs + timesfm_adapter. Updates metrics/rank in
strategy_registry and exports signal_pool/waitlist derived views.
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Dict

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROP_ROOT = PROJECT_ROOT.parent
DATA_ROOT = PROP_ROOT / "futures_lab" / "artifacts" / "tinkoff_futures_data"
sys.path.insert(0, str(PROJECT_ROOT / "code"))

from strategy_registry import StrategyRegistry  # type: ignore  # noqa:E402
from timesfm_adapter import get_adapter  # type: ignore  # noqa:E402

REPORT_DIR = PROJECT_ROOT / "reports" / "strategy_architect"


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return default
        return x
    except Exception:
        return default


def rank_score(metrics: Dict[str, Any]) -> float:
    pnl = safe_float(metrics.get("total_pnl"))
    pf = safe_float(metrics.get("profit_factor"))
    sharpe = safe_float(metrics.get("sharpe"))
    dd = safe_float(metrics.get("max_drawdown"))
    tf_conf = safe_float(metrics.get("timesfm_confidence"))
    tf_src = str(metrics.get("timesfm_source", ""))
    tf_dir = str(metrics.get("timesfm_direction", "flat"))
    timesfm_bonus = 0.0
    if tf_src == "timesfm":
        timesfm_bonus = tf_conf * 100.0
        if tf_dir != "flat":
            timesfm_bonus += tf_conf * 25.0
    return pnl + pf * 100.0 + sharpe * 50.0 - dd * 0.05 + timesfm_bonus


def latest_closes(ticker: str, timeframe: str) -> list[float] | None:
    p = DATA_ROOT / f"{ticker}_60d_{timeframe}_continuous.csv"
    if not p.exists():
        return None
    try:
        df = pd.read_csv(p)
    except Exception:
        return None
    if "close" not in df or len(df) < 32:
        return None
    return [safe_float(x) for x in df["close"].tail(256).tolist()]


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    adapter = get_adapter({})
    reg = StrategyRegistry.load()
    updated = 0
    skipped = 0
    source_seen = set()
    by_ticker: dict[str, dict[str, Any]] = {}

    for rec in reg.records():
        timeframe = str(rec.portfolio_context.get("timeframe") or rec.metrics.get("timeframe") or "1h")
        key = f"{rec.ticker}:{timeframe}"
        if key not in by_ticker:
            closes = latest_closes(rec.ticker, timeframe)
            if not closes:
                by_ticker[key] = {"error": "no_csv_or_close"}
            else:
                fr = adapter.forecast(rec.ticker, closes, horizon=20)
                by_ticker[key] = {
                    "direction": fr.direction,
                    "confidence": fr.confidence,
                    "source": fr.source,
                    "horizon": fr.horizon,
                }
        tf = by_ticker[key]
        if "error" in tf:
            skipped += 1
            continue
        metrics = dict(rec.metrics)
        metrics.update({
            "timesfm_direction": tf["direction"],
            "timesfm_confidence": tf["confidence"],
            "timesfm_source": tf["source"],
            "timesfm_rescored_ts": time.time(),
            "timeframe": timeframe,
        })
        source_seen.add(tf["source"])
        rec.metrics = metrics
        rec.active_rank = rank_score(metrics)
        rec.add_event(rec.status, reason="timesfm_rescore", note="registry rescore with TimesFM advisory", payload={"live_orders": 0, "timesfm": tf})
        reg._store_record(rec)
        updated += 1

    reg.save()
    reg.export_legacy_state_files()
    payload = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "updated": updated,
        "skipped": skipped,
        "timesfm_source_seen": sorted(source_seen),
        "adapter_class": type(adapter).__name__,
        "real_timesfm": type(adapter).__name__ == "TimesFMAdapter",
        "live_orders": 0,
    }
    (REPORT_DIR / "timesfm_rescore_latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

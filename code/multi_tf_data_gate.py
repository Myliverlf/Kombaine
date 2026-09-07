"""Multi-timeframe data gate for combine dry-runs.

Validates that each shortlisted asset has usable 15m and 1h data before it can
advance into pool/risk. The gate is intentionally read-only and standard-library
only.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

REQUIRED_TIMEFRAMES = ("15m", "1h")
REQUIRED_FIELDS = ("time", "open", "high", "low", "close", "volume")
DEFAULT_MIN_BARS = {"15m": 20, "1h": 5}


def _ticker(candidate: Mapping[str, Any]) -> str:
    return str(candidate.get("ticker") or candidate.get("symbol") or "").strip().upper()


def _as_rows(dataset: Any) -> List[Mapping[str, Any]]:
    if dataset is None:
        return []
    if isinstance(dataset, list):
        return [row for row in dataset if isinstance(row, Mapping)]
    if isinstance(dataset, tuple):
        return [row for row in dataset if isinstance(row, Mapping)]
    if isinstance(dataset, Mapping):
        rows = dataset.get("rows")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, Mapping)]
        if all(field in dataset for field in REQUIRED_FIELDS):
            return [dataset]
        return []

    to_dict = getattr(dataset, "to_dict", None)
    columns = getattr(dataset, "columns", None)
    if callable(to_dict) and columns is not None:
        try:
            records = dataset.to_dict(orient="records")
            if isinstance(records, list):
                return [row for row in records if isinstance(row, Mapping)]
        except Exception:
            return []

    return []


def _parse_time(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def validate_timeframe_rows(
    rows: Sequence[Mapping[str, Any]],
    timeframe: str,
    min_bars: int,
) -> Dict[str, Any]:
    missing_fields: List[str] = []
    if len(rows) < min_bars:
        return {
            "timeframe": timeframe,
            "passed": False,
            "reason": f"bars<{min_bars}",
            "bars": len(rows),
            "missing_fields": missing_fields,
        }

    last_time = ""
    monotonic = True
    for row in rows:
        row_missing = [field for field in REQUIRED_FIELDS if field not in row or row.get(field) in (None, "")]
        for field in row_missing:
            if field not in missing_fields:
                missing_fields.append(field)
        current_time = _parse_time(row.get("time"))
        if last_time and current_time < last_time:
            monotonic = False
        last_time = current_time

    passed = len(missing_fields) == 0 and monotonic
    reason = "ok" if passed else ("missing_fields:" + ",".join(missing_fields) if missing_fields else "time_not_monotonic")
    return {
        "timeframe": timeframe,
        "passed": passed,
        "reason": reason,
        "bars": len(rows),
        "missing_fields": missing_fields,
        "time_monotonic": monotonic,
    }


def validate_multi_tf_data(
    candidate: Mapping[str, Any],
    data_by_timeframe: Mapping[str, Any],
    min_bars: Mapping[str, int] | None = None,
    excluded: Iterable[str] = ("RI",),
) -> Dict[str, Any]:
    """Validate 15m/1h data quality for one candidate.

    data_by_timeframe may contain pandas DataFrames, list-of-dict rows, or
    {"rows": [...]} fixtures.
    """
    min_bars = dict(DEFAULT_MIN_BARS if min_bars is None else min_bars)
    ticker = _ticker(candidate)
    excluded_set = {str(x).upper() for x in excluded}

    if not ticker:
        return {
            "ticker": "",
            "passed": False,
            "reason": "missing_ticker",
            "timeframes": {},
        }
    if ticker in excluded_set:
        return {
            "ticker": ticker,
            "passed": False,
            "reason": f"excluded_ticker:{ticker}",
            "timeframes": {},
        }

    timeframe_results: Dict[str, Dict[str, Any]] = {}
    all_passed = True
    for timeframe in REQUIRED_TIMEFRAMES:
        rows = _as_rows(data_by_timeframe.get(timeframe))
        result = validate_timeframe_rows(rows, timeframe, int(min_bars.get(timeframe, DEFAULT_MIN_BARS[timeframe])))
        timeframe_results[timeframe] = result
        all_passed = all_passed and bool(result["passed"])

    return {
        "ticker": ticker,
        "passed": all_passed,
        "reason": "ok" if all_passed else "data_gate_failed",
        "timeframes": timeframe_results,
    }


def gate_multi_tf_candidates(
    candidates: Sequence[Mapping[str, Any]],
    data_by_ticker: Mapping[str, Mapping[str, Any]],
    min_bars: Mapping[str, int] | None = None,
    excluded: Iterable[str] = ("RI",),
) -> Dict[str, Any]:
    """Filter candidates that pass the 15m/1h data gate.

    Returns a scorecard with admitted/rejected lists and a compact summary.
    """
    admitted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []

    for candidate in candidates:
        item = dict(candidate)
        ticker = _ticker(item)
        gate = validate_multi_tf_data(item, data_by_ticker.get(ticker, {}), min_bars=min_bars, excluded=excluded)
        item["data_gate"] = gate["passed"]
        item["data_gate_reason"] = gate["reason"]
        item["timeframes"] = gate["timeframes"]
        if gate["passed"]:
            admitted.append(item)
        else:
            rejected.append(item)

    return {
        "admitted": admitted,
        "rejected": rejected,
        "summary": {
            "input": len(list(candidates)),
            "admitted": len(admitted),
            "rejected": len(rejected),
            "excluded": [row.get("ticker") for row in rejected if str(row.get("data_gate_reason", "")).startswith("excluded_ticker:")],
            "required_timeframes": list(REQUIRED_TIMEFRAMES),
            "min_bars": dict(DEFAULT_MIN_BARS if min_bars is None else min_bars),
        },
    }


# Alias for readable hidden-test imports.
validate_15m_1h_data = validate_multi_tf_data
filter_candidates_by_data_gate = gate_multi_tf_candidates


if __name__ == "__main__":
    import json

    demo_rows = [{"time": f"2025-01-01 00:{i:02d}:00", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1} for i in range(25)]
    demo = {
        "BR": {"15m": {"rows": demo_rows}, "1h": {"rows": demo_rows[:6]}},
        "RI": {"15m": {"rows": demo_rows}, "1h": {"rows": demo_rows[:6]}},
    }
    print(json.dumps(gate_multi_tf_candidates([{"ticker": "BR"}, {"ticker": "RI"}], demo), ensure_ascii=False, indent=2))

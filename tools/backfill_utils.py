from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any

import pandas as pd


def sha(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def rows_hash(rows: list[dict[str, Any]]) -> str:
    def _default(o):
        return o.isoformat() if hasattr(o, 'isoformat') else str(o)
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, separators=(',', ':'), default=_default).encode('utf-8')).hexdigest()


def manifest_hash(manifest) -> str:
    return sha(json.dumps(asdict(manifest), sort_keys=True, ensure_ascii=False, default=str))


def build_windows(end: datetime, target_days: int, chunk_days: int, overlap_days: int = 1):
    windows = []
    cursor = end - timedelta(days=target_days)
    while cursor < end:
        nxt = min(cursor + timedelta(days=chunk_days), end)
        windows.append((cursor, nxt))
        cursor = nxt - timedelta(days=overlap_days)
        if cursor >= end:
            break
    return windows


def backward_windows(end: datetime, chunk_days: int, overlap_days: int = 1, floor: datetime | None = None) -> list[tuple[datetime, datetime]]:
    """Deterministic backward window chain from `end`.

    Window (fr, to) semantics: from_ inclusive, to exclusive (provider API).
    Adjacent windows overlap by `overlap_days` for consistency verification.
    Stops when floor (listing date / retention boundary) is reached.
    """
    if overlap_days >= chunk_days:
        raise ValueError('overlap_days must be < chunk_days')
    windows = []
    to = end
    while True:
        fr = to - timedelta(days=chunk_days)
        if floor is not None and fr < floor:
            fr = floor
        windows.append((fr, to))
        if floor is not None and fr <= floor:
            break
        to = fr + timedelta(days=overlap_days)
    return windows


def merge_dedupe(existing: list[dict[str, Any]], new_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]]]:
    """Merge new rows into existing rows, dedupe on time.

    Identical overlaps are deduped deterministically (existing wins).
    Conflicting overlaps (same timestamp, different OHLCV) are collected
    and returned — caller must FAIL/QUARANTINE, never silently pick one.

    Returns (merged_sorted_rows, identical_dup_count, conflicts).
    """
    index = {r['time']: r for r in existing}
    identical = 0
    conflicts: list[dict[str, Any]] = []
    for r in new_rows:
        cur = index.get(r['time'])
        if cur is None:
            index[r['time']] = r
            continue
        if (cur['open'], cur['high'], cur['low'], cur['close'], cur['volume']) == (r['open'], r['high'], r['low'], r['close'], r['volume']):
            identical += 1
        else:
            conflicts.append({'time': r['time'], 'existing': cur, 'incoming': r})
    merged = sorted(index.values(), key=lambda r: r['time'])
    return merged, identical, conflicts


def suspicious_gaps(rows: list[dict[str, Any]], timeframe: str, max_gap_hours: float = 240.0) -> list[dict[str, Any]]:
    """Find internal gaps larger than max_gap_hours between consecutive bars.

    Expected market closures (weekends/holidays) produce <= ~4-day gaps for
    1h and <= ~3-day-equivalent gaps for 15m — well under the 240h default.
    Anything larger is a suspicious gap (delisting halt, data hole).
    """
    gaps = []
    step = timedelta(minutes=15) if timeframe == '15m' else timedelta(hours=1)
    for a, b in zip(rows, rows[1:]):
        ta = a['time'] if isinstance(a['time'], datetime) else datetime.fromisoformat(a['time'])
        tb = b['time'] if isinstance(b['time'], datetime) else datetime.fromisoformat(b['time'])
        delta = tb - ta
        if delta.total_seconds() / 3600.0 > max_gap_hours:
            gaps.append({'from': ta.isoformat(), 'to': tb.isoformat(), 'gap_hours': delta.total_seconds() / 3600.0})
        elif delta <= timedelta(0):
            gaps.append({'from': ta.isoformat(), 'to': tb.isoformat(), 'gap_hours': delta.total_seconds() / 3600.0, 'issue': 'non_monotonic_or_zero_step'})
    return gaps


def quality_check(df: pd.DataFrame) -> tuple[str, dict[str, Any]]:
    issues: list[str] = []
    if df.empty:
        issues.append('empty')
    if df[['open', 'high', 'low', 'close', 'volume']].isna().any().any():
        issues.append('nulls')
    if not df['time'].is_monotonic_increasing:
        issues.append('non_monotonic')
    if not (df['high'] >= df[['open', 'close', 'low']].max(axis=1)).all():
        issues.append('ohlc_high_violation')
    if not (df['low'] <= df[['open', 'close', 'high']].min(axis=1)).all():
        issues.append('ohlc_low_violation')
    if (df['volume'] < 0).any():
        issues.append('negative_volume')
    return ('PASS' if not issues else 'FAIL', {'issues': issues})

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

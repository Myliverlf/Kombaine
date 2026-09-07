"""History certification for canonical market datasets.

Iteration 23I: machine-readable certification for long-history acquisition.
Non-trading, fail-closed, deterministic.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

CSV_COLUMNS = ["time", "open", "high", "low", "close", "volume"]

@dataclass(frozen=True)
class HistoryCertificate:
    instrument: str
    timeframe: str
    requested_horizon_days: int
    actual_first_ts: str
    actual_last_ts: str
    actual_coverage_days: int
    candle_count: int
    duplicate_rows: int
    malformed_rows: int
    invalid_ohlc_rows: int
    invalid_volume_rows: int
    timezone: str
    timestamp_unique: bool
    ordering_ok: bool
    provenance_complete: bool
    suspicious_gaps: List[Dict[str, Any]]
    source_path: str
    file_hash_sha256: str
    code_version: str
    certificate_version: str = '1.0.0'
    certified: bool = False
    rejection_reasons: List[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if d['rejection_reasons'] is None:
            d['rejection_reasons'] = []
        return d


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 16), b''):
            h.update(chunk)
    return h.hexdigest()


def load_csv_strict(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = [c for c in CSV_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f'missing columns: {missing}')
    df = df[CSV_COLUMNS].copy()
    df['time'] = pd.to_datetime(df['time'], utc=True, errors='coerce')
    for c in ['open', 'high', 'low', 'close']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df['volume'] = pd.to_numeric(df['volume'], errors='coerce')
    return df


def certify_history(path: Path, instrument: str, timeframe: str, requested_horizon_days: int, expected_identity: Optional[str] = None) -> HistoryCertificate:
    df = load_csv_strict(path)
    reasons: List[str] = []
    malformed = int(df['time'].isna().sum() + df[['open','high','low','close','volume']].isna().any(axis=1).sum())
    invalid_ohlc = int(((df['high'] < df[['open','close','low']].max(axis=1)) | (df['low'] > df[['open','close','high']].min(axis=1))).sum())
    invalid_volume = int((df['volume'] < 0).sum())
    dup = int(df.duplicated(subset=['time']).sum())
    ordering_ok = bool(df['time'].is_monotonic_increasing)
    timestamp_unique = bool(df['time'].is_unique)
    df_sorted = df.sort_values('time').reset_index(drop=True)
    first = df_sorted['time'].iloc[0]
    last = df_sorted['time'].iloc[-1]
    if first.tzinfo is None:
        first = first.replace(tzinfo=timezone.utc)
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    actual_coverage_days = int((last - first).days + 1)
    # gap detection: > 1 day gap for 1h data or > 2 days for 15m data is suspicious; keep conservative
    gaps: List[Dict[str, Any]] = []
    deltas = df_sorted['time'].diff().dropna().dt.total_seconds()
    if not deltas.empty:
        threshold = 7200 if timeframe == '1h' else 3600
        big = deltas[deltas > threshold]
        for idx, secs in big.items():
            gaps.append({'after_index': int(idx), 'gap_seconds': float(secs)})
    provenance_complete = bool(path.exists() and path.stat().st_size > 0)
    file_hash = sha256_file(path)
    if expected_identity is not None and expected_identity != instrument:
        reasons.append('instrument_identity_mismatch')
    if malformed > 0:
        reasons.append('malformed_rows')
    if invalid_ohlc > 0:
        reasons.append('invalid_ohlc')
    if invalid_volume > 0:
        reasons.append('invalid_volume')
    if dup > 0:
        reasons.append('duplicate_rows')
    if not ordering_ok:
        reasons.append('ordering_invalid')
    if not timestamp_unique:
        reasons.append('timestamp_not_unique')
    if not provenance_complete:
        reasons.append('provenance_incomplete')
    if actual_coverage_days < requested_horizon_days:
        reasons.append('insufficient_coverage')
    certified = len(reasons) == 0
    return HistoryCertificate(
        instrument=instrument,
        timeframe=timeframe,
        requested_horizon_days=requested_horizon_days,
        actual_first_ts=first.isoformat(),
        actual_last_ts=last.isoformat(),
        actual_coverage_days=actual_coverage_days,
        candle_count=int(len(df_sorted)),
        duplicate_rows=dup,
        malformed_rows=malformed,
        invalid_ohlc_rows=invalid_ohlc,
        invalid_volume_rows=invalid_volume,
        timezone='UTC',
        timestamp_unique=timestamp_unique,
        ordering_ok=ordering_ok,
        provenance_complete=provenance_complete,
        suspicious_gaps=gaps,
        source_path=str(path),
        file_hash_sha256=file_hash,
        code_version='23I',
        certified=certified,
        rejection_reasons=reasons,
    )

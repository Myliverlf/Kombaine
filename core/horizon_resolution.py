"""23G Canonical Horizon Resolution Layer.

One canonical source of truth for horizon → data mapping.  No filename-label
trust, no silent synthetic fallback for qualification.

Horizon contract
~~~~~~~~~~~~~~~~
  60  — exact CSV  *_60d_*  (preferred) or tail-slice of *_365d_*
  90  — deterministic tail-slice of *_365d_*  (no 90d CSV exists)
  180 — deterministic tail-slice of *_365d_*  (no 180d CSV exists)
  365 — exact CSV  *_365d_*  (preferred) or tail-slice of *_1095d_*
  1095 — exact CSV  *_1095d_*  ONLY; fails closed if actual coverage < 1095

File hashing: SHA-256 of source CSV content for every resolution.
Future leakage guard: resolved data is clipped at "now" (or explicit as_of).
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ─── Constants ─────────────────────────────────────────────────────────
DEFAULT_DATA_DIR = Path(
    "/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data"
)

CSV_COLUMNS = ["time", "open", "high", "low", "close", "volume"]

VALID_HORIZONS = {60, 90, 180, 365, 1095}

# Canonical file-label → day-count mapping (filename label, NOT actual days).
_LABEL_TO_DAYS: Dict[int, int] = {
    60: 60,
    365: 365,
    1095: 1095,
}


# ─── Exceptions ────────────────────────────────────────────────────────

class HorizonResolutionError(Exception):
    """Base for all resolution failures."""


class InsufficientCoverageError(HorizonResolutionError):
    """Raised when no source file covers the requested number of calendar days."""


class NoSourceFileError(HorizonResolutionError):
    """Raised when no matching source CSV can be found at all."""


class FutureLeakageError(HorizonResolutionError):
    """Raised when resolved data contains timestamps after the as_of date."""


# ─── Data classes ──────────────────────────────────────────────────────

class ResolutionStrategy(str, Enum):
    EXACT_FILE = "exact_file"
    DETERMINISTIC_SLICE = "deterministic_slice"


@dataclass(frozen=True)
class HorizonResolution:
    """Immutable result of a horizon resolution call."""
    ticker: str
    interval: str
    requested_horizon_days: int
    strategy: ResolutionStrategy
    source_path: Path
    source_label_days: int            # filename label (e.g. 365)
    actual_start: datetime
    actual_end: datetime
    actual_coverage_days: int         # real calendar span
    slice_start: Optional[datetime] = None  # if sliced, the slice boundary
    slice_rows: Optional[int] = None
    file_hash_sha256: str = ""
    df: Optional[pd.DataFrame] = field(default=None, repr=False)

    def summary_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "interval": self.interval,
            "requested_horizon_days": self.requested_horizon_days,
            "strategy": self.strategy.value,
            "source_path": str(self.source_path),
            "source_label_days": self.source_label_days,
            "actual_start": self.actual_start.isoformat(),
            "actual_end": self.actual_end.isoformat(),
            "actual_coverage_days": self.actual_coverage_days,
            "slice_start": self.slice_start.isoformat() if self.slice_start else None,
            "slice_rows": self.slice_rows,
            "file_hash_sha256": self.file_hash_sha256,
            "returned_rows": len(self.df) if self.df is not None else 0,
        }


# ─── Internal helpers ─────────────────────────────────────────────────

def _csv_path(
    ticker: str, interval: str, data_dir: Path, label_days: int
) -> Path:
    return data_dir / f"{ticker}_{label_days}d_{interval}_continuous.csv"


def _read_csv_safe(filepath: Path) -> pd.DataFrame:
    """Read CSV, sort by time, return clean DataFrame."""
    df = pd.read_csv(filepath)
    df.columns = [c.strip() for c in df.columns]
    expected = set(CSV_COLUMNS)
    if not expected.issubset(set(df.columns)):
        raise ValueError(f"CSV {filepath} missing columns: {expected - set(df.columns)}")
    df = df[CSV_COLUMNS].copy()
    df["time"] = pd.to_datetime(df["time"], utc=True, format="mixed")
    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].astype(float)
    df["volume"] = df["volume"].astype(int)
    df.sort_values("time", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _actual_coverage(df: pd.DataFrame) -> tuple[datetime, datetime, int]:
    """Return (first_ts, last_ts, calendar_days) for a loaded DataFrame."""
    first = df["time"].iloc[0].to_pydatetime()
    last = df["time"].iloc[-1].to_pydatetime()
    # Ensure tz-aware
    if first.tzinfo is None:
        first = first.replace(tzinfo=timezone.utc)
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    coverage = (last - first).days + 1  # inclusive
    return first, last, coverage


def _clip_at_as_of(df: pd.DataFrame, as_of: Optional[datetime]) -> pd.DataFrame:
    """Remove rows with time > as_of. Raises FutureLeakageError if as_of is given
    and any rows survive beyond it (should not happen after clip, but we check)."""
    if as_of is None:
        return df
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    clipped = df[df["time"] <= as_of].copy()
    clipped.reset_index(drop=True, inplace=True)
    return clipped


# ─── Public API ────────────────────────────────────────────────────────

def resolve_horizon(
    ticker: str,
    interval: str,
    horizon_days: int,
    data_dir: Optional[Path] = None,
    as_of: Optional[datetime] = None,
) -> HorizonResolution:
    """Resolve a requested horizon to a concrete CSV + optional slice.

    This is the single canonical entry point for all horizon/data needs.

    Parameters
    ----------
    ticker : str
        Ticker symbol (e.g. "SBER", "GAZP").
    interval : str
        "15m" or "1h".
    horizon_days : int
        Requested coverage in calendar days. Must be in VALID_HORIZONS.
    data_dir : Path, optional
        Directory containing CSV files. Defaults to DEFAULT_DATA_DIR.
    as_of : datetime, optional
        Clip resolved data at this timestamp (future-leakage guard).

    Returns
    -------
    HorizonResolution
        Immutable result with df, metadata, and coverage info.

    Raises
    ------
    ValueError
        If horizon_days not in VALID_HORIZONS or interval invalid.
    InsufficientCoverageError
        If no source file provides enough actual coverage.
    NoSourceFileError
        If no source CSV file exists at all.
    FutureLeakageError
        If resolved data would contain future timestamps.
    """
    ticker = ticker.upper()

    if horizon_days not in VALID_HORIZONS:
        raise ValueError(
            f"horizon_days={horizon_days} not in VALID_HORIZONS={sorted(VALID_HORIZONS)}"
        )
    if interval not in ("15m", "1h"):
        raise ValueError(f"interval={interval!r} not valid; must be '15m' or '1h'")

    _dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR

    # ── Strategy selection ──────────────────────────────────────────
    # 1095: exact file ONLY, no slicing allowed
    if horizon_days == 1095:
        return _resolve_exact_1095(ticker, interval, _dir, as_of)

    # 60: prefer exact 60d, fall back to tail-slice of 365d
    if horizon_days == 60:
        return _resolve_exact_or_slice(
            ticker, interval, horizon_days, _dir, as_of,
            exact_label=60, slice_from_label=365,
        )

    # 365: prefer exact 365d, fall back to tail-slice of 1095d
    if horizon_days == 365:
        return _resolve_exact_or_slice(
            ticker, interval, horizon_days, _dir, as_of,
            exact_label=365, slice_from_label=1095,
        )

    # 90 / 180: deterministic tail-slice of 365d (no exact file expected)
    if horizon_days in (90, 180):
        return _resolve_deterministic_slice(
            ticker, interval, horizon_days, _dir, as_of,
            slice_from_label=365,
        )

    raise HorizonResolutionError(f"Unhandled horizon_days={horizon_days}")


# ─── Internal resolvers ────────────────────────────────────────────────

def _resolve_exact_1095(
    ticker: str, interval: str, data_dir: Path, as_of: Optional[datetime]
) -> HorizonResolution:
    """1095 horizon: exact file ONLY. Fails closed if actual < 1095 days."""
    path = _csv_path(ticker, interval, data_dir, 1095)
    if not path.exists():
        raise NoSourceFileError(
            f"No 1095d CSV found for {ticker}/{interval}: {path}"
        )

    df = _read_csv_safe(path)
    first, last, actual_days = _actual_coverage(df)
    file_hash = _sha256_file(path)

    if actual_days < 1095:
        raise InsufficientCoverageError(
            f"1095d horizon for {ticker}: source file {path.name} has actual "
            f"coverage {actual_days} days ({first.date()} → {last.date()}), "
            f"required 1095. FAIL CLOSED — no fallback."
        )

    df = _clip_at_as_of(df, as_of)

    return HorizonResolution(
        ticker=ticker,
        interval=interval,
        requested_horizon_days=1095,
        strategy=ResolutionStrategy.EXACT_FILE,
        source_path=path,
        source_label_days=1095,
        actual_start=first,
        actual_end=last,
        actual_coverage_days=actual_days,
        file_hash_sha256=file_hash,
        df=df,
    )


def _resolve_exact_or_slice(
    ticker: str,
    interval: str,
    horizon_days: int,
    data_dir: Path,
    as_of: Optional[datetime],
    exact_label: int,
    slice_from_label: int,
) -> HorizonResolution:
    """Try exact file first; if absent, try tail-slice from a longer file."""
    # ── Try exact ──
    exact_path = _csv_path(ticker, interval, data_dir, exact_label)
    if exact_path.exists():
        df = _read_csv_safe(exact_path)
        first, last, actual_days = _actual_coverage(df)
        file_hash = _sha256_file(exact_path)
        df = _clip_at_as_of(df, as_of)

        return HorizonResolution(
            ticker=ticker,
            interval=interval,
            requested_horizon_days=horizon_days,
            strategy=ResolutionStrategy.EXACT_FILE,
            source_path=exact_path,
            source_label_days=exact_label,
            actual_start=first,
            actual_end=last,
            actual_coverage_days=actual_days,
            file_hash_sha256=file_hash,
            df=df,
        )

    # ── Try slice from longer file ──
    slice_path = _csv_path(ticker, interval, data_dir, slice_from_label)
    if slice_path.exists():
        return _make_slice(
            ticker, interval, horizon_days, slice_path, slice_from_label, as_of
        )

    raise NoSourceFileError(
        f"No exact {exact_label}d CSV and no {slice_from_label}d CSV found "
        f"for {ticker}/{interval}."
    )


def _resolve_deterministic_slice(
    ticker: str,
    interval: str,
    horizon_days: int,
    data_dir: Path,
    as_of: Optional[datetime],
    slice_from_label: int,
) -> HorizonResolution:
    """Deterministic tail-slice from a longer certified file."""
    slice_path = _csv_path(ticker, interval, data_dir, slice_from_label)
    if not slice_path.exists():
        raise NoSourceFileError(
            f"No {slice_from_label}d CSV found for {ticker}/{interval} "
            f"(needed for {horizon_days}d slice): {slice_path}"
        )
    return _make_slice(
        ticker, interval, horizon_days, slice_path, slice_from_label, as_of
    )


def _make_slice(
    ticker: str,
    interval: str,
    horizon_days: int,
    source_path: Path,
    source_label_days: int,
    as_of: Optional[datetime],
) -> HorizonResolution:
    """Build a HorizonResolution by slicing the last `horizon_days` from source."""
    df = _read_csv_safe(source_path)
    first, last, actual_days = _actual_coverage(df)
    file_hash = _sha256_file(source_path)

    # Determine slice boundary
    if as_of is not None:
        end_point = as_of
        if end_point.tzinfo is None:
            end_point = end_point.replace(tzinfo=timezone.utc)
    else:
        end_point = last

    slice_start = end_point - timedelta(days=horizon_days)

    sliced = df[(df["time"] >= slice_start) & (df["time"] <= end_point)].copy()
    sliced.reset_index(drop=True, inplace=True)

    if len(sliced) == 0:
        raise NoSourceFileError(
            f"Slice produced 0 rows for {ticker} {horizon_days}d from "
            f"{source_path.name} (slice_start={slice_start}, end={end_point})"
        )

    return HorizonResolution(
        ticker=ticker,
        interval=interval,
        requested_horizon_days=horizon_days,
        strategy=ResolutionStrategy.DETERMINISTIC_SLICE,
        source_path=source_path,
        source_label_days=source_label_days,
        actual_start=first,
        actual_end=last,
        actual_coverage_days=actual_days,
        slice_start=slice_start,
        slice_rows=len(sliced),
        file_hash_sha256=file_hash,
        df=sliced,
    )


# ─── Convenience: batch resolve ────────────────────────────────────────

def resolve_universe(
    tickers: List[str],
    interval: str,
    horizon_days: int,
    data_dir: Optional[Path] = None,
    as_of: Optional[datetime] = None,
) -> Dict[str, HorizonResolution]:
    """Resolve horizon for multiple tickers. Returns dict ticker → resolution.
    Tickers that fail are logged but excluded from result."""
    results: Dict[str, HorizonResolution] = {}
    for t in tickers:
        try:
            results[t] = resolve_horizon(
                t, interval, horizon_days, data_dir, as_of
            )
        except HorizonResolutionError as e:
            logger.warning("[horizon_resolution] SKIP %s: %s", t, e)
    return results

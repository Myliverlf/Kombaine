"""Data Loader — единая точка входа для OHLCV-данных в strategy_combine.

Iteration 23H: Migrated to canonical horizon resolver (core/horizon_resolution.py).
All production data loading now flows through the canonical resolver.
Synthetic fallback is CLEARLY DEGRADED and NOT qualification-grade.

Interface:
    load_ohlcv(ticker, interval="15m", data_dir=None, excluded=None) -> pd.DataFrame
    load_ohlcv_horizon(ticker, interval, data_dir, excluded, horizon_days) -> pd.DataFrame

Format CSV:
    time,open,high,low,close,volume

Live orders запрещены. Модуль чистый: пишет/читает файлы, не ходит в сеть.
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

# ─── Canonical horizon resolver (23H migration) ──────────────────────

try:
    from horizon_resolution import (
        resolve_horizon,
        InsufficientCoverageError,
        NoSourceFileError,
        HorizonResolutionError,
        VALID_HORIZONS,
    )
    _CANONICAL_RESOLVER_AVAILABLE = True
except ImportError:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
        from horizon_resolution import (
            resolve_horizon,
            InsufficientCoverageError,
            NoSourceFileError,
            HorizonResolutionError,
            VALID_HORIZONS,
        )
        _CANONICAL_RESOLVER_AVAILABLE = True
    except ImportError:
        _CANONICAL_RESOLVER_AVAILABLE = False

# ─── Default data directory ────────────────────────────────────────────

DEFAULT_DATA_DIR = Path(
    "/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data"
)

# ─── Excluded tickers (immutable default) ─────────────────────────────

DEFAULT_EXCLUDED: List[str] = ["RI"]

# ─── Valid intervals ──────────────────────────────────────────────────

VALID_INTERVALS = {"15m", "1h"}

# ─── CSV columns ──────────────────────────────────────────────────────

CSV_COLUMNS = ["time", "open", "high", "low", "close", "volume"]


def _ticker_hash_seed(ticker: str) -> int:
    """Deterministic seed from ticker name for synthetic data reproducibility."""
    h = hashlib.sha256(ticker.encode()).hexdigest()
    return int(h[:8], 16)


def _build_csv_path(
    ticker: str, interval: str, data_dir: Path, days: int = 60
) -> Path:
    """Build CSV file path: {data_dir}/{TICKER}_{days}d_{interval}_continuous.csv"""
    return data_dir / f"{ticker}_{days}d_{interval}_continuous.csv"


def _candidate_csv_paths(ticker: str, interval: str, data_dir: Path, days: int) -> list[Path]:
    """LEGACY: Prefer exact horizon, then fall back to shorter/longer available windows.

    DEPRECATED for production use — canonical resolver (resolve_horizon) is authoritative.
    Kept for backward compatibility in non-qualification contexts only.
    """
    order = [days]
    for alt in (60, 365, 1095):
        if alt not in order:
            order.append(alt)
    return [_build_csv_path(ticker, interval, data_dir, d) for d in order]


def _generate_synthetic_ohlcv(
    ticker: str, n_rows: int = 200, interval: str = "15m"
) -> pd.DataFrame:
    """Generate synthetic OHLCV DataFrame via geometric random walk.

    Seed is deterministic per ticker for reproducibility.
    Returns DataFrame with columns: time, open, high, low, close, volume.

    NOTE: Synthetic data is NOT qualification-grade. It exists only for
    development/testing. Any qualification decision must use real data.
    """
    seed = _ticker_hash_seed(ticker)
    rng = random.Random(seed)

    base_price = 100.0 + (seed % 500)
    prices: List[float] = [base_price]
    for _ in range(n_rows - 1):
        ret = rng.gauss(0.0, 0.005)  # 0.5% std
        prices.append(prices[-1] * (1.0 + ret))

    # Build OHLCV rows
    interval_minutes = 15 if interval == "15m" else 60
    base_ts = 1719000000  # ~2024-06-21 epoch

    rows: List[Dict[str, Any]] = []
    for i, c in enumerate(prices):
        noise = lambda: rng.uniform(0.001, 0.01)
        o = c * (1.0 - noise())
        h = c * (1.0 + noise())
        l = c * (1.0 - 2 * noise())
        volume = max(1, int(rng.gauss(500, 200)))

        ts_epoch = base_ts + i * interval_minutes * 60
        ts_str = pd.Timestamp(ts_epoch, unit="s", tz="UTC").strftime(
            "%Y-%m-%d %H:%M:%S+00:00"
        )

        rows.append({
            "time": ts_str,
            "open": round(o, 2),
            "high": round(max(o, h, c), 2),
            "low": round(min(o, l, c), 2),
            "close": round(c, 2),
            "volume": volume,
        })

    df = pd.DataFrame(rows, columns=CSV_COLUMNS)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].astype(float)
    df["volume"] = df["volume"].astype(int)
    return df


def _read_csv(filepath: Path) -> pd.DataFrame:
    """Read CSV with OHLCV data. Handles both +00:00 and Z timezone formats."""
    df = pd.read_csv(filepath)
    expected = set(CSV_COLUMNS)
    actual = set(c.strip() for c in df.columns)
    if not expected.issubset(actual):
        missing = expected - actual
        raise ValueError(
            f"CSV {filepath} missing columns: {missing}. "
            f"Found: {actual}"
        )

    # Normalize column names (strip whitespace)
    df.columns = [c.strip() for c in df.columns]

    # Parse timestamps: handles both "+00:00" and "Z" formats
    df["time"] = pd.to_datetime(df["time"], utc=True, format="mixed")
    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].astype(float)
    df["volume"] = df["volume"].astype(int)

    return df[CSV_COLUMNS].sort_values("time").reset_index(drop=True)


# ─── Public API ───────────────────────────────────────────────────────

def load_ohlcv(
    ticker: str,
    interval: str = "15m",
    data_dir: Optional[Path] = None,
    excluded: Optional[List[str]] = None,
    fallback_rows: int = 200,
) -> pd.DataFrame:
    """Load OHLCV DataFrame for a ticker.

    1. If ticker is in excluded list -> raises ValueError.
    2. Try to read CSV via canonical horizon resolver (60d default).
    3. If canonical resolver fails -> generate synthetic OHLCV fallback.

    NOTE (23H): Synthetic fallback is NOT qualification-grade.
    Qualification decisions must use real data through the canonical resolver.

    Args:
        ticker: Ticker symbol (e.g. "SBER", "LKOH").
        interval: "15m" or "1h".
        data_dir: Directory with CSV files. Defaults to DEFAULT_DATA_DIR.
        excluded: List of excluded tickers. Defaults to DEFAULT_EXCLUDED.
        fallback_rows: Number of rows for synthetic fallback.

    Returns:
        pd.DataFrame with columns: time, open, high, low, close, volume.
        Sorted by time ascending.

    Raises:
        ValueError: If ticker is in excluded list or interval is invalid.
    """
    return load_ohlcv_horizon(
        ticker=ticker,
        interval=interval,
        data_dir=data_dir,
        excluded=excluded,
        horizon_days=60,
        fallback_rows=fallback_rows,
    )


def load_ohlcv_horizon(
    ticker: str,
    interval: str = "15m",
    data_dir: Optional[Path] = None,
    excluded: Optional[List[str]] = None,
    horizon_days: int = 60,
    fallback_rows: int = 200,
) -> pd.DataFrame:
    """Load OHLCV for a ticker at a specific horizon.

    Iteration 23H: Uses canonical horizon resolver as primary path.
    Falls back to legacy CSV search, then synthetic as degraded mode.

    Returns DataFrame with attrs:
        source: "real" | "synthetic"
        ticker: str
        interval: str
        horizon_days: int
        requested_horizon_days: int (from canonical resolver)
        actual_coverage_days: int (from canonical resolver)
        resolution_strategy: str (from canonical resolver)
        file_hash: str (from canonical resolver)
    """
    if interval not in VALID_INTERVALS:
        raise ValueError(
            f"Invalid interval '{interval}'. Must be one of: {VALID_INTERVALS}"
        )

    exc = excluded if excluded is not None else DEFAULT_EXCLUDED
    if ticker.upper() in [e.upper() for e in exc]:
        raise ValueError(
            f"Ticker '{ticker}' is in excluded list: {exc}. Will not load data."
        )

    _dir = Path(data_dir) if data_dir is not None else DEFAULT_DATA_DIR

    # ── PRIMARY: Canonical horizon resolver (23H) ──
    if _CANONICAL_RESOLVER_AVAILABLE and horizon_days in VALID_HORIZONS:
        try:
            resolution = resolve_horizon(
                ticker=ticker.upper(),
                interval=interval,
                horizon_days=horizon_days,
                data_dir=_dir,
            )
            df = resolution.df.copy()
            df.attrs["source"] = "real"
            df.attrs["ticker"] = ticker.upper()
            df.attrs["interval"] = interval
            df.attrs["horizon_days"] = horizon_days
            df.attrs["requested_horizon_days"] = resolution.requested_horizon_days
            df.attrs["actual_coverage_days"] = resolution.actual_coverage_days
            df.attrs["resolution_strategy"] = resolution.strategy.value
            df.attrs["file_hash"] = resolution.file_hash_sha256
            return df
        except (InsufficientCoverageError, NoSourceFileError) as e:
            logging.warning(
                "[data_loader] Canonical resolver: %s: %s — falling back to degraded mode",
                type(e).__name__, e,
            )
        except HorizonResolutionError as e:
            logging.warning(
                "[data_loader] Canonical resolver error: %s — falling back to degraded mode",
                e,
            )

    # ── DEGRADED: Legacy CSV search (backward compatibility) ──
    logging.warning(
        "[data_loader] DEGRADED MODE for %s horizon=%d — using legacy CSV search",
        ticker.upper(), horizon_days,
    )
    for csv_path in _candidate_csv_paths(ticker.upper(), interval, _dir, horizon_days):
        if csv_path.exists() and csv_path.is_file():
            try:
                df = _read_csv(csv_path)
                df.attrs["source"] = "real_legacy"
                df.attrs["ticker"] = ticker.upper()
                df.attrs["interval"] = interval
                df.attrs["horizon_days"] = horizon_days
                df.attrs["resolution_strategy"] = "legacy_csv_search"
                df.attrs["actual_coverage_days"] = (
                    (df["time"].iloc[-1] - df["time"].iloc[0]).days + 1
                    if len(df) > 0 else 0
                )
                return df
            except Exception as exc:
                logging.warning(
                    "[data_loader] CSV read error for %s: %s — trying next candidate",
                    ticker.upper(), exc,
                )

    # ── DEGRADED: Synthetic fallback (NOT qualification-grade) ──
    logging.warning(
        "[data_loader] SYNTHETIC FALLBACK for %s — NOT qualification-grade",
        ticker.upper(),
    )
    df = _generate_synthetic_ohlcv(ticker.upper(), n_rows=fallback_rows, interval=interval)
    df.attrs["source"] = "synthetic"
    df.attrs["ticker"] = ticker.upper()
    df.attrs["interval"] = interval
    df.attrs["horizon_days"] = horizon_days
    df.attrs["resolution_strategy"] = "synthetic_fallback"
    return df


def load_universe(
    tickers: List[str],
    interval: str = "15m",
    data_dir: Optional[Path] = None,
    excluded: Optional[List[str]] = None,
    fallback_rows: int = 200,
) -> Dict[str, pd.DataFrame]:
    """Load OHLCV for multiple tickers, skipping excluded.

    Returns
    Dict mapping ticker -> DataFrame. Excluded tickers are silently
    skipped (logged via print). Failed loads fall back to synthetic.
    """
    exc = excluded if excluded is not None else DEFAULT_EXCLUDED
    result: Dict[str, pd.DataFrame] = {}

    for ticker in tickers:
        try:
            df = load_ohlcv(
                ticker, interval, data_dir, excluded, fallback_rows
            )
            result[ticker] = df
        except ValueError as e:
            print(f"[data_loader] SKIP {ticker}: {e}")

    return result

"""Canonical horizon resolution with explicit history certification.

Iteration 23I: 1095d pass requires certified actual coverage >= requested horizon.
Nominal filename is never treated as proof.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Optional

from history_certification import certify_history
from horizon_resolution import HorizonResolution, InsufficientCoverageError, resolve_horizon as _resolve_horizon


def resolve_horizon(ticker: str, interval: str, horizon_days: int, data_dir: Optional[Path] = None, as_of=None) -> HorizonResolution:
    res = _resolve_horizon(ticker, interval, horizon_days, data_dir=data_dir, as_of=as_of)
    # Certification is required for 1095d; for shorter horizons we retain existing semantics.
    if horizon_days == 1095:
        cert = certify_history(res.source_path, ticker, interval, horizon_days)
        if not cert.certified:
            raise InsufficientCoverageError(f"Certified history insufficient for {ticker} {interval} {horizon_days}d: {cert.rejection_reasons}")
        # attach provenance-like markers in a non-breaking way
        res = replace(res, actual_coverage_days=max(res.actual_coverage_days, cert.actual_coverage_days))
    return res

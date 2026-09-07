"""Layered unit tests for the 24I-R backfill engine primitives.

Pure tests — no network, no provider, no pytest fixtures with side effects.
These must pass even when the full regression pytest process is unstable.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backfill_utils import backward_windows, merge_dedupe, suspicious_gaps


END = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
FLOOR = datetime(2023, 8, 31, 12, 0, tzinfo=timezone.utc)


def test_backward_windows_deterministic():
    a = backward_windows(END, chunk_days=55, overlap_days=1, floor=FLOOR)
    b = backward_windows(END, chunk_days=55, overlap_days=1, floor=FLOOR)
    assert a == b
    assert len(a) > 0


def test_backward_windows_chain_semantics():
    w = backward_windows(END, chunk_days=55, overlap_days=1, floor=FLOOR)
    assert w[0][1] == END, 'first window must end at anchor'
    assert w[-1][0] == FLOOR, 'last window must start at floor'
    for newer, older in zip(w, w[1:]):
        assert older[1] == newer[0] + timedelta(days=1), 'adjacent windows must overlap by overlap_days'
        assert older[0] <= older[1]


def test_backward_windows_rejects_bad_overlap():
    import pytest
    with pytest.raises(ValueError):
        backward_windows(END, chunk_days=10, overlap_days=10, floor=FLOOR)


def test_backward_windows_floor_respected():
    w = backward_windows(END, chunk_days=55, overlap_days=1, floor=FLOOR)
    for fr, to in w:
        assert fr >= FLOOR


def test_merge_dedupe_identical_overlap():
    existing = [{'time': '2026-01-02T09:00:00+00:00', 'open': 1.0, 'high': 2.0, 'low': 0.5, 'close': 1.5, 'volume': 10}]
    new = [
        {'time': '2026-01-02T09:00:00+00:00', 'open': 1.0, 'high': 2.0, 'low': 0.5, 'close': 1.5, 'volume': 10},
        {'time': '2026-01-02T08:00:00+00:00', 'open': 1.0, 'high': 1.2, 'low': 0.9, 'close': 1.1, 'volume': 5},
    ]
    merged, identical, conflicts = merge_dedupe(existing, new)
    assert identical == 1
    assert conflicts == []
    assert len(merged) == 2
    assert merged[0]['time'] < merged[1]['time']


def test_merge_dedupe_conflict_detected_never_silent():
    existing = [{'time': '2026-01-02T09:00:00+00:00', 'open': 1.0, 'high': 2.0, 'low': 0.5, 'close': 1.5, 'volume': 10}]
    new = [{'time': '2026-01-02T09:00:00+00:00', 'open': 9.9, 'high': 9.9, 'low': 9.9, 'close': 9.9, 'volume': 1}]
    merged, identical, conflicts = merge_dedupe(existing, new)
    assert identical == 0
    assert len(conflicts) == 1
    assert conflicts[0]['existing']['open'] == 1.0
    assert conflicts[0]['incoming']['open'] == 9.9
    # existing record must be kept, not silently replaced
    assert len(merged) == 1
    assert merged[0]['open'] == 1.0


def test_merge_dedupe_empty_inputs():
    merged, identical, conflicts = merge_dedupe([], [])
    assert merged == [] and identical == 0 and conflicts == []


def test_suspicious_gaps_flags_holes_not_weekends():
    # 66h weekend gap must NOT be flagged (default threshold 240h)
    weekend = [
        {'time': datetime(2026, 1, 2, 18, 0, tzinfo=timezone.utc).isoformat()},
        {'time': datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc).isoformat()},
    ]
    assert suspicious_gaps(weekend, '1h') == []
    # 14-day hole must be flagged
    hole = [
        {'time': datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat()},
        {'time': datetime(2026, 1, 15, tzinfo=timezone.utc).isoformat()},
    ]
    gaps = suspicious_gaps(hole, '1h')
    assert len(gaps) == 1
    assert gaps[0]['gap_hours'] > 240


def test_suspicious_gaps_zero_or_negative_step():
    rows = [
        {'time': datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc).isoformat()},
        {'time': datetime(2026, 1, 2, 9, 0, tzinfo=timezone.utc).isoformat()},
    ]
    gaps = suspicious_gaps(rows, '15m')
    assert len(gaps) == 1
    assert gaps[0]['issue'] == 'non_monotonic_or_zero_step'

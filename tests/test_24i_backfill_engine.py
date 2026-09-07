from pathlib import Path

from backfill_utils import build_windows, quality_check


def test_window_generation_is_deterministic():
    from datetime import datetime, timezone
    end = datetime(2026, 8, 31, 13, 0, tzinfo=timezone.utc)
    windows_a = build_windows(end, target_days=1095, chunk_days=60)
    windows_b = build_windows(end, target_days=1095, chunk_days=60)
    assert windows_a == windows_b
    assert windows_a[0][1] <= end


def test_quality_check_detects_ohlc_integrity():
    import pandas as pd
    good = pd.DataFrame({'time': pd.to_datetime(['2026-08-31T13:00:00Z']), 'open': [1.0], 'high': [2.0], 'low': [0.5], 'close': [1.5], 'volume': [1]})
    bad = pd.DataFrame({'time': pd.to_datetime(['2026-08-31T13:00:00Z']), 'open': [1.0], 'high': [0.4], 'low': [0.5], 'close': [1.5], 'volume': [1]})
    assert quality_check(good)[0] == 'PASS'
    assert quality_check(bad)[0] == 'FAIL'

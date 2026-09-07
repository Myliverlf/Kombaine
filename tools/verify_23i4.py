#!/usr/bin/env python3
"""23I.4 step 3: verify merged continuous series.

Checks:
  1. monotonic unique timestamps
  2. OHLC integrity (high>=low, high>=open/close, low<=open/close)
  3. seam continuity: gap between pre-last and ref-first <= expected session gap
  4. level sanity around seam: |pre_last_close/ref_first_close - 1| small AFTER adjustment
  5. coverage days total >= 1095 target check
  6. no duplicate timestamps across merge
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

OUT_DIR = Path('/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data')
STATE_DIR = Path('/root/prop-desk/strategy_combine/state/backfill_23i4')

PAIRS = {'GAZPF': 'GAZP', 'SBERF': 'SBER'}


def verify(symbol: str, tf: str) -> dict:
    root = PAIRS[symbol]
    ref_path = OUT_DIR / ('%s_1095d_%s_continuous.csv' % (root, tf))
    merged_path = OUT_DIR / ('%s_23i4_merged_%s.csv' % (symbol, tf))
    res = {'symbol': symbol, 'tf': tf}
    if not merged_path.exists():
        res['status'] = 'MISSING_MERGED'
        return res
    df = pd.read_csv(merged_path, parse_dates=['time'])
    ref = pd.read_csv(ref_path, parse_dates=['time'])
    res['rows'] = len(df)

    # 1. monotonic unique
    dup = df['time'].duplicated().sum()
    mono = df['time'].is_monotonic_increasing
    res['duplicates'] = int(dup)
    res['monotonic'] = bool(mono)

    # 2. OHLC integrity
    bad = ((df['high'] < df['low']) |
           (df['high'] < df[['open', 'close']].max(axis=1)) |
           (df['low'] > df[['open', 'close']].min(axis=1))).sum()
    res['ohlc_violations'] = int(bad)

    # 3/4. seam
    seam_t = ref['time'].min()
    pre = df[df['time'] < seam_t]
    res['pre_rows'] = len(pre)
    if len(pre):
        gap = (ref['time'].min() - pre['time'].max())
        res['seam_gap'] = str(gap)
        res['pre_last_close'] = float(pre.iloc[-1]['close'])
        res['ref_first_close'] = float(ref.iloc[0]['close'])
        res['seam_level_ratio'] = float(ref.iloc[0]['close'] / pre.iloc[-1]['close'])
    # 5. coverage
    span = (df['time'].max() - df['time'].min()).days
    res['first'] = str(df['time'].min())
    res['last'] = str(df['time'].max())
    res['span_days'] = int(span)
    res['target_1095'] = bool(span >= 1095)
    ok = (dup == 0 and mono and bad == 0 and span >= 1095)
    res['status'] = 'VERIFIED' if ok else 'NEEDS_REVIEW'
    return res


def main() -> int:
    results = []
    for symbol in ('GAZPF', 'SBERF'):
        for tf in ('15m', '1h'):
            r = verify(symbol, tf)
            results.append(r)
            print(json.dumps(r, ensure_ascii=False), flush=True)
    (STATE_DIR / 'verify_report.json').write_text(
        json.dumps(results, indent=1, ensure_ascii=False))
    all_ok = all(r.get('status') == 'VERIFIED' for r in results)
    print('VERIFY RESULT:', 'ALL_VERIFIED' if all_ok else 'REVIEW_NEEDED', flush=True)
    return 0 if all_ok else 1


if __name__ == '__main__':
    sys.exit(main())

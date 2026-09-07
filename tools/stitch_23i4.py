#!/usr/bin/env python3
"""23I.4 step 2: stitch MOEX 1m candles into continuous 15m/1h series.

Reproduces futures_lab.download_continuous semantics:
  - contracts ordered by expiry
  - each back contract truncated at (last_trade_date - roll_days)
  - multiplicative back-adjustment: factor = prev_last_close / first_close
  - dedupe by timestamp
  - roll_days default = 5

Then normalizes to canonical file convention (UTC, session start 07:00 UTC).
Read-only on canonical files; writes *_23i4_moex_extended_*.csv next to them.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

RAW_DIR = Path('/root/prop-desk/strategy_combine/artifacts/history_raw/moex_23i4')
OUT_DIR = Path('/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data')
STATE_DIR = Path('/root/prop-desk/strategy_combine/state/backfill_23i4')
STATE_DIR.mkdir(parents=True, exist_ok=True)

ROLL_DAYS = 5

CHAINS = {
    'GAZPF': ['GZM3', 'GZU3', 'GZZ3', 'GZH4', 'GZM4', 'GZU4', 'GZZ4'],
    'SBERF': ['SRM3', 'SRU3', 'SRZ3', 'SRH4', 'SRM4', 'SRU4', 'SRZ4'],
}

# ISS 'begin' labels are MSK (UTC+3). Session: main 10:00-18:45 MSK,
# evening 19:05-23:50 MSK. Canonical file marks bars in UTC with session
# starting 07:00 UTC (=10:00 MSK). Evening bars 19:05-23:50 MSK -> 16:05-20:50 UTC.
MSK = timezone(timedelta(hours=3))


def load_contract_1m(secid: str) -> pd.DataFrame:
    raw = RAW_DIR / ('%s_1m.json' % secid)
    if not raw.exists():
        return pd.DataFrame()
    rows = json.loads(raw.read_text())
    if not rows:
        return pd.DataFrame()
    # ISS candle row: [open, close, high, low, value, volume, begin, end]
    recs = []
    for r in rows:
        o, c, h, l, _val, v, begin, _end = r[:8]
        # begin label is MSK-local; interpret and convert to UTC
        t_msk = datetime.strptime(begin, '%Y-%m-%d %H:%M:%S').replace(tzinfo=MSK)
        recs.append({'time': t_msk.astimezone(timezone.utc),
                     'open': float(o), 'high': float(h),
                     'low': float(l), 'close': float(c),
                     'volume': int(v)})
    df = pd.DataFrame(recs).sort_values('time').drop_duplicates('time')
    return df.reset_index(drop=True)


def resample_bars(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Resample 1m -> 15m or 1h, labeling bar by its start (canonical style)."""
    if df.empty:
        return df
    d = df.set_index('time')
    rule = '15min' if tf == '15m' else '1h'
    agg = d.resample(rule, label='left', closed='left').agg(
        open=('open', 'first'), high=('high', 'max'),
        low=('low', 'min'), close=('close', 'last'),
        volume=('volume', 'sum')).dropna(subset=['open'])
    return agg.reset_index()


def contract_expiry(secid: str):
    meta = json.loads((RAW_DIR / ('%s_meta.json' % secid)).read_text())
    lt = meta.get('last_trade')
    if not lt:
        return None
    # LSTTRADE like '2024-09-19' (or with time)
    return datetime.strptime(lt[:10], '%Y-%m-%d').replace(tzinfo=timezone.utc)


def stitch(symbol: str, tf: str) -> pd.DataFrame:
    """Continuous series for symbol/timeframe using roll_days multiplicative rule."""
    segments = []
    prev_last_close = None
    last_time = None
    for secid in CHAINS[symbol]:
        raw1m = load_contract_1m(secid)
        if raw1m.empty:
            continue
        seg = resample_bars(raw1m, tf)
        if seg.empty:
            continue
        cutoff = contract_expiry(secid)
        if cutoff is not None:
            cut = cutoff - timedelta(days=ROLL_DAYS)
            seg = seg[seg['time'] <= cut]
        if seg.empty:
            continue
        if prev_last_close is not None:
            first_close = float(seg.iloc[0]['close'])
            if first_close:
                factor = prev_last_close / first_close
                for col in ('open', 'high', 'low', 'close'):
                    seg[col] = seg[col] * factor
        if last_time is not None:
            seg = seg[seg['time'] > last_time]
        if seg.empty:
            continue
        prev_last_close = float(seg.iloc[-1]['close'])
        last_time = seg.iloc[-1]['time']
        segments.append(seg)
    if not segments:
        return pd.DataFrame()
    out = pd.concat(segments, ignore_index=True)
    return out.sort_values('time').drop_duplicates('time').reset_index(drop=True)


def restrict_canonical_window(df: pd.DataFrame, ref: pd.DataFrame) -> pd.DataFrame:
    """Keep only bars whose UTC time-of-day pattern exists in canonical file."""
    if ref.empty:
        return df
    allowed_hm = set(ref['time'].dt.strftime('%H:%M'))
    mask = df['time'].dt.strftime('%H:%M').isin(allowed_hm)
    return df[mask].reset_index(drop=True)


def main() -> int:
    report = {}
    for symbol in CHAINS:
        for tf in ('15m', '1h'):
            ref_root = 'GAZP' if symbol == 'GAZPF' else 'SBER'
            ref_path = OUT_DIR / ('%s_1095d_%s_continuous.csv' % (ref_root, tf))
            ref = pd.DataFrame()
            if ref_path.exists():
                ref = pd.read_csv(ref_path, parse_dates=['time'])
            ext = stitch(symbol, tf)
            if ext.empty:
                print('%s %s: EMPTY' % (symbol, tf), flush=True)
                continue
            ext = restrict_canonical_window(ext, ref)
            # split: extended covers BEFORE canonical start
            seam = ref['time'].min() if not ref.empty else ext['time'].max()
            pre = ext[ext['time'] < seam]
            # multiplicative seam adjustment: align pre-segment level to canonical
            # (same rule as download_continuous: factor = prev_last_close/first_close)
            seam_factor = 1.0
            if len(pre) and not ref.empty:
                ref_first_close = float(ref.iloc[0]['close'])
                pre_last_close = float(pre.iloc[-1]['close'])
                if pre_last_close:
                    seam_factor = ref_first_close / pre_last_close
                    for col in ('open', 'high', 'low', 'close'):
                        pre[col] = pre[col] * seam_factor
            out_path = OUT_DIR / ('%s_23i4_moex_pre2024_%s.csv' % (symbol, tf))
            pre.to_csv(out_path, index=False,
                       columns=['time', 'open', 'high', 'low', 'close', 'volume'])
            full = pd.concat([pre, ref], ignore_index=True)
            full = full.sort_values('time').drop_duplicates('time')
            merged_path = OUT_DIR / ('%s_23i4_merged_%s.csv' % (symbol, tf))
            full.to_csv(merged_path, index=False,
                        columns=['time', 'open', 'high', 'low', 'close', 'volume'])
            span = (full['time'].max() - full['time'].min()).days
            report['%s_%s' % (symbol, tf)] = {
                'pre_rows': len(pre),
                'ref_rows': len(ref),
                'merged_rows': len(full),
                'merged_span_days': span,
                'pre_first': str(pre['time'].min()) if len(pre) else None,
                'pre_last': str(pre['time'].max()) if len(pre) else None,
                'seam': str(seam),
            }
            print('%s %s: pre=%d ref=%d merged=%d span=%dd [%s..%s]' % (
                symbol, tf, len(pre), len(ref), len(full), span,
                full['time'].min(), full['time'].max()), flush=True)
    (STATE_DIR / 'stitch_report.json').write_text(
        json.dumps(report, indent=1, ensure_ascii=False))
    print('STITCH DONE', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())

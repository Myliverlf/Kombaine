#!/usr/bin/env python3
"""23I.4 step 1: fetch MOEX ISS 1m candles for historical contract chains.

Read-only. Window: enough to reach >=1095d back from canonical end 2026-08-28
(i.e. back to mid-2023). Caches per-contract JSON for resume.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ISS = ('http://iss.moex.com/iss/engines/futures/markets/forts/'
       'securities/{sec}/candles.json')
DESC = 'http://iss.moex.com/iss/securities/{sec}.json?iss.meta=off'
UA = {'User-Agent': 'Mozilla/5.0 (23i4-fetcher)'}

RAW_DIR = Path('/root/prop-desk/strategy_combine/artifacts/history_raw/moex_23i4')
RAW_DIR.mkdir(parents=True, exist_ok=True)

WINDOW_START = '2023-05-15'
WINDOW_END = '2024-10-20'

CHAINS = {
    'GAZPF': ['GZM3', 'GZU3', 'GZZ3', 'GZH4', 'GZM4', 'GZU4', 'GZZ4'],
    'SBERF': ['SRM3', 'SRU3', 'SRZ3', 'SRH4', 'SRM4', 'SRU4', 'SRZ4'],
}


def get(url: str, tries: int = 4):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 + i * 1.5)
    raise RuntimeError('GET failed %s: %s' % (url[:100], last))


def contract_meta(secid: str) -> dict:
    d = get(DESC.format(sec=secid))
    rows = d.get('description', {}).get('data') or []
    kv = {r[0]: r[2] for r in rows}
    return {
        'secid': secid,
        'name': kv.get('NAME'),
        'lot_size': int(kv.get('LOTSIZE') or 100),
        'first_trade': kv.get('FRSTTRADE'),
        'last_trade': kv.get('LSTTRADE'),
    }


def fetch_contract(secid: str, start: str, end: str) -> list:
    """Fetch all 1m candles for secid in [start, end] with day+start paging."""
    out = []
    day = datetime.strptime(start, '%Y-%m-%d').date()
    end_d = datetime.strptime(end, '%Y-%m-%d').date()
    while day <= end_d:
        if day.weekday() >= 5:
            day += timedelta(days=1)
            continue
        ds = day.isoformat()
        start_idx = 0
        while True:
            url = (ISS.format(sec=secid) +
                   '?iss.meta=off&from=' + ds + '&till=' + ds +
                   '&interval=1&limit=500&start=' + str(start_idx))
            d = get(url)
            rows = d['candles']['data']
            if not rows:
                break
            out.extend(rows)
            if len(rows) < 500:
                break
            start_idx += len(rows)
        day += timedelta(days=1)
        time.sleep(0.12)
    return out


def main() -> int:
    manifest = {}
    for symbol, contracts in CHAINS.items():
        print('=== %s ===' % symbol, flush=True)
        for secid in contracts:
            raw_path = RAW_DIR / ('%s_1m.json' % secid)
            meta_path = RAW_DIR / ('%s_meta.json' % secid)
            meta = contract_meta(secid)
            meta_path.write_text(json.dumps(meta, ensure_ascii=False))
            lo = max(WINDOW_START, (meta['first_trade'] or WINDOW_START)[:10])
            hi = min(WINDOW_END, (meta['last_trade'] or WINDOW_END)[:10])
            if raw_path.exists() and raw_path.stat().st_size > 10:
                n = len(json.loads(raw_path.read_text()))
                print('%s: cached rows=%d (%s..%s)' % (secid, n, lo, hi), flush=True)
                manifest[secid] = {'rows': n, 'window': [lo, hi], **meta}
                continue
            if lo > hi:
                print('%s: window empty, skip' % secid, flush=True)
                continue
            t0 = time.time()
            rows = fetch_contract(secid, lo, hi)
            raw_path.write_text(json.dumps(rows))
            dt = time.time() - t0
            print('%s: fetched rows=%d (%s..%s) in %.0fs' % (
                secid, len(rows), lo, hi, dt), flush=True)
            manifest[secid] = {'rows': len(rows), 'window': [lo, hi], **meta}
    (RAW_DIR / 'fetch_manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1))
    print('FETCH DONE', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())

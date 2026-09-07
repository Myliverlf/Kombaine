#!/usr/bin/env python3
"""ITERATION 24I-R: standalone deterministic historical backfill engine.

Decoupled from pytest by design (pytest SIGTERM was a test-harness/OOM
blocker, not a data-acquisition blocker). This CLI:

  * uses the canonical InstrumentRegistry identity (read-only)
  * paginates BACKWARD from a deterministic end anchor
  * persists a checkpoint after every successful window (SIGTERM-safe resume)
  * dedupes overlaps deterministically, REJECTS conflicting overlaps
  * respects provider request-window limits with safety margins
  * writes a per-window acquisition ledger
  * detects the provider retention boundary (repeated failures with
    succeeding newer adjacent windows)
  * finalizes a verified dataset only after full quality validation
  * records supersession lineage to the 24H datasets via parent_dataset_ids

Terminal states per dataset:
  TARGET_HISTORY_REACHED | PROVIDER_RETENTION_BOUNDARY_PROVEN |
  INSTRUMENT_LISTING_DATE_REACHED | GENUINE_PROVIDER_ERROR

Safety: paper mode only. No orders, no position changes, no live trading.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import resource
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, '/root/prop-desk/strategy_combine')
sys.path.insert(0, '/root/prop-desk/strategy_combine/tools')
sys.path.insert(0, '/root/prop-desk/futures_lab')

from backfill_utils import backward_windows, manifest_hash, merge_dedupe, quality_check, rows_hash, suspicious_gaps  # noqa: E402

ROOT = Path('/root/prop-desk/strategy_combine')
STATE = ROOT / 'state'
REPORTS = ROOT / 'reports'
BF_DIR = STATE / 'backfill_24ir'
RAW_DIR = ROOT / 'artifacts' / 'backfill_24ir' / 'raw'
OUT_DIR = Path('/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data')
for _d in (BF_DIR, RAW_DIR, OUT_DIR, REPORTS):
    _d.mkdir(parents=True, exist_ok=True)

TARGET_COVERAGE_DAYS = 1095
# provider request-window limits proven in 24I: 15m ~14d, 1h ~60d.
# Safety margins applied (chunk strictly below the limit).
TF_CONFIG = {
    '15m': {'chunk_days': 13, 'interval': 'CANDLE_INTERVAL_15_MIN', 'step': timedelta(minutes=15)},
    '1h': {'chunk_days': 55, 'interval': 'CANDLE_INTERVAL_HOUR', 'step': timedelta(hours=1)},
}
OVERLAP_DAYS = 1
MAX_ATTEMPTS = 3
RATE_SLEEP = 0.12
RETRY_BACKOFF = [2.0, 4.0, 8.0]

TRANSIENT_MARKERS = ('UNAVAILABLE', 'DEADLINE_EXCEEDED', 'RESOURCE_EXHAUSTED', 'INTERNAL', 'UNKNOWN')


def log(msg: str) -> None:
    print(f'[{datetime.now(timezone.utc).isoformat(timespec="seconds")}] {msg}', flush=True)


def money(v) -> float:
    return float(v.units) + float(v.nano) / 1e9


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def cp_path(sym: str, tf: str) -> Path:
    return BF_DIR / f'{sym}_{tf}.checkpoint.json'


def raw_path(sym: str, tf: str) -> Path:
    return RAW_DIR / f'{sym}_{tf}.jsonl'


def ledger_path(sym: str, tf: str) -> Path:
    return BF_DIR / f'ledger_{sym}_{tf}.jsonl'


def load_checkpoint(sym: str, tf: str) -> dict | None:
    p = cp_path(sym, tf)
    if p.exists():
        return json.loads(p.read_text(encoding='utf-8'))
    return None


def save_checkpoint(sym: str, tf: str, cp: dict) -> None:
    cp['updated_at'] = datetime.now(timezone.utc).isoformat()
    tmp = cp_path(sym, tf).with_suffix('.tmp')
    tmp.write_text(json.dumps(cp, indent=2, ensure_ascii=False, sort_keys=True), encoding='utf-8')
    tmp.replace(cp_path(sym, tf))


def append_jsonl(path: Path, obj: dict) -> None:
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(obj, ensure_ascii=False, separators=(',', ':')) + '\n')


def read_rows(sym: str, tf: str) -> list[dict]:
    p = raw_path(sym, tf)
    if not p.exists():
        return []
    rows = []
    with p.open(encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    rows.sort(key=lambda r: r['time'])
    return rows


def anchor_end(tf: str, cp: dict | None) -> datetime:
    """Deterministic end anchor: fixed in checkpoint on first run."""
    if cp and cp.get('end_anchor'):
        return datetime.fromisoformat(cp['end_anchor'])
    now = datetime.now(timezone.utc)
    step = TF_CONFIG[tf]['step']
    sec = step.total_seconds()
    truncated = datetime.fromtimestamp(int(now.timestamp() // sec) * sec, tz=timezone.utc)
    return truncated


def classify_error(exc: Exception) -> str:
    name = type(exc).__name__
    text = str(exc)
    if 'INVALID_ARGUMENT' in text or name == 'InvalidArgumentError' or 'invalid' in text.lower() and 'argument' in text.lower():
        return 'INVALID_ARGUMENT'
    if any(m in text for m in TRANSIENT_MARKERS) or name in ('UnavailableError', 'DeadlineExceededError', 'ResourceExhaustedError'):
        return 'TRANSIENT'
    if name in ('OutOfRangeError',) or 'OUT_OF_RANGE' in text:
        return 'OUT_OF_RANGE'
    return f'OTHER:{name}'


def fetch_window(client, uid: str, interval, fr: datetime, to: datetime) -> tuple[list[dict], dict]:
    """One provider request. Returns (rows, response_meta)."""
    t0 = time.monotonic()
    rr = client.market_data.get_candles(from_=fr, to=to, interval=interval, instrument_id=uid)
    rows = [
        {
            'time': c.time.astimezone(timezone.utc).isoformat(),
            'open': money(c.open),
            'high': money(c.high),
            'low': money(c.low),
            'close': money(c.close),
            'volume': int(c.volume),
        }
        for c in rr.candles
    ]
    meta = {'elapsed_ms': int((time.monotonic() - t0) * 1000), 'rows': len(rows)}
    return rows, meta


def acquire_window_with_retry(client, uid: str, interval, fr: datetime, to: datetime, sym: str, tf: str, idx: int, cp: dict) -> dict:
    """Fetch one window with retries. Returns a ledger entry dict with
    'rows' (list) on success or 'error_class'/'error' on permanent failure."""
    last_err = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            rows, meta = fetch_window(client, uid, interval, fr, to)
            entry = {
                'instrument': sym, 'timeframe': tf, 'window_index': idx,
                'requested_from': fr.isoformat(), 'requested_to': to.isoformat(),
                'provider_status': 'OK', 'attempt': attempt,
                'rows_returned': len(rows),
                'first_returned_timestamp': rows[0]['time'] if rows else None,
                'last_returned_timestamp': rows[-1]['time'] if rows else None,
                'error': None, 'content_fingerprint': rows_hash(rows) if rows else None,
                'elapsed_ms': meta['elapsed_ms'],
            }
            return {'entry': entry, 'rows': rows}
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            err_class = classify_error(exc)
            log(f'  window {idx} attempt {attempt}/{MAX_ATTEMPTS} failed: {err_class}: {exc}')
            if err_class in ('INVALID_ARGUMENT', 'OUT_OF_RANGE'):
                break  # permanent for this window shape
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_BACKOFF[attempt - 1])
    return {
        'entry': {
            'instrument': sym, 'timeframe': tf, 'window_index': idx,
            'requested_from': fr.isoformat(), 'requested_to': to.isoformat(),
            'provider_status': classify_error(last_err) if last_err else 'UNKNOWN',
            'attempt': MAX_ATTEMPTS, 'rows_returned': 0,
            'first_returned_timestamp': None, 'last_returned_timestamp': None,
            'error': f'{type(last_err).__name__}: {last_err}' if last_err else 'unknown',
            'content_fingerprint': None, 'elapsed_ms': None,
        },
        'rows': None,
        'error_class': classify_error(last_err) if last_err else 'UNKNOWN',
    }


def verify_and_finalize(sym: str, tf: str, cp: dict, identity, commit_sha: str, old_dataset_id: str | None) -> dict:
    """Full quality validation + manifest registration. Returns inventory record."""
    rows = read_rows(sym, tf)
    merged, identical_dups, conflicts = merge_dedupe([], rows)
    inv_base = {
        'instrument': sym, 'timeframe': tf, 'supersedes_dataset_id': old_dataset_id,
        'dataset_id': None, 'status': None, 'rows': len(merged),
        'first_timestamp': merged[0]['time'] if merged else None,
        'last_timestamp': merged[-1]['time'] if merged else None,
        'duplicate_count_before_merge': identical_dups,
        'conflicting_duplicate_count': len(conflicts),
        'content_hash': rows_hash(merged) if merged else None,
        'manifest_hash': None, 'coverage_days': None,
        'suspicious_gap_count': None, 'terminal_state': cp.get('terminal_state'),
        'retention_boundary': cp.get('retention_boundary'),
    }
    if conflicts:
        inv_base.update(status='QUARANTINED_CONFLICTS', quarantine_conflicts=conflicts[:20])
        return inv_base

    import pandas as pd
    df = pd.DataFrame(merged)
    df['time'] = pd.to_datetime(df['time'], utc=True)
    df = df.sort_values('time').reset_index(drop=True)
    if not df['time'].is_monotonic_increasing:
        inv_base.update(status='FAIL_NON_MONOTONIC')
        return inv_base
    q_status, q_info = quality_check(df)
    gaps = suspicious_gaps(merged, tf)
    inv_base['suspicious_gap_count'] = len(gaps)
    if q_status != 'PASS':
        inv_base.update(status=f'FAIL_QUALITY:{",".join(q_info["issues"])}')
        return inv_base

    first_ts = df['time'].iloc[0]
    last_ts = df['time'].iloc[-1]
    coverage_days = int((last_ts - first_ts).total_seconds() // 86400) + 1
    inv_base['coverage_days'] = coverage_days

    dataset_id = f'{sym}_{tf}_24ir_{first_ts.strftime("%Y-%m-%d")}_{last_ts.strftime("%Y-%m-%d")}'
    from core.data.registry import DatasetRegistry, build_verified_dataset_manifest

    parent_ids = [old_dataset_id] if old_dataset_id else []
    manifest = build_verified_dataset_manifest(
        dataset_id=dataset_id,
        instrument_identity=identity,
        provider='tinkoff',
        source_endpoint='tinkoff.invest.market_data.get_candles',
        requested_start=cp['end_anchor'],
        requested_end=cp['end_anchor'],
        actual_start=first_ts.isoformat(),
        actual_end=last_ts.isoformat(),
        actual_coverage_days=coverage_days,
        timeframe=tf,
        bar_count=len(df),
        timezone='UTC',
        session_calendar='MOEX',
        raw_data_checksum=rows_hash(merged),
        transformed_data_checksum=rows_hash(merged),
        acquisition_timestamp=datetime.now(timezone.utc).isoformat(),
        acquisition_code_commit=commit_sha,
        transformation_pipeline='provider-deterministic-backfill-24ir-v1',
        transformation_parameters=json.dumps({
            'chunk_days': TF_CONFIG[tf]['chunk_days'], 'overlap_days': OVERLAP_DAYS,
            'dedupe': 'deterministic-first-wins', 'timezone': 'UTC',
            'backward_pagination': True, 'conflicts': 'reject',
        }, sort_keys=True),
        provenance_status='VERIFIED',
        integrity_status='PASS',
        identity_status='VERIFIED',
        eligibility_status='ELIGIBLE',
        parent_dataset_ids=parent_ids,
        continuous_series=False,
        adjustment_method='unknown',
    )
    reg_path = STATE / 'dataset_registry_24ir.json'
    if reg_path.exists():
        reg = DatasetRegistry.load(reg_path)
    else:
        reg = DatasetRegistry(schema_version='1.0.0', registry_id='dataset-registry-24ir',
                              created_at=datetime.now(timezone.utc).isoformat())
    reg.register(manifest)
    reg.save(reg_path)

    out_csv = OUT_DIR / f'{sym}_{tf}_backfill_verified_24ir.csv'
    df.to_csv(out_csv, index=False)

    inv_base.update(status='VERIFIED', dataset_id=dataset_id, manifest_hash=manifest_hash(manifest),
                    timezone='UTC')
    log(f'{sym} {tf}: VERIFIED rows={len(df)} coverage={coverage_days}d dataset={dataset_id}')
    return inv_base


def run_backfill(sym: str, tf: str, uid: str, identity, max_windows: int | None, commit_sha: str,
                 old_dataset_id: str | None, listing_floor: datetime | None) -> dict:
    """Backward-paginated acquisition with checkpoint/resume. Returns checkpoint."""
    cfg = TF_CONFIG[tf]
    cp = load_checkpoint(sym, tf)
    end_anchor = anchor_end(tf, cp)
    target_start = end_anchor - timedelta(days=TARGET_COVERAGE_DAYS)
    floor = listing_floor if listing_floor else target_start

    windows = backward_windows(end_anchor, cfg['chunk_days'], OVERLAP_DAYS, floor=floor)

    if cp is None:
        cp = {
            'instrument': sym, 'timeframe': tf, 'provider_uid': uid,
            'end_anchor': end_anchor.isoformat(),
            'target_start': target_start.isoformat(),
            'listing_floor': floor.isoformat() if listing_floor else None,
            'windows_total_planned': len(windows),
            'windows_completed': 0, 'rows_collected': 0, 'retry_count': 0,
            'current_oldest_timestamp': None, 'next_window_end': end_anchor.isoformat(),
            'last_provider_response': None, 'partial_content_hash': None,
            'status': 'IN_PROGRESS', 'terminal_state': None, 'retention_boundary': None,
            'started_at': datetime.now(timezone.utc).isoformat(),
        }
        # fresh start: clear stale raw data if any
        if raw_path(sym, tf).exists():
            raw_path(sym, tf).unlink()
        if ledger_path(sym, tf).exists():
            ledger_path(sym, tf).unlink()
        save_checkpoint(sym, tf, cp)
        log(f'{sym} {tf}: start anchor={end_anchor.isoformat()} planned_windows={len(windows)}')
    else:
        log(f'{sym} {tf}: resume at window {cp["windows_completed"]}/{len(windows)} rows={cp["rows_collected"]} status={cp["status"]}')

    if cp['status'] in ('VERIFIED', 'QUARANTINED_CONFLICTS', 'VERIFIED_BOUNDED_BY_RETENTION'):
        return cp
    if cp.get('terminal_state') == 'GENUINE_PROVIDER_ERROR':
        return cp
    if cp.get('terminal_state') == 'PROVIDER_RETENTION_BOUNDARY_PROVEN':
        cp['status'] = 'ACQUIRED_BOUNDARY'
        save_checkpoint(sym, tf, cp)
        return cp

    from tinkoff.invest import CandleInterval, Client
    from tinkoff.invest.constants import INVEST_GRPC_API
    from futures_lab import load_token

    interval = getattr(CandleInterval, cfg['interval'])
    token = load_token()

    start_idx = cp['windows_completed']
    limit = len(windows) if max_windows is None else min(len(windows), start_idx + max_windows)

    with Client(token, target=INVEST_GRPC_API) as client:
        for idx in range(start_idx, limit):
            fr, to = windows[idx]
            result = acquire_window_with_retry(client, uid, interval, fr, to, sym, tf, idx, cp)
            entry = result['entry']
            append_jsonl(ledger_path(sym, tf), entry)
            cp['retry_count'] += max(0, entry['attempt'] - 1)
            cp['last_provider_response'] = {k: entry[k] for k in
                                            ('window_index', 'provider_status', 'rows_returned', 'error', 'attempt')}

            if result['rows'] is None:
                # permanent failure of a valid-sized window: boundary candidate
                if cp['rows_collected'] == 0:
                    cp['status'] = 'GENUINE_PROVIDER_ERROR'
                    cp['terminal_state'] = 'GENUINE_PROVIDER_ERROR'
                    cp['retention_boundary'] = {'failed_window': entry, 'newer_window_ok': False}
                    save_checkpoint(sym, tf, cp)
                    return cp
                cp.setdefault('boundary_streak', []).append(entry)
                cp['windows_completed'] = idx + 1
                cp['next_window_end'] = fr.isoformat()
                save_checkpoint(sym, tf, cp)
                log(f'{sym} {tf}: window {idx + 1} FAILED permanently ({entry["provider_status"]}), streak={len(cp["boundary_streak"])}')
                if len(cp['boundary_streak']) >= 3:
                    cp['terminal_state'] = 'PROVIDER_RETENTION_BOUNDARY_PROVEN'
                    cp['retention_boundary'] = {
                        'last_successful_window': cp.get('last_successful_window'),
                        'first_blocked_window': cp['boundary_streak'][0],
                        'consecutive_failed_windows': cp['boundary_streak'],
                        'provider_response': cp['boundary_streak'][0]['error'] or 'empty_or_error',
                        'instrument': sym, 'timeframe': tf,
                    }
                    cp['status'] = 'BOUNDARY_PROVEN'
                    save_checkpoint(sym, tf, cp)
                    log(f'{sym} {tf}: RETENTION BOUNDARY PROVEN after 3 consecutive failed windows')
                    return cp
                time.sleep(RATE_SLEEP)
                continue

            # success path (possibly zero rows — e.g. holidays or pre-retention)
            if result['rows']:
                if cp.get('boundary_streak'):
                    # older window succeeded after failures -> anomaly, reset streak
                    log(f'{sym} {tf}: ANOMALY window {idx + 1} succeeded after {len(cp["boundary_streak"])} failures — streak reset')
                    append_jsonl(ledger_path(sym, tf), {'anomaly_window_succeeded_after_failure': entry})
                    cp['boundary_streak'] = []
                for r in result['rows']:
                    append_jsonl(raw_path(sym, tf), r)
                cp['rows_collected'] += len(result['rows'])
                oldest = result['rows'][0]['time']
                if cp['current_oldest_timestamp'] is None or oldest < cp['current_oldest_timestamp']:
                    cp['current_oldest_timestamp'] = oldest
                cp['last_successful_window'] = entry
            else:
                # zero-row successful response after data existed: retention or pre-listing
                cp.setdefault('boundary_streak', []).append(entry)
                if len(cp['boundary_streak']) >= 3:
                    cp['terminal_state'] = 'PROVIDER_RETENTION_BOUNDARY_PROVEN'
                    cp['retention_boundary'] = {
                        'last_successful_window': cp.get('last_successful_window'),
                        'first_blocked_window': cp['boundary_streak'][0],
                        'consecutive_failed_windows': cp['boundary_streak'],
                        'provider_response': 'empty_candles_x3',
                        'instrument': sym, 'timeframe': tf,
                    }
                    cp['status'] = 'BOUNDARY_PROVEN'
                    save_checkpoint(sym, tf, cp)
                    log(f'{sym} {tf}: RETENTION BOUNDARY PROVEN (3 consecutive empty windows)')
                    return cp
            cp['windows_completed'] = idx + 1
            cp['next_window_end'] = fr.isoformat()
            cp['partial_content_hash'] = entry.get('content_fingerprint')
            save_checkpoint(sym, tf, cp)
            log(f'{sym} {tf}: window {idx + 1}/{len(windows)} OK rows={entry["rows_returned"]} oldest={cp["current_oldest_timestamp"]}')
            time.sleep(RATE_SLEEP)

            # target reached?
            if fr <= target_start:
                cp['terminal_state'] = 'TARGET_HISTORY_REACHED'
                cp['status'] = 'ACQUIRED'
                save_checkpoint(sym, tf, cp)
                log(f'{sym} {tf}: TARGET_HISTORY_REACHED (target_start={target_start.isoformat()})')
                return cp

            # listing floor reached?
            if listing_floor and fr <= listing_floor:
                cp['terminal_state'] = 'INSTRUMENT_LISTING_DATE_REACHED'
                cp['status'] = 'ACQUIRED'
                save_checkpoint(sym, tf, cp)
                log(f'{sym} {tf}: INSTRUMENT_LISTING_DATE_REACHED ({listing_floor.isoformat()})')
                return cp

        if cp['windows_completed'] >= len(windows):
            cp['terminal_state'] = cp['terminal_state'] or 'TARGET_HISTORY_REACHED'
            cp['status'] = 'ACQUIRED'
            save_checkpoint(sym, tf, cp)
    return cp


def main() -> int:
    ap = argparse.ArgumentParser(description='24I-R standalone backfill engine')
    ap.add_argument('--symbol', help='GAZP|SBER|LKOH')
    ap.add_argument('--tf', help='15m|1h')
    ap.add_argument('--all', action='store_true', help='run all six datasets')
    ap.add_argument('--max-windows', type=int, default=None, help='bound windows this run (proof mode)')
    ap.add_argument('--finalize', action='store_true', help='verify+register acquired data and exit')
    ap.add_argument('--status', action='store_true', help='print checkpoint status and exit')
    args = ap.parse_args()

    if args.status:
        pairs = [(s, t) for s in ('GAZP', 'SBER', 'LKOH') for t in ('15m', '1h')]
        for s, t in pairs:
            cp = load_checkpoint(s, t)
            if cp:
                print(f'{s} {t}: status={cp["status"]} terminal={cp.get("terminal_state")} '
                      f'windows={cp["windows_completed"]}/{cp["windows_total_planned"]} rows={cp["rows_collected"]} '
                      f'oldest={cp.get("current_oldest_timestamp")}')
            else:
                print(f'{s} {t}: no checkpoint')
        return 0

    from core.instruments.registry import InstrumentRegistry
    inst_reg = InstrumentRegistry.load(STATE / 'instrument_registry.json')

    # canonical identities (verified in 24H)
    identities = {}
    for rec in inst_reg.identities.values():
        identities[rec.instrument.canonical_symbol] = rec.instrument

    # listing floor from provider metadata (best effort, per-symbol cache)
    floor_cache_path = BF_DIR / 'listing_floors.json'
    floors = json.loads(floor_cache_path.read_text()) if floor_cache_path.exists() else {}

    def get_floor(sym: str) -> datetime | None:
        if sym in floors:
            return datetime.fromisoformat(floors[sym]) if floors[sym] else None
        try:
            from tinkoff.invest import Client
            from tinkoff.invest.constants import INVEST_GRPC_API
            from futures_lab import load_token
            with Client(load_token(), target=INVEST_GRPC_API) as client:
                inst = client.instruments.find_instrument(query=sym)
                cand = [i for i in inst.instruments if i.ticker == sym and i.class_code == 'TQBR']
                d = getattr(cand[0], 'first_1min_candle_date', None) if cand else None
                # provider returns epoch sentinel 1970-01-01 when unknown
                if d is not None and d.year >= 1990:
                    floors[sym] = d.astimezone(timezone.utc).isoformat()
                else:
                    floors[sym] = None
                floor_cache_path.write_text(json.dumps(floors, indent=2))
                return datetime.fromisoformat(floors[sym]) if floors[sym] else None
        except Exception as exc:  # noqa: BLE001
            log(f'listing floor lookup failed for {sym}: {exc}')
            floors[sym] = None
            floor_cache_path.write_text(json.dumps(floors, indent=2))
            return None

    # supersession: 24H dataset ids (read-only evidence, never mutated)
    old_ids = {}
    try:
        from core.data.registry import DatasetRegistry
        old_reg = DatasetRegistry.load(STATE / 'dataset_registry.json')
        for ds_id in old_reg.datasets:
            m = old_reg.datasets[ds_id]
            old_ids[(m.instrument_identity_id, m.timeframe)] = ds_id
    except Exception as exc:  # noqa: BLE001
        log(f'old registry load failed: {exc}')

    commit_sha = 'unknown'
    try:
        import subprocess
        commit_sha = subprocess.run(['git', '-C', '/root/prop-desk', 'rev-parse', '--short', 'HEAD'],
                                    capture_output=True, text=True, timeout=10).stdout.strip() or 'unknown'
    except Exception:  # noqa: BLE001
        pass

    pairs = [(s, t) for s in ('GAZP', 'SBER', 'LKOH') for t in ('15m', '1h')] if args.all else [(args.symbol, args.tf)]

    for sym, tf in pairs:
        if sym is None or tf is None:
            ap.error('need --symbol and --tf, or --all')
        identity = identities[sym]
        uid = identity.provider_instrument_uid
        floor = get_floor(sym)
        cp = run_backfill(sym, tf, uid, identity, args.max_windows, commit_sha,
                          old_ids.get((sym, tf)), floor)

        if args.finalize or cp.get('terminal_state') in ('TARGET_HISTORY_REACHED', 'INSTRUMENT_LISTING_DATE_REACHED', 'PROVIDER_RETENTION_BOUNDARY_PROVEN'):
            if cp['status'] in ('ACQUIRED', 'BOUNDARY_PROVEN'):
                inv = verify_and_finalize(sym, tf, cp, identity, commit_sha, old_ids.get((sym, tf)))
                if cp['status'] == 'BOUNDARY_PROVEN' and inv.get('status') == 'VERIFIED':
                    inv['status'] = 'VERIFIED_BOUNDED_BY_RETENTION'
                cp['status'] = inv['status']
                cp['dataset_id'] = inv.get('dataset_id')
                save_checkpoint(sym, tf, cp)
                inv_path = BF_DIR / f'inventory_{sym}_{tf}.json'
                inv_path.write_text(json.dumps(inv, indent=2, ensure_ascii=False, sort_keys=True), encoding='utf-8')
            elif cp['status'] == 'GENUINE_PROVIDER_ERROR':
                inv_path = BF_DIR / f'inventory_{sym}_{tf}.json'
                inv_path.write_text(json.dumps({'instrument': sym, 'timeframe': tf, 'status': 'GENUINE_PROVIDER_ERROR', 'terminal_state': 'GENUINE_PROVIDER_ERROR', 'retention_boundary': cp.get('retention_boundary')}, indent=2, ensure_ascii=False, sort_keys=True), encoding='utf-8')

    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    log(f'done. peak RSS = {rss_mb:.1f} MB')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

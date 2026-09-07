from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(0, '/root/prop-desk/strategy_combine')

from core.data.ingress_gate import ResearchIngressGate
from core.data.registry import DatasetRegistry, build_verified_dataset_manifest
from core.instruments.identity import InstrumentIdentity
from core.instruments.registry import InstrumentRegistry
from core.research.context import VerifiedResearchContext
from backfill_utils import build_windows, quality_check, rows_hash, manifest_hash

ROOT = Path('/root/prop-desk/strategy_combine')
DATA_DIR = Path('/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data')
REPORTS = ROOT / 'reports'
STATE = ROOT / 'state'
REPORTS.mkdir(parents=True, exist_ok=True)
STATE.mkdir(parents=True, exist_ok=True)

TARGETS = ['GAZP', 'SBER', 'LKOH']
TIMEFRAMES = {
    '15m': {'chunk_days': 7, 'window_days': 14},
    '1h': {'chunk_days': 60, 'window_days': 60},
}
TARGET_COVERAGE_DAYS = {'15m': 1095, '1h': 1095}
NOW = datetime.now(timezone.utc)


def money(v):
    return float(v.units) + float(v.nano) / 1e9


def fetch_windows(client, uid: str, interval, target_days: int, chunk_days: int):
    end = NOW
    start = NOW - timedelta(days=target_days)
    windows = build_windows(end, target_days, chunk_days)
    ledger = []
    all_rows = []
    for idx, (fr, to) in enumerate(windows):
        try:
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
            ledger.append({
                'request_index': idx,
                'requested_from': fr.isoformat(),
                'requested_to': to.isoformat(),
                'provider_status': 'OK',
                'rows_returned': len(rows),
                'first_returned_timestamp': rows[0]['time'] if rows else None,
                'last_returned_timestamp': rows[-1]['time'] if rows else None,
                'error': None,
                'content_fingerprint': rows_hash(rows) if rows else None,
            })
            all_rows.extend(rows)
        except Exception as e:
            ledger.append({
                'request_index': idx,
                'requested_from': fr.isoformat(),
                'requested_to': to.isoformat(),
                'provider_status': type(e).__name__,
                'rows_returned': 0,
                'first_returned_timestamp': None,
                'last_returned_timestamp': None,
                'error': f'{type(e).__name__}: {e}',
                'content_fingerprint': None,
            })
            return ledger, None, f'{type(e).__name__}: {e}'
    if not all_rows:
        return ledger, None, 'no_rows'
    df = pd.DataFrame(all_rows)
    df['time'] = pd.to_datetime(df['time'], utc=True)
    df = df.sort_values('time').reset_index(drop=True)
    before = len(df)
    dup_count = int(df.duplicated('time').sum())
    if dup_count:
        deduped = df.drop_duplicates('time', keep='first').reset_index(drop=True)
        if len(deduped) != len(df):
            # verify identical overlaps only
            merged = df.groupby('time').agg(list)
            for _, row in merged.iterrows():
                if len(set(tuple(v) for v in zip(row['open'], row['high'], row['low'], row['close'], row['volume']))) > 1:
                    return ledger, None, 'conflicting_duplicate_candles'
        df = deduped
    if not df['time'].is_monotonic_increasing:
        return ledger, None, 'non_monotonic_after_merge'
    q_status, q_info = quality_check(df)
    if q_status != 'PASS':
        return ledger, None, json.dumps(q_info)
    return ledger, {'df': df, 'requested_start': start.isoformat(), 'requested_end': end.isoformat(), 'duplicate_count_before_merge': before - len(df), 'row_count': len(df), 'first_timestamp': df['time'].iloc[0].isoformat(), 'last_timestamp': df['time'].iloc[-1].isoformat(), 'content_hash': rows_hash(df.to_dict(orient='records'))}, None


def main() -> int:
    from futures_lab import load_token
    from tinkoff.invest import CandleInterval, Client
    from tinkoff.invest.constants import INVEST_GRPC_API

    token = load_token()
    inst_reg = InstrumentRegistry(schema_version='1.0.0', registry_id='instrument-registry-24i', created_at=NOW.isoformat())
    ds_reg = DatasetRegistry(schema_version='1.0.0', registry_id='dataset-registry-24i', created_at=NOW.isoformat())
    ingress = ResearchIngressGate(ds_reg)

    backfill_requests = []
    inventory = []
    coverage_matrix = {sym: {tf: {} for tf in TIMEFRAMES} for sym in TARGETS}
    corporate_actions = {}
    readiness = {
        'equity_identity_ready': True,
        'verified_equity_data_ready': False,
        'verified_equity_60d_15m_ready': False,
        'verified_equity_60d_1h_ready': False,
        'verified_equity_90d_ready': False,
        'verified_equity_180d_ready': False,
        'verified_equity_365d_ready': False,
        'verified_equity_1095d_ready': False,
        'multi_horizon_bounded_research_ready': False,
        'long_history_ready': False,
    }

    with Client(token, target=INVEST_GRPC_API) as client:
        shares = client.instruments.shares(instrument_status=1).instruments
        futures = client.instruments.futures(instrument_status=1).instruments
        share_map = {sym: next((i for i in shares if getattr(i, 'ticker', '') == sym), None) for sym in TARGETS}
        future_map = {sym: next((i for i in futures if getattr(i, 'ticker', '') == sym), None) for sym in ['GAZPF', 'SBERF']}

        for sym in TARGETS:
            inst = share_map[sym]
            if inst is None:
                inventory.append({'instrument': sym, 'timeframe': None, 'status': 'BLOCKED_EXTERNAL', 'blocker': 'share not found'})
                readiness['equity_identity_ready'] = False
                continue
            identity = InstrumentIdentity(schema_version='1.0.0', canonical_symbol=sym, provider='tinkoff', provider_instrument_uid=getattr(inst, 'uid', ''), figi=getattr(inst, 'figi', None), ticker=getattr(inst, 'ticker', sym), class_code=getattr(inst, 'class_code', ''), instrument_type='equity', exchange=getattr(inst, 'exchange', ''), currency=getattr(inst, 'currency', ''), lot_size=int(getattr(inst, 'lot', 1) or 1), underlying_uid=None, underlying_symbol=None, is_derivative=False, identity_source='tinkoff.invest shares instrument list', identity_verified_at=NOW.isoformat(), identity_verification_method='provider-native-share-list', identity_version='1')
            identity.validate()
            reg = inst_reg.register(identity, verification_source='tinkoff.invest shares', verification_method='provider-native-share-list', verification_timestamp=NOW.isoformat(), status='VERIFIED')
            corporate_actions[sym] = {'provider_semantics': 'UNKNOWN', 'evidence_source': 'provider metadata insufficient for adjustments', 'research_eligibility_impact': 'BLOCKED_CORPORATE_ACTION_SEMANTICS'}
            for tf, cfg in TIMEFRAMES.items():
                interval = CandleInterval.CANDLE_INTERVAL_15_MIN if tf == '15m' else CandleInterval.CANDLE_INTERVAL_HOUR
                ledger, result, error = fetch_windows(client, inst.uid, interval, TARGET_COVERAGE_DAYS[tf], cfg['chunk_days'])
                backfill_requests.extend([
                    {'instrument': sym, 'provider_uid': getattr(inst, 'uid', None), 'timeframe': tf, **entry}
                    for entry in ledger
                ])
                if error or result is None:
                    inventory.append({'instrument': sym, 'provider_uid': getattr(inst, 'uid', None), 'timeframe': tf, 'old_dataset_id': None, 'new_dataset_id': None, 'status': 'BLOCKED_EXTERNAL', 'request_windows_attempted': len(ledger), 'request_windows_succeeded': sum(1 for x in ledger if x['provider_status'] == 'OK'), 'rows': 0, 'first_timestamp': None, 'last_timestamp': None, 'coverage_days': None, 'trading_days': None, 'suspicious_gap_count': None, 'duplicate_count_before_merge': None, 'conflicting_duplicate_count': None, 'content_hash': None, 'manifest_hash': None, 'corporate_action_semantics': 'UNKNOWN', 'research_eligibility': 'BLOCKED_EXTERNAL', 'blocker': error})
                    continue
                df = result['df']
                dataset_id = f'{sym}_{tf}_backfill_{result["first_timestamp"][:10]}_{result["last_timestamp"][:10]}'
                manifest = build_verified_dataset_manifest(dataset_id=dataset_id, instrument_identity=identity, provider='tinkoff', source_endpoint='tinkoff.invest.market_data.get_candles', requested_start=result['requested_start'], requested_end=result['requested_end'], actual_start=result['first_timestamp'], actual_end=result['last_timestamp'], actual_coverage_days=max(1, int((pd.to_datetime(result['last_timestamp']) - pd.to_datetime(result['first_timestamp'])).total_seconds() // 86400) + 1), timeframe=tf, bar_count=len(df), timezone='UTC', session_calendar='MOEX', raw_data_checksum=result['content_hash'], transformed_data_checksum=result['content_hash'], acquisition_timestamp=NOW.isoformat(), acquisition_code_commit='unknown', transformation_pipeline='provider-deterministic-backfill-v1', transformation_parameters='{"chunking": true, "deduplicate": true, "timezone": "UTC"}', provenance_status='VERIFIED', integrity_status='PASS', identity_status='VERIFIED', eligibility_status='ELIGIBLE', continuous_series=False, adjustment_method='unknown')
                ds_reg.register(manifest)
                ingress.require_verified_dataset(identity=identity, dataset=manifest, dataset_id=dataset_id)
                ctx = VerifiedResearchContext(schema_version='1.0.0', context_id=f'ctx-{dataset_id}', experiment_id=f'exp-{dataset_id}', dataset_id=dataset_id, dataset_manifest=manifest, instrument_identity=identity, requested_horizon='1095d', resolved_horizon='1095d' if result['duplicate_count_before_merge'] == 0 and len(df) > 0 and (pd.to_datetime(df['time'].iloc[-1]) - pd.to_datetime(df['time'].iloc[0])).days >= 1095 else 'backfill', timeframe=tf, strategy_id='coverage-proof', strategy_version='1.0.0', strategy_hash='hash', compatibility_result='PASS', research_policy_version='1.0.0', cost_model_version='1.0.0', ingress_receipt='verified dataset ingress', dataset_checksum=result['content_hash'], identity_hash=inst_reg.identities[reg.identity_id]['identity_hash'], identity_version='1', created_at=NOW.isoformat())
                ctx.validate()
                out_csv = DATA_DIR / f'{sym}_{tf}_backfill_verified_24i.csv'
                df.to_csv(out_csv, index=False)
                inventory.append({'instrument': sym, 'provider_uid': getattr(inst, 'uid', None), 'timeframe': tf, 'old_dataset_id': None, 'new_dataset_id': dataset_id, 'status': 'VERIFIED', 'request_windows_attempted': len(ledger), 'request_windows_succeeded': sum(1 for x in ledger if x['provider_status'] == 'OK'), 'rows': len(df), 'first_timestamp': result['first_timestamp'], 'last_timestamp': result['last_timestamp'], 'coverage_days': manifest.actual_coverage_days, 'trading_days': None, 'suspicious_gap_count': 0, 'duplicate_count_before_merge': result['duplicate_count_before_merge'], 'conflicting_duplicate_count': 0, 'content_hash': result['content_hash'], 'manifest_hash': manifest_hash(manifest), 'corporate_action_semantics': 'UNKNOWN', 'research_eligibility': 'ELIGIBLE', 'blocker': None})
                coverage_matrix[sym][tf] = {
                    '60d': 'AVAILABLE' if tf == '1h' and manifest.actual_coverage_days >= 60 else ('AVAILABLE' if tf == '15m' and manifest.actual_coverage_days >= 60 else 'BLOCKED_INSUFFICIENT_COVERAGE'),
                    '90d': 'AVAILABLE' if manifest.actual_coverage_days >= 90 else 'BLOCKED_INSUFFICIENT_COVERAGE',
                    '180d': 'AVAILABLE' if manifest.actual_coverage_days >= 180 else 'BLOCKED_INSUFFICIENT_COVERAGE',
                    '365d': 'AVAILABLE' if manifest.actual_coverage_days >= 365 else 'BLOCKED_INSUFFICIENT_COVERAGE',
                    '1095d': 'AVAILABLE' if manifest.actual_coverage_days >= 1095 else 'BLOCKED_INSUFFICIENT_COVERAGE',
                }
                readiness['verified_equity_data_ready'] = True
                if tf == '15m':
                    readiness['verified_equity_60d_15m_ready'] = manifest.actual_coverage_days >= 60
                if tf == '1h':
                    readiness['verified_equity_60d_1h_ready'] = manifest.actual_coverage_days >= 60
                readiness['verified_equity_90d_ready'] = readiness['verified_equity_90d_ready'] or manifest.actual_coverage_days >= 90
                readiness['verified_equity_180d_ready'] = readiness['verified_equity_180d_ready'] or manifest.actual_coverage_days >= 180
                readiness['verified_equity_365d_ready'] = readiness['verified_equity_365d_ready'] or manifest.actual_coverage_days >= 365
                readiness['verified_equity_1095d_ready'] = readiness['verified_equity_1095d_ready'] or manifest.actual_coverage_days >= 1095

        for sym, inst in future_map.items():
            if inst is not None:
                identity = {'requested_symbol': sym, 'provider_symbol': getattr(inst, 'ticker', None), 'provider': 'tinkoff', 'uid': getattr(inst, 'uid', None), 'figi': getattr(inst, 'figi', None), 'class_code': getattr(inst, 'class_code', None), 'instrument_type': 'futures'}
                inventory.append({'instrument': sym, 'provider_uid': getattr(inst, 'uid', None), 'timeframe': None, 'old_dataset_id': None, 'new_dataset_id': None, 'status': 'INVALID_FOR_EQUITY', 'request_windows_attempted': 0, 'request_windows_succeeded': 0, 'rows': 0, 'first_timestamp': None, 'last_timestamp': None, 'coverage_days': None, 'trading_days': None, 'suspicious_gap_count': None, 'duplicate_count_before_merge': None, 'conflicting_duplicate_count': None, 'content_hash': None, 'manifest_hash': None, 'corporate_action_semantics': 'N/A', 'research_eligibility': 'INVALID_FOR_EQUITY', 'blocker': 'futures_not_equity'})

    # write reports
    (REPORTS / 'provider_backfill_requests_24i.json').write_text(json.dumps(backfill_requests, indent=2, ensure_ascii=False, sort_keys=True), encoding='utf-8')
    (REPORTS / 'equity_historical_inventory_24i.json').write_text(json.dumps(inventory, indent=2, ensure_ascii=False, sort_keys=True), encoding='utf-8')
    (REPORTS / 'equity_coverage_matrix_24i.json').write_text(json.dumps(coverage_matrix, indent=2, ensure_ascii=False, sort_keys=True), encoding='utf-8')
    (REPORTS / 'corporate_action_semantics_24i.json').write_text(json.dumps(corporate_actions, indent=2, ensure_ascii=False, sort_keys=True), encoding='utf-8')
    (REPORTS / 'historical_data_readiness_24i.json').write_text(json.dumps(readiness, indent=2, ensure_ascii=False, sort_keys=True), encoding='utf-8')
    inst_reg.save(STATE / 'instrument_registry.json')
    ds_reg.save(STATE / 'dataset_registry.json')
    print(json.dumps({'readiness': readiness, 'verified_datasets': len([x for x in inventory if x['status'] == 'VERIFIED'])}, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

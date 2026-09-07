from pathlib import Path

from core.data import DatasetRegistry, ResearchIngressGate, build_verified_dataset_manifest
from core.data.manifest import DatasetManifest
from core.instruments.identity import InstrumentIdentity


def _identity(symbol='GAZP', provider_uid='provider:GAZP', inst_type='futures_continuous'):
    return InstrumentIdentity(
        schema_version='1.0.0',
        canonical_symbol=symbol,
        provider='futures_lab',
        provider_instrument_uid=provider_uid,
        figi='FIGI',
        ticker=symbol,
        class_code='FILE',
        instrument_type=inst_type,
        exchange='MOEX',
        currency='RUB',
        lot_size=1,
        underlying_uid='under' if inst_type != 'equity' else None,
        underlying_symbol=symbol if inst_type != 'equity' else None,
        is_derivative=inst_type != 'equity',
        identity_source='provider-native',
        identity_verified_at='2026-08-31T00:00:00+00:00',
        identity_verification_method='provider-native',
    )


def _manifest(symbol='GAZP', dataset_id='ds-1', provenance='VERIFIED', identity_status='VERIFIED', integrity='PASS'):
    return DatasetManifest(
        schema_version='1.0.0',
        dataset_id=dataset_id,
        instrument_identity_id=symbol,
        provider='futures_lab',
        source_endpoint='local',
        requested_start='2025-01-01T00:00:00+00:00',
        requested_end='2025-12-31T23:59:59+00:00',
        actual_start='2025-01-01T00:00:00+00:00',
        actual_end='2025-12-31T23:59:59+00:00',
        actual_coverage_days=365,
        timeframe='1h',
        bar_count=100,
        timezone='UTC',
        session_calendar='MOEX',
        missing_bar_summary='none',
        raw_data_checksum='raw',
        transformed_data_checksum='xform',
        acquisition_timestamp='2026-08-31T00:00:00+00:00',
        acquisition_code_commit='abc',
        transformation_pipeline='pipe',
        transformation_parameters='{}',
        parent_dataset_ids=[],
        continuous_series=True,
        continuous_method='back_adjusted_continuous',
        futures_contract_chain='chain',
        roll_rule='rule',
        adjustment_method='ratio',
        provenance_status=provenance,
        integrity_status=integrity,
        identity_status=identity_status,
        eligibility_status='ELIGIBLE',
    )


def test_registry_register_and_require_verified(tmp_path: Path):
    reg = DatasetRegistry(schema_version='1.0.0', registry_id='r1', created_at='2026-08-31T00:00:00+00:00')
    man = _manifest()
    reg.register(man)
    assert reg.require_verified('ds-1') == man
    out = tmp_path / 'registry.json'
    reg.save(out)
    loaded = DatasetRegistry.load(out)
    assert loaded.get('ds-1').dataset_id == 'ds-1'


def test_registry_quarantined_dataset_blocked():
    reg = DatasetRegistry(schema_version='1.0.0', registry_id='r1', created_at='2026-08-31T00:00:00+00:00')
    reg.register(_manifest(dataset_id='ds-q', provenance='QUARANTINED', identity_status='WRONG_INSTRUMENT'))
    try:
        reg.require_verified('ds-q')
    except Exception as exc:
        assert 'DATASET_PROVENANCE_BLOCKED' in str(exc) or 'DATASET_IDENTITY_BLOCKED' in str(exc)
    else:
        raise AssertionError('expected blocked dataset')


def test_ingress_gate_blocks_bare_ticker_csv_and_dataframe():
    reg = DatasetRegistry(schema_version='1.0.0', registry_id='r1', created_at='2026-08-31T00:00:00+00:00')
    reg.register(_manifest())
    gate = ResearchIngressGate(reg)
    decision = gate.block_direct_ingest('GAZP')
    assert decision.allowed is False
    assert decision.gate == 'GATE_DIRECT_INPUT'
    decision = gate.block_direct_ingest('/tmp/x.csv')
    assert decision.allowed is False
    class DummyDF:
        columns = ['time']
    assert gate.block_direct_ingest(DummyDF()).allowed is False


def test_ingress_gate_requires_verified_identity_and_dataset():
    reg = DatasetRegistry(schema_version='1.0.0', registry_id='r1', created_at='2026-08-31T00:00:00+00:00')
    man = _manifest(dataset_id='ds-verified')
    reg.register(man)
    gate = ResearchIngressGate(reg)
    decision = gate.require_verified_dataset(identity=_identity(), dataset=man, dataset_id='ds-verified')
    assert decision.allowed is True
    blocked = gate.require_verified_dataset(identity=_identity(provider_uid='file:GAZP', inst_type='futures_continuous'), dataset=man, dataset_id='ds-verified')
    assert blocked.allowed is False
    assert blocked.gate == 'GATE_2'
    assert 'file alias cannot prove provider-native identity' in blocked.reason


def test_build_verified_dataset_manifest_roundtrip():
    identity = _identity()
    manifest = build_verified_dataset_manifest(
        dataset_id='ds-roundtrip',
        instrument_identity=identity,
        provider='futures_lab',
        source_endpoint='local',
        requested_start='2025-01-01T00:00:00+00:00',
        requested_end='2025-12-31T23:59:59+00:00',
        actual_start='2025-01-01T00:00:00+00:00',
        actual_end='2025-12-31T23:59:59+00:00',
        actual_coverage_days=365,
        timeframe='1h',
        bar_count=100,
        timezone='UTC',
        session_calendar='MOEX',
        raw_data_checksum='raw',
        transformed_data_checksum='xform',
        acquisition_timestamp='2026-08-31T00:00:00+00:00',
        acquisition_code_commit='abc',
        transformation_pipeline='pipe',
        transformation_parameters='{}',
    )
    assert manifest.provenance_status == 'VERIFIED'
    assert manifest.identity_status == 'VERIFIED'
    assert manifest.integrity_status == 'PASS'

from pathlib import Path

from core.data import DatasetRegistry, ResearchIngressGate
from core.data.manifest import DatasetManifest
from core.instruments.identity import InstrumentIdentity


def test_canonical_gated_path_requires_verified_dataset():
    reg = DatasetRegistry(schema_version='1.0.0', registry_id='r1', created_at='2026-08-31T00:00:00+00:00')
    gate = ResearchIngressGate(reg)
    identity = InstrumentIdentity(
        schema_version='1.0.0', canonical_symbol='GAZP', provider='futures_lab', provider_instrument_uid='provider:GAZP',
        figi='FIGI', ticker='GAZP', class_code='TQBR', instrument_type='equity', exchange='MOEX', currency='RUB', lot_size=1,
        underlying_uid=None, underlying_symbol=None, is_derivative=False, identity_source='provider-native',
        identity_verified_at='2026-08-31T00:00:00+00:00', identity_verification_method='provider-native'
    )
    manifest = DatasetManifest(
        schema_version='1.0.0', dataset_id='eq_gazp_verified', instrument_identity_id='GAZP', provider='futures_lab', source_endpoint='provider-native',
        requested_start='2025-01-01T00:00:00+00:00', requested_end='2025-12-31T23:59:59+00:00', actual_start='2025-01-01T00:00:00+00:00', actual_end='2025-12-31T23:59:59+00:00',
        actual_coverage_days=365, timeframe='1h', bar_count=100, timezone='UTC', session_calendar='MOEX', missing_bar_summary='none', raw_data_checksum='raw', transformed_data_checksum='x',
        acquisition_timestamp='2026-08-31T00:00:00+00:00', acquisition_code_commit='abc', transformation_pipeline='pipe', transformation_parameters='{}',
        provenance_status='VERIFIED', integrity_status='PASS', identity_status='VERIFIED', eligibility_status='ELIGIBLE'
    )
    reg.register(manifest)
    decision = gate.require_verified_dataset(identity=identity, dataset=manifest, dataset_id='eq_gazp_verified')
    assert decision.allowed is True
    assert decision.gate == 'PASS'

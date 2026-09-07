from core.data.manifest import DatasetManifest
from core.experiments.manifest import ExperimentManifest
from core.instruments.identity import InstrumentIdentity
from core.data.registry import DatasetRegistry
from core.research.context import VerifiedResearchContext
from core.research.evidence_gate import authorize_scientific_evidence
from core.data.ingress_gate import ResearchIngressGate


def _identity(symbol: str, uid: str, instrument_type: str='equity', derivative: bool=False):
    return InstrumentIdentity(
        schema_version='1.0.0', canonical_symbol=symbol, provider='tinkoff', provider_instrument_uid=uid, figi=f'FIGI-{symbol}', ticker=symbol,
        class_code='TQBR' if instrument_type == 'equity' else 'FUT', instrument_type=instrument_type, exchange='MOEX', currency='RUB', lot_size=10,
        underlying_uid=None if not derivative else 'under', underlying_symbol=None if not derivative else 'UNDER', is_derivative=derivative,
        identity_source='provider-native', identity_verified_at='2026-08-31T00:00:00+00:00', identity_verification_method='provider-api'
    )


def _manifest(symbol: str='GAZP', dataset_id: str='ds-1', prov='VERIFIED', ident='VERIFIED', integ='PASS'):
    return DatasetManifest(
        schema_version='1.0.0', dataset_id=dataset_id, instrument_identity_id=symbol, provider='tinkoff', source_endpoint='provider-native',
        requested_start='2025-01-01T00:00:00+00:00', requested_end='2025-12-31T23:59:59+00:00', actual_start='2025-01-01T00:00:00+00:00', actual_end='2025-12-31T23:59:59+00:00',
        actual_coverage_days=365, timeframe='1h', bar_count=100, timezone='UTC', session_calendar='MOEX', missing_bar_summary='none', raw_data_checksum='raw', transformed_data_checksum='xform',
        acquisition_timestamp='2026-08-31T00:00:00+00:00', acquisition_code_commit='abc', transformation_pipeline='pipe', transformation_parameters='{}',
        provenance_status=prov, integrity_status=integ, identity_status=ident, eligibility_status='ELIGIBLE'
    )


def test_verified_context_and_evidence_gate_pass():
    reg = DatasetRegistry(schema_version='1.0.0', registry_id='ds-reg', created_at='2026-08-31T00:00:00+00:00')
    identity = _identity('GAZP', 'uid-gazp')
    manifest = _manifest()
    reg.register(manifest)
    ingress = ResearchIngressGate(reg)
    decision = ingress.require_verified_dataset(identity=identity, dataset=manifest, dataset_id='ds-1')
    assert decision.allowed
    ctx = VerifiedResearchContext(
        schema_version='1.0.0', context_id='ctx-1', experiment_id='exp-1', dataset_id='ds-1', dataset_manifest=manifest, instrument_identity=identity,
        requested_horizon='365d', resolved_horizon='365d', timeframe='1h', strategy_id='strat-1', strategy_version='1.0.0', strategy_hash='hash', compatibility_result='PASS',
        research_policy_version='1.0.0', cost_model_version='1.0.0', ingress_receipt='receipt-1', dataset_checksum='xform', identity_hash='idhash', identity_version='1', created_at='2026-08-31T00:00:00+00:00'
    )
    exp = ExperimentManifest(
        schema_version='1.0.0', experiment_id='exp-1', created_at='2026-08-31T00:00:00+00:00', git_commit='abc', code_version='abc', research_policy_version='1.0.0', validation_policy_version='1.0.0', cost_model_version='1.0.0', dataset_ids=['ds-1'], strategy_id='strat-1', strategy_code_hash='hash', parameter_space={'n': 1}, candidate_manifest={'instrument_ids': ['uid-gazp']}, horizon_semantics={'requested': ['365d'], 'resolved': ['365d']}, acceptance_criteria={'pass': True}, campaign_budget=1, preregistration_hash='phash'
    )
    auth = authorize_scientific_evidence(context=ctx, experiment_manifest=exp, dataset_checksum='xform')
    assert auth.allowed


def test_context_missing_receipt_blocks_evidence():
    identity = _identity('GAZP', 'uid-gazp')
    manifest = _manifest()
    ctx = VerifiedResearchContext(
        schema_version='1.0.0', context_id='ctx-1', experiment_id='exp-1', dataset_id='ds-1', dataset_manifest=manifest, instrument_identity=identity,
        requested_horizon='365d', resolved_horizon='365d', timeframe='1h', strategy_id='strat-1', strategy_version='1.0.0', strategy_hash='hash', compatibility_result='PASS',
        research_policy_version='1.0.0', cost_model_version='1.0.0', ingress_receipt='', dataset_checksum='xform', identity_hash='idhash', identity_version='1', created_at='2026-08-31T00:00:00+00:00'
    )
    exp = ExperimentManifest(
        schema_version='1.0.0', experiment_id='exp-1', created_at='2026-08-31T00:00:00+00:00', git_commit='abc', code_version='abc', research_policy_version='1.0.0', validation_policy_version='1.0.0', cost_model_version='1.0.0', dataset_ids=['ds-1'], strategy_id='strat-1', strategy_code_hash='hash', parameter_space={'n': 1}, candidate_manifest={'instrument_ids': ['uid-gazp']}, horizon_semantics={'requested': ['365d'], 'resolved': ['365d']}, acceptance_criteria={'pass': True}, campaign_budget=1, preregistration_hash='phash'
    )
    auth = authorize_scientific_evidence(context=ctx, experiment_manifest=exp, dataset_checksum='xform')
    assert not auth.allowed
    assert auth.code == 'BLOCKED_NO_CONTEXT'

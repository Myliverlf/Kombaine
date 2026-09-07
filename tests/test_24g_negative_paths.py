from core.candidates.registry import CandidateRecord, CandidateRegistry
from core.data.ingress_gate import ResearchIngressGate
from core.data.registry import DatasetRegistry
from core.data.manifest import DatasetManifest
from core.evidence.behavioral import EvidenceClusterRegistry
from core.experiments.manifest import ExperimentManifest
from core.instruments.identity import InstrumentIdentity
from core.instruments.registry import InstrumentRegistry
from core.research.context import VerifiedResearchContext
from core.research.evidence_gate import authorize_scientific_evidence


def _identity(uid='uid-gazp', source='provider-native'):
    return InstrumentIdentity(schema_version='1.0.0', canonical_symbol='GAZP', provider='tinkoff', provider_instrument_uid=uid, figi='FIGI-GAZP', ticker='GAZP', class_code='TQBR', instrument_type='equity', exchange='MOEX', currency='RUB', lot_size=10, underlying_uid=None, underlying_symbol=None, is_derivative=False, identity_source=source, identity_verified_at='2026-08-31T00:00:00+00:00', identity_verification_method='provider-api')


def _manifest(dataset_id='ds-1', provenance='VERIFIED', identity='VERIFIED', integrity='PASS'):
    return DatasetManifest(schema_version='1.0.0', dataset_id=dataset_id, instrument_identity_id='GAZP', provider='tinkoff', source_endpoint='provider-native', requested_start='2025-01-01T00:00:00+00:00', requested_end='2025-12-31T23:59:59+00:00', actual_start='2025-01-01T00:00:00+00:00', actual_end='2025-12-31T23:59:59+00:00', actual_coverage_days=365, timeframe='1h', bar_count=100, timezone='UTC', session_calendar='MOEX', missing_bar_summary='none', raw_data_checksum='raw', transformed_data_checksum='xform', acquisition_timestamp='2026-08-31T00:00:00+00:00', acquisition_code_commit='abc', transformation_pipeline='pipe', transformation_parameters='{}', provenance_status=provenance, integrity_status=integrity, identity_status=identity, eligibility_status='ELIGIBLE')


def test_negative_paths_blocked():
    ds_reg = DatasetRegistry(schema_version='1.0.0', registry_id='ds-reg', created_at='2026-08-31T00:00:00+00:00')
    gate = ResearchIngressGate(ds_reg)
    assert not gate.block_direct_ingest('GAZP').allowed
    assert not gate.block_direct_ingest('/tmp/x.csv').allowed
    class Dummy: columns = ['x']
    assert not gate.block_direct_ingest(Dummy()).allowed
    assert not gate.require_verified_dataset(identity=_identity(uid='file:GAZP', source='file:GAZP'), dataset=_manifest(), dataset_id='ds-1').allowed

    reg = InstrumentRegistry(schema_version='1.0.0', registry_id='inst-reg', created_at='2026-08-31T00:00:00+00:00')
    assert reg.register(_identity(uid='uid-gazp'), verification_source='provider', verification_method='api', verification_timestamp='2026-08-31T00:00:00+00:00', status='VERIFIED')
    bad_ds = _manifest(provenance='QUARANTINED')
    ds_reg.register(bad_ds)
    assert not gate.require_verified_dataset(identity=_identity(uid='uid-gazp'), dataset=bad_ds, dataset_id='ds-1').allowed

    ctx = VerifiedResearchContext(schema_version='1.0.0', context_id='ctx-1', experiment_id='exp-1', dataset_id='ds-1', dataset_manifest=bad_ds, instrument_identity=_identity(uid='uid-gazp'), requested_horizon='1095d', resolved_horizon='365d', timeframe='1h', strategy_id='s', strategy_version='1', strategy_hash='h', compatibility_result='PASS', research_policy_version='1.0.0', cost_model_version='1.0.0', ingress_receipt='receipt', dataset_checksum='xform', identity_hash='idh', identity_version='1', created_at='2026-08-31T00:00:00+00:00')
    exp = ExperimentManifest(schema_version='1.0.0', experiment_id='exp-1', created_at='2026-08-31T00:00:00+00:00', git_commit='abc', code_version='abc', research_policy_version='1.0.0', validation_policy_version='1.0.0', cost_model_version='1.0.0', dataset_ids=['ds-1'], strategy_id='s', strategy_code_hash='h', parameter_space={}, candidate_manifest={}, horizon_semantics={'requested': ['1095d'], 'resolved': ['365d']}, acceptance_criteria={}, campaign_budget=1, preregistration_hash='phash')
    auth = authorize_scientific_evidence(context=ctx, experiment_manifest=exp, dataset_checksum='xform')
    assert not auth.allowed
    assert auth.code in {'BLOCKED_UNVERIFIED_DATASET', 'BLOCKED_EXPERIMENT_MANIFEST'}
    beh = EvidenceClusterRegistry(schema_version='1.0.0', registry_id='beh-reg', created_at='2026-08-31T00:00:00+00:00')
    cl = beh.assign('cand-1')
    regc = CandidateRegistry(schema_version='1.0.0', registry_id='cand-reg', created_at='2026-08-31T00:00:00+00:00')
    try:
        regc.register(CandidateRecord(candidate_id='cand-1', experiment_id='exp-1', campaign_id='camp-1', strategy_id='s', strategy_version='1', parameters={}, dataset_ids=['ds-1'], instrument_ids=['uid-gazp'], policy_version='1.0.0', cost_model_version='1.0.0', result_hashes=['r'], ingress_receipt_ids=[], evidence_authorization_ids=[], behavioral_cluster_id=cl.cluster_id, scientific_validity='VALID', qualification_status='QUALIFIED', quarantine_status='NONE', forward_eligibility='NO', promotion_state='DISCOVERY', human_approval_state='NONE', timestamps={'created_at': '2026-08-31T00:00:00+00:00'}), evidence_auth=auth)
    except Exception:
        pass
    else:
        raise AssertionError('unauthorized candidate write should fail')

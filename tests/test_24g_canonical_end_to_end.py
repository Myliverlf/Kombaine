from core.candidates.registry import CandidateRecord, CandidateRegistry
from core.data.manifest import DatasetManifest
from core.data.registry import DatasetRegistry
from core.data.ingress_gate import ResearchIngressGate
from core.evidence.behavioral import EvidenceClusterRegistry
from core.experiments.manifest import ExperimentManifest
from core.experiments.registry import ExperimentRegistry
from core.instruments.identity import InstrumentIdentity
from core.instruments.registry import InstrumentRegistry
from core.research.context import VerifiedResearchContext
from core.research.evidence_gate import authorize_scientific_evidence


def _identity():
    return InstrumentIdentity(
        schema_version='1.0.0', canonical_symbol='GAZP', provider='tinkoff', provider_instrument_uid='uid-gazp', figi='FIGI-GAZP', ticker='GAZP',
        class_code='TQBR', instrument_type='equity', exchange='MOEX', currency='RUB', lot_size=10,
        underlying_uid=None, underlying_symbol=None, is_derivative=False, identity_source='provider-native', identity_verified_at='2026-08-31T00:00:00+00:00',
        identity_verification_method='provider-api'
    )


def _manifest():
    return DatasetManifest(
        schema_version='1.0.0', dataset_id='ds-verified', instrument_identity_id='GAZP', provider='tinkoff', source_endpoint='provider-native',
        requested_start='2025-01-01T00:00:00+00:00', requested_end='2025-12-31T23:59:59+00:00', actual_start='2025-01-01T00:00:00+00:00', actual_end='2025-12-31T23:59:59+00:00',
        actual_coverage_days=365, timeframe='1h', bar_count=100, timezone='UTC', session_calendar='MOEX', missing_bar_summary='none', raw_data_checksum='raw', transformed_data_checksum='xform',
        acquisition_timestamp='2026-08-31T00:00:00+00:00', acquisition_code_commit='abc', transformation_pipeline='pipe', transformation_parameters='{}',
        provenance_status='VERIFIED', integrity_status='PASS', identity_status='VERIFIED', eligibility_status='ELIGIBLE'
    )


def test_true_canonical_positive_path_passes():
    inst_reg = InstrumentRegistry(schema_version='1.0.0', registry_id='inst-reg', created_at='2026-08-31T00:00:00+00:00')
    identity = _identity()
    inst = inst_reg.register(identity, verification_source='provider', verification_method='api', verification_timestamp='2026-08-31T00:00:00+00:00', status='VERIFIED')

    ds_reg = DatasetRegistry(schema_version='1.0.0', registry_id='ds-reg', created_at='2026-08-31T00:00:00+00:00')
    manifest = _manifest()
    ds_reg.register(manifest)
    ingress = ResearchIngressGate(ds_reg)
    decision = ingress.require_verified_dataset(identity=identity, dataset=manifest, dataset_id='ds-verified')
    assert decision.allowed

    ctx = VerifiedResearchContext(
        schema_version='1.0.0', context_id='ctx-1', experiment_id='exp-1', dataset_id='ds-verified', dataset_manifest=manifest, instrument_identity=identity,
        requested_horizon='365d', resolved_horizon='365d', timeframe='1h', strategy_id='strat-1', strategy_version='1.0.0', strategy_hash='hash', compatibility_result='PASS',
        research_policy_version='1.0.0', cost_model_version='1.0.0', ingress_receipt='receipt-1', dataset_checksum='xform', identity_hash=inst.identity_hash, identity_version='1', created_at='2026-08-31T00:00:00+00:00'
    )
    exp_manifest = ExperimentManifest(
        schema_version='1.0.0', experiment_id='exp-1', created_at='2026-08-31T00:00:00+00:00', git_commit='abc', code_version='abc', research_policy_version='1.0.0', validation_policy_version='1.0.0', cost_model_version='1.0.0', dataset_ids=['ds-verified'], strategy_id='strat-1', strategy_code_hash='hash', parameter_space={'lookback': 10}, candidate_manifest={'instrument_ids': ['uid-gazp']}, horizon_semantics={'requested': ['365d'], 'resolved': ['365d']}, acceptance_criteria={'pass': True}, campaign_budget=1, preregistration_hash='prereg-hash'
    )
    exp_reg = ExperimentRegistry(schema_version='1.0.0', registry_id='exp-reg', created_at='2026-08-31T00:00:00+00:00')
    exp = exp_reg.register(exp_manifest, campaign_id='camp-1', strategy_version='1.0.0', result_ids=['result-1'], gate_receipt_ids=['receipt-1'])
    auth = authorize_scientific_evidence(context=ctx, experiment_manifest=exp_manifest, dataset_checksum='xform')
    assert auth.allowed

    beh = EvidenceClusterRegistry(schema_version='1.0.0', registry_id='beh-reg', created_at='2026-08-31T00:00:00+00:00')
    cluster = beh.assign('cand-1', features={'returns_similarity': 0.99})
    cand_reg = CandidateRegistry(schema_version='1.0.0', registry_id='cand-reg', created_at='2026-08-31T00:00:00+00:00')
    cand = CandidateRecord(
        candidate_id='cand-1', experiment_id='exp-1', campaign_id='camp-1', strategy_id='strat-1', strategy_version='1.0.0', parameters={'lookback': 10}, dataset_ids=['ds-verified'], instrument_ids=['uid-gazp'], policy_version='1.0.0', cost_model_version='1.0.0', result_hashes=['result-1'], ingress_receipt_ids=['receipt-1'], evidence_authorization_ids=['auth-1'], behavioral_cluster_id=cluster.cluster_id, scientific_validity='VALID', qualification_status='QUALIFIED', quarantine_status='NONE', forward_eligibility='NO', promotion_state='DISCOVERY', human_approval_state='NONE', timestamps={'created_at': '2026-08-31T00:00:00+00:00'}
    )
    cand_reg.register(cand, evidence_auth=auth)
    assert cand_reg.get('cand-1').candidate_id == 'cand-1'

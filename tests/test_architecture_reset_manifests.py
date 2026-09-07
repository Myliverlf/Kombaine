from core.data.manifest import DatasetManifest, validate_dataset_manifest
from core.experiments.manifest import CampaignManifest, ExperimentManifest, validate_experiment_manifest
from core.policies import load_policy, policy_hash


def test_dataset_manifest_requires_verified_provenance():
    manifest = DatasetManifest(
        schema_version='1',
        dataset_id='ds-1',
        instrument_identity_id='ii-1',
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
        raw_data_checksum='abc',
        transformed_data_checksum='def',
        acquisition_timestamp='2026-08-30T00:00:00+00:00',
        acquisition_code_commit='deadbeef',
        transformation_pipeline='pipe',
        transformation_parameters='{}',
        provenance_status='VERIFIED',
    )
    validate_dataset_manifest(manifest)


def test_dataset_manifest_accepts_quarantined_provenance_but_registry_will_gate_it():
    manifest = DatasetManifest(
        schema_version='1',
        dataset_id='ds-1',
        instrument_identity_id='ii-1',
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
        raw_data_checksum='abc',
        transformed_data_checksum='def',
        acquisition_timestamp='2026-08-30T00:00:00+00:00',
        acquisition_code_commit='deadbeef',
        transformation_pipeline='pipe',
        transformation_parameters='{}',
        provenance_status='QUARANTINED',
        integrity_status='FAIL',
        identity_status='NOT_PROVEN',
        eligibility_status='QUARANTINED',
        quarantine_reason='test',
    )
    validate_dataset_manifest(manifest)


def test_experiment_manifest_versioned_and_nonempty():
    manifest = ExperimentManifest(
        schema_version='1',
        experiment_id='exp-1',
        created_at='2026-08-30T00:00:00+00:00',
        git_commit='abc1234',
        code_version='1.0.0',
        research_policy_version='1.0.0',
        validation_policy_version='1.0.0',
        cost_model_version='1.0.0',
        dataset_ids=['ds-1'],
        strategy_id='GAZP__bollinger_reversion__1h__23o01',
        strategy_code_hash='hash',
        parameter_space={'lookback': [10, 20]},
        candidate_manifest={'candidate_id': 'x'},
        horizon_semantics={'365d': 'exact'},
        acceptance_criteria={'min_trades': 8},
        campaign_budget=21,
        preregistration_hash='hash2',
    )
    validate_experiment_manifest(manifest)
    assert policy_hash(load_policy())


def test_campaign_manifest_dataclass_imports():
    CampaignManifest(
        schema_version='1',
        campaign_id='cmp-1',
        created_at='2026-08-30T00:00:00+00:00',
        assets=['GAZP', 'SBER', 'LKOH'],
        asset_quotas={'GAZP': 7, 'SBER': 7, 'LKOH': 7},
        strategy_families=['bollinger_reversion'],
        family_quotas={'bollinger_reversion': 7},
        timeframes=['15m', '1h'],
        timeframe_quotas={'15m': 10, '1h': 11},
        horizons=[60, 90, 180, 365],
        parameter_budgets={'bollinger_reversion': 3},
        total_candidate_budget=21,
        dataset_eligibility_policy={'provenance_status': 'VERIFIED'},
        assignment_rule='fixed',
        behavioral_dedupe_policy='cluster-before-forward',
        replacement_rule='fail-closed',
        stopping_rule='budgeted',
    )

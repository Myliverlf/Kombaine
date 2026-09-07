import pandas as pd

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'code'))

from core.experiments.manifest import ExperimentManifest
from core.instruments.identity import InstrumentIdentity
from core.research.context import VerifiedResearchContext
from qualification_campaign import run_single_backtest, get_synthetic_spec


def _identity():
    return InstrumentIdentity(
        schema_version='1.0.0', canonical_symbol='GAZP', provider='tinkoff', provider_instrument_uid='uid-gazp', figi='FIGI-GAZP', ticker='GAZP',
        class_code='TQBR', instrument_type='equity', exchange='MOEX', currency='RUB', lot_size=10,
        underlying_uid=None, underlying_symbol=None, is_derivative=False, identity_source='provider-native', identity_verified_at='2026-08-31T00:00:00+00:00',
        identity_verification_method='provider-api'
    )


def test_canonical_qualification_rejects_missing_context():
    df = pd.DataFrame({'open': [1.0], 'high': [1.0], 'low': [1.0], 'close': [1.0], 'volume': [1.0]})
    spec = get_synthetic_spec('GAZP')
    try:
        run_single_backtest(df=df, spec=spec, strategy_name='s', params={'x': 1}, timeframe='1h', cash=1_000_000, research_context=None)
    except RuntimeError as exc:
        assert 'BLOCKED_NO_CONTEXT' in str(exc)
    else:
        raise AssertionError('canonical scientific qualification must reject missing VerifiedResearchContext')


def test_canonical_qualification_accepts_valid_context_only_if_authorized():
    identity = _identity()
    # lightweight context object with validate() + required fields; canonical helper must accept it
    class Manifest:
        provenance_status = 'VERIFIED'
        integrity_status = 'PASS'
        identity_status = 'VERIFIED'
        dataset_id = 'ds-verified'
        instrument_identity_id = 'GAZP'
        def validate(self):
            return None
    ctx = VerifiedResearchContext(
        schema_version='1.0.0', context_id='ctx-1', experiment_id='exp-1', dataset_id='ds-verified', dataset_manifest=Manifest(), instrument_identity=identity,
        requested_horizon='365d', resolved_horizon='365d', timeframe='1h', strategy_id='s', strategy_version='1.0.0', strategy_hash='hash', compatibility_result='PASS',
        research_policy_version='1.0.0', cost_model_version='1.0.0', ingress_receipt='receipt-1', dataset_checksum='xform', identity_hash='idhash', identity_version='1', created_at='2026-08-31T00:00:00+00:00'
    )
    exp = ExperimentManifest(schema_version='1.0.0', experiment_id='exp-1', created_at='2026-08-31T00:00:00+00:00', git_commit='abc', code_version='abc', research_policy_version='1.0.0', validation_policy_version='1.0.0', cost_model_version='1.0.0', dataset_ids=['ds-verified'], strategy_id='s', strategy_code_hash='hash', parameter_space={}, candidate_manifest={}, horizon_semantics={'requested': ['365d'], 'resolved': ['365d']}, acceptance_criteria={}, campaign_budget=1, preregistration_hash='phash')
    result = run_single_backtest(df=pd.DataFrame({'open': [1.0], 'high': [1.0], 'low': [1.0], 'close': [1.0], 'volume': [1.0]}), spec=get_synthetic_spec('GAZP'), strategy_name='s', params={'x': 1}, timeframe='1h', cash=1_000_000, research_context=ctx, experiment_manifest=exp)
    assert isinstance(result, dict)
    assert 'BLOCKED_NO_CONTEXT' not in str(result)

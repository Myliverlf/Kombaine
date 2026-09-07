from core.instruments.identity import InstrumentIdentity, validate_instrument_identity


def test_instrument_identity_validation_passes_for_complete_identity():
    identity = InstrumentIdentity(
        schema_version='1',
        canonical_symbol='GAZP',
        provider='futures_lab',
        provider_instrument_uid='file:GAZP',
        figi=None,
        ticker='GAZP',
        class_code='FILE',
        instrument_type='equity_or_registry_artifact',
        exchange='FILE',
        currency='RUB',
        lot_size=1,
        underlying_uid=None,
        underlying_symbol=None,
        is_derivative=False,
        identity_source='/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data/GAZP_365d_1h_continuous.csv',
        identity_verified_at='2026-08-30T00:00:00+00:00',
        identity_verification_method='forensic-audit',
    )
    validate_instrument_identity(identity)


def test_instrument_identity_rejects_missing_provider_uid():
    identity = InstrumentIdentity(
        schema_version='1',
        canonical_symbol='SBER',
        provider='futures_lab',
        provider_instrument_uid='',
        figi=None,
        ticker='SBER',
        class_code='FILE',
        instrument_type='equity_or_registry_artifact',
        exchange='FILE',
        currency='RUB',
        lot_size=1,
        underlying_uid=None,
        underlying_symbol=None,
        is_derivative=False,
        identity_source='source',
        identity_verified_at='2026-08-30T00:00:00+00:00',
        identity_verification_method='forensic-audit',
    )
    try:
        validate_instrument_identity(identity)
    except Exception as exc:
        assert 'Missing identity fields' in str(exc)
    else:
        raise AssertionError('expected validation failure')

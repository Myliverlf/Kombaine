from pathlib import Path

from core.instruments.identity import InstrumentIdentity
from core.instruments.registry import InstrumentRegistry


def _identity(symbol: str, uid: str, instrument_type: str, derivative: bool, underlying_symbol=None):
    return InstrumentIdentity(
        schema_version='1.0.0',
        canonical_symbol=symbol,
        provider='tinkoff',
        provider_instrument_uid=uid,
        figi=f'FIGI-{symbol}',
        ticker=symbol,
        class_code='TQBR' if instrument_type == 'equity' else 'FUT',
        instrument_type=instrument_type,
        exchange='MOEX',
        currency='RUB',
        lot_size=10,
        underlying_uid='under-1' if derivative else None,
        underlying_symbol=underlying_symbol,
        is_derivative=derivative,
        identity_source='provider-native',
        identity_verified_at='2026-08-31T00:00:00+00:00',
        identity_verification_method='provider-api',
        identity_version='1',
    )


def test_registry_registers_verified_and_quarantined_records(tmp_path: Path):
    reg = InstrumentRegistry(schema_version='1.0.0', registry_id='inst-reg', created_at='2026-08-31T00:00:00+00:00')
    eq = reg.register(_identity('GAZP', 'uid-gazp', 'equity', False), verification_source='provider', verification_method='api', verification_timestamp='2026-08-31T00:00:00+00:00', status='VERIFIED')
    fut = reg.register(_identity('GAZPF', 'uid-gazpf', 'futures', True, underlying_symbol='GAZP'), verification_source='provider', verification_method='api', verification_timestamp='2026-08-31T00:00:00+00:00', status='QUARANTINED', quarantine_reason='derivative test')
    assert eq.status == 'VERIFIED'
    assert fut.status == 'QUARANTINED'
    assert reg.require_verified(eq.identity_id).identity_id == eq.identity_id
    out = tmp_path / 'instrument_registry.json'
    reg.save(out)
    loaded = InstrumentRegistry.load(out)
    assert loaded.get(eq.identity_id).identity_hash == eq.identity_hash


def test_registry_rejects_file_alias_identity():
    reg = InstrumentRegistry(schema_version='1.0.0', registry_id='inst-reg', created_at='2026-08-31T00:00:00+00:00')
    bad = _identity('GAZP', 'file:GAZP', 'equity', False)
    try:
        reg.register(bad, verification_source='file', verification_method='alias', verification_timestamp='2026-08-31T00:00:00+00:00', status='VERIFIED')
    except Exception as exc:
        assert 'FILE_ALIAS' in str(exc) or 'identity' in str(exc).lower()
    else:
        raise AssertionError('expected file alias rejection')

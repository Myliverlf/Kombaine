from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


class IdentityValidationError(ValueError):
    pass


@dataclass(frozen=True)
class InstrumentIdentity:
    schema_version: str
    canonical_symbol: str
    provider: str
    provider_instrument_uid: str
    figi: Optional[str]
    ticker: str
    class_code: str
    instrument_type: str
    exchange: str
    currency: str
    lot_size: int
    underlying_uid: Optional[str]
    underlying_symbol: Optional[str]
    is_derivative: bool
    identity_source: str
    identity_verified_at: str
    identity_verification_method: str
    identity_version: str = "1"

    def validate(self) -> None:
        validate_instrument_identity(self)


def validate_instrument_identity(identity: InstrumentIdentity) -> None:
    required = {
        "schema_version": identity.schema_version,
        "canonical_symbol": identity.canonical_symbol,
        "provider": identity.provider,
        "provider_instrument_uid": identity.provider_instrument_uid,
        "ticker": identity.ticker,
        "class_code": identity.class_code,
        "instrument_type": identity.instrument_type,
        "exchange": identity.exchange,
        "currency": identity.currency,
        "identity_source": identity.identity_source,
        "identity_verified_at": identity.identity_verified_at,
        "identity_verification_method": identity.identity_verification_method,
    }
    missing = [k for k, v in required.items() if not str(v).strip()]
    if missing:
        raise IdentityValidationError(f"Missing identity fields: {missing}")
    if identity.lot_size <= 0:
        raise IdentityValidationError("lot_size must be positive")
    if not identity.identity_verified_at.endswith("+00:00") and identity.identity_verified_at != "unknown":
        # allow UTC stamps or explicit unknown in tests/mock flows
        raise IdentityValidationError("identity_verified_at must be UTC or unknown")
    if identity.canonical_symbol != identity.ticker:
        # canonical symbol may differ only if provider mapping explicitly says so
        pass

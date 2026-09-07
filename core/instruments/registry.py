from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Optional
import hashlib
import json

from .identity import InstrumentIdentity, IdentityValidationError, validate_instrument_identity


class InstrumentRegistryError(ValueError):
    pass


_ALLOWED_STATUSES = {"VERIFIED", "NOT_PROVEN", "AMBIGUOUS", "WRONG_INSTRUMENT", "QUARANTINED", "INVALID"}


@dataclass(frozen=True)
class RegistryIdentityRecord:
    schema_version: str
    identity_id: str
    identity_hash: str
    instrument: InstrumentIdentity
    verification_source: str
    verification_method: str
    verification_timestamp: str
    status: str
    quarantine_reason: Optional[str] = None
    parent_identity_id: Optional[str] = None

    def validate(self) -> None:
        validate_instrument_identity(self.instrument)
        if self.status not in _ALLOWED_STATUSES:
            raise InstrumentRegistryError(f"invalid status: {self.status}")
        if not self.identity_id.strip() or not self.identity_hash.strip():
            raise InstrumentRegistryError("identity_id/hash required")


@dataclass
class InstrumentRegistry:
    schema_version: str
    registry_id: str
    created_at: str
    identities: Dict[str, RegistryIdentityRecord] = field(default_factory=dict)

    def _hash(self, identity: InstrumentIdentity) -> str:
        payload = json.dumps(asdict(identity), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def register(self, identity: InstrumentIdentity, *, verification_source: str, verification_method: str, verification_timestamp: str, status: str, quarantine_reason: Optional[str] = None, parent_identity_id: Optional[str] = None) -> RegistryIdentityRecord:
        validate_instrument_identity(identity)
        if identity.provider_instrument_uid.startswith('file:') or identity.identity_source.startswith('file:'):
            if status == 'VERIFIED':
                raise InstrumentRegistryError('FILE_ALIAS_CANNOT_BE_VERIFIED')
        identity_id = f"{identity.provider}:{identity.canonical_symbol}:{identity.provider_instrument_uid}:{identity.identity_version}"
        identity_hash = self._hash(identity)
        record = RegistryIdentityRecord(
            schema_version=self.schema_version,
            identity_id=identity_id,
            identity_hash=identity_hash,
            instrument=identity,
            verification_source=verification_source,
            verification_method=verification_method,
            verification_timestamp=verification_timestamp,
            status=status,
            quarantine_reason=quarantine_reason,
            parent_identity_id=parent_identity_id,
        )
        record.validate()
        existing = self.identities.get(identity_id)
        if existing and existing != record:
            raise InstrumentRegistryError(f"IMMUTABLE_IDENTITY_CONFLICT: {identity_id}")
        self.identities[identity_id] = record
        return record

    def get(self, identity_id: str) -> RegistryIdentityRecord:
        try:
            return self.identities[identity_id]
        except KeyError as exc:
            raise InstrumentRegistryError(f"UNKNOWN_IDENTITY: {identity_id}") from exc

    def require_verified(self, identity_id: str) -> RegistryIdentityRecord:
        rec = self.get(identity_id)
        if rec.status != "VERIFIED":
            raise InstrumentRegistryError(f"UNVERIFIED_IDENTITY: {identity_id}")
        return rec

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "registry_id": self.registry_id,
            "created_at": self.created_at,
            "identities": {k: {**asdict(v), "instrument": asdict(v.instrument)} for k, v in self.identities.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InstrumentRegistry":
        reg = cls(schema_version=data["schema_version"], registry_id=data["registry_id"], created_at=data["created_at"])
        for identity_id, payload in data.get("identities", {}).items():
            inst = InstrumentIdentity(**payload["instrument"])
            reg.identities[identity_id] = RegistryIdentityRecord(
                schema_version=payload["schema_version"],
                identity_id=payload["identity_id"],
                identity_hash=payload["identity_hash"],
                instrument=inst,
                verification_source=payload["verification_source"],
                verification_method=payload["verification_method"],
                verification_timestamp=payload["verification_timestamp"],
                status=payload["status"],
                quarantine_reason=payload.get("quarantine_reason"),
                parent_identity_id=payload.get("parent_identity_id"),
            )
        return reg

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> "InstrumentRegistry":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

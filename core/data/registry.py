from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Optional
import json

from core.data.manifest import DatasetManifest, DatasetManifestError, validate_dataset_manifest
from core.instruments.identity import InstrumentIdentity, IdentityValidationError, validate_instrument_identity


class DatasetRegistryError(ValueError):
    pass


@dataclass
class DatasetRegistry:
    schema_version: str
    registry_id: str
    created_at: str
    datasets: Dict[str, DatasetManifest] = field(default_factory=dict)
    verified_count: int = 0
    quarantined_count: int = 0
    invalid_count: int = 0

    def register(self, manifest: DatasetManifest) -> None:
        validate_dataset_manifest(manifest)
        if manifest.dataset_id in self.datasets:
            existing = self.datasets[manifest.dataset_id]
            if existing != manifest:
                raise DatasetRegistryError(f"IMMUTABLE_DATASET_CONFLICT: {manifest.dataset_id}")
            return
        self.datasets[manifest.dataset_id] = manifest
        if manifest.provenance_status == 'VERIFIED' and manifest.identity_status == 'VERIFIED' and manifest.integrity_status in {'PASS', 'VERIFIED'}:
            self.verified_count += 1
        elif manifest.provenance_status == 'QUARANTINED' or manifest.identity_status != 'VERIFIED':
            self.quarantined_count += 1
        else:
            self.invalid_count += 1

    def get(self, dataset_id: str) -> DatasetManifest:
        try:
            return self.datasets[dataset_id]
        except KeyError as exc:
            raise DatasetRegistryError(f"UNKNOWN_DATASET_ID: {dataset_id}") from exc

    def require_verified(self, dataset_id: str) -> DatasetManifest:
        manifest = self.get(dataset_id)
        if manifest.provenance_status != 'VERIFIED':
            raise DatasetRegistryError(f"DATASET_PROVENANCE_BLOCKED: {dataset_id}")
        if manifest.identity_status != 'VERIFIED':
            raise DatasetRegistryError(f"DATASET_IDENTITY_BLOCKED: {dataset_id}")
        if manifest.integrity_status not in {'PASS', 'VERIFIED'}:
            raise DatasetRegistryError(f"DATASET_INTEGRITY_BLOCKED: {dataset_id}")
        return manifest

    def to_dict(self) -> dict:
        return {
            'schema_version': self.schema_version,
            'registry_id': self.registry_id,
            'created_at': self.created_at,
            'verified_count': self.verified_count,
            'quarantined_count': self.quarantined_count,
            'invalid_count': self.invalid_count,
            'datasets': {k: v.__dict__ for k, v in self.datasets.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'DatasetRegistry':
        reg = cls(
            schema_version=data['schema_version'],
            registry_id=data['registry_id'],
            created_at=data['created_at'],
        )
        for dataset_id, manifest_data in data.get('datasets', {}).items():
            reg.datasets[dataset_id] = DatasetManifest(**manifest_data)
        reg.verified_count = data.get('verified_count', 0)
        reg.quarantined_count = data.get('quarantined_count', 0)
        reg.invalid_count = data.get('invalid_count', 0)
        return reg

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding='utf-8')
        return path

    @classmethod
    def load(cls, path: Path) -> 'DatasetRegistry':
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('schema_version') not in {'1', '1.0', '1.0.0'}:
            raise DatasetRegistryError(f"UNKNOWN_SCHEMA_VERSION: {data.get('schema_version')}")
        return cls.from_dict(data)


def build_verified_dataset_manifest(*, dataset_id: str, instrument_identity: InstrumentIdentity, provider: str, source_endpoint: str, requested_start: str, requested_end: str, actual_start: str, actual_end: str, actual_coverage_days: int, timeframe: str, bar_count: int, timezone: str, session_calendar: str, raw_data_checksum: str, transformed_data_checksum: str, acquisition_timestamp: str, acquisition_code_commit: str, transformation_pipeline: str, transformation_parameters: str, provenance_status: str = 'VERIFIED', integrity_status: str = 'PASS', identity_status: str = 'VERIFIED', eligibility_status: str = 'ELIGIBLE', parent_dataset_ids: Optional[Iterable[str]] = None, continuous_series: bool = False, continuous_method: Optional[str] = None, futures_contract_chain: Optional[str] = None, roll_rule: Optional[str] = None, adjustment_method: Optional[str] = None, quarantine_reason: Optional[str] = None, schema_version: str = '1.0.0') -> DatasetManifest:
    validate_instrument_identity(instrument_identity)
    manifest = DatasetManifest(
        schema_version=schema_version,
        dataset_id=dataset_id,
        instrument_identity_id=instrument_identity.canonical_symbol,
        provider=provider,
        source_endpoint=source_endpoint,
        requested_start=requested_start,
        requested_end=requested_end,
        actual_start=actual_start,
        actual_end=actual_end,
        actual_coverage_days=actual_coverage_days,
        timeframe=timeframe,
        bar_count=bar_count,
        timezone=timezone,
        session_calendar=session_calendar,
        missing_bar_summary='none',
        raw_data_checksum=raw_data_checksum,
        transformed_data_checksum=transformed_data_checksum,
        acquisition_timestamp=acquisition_timestamp,
        acquisition_code_commit=acquisition_code_commit,
        transformation_pipeline=transformation_pipeline,
        transformation_parameters=transformation_parameters,
        parent_dataset_ids=list(parent_dataset_ids or []),
        continuous_series=continuous_series,
        continuous_method=continuous_method,
        futures_contract_chain=futures_contract_chain,
        roll_rule=roll_rule,
        adjustment_method=adjustment_method,
        provenance_status=provenance_status,
        integrity_status=integrity_status,
        identity_status=identity_status,
        eligibility_status=eligibility_status,
        quarantine_reason=quarantine_reason,
    )
    validate_dataset_manifest(manifest)
    return manifest

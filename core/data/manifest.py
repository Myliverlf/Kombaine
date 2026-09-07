from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


class DatasetManifestError(ValueError):
    pass


@dataclass(frozen=True)
class DatasetManifest:
    schema_version: str
    dataset_id: str
    instrument_identity_id: str
    provider: str
    source_endpoint: str
    requested_start: str
    requested_end: str
    actual_start: str
    actual_end: str
    actual_coverage_days: int
    timeframe: str
    bar_count: int
    timezone: str
    session_calendar: str
    missing_bar_summary: str
    raw_data_checksum: str
    transformed_data_checksum: str
    acquisition_timestamp: str
    acquisition_code_commit: str
    transformation_pipeline: str
    transformation_parameters: str
    parent_dataset_ids: List[str] = field(default_factory=list)
    continuous_series: bool = False
    continuous_method: Optional[str] = None
    futures_contract_chain: Optional[str] = None
    roll_rule: Optional[str] = None
    adjustment_method: Optional[str] = None
    provenance_status: str = "PENDING"
    integrity_status: str = "PENDING"
    identity_status: str = "PENDING"
    eligibility_status: str = "PENDING"
    quarantine_reason: Optional[str] = None

    def validate(self) -> None:
        validate_dataset_manifest(self)


def validate_dataset_manifest(manifest: DatasetManifest) -> None:
    required = {
        "schema_version": manifest.schema_version,
        "dataset_id": manifest.dataset_id,
        "instrument_identity_id": manifest.instrument_identity_id,
        "provider": manifest.provider,
        "source_endpoint": manifest.source_endpoint,
        "actual_start": manifest.actual_start,
        "actual_end": manifest.actual_end,
        "timeframe": manifest.timeframe,
        "raw_data_checksum": manifest.raw_data_checksum,
        "transformed_data_checksum": manifest.transformed_data_checksum,
        "acquisition_timestamp": manifest.acquisition_timestamp,
        "acquisition_code_commit": manifest.acquisition_code_commit,
        "transformation_pipeline": manifest.transformation_pipeline,
        "transformation_parameters": manifest.transformation_parameters,
    }
    missing = [k for k, v in required.items() if not str(v).strip()]
    if missing:
        raise DatasetManifestError(f"Missing dataset fields: {missing}")
    if manifest.actual_coverage_days <= 0:
        raise DatasetManifestError("actual_coverage_days must be positive")
    if manifest.bar_count <= 0:
        raise DatasetManifestError("bar_count must be positive")
    allowed_prov = {"VERIFIED", "PARTIAL", "QUARANTINED", "INVALID", "PENDING"}
    allowed_id = {"VERIFIED", "NOT_PROVEN", "WRONG_INSTRUMENT", "PENDING"}
    allowed_integrity = {"PASS", "VERIFIED", "FAIL", "PENDING"}
    if manifest.provenance_status not in allowed_prov:
        raise DatasetManifestError(f"invalid provenance_status: {manifest.provenance_status}")
    if manifest.identity_status not in allowed_id:
        raise DatasetManifestError(f"invalid identity_status: {manifest.identity_status}")
    if manifest.integrity_status not in allowed_integrity:
        raise DatasetManifestError(f"invalid integrity_status: {manifest.integrity_status}")
    if manifest.eligibility_status not in {"ELIGIBLE", "QUARANTINED", "BLOCKED", "PENDING"}:
        raise DatasetManifestError(f"invalid eligibility_status: {manifest.eligibility_status}")

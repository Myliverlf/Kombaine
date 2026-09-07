from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from core.data.manifest import DatasetManifest
from core.instruments.identity import InstrumentIdentity


class ResearchContextError(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedResearchContext:
    schema_version: str
    context_id: str
    experiment_id: str
    dataset_id: str
    dataset_manifest: DatasetManifest
    instrument_identity: InstrumentIdentity
    requested_horizon: str
    resolved_horizon: str
    timeframe: str
    strategy_id: str
    strategy_version: str
    strategy_hash: str
    compatibility_result: str
    research_policy_version: str
    cost_model_version: str
    ingress_receipt: str
    dataset_checksum: str
    identity_hash: str
    identity_version: str
    created_at: str
    payload_metadata: dict[str, Any] | None = None

    def validate(self) -> None:
        if not self.schema_version.strip():
            raise ResearchContextError("schema_version required")
        if not self.context_id.strip():
            raise ResearchContextError("context_id required")
        if not self.ingress_receipt.strip():
            raise ResearchContextError("ingress_receipt required")
        self.dataset_manifest.validate()
        self.instrument_identity.validate()
        if self.dataset_manifest.dataset_id != self.dataset_id:
            raise ResearchContextError("dataset_id mismatch")
        if self.instrument_identity.canonical_symbol != self.dataset_manifest.instrument_identity_id:
            raise ResearchContextError("instrument/dataset identity mismatch")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["dataset_manifest"] = asdict(self.dataset_manifest)
        data["instrument_identity"] = asdict(self.instrument_identity)
        return data

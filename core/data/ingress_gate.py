from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from core.data.manifest import DatasetManifest
from core.data.registry import DatasetRegistry, DatasetRegistryError
from core.instruments.identity import InstrumentIdentity


class IngressGateError(ValueError):
    pass


@dataclass(frozen=True)
class IngressDecision:
    allowed: bool
    gate: str
    reason: str
    dataset_id: Optional[str] = None


class ResearchIngressGate:
    def __init__(self, registry: DatasetRegistry):
        self.registry = registry

    def _blocked(self, gate: str, reason: str, dataset_id: Optional[str] = None) -> IngressDecision:
        return IngressDecision(False, gate, reason, dataset_id=dataset_id)

    def require_verified_dataset(self, *, identity: Optional[InstrumentIdentity], dataset: Optional[DatasetManifest], dataset_id: Optional[str]) -> IngressDecision:
        if identity is None:
            return self._blocked('GATE_1', 'missing InstrumentIdentity', dataset_id)
        if identity.identity_version and identity.identity_version != '1':
            pass
        try:
            identity.validate()
        except Exception as exc:
            return self._blocked('GATE_2', f'InstrumentIdentity invalid: {exc}', dataset_id)
        if identity.provider_instrument_uid.startswith('file:') or identity.identity_source.startswith('file:'):
            return self._blocked('GATE_2', 'file alias cannot prove provider-native identity', dataset_id)
        if dataset is None and dataset_id is not None:
            try:
                dataset = self.registry.get(dataset_id)
            except DatasetRegistryError as exc:
                return self._blocked('GATE_3', str(exc), dataset_id)
        if dataset is None:
            return self._blocked('GATE_3', 'missing DatasetManifest', dataset_id)
        if dataset.provenance_status != 'VERIFIED':
            return self._blocked('GATE_4', f'provenance_status={dataset.provenance_status}', dataset.dataset_id)
        if dataset.identity_status != 'VERIFIED':
            return self._blocked('GATE_5', f'identity_status={dataset.identity_status}', dataset.dataset_id)
        if dataset.integrity_status not in {'PASS', 'VERIFIED'}:
            return self._blocked('GATE_6', f'integrity_status={dataset.integrity_status}', dataset.dataset_id)
        if dataset.dataset_id not in self.registry.datasets:
            return self._blocked('GATE_3', 'dataset not registered', dataset.dataset_id)
        return IngressDecision(True, 'PASS', 'verified dataset ingress', dataset.dataset_id)

    def block_direct_ingest(self, candidate: Any) -> IngressDecision:
        if isinstance(candidate, str):
            if candidate.endswith('.csv') or candidate.startswith('/'):
                return self._blocked('GATE_DIRECT_INPUT', 'raw CSV/file-path ingestion blocked')
            return self._blocked('GATE_DIRECT_INPUT', 'bare ticker/symbol ingestion blocked')
        if hasattr(candidate, 'columns'):
            return self._blocked('GATE_DIRECT_INPUT', 'raw DataFrame ingestion blocked')
        return self._blocked('GATE_DIRECT_INPUT', 'unsupported direct research input blocked')

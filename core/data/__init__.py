"""Dataset and provider contracts."""

from .manifest import DatasetManifest, DatasetManifestError, validate_dataset_manifest
from .registry import DatasetRegistry, DatasetRegistryError, build_verified_dataset_manifest
from .provider import AcquisitionRequest, AcquisitionResult, MarketDataProvider, MockTinkoffProvider
from .ingress_gate import ResearchIngressGate, IngressDecision, IngressGateError

__all__ = [
    "DatasetManifest",
    "DatasetManifestError",
    "validate_dataset_manifest",
    "DatasetRegistry",
    "DatasetRegistryError",
    "build_verified_dataset_manifest",
    "AcquisitionRequest",
    "AcquisitionResult",
    "MarketDataProvider",
    "MockTinkoffProvider",
    "ResearchIngressGate",
    "IngressDecision",
    "IngressGateError",
]

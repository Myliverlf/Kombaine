from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.experiments.manifest import ExperimentManifest, validate_experiment_manifest
from core.research.context import VerifiedResearchContext


class EvidenceGateError(ValueError):
    pass


@dataclass(frozen=True)
class EvidenceAuthorization:
    allowed: bool
    code: str
    reason: str
    context_id: Optional[str] = None
    experiment_id: Optional[str] = None


_ALLOWED_FAILURES = {
    "BLOCKED_NO_CONTEXT",
    "BLOCKED_NO_INGRESS_RECEIPT",
    "BLOCKED_UNVERIFIED_DATASET",
    "BLOCKED_UNVERIFIED_IDENTITY",
    "BLOCKED_QUARANTINED_DATASET",
    "BLOCKED_CHECKSUM_MISMATCH",
    "BLOCKED_EXPERIMENT_MANIFEST",
    "BLOCKED_POLICY_VERSION",
    "BLOCKED_COST_VERSION",
}


def authorize_scientific_evidence(*, context: Optional[VerifiedResearchContext], experiment_manifest: Optional[ExperimentManifest], dataset_checksum: Optional[str] = None) -> EvidenceAuthorization:
    if context is None:
        return EvidenceAuthorization(False, "BLOCKED_NO_CONTEXT", "missing VerifiedResearchContext")
    try:
        context.validate()
    except Exception as exc:
        return EvidenceAuthorization(False, "BLOCKED_NO_CONTEXT", f"invalid context: {exc}", context_id=context.context_id, experiment_id=context.experiment_id)
    if not context.ingress_receipt:
        return EvidenceAuthorization(False, "BLOCKED_NO_INGRESS_RECEIPT", "missing ingress receipt", context_id=context.context_id, experiment_id=context.experiment_id)
    if context.dataset_manifest.provenance_status != "VERIFIED":
        return EvidenceAuthorization(False, "BLOCKED_UNVERIFIED_DATASET", "dataset not verified", context_id=context.context_id, experiment_id=context.experiment_id)
    if context.instrument_identity.identity_source.startswith("file:"):
        return EvidenceAuthorization(False, "BLOCKED_UNVERIFIED_IDENTITY", "file alias cannot authorize evidence", context_id=context.context_id, experiment_id=context.experiment_id)
    if context.dataset_manifest.identity_status != "VERIFIED":
        return EvidenceAuthorization(False, "BLOCKED_UNVERIFIED_IDENTITY", "identity not verified", context_id=context.context_id, experiment_id=context.experiment_id)
    if context.dataset_manifest.provenance_status == "QUARANTINED":
        return EvidenceAuthorization(False, "BLOCKED_QUARANTINED_DATASET", "dataset quarantined", context_id=context.context_id, experiment_id=context.experiment_id)
    if dataset_checksum and dataset_checksum != context.dataset_checksum:
        return EvidenceAuthorization(False, "BLOCKED_CHECKSUM_MISMATCH", "checksum mismatch", context_id=context.context_id, experiment_id=context.experiment_id)
    if experiment_manifest is None:
        return EvidenceAuthorization(False, "BLOCKED_EXPERIMENT_MANIFEST", "missing experiment manifest", context_id=context.context_id, experiment_id=context.experiment_id)
    try:
        validate_experiment_manifest(experiment_manifest)
    except Exception as exc:
        return EvidenceAuthorization(False, "BLOCKED_EXPERIMENT_MANIFEST", f"invalid experiment manifest: {exc}", context_id=context.context_id, experiment_id=context.experiment_id)
    if experiment_manifest.research_policy_version != context.research_policy_version:
        return EvidenceAuthorization(False, "BLOCKED_POLICY_VERSION", "policy version mismatch", context_id=context.context_id, experiment_id=context.experiment_id)
    if experiment_manifest.cost_model_version != context.cost_model_version:
        return EvidenceAuthorization(False, "BLOCKED_COST_VERSION", "cost version mismatch", context_id=context.context_id, experiment_id=context.experiment_id)
    return EvidenceAuthorization(True, "PASS", "scientific evidence authorized", context_id=context.context_id, experiment_id=context.experiment_id)

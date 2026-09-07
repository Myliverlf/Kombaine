from __future__ import annotations

from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, Optional
import json

from core.research.evidence_gate import EvidenceAuthorization


class CandidateRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class CandidateRecord:
    candidate_id: str
    experiment_id: str
    campaign_id: str
    strategy_id: str
    strategy_version: str
    parameters: Dict[str, Any]
    dataset_ids: list[str]
    instrument_ids: list[str]
    policy_version: str
    cost_model_version: str
    result_hashes: list[str]
    ingress_receipt_ids: list[str]
    evidence_authorization_ids: list[str]
    behavioral_cluster_id: Optional[str]
    scientific_validity: str
    qualification_status: str
    quarantine_status: str
    forward_eligibility: str
    promotion_state: str
    human_approval_state: str
    timestamps: Dict[str, str]
    schema_version: str = "1.0.0"


@dataclass
class CandidateRegistry:
    schema_version: str
    registry_id: str
    created_at: str
    candidates: Dict[str, CandidateRecord] = field(default_factory=dict)

    def register(self, record: CandidateRecord, *, evidence_auth: EvidenceAuthorization) -> None:
        if not evidence_auth.allowed:
            raise CandidateRegistryError(f"CANONICAL_WRITE_BLOCKED: {evidence_auth.code}")
        existing = self.candidates.get(record.candidate_id)
        if existing and existing != record:
            raise CandidateRegistryError(f"IMMUTABLE_CANDIDATE_CONFLICT: {record.candidate_id}")
        self.candidates[record.candidate_id] = record

    def get(self, candidate_id: str) -> CandidateRecord:
        try:
            return self.candidates[candidate_id]
        except KeyError as exc:
            raise CandidateRegistryError(f"UNKNOWN_CANDIDATE: {candidate_id}") from exc

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": self.schema_version,
            "registry_id": self.registry_id,
            "created_at": self.created_at,
            "candidates": {k: asdict(v) for k, v in self.candidates.items()},
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        return path

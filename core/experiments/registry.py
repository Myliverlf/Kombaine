from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Optional
import hashlib
import json

from .manifest import ExperimentManifest, validate_experiment_manifest


class ExperimentRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class ExperimentRecord:
    experiment_id: str
    campaign_id: str
    schema_version: str
    manifest_hash: str
    strategy_id: str
    strategy_version: str
    strategy_hash: str
    exact_parameters: Dict[str, Any]
    dataset_ids: list[str]
    instrument_ids: list[str]
    policy_version: str
    cost_model_version: str
    requested_horizons: list[str]
    resolved_horizons: list[str]
    preregistration_timestamp: str
    execution_timestamp: Optional[str]
    gate_receipt_ids: list[str]
    result_ids: list[str]
    scientific_validity: str
    mutation_status: str = "FROZEN"
    manifest: Optional[ExperimentManifest] = None

    def validate(self) -> None:
        if self.mutation_status not in {"FROZEN", "MUTATED", "PROTECTED"}:
            raise ExperimentRegistryError(f"invalid mutation_status: {self.mutation_status}")
        if not self.experiment_id.strip() or not self.manifest_hash.strip():
            raise ExperimentRegistryError("experiment_id and manifest_hash required")


@dataclass
class ExperimentRegistry:
    schema_version: str
    registry_id: str
    created_at: str
    experiments: Dict[str, ExperimentRecord] = field(default_factory=dict)

    def _hash_manifest(self, manifest: ExperimentManifest) -> str:
        payload = json.dumps(asdict(manifest), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def register(self, manifest: ExperimentManifest, *, campaign_id: str, strategy_version: str, result_ids: Optional[list[str]] = None, gate_receipt_ids: Optional[list[str]] = None, execution_timestamp: Optional[str] = None, scientific_validity: str = "PREREGISTERED") -> ExperimentRecord:
        validate_experiment_manifest(manifest)
        manifest_hash = self._hash_manifest(manifest)
        rec = ExperimentRecord(
            experiment_id=manifest.experiment_id,
            campaign_id=campaign_id,
            schema_version=self.schema_version,
            manifest_hash=manifest_hash,
            strategy_id=manifest.strategy_id,
            strategy_version=strategy_version,
            strategy_hash=manifest.strategy_code_hash,
            exact_parameters=manifest.parameter_space,
            dataset_ids=list(manifest.dataset_ids),
            instrument_ids=list(manifest.candidate_manifest.get("instrument_ids", [])),
            policy_version=manifest.research_policy_version,
            cost_model_version=manifest.cost_model_version,
            requested_horizons=list(manifest.horizon_semantics.get("requested", [])),
            resolved_horizons=list(manifest.horizon_semantics.get("resolved", [])),
            preregistration_timestamp=manifest.created_at,
            execution_timestamp=execution_timestamp,
            gate_receipt_ids=list(gate_receipt_ids or []),
            result_ids=list(result_ids or []),
            scientific_validity=scientific_validity,
            mutation_status="FROZEN",
            manifest=manifest,
        )
        rec.validate()
        existing = self.experiments.get(rec.experiment_id)
        if existing and existing.manifest_hash != rec.manifest_hash:
            raise ExperimentRegistryError(f"IMMUTABLE_MANIFEST_CONFLICT: {rec.experiment_id}")
        self.experiments[rec.experiment_id] = rec
        return rec

    def get(self, experiment_id: str) -> ExperimentRecord:
        try:
            return self.experiments[experiment_id]
        except KeyError as exc:
            raise ExperimentRegistryError(f"UNKNOWN_EXPERIMENT: {experiment_id}") from exc

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": self.schema_version,
            "registry_id": self.registry_id,
            "created_at": self.created_at,
            "experiments": {k: {**asdict(v), "manifest": asdict(v.manifest) if v.manifest else None} for k, v in self.experiments.items()},
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> "ExperimentRegistry":
        data = json.loads(path.read_text(encoding="utf-8"))
        reg = cls(schema_version=data["schema_version"], registry_id=data["registry_id"], created_at=data["created_at"])
        for experiment_id, payload in data.get("experiments", {}).items():
            manifest = ExperimentManifest(**payload["manifest"]) if payload.get("manifest") else None
            reg.experiments[experiment_id] = ExperimentRecord(
                experiment_id=payload["experiment_id"],
                campaign_id=payload["campaign_id"],
                schema_version=payload["schema_version"],
                manifest_hash=payload["manifest_hash"],
                strategy_id=payload["strategy_id"],
                strategy_version=payload["strategy_version"],
                strategy_hash=payload["strategy_hash"],
                exact_parameters=payload["exact_parameters"],
                dataset_ids=payload["dataset_ids"],
                instrument_ids=payload["instrument_ids"],
                policy_version=payload["policy_version"],
                cost_model_version=payload["cost_model_version"],
                requested_horizons=payload["requested_horizons"],
                resolved_horizons=payload["resolved_horizons"],
                preregistration_timestamp=payload["preregistration_timestamp"],
                execution_timestamp=payload.get("execution_timestamp"),
                gate_receipt_ids=payload["gate_receipt_ids"],
                result_ids=payload["result_ids"],
                scientific_validity=payload["scientific_validity"],
                mutation_status=payload.get("mutation_status", "FROZEN"),
                manifest=manifest,
            )
        return reg

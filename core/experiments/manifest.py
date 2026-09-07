from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


class ManifestValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ExperimentManifest:
    schema_version: str
    experiment_id: str
    created_at: str
    git_commit: str
    code_version: str
    research_policy_version: str
    validation_policy_version: str
    cost_model_version: str
    dataset_ids: List[str]
    strategy_id: str
    strategy_code_hash: str
    parameter_space: Dict[str, Any]
    candidate_manifest: Dict[str, Any]
    horizon_semantics: Dict[str, Any]
    acceptance_criteria: Dict[str, Any]
    campaign_budget: int
    preregistration_hash: str
    execution_status: str = "PREREGISTERED"
    result_artifact_ids: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class CampaignManifest:
    schema_version: str
    campaign_id: str
    created_at: str
    assets: List[str]
    asset_quotas: Dict[str, int]
    strategy_families: List[str]
    family_quotas: Dict[str, int]
    timeframes: List[str]
    timeframe_quotas: Dict[str, int]
    horizons: List[int]
    parameter_budgets: Dict[str, int]
    total_candidate_budget: int
    dataset_eligibility_policy: Dict[str, Any]
    assignment_rule: str
    behavioral_dedupe_policy: str
    replacement_rule: str
    stopping_rule: str


def validate_experiment_manifest(manifest: ExperimentManifest) -> None:
    missing = [k for k, v in {
        "schema_version": manifest.schema_version,
        "experiment_id": manifest.experiment_id,
        "created_at": manifest.created_at,
        "git_commit": manifest.git_commit,
        "code_version": manifest.code_version,
        "research_policy_version": manifest.research_policy_version,
        "validation_policy_version": manifest.validation_policy_version,
        "cost_model_version": manifest.cost_model_version,
        "strategy_id": manifest.strategy_id,
        "strategy_code_hash": manifest.strategy_code_hash,
        "preregistration_hash": manifest.preregistration_hash,
    }.items() if not str(v).strip()]
    if missing:
        raise ManifestValidationError(f"Missing experiment fields: {missing}")
    if manifest.campaign_budget <= 0:
        raise ManifestValidationError("campaign_budget must be positive")
    if not manifest.dataset_ids:
        raise ManifestValidationError("dataset_ids cannot be empty")

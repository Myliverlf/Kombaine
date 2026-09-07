from __future__ import annotations

from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, List, Optional
import json


class BehavioralEvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class BehavioralEvidenceCluster:
    cluster_id: str
    schema_version: str
    method_version: str
    member_candidate_ids: List[str]
    representative_candidate_id: str
    similarity_features: Dict[str, Any]
    thresholds: Dict[str, Any]
    evidence_status: str
    created_at: str

    def validate(self) -> None:
        if not self.cluster_id.strip() or not self.representative_candidate_id.strip():
            raise BehavioralEvidenceError("cluster_id and representative required")
        if self.representative_candidate_id not in self.member_candidate_ids:
            raise BehavioralEvidenceError("representative must be a cluster member")


@dataclass
class EvidenceClusterRegistry:
    schema_version: str
    registry_id: str
    created_at: str
    clusters: Dict[str, BehavioralEvidenceCluster] = field(default_factory=dict)
    raw_candidate_count: int = 0
    raw_pass_count: int = 0
    independent_cluster_count: int = 0

    def register(self, cluster: BehavioralEvidenceCluster) -> None:
        cluster.validate()
        existing = self.clusters.get(cluster.cluster_id)
        if existing and existing != cluster:
            raise BehavioralEvidenceError(f"IMMUTABLE_CLUSTER_CONFLICT: {cluster.cluster_id}")
        self.clusters[cluster.cluster_id] = cluster
        self.independent_cluster_count = len(self.clusters)

    def assign(self, candidate_id: str, *, cluster_id: Optional[str] = None, representative: Optional[str] = None, features: Optional[Dict[str, Any]] = None) -> BehavioralEvidenceCluster:
        self.raw_candidate_count += 1
        self.raw_pass_count += 1
        cid = cluster_id or f"cluster-{len(self.clusters) + 1}"
        cluster = self.clusters.get(cid)
        if cluster is None:
            cluster = BehavioralEvidenceCluster(
                cluster_id=cid,
                schema_version=self.schema_version,
                method_version="deterministic-1",
                member_candidate_ids=[candidate_id],
                representative_candidate_id=representative or candidate_id,
                similarity_features=features or {},
                thresholds={"similarity": 0.8},
                evidence_status="ACTIVE",
                created_at=self.created_at,
            )
            self.register(cluster)
        else:
            members = list(cluster.member_candidate_ids)
            if candidate_id not in members:
                members.append(candidate_id)
            cluster = BehavioralEvidenceCluster(
                cluster_id=cluster.cluster_id,
                schema_version=cluster.schema_version,
                method_version=cluster.method_version,
                member_candidate_ids=members,
                representative_candidate_id=cluster.representative_candidate_id,
                similarity_features=cluster.similarity_features,
                thresholds=cluster.thresholds,
                evidence_status=cluster.evidence_status,
                created_at=cluster.created_at,
            )
            self.clusters[cid] = cluster
        self.independent_cluster_count = len(self.clusters)
        return cluster

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": self.schema_version,
            "registry_id": self.registry_id,
            "created_at": self.created_at,
            "raw_candidate_count": self.raw_candidate_count,
            "raw_pass_count": self.raw_pass_count,
            "independent_cluster_count": self.independent_cluster_count,
            "clusters": {k: asdict(v) for k, v in self.clusters.items()},
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        return path

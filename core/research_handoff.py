"""Canonical immutable research→selection handoff contract.

Research artifacts are run-scoped and immutable. This module only validates and
writes handoff data; it never activates candidates or touches a broker.
"""
from __future__ import annotations
import hashlib, json
from pathlib import Path
from typing import Any

REQUIRED_FIELDS = {
    "run_id", "candidate_id", "family_id", "instrument", "timeframe",
    "params", "qualification_stage", "policy_version", "policy_hash",
    "data_hashes", "cost_model_hash", "risk_status", "provenance",
}

def validate_handoff(payload: dict[str, Any]) -> None:
    missing = sorted(REQUIRED_FIELDS - set(payload))
    if missing:
        raise ValueError(f"HANDOFF_SCHEMA_ERROR: missing={missing}")
    if payload["qualification_stage"] == "PAPER_ADMISSION_READY":
        required = {"multi_horizon", "walk_forward", "robustness", "risk", "cost", "paper_status"}
        absent = sorted(required - set(payload.get("provenance", {})))
        if absent:
            raise ValueError(f"HANDOFF_SCHEMA_ERROR: missing_provenance={absent}")

def handoff_path(root: Path, run_id: str) -> Path:
    return Path(root) / "state" / "eligible_candidates" / str(run_id) / "eligible_candidates.json"

def write_handoff(root: Path, run_id: str, payload: dict[str, Any]) -> Path:
    validate_handoff(payload)
    path = handoff_path(root, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = path.read_bytes()
        incoming = json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2).encode()
        if existing != incoming:
            raise FileExistsError(f"HANDOFF_IMMUTABLE_CONFLICT: {path}")
        return path
    path.write_bytes(json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2).encode())
    return path

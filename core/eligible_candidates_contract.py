"""Canonical Eligible Candidates Contract — Iteration 23G.

Immutable run-specific handoff artifact for eligible_candidates.json.

Invariant: once a run calls finalize_eligible(), the resulting
eligible_candidates.json is IMMUTABLE. Any modification requires a
new run_id. This module provides:

  1. Immutability enforcement (write-once, read-many)
  2. Provenance stamping (run_id, config_key, hash, timestamp)
  3. Handoff manifest generation for downstream consumers
  4. Verification functions for tests and runtime guards

CLASS 2: runtime non-trading. No broker, no live, no strategy changes.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONTRACT_VERSION = "1.0.0"
IMMUTABLE_FIELDS = frozenset({
    "run_id",
    "config_key",
    "instrument",
    "strategy",
    "parameters",
    "metrics",
    "eligible",
    "reject_reasons",
    "horizon_days",
    "dataset_identity",
    "cost_assumptions",
})

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class CandidateProvenance:
    """Provenance stamp for a single eligible candidate."""
    run_id: str
    config_key: str
    instrument: str
    strategy: str
    produced_at: str  # ISO timestamp
    data_hash: str  # SHA-256 prefix of the candidate dict
    manifest_version: str
    source_module: str = "core/run_contract.py"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class HandoffManifest:
    """Machine-readable manifest for an immutable eligible_candidates artifact."""
    manifest_id: str
    run_id: str
    contract_version: str
    produced_at: str
    finalized_at: str
    eligible_count: int
    candidate_provenances: List[Dict[str, Any]]
    artifact_hash: str  # SHA-256 of the entire eligible_candidates.json
    immutable: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class HandoffVerification:
    """Result of verifying an eligible_candidates artifact against its contract."""
    valid: bool
    run_id: Optional[str] = None
    immutable_ok: bool = True
    provenance_ok: bool = True
    hash_ok: bool = True
    rejection_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Hashing helpers
# ---------------------------------------------------------------------------

def _candidate_hash(candidate: Dict[str, Any]) -> str:
    """Deterministic SHA-256 prefix for a candidate dict (canonical JSON)."""
    canonical = json.dumps(candidate, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _file_hash(path: Path) -> str:
    """SHA-256 hash of a file's contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Write path — called by ResearchRun.finalize_eligible()
# ---------------------------------------------------------------------------

def write_immutable_eligible_candidates(
    run_dir: Path,
    run_id: str,
    eligible: List[Dict[str, Any]],
    manifest_version: str = CONTRACT_VERSION,
    created_at: Optional[str] = None,
) -> Path:
    """Write an immutable eligible_candidates.json with provenance stamps.

    This function:
    1. Stamps each candidate with provenance metadata
    2. Writes eligible_candidates.json atomically
    3. Writes handoff_manifest.json alongside it
    4. Makes the file read-only (os.chmod 0o444)

    Returns the path to the written artifact.

    Raises ValueError if the file already exists (immutability enforcement).
    """
    eligible_path = run_dir / "eligible_candidates.json"
    if eligible_path.exists():
        raise ValueError(
            f"eligible_candidates.json already exists at {eligible_path}. "
            f"Immutable contract: must not overwrite. Create a new run instead."
        )

    now = created_at or datetime.now(timezone.utc).isoformat()

    # Stamp each candidate with provenance
    provenances: List[Dict[str, Any]] = []
    stamped_eligible: List[Dict[str, Any]] = []
    for cand in eligible:
        prov = CandidateProvenance(
            run_id=run_id,
            config_key=cand.get("config_key", "unknown"),
            instrument=cand.get("instrument", cand.get("ticker", "")),
            strategy=cand.get("strategy", ""),
            produced_at=now,
            data_hash=_candidate_hash(cand),
            manifest_version=manifest_version,
        )
        provenances.append(prov.to_dict())

        # Embed provenance into the candidate (non-destructive extension)
        stamped = dict(cand)
        stamped["_provenance"] = prov.to_dict()
        stamped_eligible.append(stamped)

    # Write eligible_candidates.json
    eligible_path.write_text(
        json.dumps(stamped_eligible, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Make immutable (read-only)
    os.chmod(str(eligible_path), 0o444)

    # Compute artifact hash
    artifact_hash = _file_hash(eligible_path)

    # Write handoff manifest
    manifest = HandoffManifest(
        manifest_id=f"handoff_{run_id}",
        run_id=run_id,
        contract_version=manifest_version,
        produced_at=now,
        finalized_at=now,
        eligible_count=len(eligible),
        candidate_provenances=provenances,
        artifact_hash=artifact_hash,
        immutable=True,
    )
    manifest_path = run_dir / "handoff_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return eligible_path


# ---------------------------------------------------------------------------
# Read path — for downstream consumers
# ---------------------------------------------------------------------------

def load_eligible_candidates(
    run_dir: Path,
) -> Optional[List[Dict[str, Any]]]:
    """Load immutable eligible_candidates.json from a run directory."""
    path = run_dir / "eligible_candidates.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def load_handoff_manifest(
    run_dir: Path,
) -> Optional[Dict[str, Any]]:
    """Load handoff_manifest.json from a run directory."""
    path = run_dir / "handoff_manifest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------
# Verification path — for tests and runtime guards
# ---------------------------------------------------------------------------

def verify_eligible_candidates_contract(
    run_dir: Path,
    expected_run_id: Optional[str] = None,
) -> HandoffVerification:
    """Verify that eligible_candidates.json satisfies the immutable contract.

    Checks:
    1. File exists
    2. JSON parses
    3. Every candidate has _provenance
    4. Every candidate's run_id matches expected
    5. File hash matches handoff_manifest.json
    6. File is read-only (immutability check)

    Returns HandoffVerification with structured result.
    """
    reasons: List[str] = []
    eligible_path = run_dir / "eligible_candidates.json"
    manifest_path = run_dir / "handoff_manifest.json"

    # Check 1: File exists
    if not eligible_path.exists():
        return HandoffVerification(
            valid=False,
            immutable_ok=False,
            rejection_reasons=["eligible_candidates.json not found"],
        )

    # Check 2: JSON parses
    try:
        candidates = json.loads(eligible_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return HandoffVerification(
            valid=False,
            immutable_ok=False,
            rejection_reasons=[f"eligible_candidates.json parse error: {e}"],
        )

    if not isinstance(candidates, list):
        return HandoffVerification(
            valid=False,
            immutable_ok=False,
            rejection_reasons=["eligible_candidates.json is not a list"],
        )

    # Check 3 & 4: Provenance and run_id
    provenance_ok = True
    hash_ok = True
    immutable_ok = True
    run_id = expected_run_id

    # Check 6: File is read-only
    try:
        stat = os.stat(str(eligible_path))
        is_readonly = (stat.st_mode & 0o222) == 0
        if not is_readonly:
            immutable_ok = False
            reasons.append("eligible_candidates.json is not read-only (immutability violated)")
    except OSError:
        pass

    for i, cand in enumerate(candidates):
        prov = cand.get("_provenance")
        if prov is None:
            provenance_ok = False
            reasons.append(f"candidate[{i}] missing _provenance")

        if run_id is None and prov:
            run_id = prov.get("run_id")
        elif run_id and prov and prov.get("run_id") != run_id:
            provenance_ok = False
            reasons.append(
                f"candidate[{i}] run_id mismatch: {prov.get('run_id')} != {run_id}"
            )

    if expected_run_id is not None:
        run_id = expected_run_id

    # Check 5: Artifact hash matches manifest
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_hash = manifest.get("artifact_hash", "")
            actual_hash = _file_hash(eligible_path)
            if manifest_hash and manifest_hash != actual_hash:
                hash_ok = False
                reasons.append(
                    f"artifact hash mismatch: manifest={manifest_hash} actual={actual_hash}"
                )
        except (json.JSONDecodeError, OSError):
            hash_ok = False
            reasons.append("handoff_manifest.json parse error")
    else:
        hash_ok = False
        reasons.append("handoff_manifest.json not found")

    valid = provenance_ok and hash_ok and immutable_ok and len(reasons) == 0

    return HandoffVerification(
        valid=valid,
        run_id=run_id,
        immutable_ok=immutable_ok,
        provenance_ok=provenance_ok,
        hash_ok=hash_ok,
        rejection_reasons=reasons,
    )


def assert_immutability(run_dir: Path) -> None:
    """Raise if eligible_candidates.json is mutable. For use in tests."""
    result = verify_eligible_candidates_contract(run_dir)
    if not result.immutable_ok:
        raise AssertionError(
            f"Immutability violated: {result.rejection_reasons}"
        )
    if not result.provenance_ok:
        raise AssertionError(
            f"Provenance violated: {result.rejection_reasons}"
        )
    if not result.hash_ok:
        raise AssertionError(
            f"Hash integrity violated: {result.rejection_reasons}"
        )

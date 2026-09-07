"""Canonical Seeder Handoff — Iteration 06.

Implements the validated handoff from a completed canonical research run
to the strategy seeder, replacing the legacy fixed-scan intake path.

Pipeline:
    LATEST CANONICAL COMPLETED RUN
    → eligible_candidates.json
    → HANDOFF VALIDATION GATE
    → SEEDER
    → CANONICAL STRATEGY REGISTRY
    → derived WATCHLIST / SIGNAL artifacts

Legacy path is available only via explicit opt-in (use_legacy=True).
Never silently falls back to legacy on canonical failure.

CLASS 2: runtime non-trading. No broker, no live, no strategy changes.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HANDOFF_SCHEMA_VERSION = "1.0.0"
UNIVERSE_GATE_DEFAULT = ["BR", "GAZP", "LKOH", "SBER", "Si"]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class HandoffValidation:
    """Structured validation result for a canonical handoff."""

    valid: bool
    run_id: Optional[str] = None
    run_dir: Optional[str] = None
    status: Optional[str] = None
    integrity_passed: Optional[bool] = None
    eligible_count: int = 0
    candidates_valid: int = 0
    candidates_rejected: int = 0
    rejection_reasons: List[str] = field(default_factory=list)
    source_type: str = "canonical_research_run"
    source_config_key: Optional[str] = None
    legacy_mode: bool = False
    blocked_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "run_id": self.run_id,
            "run_dir": self.run_dir,
            "status": self.status,
            "integrity_passed": self.integrity_passed,
            "eligible_count": self.eligible_count,
            "candidates_valid": self.candidates_valid,
            "candidates_rejected": self.candidates_rejected,
            "rejection_reasons": self.rejection_reasons,
            "source_type": self.source_type,
            "legacy_mode": self.legacy_mode,
            "blocked_reason": self.blocked_reason,
        }


@dataclass
class HandoffSeedResult:
    """Result of a seeding operation from the canonical handoff."""

    source_type: str  # "canonical_research_run" or "legacy_scan"
    source_run_id: Optional[str] = None
    eligible_count: int = 0
    accepted_count: int = 0
    skipped_existing_count: int = 0
    rejected_count: int = 0
    rejection_reasons: List[str] = field(default_factory=list)
    registry_committed: bool = False
    legacy_mode_enabled: bool = False
    derived_export_path: Optional[str] = None
    validation: Optional[HandoffValidation] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_type": self.source_type,
            "source_run_id": self.source_run_id,
            "eligible_count": self.eligible_count,
            "accepted_count": self.accepted_count,
            "skipped_existing_count": self.skipped_existing_count,
            "rejected_count": self.rejected_count,
            "rejection_reasons": self.rejection_reasons,
            "registry_committed": self.registry_committed,
            "legacy_mode_enabled": self.legacy_mode_enabled,
            "derived_export_path": self.derived_export_path,
            "validation": self.validation.to_dict() if self.validation else None,
        }


# ---------------------------------------------------------------------------
# 1. Resolve latest completed run
# ---------------------------------------------------------------------------


def resolve_latest_completed_run(
    reports_dir: Path,
) -> Optional[Dict[str, Any]]:
    """Resolve the atomic latest_run.json pointer.

    Returns the pointer dict with keys: run_id, path, completed_at
    or None if the pointer is missing/malformed.
    """
    pointer_path = reports_dir / "latest_run.json"
    if not pointer_path.exists():
        logger.warning("HANDOFF: latest_run.json not found at %s", pointer_path)
        return None
    try:
        data = json.loads(pointer_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.error("HANDOFF: latest_run.json malformed: %s", e)
        return None

    # Validate pointer structure
    if not isinstance(data, dict):
        logger.error("HANDOFF: latest_run.json is not a dict")
        return None
    if "run_id" not in data or "path" not in data:
        logger.error("HANDOFF: latest_run.json missing required fields (run_id, path)")
        return None

    return data


def _load_manifest(run_dir: Path) -> Optional[Dict[str, Any]]:
    """Load manifest.json from a run directory."""
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _load_eligible(run_dir: Path) -> Optional[List[Dict[str, Any]]]:
    """Load eligible_candidates.json from a run directory."""
    eligible_path = run_dir / "eligible_candidates.json"
    if not eligible_path.exists():
        return None
    try:
        return json.loads(eligible_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _load_checks(run_dir: Path) -> Optional[Dict[str, Any]]:
    """Load checks.json from a run directory."""
    checks_path = run_dir / "checks.json"
    if not checks_path.exists():
        return None
    try:
        return json.loads(checks_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------
# 2. Validate handoff
# ---------------------------------------------------------------------------


def validate_handoff(
    reports_dir: Path,
    universe: Optional[List[str]] = None,
) -> HandoffValidation:
    """Validate the canonical handoff gate.

    Checks in order:
    1. latest_run.json pointer exists and parses
    2. pointed-to run directory exists
    3. manifest exists with run_id match
    4. status == COMPLETED
    5. integrity checks passed
    6. eligible_candidates.json exists and parses
    7. each candidate has matching run_id
    8. each candidate has config_key
    9. each candidate instrument is in allowed universe
    10. no duplicate config_keys

    Returns HandoffValidation with structured result.
    Zero partial seeding on any validation failure.
    """
    universe = universe or UNIVERSE_GATE_DEFAULT
    universe_set = set(universe)

    # Step 1: Resolve pointer
    pointer = resolve_latest_completed_run(reports_dir)
    if pointer is None:
        return HandoffValidation(
            valid=False,
            blocked_reason="latest_run.json missing or malformed (F1/F2)",
        )

    run_id = pointer["run_id"]
    run_dir = Path(pointer["path"])

    # Step 2: Run directory exists
    if not run_dir.exists():
        return HandoffValidation(
            valid=False,
            run_id=run_id,
            blocked_reason=f"run directory does not exist: {run_dir} (F3)",
        )

    # Step 3: Manifest exists with matching run_id
    manifest = _load_manifest(run_dir)
    if manifest is None:
        return HandoffValidation(
            valid=False,
            run_id=run_id,
            run_dir=str(run_dir),
            blocked_reason="manifest.json missing or corrupt (F4)",
        )
    if manifest.get("run_id") != run_id:
        return HandoffValidation(
            valid=False,
            run_id=run_id,
            run_dir=str(run_dir),
            blocked_reason=f"manifest run_id mismatch: {manifest.get('run_id')} != {run_id}",
        )

    # Step 4: Status == COMPLETED
    status = manifest.get("status")
    if status != "COMPLETED":
        return HandoffValidation(
            valid=False,
            run_id=run_id,
            run_dir=str(run_dir),
            status=status,
            blocked_reason=f"run status is {status}, expected COMPLETED (F5/F6)",
        )

    # Step 5: Integrity checks passed
    checks = _load_checks(run_dir)
    if checks is None:
        return HandoffValidation(
            valid=False,
            run_id=run_id,
            run_dir=str(run_dir),
            status=status,
            integrity_passed=False,
            blocked_reason="checks.json missing — integrity not verified (F7)",
        )
    if not checks.get("all_passed", False):
        failed_checks = [
            name for name, passed in checks.get("checks", {}).items()
            if name != "_missing_config_keys" and passed is False
        ]
        return HandoffValidation(
            valid=False,
            run_id=run_id,
            run_dir=str(run_dir),
            status=status,
            integrity_passed=False,
            blocked_reason=f"integrity checks failed: {failed_checks} (F7)",
        )

    # Step 6: eligible_candidates.json exists and parses
    eligible = _load_eligible(run_dir)
    if eligible is None:
        return HandoffValidation(
            valid=False,
            run_id=run_id,
            run_dir=str(run_dir),
            status=status,
            integrity_passed=True,
            blocked_reason="eligible_candidates.json missing or corrupt (F8/F9)",
        )

    if not isinstance(eligible, list):
        return HandoffValidation(
            valid=False,
            run_id=run_id,
            run_dir=str(run_dir),
            status=status,
            integrity_passed=True,
            blocked_reason="eligible_candidates.json is not a list (F9)",
        )

    # Steps 7-10: Validate each candidate
    valid_count = 0
    rejected_count = 0
    rejection_reasons: List[str] = []
    seen_config_keys: set = set()

    for i, cand in enumerate(eligible):
        cand_run_id = cand.get("run_id")
        config_key = cand.get("config_key")
        instrument = cand.get("instrument", "")

        # Step 7: run_id match
        if cand_run_id and cand_run_id != run_id:
            rejected_count += 1
            rejection_reasons.append(
                f"candidate[{i}] run_id mismatch: {cand_run_id} != {run_id} (F10)"
            )
            continue

        # Step 8: config_key exists
        if not config_key:
            rejected_count += 1
            rejection_reasons.append(f"candidate[{i}] missing config_key (F11)")
            continue

        # Step 10: no duplicate config_key
        if config_key in seen_config_keys:
            rejected_count += 1
            rejection_reasons.append(f"candidate[{i}] duplicate config_key: {config_key} (F12)")
            continue
        seen_config_keys.add(config_key)

        # Step 9: universe gate
        if instrument and instrument not in universe_set:
            rejected_count += 1
            rejection_reasons.append(
                f"candidate[{i}] foreign universe: {instrument} not in {universe} (F13)"
            )
            continue

        valid_count += 1

    # If ANY candidate has a cross-run contamination issue, block the whole batch
    has_run_id_mismatch = any("run_id mismatch" in r for r in rejection_reasons)

    is_valid = (not has_run_id_mismatch) and valid_count > 0

    blocked_reason = None
    if has_run_id_mismatch:
        blocked_reason = "cross-run contamination detected (F10)"
    elif valid_count == 0 and len(eligible) > 0:
        blocked_reason = "all candidates rejected by validation gate"

    return HandoffValidation(
        valid=is_valid,
        run_id=run_id,
        run_dir=str(run_dir),
        status=status,
        integrity_passed=True,
        eligible_count=len(eligible),
        candidates_valid=valid_count,
        candidates_rejected=rejected_count,
        rejection_reasons=rejection_reasons,
        source_type="canonical_research_run",
        blocked_reason=blocked_reason,
    )


# ---------------------------------------------------------------------------
# 3. Seed from eligible
# ---------------------------------------------------------------------------


def seed_from_eligible(
    reports_dir: Path,
    registry,  # StrategyRegistry or AdaptiveRegistry instance
    universe: Optional[List[str]] = None,
    dry_run: bool = False,
) -> HandoffSeedResult:
    """Seed the canonical registry from the latest completed run.

    1. Validates the canonical handoff gate
    2. If valid, admits all validated candidates to the registry
    3. Idempotent: same run_id + config_key won't duplicate
    4. Registry remains lifecycle truth
    5. Derived exports (signal_pool/waitlist) follow registry save

    Returns HandoffSeedResult with structured observability data.
    """
    validation = validate_handoff(reports_dir, universe)

    result = HandoffSeedResult(
        source_type="canonical_research_run",
        source_run_id=validation.run_id,
        eligible_count=validation.eligible_count,
        validation=validation,
    )

    if not validation.valid:
        result.rejected_count = validation.eligible_count
        result.rejection_reasons = validation.rejection_reasons
        logger.warning(
            "HANDOFF BLOCKED: %s (eligible=%d, valid=%d, rejected=%d)",
            validation.blocked_reason,
            validation.eligible_count,
            validation.candidates_valid,
            validation.candidates_rejected,
        )
        return result

    # Load the eligible candidates
    run_dir = Path(validation.run_dir)
    eligible = _load_eligible(run_dir)
    if eligible is None:
        result.rejected_count = result.eligible_count
        result.rejection_reasons = ["eligible_candidates.json disappeared after validation"]
        return result

    # Filter to only valid candidates (those that passed gate)
    valid_candidates = [
        c for c in eligible
        if c.get("run_id") == validation.run_id
        and c.get("config_key")
        and (not universe or c.get("instrument", "") in set(universe))
    ]

    if dry_run:
        result.accepted_count = len(valid_candidates)
        result.skipped_existing_count = 0
        result.rejected_count = result.eligible_count - len(valid_candidates)
        return result

    # Seed into registry
    accepted = 0
    skipped = 0
    rejected = 0
    rejection_reasons: List[str] = []

    manifest = _load_manifest(run_dir)
    manifest_version = manifest.get("schema_version", "unknown") if manifest else "unknown"

    for cand in valid_candidates:
        config_key = cand["config_key"]
        instrument = cand.get("instrument", "")
        strategy = cand.get("strategy", "")
        parameters = cand.get("parameters", {})
        metrics = cand.get("metrics", {})
        timeframe = cand.get("timeframe", "")
        horizon_days = cand.get("horizon_days", 60)

        # Idempotency check: skip if already exists in registry
        existing = registry.get(config_key) if hasattr(registry, "get") else None
        if existing is not None:
            skipped += 1
            continue

        # Record generation with full provenance
        if hasattr(registry, "record_generation"):
            try:
                registry.record_generation(
                    strategy_id=config_key,
                    ticker=instrument,
                    strategy=strategy,
                    params=parameters,
                    metrics=metrics,
                    portfolio_context={
                        "regime_ok": True,
                        "stale_ok": True,
                        "contract_risk_ok": True,
                    },
                    quality_gate={"ttl_days": horizon_days},
                    source="canonical_research_run",
                    status="waitlist",
                    note=f"seeded_from_run_{validation.run_id}",
                )
                # Attach provenance metadata to the record
                record = registry.get(config_key)
                if record is not None:
                    # Store provenance if the record supports it
                    if hasattr(record, "source_run_id"):
                        record.source_run_id = validation.run_id
                    if hasattr(record, "source_config_key"):
                        record.source_config_key = config_key
                    if hasattr(record, "source_type"):
                        record.source_type = "canonical_research_run"
                    if hasattr(record, "source_manifest_version"):
                        record.source_manifest_version = manifest_version
                    if hasattr(record, "seeded_at"):
                        record.seeded_at = datetime.now(timezone.utc).isoformat()
                    if hasattr(registry, "_store_record"):
                        registry._store_record(record)
                accepted += 1
            except Exception as e:
                rejected += 1
                rejection_reasons.append(f"registry write failed for {config_key}: {e}")
        else:
            rejected += 1
            rejection_reasons.append(f"registry has no record_generation method")

    # Save registry if changes were made
    if accepted > 0 and hasattr(registry, "save"):
        try:
            registry.save()
            result.registry_committed = True
        except Exception as e:
            rejection_reasons.append(f"registry save failed: {e}")
            result.registry_committed = False

    # Export derived views
    if result.registry_committed and hasattr(registry, "export_legacy_state_files"):
        try:
            registry.export_legacy_state_files()
            result.derived_export_path = "state/waitlist.json, state/signal_pool.json"
        except Exception as e:
            rejection_reasons.append(f"derived export failed: {e}")

    result.accepted_count = accepted
    result.skipped_existing_count = skipped
    result.rejected_count = rejected + (result.eligible_count - len(valid_candidates))
    result.rejection_reasons = rejection_reasons

    logger.info(
        "HANDOFF COMPLETE: source_type=%s run_id=%s eligible=%d accepted=%d "
        "skipped=%d rejected=%d legacy=%s",
        result.source_type,
        result.source_run_id,
        result.eligible_count,
        result.accepted_count,
        result.skipped_existing_count,
        result.rejected_count,
        result.legacy_mode_enabled,
    )

    return result


# ---------------------------------------------------------------------------
# 4. Legacy fallback (explicit opt-in only)
# ---------------------------------------------------------------------------


def legacy_fallback(
    scan_path: Path,
    registry,  # StrategyRegistry or AdaptiveRegistry instance
    universe: Optional[List[str]] = None,
    dry_run: bool = False,
) -> HandoffSeedResult:
    """Seed from a legacy fixed-scan file.

    This function is available ONLY via explicit opt-in (use_legacy=True
    in the calling seeder). It is never called as a fallback when the
    canonical handoff fails.

    LEGACY mode is:
    - explicitly opted-in only
    - visibly logged as LEGACY
    - never masquerades as canonical
    - never automatically activates after canonical failure

    Args:
        scan_path: Path to the legacy scan_results JSON file
        registry: StrategyRegistry/AdaptiveRegistry instance
        universe: Allowed instruments (default core 5)
        dry_run: If True, validate but don't write

    Returns:
        HandoffSeedResult with source_type="legacy_scan" and
        legacy_mode_enabled=True
    """
    universe = universe or UNIVERSE_GATE_DEFAULT
    universe_set = set(universe)

    logger.warning(
        "LEGACY MODE ENABLED: seeding from legacy scan %s. "
        "This is NOT canonical — explicit opt-in only.",
        scan_path,
    )

    result = HandoffSeedResult(
        source_type="legacy_scan",
        legacy_mode_enabled=True,
    )

    # Validate scan file exists
    if not scan_path.exists():
        result.rejected_count = 1
        result.rejection_reasons = [f"legacy scan file not found: {scan_path}"]
        return result

    # Load scan data
    try:
        scan_data = json.loads(scan_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        result.rejected_count = 1
        result.rejection_reasons = [f"legacy scan file corrupt: {e}"]
        return result

    # Normalize scan data to list
    if isinstance(scan_data, list):
        rows = scan_data
    elif isinstance(scan_data, dict):
        rows = scan_data.get("results", [])
    else:
        result.rejected_count = 1
        result.rejection_reasons = ["legacy scan data is not a list or dict with 'results'"]
        return result

    # Filter: no RI, quality gate passed
    candidates = []
    for r in rows:
        if r.get("ticker") == "RI":
            continue
        if not r.get("wf_quality_passed"):
            continue
        candidates.append(r)

    result.eligible_count = len(candidates)

    if dry_run:
        result.accepted_count = len(candidates)
        return result

    # Seed into registry with LEGACY source marker
    accepted = 0
    skipped = 0

    for cand in candidates[:15]:  # Same cap as original seeder
        ticker = cand.get("ticker", "")
        strategy = cand.get("strategy", "")
        params = cand.get("best_params", {}) or {}
        config_key = f"{ticker}__{strategy}"

        # Universe gate
        if ticker not in universe_set:
            result.rejection_reasons.append(f"legacy candidate {ticker} outside universe")
            continue

        # Idempotency check
        existing = registry.get(config_key) if hasattr(registry, "get") else None
        if existing is not None:
            skipped += 1
            continue

        if hasattr(registry, "record_generation"):
            try:
                registry.record_generation(
                    strategy_id=config_key,
                    ticker=ticker,
                    strategy=strategy,
                    params=params,
                    metrics=cand.get("metrics", {}),
                    portfolio_context={
                        "regime_ok": True,
                        "stale_ok": True,
                        "contract_risk_ok": True,
                    },
                    quality_gate={"ttl_days": 7},
                    source="legacy_scan",
                    status="waitlist",
                    note="LEGACY_seed_from_fixed_scan",
                )
                accepted += 1
            except Exception as e:
                result.rejection_reasons.append(f"legacy registry write failed: {e}")

    if accepted > 0 and hasattr(registry, "save"):
        try:
            registry.save()
            result.registry_committed = True
        except Exception as e:
            result.rejection_reasons.append(f"legacy registry save failed: {e}")

    if result.registry_committed and hasattr(registry, "export_legacy_state_files"):
        try:
            registry.export_legacy_state_files()
            result.derived_export_path = "state/waitlist.json, state/signal_pool.json"
        except Exception as e:
            result.rejection_reasons.append(f"legacy derived export failed: {e}")

    result.accepted_count = accepted
    result.skipped_existing_count = skipped
    result.rejected_count = result.eligible_count - accepted - skipped

    logger.warning(
        "LEGACY SEED COMPLETE: eligible=%d accepted=%d skipped=%d rejected=%d",
        result.eligible_count,
        result.accepted_count,
        result.skipped_existing_count,
        result.rejected_count,
    )

    return result


# ---------------------------------------------------------------------------
# 5. Convenience entry point
# ---------------------------------------------------------------------------


def run_seeder_handoff(
    reports_dir: Path,
    registry,
    universe: Optional[List[str]] = None,
    use_legacy: bool = False,
    legacy_scan_path: Optional[Path] = None,
    dry_run: bool = False,
) -> HandoffSeedResult:
    """Canonical seeder handoff entry point.

    Default: canonical first, legacy opt-in only.
    If canonical is invalid and use_legacy=False, returns HANDOFF_BLOCKED.
    If use_legacy=True and legacy_scan_path provided, seeds from legacy
    with explicit LEGACY labeling.

    Args:
        reports_dir: Root of reports (e.g. reports/strategy_architect/)
        registry: StrategyRegistry/AdaptiveRegistry instance
        universe: Allowed instruments
        use_legacy: Explicit opt-in for legacy scan fallback
        legacy_scan_path: Path to legacy scan file (required if use_legacy=True)
        dry_run: If True, validate but don't write to registry

    Returns:
        HandoffSeedResult with structured observability data
    """
    universe = universe or UNIVERSE_GATE_DEFAULT

    # Step 1: Try canonical handoff
    canonical_result = seed_from_eligible(
        reports_dir=reports_dir,
        registry=registry,
        universe=universe,
        dry_run=dry_run,
    )

    # If canonical succeeded or canonical explicitly rejected candidates
    # (but the handoff itself was valid), return the canonical result
    if canonical_result.validation and canonical_result.validation.valid:
        return canonical_result

    # Canonical failed — block by default
    if not use_legacy:
        logger.warning(
            "HANDOFF BLOCKED: canonical handoff failed (%s). "
            "Legacy fallback NOT enabled. Use use_legacy=True to opt in.",
            canonical_result.validation.blocked_reason if canonical_result.validation
            else "unknown",
        )
        return canonical_result

    # Legacy opt-in
    if legacy_scan_path is None:
        logger.error(
            "LEGACY OPT-IN: use_legacy=True but no legacy_scan_path provided"
        )
        canonical_result.rejection_reasons.append(
            "use_legacy=True but legacy_scan_path is None"
        )
        return canonical_result

    logger.warning(
        "CANONICAL FAILED — FALLING BACK TO LEGACY (explicit opt-in). "
        "canonical_reason=%s",
        canonical_result.validation.blocked_reason if canonical_result.validation
        else "unknown",
    )

    legacy_result = legacy_fallback(
        scan_path=legacy_scan_path,
        registry=registry,
        universe=universe,
        dry_run=dry_run,
    )

    # Merge the canonical failure context into legacy result
    legacy_result.rejection_reasons = (
        [f"canonical failed: {canonical_result.validation.blocked_reason}"]
        + legacy_result.rejection_reasons
    )
    legacy_result.validation = canonical_result.validation

    return legacy_result

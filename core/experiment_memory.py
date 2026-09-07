"""Experiment Identity & Memory — Iteration 07.

Provides durable experiment identity (family/instance) and a SQLite-backed
memory index for canonical research runs.  CLASS 1 only: research metadata,
observation + index + classification.  NO VETO / SKIP / PRIORITIZE / EXECUTE.

Two-level identity:
  experiment_family_id  — same normalized hypothesis/configuration under
                          compatible methodology (deterministic, stable).
  experiment_instance_id — exact execution on exact data/code/cost/validation
                           evidence (deterministic, stable).

Classification categories:
  NEW / EXACT_DUPLICATE / REVALIDATION / METHODOLOGY_CHANGE /
  CODE_CHANGE / COST_MODEL_CHANGE / INCOMPARABLE

Store: state/experiment_memory.db (separate from analytics.db)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MEMORY_SCHEMA_VERSION = "1.0.0"
MEMORY_DB_NAME = "experiment_memory.db"

VALID_CLASSIFICATIONS = frozenset({
    "NEW",
    "EXACT_DUPLICATE",
    "REVALIDATION",
    "METHODOLOGY_CHANGE",
    "CODE_CHANGE",
    "COST_MODEL_CHANGE",
    "INCOMPARABLE",
})

# Normalized hypothesis fields that define an experiment FAMILY.
# These capture the "what question are we testing?" identity.
_FAMILY_IDENTITY_KEYS = [
    "instrument",
    "timeframe",
    "strategy",
    "parameters",          # normalized params (sorted keys)
    "horizon_days",
    "methodology_version",  # validation protocol / backtest engine
]

# Exact evidence fields that define an experiment INSTANCE.
# These capture the precise data/code/cost conditions of one execution.
_INSTANCE_IDENTITY_KEYS = [
    "instrument",
    "timeframe",
    "strategy",
    "parameters",
    "horizon_days",
    "dataset_hash",
    "dataset_start",
    "dataset_end",
    "code_hash",
    "cost_model_hash",
    "validation_version",
    "backtest_engine_version",
]


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def normalize_params(params: Any) -> Dict[str, Any]:
    """Normalize a parameter dict for deterministic identity.

    Sorted keys, canonical JSON, numeric values preserved.
    None/empty → empty dict.
    """
    if not params or not isinstance(params, dict):
        return {}
    normalized: Dict[str, Any] = {}
    for k in sorted(params.keys()):
        v = params[k]
        # Normalize floats that are whole numbers
        if isinstance(v, float) and v == int(v) and abs(v) < 1e15:
            v = int(v)
        normalized[k] = v
    return normalized


def normalize_instrument(instrument: str) -> str:
    """Normalize instrument/root ticker to uppercase stripped form."""
    return str(instrument).strip().upper() if instrument else ""


def normalize_timeframe(timeframe: str) -> str:
    """Normalize timeframe string: lowercase, stripped."""
    return str(timeframe).strip().lower() if timeframe else ""


def normalize_strategy_name(strategy: str) -> str:
    """Normalize strategy name: lowercase, stripped, underscores."""
    s = str(strategy).strip().lower() if strategy else ""
    return s.replace("-", "_").replace(" ", "_")


def _canonical_json(obj: Any) -> str:
    """Deterministic JSON serialization for identity hashing."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _hash_str(raw: str) -> str:
    """SHA-256 hash of a string, truncated to 16 hex chars."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _hash_dict(d: Dict[str, Any]) -> str:
    """Deterministic hash of a dict via canonical JSON."""
    return _hash_str(_canonical_json(d))


# ---------------------------------------------------------------------------
# Identity generation
# ---------------------------------------------------------------------------

def experiment_family_id(
    instrument: str,
    timeframe: str,
    strategy: str,
    parameters: Any,
    horizon_days: int = 60,
    methodology_version: str = "default",
) -> str:
    """Deterministic experiment FAMILY identity.

    Encodes the normalized hypothesis: what are we testing?
    Same normalized hypothesis → same family_id regardless of data window,
    code version, cost model, or execution timestamp.
    """
    norm_params = normalize_params(parameters)
    identity_blob = {
        "instrument": normalize_instrument(instrument),
        "timeframe": normalize_timeframe(timeframe),
        "strategy": normalize_strategy_name(strategy),
        "parameters": norm_params,
        "horizon_days": int(horizon_days),
        "methodology_version": str(methodology_version),
    }
    raw = _canonical_json(identity_blob)
    return "fam_" + _hash_str(raw)


def experiment_instance_id(
    instrument: str,
    timeframe: str,
    strategy: str,
    parameters: Any,
    horizon_days: int,
    dataset_hash: str,
    dataset_start: str,
    dataset_end: str,
    code_hash: str,
    cost_model_hash: str,
    validation_version: str,
    backtest_engine_version: str,
) -> str:
    """Deterministic experiment INSTANCE identity.

    Encodes exact evidence: the precise data/code/cost/validation conditions
    under which this experiment was executed.
    """
    identity_blob = {
        "instrument": normalize_instrument(instrument),
        "timeframe": normalize_timeframe(timeframe),
        "strategy": normalize_strategy_name(strategy),
        "parameters": normalize_params(parameters),
        "horizon_days": int(horizon_days),
        "dataset_hash": str(dataset_hash) if dataset_hash else "",
        "dataset_start": str(dataset_start) if dataset_start else "",
        "dataset_end": str(dataset_end) if dataset_end else "",
        "code_hash": str(code_hash) if code_hash else "",
        "cost_model_hash": str(cost_model_hash) if cost_model_hash else "",
        "validation_version": str(validation_version) if validation_version else "",
        "backtest_engine_version": str(backtest_engine_version) if backtest_engine_version else "",
    }
    raw = _canonical_json(identity_blob)
    return "inst_" + _hash_str(raw)


# ---------------------------------------------------------------------------
# Derived identity helpers for indexing
# ---------------------------------------------------------------------------

def _compute_cost_model_hash(cost_assumptions: Any) -> str:
    """Deterministic hash of cost/commission/slippage model."""
    if not cost_assumptions or not isinstance(cost_assumptions, dict):
        return _hash_str("no_cost_model")
    # Strip volatile fields
    clean = {}
    for k in sorted(cost_assumptions.keys()):
        v = cost_assumptions[k]
        if k in ("freshness", "captured_at", "timestamp"):
            continue  # skip time-dependent fields
        clean[k] = v
    return _hash_str(_canonical_json(clean))


def _compute_code_hash(code_identity_info: Any) -> str:
    """Deterministic hash of code version identity."""
    if not code_identity_info or not isinstance(code_identity_info, dict):
        return _hash_str("no_code_identity")
    git_rev = code_identity_info.get("git", {}).get("revision", "unknown")
    file_hashes = code_identity_info.get("file_hashes", {})
    combined = _canonical_json({"git_rev": git_rev, "files": file_hashes})
    return _hash_str(combined)


def _compute_dataset_hash(dataset_info: Any) -> str:
    """Extract/merge dataset hashes from manifest dataset_info."""
    if not dataset_info or not isinstance(dataset_info, dict):
        return _hash_str("no_dataset")
    # Merge all dataset entry hashes for a single fingerprint
    hashes = []
    for key in sorted(dataset_info.keys()):
        entry = dataset_info[key]
        h = entry.get("hash", "") if isinstance(entry, dict) else ""
        hashes.append(f"{key}={h}")
    return _hash_str("|".join(hashes))


def _candidate_identity_from_ledger(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Extract identity-relevant fields from a candidate ledger entry."""
    dataset_id = entry.get("dataset_identity", {})
    if isinstance(dataset_id, str):
        dataset_id = {"hash": dataset_id}

    params = entry.get("parameters", entry.get("params", {}))
    cost = entry.get("cost_assumptions", {})

    return {
        "instrument": normalize_instrument(
            entry.get("instrument", entry.get("ticker", ""))
        ),
        "timeframe": normalize_timeframe(entry.get("timeframe", "")),
        "strategy": normalize_strategy_name(entry.get("strategy", "")),
        "parameters": normalize_params(params),
        "horizon_days": int(entry.get("horizon", entry.get("horizon_days", 60))),
        "dataset_hash": str(dataset_id.get("hash", "")),
        "dataset_start": str(dataset_id.get("actual_start", "")),
        "dataset_end": str(dataset_id.get("actual_end", "")),
        "cost_model_hash": _compute_cost_model_hash(cost),
        "code_hash": "",  # filled from manifest when available
        "validation_version": "",
        "backtest_engine_version": "",
    }


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify_candidate(
    candidate_identity: Dict[str, Any],
    existing_instances: List[Dict[str, Any]],
    existing_families: List[Dict[str, Any]],
) -> str:
    """Classify a candidate relative to existing experiment history.

    Returns one of:
      NEW, EXACT_DUPLICATE, REVALIDATION, METHODOLOGY_CHANGE,
      CODE_CHANGE, COST_MODEL_CHANGE, INCOMPARABLE

    This is observation-only: the classification does NOT change
    whether the experiment executes.
    """
    if not existing_families and not existing_instances:
        return "NEW"

    # Check for exact instance match first
    if existing_instances:
        for inst in existing_instances:
            if _identical_instance(candidate_identity, inst):
                return "EXACT_DUPLICATE"

    # Find matching families
    matching_families = [
        f for f in existing_families
        if _same_family_identity(candidate_identity, f)
    ]

    if not matching_families:
        return "NEW"

    # Same family but different instance — determine the nature of change
    # Compare against all matching instances to find the strongest signal
    for inst_match in _find_family_instances(matching_families, existing_instances):
        diffs = _compute_differences(candidate_identity, inst_match)

        if diffs.get("methodology_changed"):
            return "METHODOLOGY_CHANGE"
        if diffs.get("code_changed"):
            return "CODE_CHANGE"
        if diffs.get("cost_changed"):
            return "COST_MODEL_CHANGE"

    # Same family, same code/cost/methodology but different data → revalidation
    # If we have matching family and nothing else changed, it's a revalidation
    return "REVALIDATION"


def _identical_instance(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """Check if two instances are identical on ALL identity dimensions."""
    fields = [
        "instrument", "timeframe", "strategy", "horizon_days",
        "dataset_hash", "dataset_start", "dataset_end",
        "code_hash", "cost_model_hash", "validation_version",
        "backtest_engine_version",
    ]
    for f in fields:
        va = str(a.get(f, "")).strip().lower()
        vb = str(b.get(f, "")).strip().lower()
        if va != vb:
            return False
    # Compare normalized params
    pa = normalize_params(a.get("parameters", {}))
    pb = normalize_params(b.get("parameters", {}))
    return pa == pb


def _same_family_identity(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """Check if two candidates belong to the same experiment family."""
    fields = ["instrument", "timeframe", "strategy", "horizon_days"]
    for f in fields:
        va = str(a.get(f, "")).strip().lower()
        vb = str(b.get(f, "")).strip().lower()
        if va != vb:
            return False
    pa = normalize_params(a.get("parameters", {}))
    # Family DB rows use 'normalized_parameters' (JSON string);
    # candidate identity dicts use 'parameters' (dict).
    pb_raw = b.get("parameters")
    if pb_raw is None:
        np = b.get("normalized_parameters", "{}")
        try:
            pb_raw = json.loads(np) if isinstance(np, str) else np
        except (json.JSONDecodeError, TypeError):
            pb_raw = {}
    pb = normalize_params(pb_raw if isinstance(pb_raw, dict) else {})
    return pa == pb


def _find_family_instances(
    families: List[Dict[str, Any]],
    instances: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Find instances that belong to any of the given families."""
    family_ids = {f.get("experiment_family_id") for f in families}
    return [i for i in instances if i.get("experiment_family_id") in family_ids]


def _compute_differences(
    candidate: Dict[str, Any],
    existing: Dict[str, Any],
) -> Dict[str, bool]:
    """Compute what dimensions differ between candidate and existing instance."""
    def _changed(field: str) -> bool:
        va = str(candidate.get(field, "")).strip().lower()
        vb = str(existing.get(field, "")).strip().lower()
        return va != vb

    return {
        "data_changed": _changed("dataset_hash") or _changed("dataset_start") or _changed("dataset_end"),
        "code_changed": _changed("code_hash"),
        "cost_changed": _changed("cost_model_hash"),
        "methodology_changed": _changed("validation_version") or _changed("backtest_engine_version"),
    }


# ---------------------------------------------------------------------------
# SQLite Memory Store
# ---------------------------------------------------------------------------

class ExperimentMemory:
    """Durable experiment memory backed by SQLite.

    Store: state/experiment_memory.db (separate from analytics.db).
    Supports:
      - deterministic upsert/indexing
      - query by family/instance/strategy/instrument/timeframe
      - query by run_id/config_key
      - classification of new candidates
      - idempotent indexing
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or Path("state") / MEMORY_DB_NAME
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._ensure_schema()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path),
                timeout=10,
                isolation_level="DEFERRED",
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def _ensure_schema(self) -> None:
        conn = self._connect()
        conn.executescript(_SCHEMA_SQL)
        conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def index_run(self, run_bundle_path: Path) -> Dict[str, Any]:
        """Index all candidates from a canonical completed run.

        Args:
            run_bundle_path: Path to the run bundle directory
                             (e.g. reports/strategy_architect/runs/run_xxx/)

        Returns:
            Indexing report dict with counts and classifications.
        """
        report: Dict[str, Any] = {
            "run_id": "",
            "status": "UNKNOWN",
            "instances_indexed": 0,
            "instances_skipped": 0,
            "families_indexed": 0,
            "classifications": {},
            "errors": [],
        }

        # Validate run bundle
        manifest_path = run_bundle_path / "manifest.json"
        if not manifest_path.exists():
            report["errors"].append("manifest.json not found")
            report["status"] = "NO_MANIFEST"
            return report

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as e:
            report["errors"].append(f"manifest read error: {e}")
            report["status"] = "MANIFEST_CORRUPT"
            return report

        run_status = manifest.get("status", "UNKNOWN")
        if run_status != "COMPLETED":
            report["errors"].append(f"run status is {run_status}, only COMPLETED can be indexed")
            report["status"] = "NOT_COMPLETED"
            report["run_id"] = manifest.get("run_id", "")
            return report

        run_id = manifest.get("run_id", "")
        report["run_id"] = run_id

        # Read ledger
        ledger_path = run_bundle_path / "candidates.jsonl"
        if not ledger_path.exists():
            report["errors"].append("candidates.jsonl not found")
            report["status"] = "NO_LEDGER"
            return report

        # Extract code identity from manifest
        code_id = manifest.get("code_version", {})
        code_hash = _compute_code_hash(code_id)

        # Extract backtest engine version
        backtest_engine = manifest.get("backtest_engine_version", "unknown")

        # Extract validation/methodology version from plan
        validation_version = manifest.get("schema_version", MEMORY_SCHEMA_VERSION)

        # Parse candidates
        candidates = []
        for line in ledger_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                candidates.append(json.loads(line))
            except json.JSONDecodeError as e:
                report["errors"].append(f"corrupt ledger line: {e}")

        conn = self._connect()
        now = datetime.now(timezone.utc).isoformat()

        # Idempotency: check if this run is already indexed
        existing = conn.execute(
            "SELECT COUNT(*) as cnt FROM experiment_instances WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if existing and existing["cnt"] > 0:
            report["status"] = "ALREADY_INDEXED"
            report["instances_indexed"] = existing["cnt"]
            report["instances_skipped"] = len(candidates)
            return report

        families_seen: Dict[str, bool] = {}

        for entry in candidates:
            try:
                config_key = entry.get("config_key", "")
                if not config_key:
                    report["errors"].append("candidate missing config_key")
                    report["instances_skipped"] += 1
                    continue

                # Extract identity
                ident = _candidate_identity_from_ledger(entry)
                ident["code_hash"] = code_hash
                ident["validation_version"] = validation_version
                ident["backtest_engine_version"] = backtest_engine

                # Compute IDs
                fid = experiment_family_id(
                    ident["instrument"], ident["timeframe"], ident["strategy"],
                    ident["parameters"], ident["horizon_days"], validation_version,
                )
                iid = experiment_instance_id(**{
                    k: ident[k] for k in _INSTANCE_IDENTITY_KEYS
                })

                # Classify
                existing_instances = self._query_all_instances(conn)
                existing_families = self._query_all_families(conn)
                classification = classify_candidate(
                    ident, existing_instances, existing_families,
                )

                # Upsert family
                if fid not in families_seen:
                    conn.execute(
                        """INSERT OR REPLACE INTO experiment_families
                           (experiment_family_id, instrument, timeframe, strategy,
                            normalized_parameters, horizon_days, methodology_version,
                            created_at, last_seen_at, schema_version)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            fid,
                            ident["instrument"],
                            ident["timeframe"],
                            ident["strategy"],
                            _canonical_json(ident["parameters"]),
                            ident["horizon_days"],
                            validation_version,
                            now, now, MEMORY_SCHEMA_VERSION,
                        ),
                    )
                    families_seen[fid] = True

                # Extract metrics
                metrics = entry.get("metrics", {})

                # Insert instance
                conn.execute(
                    """INSERT OR REPLACE INTO experiment_instances
                       (experiment_instance_id, experiment_family_id,
                        run_id, config_key,
                        instrument, timeframe, strategy, parameters_json, horizon_days,
                        dataset_hash, dataset_start, dataset_end,
                        code_hash, cost_model_hash,
                        validation_version, backtest_engine_version,
                        classification,
                        status, eligible, reject_reasons,
                        metrics_json, error_type, error_message,
                        started_at, finished_at, indexed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                               ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        iid, fid,
                        run_id, config_key,
                        ident["instrument"], ident["timeframe"], ident["strategy"],
                        _canonical_json(ident["parameters"]), ident["horizon_days"],
                        ident["dataset_hash"], ident["dataset_start"], ident["dataset_end"],
                        ident["code_hash"], ident["cost_model_hash"],
                        validation_version, backtest_engine,
                        classification,
                        entry.get("status", "tested"),
                        1 if entry.get("eligible") else 0,
                        json.dumps(entry.get("reject_reasons", []), ensure_ascii=False),
                        json.dumps(metrics, ensure_ascii=False) if metrics else "{}",
                        entry.get("error_type"),
                        entry.get("error_message"),
                        entry.get("started_at", now),
                        entry.get("finished_at", now),
                        now,
                    ),
                )

                report["instances_indexed"] += 1
                cls_key = classification
                report["classifications"][cls_key] = report["classifications"].get(cls_key, 0) + 1

            except Exception as e:
                report["errors"].append(f"error indexing candidate: {e}")
                report["instances_skipped"] += 1

        report["families_indexed"] = len(families_seen)

        # Record the run as indexed
        conn.execute(
            """INSERT OR REPLACE INTO indexed_runs
               (run_id, run_bundle_path, indexed_at, instance_count, schema_version)
               VALUES (?, ?, ?, ?, ?)""",
            (run_id, str(run_bundle_path), now, report["instances_indexed"], MEMORY_SCHEMA_VERSION),
        )

        conn.commit()
        report["status"] = "INDEXED"
        return report

    # ------------------------------------------------------------------
    # Backfill
    # ------------------------------------------------------------------

    def backfill(self, runs_dir: Path) -> Dict[str, Any]:
        """Backfill all canonical completed runs from a runs directory.

        Only indexes Iteration 05+ canonical runs with COMPLETED status.
        Legacy/pre-canonical artifacts are recorded as LEGACY_UNINDEXED.

        Args:
            runs_dir: Path to reports/strategy_architect/runs/

        Returns:
            Backfill report with counts.
        """
        report: Dict[str, Any] = {
            "runs_discovered": 0,
            "runs_indexed": 0,
            "runs_skipped": 0,
            "instances_indexed": 0,
            "exact_duplicates_found": 0,
            "revalidations_found": 0,
            "incomparable_count": 0,
            "legacy_count": 0,
            "errors": [],
        }

        if not runs_dir.exists():
            report["errors"].append(f"runs directory does not exist: {runs_dir}")
            return report

        # Discover run directories
        run_dirs = sorted([
            d for d in runs_dir.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ])
        report["runs_discovered"] = len(run_dirs)

        for run_dir in run_dirs:
            result = self.index_run(run_dir)
            report["errors"].extend(result.get("errors", []))

            if result["status"] == "INDEXED":
                report["runs_indexed"] += 1
                report["instances_indexed"] += result.get("instances_indexed", 0)
                for cls, cnt in result.get("classifications", {}).items():
                    if cls == "EXACT_DUPLICATE":
                        report["exact_duplicates_found"] += cnt
                    elif cls == "REVALIDATION":
                        report["revalidations_found"] += cnt
                    elif cls == "INCOMPARABLE":
                        report["incomparable_count"] += cnt
            elif result["status"] == "ALREADY_INDEXED":
                report["runs_skipped"] += 1
            else:
                report["runs_skipped"] += 1
                if "NOT_COMPLETED" in result["status"]:
                    report["legacy_count"] += 1

        return report

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def find_history(self, experiment_family_id: str) -> List[Dict[str, Any]]:
        """Find all instances belonging to an experiment family.

        Returns instances ordered by indexed_at (chronological).
        """
        conn = self._connect()
        rows = conn.execute(
            """SELECT * FROM experiment_instances
               WHERE experiment_family_id = ?
               ORDER BY indexed_at ASC""",
            (experiment_family_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def find_exact_instance(self, experiment_instance_id: str) -> Optional[Dict[str, Any]]:
        """Find one exact instance by its instance ID."""
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM experiment_instances WHERE experiment_instance_id = ?",
            (experiment_instance_id,),
        ).fetchone()
        return dict(row) if row else None

    def find_by_run(self, run_id: str) -> List[Dict[str, Any]]:
        """Find all instances from a specific run."""
        conn = self._connect()
        rows = conn.execute(
            """SELECT * FROM experiment_instances
               WHERE run_id = ?
               ORDER BY indexed_at ASC""",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def find_by_config_key(self, config_key: str) -> List[Dict[str, Any]]:
        """Find all instances with a specific config_key (may span runs)."""
        conn = self._connect()
        rows = conn.execute(
            """SELECT * FROM experiment_instances
               WHERE config_key = ?
               ORDER BY indexed_at ASC""",
            (config_key,),
        ).fetchall()
        return [dict(r) for r in rows]

    def classify_candidate(
        self,
        instrument: str,
        timeframe: str,
        strategy: str,
        parameters: Any,
        horizon_days: int = 60,
        dataset_hash: str = "",
        dataset_start: str = "",
        dataset_end: str = "",
        code_hash: str = "",
        cost_model_hash: str = "",
        validation_version: str = "",
        backtest_engine_version: str = "",
    ) -> Dict[str, Any]:
        """Classify a candidate against experiment history.

        Returns:
            {
                "classification": str,
                "experiment_family_id": str or None,
                "prior_instance_count": int,
                "last_seen_at": str or None,
                "related_instance_ids": [str],
            }

        OBSERVATION ONLY: classification does NOT change execution.
        """
        ident = {
            "instrument": normalize_instrument(instrument),
            "timeframe": normalize_timeframe(timeframe),
            "strategy": normalize_strategy_name(strategy),
            "parameters": normalize_params(parameters),
            "horizon_days": int(horizon_days),
            "dataset_hash": dataset_hash,
            "dataset_start": dataset_start,
            "dataset_end": dataset_end,
            "code_hash": code_hash,
            "cost_model_hash": cost_model_hash,
            "validation_version": validation_version,
            "backtest_engine_version": backtest_engine_version,
        }

        conn = self._connect()
        existing_families = [
            dict(r) for r in conn.execute("SELECT * FROM experiment_families").fetchall()
        ]
        existing_instances = self._query_all_instances(conn)

        classification = classify_candidate(ident, existing_families, existing_instances)

        # Find matching family for metadata
        fid = experiment_family_id(
            ident["instrument"], ident["timeframe"], ident["strategy"],
            ident["parameters"], ident["horizon_days"], validation_version,
        )

        family_instances = self.find_history(fid)

        result: Dict[str, Any] = {
            "classification": classification,
            "experiment_family_id": fid,
            "experiment_family_found": any(f["experiment_family_id"] == fid for f in existing_families),
            "prior_instance_count": len(family_instances),
            "last_seen_at": family_instances[-1]["indexed_at"] if family_instances else None,
            "related_instance_ids": [i["experiment_instance_id"] for i in family_instances],
        }
        return result

    def recent_history(
        self,
        strategy: Optional[str] = None,
        instrument: Optional[str] = None,
        timeframe: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Query recent experiment instances with optional filters.

        Returns the most recent instances, optionally filtered by
        strategy, instrument, and/or timeframe.
        """
        conn = self._connect()
        conditions = []
        params: List[Any] = []

        if strategy:
            conditions.append("strategy = ?")
            params.append(normalize_strategy_name(strategy))
        if instrument:
            conditions.append("instrument = ?")
            params.append(normalize_instrument(instrument))
        if timeframe:
            conditions.append("timeframe = ?")
            params.append(normalize_timeframe(timeframe))

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"""SELECT * FROM experiment_instances
                    {where}
                    ORDER BY indexed_at DESC
                    LIMIT ?"""
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _query_all_instances(self, conn: sqlite3.Connection) -> List[Dict[str, Any]]:
        rows = conn.execute("SELECT * FROM experiment_instances").fetchall()
        result = []
        for r in rows:
            d = dict(r)
            # Deserialize parameters_json → parameters for classification
            try:
                d["parameters"] = json.loads(d.get("parameters_json", "{}"))
            except (json.JSONDecodeError, TypeError):
                d["parameters"] = {}
            result.append(d)
        return result

    def _query_all_families(self, conn: sqlite3.Connection) -> List[Dict[str, Any]]:
        rows = conn.execute("SELECT * FROM experiment_families").fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def summary(self) -> Dict[str, Any]:
        """Generate a derived summary report (not source of truth)."""
        conn = self._connect()

        total_families = conn.execute(
            "SELECT COUNT(*) as cnt FROM experiment_families"
        ).fetchone()["cnt"]

        total_instances = conn.execute(
            "SELECT COUNT(*) as cnt FROM experiment_instances"
        ).fetchone()["cnt"]

        cls_rows = conn.execute(
            """SELECT classification, COUNT(*) as cnt
               FROM experiment_instances
               GROUP BY classification"""
        ).fetchall()
        classifications = {r["classification"]: r["cnt"] for r in cls_rows}

        # Most retested families
        top_families = conn.execute(
            """SELECT experiment_family_id, strategy, instrument, timeframe,
                      COUNT(*) as cnt
               FROM experiment_instances
               GROUP BY experiment_family_id
               ORDER BY cnt DESC
               LIMIT 10"""
        ).fetchall()

        # Recently seen
        recent = conn.execute(
            """SELECT experiment_family_id, strategy, instrument, timeframe,
                      MAX(indexed_at) as last_seen
               FROM experiment_instances
               GROUP BY experiment_family_id
               ORDER BY last_seen DESC
               LIMIT 10"""
        ).fetchall()

        runs_indexed = conn.execute(
            "SELECT COUNT(*) as cnt FROM indexed_runs"
        ).fetchone()["cnt"]

        return {
            "schema_version": MEMORY_SCHEMA_VERSION,
            "total_families": total_families,
            "total_instances": total_instances,
            "runs_indexed": runs_indexed,
            "classifications": classifications,
            "exact_duplicates": classifications.get("EXACT_DUPLICATE", 0),
            "revalidations": classifications.get("REVALIDATION", 0),
            "code_changes": classifications.get("CODE_CHANGE", 0),
            "cost_changes": classifications.get("COST_MODEL_CHANGE", 0),
            "methodology_changes": classifications.get("METHODOLOGY_CHANGE", 0),
            "incomparable": classifications.get("INCOMPARABLE", 0),
            "new_experiments": classifications.get("NEW", 0),
            "most_retested_families": [dict(r) for r in top_families],
            "recently_seen": [dict(r) for r in recent],
        }


# ---------------------------------------------------------------------------
# SQLite schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
-- Experiment Memory schema v1.0.0 (Iteration 07)
-- Separate from analytics.db — this is research knowledge index only.

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_meta (key, value)
VALUES ('schema_version', '1.0.0'),
       ('created_at', datetime('now'));

-- Experiment families: the normalized hypothesis identity.
CREATE TABLE IF NOT EXISTS experiment_families (
    experiment_family_id   TEXT PRIMARY KEY,
    instrument             TEXT NOT NULL,
    timeframe              TEXT NOT NULL,
    strategy               TEXT NOT NULL,
    normalized_parameters  TEXT NOT NULL,  -- canonical JSON
    horizon_days           INTEGER NOT NULL,
    methodology_version    TEXT NOT NULL DEFAULT 'default',
    created_at             TEXT NOT NULL,
    last_seen_at           TEXT NOT NULL,
    schema_version         TEXT NOT NULL DEFAULT '1.0.0'
);

CREATE INDEX IF NOT EXISTS idx_fam_strategy ON experiment_families(strategy);
CREATE INDEX IF NOT EXISTS idx_fam_instrument ON experiment_families(instrument);
CREATE INDEX IF NOT EXISTS idx_fam_timeframe ON experiment_families(timeframe);
CREATE INDEX IF NOT EXISTS idx_fam_instrument_strategy ON experiment_families(instrument, strategy);

-- Experiment instances: exact execution evidence.
-- PK is (run_id, experiment_instance_id) because two different runs may
-- produce the same instance_id if evidence is identical (EXACT_DUPLICATE).
CREATE TABLE IF NOT EXISTS experiment_instances (
    experiment_instance_id   TEXT NOT NULL,
    experiment_family_id     TEXT NOT NULL,

    run_id                   TEXT NOT NULL,
    config_key               TEXT NOT NULL,

    instrument               TEXT NOT NULL DEFAULT '',
    timeframe                TEXT NOT NULL DEFAULT '',
    strategy                 TEXT NOT NULL DEFAULT '',
    parameters_json          TEXT NOT NULL DEFAULT '{}',
    horizon_days             INTEGER NOT NULL DEFAULT 60,

    dataset_hash             TEXT NOT NULL DEFAULT '',
    dataset_start            TEXT NOT NULL DEFAULT '',
    dataset_end              TEXT NOT NULL DEFAULT '',

    code_hash                TEXT NOT NULL DEFAULT '',
    cost_model_hash          TEXT NOT NULL DEFAULT '',
    validation_version       TEXT NOT NULL DEFAULT '',
    backtest_engine_version  TEXT NOT NULL DEFAULT '',

    classification           TEXT NOT NULL DEFAULT 'NEW',

    status                   TEXT NOT NULL DEFAULT 'tested',
    eligible                 INTEGER NOT NULL DEFAULT 0,
    reject_reasons           TEXT NOT NULL DEFAULT '[]',
    metrics_json             TEXT NOT NULL DEFAULT '{}',
    error_type               TEXT,
    error_message            TEXT,

    started_at               TEXT NOT NULL DEFAULT '',
    finished_at              TEXT NOT NULL DEFAULT '',
    indexed_at               TEXT NOT NULL DEFAULT '',

    PRIMARY KEY (run_id, experiment_instance_id)
);

CREATE INDEX IF NOT EXISTS idx_inst_family ON experiment_instances(experiment_family_id);
CREATE INDEX IF NOT EXISTS idx_inst_run ON experiment_instances(run_id);
CREATE INDEX IF NOT EXISTS idx_inst_config ON experiment_instances(config_key);
CREATE INDEX IF NOT EXISTS idx_inst_strategy ON experiment_instances(strategy);
CREATE INDEX IF NOT EXISTS idx_inst_instrument ON experiment_instances(instrument);
CREATE INDEX IF NOT EXISTS idx_inst_timeframe ON experiment_instances(timeframe);
CREATE INDEX IF NOT EXISTS idx_inst_classification ON experiment_instances(classification);
CREATE INDEX IF NOT EXISTS idx_inst_instrument_strategy ON experiment_instances(instrument, strategy);
CREATE INDEX IF NOT EXISTS idx_inst_eligible ON experiment_instances(eligible);

-- Indexed runs: provenance record of which runs have been backfilled.
CREATE TABLE IF NOT EXISTS indexed_runs (
    run_id            TEXT PRIMARY KEY,
    run_bundle_path   TEXT NOT NULL,
    indexed_at        TEXT NOT NULL,
    instance_count    INTEGER NOT NULL DEFAULT 0,
    schema_version    TEXT NOT NULL DEFAULT '1.0.0'
);
"""

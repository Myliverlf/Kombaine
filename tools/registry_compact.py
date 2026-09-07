#!/usr/bin/env python3
"""Compact strategy registry by archiving stale terminal records.

Archives records with status in {superseded, rejected, dropped} and age > 7 days
from the canonical registry into state/registry_archive_YYYYMMDD.json, while
keeping the active registry focused on current records.

Safe/idempotent:
- writes a .bak copy before modifying the registry file
- preserves the registry schema and top-level metadata
- leaves the registry unchanged when nothing qualifies
"""
from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = PROJECT_ROOT / "state"
REGISTRY_PATH = STATE_DIR / "strategy_registry.json"
ARCHIVE_STATUSES = {"superseded", "rejected", "dropped"}
RETENTION_DAYS = 7


@dataclass
class CompactResult:
    registry_path: Path
    backup_path: Path
    archive_path: Path | None
    archived_count: int
    remaining_count: int


def _timestamp_to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _record_age_seconds(record: dict[str, Any]) -> float | None:
    ts_values = [
        _timestamp_to_float(record.get("updated_ts")),
        _timestamp_to_float(record.get("created_ts")),
    ]
    ts_values = [ts for ts in ts_values if ts is not None]
    if not ts_values:
        return None
    latest = max(ts_values)
    return max(0.0, datetime.now(timezone.utc).timestamp() - latest)


def _load_registry(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("strategy_registry.json must be a JSON object")
    data.setdefault("strategies", {})
    data.setdefault("events", [])
    return data


def compact_registry(registry_path: Path = REGISTRY_PATH, retention_days: int = RETENTION_DAYS) -> CompactResult:
    if not registry_path.exists():
        raise FileNotFoundError(registry_path)

    data = _load_registry(registry_path)
    strategies = data.get("strategies", {})
    if not isinstance(strategies, dict):
        raise ValueError("strategy_registry.json['strategies'] must be a dict")

    cutoff_seconds = retention_days * 86400
    archived: dict[str, Any] = {}
    active: dict[str, Any] = {}

    for strategy_id, record in strategies.items():
        if not isinstance(record, dict):
            active[strategy_id] = record
            continue
        status = str(record.get("status") or "").lower()
        age = _record_age_seconds(record)
        if status in ARCHIVE_STATUSES and age is not None and age > cutoff_seconds:
            archived[strategy_id] = record
        else:
            active[strategy_id] = record

    backup_path = registry_path.with_suffix(registry_path.suffix + ".bak")
    shutil.copy2(registry_path, backup_path)

    archive_path: Path | None = None
    if archived:
        archive_path = STATE_DIR / f"registry_archive_{datetime.now(timezone.utc):%Y%m%d}.json"
        archive_payload = {
            "version": data.get("version", 1),
            "created_ts": data.get("created_ts"),
            "updated_ts": datetime.now(timezone.utc).timestamp(),
            "retention_days": retention_days,
            "archived_count": len(archived),
            "archived_statuses": sorted(ARCHIVE_STATUSES),
            "strategies": archived,
        }
        if archive_path.exists():
            try:
                existing = json.loads(archive_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                existing = None
            if isinstance(existing, dict) and isinstance(existing.get("strategies"), dict):
                merged = dict(existing["strategies"])
                merged.update(archived)
                archive_payload["strategies"] = merged
                archive_payload["archived_count"] = len(merged)
        archive_path.write_text(json.dumps(archive_payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    data["strategies"] = active
    data["updated_ts"] = datetime.now(timezone.utc).timestamp()
    registry_path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    return CompactResult(
        registry_path=registry_path,
        backup_path=backup_path,
        archive_path=archive_path,
        archived_count=len(archived),
        remaining_count=len(active),
    )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    path = REGISTRY_PATH
    if argv:
        path = Path(argv[0])
    result = compact_registry(path)
    print(json.dumps({
        "registry_path": str(result.registry_path),
        "backup_path": str(result.backup_path),
        "archive_path": str(result.archive_path) if result.archive_path else None,
        "archived_count": result.archived_count,
        "remaining_count": result.remaining_count,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

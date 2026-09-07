#!/usr/bin/env python3
"""Recent-log collector for the daily audit.

Read-only helper: scans local logs and recent JSON/Markdown reports for
WARN/ERROR/FAIL signals within a time window and returns a structured list.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = PROJECT_ROOT
DEFAULT_SINCE = "24h"
DEFAULT_LOG_DIRS = ("logs",)
DEFAULT_REPORT_DIRS: tuple[str, ...] = ()
TEXT_SUFFIXES = {".log", ".md", ".txt", ".json"}
KEYWORDS = (
    ("CRITICAL", "ERROR"),
    ("ERROR", "ERROR"),
    ("FAIL", "FAIL"),
    ("WARNING", "WARN"),
    ("WARN", "WARN"),
    ("ALERT", "ERROR"),
)
LINE_RE = re.compile(r"(?i)\b(critical|error|fail|warning|warn|alert)\b")


@dataclass(frozen=True)
class IssueEntry:
    level: str
    source: str
    path: str
    message: str
    excerpt: str

    def as_dict(self) -> dict[str, str]:
        return {
            "level": self.level,
            "source": self.source,
            "path": self.path,
            "message": self.message,
            "excerpt": self.excerpt,
        }


def parse_since(spec: str) -> timedelta:
    value = str(spec).strip().lower()
    if not value:
        raise ValueError("since must not be empty")
    unit = value[-1]
    number_part = value[:-1] if unit.isalpha() else value
    if unit.isalpha():
        try:
            amount = float(number_part)
        except ValueError as exc:
            raise ValueError(f"invalid since value: {spec!r}") from exc
        if unit == "s":
            return timedelta(seconds=amount)
        if unit == "m":
            return timedelta(minutes=amount)
        if unit == "h":
            return timedelta(hours=amount)
        if unit == "d":
            return timedelta(days=amount)
        raise ValueError(f"unsupported since unit: {unit!r}")
    try:
        amount = float(value)
    except ValueError as exc:
        raise ValueError(f"invalid since value: {spec!r}") from exc
    return timedelta(hours=amount)


def _time_cutoff(spec: str) -> datetime:
    return datetime.now(timezone.utc) - parse_since(spec)


def _candidate_dirs(root: Path) -> list[Path]:
    dirs: list[Path] = []
    for rel in DEFAULT_LOG_DIRS + DEFAULT_REPORT_DIRS:
        candidate = root / rel
        if candidate.exists() and candidate.is_dir():
            dirs.append(candidate)
    parent_logs = root.parent / "logs"
    if parent_logs.exists() and parent_logs.is_dir() and parent_logs not in dirs:
        dirs.append(parent_logs)
    return dirs


def _iter_recent_files(root: Path, since: str) -> Iterable[Path]:
    cutoff = _time_cutoff(since)
    seen: set[Path] = set()
    for directory in _candidate_dirs(root):
        for dirpath, _, filenames in os.walk(directory):
            for filename in filenames:
                path = Path(dirpath) / filename
                if path in seen:
                    continue
                seen.add(path)
                if path.suffix.lower() not in TEXT_SUFFIXES:
                    continue
                try:
                    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                except OSError:
                    continue
                if mtime >= cutoff:
                    yield path


def _coerce_level(token: str | None, default: str = "ERROR") -> str:
    if not token:
        return default
    token = token.upper()
    if token in {"WARN", "WARNING"}:
        return "WARN"
    if token in {"FAIL", "ERROR", "CRITICAL", "ALERT"}:
        return "ERROR"
    return default


def _record(entries: list[IssueEntry], level: str, source: str, path: Path, message: str, excerpt: str) -> None:
    entries.append(
        IssueEntry(
            level=level,
            source=source,
            path=str(path),
            message=message.strip(),
            excerpt=excerpt.strip(),
        )
    )


TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})")


def _line_is_recent(line: str, cutoff: datetime) -> bool:
    match = TS_RE.search(line)
    if not match:
        # No timestamp: include it only when the file mtime made the file recent.
        return True
    try:
        ts = datetime.fromisoformat(f"{match.group(1)}T{match.group(2)}+00:00")
    except ValueError:
        return True
    return ts >= cutoff


def _scan_text(path: Path, text: str, source: str, only_issues: bool, entries: list[IssueEntry], cutoff: datetime) -> None:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if not _line_is_recent(line, cutoff):
            continue
        match = LINE_RE.search(line)
        if not match:
            continue
        level = _coerce_level(match.group(1))
        lowered = line.lower()
        if "grpc_status:8" in lowered or "resource_exhausted" in lowered or "ratelimit" in lowered:
            level = "WARN"
        if only_issues and level != "ERROR":
            continue
        _record(entries, level, source, path, line, line)


def _walk_json(prefix: str, value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            new_prefix = f"{prefix}.{key}" if prefix else str(key)
            yield from _walk_json(new_prefix, child)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            new_prefix = f"{prefix}[{index}]" if prefix else f"[{index}]"
            yield from _walk_json(new_prefix, child)
    else:
        yield prefix, value


def _scan_json(path: Path, payload: Any, source: str, only_issues: bool, entries: list[IssueEntry]) -> None:
    if isinstance(payload, dict):
        verdict = str(payload.get("verdict") or "").upper()
        if verdict in {"FAIL", "ERROR"}:
            _record(entries, "ERROR", source, path, f"verdict={verdict}", verdict)
        issues = payload.get("issues")
        if isinstance(issues, list):
            for item in issues:
                if item is None:
                    continue
                level = "ERROR"
                text = str(item)
                if only_issues and level != "ERROR":
                    continue
                _record(entries, level, source, path, text, text)
        warnings = payload.get("warnings")
        if isinstance(warnings, list) and not only_issues:
            for item in warnings:
                if item is None:
                    continue
                text = str(item)
                _record(entries, "WARN", source, path, text, text)
        checks = payload.get("checks")
        if isinstance(checks, list):
            for check in checks:
                if not isinstance(check, dict):
                    continue
                if check.get("ok") is False:
                    severity = _coerce_level(str(check.get("severity") or "ERROR"))
                    if only_issues and severity != "ERROR":
                        continue
                    detail = str(check.get("detail") or check.get("name") or "check failed")
                    name = str(check.get("name") or "check")
                    _record(entries, severity, source, path, f"{name}: {detail}", detail)
    for key_path, value in _walk_json("", payload):
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                continue
            if not LINE_RE.search(raw):
                continue
            level = _coerce_level(next((token for token in ("CRITICAL", "ERROR", "FAIL", "WARN", "WARNING", "ALERT") if token in raw.upper()), None))
            if only_issues and level != "ERROR":
                continue
            _record(entries, level, source, path, f"{key_path}: {raw}", raw)


def collect_recent_issues(root: Path | str = DEFAULT_ROOT, since: str = DEFAULT_SINCE, only_issues: bool = False) -> dict[str, Any]:
    root_path = Path(root).resolve()
    cutoff = _time_cutoff(since)
    files = list(_iter_recent_files(root_path, since))
    entries: list[IssueEntry] = []

    for path in files:
        source = path.relative_to(root_path).as_posix() if root_path in path.parents else path.as_posix()
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            _record(entries, "ERROR", source, path, f"read_failed: {type(exc).__name__}: {exc}", str(exc))
            continue
        if path.suffix.lower() == ".json":
            try:
                payload = json.loads(content)
            except json.JSONDecodeError as exc:
                _record(entries, "ERROR", source, path, f"json_decode_failed: {exc.msg}", content[:240])
                continue
            _scan_json(path, payload, source, only_issues, entries)
        else:
            _scan_text(path, content, source, only_issues, entries, cutoff)

    issues = [entry.as_dict() for entry in entries if entry.level == "ERROR"]
    warnings = [entry.as_dict() for entry in entries if entry.level == "WARN"]
    counts = {
        "files_scanned": len(files),
        "issues": len(issues),
        "warnings": len(warnings),
        "entries": len(entries),
    }
    return {
        "root": str(root_path),
        "since": since,
        "cutoff": cutoff.isoformat(),
        "files": [str(path) for path in files],
        "counts": counts,
        "issues": issues,
        "warnings": warnings,
        "only_issues": only_issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect recent audit issues from local logs and reports")
    parser.add_argument("--path", default=str(DEFAULT_ROOT), help="project root to scan")
    parser.add_argument("--since", default=DEFAULT_SINCE, help="time window such as 24h, 90m, 2d")
    parser.add_argument("--only-issues", action="store_true", help="return only ERROR/FAIL findings")
    args = parser.parse_args()

    payload = collect_recent_issues(Path(args.path), args.since, args.only_issues)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Deterministic Evidence Collector for the layered agent runtime.

Replaces hardcoded evidence in l3_executor with real subprocess-backed
module verification. Accepts a module path and a check command, runs it,
and returns a structured evidence dict with stdout, return code, and timestamp.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


COLLECTOR_VERSION = "1.0.0"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ModuleEvidence:
    """Structured evidence from a single module verification run."""
    module_path: str
    check_command: List[str]
    stdout: str
    stderr: str
    returncode: int
    verdict: str  # "PASS" | "FAIL" | "ERROR" | "TIMEOUT"
    timestamp: str
    duration_seconds: float
    timeout_seconds: int
    collector_version: str = COLLECTOR_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def collect_module_evidence(
    module_path: str,
    check_command: List[str],
    *,
    timeout_seconds: int = 30,
    working_dir: Optional[str] = None,
) -> ModuleEvidence:
    """Run a check command and return structured evidence.

    Args:
        module_path: Human-readable path of the module under test (for labeling).
        check_command: Command to execute as a list of strings.
        timeout_seconds: Maximum wall-clock seconds before killing the process.
        working_dir: Optional working directory for subprocess.

    Returns:
        ModuleEvidence with verdict PASS/FAIL/ERROR/TIMEOUT.
    """
    start = time.monotonic()
    try:
        result = subprocess.run(
            check_command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=working_dir,
        )
        elapsed = time.monotonic() - start
        stdout = result.stdout
        stderr = result.stderr
        returncode = result.returncode

        if returncode == 0:
            verdict = "PASS"
        else:
            verdict = "FAIL"

    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - start
        stdout = (exc.stdout or b"").decode("utf-8", errors="replace") if exc.stdout else ""
        stderr = (exc.stderr or b"").decode("utf-8", errors="replace") if exc.stderr else ""
        returncode = -1
        verdict = "TIMEOUT"

    except FileNotFoundError as exc:
        elapsed = time.monotonic() - start
        stdout = ""
        stderr = f"Command not found: {exc}"
        returncode = -2
        verdict = "ERROR"

    except OSError as exc:
        elapsed = time.monotonic() - start
        stdout = ""
        stderr = f"OS error: {exc}"
        returncode = -3
        verdict = "ERROR"

    return ModuleEvidence(
        module_path=module_path,
        check_command=check_command,
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
        verdict=verdict,
        timestamp=_now_iso(),
        duration_seconds=round(elapsed, 4),
        timeout_seconds=timeout_seconds,
    )


def collect_batch(
    checks: List[Dict[str, Any]],
    *,
    timeout_seconds: int = 30,
    working_dir: Optional[str] = None,
) -> List[ModuleEvidence]:
    """Run multiple module checks and return all evidence.

    Each item in checks must have keys: module_path, check_command.
    Optional key: timeout_seconds (overrides the batch default).
    """
    results: List[ModuleEvidence] = []
    for check in checks:
        module_path = check.get("module_path", "unknown")
        command = check.get("check_command", [])
        per_timeout = check.get("timeout_seconds", timeout_seconds)
        evidence = collect_module_evidence(
            module_path,
            command,
            timeout_seconds=per_timeout,
            working_dir=working_dir,
        )
        results.append(evidence)
    return results


def summarize_verdicts(evidences: List[ModuleEvidence]) -> Dict[str, Any]:
    """Summarize a batch of evidence into an aggregate dict.

    Returns:
        Dict with keys: total, pass_count, fail_count, error_count,
        timeout_count, overall_verdict.
    """
    counts = {"PASS": 0, "FAIL": 0, "ERROR": 0, "TIMEOUT": 0}
    for e in evidences:
        counts[e.verdict] = counts.get(e.verdict, 0) + 1
    total = len(evidences)
    if counts["ERROR"] > 0 or counts["TIMEOUT"] > 0:
        overall = "ERROR"
    elif counts["FAIL"] > 0:
        overall = "FAIL"
    elif total > 0:
        overall = "PASS"
    else:
        overall = "NO_DATA"

    return {
        "total": total,
        "pass_count": counts["PASS"],
        "fail_count": counts["FAIL"],
        "error_count": counts["ERROR"],
        "timeout_count": counts["TIMEOUT"],
        "overall_verdict": overall,
    }

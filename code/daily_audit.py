#!/usr/bin/env python3
"""Daily audit cron target for strategy_combine.

Safe read-only orchestration:
- live_watchdog on local dry-run fixtures
- market_session_preflight without broker probe
- supervisor_pickup_dryrun
- recent local logs/reports

Silent on OK. Emits alerts only when issues are detected.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from daily_audit_logs import collect_recent_issues
from daily_audit_report import write_report

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = PROJECT_ROOT / "code"
REPORT_DIR = PROJECT_ROOT / "reports" / "daily_audit"
WATCHDOG_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "dry-run" / "watchdog" / "pass"


def _parse_json_output(stdout: str) -> dict[str, Any] | None:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    for candidate in reversed(lines):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _run_step(label: str, script: str, *args: str) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(CODE_DIR / script), *args],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    payload = _parse_json_output(proc.stdout) or {}
    ok = proc.returncode == 0
    summary = {
        "ok": ok and bool(payload.get("ok", True)) if label == "live_watchdog" else ok,
        "returncode": proc.returncode,
        "summary": "",
        "payload": payload,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }
    if label == "live_watchdog":
        summary["ok"] = proc.returncode == 0 and bool(payload.get("ok", False))
        summary["summary"] = f"issues={len(payload.get('issues') or [])} warnings={len(payload.get('warnings') or [])}"
    elif label == "market_session_preflight":
        summary["ok"] = proc.returncode == 0 and str(payload.get("verdict", "")).upper() == "PASS"
        summary["summary"] = f"verdict={payload.get('verdict')} failures={payload.get('failures', 0)}"
    elif label == "supervisor_pickup_dryrun":
        summary["ok"] = proc.returncode == 0 and str(payload.get("verdict", "")).upper() in {"PASS", "WARN"}
        summary["summary"] = f"verdict={payload.get('verdict')} free_slots={payload.get('free_slots', 0)}"
    else:
        summary["summary"] = f"returncode={proc.returncode}"
    return summary


def _step_issue(label: str, data: dict[str, Any]) -> dict[str, str]:
    message = data.get("summary") or data.get("stderr") or data.get("stdout") or f"{label} failed"
    return {
        "level": "ERROR",
        "source": label,
        "path": str(CODE_DIR / f"{label}.py"),
        "message": message,
        "excerpt": message,
    }


def run_daily_audit(project_root: Path = PROJECT_ROOT, since: str = "24h", report_dir: Path = REPORT_DIR) -> dict[str, Any]:
    watchdog = _run_step(
        "live_watchdog",
        "live_watchdog.py",
        "--dry-run-dir",
        str(WATCHDOG_FIXTURE),
        "--report-dir",
        str(report_dir.parent / "live_watchdog"),
    )
    preflight = _run_step("market_session_preflight", "market_session_preflight.py")
    supervisor = _run_step("supervisor_pickup_dryrun", "supervisor_pickup_dryrun.py")
    recent_logs = collect_recent_issues(project_root, since=since, only_issues=False)

    issues: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    notes: list[str] = []

    for label, step in (("live_watchdog", watchdog), ("market_session_preflight", preflight), ("supervisor_pickup_dryrun", supervisor)):
        payload = step.get("payload") or {}
        if not step.get("ok", False):
            issues.append(_step_issue(label, step))
        if label == "live_watchdog":
            for item in payload.get("issues") or []:
                issues.append({"level": "ERROR", "source": label, "path": str(project_root / "reports" / "live_watchdog" / "latest.json"), "message": str(item), "excerpt": str(item)})
            for item in payload.get("warnings") or []:
                warnings.append({"level": "WARN", "source": label, "path": str(project_root / "reports" / "live_watchdog" / "latest.json"), "message": str(item), "excerpt": str(item)})
        elif label == "market_session_preflight":
            for check in payload.get("checks") or []:
                if isinstance(check, dict) and not check.get("ok"):
                    level = "ERROR" if str(check.get("severity") or "FAIL").upper() == "FAIL" else "WARN"
                    item = {
                        "level": level,
                        "source": label,
                        "path": str(project_root / "reports" / "strategy_architect" / "market_session_preflight_latest.json"),
                        "message": f"{check.get('name')}: {check.get('detail')}",
                        "excerpt": str(check.get("detail") or check.get("name") or "check failed"),
                    }
                    (issues if level == "ERROR" else warnings).append(item)
        elif label == "supervisor_pickup_dryrun":
            if str(payload.get("verdict", "")).upper() not in {"PASS", "WARN"}:
                issues.append({"level": "ERROR", "source": label, "path": str(project_root / "reports" / "strategy_architect" / "supervisor_pickup_dryrun_latest.json"), "message": f"verdict={payload.get('verdict')}", "excerpt": json.dumps(payload, ensure_ascii=False)[:240]})
    for item in recent_logs.get("issues") or []:
        issues.append(item)
    for item in recent_logs.get("warnings") or []:
        warnings.append(item)

    components = {
        "live_watchdog": {
            "ok": watchdog.get("ok", False),
            "summary": watchdog.get("summary", ""),
            "message": "fixture-driven watchdog on dry-run data",
        },
        "market_session_preflight": {
            "ok": preflight.get("ok", False),
            "summary": preflight.get("summary", ""),
            "message": "read-only preflight without broker mutations",
        },
        "supervisor_pickup_dryrun": {
            "ok": supervisor.get("ok", False),
            "summary": supervisor.get("summary", ""),
            "message": "safe dry-run contour for promotion logic",
        },
        "recent_logs": {
            "ok": not recent_logs.get("issues"),
            "summary": f"issues={recent_logs.get('counts', {}).get('issues', 0)} warnings={recent_logs.get('counts', {}).get('warnings', 0)} files={recent_logs.get('counts', {}).get('files_scanned', 0)}",
            "message": "local logs and recent reports within the audit window",
        },
    }

    ok = not issues
    payload = {
        "checked_at": preflight.get("payload", {}).get("checked_at") or watchdog.get("payload", {}).get("ts") or "",
        "ok": ok,
        "issues": issues,
        "warnings": warnings,
        "components": components,
        "sources": {
            "project_root": str(project_root),
            "watchdog_fixture": str(WATCHDOG_FIXTURE),
            "since": since,
        },
        "notes": notes,
        "report_dir": str(REPORT_DIR),
        "live_orders": 0,
    }
    write_report(payload, report_dir)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the daily audit for strategy_combine")
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT, help="project root")
    parser.add_argument("--since", default="24h", help="recent logs window")
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR, help="report output directory")
    args = parser.parse_args()

    payload = run_daily_audit(args.project_root.resolve(), since=args.since, report_dir=args.report_dir.resolve())
    if payload["issues"]:
        print(
            json.dumps(
                {
                    "ok": False,
                    "issues": len(payload["issues"]),
                    "warnings": len(payload["warnings"]),
                    "report": str(args.report_dir.resolve() / "latest.json"),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

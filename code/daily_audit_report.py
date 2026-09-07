#!/usr/bin/env python3
"""Daily audit report writer.

Takes an aggregated audit payload, writes report artifacts, and stays silent on
OK. Alerts are represented only in the rendered report when issues exist.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = PROJECT_ROOT / "reports" / "daily_audit"


def load_payload(path: Path | None) -> dict[str, Any]:
    if path is None:
        raw = sys.stdin.read().strip()
        if not raw:
            raise ValueError("missing input payload")
        return json.loads(raw)
    return json.loads(path.read_text(encoding="utf-8"))


def build_report(payload: dict[str, Any]) -> dict[str, Any]:
    issues = list(payload.get("issues") or [])
    warnings = list(payload.get("warnings") or [])
    components = payload.get("components") or {}
    ok = bool(payload.get("ok", not issues)) and not issues
    report = {
        "checked_at": payload.get("checked_at") or datetime.now(timezone.utc).isoformat(),
        "ok": ok,
        "issue_count": len(issues),
        "warning_count": len(warnings),
        "issues": issues,
        "warnings": warnings,
        "components": components,
        "sources": payload.get("sources") or {},
        "notes": payload.get("notes") or [],
        "alerts": payload.get("alerts") or (["issues present"] if issues else []),
        "report_dir": payload.get("report_dir") or str(DEFAULT_OUT),
    }
    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Daily Audit",
        "",
        f"- checked_at: {report['checked_at']}",
        f"- ok: {report['ok']}",
        f"- issue_count: {report['issue_count']}",
        f"- warning_count: {report['warning_count']}",
        f"- report_dir: {report['report_dir']}",
        "",
        "## Components",
    ]
    components = report.get("components") or {}
    for name, data in components.items():
        status = "ok" if data.get("ok", True) else "issue"
        details = data.get("summary") or data.get("message") or ""
        lines.append(f"- {name}: {status}{(' — ' + details) if details else ''}")
    if report["issues"]:
        lines.extend([
            "",
            "## Alerts",
            "| level | source | path | message |",
            "|---|---|---|---|",
        ])
        for item in report["issues"]:
            lines.append(
                f"| {item.get('level', 'ERROR')} | {item.get('source', '')} | {item.get('path', '')} | {item.get('message', '')} |"
            )
    if report["warnings"]:
        lines.extend([
            "",
            "## Warnings",
            "| source | path | message |",
            "|---|---|---|",
        ])
        for item in report["warnings"]:
            lines.append(
                f"| {item.get('source', '')} | {item.get('path', '')} | {item.get('message', '')} |"
            )
    return "\n".join(lines).strip() + "\n"


def write_report(payload: dict[str, Any], out_dir: Path = DEFAULT_OUT) -> dict[str, Any]:
    report = build_report(payload)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "latest.md").write_text(render_markdown(report), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Write daily audit reports")
    parser.add_argument("--input", type=Path, default=None, help="path to aggregated JSON input; omit to read stdin")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory for latest.json/latest.md")
    args = parser.parse_args()

    payload = load_payload(args.input)
    write_report(payload, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Stage-ready futures universe audit for strategy_combine.

This module is read-only. It inspects locally available futures data files,
filters excluded roots, and classifies candidates as ready or missing for a
staged expansion to 15-20 assets.

Evidence-driven rules:
  - only local CSV files are used;
  - RI is excluded by policy;
  - a root is ready only when both 15m and 1h CSVs exist with >= 300 rows.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path("/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data")
REPORT_DIR = PROJECT_ROOT / "reports" / "strategy_architect"
UNIVERSE_DISCOVERY_JSON = REPORT_DIR / "universe_discovery.json"
DEFAULT_MIN_ROWS = 300
DEFAULT_TIMEFRAMES = ("15m", "1h")
DEFAULT_EXCLUDED = {"RI"}
DEFAULT_TARGET_SIZE = 20


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _row_count(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return max(0, sum(1 for _ in fh) - 1)
    except OSError:
        return 0


def root_from_filename(name: str) -> str:
    """Extract a root ticker from a continuous CSV file name."""
    if "_60d_" not in name:
        return name.split(".", 1)[0]
    return name.split("_60d_", 1)[0]


def discover_available_roots(data_root: Path = DATA_ROOT) -> List[str]:
    """List roots with at least one continuous CSV present locally."""
    if not data_root.exists():
        return []
    roots = {root_from_filename(p.name) for p in data_root.glob("*_60d_*_continuous.csv")}
    return sorted(roots)


def count_rows_by_root(data_root: Path = DATA_ROOT, timeframes: Sequence[str] = DEFAULT_TIMEFRAMES) -> Dict[str, Dict[str, Any]]:
    """Return row counts and existence flags per root/timeframe."""
    table: Dict[str, Dict[str, Any]] = {}
    for path in sorted(data_root.glob("*_60d_*_continuous.csv")):
        root = root_from_filename(path.name)
        table.setdefault(root, {})[path.name.split("_60d_")[1].split("_continuous.csv")[0]] = {
            "exists": True,
            "rows": _row_count(path),
            "path": str(path),
        }
    for root in list(table.keys()):
        for tf in timeframes:
            table[root].setdefault(tf, {"exists": False, "rows": 0, "path": str(data_root / f"{root}_60d_{tf}_continuous.csv")})
    return table


def classify_root(root: str, rows_by_tf: Dict[str, Dict[str, Any]], min_rows: int = DEFAULT_MIN_ROWS) -> Dict[str, Any]:
    """Classify a single root as ready/missing with evidence."""
    tf_info = rows_by_tf.get(root, {})
    details = {}
    ready = True
    missing: List[str] = []
    for tf in DEFAULT_TIMEFRAMES:
        info = tf_info.get(tf) or {
            "exists": False,
            "rows": 0,
            "path": str(DATA_ROOT / f"{root}_60d_{tf}_continuous.csv"),
        }
        rows = _safe_int(info.get("rows", 0), 0)
        exists = bool(info.get("exists", False))
        details[tf] = {"exists": exists, "rows": rows, "path": info.get("path", "")}
        if not exists or rows < min_rows:
            ready = False
            missing.append(tf)
    excluded = root.upper() in DEFAULT_EXCLUDED
    if excluded:
        ready = False
    return {
        "root": root,
        "excluded": excluded,
        "ready": ready,
        "missing_timeframes": missing,
        "timeframes": details,
        "rows_total": sum(_safe_int(details[tf]["rows"], 0) for tf in DEFAULT_TIMEFRAMES),
    }


def build_universe_candidates(
    target_size: int = DEFAULT_TARGET_SIZE,
    min_rows: int = DEFAULT_MIN_ROWS,
    data_root: Path = DATA_ROOT,
    excluded: Iterable[str] = DEFAULT_EXCLUDED,
) -> Dict[str, Any]:
    """Build a staged expansion candidate list with blockers."""
    excluded_set = {str(x).upper() for x in excluded}
    rows_by_root = count_rows_by_root(data_root)
    roots = sorted(rows_by_root.keys())
    # prefer already-ready assets, then others with local coverage
    classified = []
    for root in roots:
        item = classify_root(root, rows_by_root, min_rows=min_rows)
        item["excluded"] = item["excluded"] or (root.upper() in excluded_set)
        classified.append(item)
    classified.sort(key=lambda x: (not x["ready"], x["excluded"], x["root"]))
    selected = classified[:target_size]
    ready = [item for item in selected if item["ready"] and not item["excluded"]]
    blocked = [item for item in selected if not item["ready"] or item["excluded"]]
    return {
        "target_size": target_size,
        "min_rows": min_rows,
        "excluded": sorted(excluded_set),
        "data_root": str(data_root),
        "selected": selected,
        "ready_roots": [item["root"] for item in ready],
        "missing_roots": [item["root"] for item in blocked],
        "ready_count": len(ready),
        "blocked_count": len(blocked),
        "rows_by_root": rows_by_root,
    }


def render_markdown(payload: Dict[str, Any]) -> str:
    """Render a markdown report for humans."""
    lines = [
        "# Staged universe audit",
        "",
        f"- target_size: {payload['target_size']}",
        f"- min_rows: {payload['min_rows']}",
        f"- excluded: {', '.join(payload['excluded']) or 'none'}",
        f"- ready_count: {payload['ready_count']}",
        f"- blocked_count: {payload['blocked_count']}",
        "",
        "| root | ready | excluded | 15m rows | 1h rows | missing timeframes |",
        "|---|---|---|---:|---:|---|",
    ]
    for item in payload["selected"]:
        tf = item["timeframes"]
        lines.append(
            f"| {item['root']} | {item['ready']} | {item['excluded']} | {tf['15m']['rows']} | {tf['1h']['rows']} | {', '.join(item['missing_timeframes']) or '-'} |"
        )
    return "\n".join(lines)


def render_json(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def write_reports(payload: Dict[str, Any], report_dir: Path = REPORT_DIR) -> Dict[str, str]:
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "universe_discovery.json"
    md_path = report_dir / "universe_discovery.md"
    json_path.write_text(render_json(payload), encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def main() -> int:
    payload = build_universe_candidates()
    paths = write_reports(payload)
    print(json.dumps({
        "target_size": payload["target_size"],
        "ready_count": payload["ready_count"],
        "blocked_count": payload["blocked_count"],
        "ready_roots": payload["ready_roots"],
        "missing_roots": payload["missing_roots"],
        "excluded": payload["excluded"],
        "reports": paths,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

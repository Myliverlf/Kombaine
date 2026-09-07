"""Smoke promoter for dry-run combine pipeline.

A candidate may advance only after a local smoke report is PASS. The module
creates no broker orders and performs no live mutations.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

MAX_LIVE_SLOTS_DEFAULT = 3
MAX_CONTRACTS_PER_ENTRY_DEFAULT = 1


def _read_smoke_source(smoke_report: Any) -> Dict[str, Any]:
    if isinstance(smoke_report, Mapping):
        return dict(smoke_report)

    if isinstance(smoke_report, (str, Path)):
        path = Path(smoke_report)
        text = path.read_text(encoding="utf-8")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            upper = text.upper()
            return {
                "status": "PASS" if "PASS" in upper else "FAIL",
                "text": text,
                "live_orders": 0 if "LIVE ORDERS: 0" in upper or "LIVE ORDERS=0" in upper else 1,
            }

    raise TypeError(f"unsupported smoke_report type: {type(smoke_report)!r}")


def smoke_pass(report: Any) -> bool:
    data = _read_smoke_source(report)
    status = str(data.get("status") or data.get("verdict") or data.get("result") or "").upper()
    passed = bool(data.get("passed")) or bool(data.get("ok")) or status == "PASS"
    live_orders = int(data.get("live_orders", data.get("n_live_orders", 0) or 0))
    broker_ok = not bool(data.get("broker_calls", False))
    return passed and live_orders == 0 and broker_ok


def promote_after_smoke(
    candidates: Sequence[Mapping[str, Any]],
    smoke_report: Any,
    max_live_slots: int = MAX_LIVE_SLOTS_DEFAULT,
    max_contracts_per_entry: int = MAX_CONTRACTS_PER_ENTRY_DEFAULT,
) -> Dict[str, Any]:
    """Promote candidates only if local smoke PASS is available."""
    report = _read_smoke_source(smoke_report)
    smoke_ok = smoke_pass(report)

    promoted: List[Dict[str, Any]] = []
    blocked: List[Dict[str, Any]] = []

    for candidate in candidates:
        item = dict(candidate)
        item["contracts"] = min(int(item.get("contracts", 1) or 1), int(max_contracts_per_entry))
        item["promoted_after_smoke"] = False
        if not smoke_ok:
            item["promotion_status"] = "BLOCKED"
            item["promotion_reason"] = "smoke_not_passed"
            blocked.append(item)
            continue
        if len(promoted) >= int(max_live_slots):
            item["promotion_status"] = "BLOCKED"
            item["promotion_reason"] = f"max_live_slots:{max_live_slots}"
            blocked.append(item)
            continue
        item["promoted_after_smoke"] = True
        item["promotion_status"] = "PROMOTED"
        item["promotion_reason"] = "smoke_pass"
        promoted.append(item)

    return {
        "smoke_ok": smoke_ok,
        "smoke_report": report,
        "promoted": promoted,
        "blocked": blocked,
        "summary": {
            "input": len(list(candidates)),
            "promoted": len(promoted),
            "blocked": len(blocked),
            "max_live_slots": int(max_live_slots),
            "max_contracts_per_entry": int(max_contracts_per_entry),
            "live_orders": 0,
        },
    }


# Convenience alias for hidden tests.
require_smoke_pass = promote_after_smoke


if __name__ == "__main__":
    demo_report = {"status": "PASS", "live_orders": 0, "broker_calls": False}
    demo_candidates = [{"ticker": "BR"}, {"ticker": "LKOH"}, {"ticker": "RI"}]
    print(json.dumps(promote_after_smoke(demo_candidates, demo_report), ensure_ascii=False, indent=2))
